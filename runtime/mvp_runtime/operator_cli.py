"""R4.3 Operator control-channel entrypoint.

Runs the operator loop: select the control channel (mock by default; real Telegram only
behind the Safety-Flag Gate), load the registered operator, then repeatedly poll → verify →
run the pipeline for the registered operator → reply. Unverified senders are silently
dropped. Every run persists to the durable ledger, exactly like the single-shot CLI.

Fail-closed: a missing operator registration or a gate refusal exits non-zero without
polling anything. Uses the deterministic mock channel/provider by default (no network); a
real Telegram channel + hosted provider are enabled only by their env vars *and* valid local
activation records.

Usage:
    # one poll batch (default) with the mock channel — a smoke test that touches no network:
    python -m runtime.mvp_runtime.operator_cli

    # continuous long-poll against real Telegram (needs a bot token + activation):
    MVP_OPERATOR_CHANNEL=telegram TELEGRAM_BOT_TOKEN=... \
        python -m runtime.mvp_runtime.operator_cli --max-batches 0 --sleep-seconds 2
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any

from .errors import MvpRuntimeError
from .operator import (
    OperatorChannel,
    OperatorIdentity,
    TelegramChannel,
    load_operator_registration,
    run_operator_once,
    select_operator_channel,
)
from .providers import GoogleAIStudioProvider, select_provider
from .store import LedgerStore
from .tools import WebSearchTool, select_search_tool
from .working_memory import WorkingMemoryStore

EXIT_OK = 0
EXIT_BLOCKED = 2


def _force_utf8_io() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the operator control-channel loop.")
    parser.add_argument("--max-batches", type=int, default=1,
                        help="number of poll batches to process; 0 = run until interrupted (default 1)")
    parser.add_argument("--long-poll-seconds", type=int, default=0,
                        help="hold each poll open until a message arrives, up to N seconds "
                             "(real long-poll; 0 = return immediately). Use e.g. 25 for continuous runs.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0,
                        help="extra pause between poll batches (default 0; unneeded with --long-poll-seconds)")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    channel: OperatorChannel | None = None,
    registration: OperatorIdentity | None = None,
    provider: Any | None = None,
    search_tool: Any | None = None,
    working_memory: Any | None = None,
    store: LedgerStore | None = None,
    repo_root: Path | None = None,
    sleep: Any = time.sleep,
) -> int:
    """Run the operator loop. Returns 0 on a clean finish, non-zero on a fail-closed block.
    Dependencies are injectable for tests; unset ones are selected through the Safety-Flag
    Gate / loaded from local state."""
    _force_utf8_io()
    args = _parse_args(argv)

    try:
        registration = registration if registration is not None else load_operator_registration(repo_root)
        channel = channel if channel is not None else select_operator_channel(root=repo_root)
        provider = provider if provider is not None else select_provider()
        search_tool = search_tool if search_tool is not None else select_search_tool()
    except MvpRuntimeError as exc:
        sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason}\n")
        return EXIT_BLOCKED

    if isinstance(channel, TelegramChannel):
        sys.stderr.write("SAFETY_GATE: network-capable operator channel authorized (network_access)\n")
    if isinstance(provider, GoogleAIStudioProvider):
        sys.stderr.write("SAFETY_GATE: network-capable provider authorized (model_invocation, network_access)\n")
    if isinstance(search_tool, WebSearchTool):
        sys.stderr.write("SAFETY_GATE: network-capable search tool authorized (network_access)\n")

    store = store if store is not None else LedgerStore.default()
    working_memory = working_memory if working_memory is not None else WorkingMemoryStore.default()
    sys.stderr.write(f"OPERATOR: listening for the registered operator (ledger: {store.root})\n")

    total_handled = 0
    total_dropped = 0
    batch = 0
    try:
        while args.max_batches == 0 or batch < args.max_batches:
            summary = run_operator_once(
                channel, registration, long_poll_seconds=args.long_poll_seconds,
                provider=provider, search_tool=search_tool, working_memory=working_memory,
                store=store, repo_root=repo_root,
            )
            total_handled += summary["handled"]
            total_dropped += summary["dropped"]
            for reply in summary["replies"]:
                sys.stderr.write(f"  handled trace={reply.trace_id} status={reply.status}\n")
            batch += 1
            if args.sleep_seconds > 0 and (args.max_batches == 0 or batch < args.max_batches):
                sleep(args.sleep_seconds)
    except KeyboardInterrupt:
        sys.stderr.write("\nOPERATOR: stopped.\n")

    sys.stdout.write(f"handled {total_handled}, dropped {total_dropped} over {batch} batch(es)\n")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
