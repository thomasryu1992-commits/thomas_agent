"""What the Toss account holds — the read-only multi-account board's account.

    python -m scripts.holdings_board            # the stored aggregate (no call to Toss)
    python -m scripts.holdings_board --json     # the same, as JSON
    python -m scripts.holdings_board --full     # a LIVE read with every holding, terminal only
    python -m scripts.holdings_board --local    # the last fire in full (by market, wallet, class), terminal only
    python -m scripts.holdings_board --reset-peak   # forget the combined peak (after a deposit/withdrawal)

Read-only. It places nothing and has no flag that would let it.

**Default is the stored snapshot, not a live read.** Toss allows one valid token per client, and
issuing one revokes the previous one. scheduler-maint is that one issuer: its ``holdings_refresh``
fire reads the account and stores the aggregate, and this command renders that file without touching
the network. The default output is also the aggregate, because the data may serve only the investor's
own trading purpose (appendix B of ``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``). Output that might
be pasted into a chat, a model prompt or a channel should be this one.

**``--full`` is a live read**, with per-symbol values from which a price follows, meant for the
account holder's terminal. It issues its own token, which revokes the lane's. The lane's next fire
reissues once, so nothing breaks, but the command says so when it runs. It needs the lane's variables:
run it where they are (``docker exec thomas-scheduler-maint python -m scripts.holdings_board --full``).
Without the gate it prints that it is not configured and exits 0. A failed read exits
``EXIT_BLOCKED`` with the error's reason code.

**``--local``** (H3, Thomas 2026-10-08) renders the last fire's local file: the Toss account by market,
Binance by wallet and sub-class and every class amount, which the stored snapshot leaves out under the
single-holding rule (``holdings/disclosure.py``). No network. Terminal only, like ``--full``.

**``--reset-peak``** (P2) forgets the combined total's peak and the drawdown alert's told state; the
next complete ``holdings_refresh`` fire starts a new peak. The drawdown metric cannot tell a withdrawal
from a loss, so this is the answer after any deposit or withdrawal. It writes governed state, so it runs
in the lane as the service user (``docker exec -u 10001 thomas-scheduler-maint python -m
scripts.holdings_board --reset-peak``) and refuses a host-side root run.

Claude does not run the live read or ``--local`` (its output would reach a model provider), and does not
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

from runtime.mvp_runtime import timeutil  # noqa: E402
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError  # noqa: E402
from runtime.mvp_runtime.holdings import combined  # noqa: E402
from runtime.mvp_runtime.holdings.board import render_full  # noqa: E402
from runtime.mvp_runtime.holdings.store import load_holdings_view, load_local_view, state_dir  # noqa: E402
from runtime.mvp_runtime.state_guard import assert_not_foreign_root_run  # noqa: E402
from runtime.mvp_runtime.holdings.toss_account import read_holdings  # noqa: E402

LIVE_READ_NOTICE = (
    "note        : this live read issued its own Toss token, which revoked scheduler-maint's; "
    "the lane reissues once on its next fire"
)


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="the stored aggregate as JSON")
    mode.add_argument("--full", action="store_true", help="a live read with every holding (terminal only)")
    mode.add_argument("--local", action="store_true",
                      help="the last fire in full, from the local file (terminal only; no network)")
    mode.add_argument("--reset-peak", action="store_true",
                      help="forget the combined peak and the drawdown told-state (after a deposit/withdrawal)")
    parser.add_argument("--timeout", type=int, default=10, help="seconds per request (--full)")
    args = parser.parse_args(argv)

    if args.reset_peak:
        try:
            assert_not_foreign_root_run()
        except MvpRuntimeError as exc:
            print(f"refused ({exc.reason_code}): {exc}")
            return EXIT_BLOCKED
        removed = combined.reset_peak(state_dir())
        print("reset: " + (", ".join(removed) if removed else "nothing to reset")
              + " — the next complete holdings_refresh fire starts a new peak")
        return EXIT_OK

    if args.full:
        snapshot, reason_code = read_holdings(timeout_seconds=args.timeout)
        print(render_full(snapshot, reason_code=reason_code))
        if snapshot is not None:
            print(LIVE_READ_NOTICE)
        if snapshot is None and reason_code != "NOT_CONFIGURED":
            return EXIT_BLOCKED
        return EXIT_OK

    if args.local:
        try:
            print(load_local_view(now=timeutil.utc_now_iso()))
        except ToolError as exc:
            print(f"holdings    : local board unreadable ({exc.reason_code})")
            return EXIT_BLOCKED
        return EXIT_OK

    try:
        text, data = load_holdings_view(now=timeutil.utc_now_iso())
    except ToolError as exc:
        print(f"holdings    : snapshot unreadable ({exc.reason_code})")
        return EXIT_BLOCKED
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(text)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
