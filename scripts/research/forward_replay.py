"""Historical forward replay: the factory's own generation and selection re-run at past anchors, scored forward.

``docs/proposals/HISTORICAL_FORWARD_REPLAY_V0.1.md`` (Thomas 2026-11-05). Arm A, the current procedure:

- **Fires (§2):** every context of today's seven factory schedules fires once a day at :data:`FIRE_TIME`. Each
  fire calls ``factory.run_factory`` unchanged, on a snapshot built the way the scheduler builds it
  (``collect_market_data`` at ``factory_candle_target`` plus ``attach_mining_legs``), cut at the fire's
  instant with ``lake.as_of``. The context's own replayed store is its ``existing_candidates``, so elite
  centres, the rotation cursor and fusion parents come from the replay, never from the live store.
- **Freeze (§2):** every :data:`ANCHOR_STEP_DAYS` days ``forward_cohort.eligible_members`` runs over the
  merged replayed store (rows made before the anchor), with the ids earlier replayed cohorts hold and the
  lineages of cohorts still open (:data:`COHORT_OPEN_DAYS`) excluded, and an empty pool.
- **Scoring (§2, §4):** each member's trades OPENED in ``[T, T+28d)`` on frames cut at the end of the
  window plus :data:`HOLD_ALLOWANCE_BARS`, so a trade opened inside can close. Its twins are the cohort
  nulls ``forward_cohort_null.build_null_record`` builds (direction, exits and scope the parent's, entry a
  seeded coin), over :data:`TWIN_SEEDS` draws. The anchor's value is the members' mean of (member R − twin R).

Fidelity choices, recorded in the proposal's 구현 선택:

- the generation id is :func:`generation_id` (day × context), so a context run alone (a shard) mints the
  same ids as all seven interleaved; ``run_factory`` otherwise counts the global store;
- no trial proposals (the LLM proposer cannot be replayed) and no positioning (``positioning_eligible``
  False, the positioning store empty);
- no Coinalyze open interest or liquidations, on every machine: the feed is the lake feed with no lake,
  which raises, so ``attach_feeds`` marks it degraded and those features are None.

Commands: ``time-fires`` (one fire per context at one instant, seconds and peak memory), ``simulate`` (one
context over a day range into a resumable shard file), ``compare-shards`` (the Mac / server identity check),
and ``judge`` (merge shards, freeze, score, verdict). In the research sandbox, or anywhere with ``--cache``
and no ``MVP_*`` variable set (the Mac).
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import math
import os
import platform
import random
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from runtime.mvp_runtime.crypto import factory, feed_assembly, forward_cohort, forward_cohort_null  # noqa: E402
from runtime.mvp_runtime.crypto import market_data  # noqa: E402
from runtime.mvp_runtime.crypto.backtest import _replay, build_replay_frame  # noqa: E402
from runtime.mvp_runtime.crypto.candidate_identity import candidate_id  # noqa: E402
from runtime.mvp_runtime.crypto.null_control import _slice  # noqa: E402
from runtime.mvp_runtime.crypto.strategy import StrategySpec  # noqa: E402
from runtime.mvp_runtime.errors import ToolBlocked, ToolError  # noqa: E402
from scripts.research import lake  # noqa: E402

SPEC = "HISTORICAL_FORWARD_REPLAY_V0.1"
SHARD_VERSION = "forward_replay_shard.v1"
FIRE_TIME = "08:10:00"                      # the factory schedules fire 08:09–08:16 UTC
ANCHOR_STEP_DAYS = 28
FORWARD_DAYS = 28
COHORT_OPEN_DAYS = 180                      # forward_cohort: a cohort holds its lineages this long
WARM_START_DAYS = 180                       # fires begin this long before the first anchor
HOLD_ALLOWANCE_BARS = 48                    # the longest max_holding_bars a template may draw
TWIN_SEEDS = 2
FUSION_PAIRS = 4                            # scheduler.FACTORY_FUSION_PAIRS (a test pins the two equal)
VERDICT_ALPHA = 0.05
FLOOR_ANCHORS = 20
BLOCK_ANCHORS = 3
BOOTSTRAP_DRAWS = 2000
BOOTSTRAP_SEED = f"{SPEC}/bootstrap"
# §5: the anchors. The first is the first Monday after BTC/ETH/BNB 1h reach FULL depth (24,000 bars from the
# 2019-09 listing). The last leaves 28 forward days plus the longest 1d hold (48 days) before the cache's end
# (2026-11-04), so every trade a member opens in its window can close. Fires start WARM_START_DAYS earlier.
FIRST_ANCHOR = date(2022, 6, 6)
ANCHOR_COUNT = 55
DATA_END = date(2026, 11, 4)
POOLED = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "DOGEUSDT")   # the stored per_symbol order


@dataclass(frozen=True)
class Context:
    name: str
    symbols: tuple[str, ...]
    timeframe: str


# Today's seven factory schedules (docs/history/2026-10-01-strategy-edge-s1-schedule-correction.md; the store's
# contexts on 2026-11-04). The pooled 1h schedule mints its first symbol only (1h is not a pooled timeframe), so
# it is the BTC 1h context.
CONTEXTS: tuple[Context, ...] = (
    Context("BTCUSDT-1h", ("BTCUSDT",), "1h"),
    Context("ETHUSDT-1h", ("ETHUSDT",), "1h"),
    Context("SOLUSDT-1h", ("SOLUSDT",), "1h"),
    Context("BNBUSDT-1h", ("BNBUSDT",), "1h"),
    Context("DOGEUSDT-1h", ("DOGEUSDT",), "1h"),
    Context("pooled-4h", POOLED, "4h"),
    Context("pooled-1d", POOLED, "1d"),
)
BY_NAME = {c.name: c for c in CONTEXTS}


# --- time ----------------------------------------------------------------------------------------------------

def iso(day: date, clock: str = FIRE_TIME) -> str:
    return f"{day.isoformat()}T{clock}Z"


def parse_day(value: str) -> date:
    return date.fromisoformat(value[:10])


def anchors(first: date, last: date, step: int = ANCHOR_STEP_DAYS) -> list[date]:
    out, day = [], first
    while day + timedelta(days=FORWARD_DAYS) <= last:
        out.append(day)
        day += timedelta(days=step)
    return out


def replay_anchors() -> list[date]:
    return [FIRST_ANCHOR + timedelta(days=ANCHOR_STEP_DAYS * k) for k in range(ANCHOR_COUNT)]


def fire_span() -> tuple[date, date]:
    """The days every context fires: from WARM_START_DAYS before the first anchor to the day before the last."""
    return FIRST_ANCHOR - timedelta(days=WARM_START_DAYS), replay_anchors()[-1] - timedelta(days=1)


def generation_id(day: date, context: Context) -> str:
    """The fire's generation id: day-major, context-minor, as if all seven fired each day in order."""
    index = (day - date(2019, 1, 1)).days * len(CONTEXTS) + CONTEXTS.index(context) + 1
    return f"GEN-{index:06d}"


# --- one fire -----------------------------------------------------------------------------------------------

class _NoFeed:
    """No Coinalyze rows on any machine: every call raises, so the feed is degraded and its columns None."""

    def __getattr__(self, name: str) -> Callable[..., Any]:
        def _refuse(*args: Any, **kwargs: Any) -> Any:
            raise ToolError(lake.LAKE_NO_COINALYZE, "the forward replay reads no vendor daily rows")
        return _refuse


def mining_snapshot(symbol: str, timeframe: str, *, now: str, state: Path, cache: Any) -> dict[str, Any] | None:
    """One leg, the scheduler's ``_mining_snapshot`` cut at ``now``. None when the cache cannot serve it."""
    with lake.as_of(now):
        collector = market_data.select_market_data_collector(now=now, root=state)
        try:
            built, _ = market_data.collect_market_data(symbol, timeframe, collector=collector, now=now,
                                                       limit=market_data.factory_candle_target(timeframe))
        except (ToolBlocked, ToolError):
            return None
        feed_assembly.attach_mining_legs(
            built, collector=collector, timeframe=timeframe, now=now, root=state, liquidation_feed=_NoFeed(),
            candle_target=market_data.factory_candle_target, candle_cache=cache, record_vendor_daily=False)
    return built


@contextlib.contextmanager
def fixed_generation(gid: str) -> Iterator[None]:
    saved = factory.next_generation_id
    factory.next_generation_id = lambda existing: gid        # type: ignore[assignment]
    try:
        yield
    finally:
        factory.next_generation_id = saved                   # type: ignore[assignment]


def fire(context: Context, day: date, store: list[Mapping[str, Any]], *, state: Path,
         run: Callable[..., dict[str, Any]] | None = None) -> dict[str, Any]:
    """One replayed fire: its new candidate rows, or why it minted nothing."""
    now = iso(day)
    started = time.monotonic()
    with lake.as_of(now):
        cache = market_data.PeerCandleCache(market_data.select_market_data_collector(now=now, root=state))
    legs = []
    for symbol in context.symbols:
        leg = mining_snapshot(symbol, context.timeframe, now=now, state=state, cache=cache)
        if leg is None:
            return {"day": day.isoformat(), "status": f"no_data:{symbol}", "candidates": [],
                    "seconds": round(time.monotonic() - started, 3)}
        legs.append(leg)
    with fixed_generation(generation_id(day, context)):
        result = (run or factory.run_factory)(
            legs[0], active_pool={}, existing_candidates=list(store), now=now, fusion_pairs=FUSION_PAIRS,
            positioning_eligible=False, cohort_snapshots=legs[1:] or None, trial_proposals=None)
    return {"day": day.isoformat(), "status": "fired", "candidates": list(result.get("candidates") or []),
            "seconds": round(time.monotonic() - started, 3)}


# --- shards ------------------------------------------------------------------------------------------------

def code_digest() -> str:
    """This module and the crypto lane it runs, so two machines can only compare shards of the same code."""
    h = hashlib.sha256()
    crypto = ROOT / "runtime" / "mvp_runtime" / "crypto"
    for path in [Path(__file__), *sorted(crypto.glob("*.py"))]:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def cache_digest(cache_root: Path, context: Context) -> str:
    """The series a context's fires can read: every timeframe of every symbol it or its legs name."""
    h = hashlib.sha256()
    symbols = sorted({*context.symbols, market_data.REFERENCE_SYMBOL, *market_data.CROSS_SECTION_UNIVERSE})
    base = cache_root / "binance"
    for path in sorted(p for p in base.rglob("*.jsonl") if p.name.split(".")[0] in symbols):
        h.update(str(path.relative_to(base)).encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def manifest(context: Context, arm: str, cache_root: Path) -> dict[str, Any]:
    return {"shard_version": SHARD_VERSION, "spec": SPEC, "arm": arm, "context": context.name,
            "code_digest": code_digest(), "cache_digest": cache_digest(cache_root, context),
            "python": platform.python_version(), "machine": platform.machine(), "system": platform.system()}


def read_shard(path: Path) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    if not path.exists():
        return None, []
    head, fires = None, []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue                                         # a torn last line: that fire runs again
        if head is None:
            head = row.get("manifest")
            continue
        fires.append(row)
    return head, fires


def simulate(context: Context, start: date, end: date, out: Path, *, arm: str, cache_root: Path,
             state: Path, say: Callable[..., None] = lambda *a, **k: None,
             run: Callable[..., dict[str, Any]] | None = None) -> int:
    """Fire ``context`` every day in [start, end] into ``out``, resuming after the last fire it holds.

    Returns EXIT_BLOCKED when ``out`` was written by other code, data or arm."""
    want = manifest(context, arm, cache_root)
    head, fires = read_shard(out)
    if head is not None and head != want:
        say(f"BLOCKED: {out} was written with {head}, not {want}")
        return EXIT_BLOCKED
    store = [c for f in fires for c in f["candidates"]]
    done = {f["day"] for f in fires}
    with out.open("a", encoding="utf-8") as fh:
        if head is None:
            fh.write(json.dumps({"manifest": want}, sort_keys=True) + "\n")
        day = start
        while day <= end:
            if day.isoformat() not in done:
                row = fire(context, day, store, state=state, run=run)
                store.extend(row["candidates"])
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                fh.flush()
                say(f"{context.name} {day} {row['status']} +{len(row['candidates'])} {row['seconds']}s")
            day += timedelta(days=1)
    return EXIT_OK


def compare_shards(a: Path, b: Path) -> dict[str, Any]:
    """Day by day, the rows two machines minted: identical rule hashes and evidence, or the first difference."""
    ha, fa = read_shard(a)
    hb, fb = read_shard(b)
    if ha is None or hb is None:
        return {"identical": False, "reason": "missing_manifest"}
    for key in ("spec", "arm", "context", "code_digest", "cache_digest"):
        if ha.get(key) != hb.get(key):
            return {"identical": False, "reason": f"manifest:{key}", "a": ha.get(key), "b": hb.get(key)}
    by_b = {f["day"]: f for f in fb}
    compared = 0
    for f in fa:
        g = by_b.get(f["day"])
        if g is None:
            continue
        compared += 1
        if _fingerprint(f["candidates"]) != _fingerprint(g["candidates"]):
            return {"identical": False, "reason": "candidates", "day": f["day"], "compared_days": compared}
    return {"identical": compared > 0, "compared_days": compared}


def _fingerprint(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return [hashlib.sha256(json.dumps({k: r.get(k) for k in ("strategy_rule_hash", "backtest_evidence",
                                                              "mint_params")}, sort_keys=True,
                                      default=str).encode()).hexdigest() for r in rows]


# --- the leak check (§4) ------------------------------------------------------------------------------------

def _ms(instant: str) -> int:
    return int(datetime.fromisoformat(instant.replace("Z", "+00:00")).timestamp() * 1000)


def truncate_cache(source: Path, target: Path, instant: str) -> int:
    """A copy of ``source``'s public series holding only what had opened (klines, price series) or settled
    (funding) before ``instant``. Returns the rows dropped."""
    cut, dropped = _ms(instant), 0
    for path in sorted((source / "binance").rglob("*.jsonl")):
        out = target / path.relative_to(source)
        out.parent.mkdir(parents=True, exist_ok=True)
        kept = []
        for row in lake.read_rows(path):
            stamp = row[0] if isinstance(row, list) and row else (row.get("fundingTime") if isinstance(row, dict)
                                                                   else None)
            if isinstance(stamp, (int, float)) and int(stamp) >= cut:
                dropped += 1
                continue
            kept.append(json.dumps(row))
        out.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
    return dropped


def leak_check(context: Context, day: date, *, cache_root: Path, work: Path, state: Path) -> dict[str, Any]:
    """The fire at ``day`` on the whole cache and on a cache cut at the fire's instant: the same rows, or a leak."""
    full = fire(context, day, [], state=state)
    cut = work / f"cut-{context.name}-{day.isoformat()}"
    dropped = truncate_cache(cache_root, cut, iso(day))
    lake.install(cache=cut, lake=None)
    try:
        bounded = fire(context, day, [], state=state)
    finally:
        lake.install(cache=cache_root, lake=None)
        shutil.rmtree(cut, ignore_errors=True)
    same = full["status"] == bounded["status"] and _fingerprint(full["candidates"]) == _fingerprint(bounded["candidates"])
    return {"context": context.name, "day": day.isoformat(), "rows_after_cut_dropped": dropped,
            "candidates": len(full["candidates"]), "identical": same}


# --- freeze and scoring ---------------------------------------------------------------------------------------

def made_before(rows: Sequence[Mapping[str, Any]], instant: str) -> list[Mapping[str, Any]]:
    return [r for r in rows if str(r.get("created_at_utc") or "") < instant]


def freeze_all(store: Sequence[Mapping[str, Any]], at: Sequence[date], *,
               eligible: Callable[..., list[dict[str, Any]]] | None = None) -> list[dict[str, Any]]:
    """One replayed cohort per anchor, in order, with the live door's exclusions."""
    eligible = eligible or forward_cohort.eligible_members
    cohorts: list[dict[str, Any]] = []
    for anchor in at:
        instant = iso(anchor, "00:00:00")
        held = frozenset(m["candidate_id"] for c in cohorts for m in c["members"])
        open_lineages = frozenset(forward_cohort._lineage_key(m) for c in cohorts
                                  if (anchor - c["anchor"]).days < COHORT_OPEN_DAYS for m in c["members"])
        members = eligible(made_before(store, instant), {}, exclude_candidate_ids=held,
                           exclude_lineages=open_lineages)
        record = forward_cohort.build_cohort_record(members, now=instant) if members else None
        cohorts.append({"anchor": anchor, "members": members, "record": record})
    return cohorts


def window_trades(spec: StrategySpec, frames: Sequence[Any], start: str, end: str, *,
                  seed: str | None = None) -> list[float]:
    """R of every trade the spec OPENED in [start, end) on the frames (one per leg)."""
    out: list[float] = []
    for frame in frames:
        index = next((i for i, c in enumerate(frame.candles) if str(c.get("open_time")) >= start), None)
        if index is None:
            continue
        w = _slice(frame, index)
        rows = w.rows if seed is None else forward_cohort_null.null_rows({"seed": seed}, w.rows, w.candles)
        outcomes, *_ = _replay(spec, rows, w.candles, cost=w.cost, funding=w.funding)
        out.extend(float(o["result_R"]) for o in outcomes if start <= str(o["created_at_utc"]) < end)
    return out


def twin_seeds(seed: str, count: int = TWIN_SEEDS) -> list[str]:
    return [f"{seed}|{k}" for k in range(1, count + 1)]


def score_cohort(cohort: Mapping[str, Any], latest: Mapping[str, Mapping[str, Any]], *,
                 frames_for: Callable[[Sequence[str], str, str], Sequence[Any]]) -> dict[str, Any]:
    """Each member's R and its twins' mean R over the anchor's forward window, and the anchor's value."""
    anchor: date = cohort["anchor"]
    start, end = iso(anchor, "00:00:00"), iso(anchor + timedelta(days=FORWARD_DAYS), "00:00:00")
    if cohort["record"] is None:
        return {"anchor": anchor.isoformat(), "members": 0, "value_R": None, "rows": []}
    nulls = forward_cohort_null.build_null_record(cohort["record"], latest, now=start)
    twin_of = {t["parent_candidate_id"]: t for t in nulls["members"]}
    rows = []
    for member in cohort["members"]:
        twin = twin_of.get(member["candidate_id"])
        if twin is None:
            continue
        spec = StrategySpec.from_dict(latest[member["candidate_id"]]["strategy_spec"])
        frames = frames_for(member["symbol_scope"] or [spec.symbol], member["timeframe"], end)
        mine = sum(window_trades(spec, frames, start, end))
        twin_spec = StrategySpec.from_dict(twin["null_spec"])
        theirs = statistics.fmean(sum(window_trades(twin_spec, frames, start, end, seed=s))
                                  for s in twin_seeds(str(twin["seed"])))
        rows.append({"candidate_id": member["candidate_id"], "timeframe": member["timeframe"],
                     "family": member["strategy_family"], "member_R": mine, "twin_R": theirs,
                     "excess_R": mine - theirs})
    value = statistics.fmean(r["excess_R"] for r in rows) if rows else None
    return {"anchor": anchor.isoformat(), "members": len(rows), "value_R": value, "rows": rows,
            "skipped_twins": nulls.get("skipped")}


# --- the verdict (§4) --------------------------------------------------------------------------------------

def t_stats(values: Sequence[float]) -> dict[str, Any]:
    n = len(values)
    if n < 2 or statistics.stdev(values) == 0:
        return {"anchors": n, "mean_R": statistics.fmean(values) if values else None, "t": None, "p_one_sided": None}
    mean, se = statistics.fmean(values), statistics.stdev(values) / math.sqrt(n)
    t = mean / se
    return {"anchors": n, "mean_R": mean, "t": t, "p_one_sided": 0.5 * math.erfc(t / math.sqrt(2)),
            "ci95_R": [mean - 1.96 * se, mean + 1.96 * se]}


def block_bootstrap_p(values: Sequence[float], *, block: int = BLOCK_ANCHORS, draws: int = BOOTSTRAP_DRAWS,
                      seed: str = BOOTSTRAP_SEED) -> float | None:
    """Reported only: the share of centred moving-block resamples whose mean reaches the observed mean."""
    n = len(values)
    if n < block * 2:
        return None
    mean = statistics.fmean(values)
    centred = [v - mean for v in values]
    rng = random.Random(seed)
    hits = 0
    for _ in range(draws):
        sample: list[float] = []
        while len(sample) < n:
            i = rng.randrange(0, n - block + 1)
            sample.extend(centred[i:i + block])
        hits += statistics.fmean(sample[:n]) >= mean
    return hits / draws


def holm_pass(pvalues: Mapping[str, float | None], alpha: float = VERDICT_ALPHA) -> set[str]:
    named = sorted(((p, k) for k, p in pvalues.items() if p is not None))
    out: set[str] = set()
    for rank, (p, k) in enumerate(named):
        if p > alpha / (len(named) - rank):
            break
        out.add(k)
    return out


def arm_reading(anchor_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values = [r["value_R"] for r in anchor_rows if r["value_R"] is not None]
    years: dict[str, float] = {}
    for r in anchor_rows:
        if r["value_R"] is not None:
            years[r["anchor"][:4]] = years.get(r["anchor"][:4], 0.0) + r["value_R"]
    return {**t_stats(values), "years_R": years, "block_bootstrap_p": block_bootstrap_p(values),
            "members_per_anchor": [r["members"] for r in anchor_rows],
            "anchors_without_members": sum(1 for r in anchor_rows if r["value_R"] is None)}


def verdicts(readings: Mapping[str, Mapping[str, Any]], *, leak_ok: bool, sanity_ok: bool) -> dict[str, str]:
    """REPLAY_CONFIRMED per arm (§4): positive mean, Holm α 0.05 across arms, a majority of positive years,
    the floor, and the leak and real-cohort checks."""
    judgeable = {a: r for a, r in readings.items() if r["anchors"] >= FLOOR_ANCHORS}
    passed = holm_pass({a: r["p_one_sided"] for a, r in judgeable.items()})
    out = {}
    for arm, r in readings.items():
        if arm not in judgeable:
            out[arm] = "NOT_JUDGEABLE"
        elif not (leak_ok and sanity_ok):
            out[arm] = "BLOCKED"
        else:
            years = list(r["years_R"].values())
            ok = arm in passed and r["mean_R"] > 0 and sum(v > 0 for v in years) * 2 > len(years)
            out[arm] = "REPLAY_CONFIRMED" if ok else "REPLAY_NOT_CONFIRMED"
    return out


# --- the command line ---------------------------------------------------------------------------------------

def _in_sandbox() -> bool:
    return getattr(market_data.select_market_data_collector, "__module__", "") == lake.__name__


def _prepare(cache: Path | None) -> tuple[Path | None, str | None]:
    """The cache to read, or why this machine may not run. Outside the sandbox: ``--cache`` and no MVP_* var."""
    if _in_sandbox():
        return Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache"))), None
    if cache is None:
        return None, "outside the research sandbox, --cache is required (the Mac)"
    gated = sorted(k for k in os.environ if k.startswith("MVP_"))
    if gated:
        return None, f"refused: environment gate variables are set ({', '.join(gated)})"
    lake.install(cache=cache, lake=None)
    return cache, None


def peak_mb() -> float | None:
    try:
        import resource          # POSIX only; the replay runs on Linux and macOS, the suite also on Windows
    except ImportError:
        return None
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(usage / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_replay", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pt = sub.add_parser("time-fires", help="one fire per context at --at: seconds, rows, peak memory")
    pt.add_argument("--at", required=True)
    pt.add_argument("--context", action="append", choices=sorted(BY_NAME))
    ps = sub.add_parser("simulate", help="fire one context daily over [--start, --end] into --out (resumable)")
    ps.add_argument("--context", required=True, choices=sorted(BY_NAME))
    ps.add_argument("--start", default=None, help="default: the fire span's first day")
    ps.add_argument("--end", default=None, help="default: the day before the last anchor")
    ps.add_argument("--out", type=Path, required=True)
    ps.add_argument("--arm", default="A", choices=("A",))
    pl = sub.add_parser("leak-check", help="one fire on the whole cache and on a cache cut at its instant")
    pl.add_argument("--context", required=True, choices=sorted(BY_NAME))
    pl.add_argument("--at", required=True)
    pl.add_argument("--work", type=Path, default=None, help="where the cut cache is written (default: the run dir)")
    pc = sub.add_parser("compare-shards", help="the Mac / server identity check")
    pc.add_argument("a", type=Path)
    pc.add_argument("b", type=Path)
    for p in (pt, ps, pl):
        p.add_argument("--cache", type=Path, default=None, help="outside the sandbox: the copied public cache")
    args = parser.parse_args(argv)
    if args.command == "compare-shards":
        print(json.dumps(compare_shards(args.a, args.b), indent=1, sort_keys=True))
        return EXIT_OK
    cache, refusal = _prepare(args.cache)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_USAGE
    with tempfile.TemporaryDirectory(prefix="forward-replay-state-") as tmp:
        state = Path(tmp)
        if args.command == "time-fires":
            day = parse_day(args.at)
            rows = []
            for context in [BY_NAME[n] for n in (args.context or [c.name for c in CONTEXTS])]:
                row = fire(context, day, [], state=state)
                rows.append({"context": context.name, "status": row["status"], "seconds": row["seconds"],
                             "candidates": len(row["candidates"]), "peak_mb": peak_mb()})
                print(json.dumps(rows[-1]), file=sys.stderr, flush=True)
            print(json.dumps({"spec": SPEC, "at": day.isoformat(), "code_digest": code_digest(), "fires": rows},
                             indent=1, sort_keys=True))
            return EXIT_OK
        context = BY_NAME[args.context]
        if args.command == "leak-check":
            work = args.work or Path(os.environ.get("RESEARCH_RUN") or tmp)
            result = leak_check(context, parse_day(args.at), cache_root=cache, work=work, state=state)
            print(json.dumps({"spec": SPEC, "code_digest": code_digest(), **result}, indent=1, sort_keys=True))
            return EXIT_OK if result["identical"] else EXIT_BLOCKED
        first, last = fire_span()
        return simulate(context, parse_day(args.start) if args.start else first,
                        parse_day(args.end) if args.end else last, args.out, arm=args.arm,
                        cache_root=cache, state=state,
                        say=lambda *a, **k: print(*a, file=sys.stderr, flush=True))


if __name__ == "__main__":
    raise SystemExit(main())
