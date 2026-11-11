"""Basket search: iterate on a development span, confirm once on a locked span.

``docs/proposals/BASKET_SEARCH_PROTOCOL_V0.1.md`` (Thomas 2026-11-10: "그 절차로 진행"). It runs in the research
sandbox only, reads the research cache's archive and writes nothing.

- **Engine:** ``cross_section_basket.simulate`` on the point-in-time top-:data:`TOP_N` coin perps, weekly: the top
  fifth of a score long, the bottom fifth short, its costs and funding. A candidate changes only what is ranked.
- **Spans:** ``explore`` reads :data:`DEV` and nothing after it: bars and funding are cut at its last day before
  anything is computed. ``confirm`` reads :data:`CONFIRM`, starts flat on its first day, and runs only for the
  one candidate named in :data:`FINAL`.
- **Candidates:** :data:`CANDIDATES`, one entry per attempt, at most :data:`BUDGET`. A score is a weighted sum of
  cross-sectional ranks of :data:`FEATURES`. No feature ranks a trailing return or trailing funding: those were
  measured through 2026-10 already (``S-REV``, ``S-MOM7``, ``S-MOM30``, ``S-CARRY``), so the locked span is not
  unseen for them.
- **Rule:** annual Sharpe (monthly net sums × √12 over the span's complete months, a month without a basket is 0)
  of at least :data:`ENTRY_SHARPE`, on each span. The first candidate to clear it on ``DEV`` is the final one.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE  # noqa: E402
from scripts.research import cross_section_basket as xsb  # noqa: E402

SPEC = "BASKET_SEARCH_PROTOCOL_V0.1"
DEV = (date(2020, 1, 1), date(2024, 12, 31))
CONFIRM = (date(2025, 1, 1), date(2026, 9, 30))
TOP_N = 30
ENTRY_SHARPE = 1.0
BUDGET = 10
RANDOM_SEEDS = xsb.RANDOM_SEEDS
CADENCE = "S-MOM30"                      # only its weekly cadence is used; the ranking is the candidate's
REFERENCE = "BTCUSDT"                    # the market leg of beta and idiosyncratic volatility
MIN_SHARE = 2 / 3                        # of a lookback's daily returns a member needs to be ranked
FINAL: str | None = None                 # the one candidate ``confirm`` may run; set when DEV is cleared
# The locked span was read once, on 2026-11-11, by METRICS_BASKET_SEARCH_V0.1's final candidate M04. It is spent for
# every protocol that shares it: ``confirm`` refuses every candidate here and in ``metrics_basket_search``.
CONFIRM_SPENT: str | None = "METRICS_BASKET_SEARCH_V0.1 M04, run 20261111T041212Z-3935191"
# NEXT_LOCKED_SPAN_V0.1 (2026-11-11): archive days from here on are kept unread by every development/confirm search,
# so a later protocol has a span to confirm on. A search tool may not measure a span that reaches into it; the
# forward screens, the factory's cohorts and the forward-only observations read these days by their own registrations.
RESERVED_FROM = date(2026, 10, 1)


def _returns(market: xsb.Market, symbol: str, day: date, lookback: int) -> list[tuple[date, float]]:
    bars = market.bars.get(symbol) or {}
    out = []
    for k in range(lookback):
        d = day - timedelta(days=k)
        now, then = bars.get(d), bars.get(d - timedelta(days=1))
        if now is not None and then is not None:
            out.append((d, now.close / then.close - 1))
    return out if len(out) >= math.ceil(lookback * MIN_SHARE) else []


def _vol(market, symbol, day, lookback):
    r = [v for _, v in _returns(market, symbol, day, lookback)]
    return statistics.stdev(r) if len(r) > 1 else None


def _max(market, symbol, day, lookback):
    r = [v for _, v in _returns(market, symbol, day, lookback)]
    return max(r) if r else None


def _skew(market, symbol, day, lookback):
    r = [v for _, v in _returns(market, symbol, day, lookback)]
    if len(r) < 3:
        return None
    mean, sd = statistics.fmean(r), statistics.pstdev(r)
    return None if sd == 0 else statistics.fmean(((v - mean) / sd) ** 3 for v in r)


def _illiquidity(market, symbol, day, lookback):
    """Amihud: the mean of |daily return| per unit of quote volume."""
    bars = market.bars.get(symbol) or {}
    values = [abs(v) / bars[d].quote_volume for d, v in _returns(market, symbol, day, lookback)
              if bars[d].quote_volume > 0]
    return statistics.fmean(values) if values else None


def _volume_growth(market, symbol, day, lookback):
    bars = market.bars.get(symbol) or {}
    recent = [bars[d].quote_volume for d in (day - timedelta(days=k) for k in range(lookback)) if d in bars]
    before = [bars[d].quote_volume for d in (day - timedelta(days=k) for k in range(lookback, 2 * lookback))
              if d in bars]
    if len(recent) < math.ceil(lookback * MIN_SHARE) or len(before) < math.ceil(lookback * MIN_SHARE):
        return None
    base = statistics.fmean(before)
    return None if base <= 0 else statistics.fmean(recent) / base - 1


def _range(market, symbol, day, lookback):
    bars = market.bars.get(symbol) or {}
    values = [(b.high - b.low) / b.close for b in (bars.get(day - timedelta(days=k)) for k in range(lookback))
              if b is not None and b.high is not None and b.low is not None]
    return statistics.fmean(values) if len(values) >= math.ceil(lookback * MIN_SHARE) else None


def _paired(market, symbol, day, lookback):
    own = dict(_returns(market, symbol, day, lookback))
    ref = dict(_returns(market, REFERENCE, day, lookback))
    days = sorted(set(own) & set(ref))
    return ([own[d] for d in days], [ref[d] for d in days]) if len(days) >= math.ceil(lookback * MIN_SHARE) else None


def _beta(market, symbol, day, lookback):
    pair = _paired(market, symbol, day, lookback)
    if pair is None:
        return None
    own, ref = pair
    var = statistics.pvariance(ref)
    return None if var == 0 else statistics.covariance(own, ref) * (len(ref) - 1) / len(ref) / var


def _idio_vol(market, symbol, day, lookback):
    pair, beta = _paired(market, symbol, day, lookback), _beta(market, symbol, day, lookback)
    if pair is None or beta is None:
        return None
    return statistics.stdev([o - beta * r for o, r in zip(*pair)])


Feature = Callable[[xsb.Market, str, date, int], float | None]
# What a candidate may rank. Deliberately no trailing return and no trailing funding (the module docstring).
FEATURES: dict[str, Feature] = {
    "vol": _vol, "max": _max, "skew": _skew, "illiquidity": _illiquidity, "volume_growth": _volume_growth,
    "range": _range, "beta": _beta, "idio_vol": _idio_vol,
}


@dataclass(frozen=True)
class Candidate:
    """One attempt: the long side is the HIGH end of ``Σ weight × rank(feature)`` unless ``long_low``."""
    why: str
    features: tuple[tuple[str, float], ...]
    lookback: int = 30
    long_low: bool = False
    inverse_vol: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"why": self.why, "features": [list(f) for f in self.features], "lookback": self.lookback,
                "long": "low" if self.long_low else "high", "weights": "inverse_vol" if self.inverse_vol else "equal"}


# The attempts, in the order they were run. Each was added, run on DEV once and recorded before the next was written
# (docs/proposals/BASKET_SEARCH_PROTOCOL_V0.1.md, "탐색 기록").
CANDIDATES: dict[str, Candidate] = {
    "A01": Candidate(why="고유 변동성이 낮은 쪽을 사고 높은 쪽을 판다(고유 변동성 퍼즐). 변동성 비중.", features=(("idio_vol", 1.0),), long_low=True, inverse_vol=True),
    "A02": Candidate(why="A01이 시장 하락에 베팅하는 꼴이라, 고유 변동성은 낮고 베타는 높은 쪽을 사서 양쪽의 베타 차이를 줄인다.", features=(("idio_vol", 1.0), ("beta", -1.0)), long_low=True, inverse_vol=True),
    "A03": Candidate(why="변동성 큰 쪽을 파는 구조가 2021년에 졌다. 변동성 대신 관심을 본다: 거래대금이 늘어난 쪽을 사고 줄어든 쪽을 판다.", features=(("volume_growth", 1.0),)),
    "A04": Candidate(why="A03은 순위가 매주 바뀌어 비용이 컸다. 천천히 바뀌는 특성을 쓴다: 덜 유동적인 쪽(수익률 크기÷거래대금이 큰 쪽)을 사고 유동적인 쪽을 판다(비유동성 프리미엄).", features=(("illiquidity", 1.0),)),
    "A05": Candidate(why="A04에 변동성 비중을 더한다. 변동성 큰 비유동 코인 몇 개가 바스켓을 좌우하지 않게 한다.", features=(("illiquidity", 1.0),), inverse_vol=True),
    "A06": Candidate(why="A04(비유동성)와 A01(낮은 고유 변동성)은 좋은 해와 나쁜 해가 반대다. 한 점수로 섞는다: 비유동적이면서 고유 변동성이 낮은 쪽을 사고, 유동적이면서 고유 변동성이 높은 쪽을 판다.", features=(("illiquidity", 1.0), ("idio_vol", -1.0))),
    "A07": Candidate(why="A06의 비용(25%)을 줄인다. 관찰 기간을 30일에서 90일로 늘려 순위가 덜 자주 바뀌게 한다.", features=(("illiquidity", 1.0), ("idio_vol", -1.0)), lookback=90),
    "A08": Candidate(why="섞은 점수는 기간에 약했다. 단일 변수인 A04(비유동성)가 기간을 90일로 늘려도 남는지 본다. 남으면 비용도 준다.", features=(("illiquidity", 1.0),), lookback=90),
    "A09": Candidate(why="아직 안 본 가설: 복권 같은 코인은 비싸다. 최근 30일의 하루 최대 수익률이 작은 쪽을 사고 큰 쪽을 판다(MAX 효과).", features=(("max", 1.0),), long_low=True),
    "A10": Candidate(why="마지막 시도, 아직 안 본 가설: 수익률 분포가 오른쪽으로 치우친(왜도가 큰) 코인은 비싸다. 왜도가 작은 쪽을 사고 큰 쪽을 판다.", features=(("skew", 1.0),), long_low=True),
}


def scores(market: xsb.Market, members: Sequence[str], day: date, candidate: Candidate,
           features: Mapping[str, Feature] | None = None) -> dict[str, float]:
    """``{member: score}`` over the members that have every feature; ranks are percentiles in (0, 1].
    ``features`` is another protocol's feature list (``metrics_basket_search``); None is this module's."""
    features = FEATURES if features is None else features
    values = {name: {s: features[name](market, s, day, candidate.lookback) for s in members}
              for name, _ in candidate.features}
    ranked = [s for s in members if all(values[name][s] is not None for name, _ in candidate.features)]
    out = dict.fromkeys(ranked, 0.0)
    for name, weight in candidate.features:
        order = sorted(ranked, key=lambda s: (values[name][s], s))
        for position, s in enumerate(order, start=1):
            out[s] += weight * position / len(order)
    return out


def weigher(candidate: Candidate, features: Mapping[str, Feature] | None = None) -> Callable[..., dict[str, float]]:
    """A ``cross_section_basket.simulate`` weigher for one candidate (or, with ``seed``, its random twin)."""
    def weights(market: xsb.Market, members: Sequence[str], day: date, signal: str,
                *, seed: str | None = None) -> dict[str, float]:
        scored = scores(market, members, day, candidate, features)
        vols = {s: _vol(market, s, day, candidate.lookback) for s in scored} if candidate.inverse_vol else {}
        usable = [s for s in scored if not candidate.inverse_vol or vols[s]]
        k = xsb.side_size(len(usable))
        if k == 0:
            return {}
        if seed is not None:
            order = sorted(usable, key=lambda s: xsb.random_key(seed, day, s))
        else:
            order = sorted(usable, key=lambda s: (-scored[s], s))
            if candidate.long_low:
                order.reverse()
        out: dict[str, float] = {}
        for sign, side in ((1.0, order[:k]), (-1.0, order[-k:])):
            size = {s: (1 / vols[s] if candidate.inverse_vol else 1.0) for s in side}
            total = sum(size.values())
            out.update({s: sign * size[s] / total for s in side})
        return out
    return weights


def cut(market: xsb.Market, end: date) -> xsb.Market:
    """The market as the archive held it at ``end``'s close: no later bar, no later settlement."""
    limit_ms = (end.toordinal() - date(1970, 1, 1).toordinal() + 1) * 86_400_000
    return xsb.Market(
        bars={s: {d: b for d, b in bars.items() if d <= end} for s, bars in market.bars.items()},
        funding={s: [(t, r) for t, r in rows if (t / 1000 if t > 1e14 else t) < limit_ms]
                 for s, rows in market.funding.items()},
        contract_types=market.contract_types)


def check_search_span(span: tuple[date, date]) -> None:
    """Raise unless ``span`` ends before the reserved days (:data:`RESERVED_FROM`)."""
    if span[1] >= RESERVED_FROM:
        raise ValueError(f"a search span may not reach {RESERVED_FROM} or later (reserved, NEXT_LOCKED_SPAN_V0.1)")


def _complete_months(start: date, end: date) -> list[str]:
    return [m.strftime("%Y-%m") for m in xsb.month_starts(start, end)
            if m >= start and (date(m.year + (m.month == 12), m.month % 12 + 1, 1) - timedelta(days=1)) <= end]


def annual_sharpe(daily: Mapping[date, Mapping[str, float]], start: date, end: date) -> tuple[float | None, list[float]]:
    months = _complete_months(start, end)
    total = dict.fromkeys(months, 0.0)
    for day, row in daily.items():
        key = day.strftime("%Y-%m")
        if key in total:
            total[key] += row["net"]
    values = [total[m] for m in months]
    if len(values) < 2 or statistics.stdev(values) == 0:
        return None, values
    return round(statistics.fmean(values) / statistics.stdev(values) * math.sqrt(12), 3), values


def measure(market: xsb.Market, candidate: Candidate, span: tuple[date, date],
            features: Mapping[str, Feature] | None = None) -> dict[str, Any]:
    """One candidate over one span, on a market already cut at the span's end."""
    start, end = span
    monthly = xsb.universes(market, top_n=TOP_N, start=date(start.year, start.month, 1) - timedelta(days=1), end=end)
    monthly = {date(m.year, m.month, 1): v for m, v in monthly.items()}
    run = lambda **kw: xsb.simulate(market, CADENCE, monthly, start=start, end=end,  # noqa: E731
                                    weigher=weigher(candidate, features), **kw)
    sim = run()
    sharpe, values = annual_sharpe(sim["daily"], start, end)
    weeks = xsb.weekly(sim["daily"])
    random = [annual_sharpe(run(seed=f"{SPEC}|{k}")["daily"], start, end)[0] for k in range(RANDOM_SEEDS)]
    random = [v for v in random if v is not None]
    years = sorted({d.year for d in sim["daily"]})
    return {
        "span": [start.isoformat(), end.isoformat()], "months": len(values), "annual_sharpe": sharpe,
        "pass": sharpe is not None and sharpe >= ENTRY_SHARPE,
        "weeks": len(weeks),
        "mean_bps_per_week": round(statistics.fmean(weeks.values()) * 10_000, 1) if weeks else None,
        "sum_pct": round(sum(values) * 100, 2), "negative_months": sum(1 for v in values if v < 0),
        "years_pct": {str(y): round(sum(r["net"] for d, r in sim["daily"].items() if d.year == y) * 100, 2)
                      for y in years},
        "cost_pct": round(sum(r["cost"] for r in sim["daily"].values()) * 100, 2),
        "funding_pct": round(sum(r["funding"] for r in sim["daily"].values()) * 100, 2),
        "forced_closes": sim["forced_closes"]["count"],
        "random_baskets_mean_annual_sharpe": round(statistics.fmean(random), 3) if random else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="basket_search", description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("list", "explore", "confirm"))
    parser.add_argument("--candidate", default=None)
    parser.add_argument("--archive", type=Path, default=None, help="default: $RESEARCH_CACHE/archive")
    args = parser.parse_args(argv)
    if args.command == "list":
        print(json.dumps({"budget": BUDGET, "final": FINAL,
                          "candidates": {k: c.as_dict() for k, c in CANDIDATES.items()}}, indent=1, ensure_ascii=False))
        return EXIT_OK
    if not xsb._in_sandbox():
        print("refused: runs in the research sandbox only (scripts/research/sandbox_run.sh)", file=sys.stderr)
        return EXIT_USAGE
    if args.candidate not in CANDIDATES:
        print(f"refused: no candidate {args.candidate!r}; registered: {', '.join(CANDIDATES) or 'none'}", file=sys.stderr)
        return EXIT_USAGE
    if len(CANDIDATES) > BUDGET:
        print(f"refused: {len(CANDIDATES)} candidates are over the budget of {BUDGET}", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "confirm" and CONFIRM_SPENT:
        print(f"refused: the locked span is spent, it was read once ({CONFIRM_SPENT})", file=sys.stderr)
        return EXIT_USAGE
    if args.command == "confirm" and args.candidate != FINAL:
        print(f"refused: the locked span is read once, for the final candidate ({FINAL}), not for "
              f"{args.candidate}", file=sys.stderr)
        return EXIT_USAGE
    span = DEV if args.command == "explore" else CONFIRM
    try:
        check_search_span(span)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_USAGE
    archive = args.archive or xsb._archive_default()
    snapshot = xsb.read_contract_types(archive)
    if snapshot is None:
        print(f"BLOCKED: no readable {xsb.CONTRACT_TYPES_FILENAME} under {archive}", file=sys.stderr)
        return EXIT_BLOCKED
    market = xsb.load_market(archive)
    market.contract_types = snapshot[0]
    market = cut(market, span[1])                      # before anything is computed
    candidate = CANDIDATES[args.candidate]
    report = {"measured_at": timeutil.utc_now_iso(), **xsb.provenance(), "spec": SPEC, "command": args.command,
              "candidate": args.candidate, "definition": candidate.as_dict(), "entry_sharpe": ENTRY_SHARPE,
              **measure(market, candidate, span)}
    print(json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
