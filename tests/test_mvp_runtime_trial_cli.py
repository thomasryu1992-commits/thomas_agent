"""trial_cli tests — the operator surface for requesting and running a candidate-role trial.

`trial.py` is covered; the CLI wrapping it measured 0%, with no test file importing it. What
that left untested is the CLI's own work: subcommand dispatch, translating a fail-closed
`MvpRuntimeError` into `EXIT_BLOCKED`, and — the part with real reporting consequences — how
a finished trial is narrated. A run whose ledger append failed, or whose grant was consumed
by a run that then blocked, must say so; those lines are the operator's only evidence.

`trial.run_trial` is stubbed here rather than driven end-to-end. That is deliberate and not
just convenience: the trial machinery needs a local Core activation, which a core-neutral
checkout does not have, so an end-to-end test would skip on exactly the machines where the
CLI is most likely to be edited. Stubbing keeps these assertions running everywhere. The
one Core-dependent path is asserted through its block, which needs no activation to reach.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime import trial, trial_cli
from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.errors import ApprovalBlocked
from runtime.mvp_runtime.providers import HOSTED_PROVIDER_ENV, VALIDATOR_PROVIDER_ENV

APPROVAL_ID = "approval_abc123"


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """`_run` selects its providers from the ambient environment before running the
    trial. Unset the opt-ins so a granted developer machine does not construct real
    hosted providers — and print their SAFETY_GATE banners — while running the suite."""
    monkeypatch.delenv(HOSTED_PROVIDER_ENV, raising=False)
    monkeypatch.delenv(VALIDATOR_PROVIDER_ENV, raising=False)


def _result(status="COMPLETED", *, persist_error=None, block=None, report=True):
    """The shape `trial.run_trial` returns, reduced to what the CLI reads."""
    records = {}
    if report:
        records["trial_report"] = {
            "role_id": "research.general", "role_version": "0.1",
            "final_result": "PASS", "automatic_result": "PASS", "independent_result": None,
        }
    return {
        "status": status,
        "final_response": "the trial answer",
        "block": block,
        "persist_error": persist_error,
        "records": records,
        "approval": {
            "approval_id": APPROVAL_ID,
            "consumption": {"consumption_ref": "consumption_xyz"},
        },
    }


@pytest.fixture
def stub_run(monkeypatch):
    """Replace `trial.run_trial` with a canned result; return a setter for it."""
    holder = {}

    def _run_trial(approval_id, **kw):
        holder["approval_id"] = approval_id
        return holder["result"]

    monkeypatch.setattr(trial, "run_trial", _run_trial)
    monkeypatch.setattr(trial_cli.trial, "run_trial", _run_trial)

    def _set(result):
        holder["result"] = result
        return holder

    return _set


# === dispatch =======================================================================

def test_no_subcommand_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        trial_cli.main([])
    assert exc.value.code != 0


def test_run_dispatches_the_approval_id(stub_run, capsys):
    holder = stub_run(_result())
    assert trial_cli.main(["run", APPROVAL_ID]) == EXIT_OK
    assert holder["approval_id"] == APPROVAL_ID


# === how a finished trial is narrated ===============================================

def test_completed_run_prints_the_answer_on_stdout(stub_run, capsys):
    stub_run(_result())
    assert trial_cli.main(["run", APPROVAL_ID]) == EXIT_OK
    captured = capsys.readouterr()  # one call — a second would read an already-drained buffer
    assert "the trial answer" in captured.out
    # The answer is the payload; the bookkeeping belongs on stderr so `> file` keeps it clean.
    assert "CONSUMED" not in captured.out


def test_completed_run_reports_consumption_and_promotion_effect(stub_run, capsys):
    stub_run(_result())
    trial_cli.main(["run", APPROVAL_ID])
    err = capsys.readouterr().err
    assert f"CONSUMED {APPROVAL_ID}" in err
    assert "one-time use" in err
    # A trial is evidence for a later decision — it must never read as an activation.
    assert "promotion_effect=NONE" in err


def test_blocked_run_still_reports_the_spent_grant(stub_run, capsys):
    """A failed run does not refund the grant, so the CONSUMED line must still appear."""
    stub_run(_result("BLOCKED", block={"reason_code": "VALIDATION_FAILED", "message": "no"}))
    assert trial_cli.main(["run", APPROVAL_ID]) == EXIT_BLOCKED
    err = capsys.readouterr().err
    assert f"CONSUMED {APPROVAL_ID}" in err
    assert "BLOCKED VALIDATION_FAILED: no" in err


def test_blocked_run_without_a_block_body_still_exits_blocked(stub_run, capsys):
    """Missing block detail must not crash the report or leak a success exit."""
    stub_run(_result("BLOCKED", block=None, report=False))
    assert trial_cli.main(["run", APPROVAL_ID]) == EXIT_BLOCKED
    assert "BLOCKED BLOCKED: unknown" in capsys.readouterr().err


def test_failed_ledger_append_is_stated_not_implied(stub_run, capsys):
    """The trial ran and the grant is gone; if nothing was written, say it plainly."""
    stub_run(_result(persist_error="disk full"))
    assert trial_cli.main(["run", APPROVAL_ID]) == EXIT_OK
    err = capsys.readouterr().err
    assert "LEDGER: NOT recorded (disk full)" in err
    assert "no durable audit trail" in err


def test_successful_ledger_append_says_where(stub_run, capsys):
    stub_run(_result())
    trial_cli.main(["run", APPROVAL_ID])
    err = capsys.readouterr().err
    assert "LEDGER: recorded to" in err
    assert "NOT recorded" not in err


# === fail-closed translation ========================================================

def test_a_runtime_block_becomes_exit_blocked(monkeypatch, capsys):
    """`main` catches `MvpRuntimeError` and routes it through `report_block`."""
    def _blocked(approval_id, **kw):
        raise ApprovalBlocked("UNKNOWN_APPROVAL", "no approval with id x")

    # via monkeypatch, not a bare attribute write: `trial_cli.trial` *is* the trial module,
    # so an unmanaged assignment would leak into every later test in the session.
    monkeypatch.setattr(trial_cli.trial, "run_trial", _blocked)
    assert trial_cli.main(["run", "nope"]) == EXIT_BLOCKED
    assert "UNKNOWN_APPROVAL" in capsys.readouterr().err


def test_request_without_a_local_core_blocks_rather_than_guessing(capsys, monkeypatch):
    """The Core-dependent path, asserted through the block it fails closed with.

    Reaching this needs no activation — which is the point: on a core-neutral checkout the
    CLI must refuse, not proceed against an unbound Core.
    """
    monkeypatch.setattr(
        trial_cli.trial, "request_trial",
        lambda *a, **kw: (_ for _ in ()).throw(
            ApprovalBlocked("CORE_NOT_ACTIVATED", "active Core pointer not found")),
    )
    assert trial_cli.main(["request", "research.general", "일감"]) == EXIT_BLOCKED
    assert "CORE_NOT_ACTIVATED" in capsys.readouterr().err
