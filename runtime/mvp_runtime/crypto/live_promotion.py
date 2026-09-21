"""LP6 live-canary evidence (source L5) — history, read-only.

A canary was one small real mainnet order placed by the operator on purpose, to prove the signing,
submission and reconciliation path against the live venue before anything autonomous used it. The
registry recorded each one, and its clean count used to be the final guard's promotion gate: no
autonomous live entry until enough canaries had reconciled cleanly. Thomas removed the canary door
and that gate together on 2026-09-15 (PR1r), so nothing counts these rows toward anything now.

They are still a record of what the live path did, so the read stays verified: a registry that is
unreadable, tampered or duplicated raises rather than rendering, because a board showing a row that
cannot prove itself would vouch for it. The rows are read as they sit on disk — never normalised —
by the evidence board below and by ``scripts/record_unreported_live_order.py``.

Nothing here writes any more. The registry's only writer was the canary door, and it went with the
door. ``RECONCILED`` is the reconcile vocabulary's, defined in ``live_execution`` and re-exported here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from runtime.read_only_kernel import integrity

from .. import jsonl
from ..errors import ToolError
from .live_execution import RECONCILED  # noqa: F401  (re-exported: scripts/run_slippage_probe.py reads it here)
from .live_pnl import state_dir

# The shape of the rows already on disk: their file and the provenance they carry. Kept after the
# writer went so the history stays nameable, and so neither string is reused for something else.
CANARY_ORDERS_FILENAME = "live_canary_orders.jsonl"
CANARY_PROVENANCE = "mvp_live_canary"


CANARY_HISTORY_UNREADABLE = "CANARY_HISTORY_UNREADABLE"
CANARY_HISTORY_TAMPERED = "CANARY_HISTORY_TAMPERED"
CANARY_HISTORY_DUPLICATE = "CANARY_HISTORY_DUPLICATE"


def _money(value: Any) -> float | None:
    """A number, or nothing. Never whatever the venue happened to send.

    Born in the canary record's builder, which refused to store an arbitrary string in a money
    field on a self-hashed governance record. The builder went with the canary door on
    2026-09-15 (PR1r); the rule stays because the evidence READERS below need it — a board that
    renders `"66.83"` as a number where the record refused to store one would report agreement
    the record never claimed.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def read_canary_orders(root: Path | None = None) -> list[dict[str, Any]]:
    """All canary records, oldest first — a VERIFIED read.

    Missing store = honestly empty (no canary has been placed). Anything unreadable,
    tampered, or duplicated raises: this history once gated autonomous live trading and is
    still what the board vouches for, so a record that cannot prove itself must not be shown
    as if it could.
    """
    path = state_dir(root) / CANARY_ORDERS_FILENAME
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    # Reads only: nothing appends to this registry since the canary door was removed.
    for lineno, record in jsonl.iter_numbered(
        path,
        read_code=CANARY_HISTORY_UNREADABLE,
        label="canary registry",
        exc_type=ToolError,
    ):
        if not isinstance(record, dict):
            continue
        stored = record.get("record_sha256")
        body = {k: v for k, v in record.items() if k != "record_sha256"}
        if not isinstance(stored, str) or integrity.sha256_record(body) != stored:
            raise ToolError(CANARY_HISTORY_TAMPERED, f"canary registry line {lineno} fails its self-hash")
        order_id = record.get("canary_order_id")
        if isinstance(order_id, str) and order_id:
            if order_id in seen:
                raise ToolError(CANARY_HISTORY_DUPLICATE, f"duplicate canary_order_id: {order_id}")
            seen.add(order_id)
        records.append(record)
    return records


# --- the operator's read side: can each canary prove what it was? ------------
#
#     python -m runtime.mvp_runtime.crypto.live_promotion
#
# Written while canaries were still being placed, when "did the one I just placed record its fill"
# was the whole question and answering it meant opening the jsonl by hand. The readiness board's
# aggregate row went with the promotion gate (2026-09-15, PR1r); this is where the frozen history
# is read now. Read-only, no gate, no network: it renders per record what the record itself carries.


def canary_evidence_rows(root: Path | None = None) -> list[dict[str, Any]]:
    """One row per canary, newest last, saying whether it can prove its own size.

    ``size_proven`` is derived from the record, never asserted: a record proves its size when it
    carries the venue's filled notional, because only then is declared-versus-filled a
    subtraction. Records written before those fields existed carry no comparison at all, and
    that is reported as *unproven*, not as agreement."""
    rows: list[dict[str, Any]] = []
    for record in read_canary_orders(root):
        gap = record.get("notional_declared_vs_filled_usdt")
        proven = isinstance(gap, (int, float)) and not isinstance(gap, bool)
        rows.append({
            "recorded_at_utc": record.get("recorded_at_utc"),
            "symbol": record.get("symbol"),
            "clean": record.get("clean") is True,
            "exchange_order_id": record.get("exchange_order_id"),
            "declared_usdt": record.get("notional_usdt"),
            "filled_usdt": record.get("filled_notional_usdt"),
            "gap_usdt": float(gap) if proven else None,
            "size_proven": proven,
        })
    return rows


def render_canary_evidence_text(rows: list[dict[str, Any]]) -> str:
    """The rows as a board. Deliberately says *why* an old record is unproven rather than
    printing a blank: "no fill recorded" is a fact about the record, and a blank column would
    read as a zero-sized order."""
    lines = ["=== canary evidence ===", ""]
    if not rows:
        lines.append("no canary orders recorded on this machine")
        return "\n".join(lines)
    for index, row in enumerate(rows, start=1):
        mark = "PROVEN " if row["size_proven"] else "UNPROVEN"
        size = (f"declared {row['declared_usdt']} / filled {row['filled_usdt']} "
                f"(gap {row['gap_usdt']:+.2f})" if row["size_proven"]
                else f"declared {row['declared_usdt']} / filled ? — no fill recorded")
        lines.append(f"[{mark}] {index}. {row['recorded_at_utc']}  {row['symbol']}  "
                     f"order {row['exchange_order_id']}")
        lines.append(f"           {size}"
                     + ("" if row["clean"] else "   (NOT clean — does not count as evidence)"))
    proven = sum(1 for r in rows if r["size_proven"])
    lines += ["", f"{proven}/{len(rows)} can prove their size"]
    if proven < len(rows):
        lines.append("a record written before the fill fields existed cannot be repaired, and no "
                     "new canary can be placed — the live trades below are the evidence now")
    return "\n".join(lines)


def _pnl_agrees_with_prices(
    *, quantity: float | None, entry: float | None, exit_price: float | None,
    side: Any, realized: float | None,
) -> bool | None:
    """Does this row's realized P&L follow from its own prices and quantity?

    ``None`` when a figure is missing — "cannot check" is a third answer and must not read as
    "checked and fine". Otherwise the row is asked to satisfy its own arithmetic:

        realized == (exit - entry) * quantity, negated when the row closed a SHORT

    ``side`` is the CLOSING order's side, so ``SELL`` closed a LONG. This is not a heuristic —
    every term is on the row, so a disagreement means two of its own fields describe different
    trades. Measured across the 28 rows on this machine, 27 satisfy it to within 2e-16 of
    notional (float noise) and one misses by 99.8% of notional, so the tolerance below has ten
    orders of magnitude of headroom and still catches an error a millionth the size of the one
    that prompted it. A board that cries wolf gets ignored, which is the failure this is
    guarding against in the first place.
    """
    if quantity is None or entry is None or exit_price is None or realized is None:
        return None
    direction = 1.0 if str(side).upper() == "SELL" else -1.0
    expected = (exit_price - entry) * quantity * direction
    notional = abs(quantity * entry)
    return abs(realized - expected) <= max(notional * 1e-6, 1e-6)


def live_trade_evidence_rows(root: Path | None = None) -> list[dict[str, Any]]:
    """One row per CLOSED live trade, saying whether it recorded the venue's numbers.

    This is the evidence that matters from here on. The four canaries are frozen at 0/4 and
    cannot be repaired — the venue's figures were never stored — and no more are being placed,
    so "can the live path prove what it did" has to be answered by real trades instead.

    Real trades answer it **by construction**, which is a stronger guarantee than the canary
    path ever had: `build_live_position` takes ``entry_price``/``quantity`` from the actual
    fill and never from the intent, and `live_leg` refuses to confirm an entry unless the
    venue reported a positive filled quantity AND price. A position that cannot show a fill is
    not booked at all. So an UNPROVEN row here does not mean "the size drifted" — it means
    something wrote an outcome by a path that skipped that rule, which is worth seeing.

    Reads outcomes only. The cycle already reads live outcomes so the risk guard can see live
    losses, and reading a result is not reaching the order path — the same boundary the
    order-path tripwire draws by leaving ``live_pnl`` out of its module set.

    **WHICH STRATEGY placed it rides along too.** The outcome record has carried
    ``candidate_id`` / ``strategy_rule_hash`` / ``strategy_generation_id`` since 2026-07-26,
    when the executing leg stopped copying only the display id — and then this board, the one
    surface an operator reads live results on, dropped all three again. That is the same half
    repair the canary size read was written to finish: a field stored where nobody can see
    it answers no question.

    It is not decoration while a live test is running. 25 of the 72 strategies currently
    routing came through the C7 import and carry **no ``candidate_id`` at all**, so a trade
    from one of them cannot be traced to the evidence that promoted it. Their rule hash and
    generation are the whole attribution that exists, which is exactly why the board has to
    print them — and has to say plainly when the stronger key is missing, rather than leaving
    a blank an operator reads as "not filled in yet".
    """
    from .live_pnl import read_live_outcomes

    rows: list[dict[str, Any]] = []
    for record in read_live_outcomes(root):
        qty = _money(record.get("quantity"))
        entry = _money(record.get("entry_price"))
        exit_price = _money(record.get("exit_price"))
        realized = _money(record.get("realized_pnl_usdt"))
        proven = bool(qty) and bool(entry)
        rule_hash = record.get("strategy_rule_hash")
        candidate_id = record.get("candidate_id")
        rows.append({
            "closed_at_utc": record.get("closed_at_utc"),
            "symbol": record.get("symbol"),
            "side": record.get("side"),
            "quantity": qty,
            "entry_price": entry,
            "exit_price": exit_price,
            "entry_notional_usdt": round(qty * entry, 8) if proven else None,
            "realized_pnl_usdt": realized,
            # Its own field, beside `size_proven`, because they answer different questions and
            # the stronger-sounding one is the weaker check: `size_proven` asks only whether a
            # quantity and an entry price are PRESENT. A row can hold both and still carry a
            # P&L that does not follow from them, which is what 2026-08-21's BTCUSDT row did —
            # `realized 77.5357 USDT` printed beside `= 77.8813 USDT` notional on a 0.22% price
            # move, marked PROVEN, counted in "28/28 can prove their size".
            "pnl_consistent": _pnl_agrees_with_prices(
                quantity=qty, entry=entry, exit_price=exit_price,
                side=record.get("side"), realized=realized,
            ),
            "entry_order_id": record.get("entry_order_id"),
            "size_proven": proven,
            "strategy_id": record.get("strategy_id"),
            "candidate_id": candidate_id,
            "strategy_rule_hash": rule_hash if isinstance(rule_hash, str) and rule_hash else None,
            "strategy_generation_id": record.get("strategy_generation_id"),
            # The artifact the order was approved as (PR3a-2): two installs of one candidate
            # share its id, rule and generation and differ here. None before it rode on orders.
            "strategy_artifact_sha256": record.get("strategy_artifact_sha256") or None,
            # Its own field rather than something the reader infers from a null: "no
            # candidate_id" is a statement about the STRATEGY (imported, never minted by the
            # factory), not about this trade.
            "lineage_attributable": bool(isinstance(candidate_id, str) and candidate_id),
        })
    return rows


def render_live_trade_evidence_text(rows: list[dict[str, Any]]) -> str:
    """The real-trade half of the board."""
    lines = ["=== live trades ===", ""]
    if not rows:
        lines += [
            "no live trades closed on this machine yet",
            "",
            "when one closes it appears here with the venue's own entry price and quantity —",
            "a live position is built from the ACTUAL fill or it is not booked at all, so a row",
            "that cannot prove its size means something skipped that rule, not that it drifted",
        ]
        return "\n".join(lines)
    for index, row in enumerate(rows, start=1):
        mark = "PROVEN " if row["size_proven"] else "UNPROVEN"
        body = (f"qty {row['quantity']} @ {row['entry_price']} "
                f"= {row['entry_notional_usdt']} USDT  ->  exit {row['exit_price']}"
                if row["size_proven"] else "no fill figures recorded — investigate")
        lines.append(f"[{mark}] {index}. {row['closed_at_utc']}  {row['symbol']} {row['side']}"
                     f"  order {row['entry_order_id']}")
        lines.append(f"           {body}   realized {row['realized_pnl_usdt']} USDT")
        if row["pnl_consistent"] is False:
            expected = None
            if None not in (row["quantity"], row["entry_price"], row["exit_price"]):
                direction = 1.0 if str(row["side"]).upper() == "SELL" else -1.0
                expected = round(
                    (row["exit_price"] - row["entry_price"]) * row["quantity"] * direction, 8)
            lines.append("           ^ P&L DOES NOT FOLLOW FROM THESE PRICES — do not read the "
                         "realized figure above.")
            lines.append(f"             prices and quantity on this row give {expected} USDT. "
                         "Two of its own fields")
            lines.append("             describe different trades, so one of them is wrong and "
                         "this board cannot say which.")
        elif row["pnl_consistent"] is None:
            lines.append("           ^ P&L NOT CHECKED — a price or quantity is missing, so the "
                         "realized figure")
            lines.append("             above stands on nothing this board can verify.")
        # The display id restarts at S001 every generation, so it names the row without
        # identifying it; the generation and the rule hash are what make it a lineage.
        rule_hash = row["strategy_rule_hash"]
        short_hash = f"{rule_hash[:12]}…" if rule_hash else "NO RULE HASH"
        artifact = row.get("strategy_artifact_sha256")
        lines.append(f"           strategy {row['strategy_id'] or '-'} "
                     f"({row['strategy_generation_id'] or 'no generation'})  rule {short_hash}"
                     + (f"  artifact {str(artifact).removeprefix('sha256:')[:12]}…" if artifact else ""))
        if not row["lineage_attributable"]:
            lines.append("           ^ NOT ATTRIBUTABLE — no candidate_id, so this trade cannot "
                         "be traced back to")
            lines.append("             the backtest evidence that promoted it. Imported "
                         "lineage; the rule hash")
            lines.append("             above is the whole attribution that exists for it.")
    proven = sum(1 for r in rows if r["size_proven"])
    traceable = sum(1 for r in rows if r["lineage_attributable"])
    consistent = sum(1 for r in rows if r["pnl_consistent"] is True)
    inconsistent = sum(1 for r in rows if r["pnl_consistent"] is False)
    unchecked = sum(1 for r in rows if r["pnl_consistent"] is None)
    lines += ["", f"{proven}/{len(rows)} can prove their size",
              f"{consistent}/{len(rows)} have a P&L that follows from their own prices",
              f"{traceable}/{len(rows)} can be traced to promotion evidence"]
    if inconsistent:
        lines += [
            "",
            f"{inconsistent} row(s) above carry a realized P&L their own prices do not produce.",
            "Treat every total that includes them as unusable, this board's included — and do",
            "not arm a strategy on evidence one of them is part of. A row like that is not a",
            "rounding argument: it means something wrote an outcome from figures that do not",
            "describe one round trip.",
        ]
    if unchecked:
        lines += [
            "",
            f"{unchecked} row(s) could not be checked at all — a price or a quantity is absent.",
            "That is a different statement from passing, and it is why it has its own count.",
        ]
    if traceable < len(rows):
        lines += [
            "",
            "the untraceable ones came through the C7 import and were never minted by the",
            "factory, so no candidate row holds their window or cost model — group them by",
            "rule hash instead, and read the result as a rule's, not a lineage's",
        ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Print the canary evidence board. Read-only; writes nothing and opens no socket."""
    import argparse
    import json as _json
    import sys

    from ..cli_common import force_utf8_io

    # A board with an em dash is not cp949-encodable, and this one has several. Called at
    # every entry point rather than the ones that print non-ASCII today — which strings are
    # ASCII is not a property anyone maintains.
    force_utf8_io()
    parser = argparse.ArgumentParser(description="Canary evidence: can each order prove its size?")
    parser.add_argument("--json", action="store_true", help="machine-readable rows")
    args = parser.parse_args(argv)
    try:
        rows = canary_evidence_rows()
    except ToolError as exc:
        # A registry that cannot be verified is refused, never rendered as an empty board that
        # reads as "no canaries placed". It withholds the live-trade half below too; PR1r left
        # that exit as it was.
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return 2
    trades = live_trade_evidence_rows()
    if args.json:
        sys.stdout.write(_json.dumps({"canaries": rows, "live_trades": trades},
                                     ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_canary_evidence_text(rows) + "\n\n"
                         + render_live_trade_evidence_text(trades) + "\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
