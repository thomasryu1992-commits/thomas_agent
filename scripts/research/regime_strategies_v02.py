"""Regime strategy set v0.2: timing and selection judged apart, four hypotheses confirmed once on spot 2018–2019.

``docs/proposals/CRYPTO_REGIME_STRATEGY_SET_V0.2.md`` (Thomas 2026-11-05: §7 Q1–Q3 "기본값대로"). It uses
``regime_strategies`` (v0.1) as a library and leaves it byte-for-byte as v0.1 ran, so v0.1's records stay
reproducible by their own ``code_digest``.

- **Labels (§2):** ``full`` is v0.1's (BTC SMA200, SMA50 slope, top-30 breadth). ``btc_only`` drops breadth:
  BTC close above SMA200 and SMA50's 20-day change positive is BULL, the mirror is BEAR, anything else SIDEWAYS.
  ``sma200`` is BULL above SMA200, else BEAR. All three confirm a new label after
  ``regime_strategies.CONFIRM_DAYS`` identical readings, by the same loop.
- **Timing (T1):** BTC held long only on the days of one regime, through the v0.1 engine with costs; its weekly
  net in that regime's weeks. BULL-week mean minus BEAR-week mean, Welch t, one-sided normal p, α 0.10.
- **Selection (S1–S3):** B08 in BULL, D10 in BEAR, S01R in SIDEWAYS. A week's excess is the strategy's net minus
  the mean of eight same-direction random twins. Holm α 0.10 over the judgeable rows' one-sided p and the spot
  S-MOM30 p, then a positive excess mean and a positive excess sum in each judged year.
- **S01R:** S01's entry with the sign flipped (long above the upper band, short below the lower); out when the
  close comes back to the middle band (long: close ≤ SMA20, short: close ≥ SMA20). The common stop and the
  30-day hold are kept.

``stage0`` re-measures the record's §2 on the seen futures window (it reads no spot). ``confirm-spot`` reads
Binance spot 2018-03-05 → 2019-12-29 once. Both run in the research sandbox only and write nothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xs  # noqa: E402
from scripts.research import regime_strategies as rs  # noqa: E402

SPEC = "CRYPTO_REGIME_STRATEGY_SET_V0.2"
BULL, SIDEWAYS, BEAR = rs.BULL, rs.SIDEWAYS, rs.BEAR
BTC = rs.BTC
# §4: the confirmation sample, its universe (CROSS_SECTION_SPOT_HOLDOUT_V0.1 §2, hash recorded there) and costs
SPOT_START, SPOT_END = date(2018, 3, 5), date(2019, 12, 29)          # Monday .. Sunday, 95 weeks
SPOT_LABEL_START = date(2017, 8, 1)
SPOT_UNIVERSE_START, SPOT_UNIVERSE_END = date(2017, 8, 1), date(2019, 12, 31)
SPOT_UNIVERSE_HASH = "3690d0e61fedf3e8"
SPOT_FEE_BPS = 10.0
SPOT_YEARS = (2018, 2019)
TIMING_ALPHA = 0.10
HOLM_ALPHA = rs.HOLM_ALPHA
FLOOR_WEEKS, FLOOR_TRADES = rs.FLOOR_WEEKS, rs.FLOOR_TRADES
SEEDS = rs.RANDOM_SEEDS
SEED_PREFIX = f"{rs.SPEC}/random"            # the twins of the record's §2 (stage 0) and of the confirmation
# §4/§5: the spot window was read once before, by CROSS_SECTION_SPOT_HOLDOUT_V0.1 (run 20261104T102324Z-304513)
PRIOR_P = {"spot-holdout S-MOM30": 0.20803269693879048}
SELECTION: dict[str, tuple[str, str]] = {"S1": ("B08", BULL), "S2": ("D10", BEAR), "S3": ("S01R", SIDEWAYS)}
TIMING_LABEL = "btc_only"
S01R = "S01R"

Raw = Callable[[rs.Data, date], "str | None"]


# --- the labels -------------------------------------------------------------------------------------------

def raw_btc_only(data: rs.Data, day: date) -> str | None:
    s200, s50, s50_then = (data.at(BTC, "sma200", day), data.at(BTC, "sma50", day), data.at(BTC, "sma50", day, 20))
    close = data.at(BTC, "close", day)
    if None in (s200, s50, s50_then, close):
        return None
    up, slope = close > s200, s50 - s50_then
    if up and slope > 0:
        return BULL
    if not up and slope < 0:
        return BEAR
    return SIDEWAYS


def raw_sma200(data: rs.Data, day: date) -> str | None:
    s200, close = data.at(BTC, "sma200", day), data.at(BTC, "close", day)
    if None in (s200, close):
        return None
    return BULL if close > s200 else BEAR


RAW: dict[str, Raw] = {"full": rs.raw_regime, "btc_only": raw_btc_only, "sma200": raw_sma200}


def labels_from(raw: Raw, data: rs.Data, start: date, end: date) -> dict[date, str]:
    """``regime_strategies.regime_labels``' confirmation loop over any raw reading."""
    labels: dict[date, str] = {}
    state, candidate, run = None, None, 0
    day = start
    while day <= end:
        reading = raw(data, day)
        if reading is not None:
            if state is None:
                state = reading
            elif reading != state:
                run = run + 1 if reading == candidate else 1
                candidate = reading
                if run >= rs.CONFIRM_DAYS:
                    state, candidate, run = reading, None, 0
            else:
                candidate, run = None, 0
            labels[day] = state
        day += timedelta(days=1)
    return labels


# --- S01R and the rules that know it ----------------------------------------------------------------------

def e_s01r(r: rs.Rules, s: str, day: date):
    hit = rs.e_s01(r, s, day)
    return None if hit is None else (-hit[0], hit[1], hit[2], hit[3])


def x_back_to_middle(r: rs.Rules, p: rs.Position, day: date) -> bool:
    c, m = rs._v(r, p.symbol, "close", day), rs._v(r, p.symbol, "sma20", day)
    return rs._all(c, m) and (c <= m if p.side > 0 else c >= m)


def positions(rules: rs.Rules, sid: str, entry_signal: Callable, exit_rule: Callable) -> rs.Decide:
    """``regime_strategies.Rules.positions`` with its entry and exit handed in (a test pins the two equal)."""
    d = rules.d
    book: list[rs.Position] = []

    def decide(day: date, current: dict[str, float]) -> dict[str, float] | None:
        book[:] = [p for p in book if p.symbol in current]
        keep = []
        for p in book:
            if p.held(day) >= rs.MAX_HOLD_DAYS or exit_rule(rules, p, day) or rs.stopped(rules, sid, p, day):
                continue
            keep.append(p)
        book[:] = keep
        if rules.in_regime(sid, day) and len(book) < rs.MAX_POSITIONS:
            taken = {p.symbol for p in book}
            found = []
            for symbol in d.members(day):
                if symbol in taken:
                    continue
                hit = entry_signal(rules, symbol, day)
                if hit is not None:
                    side, strength, hedge, info = hit
                    found.append((strength, symbol, side, hedge, info))
            found.sort(key=lambda f: (-f[0], f[1]))
            for strength, symbol, side, hedge, info in found[:rs.MAX_POSITIONS - len(book)]:
                book.append(rs.Position(symbol, side, day, d.at(symbol, "atr14", day), hedge, info))
        return rs.weights_of(book)
    return decide


class Rules(rs.Rules):
    def in_regime(self, sid: str, day: date) -> bool:
        if sid == S01R:
            return self.labels.get(day) == SIDEWAYS
        return super().in_regime(sid, day)


def regime_of(sid: str) -> str:
    return SIDEWAYS if sid == S01R else rs.STRATEGIES[sid].regime


def decider(rules: Rules, sid: str) -> rs.Decide:
    if sid == S01R:
        return positions(rules, sid, e_s01r, x_back_to_middle)
    return rs.decider(rules, sid)


def needs(sid: str) -> tuple[str, ...]:
    return () if sid == S01R else rs.STRATEGIES[sid].needs


# --- the measurements --------------------------------------------------------------------------------------

def _round(stats: Mapping[str, Any]) -> dict[str, Any]:
    return {k: (round(v, 4) if isinstance(v, float) else v) for k, v in stats.items()}


def measure_selection(data: rs.Data, labels: Mapping[date, str], sid: str, spans: Sequence[tuple[date, date]],
                      weeks: Mapping[date, str], *, fee_bps: float | None = None,
                      seeds: int = SEEDS) -> dict[str, Any]:
    """One strategy's weeks in its regime: its own net, the twins' mean, and the excess the verdict reads."""
    regime = regime_of(sid)
    path: dict[date, dict[str, float]] = {}
    daily: dict[date, dict[str, float]] = {}
    forced = 0
    for a, b in spans:
        result = rs.run(data, rs.recording(decider(Rules(data, labels), sid), path), a, b, fee_bps=fee_bps)
        daily.update(result["daily"])
        forced += result["forced_closes"]
    mine = [w for w, r in weeks.items() if r == regime]
    own = xs.weekly(daily) if daily else {}
    twins = []
    for k in range(seeds):
        twin: dict[date, dict[str, float]] = {}
        for a, b in spans:
            twin.update(rs.run(data, rs.random_twin(data, path, sid, f"{SEED_PREFIX}/{k}"), a, b,
                               fee_bps=fee_bps)["daily"])
        twins.append(xs.weekly(twin) if twin else {})
    control = {w: statistics.fmean(t.get(w, 0.0) for t in twins) if twins else 0.0 for w in mine}
    excess = {w: own.get(w, 0.0) - control[w] for w in mine}
    stats = rs.t_stats(list(excess.values()))
    years: dict[int, float] = {}
    for w, v in excess.items():
        years[w.year] = years.get(w.year, 0.0) + v
    trades = sum(int(row["entries"]) for day, row in daily.items() if weeks.get(xs.week_of(day)) == regime)
    own_stats = rs.t_stats([own.get(w, 0.0) for w in mine])
    return {"strategy": sid, "regime": regime, "weeks": stats["weeks"], "trades": trades, "forced_closes": forced,
            "excess_mean_bps": stats["mean_bps"], "excess_t": stats["t"], "p_one_sided": stats["p_one_sided"],
            "excess_years_bps": {str(y): round(v * 1e4, 3) for y, v in sorted(years.items())},
            "own_mean_bps": own_stats["mean_bps"], "own_t": own_stats["t"],
            "control_mean_bps": round(statistics.fmean(control.values()) * 1e4, 3) if control else None}


def regime_long(data: rs.Data, labels: Mapping[date, str], regime: str | None, spans: Sequence[tuple[date, date]],
                *, fee_bps: float | None = None) -> dict[date, float]:
    """Weekly net of BTC held long on the days labelled ``regime`` (every day when None)."""
    def decide(day: date, book: dict[str, float]) -> dict[str, float]:
        return {BTC: 1.0} if regime is None or labels.get(day) == regime else {}
    daily: dict[date, dict[str, float]] = {}
    for a, b in spans:
        daily.update(rs.run(data, decide, a, b, fee_bps=fee_bps)["daily"])
    return xs.weekly(daily) if daily else {}


def sharpe(values: Sequence[float]) -> float | None:
    if len(values) < 2 or statistics.stdev(values) == 0:
        return None
    return statistics.fmean(values) / statistics.stdev(values) * math.sqrt(52)


def welch(a: Sequence[float], b: Sequence[float]) -> dict[str, Any]:
    """Mean of ``a`` minus mean of ``b``, Welch t, one-sided normal p (H1: a > b)."""
    if len(a) < 2 or len(b) < 2:
        return {"diff_bps": None, "t": None, "p_one_sided": None}
    diff = statistics.fmean(a) - statistics.fmean(b)
    se = math.sqrt(statistics.variance(a) / len(a) + statistics.variance(b) / len(b))
    t = diff / se if se > 0 else None
    return {"diff_bps": round(diff * 1e4, 3), "t": t, "p_one_sided": None if t is None else 0.5 * math.erfc(t / math.sqrt(2))}


def measure_timing(data: rs.Data, labels: Mapping[date, str], spans: Sequence[tuple[date, date]],
                   weeks: Mapping[date, str], *, fee_bps: float | None = None) -> dict[str, Any]:
    by = {r: regime_long(data, labels, r, spans, fee_bps=fee_bps) for r in (BULL, BEAR)}
    bull = [by[BULL].get(w, 0.0) for w, r in weeks.items() if r == BULL]
    bear = [by[BEAR].get(w, 0.0) for w, r in weeks.items() if r == BEAR]
    hold = regime_long(data, labels, None, spans, fee_bps=fee_bps)
    every = sorted(weeks)
    return {"bull": _round(rs.t_stats(bull)), "bear": _round(rs.t_stats(bear)), **welch(bull, bear),
            "long_flat_sharpe": sharpe([by[BULL].get(w, 0.0) for w in every]),
            "buy_hold_sharpe": sharpe([hold.get(w, 0.0) for w in every])}


# --- the verdicts (§5) -----------------------------------------------------------------------------------

def timing_verdict(row: Mapping[str, Any]) -> str:
    if row["bull"]["weeks"] < FLOOR_WEEKS or row["bear"]["weeks"] < FLOOR_WEEKS or row["p_one_sided"] is None:
        return "NOT_JUDGEABLE"
    return "TIMING_CONFIRMED" if row["diff_bps"] > 0 and row["p_one_sided"] <= TIMING_ALPHA \
        else "TIMING_NOT_CONFIRMED"


def judgeable(row: Mapping[str, Any]) -> bool:
    return row["weeks"] >= FLOOR_WEEKS and row["trades"] >= FLOOR_TRADES and row["p_one_sided"] is not None


def selection_family(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, float]:
    return {**{hid: r["p_one_sided"] for hid, r in rows.items() if judgeable(r)}, **PRIOR_P}


def selection_verdicts(rows: Mapping[str, Mapping[str, Any]]) -> dict[str, str]:
    fam = selection_family(rows)
    names = list(fam)
    passed = {names[i] for i in xs.holm([fam[n] for n in names], HOLM_ALPHA)}
    out = {}
    for hid, r in rows.items():
        if not judgeable(r):
            out[hid] = "NOT_JUDGEABLE"
            continue
        years = r["excess_years_bps"].values()
        ok = hid in passed and r["excess_mean_bps"] > 0 and all(v > 0 for v in years)
        out[hid] = "SELECTION_CONFIRMED" if ok else "SELECTION_NOT_CONFIRMED"
    return out


# --- the runs ----------------------------------------------------------------------------------------------

def stage_zero(data: rs.Data, *, seeds: int = SEEDS) -> dict[str, Any]:
    """§2 on the seen futures window: the timing table for three labels, the selection excess for 37 strategies."""
    split = rs.segments()
    spansets = {"screen": split["screen"], "holdout": split["holdout"], "all": [(rs.WINDOW_START, rs.WINDOW_END)]}
    timing: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    for name, raw in RAW.items():
        labels = labels_from(raw, data, rs.LABEL_START, rs.WINDOW_END)
        timing[name] = {"days": rs.label_record(labels)["days"]}
        for sname, spans in spansets.items():
            timing[name][sname] = measure_timing(data, labels, spans, rs.regime_weeks(labels, spans))
        if name in ("full", "btc_only"):
            spans = spansets["screen"]
            weeks = rs.regime_weeks(labels, spans)
            selection[name] = {sid: measure_selection(data, labels, sid, spans, weeks, seeds=seeds)
                               for sid in [*rs.STRATEGIES, S01R]}
    return {"timing": timing, "selection": selection}


def confirm_spot(data: rs.Data, labels: Mapping[date, str], spans: Sequence[tuple[date, date]], *,
                 fee_bps: float = SPOT_FEE_BPS, seeds: int = SEEDS) -> dict[str, Any]:
    weeks = rs.regime_weeks(labels, spans)
    t1 = measure_timing(data, labels, spans, weeks, fee_bps=fee_bps)
    t1["verdict"] = timing_verdict(t1)
    rows = {hid: measure_selection(data, labels, sid, spans, weeks, fee_bps=fee_bps, seeds=seeds)
            for hid, (sid, _) in SELECTION.items()}
    verdicts = selection_verdicts(rows)
    for hid in rows:
        rows[hid]["verdict"] = verdicts[hid]
    return {"weeks_by_regime": {r: sum(1 for v in weeks.values() if v == r) for r in rs.REGIMES},
            "timing": {"T1": t1}, "selection": rows, "holm_family": selection_family(rows)}


def spot_blocked(sids: Sequence[str]) -> list[str]:
    """Strategies spot cannot measure: spot has no funding and no open interest."""
    return [s for s in sids if needs(s)]


def load_spot(archive: Path) -> tuple[rs.Data, str]:
    market = xs.load_market(archive, funding=False)
    market.bars = {s: b for s, b in market.bars.items() if s.endswith("USDT")}   # USDT pairs only (Thomas)
    market.contract_types = xs.spot_kinds(market.bars)
    monthly = xs.universes(market, top_n=rs.TOP_N, start=SPOT_UNIVERSE_START, end=SPOT_UNIVERSE_END)
    return rs.Data(market, monthly, {}), xs.universe_hash(monthly)


def code_digest() -> str:
    """This module and the two it runs on, so a later change to either is a different test."""
    h = hashlib.sha256()
    for path in (Path(__file__), Path(rs.__file__), Path(xs.__file__)):
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def provenance(stage: str, fee_bps: float | None) -> dict[str, Any]:
    return {"spec": SPEC, "stage": stage, "measured_at": timeutil.utc_now_iso(), **xs.provenance(fee_bps),
            "code_digest": code_digest(), "seeds": SEEDS, "seed_prefix": SEED_PREFIX}


def _cache() -> Path:
    return Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="regime_strategies_v02", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p0 = sub.add_parser("stage0")
    p0.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    p0.add_argument("--metrics", type=Path, default=None, help="default: $RESEARCH_CACHE/archive-metrics")
    p1 = sub.add_parser("confirm-spot")
    p1.add_argument("--spot-archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive-spot")
    args = parser.parse_args(argv)
    if not xs._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "stage0":
        archive = args.archive or _cache() / "archive"
        metrics = args.metrics or _cache() / "archive-metrics"
        try:
            data, _ = rs.load(archive, metrics)
        except FileNotFoundError as exc:
            print(f"BLOCKED: no {exc} under {archive}", file=sys.stderr)
            return EXIT_BLOCKED
        print(json.dumps({**provenance("stage0", None), **stage_zero(data)}, indent=1, sort_keys=True, default=str))
        return EXIT_OK
    blocked = spot_blocked([sid for sid, _ in SELECTION.values()])
    if blocked:
        print(f"BLOCKED: spot has no funding or open interest for {blocked}", file=sys.stderr)
        return EXIT_BLOCKED
    archive = args.spot_archive or _cache() / "archive-spot"
    data, universe = load_spot(archive)
    if universe != SPOT_UNIVERSE_HASH:
        print(f"BLOCKED: spot universe {universe} is not the recorded {SPOT_UNIVERSE_HASH}", file=sys.stderr)
        return EXIT_BLOCKED
    labels = labels_from(RAW[TIMING_LABEL], data, SPOT_LABEL_START, SPOT_UNIVERSE_END)
    body = confirm_spot(data, labels, [(SPOT_START, SPOT_END)])
    print(json.dumps({**provenance("confirm-spot", SPOT_FEE_BPS), "universe_hash": universe, "label": TIMING_LABEL,
                      "window": [SPOT_START.isoformat(), SPOT_END.isoformat()], "prior_p": PRIOR_P, **body},
                     indent=1, sort_keys=True, default=str))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
