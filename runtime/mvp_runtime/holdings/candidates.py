"""The candidate screening report (REBALANCING_PROPOSAL §2.7, RB2', Thomas 2026-10-26).

Once per class, not per rebalance: this lists the ETFs Toss trades that fit each class's decided exposure, with
the criteria Toss can answer for. Thomas picks three or four and approves them (``holdings_board
--approve-candidates``); the calculator then buys inside that list only.

**The exposures are already decided** (TOTAL_ASSET_ALLOCATION_V0.1):
- global equity: a broad index, held as a US-listed ETF in the ordinary account (§8.4: overseas direct, to use
  the 2.5M deduction);
- domestic equity: the KOSPI 200, as a domestic equity ETF (gain tax-free);
- bonds: Korean 3-year government bonds at the centre; a quarter in US Treasuries 7-10y, unhedged, as a
  domestic-listed ETF (D-A, 2026-10-20);
- gold: a Toss-traded gold ETF (Q3).

**What Toss cannot say.** There is no index, hedge or expense-ratio field (``openapi.json`` v1.2.24,
``StockInfo``). Exposure is matched by name, the hedge by Korea's "(H)" naming convention, and the expense
ratio is left for Thomas to read off the issuer's page. The match is a shortlist, never a verdict.

**Pure.** The script (``scripts/rebalance_candidates.py``) does the reads. The rows are raw market data: Toss's
terms allow the investor's own use only, so the report is for Thomas's terminal, never a model prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from . import allocation
from .rebalance import TAX_FREE, TAXED_DIVIDEND, OVERSEAS, Position, tax_kind

KR_MARKETS = ("KOSPI", "KOSDAQ", "KR_ETC")
US_MARKETS = ("NYSE", "NASDAQ", "AMEX")
LIQUIDITY_DAYS = 20
# A shortlist bound: the reads per candidate are one info row and one candle page.
POOL_LIMIT = 40
SHOWN = 4

_ALWAYS_OUT = re.compile(r"레버리지|인버스|커버드콜|covered call|\b[23]x\b|ultra|bear|short|2배|3배", re.I)
HEDGED = re.compile(r"\(H\)|hedged|헤지", re.I)


@dataclass(frozen=True)
class Rule:
    asset_class: str
    key: str               # the shortlist's name in the report
    label: str
    markets: tuple[str, ...]
    match: re.Pattern[str]


RULES = (
    Rule(allocation.GLOBAL_EQUITY, "global_equity", "US-listed broad index ETF (S&P 500, total world, ACWI)",
         US_MARKETS, re.compile(r"s&p\s?500|total world|all[- ]?world|all[- ]?country|acwi|total (stock|us) market|"
                                r"토탈\s?월드|전세계", re.I)),
    Rule(allocation.DOMESTIC_EQUITY, "domestic_equity", "KOSPI 200 ETF", KR_MARKETS,
         re.compile(r"(코스피|kospi)\s?200|^\S+\s200(tr)?$", re.I)),
    Rule(allocation.BONDS, "bonds_kr3y", "Korean government bond 3y ETF (the centre of bonds)", KR_MARKETS,
         re.compile(r"국고채\s?3년", re.I)),
    Rule(allocation.BONDS, "bonds_us7_10y", "US Treasury 7-10y ETF, domestic-listed (a quarter of bonds, unhedged)",
         KR_MARKETS, re.compile(r"미국채\s?(7\s?-\s?10|10)\s?년|미국\s?국채\s?(7\s?-\s?10|10)\s?년", re.I)),
    Rule(allocation.GOLD, "gold", "gold ETF", KR_MARKETS, re.compile(r"골드|금현물|금선물|krx\s?금|\bgold\b", re.I)),
)


def instrument(symbol: str, market: str) -> str:
    return f"toss:{'domestic' if market in KR_MARKETS else 'overseas'}:{symbol}"


def _names(row: Mapping[str, Any]) -> list[str]:
    return [str(row[k]).strip() for k in ("name", "englishName") if row.get(k)]


def pool(listings: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Each rule's shortlist from the market listings: name matches, leverage, inverse and covered-call out."""
    out: dict[str, list[dict[str, Any]]] = {rule.key: [] for rule in RULES}
    for rule in RULES:
        for market in rule.markets:
            for row in listings.get(market) or ():
                names = _names(row)
                if (row.get("symbol") and any(rule.match.search(n) for n in names)
                        and not any(_ALWAYS_OUT.search(n) for n in names)):
                    out[rule.key].append({"symbol": str(row["symbol"]), "name": str(row.get("name") or ""),
                                          "market": market})
        out[rule.key] = sorted(out[rule.key], key=lambda r: r["symbol"])[:POOL_LIMIT]
    return out


@dataclass(frozen=True)
class Candidate:
    instrument: str
    name: str
    market: str
    avg_value_krw: float | None      # mean daily close × volume over the last LIQUIDITY_DAYS candles
    days: int
    fee_rate: float | None
    tax_kind: str
    fx: str                          # KRW / USD / hedged
    note: str | None


def _avg_value(candles: Iterable[Mapping[str, Any]], usd_krw: float | None, us: bool) -> tuple[float | None, int]:
    values = []
    for candle in list(candles)[:LIQUIDITY_DAYS]:
        try:
            values.append(float(candle["closePrice"]) * float(candle["volume"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return None, 0
    mean = sum(values) / len(values)
    if us:
        return (None if usd_krw is None else mean * usd_krw), len(values)
    return mean, len(values)


def rank(shortlists: Mapping[str, list[dict[str, Any]]], *, info: Mapping[str, Mapping[str, Any]],
         candles: Mapping[str, list[Mapping[str, Any]]], fees: Mapping[str, float],
         usd_krw: float | None) -> dict[str, list[Candidate]]:
    """Each shortlist, inactive and leveraged rows out, by liquidity (highest first; unknown last)."""
    rules = {rule.key: rule for rule in RULES}
    out: dict[str, list[Candidate]] = {}
    for key, rows in shortlists.items():
        rule = rules[key]
        ranked = []
        for row in rows:
            symbol, market = row["symbol"], row["market"]
            detail = info.get(symbol) or {}
            if detail.get("status") not in (None, "ACTIVE"):
                continue
            try:
                if float(detail.get("leverageFactor") or 1.0) != 1.0:
                    continue
            except (TypeError, ValueError):
                continue                                 # an unreadable factor is not a plain index ETF
            us = market in US_MARKETS
            value, days = _avg_value(candles.get(symbol) or (), usd_krw, us)
            ident = instrument(symbol, market)
            kind = tax_kind(Position(ident, rule.asset_class, None, 0.0, None))
            hedged = bool(HEDGED.search(row["name"]))
            fx = "USD" if us else ("hedged" if hedged else ("USD" if key == "bonds_us7_10y" else "KRW"))
            note = "hedged; D-A decided unhedged" if key == "bonds_us7_10y" and hedged else None
            ranked.append(Candidate(ident, row["name"], market, value, days, fees.get("US" if us else "KR"),
                                    kind, fx, note))
        out[key] = sorted(ranked, key=lambda c: (c.avg_value_krw is None, -(c.avg_value_krw or 0.0), c.instrument))
    return out


_TAX_LABEL = {TAX_FREE: "tax-free gain", TAXED_DIVIDEND: "15.4% (dividend income)",
              OVERSEAS: "22% above the 2.5M deduction"}


def _example(ranked: Mapping[str, list[Candidate]]) -> list[str]:
    """An example approve line per class, by liquidity alone. Thomas edits it; it is not a choice."""
    def top(key: str, n: int, unhedged_only: bool = False) -> list[str]:
        rows = [c for c in ranked.get(key, []) if c.avg_value_krw and not (unhedged_only and c.fx == "hedged")]
        return [c.instrument for c in rows[:n]]

    picks = {allocation.GLOBAL_EQUITY: top("global_equity", 3), allocation.DOMESTIC_EQUITY: top("domestic_equity", 3),
             # Three Korean 3y plus one US 7-10y: the calculator fills approved candidates evenly, so 3 + 1 holds
             # the decided quarter.
             allocation.BONDS: top("bonds_kr3y", 3) + top("bonds_us7_10y", 1, unhedged_only=True),
             allocation.GOLD: top("gold", 3)}
    return [f"  --approve-candidates {cls}={','.join(ids)}" for cls, ids in picks.items() if ids]


def render(ranked: Mapping[str, list[Candidate]], *, fetched_at: str, usd_krw: float | None) -> list[str]:
    lines = ["REBALANCE CANDIDATES - terminal only; raw market data, never a chat, a model prompt or a channel",
             f"read at      : {fetched_at}   USD/KRW {usd_krw if usd_krw else 'unknown (US liquidity not converted)'}",
             f"liquidity    : mean daily close × volume over the last {LIQUIDITY_DAYS} sessions, KRW",
             "expense ratio: not in Toss's API — read it on the issuer's page before approving"]
    rules = {rule.key: rule for rule in RULES}
    for key, rows in ranked.items():
        rule = rules[key]
        lines += ["", f"[{rule.asset_class}] {rule.label} — {len(rows)} matched"]
        if not rows:
            lines.append("  none matched; widen the rule or name the ETF yourself")
        for i, c in enumerate(rows[:SHOWN], 1):
            value = "unknown" if c.avg_value_krw is None else f"{c.avg_value_krw / 1e8:,.1f}억/day ({c.days}d)"
            fee = "?" if c.fee_rate is None else f"{c.fee_rate * 100:.3f}%"
            lines.append(f"  {i}. {c.instrument:26} {c.name[:28]:28} {value:>22}  fee {fee}  "
                         f"tax {_TAX_LABEL.get(c.tax_kind, c.tax_kind)}  fx {c.fx}" + (f"  ({c.note})" if c.note else ""))
        if len(rows) > SHOWN:
            lines.append(f"  … {len(rows) - SHOWN} more below these by liquidity")
    lines += ["", "example (by liquidity only — edit it; choosing is Thomas's):", *_example(ranked)]
    return lines
