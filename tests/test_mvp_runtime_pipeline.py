"""R2.7 Single-Agent End-to-End pipeline tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.errors import ProviderError, ToolError
from runtime.mvp_runtime.pipeline import run_task
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization
from runtime.mvp_runtime.worker import MockProvider
from runtime.mvp_runtime.workspace import RealWorkspaceWriter

NOW = "2026-07-15T09:00:00Z"
REQUEST = "이 사업 아이디어를 분석해줘: 구독형 반려동물 사료 배송"

from tests._helpers import requires_local_core


class _ErrorProvider:
    model_id, model_version = "err", "0.1.0"

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        raise ProviderError("BOOM", "provider exploded")


class _OverconfidentProvider(MockProvider):
    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        r = super().generate(prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds)
        r.analysis = {**r.analysis, "uncertainty": [], "assumptions": []}
        return r


class _ErrorSearchTool:
    tool_id, tool_version, network_egress = "search.readonly", "0.1.0", False

    def search(self, query, *, max_results, timeout_seconds):
        raise ToolError("BOOM", "search backend unavailable")


def _authorized_writer() -> RealWorkspaceWriter:
    """A writer holding a granted authorization, as select_writer would return once the
    Safety-Flag Gate has passed. Lets the write path be exercised without an activation
    record on the test machine."""
    return RealWorkspaceWriter(authorization=Authorization(
        flags=(FILESYSTEM_WRITE,), provider_id="workspace.writer",
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    ))


@pytest.fixture
def workspace_repo(tmp_path, monkeypatch):
    """Redirect only the write-facing root at ``workspace/`` under tmp_path.

    The pipeline resolves governance, schemas and the Core pointer through each module's
    own root, so those keep reading the real repo; repointing ``workspace``'s root alone
    isolates the write (and the control state it consults) without cloning the repo.
    Returns the tmp root, so ``<root>/workspace/...`` is where a write should land.
    """
    (tmp_path / "workspace").mkdir()
    monkeypatch.setattr("runtime.mvp_runtime.workspace._repo_root", lambda: tmp_path)
    return tmp_path


# --- fail-closed without a Core (run everywhere) ----------------------------

def test_empty_request_blocks():
    r = run_task("", now=NOW)
    assert r["status"] == "BLOCKED" and r["delivered"] is False
    assert r["block"]["reason_code"] == "EMPTY_REQUEST"


def test_out_of_scope_blocks_before_binding():
    r = run_task("분석해줘", now=NOW, constraints=["something_else"])
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "OUT_OF_MVP_SCOPE"


# --- full run (needs a Core) ------------------------------------------------

@requires_local_core
def test_normal_task_completes_and_delivers():
    r = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    assert isinstance(r["final_response"], str) and "Key findings" in r["final_response"]
    rec = r["records"]
    for key in ("received_task", "task", "binding", "permission_decision", "search_permission_decision",
                "role_assignment", "tool_use", "agent_output", "invocation", "validation_result", "audit_trail"):
        assert key in rec
    assert rec["agent_output"]["status"] == "needs_validation"
    assert rec["validation_result"]["validation"]["result"] == "PASS"
    assert len(rec["audit_trail"]) == 7  # + TOOL_USED + MODEL_INVOKED + MEMORY_CANDIDATE_CREATED
    assert [e["event_type"] for e in rec["audit_trail"]] == [
        "TASK_CREATED", "PERMISSION_DECIDED", "OTHER", "OTHER",
        "MEMORY_CANDIDATE_CREATED", "VALIDATION_COMPLETED", "TASK_STATE_CHANGED"
    ]
    # The read-only search hits are recorded as source-attributed evidence on the output.
    assert any(e["type"] == "web_search" for e in rec["agent_output"]["evidence"])
    assert rec["tool_use"]["tool_id"] == "search.readonly" and rec["tool_use"]["read_only"] is True


@requires_local_core
def test_common_safety_invariants_hold_on_completed_run():
    rec = run_task(REQUEST, provider=MockProvider(), now=NOW)["records"]
    a = rec["role_assignment"]
    assert a["allowed_tool_ids"] == [] and a["allowed_program_ids"] == []
    limits = a["execution_budget"]["limits"]
    assert limits["max_tool_calls"] == 0 and limits["max_program_calls"] == 0
    # ALLOW is not an executor token; nothing is granted/executed.
    assert rec["permission_decision"]["decision"]["permission_decision"] == "ALLOW"
    assert rec["permission_decision"]["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert rec["validation_result"]["runtime_effect"]["mode"] == "REVIEW_ONLY"
    assert all(e["runtime_effect"]["mode"] == "EVIDENCE_ONLY" for e in rec["audit_trail"])


@requires_local_core
def test_provider_error_blocks_the_run():
    r = run_task(REQUEST, provider=_ErrorProvider(), now=NOW)
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "PROVIDER_ERROR"


@requires_local_core
def test_search_tool_error_blocks_the_run():
    # A failing read-only search fails the whole run closed (no silent skip).
    r = run_task(REQUEST, provider=MockProvider(), search_tool=_ErrorSearchTool(), now=NOW)
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "TOOL_ERROR"


@requires_local_core
def test_revise_validation_withholds_delivery():
    r = run_task(REQUEST, provider=_OverconfidentProvider(), now=NOW)
    assert r["status"] == "BLOCKED" and r["delivered"] is False
    assert r["block"]["reason_code"] == "VALIDATION_REVISE"


@requires_local_core
def test_recorded_replay_determinism():
    # No working_memory store => pure, deterministic (two identical runs are equal).
    a = run_task(REQUEST, provider=MockProvider(), now=NOW)
    b = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert a == b


@requires_local_core
def test_working_memory_accumulates_and_feeds_back(tmp_path):
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")

    # First run: no prior memory to draw on; it stores its candidates.
    first = run_task(REQUEST, provider=MockProvider(), working_memory=wm, now=NOW)
    assert first["status"] == "COMPLETED"
    assert first["records"]["memory_retrieved"] == []
    assert wm.read_all()  # candidates were accumulated

    # Second run: retrieves the first run's candidates and records them as working_memory evidence.
    second = run_task(REQUEST, provider=MockProvider(), working_memory=wm, now="2026-07-16T10:00:00Z")
    assert second["status"] == "COMPLETED"
    assert second["records"]["memory_retrieved"]  # prior candidates surfaced as context
    ev_types = {e["type"] for e in second["records"]["agent_output"]["evidence"]}
    assert "working_memory" in ev_types


@requires_local_core
def test_working_memory_corrupt_store_fails_closed(tmp_path):
    from runtime.mvp_runtime.working_memory import ENTRIES_FILE, WorkingMemoryStore
    root = tmp_path / "wm"
    root.mkdir()
    (root / ENTRIES_FILE).write_text("{bad json}\n", encoding="utf-8")
    r = run_task(REQUEST, provider=MockProvider(), working_memory=WorkingMemoryStore(root), now=NOW)
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "WORKING_MEMORY_UNREADABLE"


# --- R8: the controlled write -----------------------------------------------


@requires_local_core
def test_no_write_happens_unless_asked(tmp_path):
    """The write is opt-in: a plain run plans no write grant and produces no write."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert r["status"] == "COMPLETED"
    assert "write" not in r
    assert "write_use" not in r["records"]
    assert r["records"].get("write_permission_decision") is None


@requires_local_core
def test_write_is_planned_audited_and_reported(workspace_repo):
    """The happy path: a passing run creates the file, audits it, and reports it —
    the EXECUTE_AND_REPORT obligation end to end."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW,
                 write_path="reports/out.md", writer=_authorized_writer())
    assert r["status"] == "COMPLETED"
    assert (workspace_repo / "workspace/reports/out.md").is_file()
    # Reported to the caller...
    assert r["write"]["relative_path"] == "reports/out.md"
    assert r["write"]["disposition"] == "EXECUTE_AND_REPORT"
    assert r["write"]["filesystem_write"] is True
    # ...and durably audited, referencing its own EXECUTE_AND_REPORT grant.
    write_events = [
        e for e in r["records"]["audit_trail"]
        if "WORKSPACE_WRITE" in e["event"]["reason_codes"]
    ]
    assert len(write_events) == 1
    assert "EXECUTE_AND_REPORT" in write_events[0]["event"]["reason_codes"]
    permdec = r["records"]["write_permission_decision"]
    assert permdec["decision"]["permission_decision"] == "EXECUTE_AND_REPORT"


@requires_local_core
def test_a_rejected_analysis_never_leaves_an_artifact(workspace_repo):
    """The safety property that matters most: validation must gate the write, so a run
    that is not delivered also does not write."""
    r = run_task(REQUEST, provider=_OverconfidentProvider(), now=NOW,
                 write_path="reports/rejected.md", writer=_authorized_writer())
    assert r["status"] == "BLOCKED"
    assert r["delivered"] is False
    assert not (workspace_repo / "workspace/reports/rejected.md").exists()
    assert "write" not in r


@requires_local_core
def test_the_written_file_is_the_delivered_response(workspace_repo):
    """What lands on disk must be what the run reported — not a divergent rendering."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW,
                 write_path="reports/out.md", writer=_authorized_writer())
    on_disk = (workspace_repo / "workspace/reports/out.md").read_text(encoding="utf-8")
    assert on_disk == r["final_response"]


@requires_local_core
def test_default_writer_is_a_dry_run(workspace_repo):
    """Without an explicitly authorized writer the pipeline plans, audits and reports the
    write but leaves nothing on disk."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW,
                 write_path="reports/out.md")
    assert r["status"] == "COMPLETED"
    assert not (workspace_repo / "workspace/reports/out.md").exists()
    assert r["write"]["filesystem_write"] is False


@requires_local_core
def test_an_escaping_write_path_blocks_the_run(workspace_repo):
    """A path escape must fail the run closed, not fall back to a safe location."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW,
                 write_path="../../escaped.md", writer=_authorized_writer())
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "PATH_ESCAPE"
    assert not (workspace_repo.parent / "escaped.md").exists()
