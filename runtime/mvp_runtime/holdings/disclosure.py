"""H3: what of a holdings fire may leave the process — one table, and no single instrument's amount.

Thomas 2026-10-08 (``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``, H3 decision). The stored snapshot is
read by the doors (the Telegram ``/holdings`` verb, the assistant's ``holdings_status``), so whatever it
holds can reach a channel or a model provider. The invariant:

    No externally visible aggregate may reveal a single instrument's amount by construction.

**One table.** Every breakdown is a set of rows that sum to a total, and two breakdowns of the same
money can be subtracted from each other: the cash class minus the Toss cash is the stablecoins, and the
spot plus Earn totals minus those is the coins. No per-table rule survives that. So exactly one
breakdown leaves — the allocation table (asset classes plus the unclassified row, against the portfolio
NAV) — and every other (Toss by market, Binance by wallet, the crypto sub-classes, the stablecoin share)
goes only to the local file, which no door reads (``store.LOCAL_FILENAME``).

**Within that table** (:func:`withheld`), a row is withheld when it holds exactly one instrument and no
cash balance. A withheld row would still follow from the total minus the published rows, so the
withheld set grows — smallest published row first — until it covers at least two holdings, counting
each cash balance as one. When the whole table is one instrument, the total itself is that
instrument's amount and is withheld too (:func:`whole_is_one_instrument`). A withheld row's weight,
drift and band go with it: each is its amount divided by the total.

What counts as an instrument is the row's makeup (``allocation.allocate_with_parts``): a Toss symbol, a
Binance asset — the same asset in spot and Earn is one — never a cash balance or the futures margin.
The makeup never leaves the process; a count of withheld rows does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

# Keys of the Toss aggregate that are a breakdown other than the one table, or a sum of one: local only.
LOCAL_ONLY_TOP_KEYS = frozenset({
    "domestic_stock_krw",
    "overseas_stock_krw",
    "krw_cash",
    "usd_cash_krw",
    "known_total_krw",
    "weights",
    "unrealized_pnl_krw",
})
# The combined block's per-venue and per-sub-class figures: local only.
LOCAL_ONLY_COMBINED_KEYS = frozenset({
    "crypto_futures_krw",
    "crypto_spot_krw",
    "crypto_earn_krw",
    "crypto_classes_krw",
})
# The allocation block's sub-class figure: local only.
LOCAL_ONLY_ALLOCATION_KEYS = frozenset({"cash_stablecoin_krw"})
# Added to the allocation block outside: which rows were withheld (names only).
WITHHELD_KEY = "withheld"

WITHHELD_NOTE = "withheld under the single-holding rule (H3): {rows}; amounts on the local board only"


@dataclass(frozen=True)
class Part:
    """One row of the table: its KRW value and what it is made of (in-process only)."""

    value: float | None
    instruments: frozenset[str] = frozenset()
    cash_parts: int = 0


def _nonzero(part: Part) -> bool:
    return isinstance(part.value, (int, float)) and part.value != 0


def _single(part: Part) -> bool:
    return _nonzero(part) and len(part.instruments) == 1 and part.cash_parts == 0


def _covered(parts: Mapping[str, Part], names: set[str]) -> int:
    instruments: set[str] = set()
    cash = 0
    for name in names:
        part = parts[name]
        if _nonzero(part):
            instruments |= part.instruments
            cash += part.cash_parts
    return len(instruments) + cash


def withheld(parts: Mapping[str, Part]) -> set[str]:
    """The rows that may not leave: every single-instrument row, then the smallest others until the
    withheld set covers two holdings (module docstring)."""
    out = {name for name, part in parts.items() if _single(part)}
    while out and _covered(parts, out) < 2:
        rest = sorted((abs(part.value), name) for name, part in parts.items()
                      if name not in out and _nonzero(part))
        if not rest:
            break
        out.add(rest[0][1])
    return out


def whole_is_one_instrument(parts: Mapping[str, Part]) -> bool:
    """The table holds exactly one instrument and nothing else: its total is that instrument's amount."""
    held = {name for name, part in parts.items() if _nonzero(part)}
    if not held:
        return False
    cash = sum(parts[name].cash_parts for name in held)
    instruments = set().union(*(parts[name].instruments for name in held))
    return cash == 0 and len(instruments) == 1


def external_view(body: Mapping[str, Any], parts: Mapping[str, Part]) -> dict[str, Any]:
    """The stored snapshot: ``body`` (the full fire) with every other breakdown removed and the one
    table's withheld rows emptied. ``parts`` is the allocation's makeup, from the same fire."""
    from .allocation import BAND_GROUPS, UNCLASSIFIED

    out = {key: value for key, value in body.items() if key not in LOCAL_ONLY_TOP_KEYS}
    hidden = withheld(parts)
    one = whole_is_one_instrument(parts)

    block = body.get("combined")
    if isinstance(block, dict):
        block = {key: value for key, value in block.items() if key not in LOCAL_ONLY_COMBINED_KEYS}
        if one:
            block["combined_total_krw"] = None
            block["peak_total_krw"] = None
        out["combined"] = block

    table = body.get("allocation")
    if isinstance(table, dict):
        table = {key: value for key, value in table.items() if key not in LOCAL_ONLY_ALLOCATION_KEYS}
        classes = {}
        for name, row in (table.get("classes") or {}).items():
            row = dict(row)
            if name in hidden:
                row.update({"krw": None, "weight_pct": None, "drift_pp": None})
            classes[name] = row
        table["classes"] = classes
        if UNCLASSIFIED in hidden:
            table["unclassified_krw"] = None
        if isinstance(table.get("bands"), dict):
            table["bands"] = {group: row for group, row in table["bands"].items()
                              if not set(BAND_GROUPS.get(group, (group,))) & hidden}
            table["outside_band"] = [group for group in table.get("outside_band") or []
                                     if group in table["bands"]]
        if not table.get("complete") or one:
            table["total_krw"] = None      # D-H2-7: no partial sum; and a one-instrument total is that instrument
        table[WITHHELD_KEY] = sorted(hidden)
        if hidden:
            table["notes"] = [*(table.get("notes") or []), WITHHELD_NOTE.format(rows=", ".join(sorted(hidden)))]
        out["allocation"] = table
    return out
