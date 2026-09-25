"""How many independent bets a set of forward records is — not how many strategies (2026-09-24).

Thomas decided `docs/proposals/PORTFOLIO_INDEPENDENCE_V0.1.md` as recommended (Q1, option A): a
read-only measurement, no gate. Measured the day it was decided, the forward cohort's 45 lineages
with five or more trades were about nine independent bets, and the dependence was direction — the
coin-flip twins, which inherit direction but not entry logic, showed the same structure. Several
confirmations in one direction in a trending market may be one bet; this is how to see that.

**The measure.** Each lineage's daily net R (``result_R`` summed by the day its row closed, zero on
a day it closed nothing), Pearson correlation between every pair, and the effective number of
independent series of the correlation matrix, ``N_eff = (Σλ)² / Σλ² = n² / ‖C‖²_F`` — no eigen
decomposition needed. A short, sparse sample correlates by chance alone and pulls N_eff down, so
every figure comes with a **shuffled-days baseline**: the same series with each lineage's days
permuted independently, which keeps each one's sparsity and destroys any real co-movement. A
measured N_eff well under its baseline is dependence; one near it is noise.

Pure and deterministic (a fixed seed), standard library only — no numpy exists on this host or
in the image. Nothing reads this to decide anything.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Any, Iterable, Mapping, Sequence

# A lineage with fewer closed rows is left out: one or two closes make a series that is almost all
# zeros, whose correlation with anything is noise. Five is the census the decision was made on.
MIN_ROWS_PER_LINEAGE = 5
BASELINE_DRAWS = 20
_SEED = 20260924


def daily_series(rows: Iterable[Mapping[str, Any]], *, min_rows: int = MIN_ROWS_PER_LINEAGE,
                 id_key: str = "candidate_id") -> dict[str, Any]:
    """``{"ids", "days", "series", "direction"}`` for every lineage with ``min_rows`` priced rows."""
    by: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        net = row.get("result_R")
        if isinstance(net, (int, float)) and not isinstance(net, bool) and row.get("created_at_utc") \
                and row.get(id_key):
            by[str(row[id_key])].append(row)
    ids = sorted(k for k, v in by.items() if len(v) >= min_rows)
    if not ids:
        return {"ids": [], "days": [], "series": [], "direction": {}}
    stamps = sorted({str(r["created_at_utc"])[:10] for k in ids for r in by[k]})
    first, last = date.fromisoformat(stamps[0]), date.fromisoformat(stamps[-1])
    days = [(first + timedelta(n)).isoformat() for n in range((last - first).days + 1)]
    index = {d: i for i, d in enumerate(days)}
    series = []
    for k in ids:
        values = [0.0] * len(days)
        for r in by[k]:
            values[index[str(r["created_at_utc"])[:10]]] += float(r["result_R"])
        series.append(values)
    direction = {k: str(by[k][0].get("direction") or "?").upper() for k in ids}
    return {"ids": ids, "days": days, "series": series, "direction": direction}


def correlation(a: Sequence[float], b: Sequence[float]) -> float:
    """Pearson r; 0.0 when either series is flat (nothing to co-move)."""
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((y - mb) ** 2 for y in b))
    if not sa or not sb:
        return 0.0
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def correlation_matrix(series: Sequence[Sequence[float]]) -> list[list[float]]:
    """Pairwise :func:`correlation` of every series, 1.0 on the diagonal."""
    return _matrix(series)


def _matrix(series: Sequence[Sequence[float]]) -> list[list[float]]:
    n = len(series)
    m = [[1.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            m[i][j] = m[j][i] = correlation(series[i], series[j])
    return m


def effective_bets(matrix: Sequence[Sequence[float]], members: Sequence[int] | None = None) -> float | None:
    """``n² / ‖C‖²_F`` over ``members`` (all by default); None under two series."""
    ids = list(range(len(matrix))) if members is None else list(members)
    if len(ids) < 2:
        return None
    return len(ids) ** 2 / sum(matrix[a][b] ** 2 for a in ids for b in ids)


def independence(rows: Iterable[Mapping[str, Any]], *, id_key: str = "candidate_id",
                 min_rows: int = MIN_ROWS_PER_LINEAGE, draws: int = BASELINE_DRAWS) -> dict[str, Any] | None:
    """The census over one set of forward rows; None under two qualifying lineages."""
    data = daily_series(rows, min_rows=min_rows, id_key=id_key)
    ids, series, direction = data["ids"], data["series"], data["direction"]
    if len(ids) < 2:
        return None
    matrix = _matrix(series)
    rng = random.Random(_SEED)
    baseline = []
    for _ in range(draws):
        baseline.append(effective_bets(_matrix([rng.sample(s, len(s)) for s in series])))
    same, opposite, pairs = [], [], []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            together = direction[ids[i]] == direction[ids[j]]
            (same if together else opposite).append(matrix[i][j])
            if together:
                pairs.append((matrix[i][j], ids[i], ids[j]))
    by_direction = {}
    for side in sorted(set(direction.values())):
        members = [i for i, k in enumerate(ids) if direction[k] == side]
        by_direction[side] = {"lineages": len(members), "effective_bets": _round(effective_bets(matrix, members))}
    mean = lambda xs: _round(sum(xs) / len(xs)) if xs else None  # noqa: E731
    return {
        "lineages": len(ids),
        "days": len(data["days"]),
        "first_day": data["days"][0],
        "last_day": data["days"][-1],
        "effective_bets": _round(effective_bets(matrix)),
        "baseline_effective_bets": mean(baseline),
        "mean_corr_same_direction": mean(same),
        "mean_corr_opposite_direction": mean(opposite),
        "by_direction": by_direction,
        "top_same_direction_pairs": [
            {"a": a, "b": b, "corr": _round(r)} for r, a, b in sorted(pairs, reverse=True)[:3]],
    }


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 3)
