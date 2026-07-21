"""CLI entry point for the single-agent MVP pipeline.

Reads a request from CLI args (or stdin), runs it end-to-end (intake -> plan ->
worker -> validation -> audit), and writes the final response to stdout. Fail-closed:
on any BLOCK it writes the reason to stderr and exits non-zero. Uses the deterministic
MockProvider — a real hosted provider is enabled only behind the Safety-Flag Gate — so
the run performs no external write and no network I/O.

Usage:
    python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
    echo "..." | python -m runtime.mvp_runtime.cli

    # R7 (opt-in): add the independent validation agent — a second reviewer whose
    # stricter verdict decides delivery:
    python -m runtime.mvp_runtime.cli --independent-validation "이 사업 아이디어를 분석해줘: ..."

    # R8 (opt-in): also create the response as a file under workspace/. Create-only and
    # dry-run by default; a real write needs filesystem_write activated locally:
    python -m runtime.mvp_runtime.cli --write-output reports/idea.md "이 사업 아이디어를 분석해줘: ..."
"""

from __future__ import annotations

import sys

from . import control
from .cli_common import EXIT_BLOCKED, EXIT_OK, EXIT_USAGE, force_utf8_io, gate_banners, report_block
from .control import ControlStore
from .errors import MvpRuntimeError
from .pipeline import AUTO_VALIDATION, run_task
from .providers import select_provider, select_validator_provider
from .store import LedgerStore
from .tools import select_search_tool
from .working_memory import WorkingMemoryStore
from .workspace import select_writer


def _extract_write_path(argv: list[str]) -> tuple[str | None, list[str], str | None]:
    """Pull ``--write-output PATH`` out of argv. Returns ``(path, rest, usage_error)``.

    Hand-parsed rather than argparse'd because the request itself is free-form positional
    text that argparse would try to interpret.
    """
    if "--write-output" not in argv:
        return None, argv, None
    index = argv.index("--write-output")
    if index + 1 >= len(argv):
        return None, argv, "--write-output requires a workspace-relative PATH"
    path = argv[index + 1]
    if path.startswith("-"):
        return None, argv, f"--write-output requires a PATH, got the flag {path!r}"
    return path, argv[:index] + argv[index + 2:], None


def main(
    argv: list[str] | None = None,
    *,
    store: LedgerStore | None = None,
    working_memory: WorkingMemoryStore | None = None,
    control_store: ControlStore | None = None,
) -> int:
    """Run one request end-to-end. Returns the process exit code.

    ``store`` / ``working_memory`` default to the repo-local per-machine ones. They are
    injectable so a test can drive the real entry point without appending synthetic runs
    to the operator's ledger and — more importantly — without seeding working memory,
    which retrieval feeds back as context into subsequent real runs.
    """
    force_utf8_io()
    argv = list(sys.argv[1:] if argv is None else argv)
    independent_validation: bool | str = False
    if "--independent-validation=auto" in argv:
        independent_validation = AUTO_VALIDATION
    elif "--independent-validation" in argv:
        independent_validation = True
    argv = [a for a in argv if a not in ("--independent-validation", "--independent-validation=auto")]
    # R7.1: mark this request important — intake priority HIGH, which under the "auto"
    # validation policy adds the independent reviewer to this run.
    important = "--important" in argv
    argv = [a for a in argv if a != "--important"]
    write_path, argv, usage_error = _extract_write_path(argv)
    if usage_error is not None:
        sys.stderr.write(f"BLOCKED USAGE: {usage_error}\n")
        return EXIT_USAGE
    # Everything left is free-form request text. Reject leftover option-shaped tokens
    # rather than silently folding them into the prompt: a mistyped or non-existent flag
    # (e.g. -i, or --current-pointer, which this CLI never had) would otherwise be
    # swallowed into the request, polluting the prompt and the audited record while the
    # caller believes it took effect. Fail closed, as everywhere else — single-dash
    # tokens included, one dash short is still a mistyped flag. A request that genuinely
    # starts with "-" can be piped on stdin.
    unknown = [a for a in argv if a.startswith("-") and len(a) > 1]
    if unknown:
        sys.stderr.write(
            f"BLOCKED USAGE: unrecognized option {unknown[0]!r} "
            "(known options: --independent-validation[=auto], --important, --write-output PATH); "
            "pipe the request on stdin if it must start with '-'\n"
        )
        return EXIT_USAGE
    text = " ".join(argv) if argv else sys.stdin.read()
    # Drop a leading BOM (some shells inject U+FEFF on an "empty" pipe) so a
    # BOM-only input is correctly treated as empty rather than a 1-char request.
    raw_request = text.lstrip("﻿").strip()

    if not raw_request:
        sys.stderr.write("BLOCKED EMPTY_REQUEST: no request text provided (arg or stdin)\n")
        return EXIT_USAGE

    # Kill-switch binding for the manual door (kill_blocks: new_execution). The operator
    # loop, scheduler, R8 write, and R10 consume already refuse while PAUSED/KILLED; this
    # host CLI was the one execution door without the check — a KILLED runtime performing
    # a model-invoking pipeline run because the request came in over SSH instead of
    # Telegram was the asymmetry, not the intent.
    control_store = control_store if control_store is not None else ControlStore.default()
    state = control_store.load()
    if not state.execution_allowed:
        reason_code = state.refusal_reason_code()
        sys.stderr.write(
            f"BLOCKED {reason_code}: runtime is {state.mode}; new requests are blocked "
            "(an authenticated operator resume clears it)\n"
        )
        return EXIT_BLOCKED

    # Select the provider through the enforced Safety-Flag Gate. Default = MockProvider
    # (no network). A hosted provider is returned only if a valid local activation record
    # authorizes it; otherwise select_provider fails closed and the run is BLOCKED here.
    try:
        provider = select_provider()
        # R7.1: the validator's own (gated) provider — None keeps the pipeline pairing.
        validator_provider = select_validator_provider()
        # The read-only search tool goes through the same Safety-Flag Gate: default is the
        # network-free MockSearchTool; a real network tool requires a valid activation.
        search_tool = select_search_tool()
        # R8: same gate again for the writer. Default is the DryRunWriter (touches nothing);
        # a disk-writing writer requires a valid activation enabling filesystem_write.
        writer = select_writer() if write_path is not None else None
    except MvpRuntimeError as exc:
        return report_block(exc)
    gate_banners(provider=provider, search_tool=search_tool, writer=writer)

    # Persist every run's records + hash-chained audit trail to the local append-only ledger.
    # Working memory (local, per-machine) accumulates candidates and feeds them back as context.
    store = store if store is not None else LedgerStore.default()
    working_memory = (
        working_memory if working_memory is not None else WorkingMemoryStore.default()
    )
    result = run_task(raw_request, provider=provider, search_tool=search_tool,
                      working_memory=working_memory, channel="manual", store=store,
                      independent_validation=independent_validation,
                      validator_provider=validator_provider,
                      priority="HIGH" if important else "NORMAL",
                      write_path=write_path, writer=writer)
    # One field answers "is this run's evidence durable?" for every failure shape. Checking
    # only the block stage claimed "LEDGER: recorded" over two real persistence failures —
    # a secondary failure while recording a block, and a failure on the validation-withheld
    # path — telling the operator an audit trail exists when none does.
    persist_error = result.get("persist_error")
    if persist_error is None:
        sys.stderr.write(f"LEDGER: recorded to {store.root}\n")
    else:
        sys.stderr.write(
            f"LEDGER: NOT recorded ({persist_error}) — this run has no durable audit trail\n"
        )
    # EXECUTE_AND_REPORT: a write is never silent, and never conditional on what happened
    # after it. If persistence failed once the file existed, the run BLOCKs — and the file
    # is still on disk, so it is still reported here.
    write = result.get("write")
    if write is not None:
        kind = "created" if write["filesystem_write"] else "dry-run (no file written)"
        sys.stderr.write(
            f"WRITE {kind}: {write['target_ref']} ({write['bytes_written']} bytes) "
            f"[EXECUTE_AND_REPORT]\n"
        )
    if result["status"] == "COMPLETED":
        sys.stdout.write(result["final_response"] + "\n")
        return EXIT_OK

    block = result["block"] or {"reason_code": "BLOCKED", "message": "unknown"}
    sys.stderr.write(f"BLOCKED {block['reason_code']}: {block['message']}\n")
    return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
