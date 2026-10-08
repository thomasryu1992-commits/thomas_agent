"""The board's allocation against the target mix — display only (Q9 of ``TOTAL_ASSET_ALLOCATION_V0.1.md``).

Thomas 2026-10-07: show each asset class's weight next to its target and the 5/25 band (§6 of that
document), as the design in its §11.1 lays out. Nothing here alerts, stores a state of its own or is
read by any door: Q2 and Q4 were dropped as limits the same day, so ``outside`` is a fact on the board
and the monthly rebalance stays Thomas's own act.

**Classified in this process.** Per-symbol rows cannot leave the process (``board`` module docstring,
appendix B's no-third-party rule), so the symbol -> class step runs here and only class totals, weights,
targets and drift reach the stored snapshot. Unclassified symbols are named on the terminal board only.

**The table starts empty** (Thomas 2026-10-07): a symbol not in :data:`CLASSIFICATION` is
"unclassified" and is never guessed into a class. While anything is unclassified, the total is partial
and no band verdict is given (§11.1 point 5).

**Inputs, all from the same fire.** Toss holdings and cash (overseas values at the read's own mid-rate),
the P2 combined block for the Binance futures margin (target 0%: the engine) and, when its gate is on,
the Binance wallet's class totals: BTC and ETH are coin spot, stablecoins are cash (Thomas 2026-10-07),
anything else is unclassified.

**No band verdict without a portfolio NAV** (H2, Thomas 2026-10-08, D-H2-8). The weights are judged on the
same declared scope as the P2 total: when ``combined.portfolio_nav_complete`` is not true — a declared
source left out (the wallet's gate off included), a part not fully valued, a stale futures file — the
block is partial and gives no weight and no band.
"""

from __future__ import annotations

from typing import Any, Mapping

from .disclosure import Part
from .model import HoldingsSnapshot

GLOBAL_EQUITY = "global_equity"
DOMESTIC_EQUITY = "domestic_equity"
BONDS = "bonds"
GOLD = "gold"
COIN = "coin"
CASH = "cash"
ENGINE_MARGIN = "engine_margin"
CLASSES = (GLOBAL_EQUITY, DOMESTIC_EQUITY, BONDS, GOLD, COIN, CASH, ENGINE_MARGIN)
# The table's other row: what the table cannot place (H3 treats it as a row like any class).
UNCLASSIFIED = "unclassified"

# §4 after Q6 and Q8 (2026-10-07). The engine is 0% (Q5), so any margin shows as drift (Q10).
TARGET_PCT: Mapping[str, float] = {
    GLOBAL_EQUITY: 30.0,
    DOMESTIC_EQUITY: 10.0,
    BONDS: 37.0,
    GOLD: 13.0,
    COIN: 2.5,
    CASH: 7.5,
    ENGINE_MARGIN: 0.0,
}

# §6: band b = min(5pp, 0.25 x target), judged on stock as one 40% sleeve (its table: "주식 40 -> ±5%p").
# Cash has no band (§6: "현금에는 사실상 밴드를 두지 않는다").
BAND_GROUPS: Mapping[str, tuple[str, ...]] = {
    "equity": (GLOBAL_EQUITY, DOMESTIC_EQUITY),
    BONDS: (BONDS,),
    GOLD: (GOLD,),
    COIN: (COIN,),
    ENGINE_MARGIN: (ENGINE_MARGIN,),
}

# Toss symbol -> class. Empty by decision (Thomas 2026-10-07): a code is added when it is bought.
CLASSIFICATION: Mapping[str, str] = {}

# The wallet's classes (``binance_wallet.CLASSES``), mapped here rather than imported so the wallet
# module stays free of the allocation's vocabulary. ``other`` is deliberately absent: unclassified.
WALLET_CLASS_MAP: Mapping[str, str] = {"btc": COIN, "eth": COIN, "stable": CASH}

# The block's keys, exactly. It rides the stored snapshot, so a key here is a decision about what may
# leave the process (the same rule as ``board.AGGREGATE_KEYS``).
ALLOCATION_KEYS = frozenset({
    "complete",
    "total_krw",
    "classes",
    "bands",
    "outside_band",
    "cash_stablecoin_krw",
    "unclassified_count",
    "unclassified_krw",
    "notes",
})
CLASS_ROW_KEYS = frozenset({"krw", "weight_pct", "target_pct", "drift_pp"})
BAND_ROW_KEYS = frozenset({"weight_pct", "target_pct", "band_pp", "drift_pp", "outside"})


def band_pp(target_pct: float) -> float:
    return min(5.0, 0.25 * target_pct)


def _holding_krw(value: float | None, currency: str, rate: float | None) -> float | None:
    if value is None:
        return None
    if currency.upper() == "KRW":
        return value
    if currency.upper() == "USD" and rate is not None:
        return value * rate
    return None


def unclassified_symbols(snapshot: HoldingsSnapshot) -> list[str]:
    """For the terminal board only: which codes the table is missing."""
    return sorted({h.symbol for h in snapshot.holdings if h.symbol not in CLASSIFICATION})


def allocate(snapshot: HoldingsSnapshot, toss_view: Mapping[str, Any],
             combined_block: Mapping[str, Any] | None, wallet: Any = None) -> dict[str, Any]:
    """Class totals, weights against target and the band facts. Never raises on missing data: a
    missing part makes the block partial, and a partial block gives no band verdict."""
    return allocate_with_parts(snapshot, toss_view, combined_block, wallet)[0]


def allocate_with_parts(snapshot: HoldingsSnapshot, toss_view: Mapping[str, Any],
                        combined_block: Mapping[str, Any] | None,
                        wallet: Any = None) -> tuple[dict[str, Any], dict[str, Part]]:
    """:func:`allocate`, and each row's makeup for the single-holding rule (H3, ``disclosure``): which
    instruments it holds and how many cash balances. The makeup stays in this process.

    ``wallet`` is the ``binance_wallet.WalletSnapshot`` the fire read, for its ``class_assets``. A wallet
    class with value but no makeup (no snapshot, or one without it) counts as one instrument, so it is
    withheld rather than guessed safe."""
    krw = {name: 0.0 for name in CLASSES}
    instruments: dict[str, set[str]] = {name: set() for name in (*CLASSES, UNCLASSIFIED)}
    cash_parts: dict[str, int] = {name: 0 for name in (*CLASSES, UNCLASSIFIED)}
    gaps: list[str] = []
    notes: list[str] = []
    unclassified_count = 0
    unclassified_krw = 0.0
    stable_krw = 0.0

    if toss_view.get("partial"):
        gaps.append("toss partial")
    for holding in snapshot.holdings:
        value = _holding_krw(holding.value, holding.currency, snapshot.usd_krw_rate)
        if value is None:
            gaps.append("a holding's value or rate unread")
            continue
        name = CLASSIFICATION.get(holding.symbol)
        if value:
            instruments[name or UNCLASSIFIED].add(f"toss:{holding.market}:{holding.symbol}")
        if name is None:
            unclassified_count += 1
            unclassified_krw += value
        else:
            krw[name] += value
    for key in ("krw_cash", "usd_cash_krw"):
        cash = toss_view.get(key)
        if isinstance(cash, (int, float)):
            krw[CASH] += cash
            cash_parts[CASH] += 1 if cash else 0

    block = combined_block or {}
    futures = block.get("crypto_futures_krw")
    if block.get("crypto_status") == "ok" and isinstance(futures, (int, float)):
        krw[ENGINE_MARGIN] += futures
        cash_parts[ENGINE_MARGIN] += 1 if futures else 0   # a USDT margin balance, not an instrument
    else:
        gaps.append(f"binance futures {block.get('crypto_status') or 'not combined'}")
    wallet_status = block.get("crypto_wallet_status")
    wallet_classes = block.get("crypto_classes_krw")
    if wallet_status == "ok" and isinstance(wallet_classes, dict):
        makeup = getattr(wallet, "class_assets", None) or {}
        for wallet_class, value in wallet_classes.items():
            if not isinstance(value, (int, float)) or value == 0:
                continue
            name = WALLET_CLASS_MAP.get(wallet_class)
            assets = makeup.get(wallet_class) or {f"?{wallet_class}"}
            instruments[name or UNCLASSIFIED].update(f"binance:{asset}" for asset in assets)
            if name is None:
                unclassified_count += 1
                unclassified_krw += value
                continue
            krw[name] += value
            if wallet_class == "stable":
                stable_krw += value
    elif wallet_status in (None, "not_configured"):
        gaps.append("binance spot/earn not read (gate off)")
    else:
        gaps.append(f"binance wallet {wallet_status}")
    if block and block.get("portfolio_nav_complete") is not True and not gaps:
        gaps.append("portfolio NAV incomplete")

    if krw[ENGINE_MARGIN] > 0:
        notes.append("engine margin target is 0% by decision (Q5, Q10): its balance shows as drift")
    if unclassified_count:
        gaps.append(f"{unclassified_count} unclassified")
    total = sum(krw.values()) + unclassified_krw
    complete = not gaps and total > 0
    if total <= 0:
        gaps.append("nothing held")
    notes[:0] = [f"partial total ({'; '.join(gaps)}): no band verdict"] if gaps else []

    def weight(value: float) -> float | None:
        return round(value / total * 100.0, 2) if complete else None

    classes = {}
    for name in CLASSES:
        w = weight(krw[name])
        classes[name] = {
            "krw": krw[name],
            "weight_pct": w,
            "target_pct": TARGET_PCT[name],
            "drift_pp": None if w is None else round(w - TARGET_PCT[name], 2),
        }
    bands = None
    outside = None
    if complete:
        bands = {}
        for group, members in BAND_GROUPS.items():
            w = round(sum(krw[name] for name in members) / total * 100.0, 2)
            target = sum(TARGET_PCT[name] for name in members)
            drift = round(w - target, 2)
            bands[group] = {"weight_pct": w, "target_pct": target, "band_pp": band_pp(target),
                            "drift_pp": drift, "outside": abs(drift) > band_pp(target)}
        outside = [group for group, row in bands.items() if row["outside"]]
    parts = {name: Part(krw[name], frozenset(instruments[name]), cash_parts[name]) for name in CLASSES}
    parts[UNCLASSIFIED] = Part(unclassified_krw, frozenset(instruments[UNCLASSIFIED]), 0)
    return {
        "complete": complete,
        "total_krw": total if total > 0 else None,
        "classes": classes,
        "bands": bands,
        "outside_band": outside,
        "cash_stablecoin_krw": stable_krw,
        "unclassified_count": unclassified_count,
        "unclassified_krw": unclassified_krw,
        "notes": notes,
    }, parts
