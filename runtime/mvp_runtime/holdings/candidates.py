"""Rebalancing candidates, picked automatically (REBALANCING_PROPOSAL §2.7; RB2'' Thomas 2026-10-26).

RB2'' replaced "Thomas approves 3-4 per class" with an automatic pick: once a month, and at once when a pick
stops trading, the holdings fire screens the ETFs Toss trades and keeps, per class, the three with the highest
20-session trading value; bonds keep three Korean 3y plus one unhedged US 7-10y, so the calculator's even fill
holds the decided quarter. ``holdings_board --approve-candidates`` stays as Thomas's override for a class, and
the automatic pick never touches a class he set.

**The exposures are already decided** (TOTAL_ASSET_ALLOCATION_V0.1):
- global equity: a global index ("글로벌 지수, 환노출"), held as a US-listed ETF in the ordinary account (§8.4:
  overseas direct, to use the 2.5M deduction). A US-only index such as the S&P 500 is not a global one;
- domestic equity: the KOSPI 200, as a domestic equity ETF (gain tax-free);
- bonds: Korean 3-year government bonds at the centre; a quarter in US Treasuries 7-10y, unhedged, as a
  domestic-listed ETF (D-A, 2026-10-20);
- gold: a Toss-traded gold ETF (Q3).

**What Toss cannot say.** There is no index, hedge or expense-ratio field (``openapi.json`` v1.2.24,
``StockInfo``). Exposure is matched by name, the hedge by Korea's "(H)" naming convention, and the expense ratio
is not a criterion. Toss's listing holds only what Toss trades (``/api/v1/stocks/all``).

:func:`pool`, :func:`rank`, :func:`auto_picks` and :func:`render` are pure; :func:`collect` does the paced reads
through the feed it is handed. The rows are raw market data: Toss's terms allow the investor's own use only, so
they stay in the process, and only the picked instrument ids are written (a local file).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from . import allocation
from .rebalance import TAX_FREE, TAXED_DIVIDEND, OVERSEAS, Position, tax_kind

KR_MARKETS = ("KOSPI", "KOSDAQ", "KR_ETC")
US_MARKETS = ("NYSE", "NASDAQ", "AMEX")
LIQUIDITY_DAYS = 20
# A shortlist bound per rule: the reads per candidate are one info row and one candle page.
POOL_LIMIT = 80
SHOWN = 4
PICKS = 3

_ALWAYS_OUT = re.compile(r"레버리지|인버스|커버드콜|covered call|buywrite|\b[23]x\b|ultra|bear|short|2배|3배", re.I)
HEDGED = re.compile(r"\(H\)|hedged|헤지", re.I)


@dataclass(frozen=True)
class Rule:
    asset_class: str
    key: str               # the shortlist's name in the report
    label: str
    markets: tuple[str, ...]
    match: re.Pattern[str]
    out: re.Pattern[str] | None = None     # what the name match lets through but the exposure is not


RULES = (
    Rule(allocation.GLOBAL_EQUITY, "global_equity", "US-listed global index ETF (total world, ACWI, all-world)",
         US_MARKETS, re.compile(r"total world|all[- ]?world|all[- ]?country|\bacwi\b|토탈\s?월드|전세계", re.I),
         re.compile(r"ex[- ]?us|ex[- ]?u\.s|\bex\b|growth|value|dividend|esg|sri|min(imum)? vol|momentum|quality|"
                    r"equal|small|mid|bond|real estate|infrastructure|배당|성장|가치", re.I)),
    Rule(allocation.DOMESTIC_EQUITY, "domestic_equity", "KOSPI 200 ETF", KR_MARKETS,
         re.compile(r"(코스피|kospi)\s?200|^\S+\s200(tr)?$", re.I),
         re.compile(r"선물|동일가중|고배당|배당|가치|성장|중소형|섹터|건설|금융|산업|에너지|정보기술|헬스케어", re.I)),
    Rule(allocation.BONDS, "bonds_kr3y", "Korean government bond 3y ETF (the centre of bonds)", KR_MARKETS,
         re.compile(r"국고채\s?3년", re.I)),
    Rule(allocation.BONDS, "bonds_us7_10y", "US Treasury 7-10y ETF, domestic-listed (a quarter of bonds, unhedged)",
         KR_MARKETS, re.compile(r"미국채\s?(7\s?-\s?10|10)\s?년|미국\s?국채\s?(7\s?-\s?10|10)\s?년", re.I)),
    Rule(allocation.GOLD, "gold", "gold ETF", KR_MARKETS, re.compile(r"골드|금현물|금선물|krx\s?금|\bgold\b", re.I),
         re.compile(r"채굴|광산|miner|골드만", re.I)),
)


def instrument(symbol: str, market: str) -> str:
    return f"toss:{'domestic' if market in KR_MARKETS else 'overseas'}:{symbol}"


def _names(row: Mapping[str, Any]) -> list[str]:
    return [str(row[k]).strip() for k in ("name", "englishName") if row.get(k)]


def pool(listings: Mapping[str, Iterable[Mapping[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    """Each rule's shortlist from the market listings: the name matches, and nothing in it is leverage, inverse,
    covered call or the rule's own exclusions."""
    out: dict[str, list[dict[str, Any]]] = {rule.key: [] for rule in RULES}
    for rule in RULES:
        for market in rule.markets:
            for row in listings.get(market) or ():
                names = _names(row)
                if not row.get("symbol") or not any(rule.match.search(n) for n in names):
                    continue
                if any(_ALWAYS_OUT.search(n) or (rule.out is not None and rule.out.search(n)) for n in names):
                    continue
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


def auto_picks(ranked: Mapping[str, list[Candidate]]) -> dict[str, list[str]]:
    """RB2'': per class, the PICKS most-traded candidates with a known trading value; bonds take three Korean 3y
    plus the most-traded unhedged US 7-10y (the calculator fills candidates evenly, so 3 + 1 holds the decided
    quarter). A class with nothing tradable maps to an empty list: the caller keeps the last pick for it."""
    return {allocation.GLOBAL_EQUITY: _top(ranked, "global_equity", PICKS),
            allocation.DOMESTIC_EQUITY: _top(ranked, "domestic_equity", PICKS),
            allocation.BONDS: (_top(ranked, "bonds_kr3y", PICKS) + us_bond_pick(ranked)
                               if _top(ranked, "bonds_kr3y", PICKS) else []),
            allocation.GOLD: _top(ranked, "gold", PICKS)}


def _top(ranked: Mapping[str, list[Candidate]], key: str, n: int, unhedged_only: bool = False) -> list[str]:
    rows = [c for c in ranked.get(key, []) if c.avg_value_krw and not (unhedged_only and c.fx == "hedged")]
    return [c.instrument for c in rows[:n]]


def us_bond_pick(ranked: Mapping[str, list[Candidate]]) -> list[str]:
    """The bond pick that is the US Treasury quarter (D-A), so a buy can tell the two bond slots apart."""
    return _top(ranked, "bonds_us7_10y", 1, unhedged_only=True)


# Toss's per-group limits (overview "Rate Limits", 2026-10-26): STOCK_ALL 1/s, STOCK 5/s, MARKET_DATA_CHART 20/s.
PAUSE_SECONDS = {"listing": 1.1, "info": 0.25, "candles": 0.06}


def collect(feed: Any, *, timeout: int = 10,
            sleep: Callable[[float], None] = time.sleep) -> tuple[dict[str, list[Candidate]], float | None]:
    """Every read the screen needs, through ``feed`` (a ``TossHoldingsFeed``), paced under Toss's limits."""
    from .toss_account import STOCKS_PAGE_LIMIT

    fees = {}
    for row in feed.commission_rates(timeout_seconds=timeout):
        try:
            fees[str(row.get("marketCountry"))] = float(row.get("commissionRate"))
        except (TypeError, ValueError):
            continue
    usd_krw = feed.usd_krw_rate(timeout_seconds=timeout)
    listings = {}
    for market in (*KR_MARKETS, *US_MARKETS):
        listings[market] = feed.listed_etfs(market, timeout_seconds=timeout)
        sleep(PAUSE_SECONDS["listing"])
    shortlists = pool(listings)
    symbols = sorted({row["symbol"] for rows in shortlists.values() for row in rows})
    info = {}
    for start in range(0, len(symbols), STOCKS_PAGE_LIMIT):
        for row in feed.stock_info(symbols[start:start + STOCKS_PAGE_LIMIT], timeout_seconds=timeout):
            info[str(row.get("symbol"))] = row
        sleep(PAUSE_SECONDS["info"])
    bars = {}
    for symbol in symbols:
        bars[symbol] = feed.daily_candles(symbol, count=LIQUIDITY_DAYS, timeout_seconds=timeout)
        sleep(PAUSE_SECONDS["candles"])
    return rank(shortlists, info=info, candles=bars, fees=fees, usd_krw=usd_krw), usd_krw


_TAX_LABEL = {TAX_FREE: "tax-free gain", TAXED_DIVIDEND: "15.4% (dividend income)",
              OVERSEAS: "22% above the 2.5M deduction"}


def render(ranked: Mapping[str, list[Candidate]], *, fetched_at: str, usd_krw: float | None) -> list[str]:
    lines = ["REBALANCE CANDIDATES - terminal only; raw market data, never a chat, a model prompt or a channel",
             f"read at      : {fetched_at}   USD/KRW {usd_krw if usd_krw else 'unknown (US liquidity not converted)'}",
             f"liquidity    : mean daily close × volume over the last {LIQUIDITY_DAYS} sessions, KRW",
             "expense ratio: not in Toss's API, so not a criterion"]
    rules = {rule.key: rule for rule in RULES}
    for key, rows in ranked.items():
        rule = rules[key]
        lines += ["", f"[{rule.asset_class}] {rule.label} — {len(rows)} matched"]
        if not rows:
            lines.append("  none matched")
        for i, c in enumerate(rows[:SHOWN], 1):
            value = "unknown" if c.avg_value_krw is None else f"{c.avg_value_krw / 1e8:,.1f}억/day ({c.days}d)"
            fee = "?" if c.fee_rate is None else f"{c.fee_rate * 100:.3f}%"
            lines.append(f"  {i}. {c.instrument:26} {c.name[:28]:28} {value:>22}  fee {fee}  "
                         f"tax {_TAX_LABEL.get(c.tax_kind, c.tax_kind)}  fx {c.fx}" + (f"  ({c.note})" if c.note else ""))
        if len(rows) > SHOWN:
            lines.append(f"  … {len(rows) - SHOWN} more below these by liquidity")
    lines += ["", "the holdings fire picks these automatically once a month (RB2''):"]
    lines += [f"  {cls}: {', '.join(ids) or 'nothing tradable (the last pick is kept)'}"
              for cls, ids in auto_picks(ranked).items()]
    return lines
