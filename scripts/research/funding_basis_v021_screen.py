"""Funding basis v0.2.1 screen: B-POS over the point-in-time top-30 universe, one fixed four-month forward window.

``docs/proposals/FUNDING_BASIS_V0.2.md`` §4, §7, §8 (Thomas 2026-11-09). The official v0.2.1 run
(``20261109T090121Z-3112463``) cleared the entry floor for B-POS only, so only B-POS is screened. The rule is
``STRATEGY_SCREEN_GATE_V0.1``: ``SCREEN_KEEP`` when the mean daily portfolio return over the whole window is above
zero, else ``SCREEN_DROP``. A strategy it keeps is still unconfirmed.

- **Model, universe, holding:** ``FUNDING_BASIS_V0.2.1`` unchanged (:mod:`funding_basis_v02`). Each window month's
  members are the top :data:`funding_basis_v02.TOP_N` perps by the previous month's volume with a spot pair then.
  A position is held only on days the perp traded, and opens fresh at the window's first traded close.
- **Window:** :data:`funding_basis_v02.SCREEN_WINDOW` (one source of truth).
- **Coverage before exclusion.** In a forward run, data not fetched yet looks like a gap. So:
  1. The perp kline archive must hold every ranking month (M − 1) and reach the window's last day. Otherwise
     ``NOT_YET``.
  2. A ranking month whose klines hold fewer than ``TOP_N`` perps means an incomplete fetch: ``BLOCKED``, so it
     cannot sit as ``NOT_YET`` forever. A ranking month absent from the spot archive is ``NOT_YET``. Fewer than
     ``TOP_N`` members is normal (the top ``TOP_N`` are ranked first, then those without a spot pair drop; 40 of
     v0.2's 81 months had fewer) and is only shown.
  3. Each member's derivative bars must reach its last traded member day. When that day is the window's last, its
     settlements must reach 16:00 of it, as in the v0.1 screen. A member short of that is ``NOT_YET``: it traded,
     so the shortfall is unfetched data, not a delisting.
  4. Only then: v0.2's missing-data exclusions are listed, and more than
     :data:`funding_basis_v02.MAX_EXCLUDED_SHARE` of member-months blocks.
- **No early reading:** a ``NOT_YET`` report carries how far the data reaches and what is short, nothing about
  returns.
- **Archive holes (proposed 2026-11-09, ``FUNDING_BASIS_V0.2.md`` §10):** the archive's 1d kline is the witness
  that a perp traded. On 2026-10-28 it is missing for every perp alive that day, while their index, premium and
  mark bars and funding settlements all exist: a publication defect, not a halt. A day missing from the klines of
  at least :data:`HOLE_SHARE` of the perps alive across it is an archive hole, and counts as traded for each perp
  with klines on both sides. The derivative bars then decide as on any other day: present, the day is held and
  priced; absent, v0.2's missing-data exclusion fires. A halt or a delisting is per-symbol, and the trailing
  "bars end" rule (the LUNA protection) is untouched: a hole is interior by definition.

It reads only the research cache and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402
from scripts.research import funding_basis as fb  # noqa: E402
from scripts.research import funding_basis_screen as sc  # noqa: E402
from scripts.research import funding_basis_v02 as v  # noqa: E402

SPEC = sc.SPEC
SCREENED = ("B-POS",)                    # cleared the entry floor in run 20261109T090121Z-3112463
HOLE_SHARE = 0.25                        # a day missing for this share of the perps alive across it is a hole
KEEP, DROP, NOT_YET, JUDGED = sc.KEEP, sc.DROP, sc.NOT_YET, sc.JUDGED


@dataclass
class Forward:
    volumes: dict[str, dict[str, float]]         # month -> {perp: quote volume}
    traded: dict[str, set[date]]                 # perp -> days it traded
    spot_months: dict[str, set[str]]             # spot pair -> months archived
    spot_listing: set[str]
    series: dict[str, fb.Series | None]          # candidate member -> its derivative series (None: unreadable)


def archive_holes(traded: Mapping[str, set[date]], share: float | None = None) -> dict[date, tuple[int, int]]:
    """``{day: (missing, alive)}`` for every day missing from the klines of at least ``share`` of the perps
    alive across it (a kline on some earlier and some later day)."""
    share = HOLE_SHARE if share is None else share
    spans = [(min(days), max(days), days) for days in traded.values() if days]
    if not spans:
        return {}
    alive: dict[date, int] = defaultdict(int)
    missing: dict[date, int] = defaultdict(int)
    for first, last, days in spans:
        day = first + timedelta(days=1)
        while day < last:
            alive[day] += 1
            if day not in days:
                missing[day] += 1
            day += timedelta(days=1)
    return {d: (n, alive[d]) for d, n in sorted(missing.items()) if n >= share * alive[d]}


def fill_holes(traded: Mapping[str, set[date]], holes: Mapping[date, Any]) -> dict[str, set[date]]:
    """Each perp's traded days plus the archive holes strictly inside its kline span."""
    out = {}
    for symbol, days in traded.items():
        if days:
            first, last = min(days), max(days)
            out[symbol] = days | {d for d in holes if first < d < last}
        else:
            out[symbol] = set(days)
    return out


def _not_yet(head: dict[str, Any], data_through: date | None, short: list[str]) -> dict[str, Any]:
    return {**head, "status": NOT_YET, "data_through": data_through.isoformat() if data_through else None,
            "short": short}


def screen(data: Forward, *, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """The report. ``NOT_YET`` carries only how far the data reaches; ``JUDGED`` carries the verdict."""
    start = start or v.SCREEN_WINDOW[0]
    end = end or v.SCREEN_WINDOW[1]
    months = v.months_between(start, end)
    head = {"spec": SPEC, "model": v.SPEC, "window": [start.isoformat(), end.isoformat()],
            "strategies_screened": list(SCREENED)}

    holes = archive_holes(data.traded)
    traded = fill_holes(data.traded, holes)
    head["archive_holes"] = {d.isoformat(): {"missing": n, "alive": a} for d, (n, a) in holes.items()
                             if start <= d <= end}
    klines_through = max((max(days) for days in traded.values() if days), default=None)
    unranked = [v.prev_month(m) for m in months if not data.volumes.get(v.prev_month(m))]
    if unranked or klines_through is None or klines_through < end:
        return _not_yet(head, klines_through, [f"klines {m}" for m in unranked] or ["klines"])

    ranking = [v.prev_month(m) for m in months]
    thin = {k: len(data.volumes[k]) for k in ranking if len(data.volumes[k]) < v.TOP_N}
    if thin:                         # fewer perps with klines than the universe ranks: the fetch missed some
        raise v.Blocked(f"ranking month with klines for fewer than {v.TOP_N} perps: {thin}")
    unspotted = [k for k in ranking if not any(k in held for held in data.spot_months.values())]
    if unspotted:
        return _not_yet(head, klines_through, [f"spot {k}" for k in unspotted])

    # Fewer than TOP_N members is normal: the top TOP_N are ranked first, then those without a spot pair drop.
    members = v.universe(data.volumes, data.spot_months, data.spot_listing, months)

    months_of: dict[str, list[str]] = defaultdict(list)
    for month, symbols in members.items():
        for symbol in symbols:
            months_of[symbol].append(month)

    short, reach, held = [], [], {}
    for symbol, ms in sorted(months_of.items()):
        days = {d for d in traded.get(symbol, set()) if start <= d <= end and v.month_key(d) in ms}
        if not days:
            continue                                      # never traded while a member: nothing to hold
        last = max(days)
        s = data.series.get(symbol)
        if s is not None and s.days:
            reach.append(min(max(s.days), end))
        if s is None or not s.days or max(s.days) < last:
            short.append(symbol)
            continue
        settled = s.settlements[-1][0] if s.settlements else -1
        need = fb._ms(last) + (sc.LAST_SETTLEMENT_HOUR * fb.HOUR_MS if last == end else 0)
        if settled < need:
            short.append(symbol)
            continue
        held[symbol] = s
    if short:
        return _not_yet(head, min([klines_through, *reach]), short)

    excluded = {}
    for symbol, s in held.items():
        missing = v.missing_months(s, traded[symbol], months_of[symbol])
        if missing:
            excluded[symbol] = missing
    inputs = v.Inputs(series=held, members=members, excluded=excluded,
                      traded={s: traded[s] for s in held})
    member_total = sum(len(ms) for ms in members.values())
    excluded_total = sum(len(ms) for ms in excluded.values())
    if excluded_total / member_total > v.MAX_EXCLUDED_SHARE:
        raise v.Blocked(f"excluded member-months {excluded_total}/{member_total} > {v.MAX_EXCLUDED_SHARE}")

    by_symbol = v.member_months(inputs)
    strategies = {}
    for name in SCREENED:
        legs = {s: v.simulate_member(name, held[s], ms, inputs.traded[s], start, end)
                for s, ms in sorted(by_symbol.items()) if ms}
        strategies[name] = sc.summarise(name, legs)
    return {**head, "status": JUDGED, "input_digest": fb.input_digest(held),
            "universe": {"digest": v.universe_digest(members), "members": {m: len(ms) for m, ms in members.items()},
                         "member_months": member_total, "excluded_member_months": excluded_total,
                         "excluded": {s: sorted(ms) for s, ms in sorted(excluded.items())}},
            "strategies": strategies}


def load(cache: Path, *, start: date | None = None, end: date | None = None) -> Forward:
    start = start or v.SCREEN_WINDOW[0]
    end = end or v.SCREEN_WINDOW[1]
    archive, spot_archive = cache / "archive", cache / "archive-spot"
    perps = json.loads((archive / "symbols.json").read_text())
    contract_types = json.loads((archive / "contract_types.json").read_text())["contract_types"]
    spot_listing = set(json.loads((spot_archive / "symbols.json").read_text()))
    volumes, traded = v.read_volumes(archive, perps, contract_types)
    spot_months = v.read_spot_months(spot_archive)
    candidates = {s for ms in v.universe(volumes, spot_months, spot_listing,
                                         v.months_between(start, end)).values() for s in ms}
    series: dict[str, fb.Series | None] = {}
    for symbol in sorted(candidates):
        s = fb.read_series(cache, symbol)
        if s is not None:
            s.name = symbol
        series[symbol] = s
    return Forward(volumes=dict(volumes), traded=traded, spot_months=spot_months, spot_listing=spot_listing,
                   series=series)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="funding_basis_v021_screen", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("run",))
    parser.add_argument("--cache", type=Path, default=None, help="default: $RESEARCH_CACHE")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    cache = args.cache or Path(os.environ.get("RESEARCH_CACHE", str(Path.home() / "research" / "cache")))
    try:
        report = {"measured_at": timeutil.utc_now_iso(), **fb.provenance(), **screen(load(cache))}
    except (OSError, ValueError, KeyError) as error:
        print(f"BLOCKED: cannot read the inputs under {cache}: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    except (v.Blocked, fb.DataGap) as error:
        print(f"BLOCKED: {error}", file=sys.stderr)
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(report, indent=1, sort_keys=True))
        return EXIT_OK
    if report["status"] == NOT_YET:
        print(f"NOT_YET: window {report['window'][0]}..{report['window'][1]}, data through "
              f"{report['data_through']}, short: {','.join(report['short'])}")
        return EXIT_OK
    for name, row in report["strategies"].items():
        print(f"{name}: {row['verdict']} | mean {row['mean_bps_per_day']} bps/day "
              f"({row['annualised_return_pct']}%/yr on capital) | SR {row['annual_sharpe']} "
              f"| worst {row['worst_month']} | maxDD {row['max_drawdown_pct']}%")
    print(f"input {report['input_digest']} universe {report['universe']['digest']}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
