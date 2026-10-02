"""What the KIS account holds right now — the read-only multi-account board's first account.

    python -m scripts.holdings_board            # aggregate: totals by asset class, no symbol
    python -m scripts.holdings_board --json     # the same aggregate as JSON
    python -m scripts.holdings_board --full     # every holding, for your own terminal only

Read-only. It places nothing and has no flag that would let it: the feed it holds can reach the
token endpoint and two balance inquiries, nothing else.

**Default is the aggregate on purpose.** The KIS terms let quotes serve the customer's own work and
forbid giving them to a third party (art. 5(3)); Thomas set the boundary on 2026-10-02 (appendix A of
``docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md``). Output that might be pasted into a chat, a model
prompt or a channel should be the aggregate. ``--full`` prints per-symbol values, from which a
price follows, and is meant for the account holder's terminal.

**What it needs.** ``MVP_KIS_ACCOUNT=kis`` opens the gate; ``KIS_APP_KEY``, ``KIS_APP_SECRET``,
``KIS_ACCOUNT_NO`` and ``KIS_ACCOUNT_PRODUCT_CODE`` are read at call time; ``KIS_SERVER`` is ``real``
(the default) or ``demo``. No compose service carries these yet — wiring one is a separate,
governance-level change — so today it runs only where the operator passes them explicitly.
Without the gate it prints that it is not configured and exits 0: an unconfigured board is a normal
state. A failed read exits ``EXIT_BLOCKED`` with the error's reason code.

Each run issues one access token, and KIS sends the account holder a KakaoTalk notice per issuance.
Claude does not run this and does not handle the keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io  # noqa: E402
from runtime.mvp_runtime.holdings.board import (  # noqa: E402
    aggregate_view,
    render_aggregate,
    render_full,
)
from runtime.mvp_runtime.holdings.kis_account import read_holdings  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    force_utf8_io()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true", help="the aggregate as JSON")
    mode.add_argument("--full", action="store_true", help="every holding (terminal only)")
    parser.add_argument("--timeout", type=int, default=10, help="seconds per request")
    args = parser.parse_args(argv)

    snapshot, reason_code = read_holdings(timeout_seconds=args.timeout)
    if args.json:
        payload = (
            aggregate_view(snapshot) if snapshot is not None
            else {"available": False, "reason_code": reason_code}
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.full:
        print(render_full(snapshot, reason_code=reason_code))
    else:
        print(render_aggregate(snapshot, reason_code=reason_code))
    if snapshot is None and reason_code != "NOT_CONFIGURED":
        return EXIT_BLOCKED
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
