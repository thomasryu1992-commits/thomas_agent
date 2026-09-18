"""Register the C4 risk-breaker limits (``crypto_risk_limits.v0.1``) on this machine.

Operator step. Builds a self-hashed limits record and writes it to the per-machine state dir
(gitignored). **This grants nothing and enables no trading** — it can only move a breaker
*within* the relaxation bounds `runtime/mvp_runtime/crypto/guards.py` already accepts, and every
door between here and a live order (the `MVP_LIVE_TRADING=real` opt-in, the confirmation phrase,
the registered budget, both kill switches) stands exactly where it stood. That first door was a
per-machine `live_trading` grant until 2026-07-28; see
`crypto/risk_limits.py` for what its removal changed and what it did not.

With nothing registered the runtime judges on the guards.py defaults, which is the supported
steady state — register a record only to change a number, and only with the approval that number
needs. Every flag defaults to the current default, so the record always states all five limits:

    # tighten the daily breaker, leave the rest at their defaults
    python -m scripts.register_crypto_risk_limits --registered-by thomas --daily-max-loss-r -1.5

    # relax the consecutive-loss breaker; it stands until you revert it
    python -m scripts.register_crypto_risk_limits --registered-by thomas --max-consecutive-losses 5

Two properties to know before running it:

- **The record does not expire.** Until 2026-09-15 it carried a validity window (``--valid-days``,
  default 30) and a relaxation lapsed on its own; Thomas retired the window with the canary door
  (PR1r), and the flag went with it — passing it now exits 2 and writes nothing. A record
  registered today judges until it is replaced, so a relaxation is reverted by hand: re-register
  the prior values, or delete the record (``--show`` prints its path) to return to the defaults.
  A record registered before still carries its window and is still held to it: outside it the
  guard refuses new positions rather than reverting to the defaults, because "which limits are
  authorized" is then unknown.
- **Changing a limit is a re-run, never an edit.** The id and self-hash derive from the numbers,
  so an edited file fails its own hash and fails the guard closed.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from runtime.mvp_runtime.crypto import guards, risk_limits
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run

_ISO = "%Y-%m-%dT%H:%M:%SZ"


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    d = guards.DEFAULT_RISK_LIMITS
    p = argparse.ArgumentParser(
        description="Register the per-machine C4 risk-breaker limits record.",
        epilog=(
            f"bounds: risk-per-trade <= {guards.MAX_RISK_PER_TRADE}, "
            f"daily >= {guards.MIN_DAILY_MAX_LOSS_R}R, weekly >= {guards.MIN_WEEKLY_MAX_LOSS_R}R, "
            f"consecutive <= {guards.MAX_MAX_CONSECUTIVE_LOSSES}, "
            f"drawdown >= {guards.MIN_MAX_DRAWDOWN_PCT}%; a value outside them is refused, never clamped"
        ),
    )
    p.add_argument("--registered-by", help="operator identity registering the limits (required unless --show)")
    p.add_argument("--risk-per-trade", type=float, default=d.risk_per_trade,
                   help=f"equity fraction risked per trade (default {d.risk_per_trade})")
    p.add_argument("--daily-max-loss-r", type=float, default=d.daily_max_loss_r,
                   help=f"daily realized-loss breaker in R, negative (default {d.daily_max_loss_r})")
    p.add_argument("--weekly-max-loss-r", type=float, default=d.weekly_max_loss_r,
                   help=f"weekly realized-loss breaker in R, negative (default {d.weekly_max_loss_r})")
    p.add_argument("--max-consecutive-losses", type=int, default=d.max_consecutive_losses,
                   help=f"consecutive-loss breaker (default {d.max_consecutive_losses})")
    p.add_argument("--max-drawdown-pct", type=float, default=d.max_drawdown_pct,
                   help=f"drawdown-from-peak breaker in equity percent, negative (default {d.max_drawdown_pct})")
    p.add_argument("--show", action="store_true",
                   help="print the currently registered limits and exit, registering nothing")
    p.add_argument("--root", type=Path, default=Path("."),
                   help="repo/state root (default cwd); the limits file lands under it")
    return p.parse_args(argv)


def _show(root: Path) -> int:
    now = datetime.now(timezone.utc).strftime(_ISO)
    status = risk_limits.limits_status(root, now=now)
    print(f"limits file: {risk_limits.limits_path(root)}")
    if not status["registered"]:
        print("registered:  none — the guard judges on the guards.py defaults")
    else:
        print(f"registered:  {status['limits_id']} by {status.get('registered_by')}"
              + (f" at {status.get('registered_at')}" if status.get("registered_at") else ""))
        # A record registered since PR1r carries no window; one registered before still does and
        # is still held to it. Both ends are optional on the record: `.get`, never a subscript.
        if status.get("valid_until"):
            window = f" (legacy window {status.get('valid_from')} .. {status.get('valid_until')})"
        elif status.get("registered_at"):
            window = " (no expiry - stands until re-registered or deleted)"
        else:
            window = ""
        print(f"valid:       {status['valid']}{window}")
        rebase_count = status.get("drawdown_rebase_excluded_count")
        if rebase_count:
            print(f"rebase:      the drawdown baseline excludes {rebase_count} lineage key(s); "
                  "the exclusion stands as long as these numbers do")
        if status["error"]:
            print(f"error:       {status['error']}  <-- the guard REFUSES new positions in this state")
    effective = status["effective"]
    if effective:
        print(f"effective:   risk/trade {effective['risk_per_trade']}, daily {effective['daily_max_loss_r']}R, "
              f"weekly {effective['weekly_max_loss_r']}R, consecutive {effective['max_consecutive_losses']}, "
              f"drawdown {effective['max_drawdown_pct']}% (= {effective['drawdown_limit_r']}R) "
              f"[source={effective['source']}]")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    root = args.root.resolve()

    if args.show:
        return _show(root)
    if not args.registered_by:
        print("ERROR: --registered-by is required to register limits", file=sys.stderr)
        return 2

    # Before the record exists: a host-side root run would leave the limits file root-owned, and
    # re-registering is how every limit change lands — so the service would be stuck with the
    # numbers from the last root run, unable to rewrite them. Exit 3 matches the guard's first
    # adopter (the since-removed `activate_safety_flag.py`) rather than this script's own generic 2.
    try:
        assert_not_foreign_root_run(root)
    except MvpRuntimeError as exc:
        print(f"BLOCKED {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 3

    registered_at = datetime.now(timezone.utc).strftime(_ISO)

    limits = {
        "risk_per_trade": args.risk_per_trade,
        "daily_max_loss_r": args.daily_max_loss_r,
        "weekly_max_loss_r": args.weekly_max_loss_r,
        "max_consecutive_losses": args.max_consecutive_losses,
        "max_drawdown_pct": args.max_drawdown_pct,
    }

    try:
        record = risk_limits.build_risk_limits_record(
            limits=limits, registered_by=args.registered_by, registered_at=registered_at,
        )
        path = risk_limits.write_registered_limits(record, root=root)
    except ToolError as exc:
        print(f"ERROR {exc.reason_code}: {exc.reason}", file=sys.stderr)
        return 2

    default = guards.DEFAULT_RISK_LIMITS
    changed = [k for k, v in limits.items() if getattr(default, k) != v]
    print(f"registered crypto risk limits {record['limits_id']}")
    print(f"  limits:   risk/trade {limits['risk_per_trade']}, daily {limits['daily_max_loss_r']}R, "
          f"weekly {limits['weekly_max_loss_r']}R, consecutive {limits['max_consecutive_losses']}, "
          f"drawdown {limits['max_drawdown_pct']}%")
    print(f"  changed:  {', '.join(changed) if changed else 'nothing (all five at their defaults)'}")
    print(f"  expiry:   none - stands until re-registered, or until {path.name} is deleted")
    print(f"  sha256:   {record['record_sha256']}")
    print(f"  written:  {path}")
    print("This record grants nothing and enables no trading — it moves a breaker inside the "
          "guards.py bounds and nothing else.")
    print("It does not lapse: to revert it, re-register the prior values, or delete the file to "
          "return to the defaults.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
