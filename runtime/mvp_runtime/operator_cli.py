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

from . import heartbeat, timeutil
from .approval_store import ApprovalStore
from .cli_common import EXIT_BLOCKED, EXIT_OK, force_utf8_io, gate_banners, report_block
from .control import ControlStore
from .errors import MvpRuntimeError, OperatorBlocked
from .frontdesk import select_frontdesk_provider
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
from .state_guard import assert_state_writable
from .store import LedgerStore
from .task_registry import TaskRegistryStore, reconcile_stale_running
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
    registry: TaskRegistryStore | None = None,
    frontdesk_provider: Any | None = None,
    repo_root: Path | None = None,
    sleep: Any = time.sleep,
) -> int:
    """Run the operator loop. Returns 0 on a clean finish, non-zero on a fail-closed block.
    Dependencies are injectable for tests; unset ones are selected through the Safety-Flag
    Gate / loaded from local state."""
    force_utf8_io()
    args = _parse_args(argv)

    try:
        # Before anything selects a provider or opens a socket: if this process cannot
        # write its own state, every capability would refuse one request at a time with
        # its own reason code and no single startup signal. Say it once, here, and stop.
        assert_state_writable(repo_root)
        registration = registration if registration is not None else load_operator_registration(repo_root)
        channel = channel if channel is not None else select_operator_channel(root=repo_root)
        provider = provider if provider is not None else select_provider()
        search_tool = search_tool if search_tool is not None else select_search_tool()
        # R7.1: the validator's own (gated) provider — None keeps the pipeline pairing.
        # Selected here even when validation is off so a misconfigured env fails at
        # startup, not on the first important request at 3am.
        validator_provider = validator_provider if validator_provider is not None else select_validator_provider()
        # F2: the conversational front desk's own gated provider — None keeps the F1
        # deterministic channel. Selected at startup for the validator's reason (a
        # misconfigured env or an unactivated role fails HERE, not on the first message),
        # and the selection itself enforces the registry's D2 activation flip.
        frontdesk_provider = (frontdesk_provider if frontdesk_provider is not None
                              else select_frontdesk_provider(root=repo_root))
    except MvpRuntimeError as exc:
        return report_block(exc)

    gate_banners(channel=channel, provider=provider, search_tool=search_tool)
    if getattr(validator_provider, "network_egress", False):
        sys.stderr.write("SAFETY_GATE: validator provider authorized separately "
                         f"(model: {getattr(validator_provider, 'model_id', 'unknown')})\n")
    if frontdesk_provider is not None:
        sys.stderr.write("FRONTDESK: conversational mode ON "
                         f"(model: {getattr(frontdesk_provider, 'model_id', 'unknown')}; "
                         "plain text is a conversation turn, /verbs stay deterministic)\n")

    store = store if store is not None else LedgerStore.default(repo_root)
    working_memory = working_memory if working_memory is not None else WorkingMemoryStore.default(repo_root)
    programization = ProgramizationStore.default(repo_root)
    control_store = control_store if control_store is not None else ControlStore.default(repo_root)
    # R9: without the approval store, Thomas's /approve over the deployed loop would fall
    # through to the pipeline and be analyzed as a business idea. The production entrypoint
    # must wire the documented answer path, not only the tests.
    approval_store = approval_store if approval_store is not None else ApprovalStore.default(repo_root)
    # F1: the task-coordination registry, wired for the same reason as the approval store —
    # without it /tasks and /history would be refused on the one entrypoint they exist for.
    registry = registry if registry is not None else TaskRegistryStore.default(repo_root)
    # An entry still RUNNING when this process starts belongs to a dead one (the MVP runs
    # tasks one at a time, single-process), so it gets the terminal that process never
    # wrote. Startup is the only vantage point that can see it — the scheduler's
    # `abandoned` precedent. Best-effort: bookkeeping must not stop the service.
    try:
        abandoned = reconcile_stale_running(registry, now=timeutil.utc_now_iso())
        if abandoned:
            sys.stderr.write(f"OPERATOR: {len(abandoned)} interrupted task(s) marked RUN_ABANDONED\n")
    except MvpRuntimeError as exc:
        sys.stderr.write(f"OPERATOR: task registry not reconciled ({exc.reason_code})\n")
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
                    registry=registry, frontdesk_provider=frontdesk_provider,
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
                partial = summary.get("partial_sends") or 0
                total = summary["send_failures"]
                # A long analysis goes out as several numbered parts, so "the reply was not
                # delivered" was a false statement about the ones where part of it did arrive.
                detail = "the reply was not delivered"
                if partial:
                    detail = (f"{partial} of them delivered only part of the answer "
                              f"(Thomas has an incomplete reply; /result <id> re-sends it)")
                    if partial < total:
                        detail += f"; the other {total - partial} delivered nothing"
                sys.stderr.write(
                    f"OPERATOR: {total} reply delivery failure(s) this batch "
                    f"(handled work is recorded; {detail})\n"
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
