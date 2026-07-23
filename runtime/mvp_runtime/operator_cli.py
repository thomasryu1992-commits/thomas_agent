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

from . import heartbeat
from .approval_store import ApprovalStore
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, gate_banners, report_block
from .control import ControlStore
from .errors import MvpRuntimeError, OperatorBlocked
from .operator import (
    OperatorChannel,
    OperatorIdentity,
    load_operator_registration,
    run_operator_once,
    select_operator_channel,
)
from .pipeline import AUTO_VALIDATION
from .programization import ProgramizationStore
from .providers import select_provider, select_validator_provider
from .store import LedgerStore
from .tools import select_search_tool
from .working_memory import WorkingMemoryStore


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the operator control-channel loop.")
    parser.add_argument("--max-batches", type=int, default=1,
                        help="number of poll batches to process; 0 = run until interrupted (default 1)")
    parser.add_argument("--long-poll-seconds", type=int, default=0,
                        help="hold each poll open until a message arrives, up to N seconds "
                             "(real long-poll; 0 = return immediately). Use e.g. 25 for continuous runs.")
    parser.add_argument("--sleep-seconds", type=float, default=0.0,
                        help="extra pause between poll batches (default 0; unneeded with --long-poll-seconds)")
    parser.add_argument("--independent-validation", nargs="?", const="always",
                        choices=("always", "auto"), default=None,
                        help="R7: add the independent validation agent (a second reviewer; the "
                             "stricter verdict decides delivery). Bare flag or 'always' = every "
                             "request; 'auto' (R7.1) = only ORANGE/RED-risk tasks and requests "
                             "the operator marks important (!중요 / !important prefix)")
    return parser.parse_args(argv)


def _validation_policy(arg: str | None) -> bool | str:
    """Map the CLI argument onto run_task's ``independent_validation`` value."""
    if arg is None:
        return False
    return True if arg == "always" else AUTO_VALIDATION


def main(
    argv: list[str] | None = None,
    *,
    channel: OperatorChannel | None = None,
    registration: OperatorIdentity | None = None,
    provider: Any | None = None,
    validator_provider: Any | None = None,
    search_tool: Any | None = None,
    working_memory: Any | None = None,
    store: LedgerStore | None = None,
    control_store: ControlStore | None = None,
    approval_store: ApprovalStore | None = None,
    repo_root: Path | None = None,
    sleep: Any = time.sleep,
) -> int:
    """Run the operator loop. Returns 0 on a clean finish, non-zero on a fail-closed block.
    Dependencies are injectable for tests; unset ones are selected through the Safety-Flag
    Gate / loaded from local state."""
    force_utf8_io()
    args = _parse_args(argv)

    try:
        registration = registration if registration is not None else load_operator_registration(repo_root)
        channel = channel if channel is not None else select_operator_channel(root=repo_root)
        provider = provider if provider is not None else select_provider()
        search_tool = search_tool if search_tool is not None else select_search_tool()
        # R7.1: the validator's own (gated) provider — None keeps the pipeline pairing.
        # Selected here even when validation is off so a misconfigured env fails at
        # startup, not on the first important request at 3am.
        validator_provider = validator_provider if validator_provider is not None else select_validator_provider()
    except MvpRuntimeError as exc:
        return report_block(exc)

    gate_banners(channel=channel, provider=provider, search_tool=search_tool)
    if getattr(validator_provider, "network_egress", False):
        sys.stderr.write("SAFETY_GATE: validator provider authorized separately "
                         f"(model: {getattr(validator_provider, 'model_id', 'unknown')})\n")

    store = store if store is not None else LedgerStore.default()
    working_memory = working_memory if working_memory is not None else WorkingMemoryStore.default()
    programization = ProgramizationStore.default()
    control_store = control_store if control_store is not None else ControlStore.default()
    # R9: without the approval store, Thomas's /approve over the deployed loop would fall
    # through to the pipeline and be analyzed as a business idea. The production entrypoint
    # must wire the documented answer path, not only the tests.
    approval_store = approval_store if approval_store is not None else ApprovalStore.default()
    control_mode = control_store.load().mode
    sys.stderr.write(
        f"OPERATOR: listening for the registered operator (ledger: {store.root}; control: {control_mode})\n"
    )

    # Liveness: one stamp before the first poll so a probe has an answer as soon as the
    # service is up, then one per completed batch. The cadence a probe judges against is
    # the long-poll window — the loop's own natural pass length. Best-effort: a heartbeat
    # write must never take down the loop it only observes.
    def _beat() -> None:
        try:
            heartbeat.write_heartbeat(
                heartbeat.OPERATOR_SERVICE,
                interval_seconds=max(args.long_poll_seconds, args.sleep_seconds, 1.0),
                root=repo_root,
            )
        except OSError as exc:
            sys.stderr.write(f"OPERATOR: heartbeat not written ({type(exc).__name__})\n")

    _beat()
    total_handled = 0
    total_dropped = 0
    channel_egress = False
    batch = 0
    # Transient transport errors (a long-poll timeout, a network blip during getUpdates or
    # sendMessage) are routine for a continuous service — one must never kill the loop.
    # Continuous mode (--max-batches 0) retries forever with capped exponential backoff;
    # finite mode caps consecutive retries so a smoke test cannot hang on a dead network.
    transport_failures = 0
    max_transport_retries = None if args.max_batches == 0 else 3
    try:
        while args.max_batches == 0 or batch < args.max_batches:
            try:
                summary = run_operator_once(
                    channel, registration, long_poll_seconds=args.long_poll_seconds,
                    provider=provider, search_tool=search_tool, working_memory=working_memory,
                    programization=programization,
                    store=store, control_store=control_store, approval_store=approval_store,
                    independent_validation=_validation_policy(args.independent_validation),
                    validator_provider=validator_provider, repo_root=repo_root,
                )
            except OperatorBlocked as exc:
                if exc.reason_code != "CHANNEL_TRANSPORT":
                    # Anything but a transport blip (missing token, malformed offset state,
                    # persist failure) is a real fail-closed condition: stop, don't spin.
                    return report_block(exc)
                transport_failures += 1
                if max_transport_retries is not None and transport_failures > max_transport_retries:
                    sys.stderr.write(f"BLOCKED {exc.reason_code}: {exc.reason} (retries exhausted)\n")
                    return EXIT_BLOCKED
                delay = min(60.0, 2.0 ** min(transport_failures, 6))
                sys.stderr.write(
                    f"OPERATOR: transient channel error; retry {transport_failures} in {delay:.0f}s\n"
                )
                sleep(delay)
                continue
            transport_failures = 0
            total_handled += summary["handled"]
            total_dropped += summary["dropped"]
            if summary.get("send_failures"):
                # The work is durable (ledger/control/approval stores); only the reply's
                # delivery failed. Surface it so a systematically undeliverable reply
                # (e.g. a dead bot token) is visible instead of silently swallowed.
                sys.stderr.write(
                    f"OPERATOR: {summary['send_failures']} reply delivery failure(s) this batch "
                    "(handled work is recorded; the reply was not delivered)\n"
                )
            channel_egress = channel_egress or bool(summary.get("network_egress"))
            for reply in summary["replies"]:
                sys.stderr.write(f"  handled trace={reply.trace_id} status={reply.status}\n")
            batch += 1
            _beat()
            if args.sleep_seconds > 0 and (args.max_batches == 0 or batch < args.max_batches):
                sleep(args.sleep_seconds)
    except KeyboardInterrupt:
        sys.stderr.write("\nOPERATOR: stopped.\n")

    sys.stdout.write(
        f"handled {total_handled}, dropped {total_dropped} over {batch} batch(es) "
        f"(channel network_egress={channel_egress})\n"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
