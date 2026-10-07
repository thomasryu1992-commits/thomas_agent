"""Two renders of one holdings snapshot, split by where the text may go.

The split is the external-send boundary Thomas set on 2026-10-02 (appendix A of
``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``), carried over unchanged to the Toss account
(appendix B). The broker's data may serve the investor's own trading purpose and may not be
distributed to a third party.

- :func:`aggregate_view` / :func:`render_aggregate` — totals by asset class in KRW, unrealized P&L, a
  count and the weights. No symbol, no name, no per-symbol number (a per-symbol value divided by its
  quantity is a price), and no exchange rate. This is the only render for anything that can leave the
  process: a console verb, a Telegram message, a model prompt.
- :func:`render_full` — every holding with its value. For the account holder's own terminal only.

ASCII labels throughout: Windows consoles are cp949 (the ``crypto.account`` board's reason).
"""

from __future__ import annotations

from typing import Any

from .model import HoldingsSnapshot

# The aggregate view's keys, exactly. A test pins this set: a key added here is a decision about
# what may leave the process, so it has to be added on purpose.
AGGREGATE_KEYS = frozenset({
    "account",
    "broker",
    "collected_at",
    "latency_ms",
    "domestic_stock_krw",
    "overseas_stock_krw",
    "krw_cash",
    "usd_cash_krw",
    "known_total_krw",
    "weights",
    "unrealized_pnl_krw",
    "holding_count",
    "partial",
    "notes",
})

# Cash is cash-based buying power (no margin), not a deposit balance — said wherever it is shown.
CASH_NOTE = "cash = cash buying power, not the deposit balance"


def _sum_known(*values: float | None) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def aggregate_view(snapshot: HoldingsSnapshot) -> dict[str, Any]:
    """The account in numbers that reveal no price. Values the broker did not give are ``None``."""
    domestic = snapshot.domestic
    overseas = snapshot.overseas
    parts = {
        "domestic_stock": domestic.holdings_value_krw if domestic else None,
        "overseas_stock": overseas.holdings_value_krw if overseas else None,
        "krw_cash": domestic.cash_krw if domestic else None,
        "usd_cash": overseas.cash_krw if overseas else None,
    }
    known_total = _sum_known(*parts.values())
    weights = None
    if known_total:
        weights = {
            name: round(value / known_total * 100.0, 2)
            for name, value in parts.items() if value is not None
        }
    missing = [name for name, value in parts.items() if value is None]
    notes = [CASH_NOTE]
    if missing:
        notes.append(f"not read: {', '.join(missing)}; the total is the known parts only")
    if snapshot.warnings:
        # A count, not the warnings: the count is all a reader outside the terminal needs to go and look.
        notes.append(f"{len(snapshot.warnings)} parse warning(s); see the full board")
    return {
        "account": snapshot.account,
        "broker": snapshot.broker,
        "collected_at": snapshot.collected_at,
        "latency_ms": snapshot.latency_ms,
        "domestic_stock_krw": parts["domestic_stock"],
        "overseas_stock_krw": parts["overseas_stock"],
        "krw_cash": parts["krw_cash"],
        "usd_cash_krw": parts["usd_cash"],
        "known_total_krw": known_total,
        "weights": weights,
        "unrealized_pnl_krw": _sum_known(
            domestic.unrealized_pnl_krw if domestic else None,
            overseas.unrealized_pnl_krw if overseas else None,
        ),
        "holding_count": len(snapshot.holdings),
        "partial": bool(missing),
        "notes": notes,
    }


def krw(value: Any) -> str:
    return "n/a" if not isinstance(value, (int, float)) else f"{value:,.0f} KRW"


def render_view(view: dict[str, Any], *, stamp_line: str) -> list[str]:
    """The aggregate's lines, shared by the live render and the stored-snapshot render."""
    weights = view.get("weights") or {}

    def line(label: str, key: str, weight_key: str) -> str:
        weight = weights.get(weight_key)
        share = f" ({weight:.1f}%)" if isinstance(weight, (int, float)) else ""
        return f"{label:12}: {krw(view.get(key))}{share}"

    lines = [
        f"=== holdings {view.get('account', '****')} ({view.get('broker', '?')}) ===",
        line("domestic", "domestic_stock_krw", "domestic_stock"),
        line("overseas", "overseas_stock_krw", "overseas_stock"),
        line("krw cash", "krw_cash", "krw_cash"),
        line("usd cash", "usd_cash_krw", "usd_cash"),
        f"{'known total':12}: {krw(view.get('known_total_krw'))}",
        f"{'unrealized':12}: {krw(view.get('unrealized_pnl_krw'))}",
        f"{'holdings':12}: {view.get('holding_count', 'n/a')}",
        stamp_line,
    ]
    block = view.get("combined")
    if isinstance(block, dict):
        lines.extend(render_combined(block))
    return lines


def render_combined(block: dict[str, Any]) -> list[str]:
    """P2: the Toss total plus the Binance futures account, and the drawdown from peak (alert only)."""
    crypto = krw(block.get("crypto_futures_krw"))
    if block.get("crypto_status") != "ok":
        crypto = f"n/a ({block.get('crypto_status')})"
    state = block.get("drawdown_state")
    drawdown = block.get("drawdown_pct")
    limit = block.get("drawdown_limit_pct")
    if state == "unknown":
        dd = "unknown (a part is missing or stale; peak untouched)"
    elif state == "initialized":
        dd = "peak initialized; no verdict yet"
    else:
        dd = f"{drawdown:+.1f}% (limit {limit:.0f}%){'  !! BREACHED (alert only)' if state == 'breached' else ''}"
    wallet = []
    status = block.get("crypto_wallet_status")
    if status and status != "not_configured":
        def part(key: str) -> str:
            return krw(block.get(key)) if block.get(key) is not None else f"n/a ({status})"
        wallet = [f"{'crypto spot':12}: {part('crypto_spot_krw')}", f"{'crypto earn':12}: {part('crypto_earn_krw')}"]
        classes = block.get("crypto_classes_krw")
        if isinstance(classes, dict):
            wallet.append(f"{'crypto mix':12}: " + " / ".join(f"{name} {krw(value)}" for name, value in classes.items()))
    return [
        "--- all accounts ---",
        f"{'binance':12}: {crypto}" + (f" (as of {block.get('crypto_as_of')})" if block.get("crypto_as_of") else ""),
        *wallet,
        f"{'combined':12}: {krw(block.get('combined_total_krw'))}",
        f"{'peak':12}: {krw(block.get('peak_total_krw'))}" + (f" ({block.get('peak_at')})" if block.get("peak_at") else ""),
        f"{'drawdown':12}: {dd}",
        f"{'note':12}: USDT counted as 1 USD at the Toss mid-rate; deposits/withdrawals read as drawdown",
    ]


def render_aggregate(snapshot: HoldingsSnapshot | None, *, reason_code: str | None = None) -> str:
    """The board that may leave the process. Built only from :func:`aggregate_view`."""
    if snapshot is None:
        return f"holdings    : not available ({reason_code or 'NOT_CONFIGURED'})"
    view = aggregate_view(snapshot)
    lines = render_view(view, stamp_line=f"{'collected':12}: {view['collected_at']} ({view['latency_ms']} ms)")
    lines.extend(f"{'note':12}: {note}" for note in view["notes"])
    return "\n".join(lines)


def render_full(snapshot: HoldingsSnapshot | None, *, reason_code: str | None = None) -> str:
    """Every holding, for the account holder's terminal. Never route this text anywhere else."""
    if snapshot is None:
        return render_aggregate(None, reason_code=reason_code)
    lines = [render_aggregate(snapshot), "--- positions (terminal only) ---"]
    if not snapshot.holdings:
        lines.append("none")
    for holding in snapshot.holdings:
        quantity = "n/a" if holding.quantity is None else f"{holding.quantity:g}"
        value = "n/a" if holding.value is None else f"{holding.value:,.2f}"
        pnl = "n/a" if holding.unrealized_pnl is None else f"{holding.unrealized_pnl:+,.2f}"
        lines.append(
            f"{holding.market:9} {holding.symbol:12} {holding.name} qty {quantity} "
            f"value {value} {holding.currency} upnl {pnl}"
        )
    lines.extend(f"WARNING     : {warning}" for warning in snapshot.warnings)
    return "\n".join(lines)
