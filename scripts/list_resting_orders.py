"""What is resting at the venue right now, and which of it the runtime does not own.

    python -m scripts.list_resting_orders
    python -m scripts.list_resting_orders --symbol ETHUSDT --json

Read-only. It places nothing, cancels nothing, and has no flag that would let it.

**Why it exists.** On 2026-08-03T04:28:58Z a ``closePosition`` STOP_MARKET was accepted by the
venue, could not be found by the query that followed, was therefore recorded as ``placed:
false`` — and so was never cancelled when the naked position was closed. Nothing in this repo
could then answer whether it is resting: ``fetch_order`` only knows ids the runtime already
holds, and ``/fapi/v1/openOrders`` stopped returning conditional orders when Binance moved them
to the Algo service. The one class of order that can be orphaned was the one class nothing
could see.

**A stray conditional order is not inert.** It counts against the venue's per-symbol
conditional cap, so it can refuse the NEXT bracket; and a ``closePosition`` stop that outlives
its position will Close-All against whatever is open when it triggers — a position it was never
placed to protect.

**Owned vs unowned.** An order is OWNED when its client id appears on an open live position's
bracket. Everything else is UNOWNED, which is a finding rather than an accusation: a canary's
leg, a hand-placed order, or an orphan all land there, and only a person can tell them apart.
The runtime holding no open positions makes every resting order unowned by definition, which is
exactly the state that matters after a naked close.

Reaching the venue needs the order key, so without ``MVP_LIVE_TRADING=real`` this selects the
inert adapter and says plainly that it asked nothing. Claude does not run this and does not
handle the keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.crypto.live_execution import select_order_adapter  # noqa: E402
from runtime.mvp_runtime.crypto.live_position import list_open_live_positions  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError  # noqa: E402

EXIT_OK = 0
EXIT_BLOCKED = 3
EXIT_UNOWNED = 4

# The bracket id fields a live position carries. Named here rather than imported from the leg
# because this tool must keep working when the leg cannot run at all — reading what is at the
# venue is exactly the thing you want after the code that places orders has misbehaved.
BRACKET_ID_FIELDS = ("stop_client_order_id", "take_profit_client_order_id")


def owned_client_order_ids(root: Path | None = None) -> set[str]:
    """Every bracket id the runtime believes belongs to an open live position."""
    owned: set[str] = set()
    for position in list_open_live_positions(root):
        for field in BRACKET_ID_FIELDS:
            value = position.get(field)
            if isinstance(value, str) and value:
                owned.add(value)
    return owned


def classify(orders: list[dict], owned: set[str], *, kind: str) -> list[dict]:
    rows = []
    for order in orders:
        cid = order.get("clientAlgoId") or order.get("clientOrderId") or order.get("newClientOrderId")
        rows.append({
            "kind": kind,
            "client_order_id": cid,
            "symbol": order.get("symbol"),
            "side": order.get("side"),
            "type": order.get("orderType") or order.get("type"),
            "status": order.get("status") or order.get("algoStatus"),
            "trigger_price": order.get("stopPrice") or order.get("triggerPrice"),
            "price": order.get("price"),
            "close_position": order.get("closePosition"),
            "owned": bool(cid) and cid in owned,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbol", default=None, help="filter to one symbol (default: all)")
    parser.add_argument("--timeout-seconds", type=int, default=10)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        adapter = select_order_adapter()
    except MvpRuntimeError as exc:
        print(f"BLOCKED: could not select an order adapter ({exc.reason_code})")
        return EXIT_BLOCKED
    live = bool(getattr(adapter, "network_egress", False))

    try:
        plain = adapter.open_orders(args.symbol, timeout_seconds=args.timeout_seconds)
        algo = adapter.algo_open_orders(args.symbol, timeout_seconds=args.timeout_seconds)
    except MvpRuntimeError as exc:
        # An unreadable venue is not "nothing is resting". Refuse rather than report an
        # emptiness that was never observed — the whole failure this tool exists to end.
        print(f"BLOCKED: the venue could not be read ({exc.reason_code}: {exc})")
        return EXIT_BLOCKED

    owned = owned_client_order_ids()
    rows = classify(plain, owned, kind="order") + classify(algo, owned, kind="algo")
    unowned = [r for r in rows if not r["owned"]]

    if args.json:
        print(json.dumps({"asked_the_venue": live, "resting": rows,
                          "owned_ids": sorted(owned)}, indent=2, sort_keys=True))
    else:
        if not live:
            print("DRY RUN — MVP_LIVE_TRADING is not 'real'; the venue was not asked.\n")
        print(f"open live positions the runtime knows: {len(owned)//2 if owned else 0} "
              f"(bracket ids: {len(owned)})")
        if not rows:
            print("resting at the venue: none")
        for r in rows:
            mark = "owned" if r["owned"] else "UNOWNED"
            print(f"  [{r['kind']:5}] {mark:8} {r['symbol']} {r['side']} {r['type']} "
                  f"trigger={r['trigger_price']} closePosition={r['close_position']}")
            print(f"           id: {r['client_order_id']}")
        if unowned:
            print(f"\n{len(unowned)} resting order(s) the runtime does not own.")
            print("A conditional one counts against this symbol's cap and, if it is a")
            print("closePosition stop, will Close-All against whatever is open when it")
            print("triggers. Withdraw by hand unless you know what placed it.")

    return EXIT_UNOWNED if unowned else EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
