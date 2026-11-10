"""Held-out forward replay: the replay's 4h and 1d schedules re-run on five perpetuals it never read.

``docs/proposals/HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1.md`` (Thomas 2026-11-07, "그래도 실행"). The question is
the one ``HISTORICAL_FORWARD_REPLAY_V0.1`` answered NOT_CONFIRMED for all seven contexts, asked again for the 4h
and 1d schedules alone on symbols no step of that study read: :data:`HELD`.

Everything but the symbols is the original's, and this module re-implements only what binds the context list:

- **Contexts:** ``heldout-4h`` and ``heldout-1d`` pool :data:`HELD` exactly as ``pooled-4h`` and ``pooled-1d``
  pool ``forward_replay.POOLED``: the first symbol is the minting leg, the rest are cohort legs. The order is the
  pre-registered volume ranking (median daily quote volume 2021-12..2026-10), fixed before any fire.
- **Generation ids:** each held-out context takes the id of the schedule slot it stands in
  (:data:`SLOT`), so the generator's rotation stride and seeds see what the seven-schedule day would hand them.
- **Fire span:** from the original's first fire day to the day before the last anchor. Fires after it were the
  original's real-cohort sanity comparison; no real cohort ever held these symbols, so nothing reads them.
- **Shards:** their own :data:`SPEC`, and a code digest over this module, ``forward_replay`` and the crypto
  lane, so neither judge can read the other study's shards as its own.

Commands, as ``forward_replay``'s: ``time-fires``, ``simulate``, ``leak-check``. ``forward_replay_judge
--design heldout`` judges the shards.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import forward_replay as fr  # noqa: E402
from scripts.research import lake  # noqa: E402

SPEC = "HISTORICAL_FORWARD_REPLAY_HELDOUT_V0.1"
# Listed on Binance USD-M by 2020-09, still PERPETUAL, outside CROSS_SECTION_UNIVERSE and POOLED; the five largest
# by median daily quote volume 2021-12..2026-10 (ADA 284M, AVAX 240M, LINK 227M, LTC 192M, DOT 142M USDT).
HELD = ("ADAUSDT", "AVAXUSDT", "LINKUSDT", "LTCUSDT", "DOTUSDT")
CONTEXTS: tuple[fr.Context, ...] = (
    fr.Context("heldout-4h", HELD, "4h"),
    fr.Context("heldout-1d", HELD, "1d"),
)
BY_NAME = {c.name: c for c in CONTEXTS}
SLOT = {"heldout-4h": "pooled-4h", "heldout-1d": "pooled-1d"}


def generation_id(day: date, context: fr.Context) -> str:
    """The id of the original schedule slot this context stands in, on the same day."""
    return fr.generation_id(day, fr.BY_NAME[SLOT[context.name]])


def fire_span() -> tuple[date, date]:
    """The original's first fire day to the day before the last anchor: every fire some freeze reads."""
    return fr.fire_span()[0], fr.replay_anchors()[-1] - timedelta(days=1)


def fire(context: fr.Context, day: date, store: list[Mapping[str, Any]], *, state: Path,
         run: Callable[..., dict[str, Any]] | None = None, arm: str = "A") -> dict[str, Any]:
    """``forward_replay.fire`` with the held-out generation id; nothing else differs."""
    now = fr.iso(day)
    started = time.monotonic()
    with lake.as_of(now):
        cache = fr.market_data.PeerCandleCache(fr.market_data.select_market_data_collector(now=now, root=state))
    legs = []
    for symbol in context.symbols:
        leg = fr.mining_snapshot(symbol, context.timeframe, now=now, state=state, cache=cache, arm=arm)
        if leg is None:
            return {"day": day.isoformat(), "status": f"no_data:{symbol}", "candidates": [],
                    "seconds": round(time.monotonic() - started, 3)}
        legs.append(leg)
    with fr.fixed_generation(generation_id(day, context)), (fr.arm_b() if arm == "B" else contextlib.nullcontext()):
        result = (run or fr.factory.run_factory)(
            legs[0], active_pool={}, existing_candidates=list(store), now=now, fusion_pairs=fr.FUSION_PAIRS,
            positioning_eligible=False, cohort_snapshots=legs[1:] or None, trial_proposals=None)
    return {"day": day.isoformat(), "status": "fired", "candidates": list(result.get("candidates") or []),
            "seconds": round(time.monotonic() - started, 3)}


def code_digest() -> str:
    """This module, the replay it extends and the crypto lane both run."""
    h = hashlib.sha256()
    crypto = ROOT / "runtime" / "mvp_runtime" / "crypto"
    for path in [Path(__file__), Path(fr.__file__), *sorted(crypto.glob("*.py"))]:
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()[:16]


def manifest(context: fr.Context, arm: str, cache_root: Path) -> dict[str, Any]:
    return {"shard_version": fr.SHARD_VERSION, "spec": SPEC, "arm": arm, "context": context.name,
            "code_digest": code_digest(), "cache_digest": fr.cache_digest(cache_root, context),
            "python": platform.python_version(), "machine": platform.machine(), "system": platform.system()}


def simulate(context: fr.Context, start: date, end: date, out: Path, *, arm: str, cache_root: Path,
             state: Path, say: Callable[..., None] = lambda *a, **k: None,
             run: Callable[..., dict[str, Any]] | None = None) -> int:
    """``forward_replay.simulate`` with this study's manifest and fire: resumable, refuses another study's file."""
    want = manifest(context, arm, cache_root)
    head, fires = fr.read_shard(out)
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
                row = fire(context, day, store, state=state, run=run, arm=arm)
                store.extend(row["candidates"])
                fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")
                fh.flush()
                say(f"{context.name} {day} {row['status']} +{len(row['candidates'])} {row['seconds']}s")
            day += timedelta(days=1)
    return EXIT_OK


def leak_check(context: fr.Context, day: date, *, cache_root: Path, work: Path, state: Path,
               arm: str = "A") -> dict[str, Any]:
    """``forward_replay.leak_check`` with this study's fire."""
    full = fire(context, day, [], state=state, arm=arm)
    cut = work / f"cut-{context.name}-{day.isoformat()}"
    dropped = fr.truncate_cache(cache_root, cut, fr.iso(day))
    lake.install(cache=cut, lake=None)
    try:
        bounded = fire(context, day, [], state=state, arm=arm)
    finally:
        lake.install(cache=cache_root, lake=None)
        shutil.rmtree(cut, ignore_errors=True)
    same = full["status"] == bounded["status"] and \
        fr._fingerprint(full["candidates"]) == fr._fingerprint(bounded["candidates"])
    return {"context": context.name, "arm": arm, "day": day.isoformat(), "rows_after_cut_dropped": dropped,
            "candidates": len(full["candidates"]), "identical": same}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="forward_replay_heldout", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    pt = sub.add_parser("time-fires", help="one fire per context at --at: seconds, rows, peak memory")
    pt.add_argument("--at", required=True)
    pt.add_argument("--context", action="append", choices=sorted(BY_NAME))
    pt.add_argument("--arm", default="A", choices=fr.ARMS)
    ps = sub.add_parser("simulate", help="fire one context daily over [--start, --end] into --out (resumable)")
    ps.add_argument("--context", required=True, choices=sorted(BY_NAME))
    ps.add_argument("--start", default=None, help="default: the fire span's first day")
    ps.add_argument("--end", default=None, help="default: the day before the last anchor")
    ps.add_argument("--out", type=Path, required=True)
    ps.add_argument("--arm", default="A", choices=fr.ARMS)
    pl = sub.add_parser("leak-check", help="one fire on the whole cache and on a cache cut at its instant")
    pl.add_argument("--context", required=True, choices=sorted(BY_NAME))
    pl.add_argument("--at", required=True)
    pl.add_argument("--work", type=Path, default=None, help="where the cut cache is written (default: the run dir)")
    pl.add_argument("--arm", default="A", choices=fr.ARMS)
    for p in (pt, ps, pl):
        p.add_argument("--cache", type=Path, default=None, help="outside the sandbox: the copied public cache")
    args = parser.parse_args(argv)
    cache, refusal = fr._prepare(args.cache)
    if refusal:
        print(refusal, file=sys.stderr)
        return EXIT_USAGE
    with tempfile.TemporaryDirectory(prefix="forward-replay-heldout-state-") as tmp:
        state = Path(tmp)
        if args.command == "time-fires":
            day = fr.parse_day(args.at)
            rows = []
            for context in [BY_NAME[n] for n in (args.context or [c.name for c in CONTEXTS])]:
                row = fire(context, day, [], state=state, arm=args.arm)
                rows.append({"context": context.name, "arm": args.arm, "status": row["status"],
                             "seconds": row["seconds"], "candidates": len(row["candidates"]),
                             "peak_mb": fr.peak_mb()})
                print(json.dumps(rows[-1]), file=sys.stderr, flush=True)
            print(json.dumps({"spec": SPEC, "at": day.isoformat(), "code_digest": code_digest(), "fires": rows},
                             indent=1, sort_keys=True))
            return EXIT_OK
        context = BY_NAME[args.context]
        if args.command == "leak-check":
            work = args.work or Path(os.environ.get("RESEARCH_RUN") or tmp)
            result = leak_check(context, fr.parse_day(args.at), cache_root=cache, work=work, state=state,
                                arm=args.arm)
            print(json.dumps({"spec": SPEC, "code_digest": code_digest(), **result}, indent=1, sort_keys=True))
            return EXIT_OK if result["identical"] else EXIT_BLOCKED
        first, last = fire_span()
        return simulate(context, fr.parse_day(args.start) if args.start else first,
                        fr.parse_day(args.end) if args.end else last, args.out, arm=args.arm,
                        cache_root=cache, state=state,
                        say=lambda *a, **k: print(*a, file=sys.stderr, flush=True))


if __name__ == "__main__":
    raise SystemExit(main())
