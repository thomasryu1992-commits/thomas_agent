"""CLI tests — full single-agent pipeline (MockProvider)."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime import cli, control
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.store import LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore

from tests._helpers import requires_local_core


def _ledger_fingerprint() -> list[tuple[str, int]]:
    """Sizes of the machine-local ledger + working-memory files, for an untouched check."""
    roots = (LedgerStore.default().root, WorkingMemoryStore.default().root)
    return sorted((str(p), p.stat().st_size)
                  for root in roots if root.is_dir() for p in root.glob("*.jsonl"))


@requires_local_core
def test_cli_runs_pipeline_and_emits_response(capsys, tmp_path):
    rc = cli.main(["이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"],
                  store=LedgerStore(tmp_path / "ledger"),
                  working_memory=WorkingMemoryStore(tmp_path / "memory"),
                  control_store=ControlStore(tmp_path))
    assert rc == cli.EXIT_OK
    out = capsys.readouterr().out
    # Final response is human-readable text (not raw JSON) with findings + the read-only note.
    assert "Key findings" in out
    assert "Read-only analysis" in out


@requires_local_core
def test_cli_run_does_not_touch_the_machine_ledger_or_working_memory(tmp_path):
    # Regression: main() used to hardcode the repo-local stores, so this suite appended
    # synthetic runs to the operator's ledger and seeded working memory — which retrieval
    # feeds back as context into later real runs.
    ledger, memory = tmp_path / "ledger", tmp_path / "memory"
    before = _ledger_fingerprint()
    rc = cli.main(["이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"],
                  store=LedgerStore(ledger), working_memory=WorkingMemoryStore(memory),
                  control_store=ControlStore(tmp_path))
    assert rc == cli.EXIT_OK
    assert _ledger_fingerprint() == before
    # The run really happened — it just landed in the injected stores.
    assert (ledger / "audit_events.jsonl").is_file()
    assert (memory / "candidates.jsonl").is_file()


@pytest.mark.parametrize("command, code", [("kill", "RUNTIME_KILLED"), ("pause", "RUNTIME_PAUSED")])
def test_cli_refuses_new_execution_while_not_active(tmp_path, capsys, command, code):
    """kill_blocks: new_execution — the manual host door. A KILLED runtime refusing a
    Telegram request but running the same request from the host CLI (model call included)
    was the one unbound execution door the QA review found."""
    control_store = ControlStore(tmp_path)
    control.apply_command(control_store, command, actor="op", now="2026-07-19T00:00:00Z")
    ledger = tmp_path / "ledger"
    rc = cli.main(["이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"],
                  store=LedgerStore(ledger),
                  working_memory=WorkingMemoryStore(tmp_path / "memory"),
                  control_store=control_store)
    assert rc == cli.EXIT_BLOCKED
    assert code in capsys.readouterr().err
    assert not ledger.exists()          # refused at the door: no run, no ledger writes


def test_cli_empty_argv_is_usage_block(capsys):
    rc = cli.main([""])
    assert rc == cli.EXIT_USAGE
    assert "EMPTY_REQUEST" in capsys.readouterr().err


def test_cli_bom_only_argv_is_usage_block(capsys):
    rc = cli.main(["﻿"])
    assert rc == cli.EXIT_USAGE
    assert "EMPTY_REQUEST" in capsys.readouterr().err


def test_cli_unknown_option_is_usage_block_not_request_text(capsys):
    # A non-existent flag must fail closed, not silently become part of the prompt.
    rc = cli.main(["--current-pointer", ".runtime_governance_state/CURRENT_CORE_RELEASE.yaml",
                   "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"])
    assert rc == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "unrecognized option" in err
    assert "--current-pointer" in err


def test_cli_known_options_are_not_rejected(capsys):
    # The rejection must not catch the two real flags (or --write-output's PATH value).
    rc = cli.main(["--independent-validation", "--write-output", "x.md", ""])
    assert rc == cli.EXIT_USAGE
    assert "EMPTY_REQUEST" in capsys.readouterr().err


def test_cli_single_dash_unknown_option_is_usage_block(capsys):
    # One dash short is still a mistyped flag — it must not fold into the request text
    # (the e1609db fix covered only the double-dash form).
    rc = cli.main(["-i", "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"])
    assert rc == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "unrecognized option" in err and "'-i'" in err
