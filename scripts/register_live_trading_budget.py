"""Register the live-trading budget (``live_trading_budget.v0.1``) on this machine.

Operator step. Builds a self-hashed budget record from the approved caps and writes it to the
per-machine state dir (gitignored). **This grants nothing and enables no trading** — it is a
record, not a permission. The `live_trading` safety-flag grant, the confirmation phrase, the
>= 3 clean canary orders, the P5 role, and LP4/LP5 all still stand between here and a live
order (see LIVE_EXECUTION_GOVERNANCE_V0.1.md).

Approved starting caps (Thomas, 2026-07-23): 60 USDT/order, 2 orders/day, 120 USDT open
exposure, 20 USDT daily loss, against the 200 USDT absolute ceiling; >= 3 clean canary orders.

    python -m scripts.register_live_trading_budget --registered-by thomas --symbols BTCUSDT
    python -m scripts.register_live_trading_budget --registered-by thomas --symbols BTCUSDT,ETHUSDT \
        --max-order-notional 60 --daily-loss-limit 20 --valid-days 30

Changing a limit is a re-run: the id and self-hash derive from the caps, so a new cap is a new
record, never a silent edit.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from runtime.mvp_runtime.crypto import live_budget
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Register the per-machine live-trading budget record.")
    p.add_argument("--registered-by", required=True, help="operator identity registering the budget")
    p.add_argument("--symbols", default="BTCUSDT",
                   help="comma-separated symbol allowlist (default BTCUSDT)")
    p.add_argument("--venue", default=live_budget.SUPPORTED_VENUE, help="trading venue")
    p.add_argument("--max-order-notional", type=float, default=60.0)
    p.add_argument("--absolute-max-notional", type=float, default=200.0)
    p.add_argument("--max-daily-order-count", type=int, default=2)
    p.add_argument("--max-open-notional", type=float, default=120.0)
    p.add_argument("--daily-loss-limit", type=float, default=20.0)
    p.add_argument("--min-clean-canary-orders", type=int, default=3)
    p.add_argument("--valid-days", type=int, default=30, help="validity window length in days (default 30)")
    p.add_argument("--root", type=Path, default=Path("."),
                   help="repo/state root (default cwd); the budget file lands under it")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.valid_days < 1:
        print("ERROR: --valid-days must be >= 1", file=sys.stderr)
        return 2

    # Before the record exists: a host-side root run would leave the budget file root-owned,
    # and re-registering a cap is how every limit change lands — so the service would be stuck
    # with the caps from the last root run, unable to rewrite them. Exit 3 matches the guard's
    # first adopter (`activate_safety_flag.py`) rather than this script's own generic 2.
    try:
        assert_not_foreign_root_run(args.root.resolve())
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 3

    now = datetime.now(timezone.utc)
    valid_from = now.strftime(_ISO)
    valid_until = (now + timedelta(days=args.valid_days)).strftime(_ISO)
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]

    caps = {
        "max_order_notional_usdt": args.max_order_notional,
        "absolute_max_notional_usdt": args.absolute_max_notional,
        "max_daily_order_count": args.max_daily_order_count,
        "max_open_notional_usdt": args.max_open_notional,
        "daily_loss_limit_usdt": args.daily_loss_limit,
        "min_clean_canary_orders": args.min_clean_canary_orders,
    }

    try:
        record = live_budget.build_live_trading_budget_record(
            caps=caps, symbol_allowlist=symbols, venue=args.venue,
            valid_from=valid_from, valid_until=valid_until,
            registered_by=args.registered_by, registered_at=valid_from,
        )
        path = live_budget.write_registered_budget(record, root=args.root.resolve())
    except ToolError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2

    print(f"registered live-trading budget {record['budget_id']}")
    print(f"  venue:    {record['venue']}")
    print(f"  symbols:  {', '.join(record['symbol_allowlist'])}")
    print(f"  caps:     order<={caps['max_order_notional_usdt']} (ceiling {caps['absolute_max_notional_usdt']}), "
          f"{caps['max_daily_order_count']}/day, open<={caps['max_open_notional_usdt']}, "
          f"loss<={caps['daily_loss_limit_usdt']}, canary>={caps['min_clean_canary_orders']}")
    print(f"  valid:    {record['valid_from']} .. {record['valid_until']}")
    print(f"  sha256:   {record['record_sha256']}")
    print(f"  written:  {path}")
    print("This record grants nothing and enables no trading — the live_trading grant, the "
          "confirmation phrase, >= 3 clean canary orders, the P5 role, and LP4/LP5 all still apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
