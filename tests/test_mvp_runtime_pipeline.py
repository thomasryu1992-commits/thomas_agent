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
def test_final_response_lists_the_sources_the_analysis_cites():
    """The analysis cites evidence as [S1]..[SN] (the worker's numbering); the delivered
    response must resolve those citations — same order as the prompt, so the numbers line
    up by construction. Citations that resolved to nothing were the gap."""
    r = run_task(REQUEST, provider=MockProvider(), now=NOW)
    assert r["status"] == "COMPLETED"
    hits = r["records"]["tool_use"]["hits"]
    assert hits                                       # mock search returns deterministic hits
    assert "## Sources" in r["final_response"]
    assert f"[S1] {hits[0]['title']} — {hits[0]['url']}" in r["final_response"]


@requires_local_core
def test_search_tool_error_degrades_the_run_not_blocks():
    """Search is enrichment, not the task (explicit decision with the 2026-07-21 Tavily
    rollout): a failing backend — quota exhausted, transport, malformed — degrades the
    run to no live evidence, recorded and audited, never a blocked analysis. Inverts the
    original fail-closed behavior; 'recorded and audited' is what keeps it non-silent."""
    r = run_task(REQUEST, provider=MockProvider(), search_tool=_ErrorSearchTool(), now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    tool_use = r["records"]["tool_use"]
    assert tool_use["degraded"] is True
    assert tool_use["degraded_reason_code"] == "TOOL_ERROR"
    assert tool_use["result_count"] == 0 and tool_use["sources"] == []
    tool_events = [e for e in r["records"]["audit_trail"]
                   if "TOOL_USED" in e["event"]["reason_codes"]]
    assert len(tool_events) == 1
    assert "SEARCH_DEGRADED" in tool_events[0]["event"]["reason_codes"]
    assert "TOOL_ERROR" in tool_events[0]["event"]["reason_codes"]


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


# --- M5a: a successful revision is captured as a correction candidate ---------

class _ReviseThenPassSpecialist(MockProvider):
    """First output is over-confident (automatic REVISE); the regeneration passes. Mirrors the
    validator suite's provider — proves the M3 revision recovers a REVISE to a delivered PASS."""

    def __init__(self):
        super().__init__()
        self.calls = 0

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):
        r = super().generate(prompt, max_output_tokens=max_output_tokens, timeout_seconds=timeout_seconds)
        self.calls += 1
        if self.calls == 1:
            r.analysis = {**r.analysis, "uncertainty": [], "assumptions": []}
        return r


def _learning_events(tmp_path):
    from runtime.mvp_runtime.jsonl import read_objects
    from runtime.mvp_runtime.store import MEMORY_FILE
    path = tmp_path / "ledger" / MEMORY_FILE
    if not path.is_file():
        return []
    return [e for e in read_objects(path, read_code="X", label="mem")
            if e.get("record_type") == "working_memory_learning_event.v0"]


@requires_local_core
def test_m5a_successful_revision_mints_a_correction_candidate(tmp_path):
    from runtime.mvp_runtime.store import LedgerStore
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")
    store = LedgerStore(tmp_path / "ledger")
    r = run_task(REQUEST, provider=_ReviseThenPassSpecialist(), working_memory=wm,
                 store=store, revise=True, now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    assert r["records"]["revision"]["attempted"] is True
    assert r["records"]["revision"]["exhausted"] is False
    # A correction candidate was minted, CANDIDATE-only (never auto-trusted).
    corr = [c for c in wm.read_all() if c.get("learning_source") == "revision"]
    assert len(corr) == 1
    assert corr[0]["status"] == "CANDIDATE" and corr[0]["validated"] is False
    assert corr[0]["correction_ref"].startswith("trace:")
    assert r["learning_candidates"][0]["candidate_id"] == corr[0]["candidate_id"]
    # ...and audited on the memory-event stream (the retention-event precedent).
    events = _learning_events(tmp_path)
    assert len(events) == 1 and events[0]["candidate_id"] == corr[0]["candidate_id"]
    assert events[0]["learning_source"] == "revision"


@requires_local_core
def test_m5a_first_pass_success_mints_no_correction(tmp_path):
    """A run that PASSes on the first try — even with --revise armed — has no correction to
    learn: no revision happened, so no correction candidate is minted."""
    from runtime.mvp_runtime.store import LedgerStore
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")
    store = LedgerStore(tmp_path / "ledger")
    r = run_task(REQUEST, provider=MockProvider(), working_memory=wm, store=store, revise=True, now=NOW)
    assert r["status"] == "COMPLETED"
    assert [c for c in wm.read_all() if c.get("learning_source") == "revision"] == []
    assert "learning_candidates" not in r
    assert _learning_events(tmp_path) == []


@requires_local_core
def test_m5a_a_correction_that_was_not_stored_is_not_audited_as_captured(tmp_path):
    """The learning event's own contract is that a candidate WAS captured. It lived in a
    separate try block from the append, so a failed append still put that tamper-evident
    claim on the ledger — and `/promote` on the id it names refuses CANDIDATE_GONE while the
    audit says it exists. `operator_feedback._capture_correction` already had this right."""
    from runtime.mvp_runtime.errors import PersistenceError
    from runtime.mvp_runtime.store import LedgerStore
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore

    class _AppendFails(WorkingMemoryStore):
        def append(self, entries):
            raise PersistenceError("WORKING_MEMORY_WRITE_FAILED", "disk full")

    store = LedgerStore(tmp_path / "ledger")
    r = run_task(REQUEST, provider=_ReviseThenPassSpecialist(),
                 working_memory=_AppendFails(tmp_path / "wm"), store=store, revise=True, now=NOW)

    # The run still delivers — working memory is enrichment, and the analysis was audited.
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    # Nothing was stored, so nothing claims it was.
    assert [c for c in WorkingMemoryStore(tmp_path / "wm").read_all()
            if c.get("learning_source") == "revision"] == []
    assert _learning_events(tmp_path) == []
    assert "learning_candidates" not in r
    # ...and the loss is reported, on both the general and the correction-specific key.
    assert r["working_memory_error"] == "WORKING_MEMORY_WRITE_FAILED"
    assert r["learning_error"] == "WORKING_MEMORY_WRITE_FAILED"


@requires_local_core
def test_recorded_retries_cover_every_model_call_the_run_made(tmp_path):
    """`retry_count` counted only the specialist and the reviewer, so a run whose triage or
    pre-revision calls retried a 503 reported 0 and read as a clean first try — while
    `tokens_used` in the same call already summed all of them. Recording provider
    instability is the entire reason the field exists."""
    from runtime.mvp_runtime.store import LedgerStore

    class _RetryingSpecialist(_ReviseThenPassSpecialist):
        def generate(self, prompt, *, max_output_tokens, timeout_seconds):
            r = super().generate(prompt, max_output_tokens=max_output_tokens,
                                 timeout_seconds=timeout_seconds)
            r.retries = 1          # every call needed one transient retry
            return r

    r = run_task(REQUEST, provider=_RetryingSpecialist(), store=LedgerStore(tmp_path / "ledger"),
                 revise=True, now=NOW)
    assert r["status"] == "COMPLETED"
    usage = r["records"]["budget_usage"]["usage"]
    # Two specialist calls (the first, then the regeneration), each retried once. Before the
    # fix the pre-revision call's retry was dropped and this read 1.
    assert usage["retry_count"] == 2
    assert usage["revision_cycles"] == 1


@requires_local_core
def test_m5a_revision_without_working_memory_is_safe():
    """No working-memory store => nothing to accumulate into; the revision still delivers and
    the run does not crash reaching for a store that isn't there."""
    r = run_task(REQUEST, provider=_ReviseThenPassSpecialist(), revise=True, now=NOW)
    assert r["status"] == "COMPLETED" and r["delivered"] is True
    assert "learning_candidates" not in r


@requires_local_core
def test_m5c_promoted_correction_feeds_back_as_a_correction(tmp_path):
    """The full A→D loop: a revision correction (M5a) → operator promotion (M5b) → a later run
    reads it back as a VALIDATED correction with its marker intact (M5c). The framing that makes
    the specialist apply it is unit-tested in the worker; here we prove the marker round-trips
    all the way to the next run's validated context."""
    from runtime.mvp_runtime.memory import promote_candidate
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")

    # Run 1: a revision recovers a REVISE to PASS and mints a correction candidate (full origin).
    first = run_task(REQUEST, provider=_ReviseThenPassSpecialist(), working_memory=wm,
                     revise=True, now=NOW)
    assert first["status"] == "COMPLETED"
    correction = [c for c in wm.read_all() if c.get("learning_source") == "revision"][0]

    # M5b: the operator promotes it (the revision correction carries origin, so the audited
    # promotion path accepts it).
    validated = promote_candidate(correction, promoted_by="thomas", reason="반복 교정",
                                  now="2026-07-15T09:30:00Z")
    assert validated["learning_source"] == "revision"
    wm.append_validated([validated])

    # Run 2: the promoted correction is read back as VALIDATED context, marker intact.
    second = run_task(REQUEST, provider=MockProvider(), working_memory=wm,
                      now="2026-07-15T10:00:00Z")
    assert second["status"] == "COMPLETED"
    retrieved = second["records"]["validated_memory_retrieved"]
    assert [e["validated_memory_id"] for e in retrieved] == [validated["validated_memory_id"]]
    assert retrieved[0]["learning_source"] == "revision"     # feeds back AS a correction
    evidence = second["records"]["agent_output"]["evidence"]
    assert any(e.get("ref") == f"validated_memory:{validated['validated_memory_id']}" for e in evidence)


@requires_local_core
def test_promoted_memory_feeds_back_as_validated_context(tmp_path):
    """The full loop: run -> candidate -> operator promotion -> next run reads it back."""
    from runtime.mvp_runtime.memory import promote_candidate
    from runtime.mvp_runtime.working_memory import WorkingMemoryStore
    wm = WorkingMemoryStore(tmp_path / "wm")

    first = run_task(REQUEST, provider=MockProvider(), working_memory=wm, now=NOW)
    assert first["status"] == "COMPLETED"
    assert first["records"]["validated_memory_retrieved"] == []  # nothing promoted yet

    # Explicit operator promotion (never on the run path) of the first run's candidate.
    candidate = wm.read_all()[0]
    validated = promote_candidate(candidate, promoted_by="thomas", reason="test promotion",
                                  now="2026-07-16T09:30:00Z")
    wm.append_validated([validated])

    second = run_task(REQUEST, provider=MockProvider(), working_memory=wm, now="2026-07-16T10:00:00Z")
    assert second["status"] == "COMPLETED"
    retrieved = second["records"]["validated_memory_retrieved"]
    assert [e["validated_memory_id"] for e in retrieved] == [validated["validated_memory_id"]]
    evidence = second["records"]["agent_output"]["evidence"]
    assert {"ref": f"validated_memory:{validated['validated_memory_id']}",
            "type": "validated_memory",
            "candidate_type": validated["candidate_type"]} in evidence


@requires_local_core
def test_corrupt_validated_store_fails_closed(tmp_path):
    from runtime.mvp_runtime.working_memory import VALIDATED_FILE, WorkingMemoryStore
    root = tmp_path / "wm"
    root.mkdir()
    (root / VALIDATED_FILE).write_text("{bad json}\n", encoding="utf-8")
    r = run_task(REQUEST, provider=MockProvider(), working_memory=WorkingMemoryStore(root), now=NOW)
    assert r["status"] == "BLOCKED"
    assert r["block"]["reason_code"] == "VALIDATED_MEMORY_UNREADABLE"


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


@requires_local_core
def test_a_revision_that_fails_mid_way_still_records_what_it_produced(tmp_path, monkeypatch):
    """`_revise_once` takes `records` and writes it step by step, which looks like a leaked
    parameter and is not: if the re-verify raises, run_task's handler persists whatever the
    revision had already produced, so a blocked run's trail still shows the regenerated output
    and its validation. Returning everything at the end would lose exactly that."""
    import runtime.mvp_runtime.pipeline as pipeline_mod
    from runtime.mvp_runtime.errors import WorkerBlocked

    def _boom(*args, **kwargs):
        raise WorkerBlocked("PROVIDER_ERROR", "re-verify exploded")

    monkeypatch.setattr(pipeline_mod, "run_validation_worker", _boom)
    result = run_task(REQUEST, provider=_ReviseThenPassSpecialist(),
                      independent_validation=True, revise=True, now=NOW)

    assert result["status"] == "BLOCKED"
    assert result["block"]["reason_code"] == "PROVIDER_ERROR"
    # The regenerated attempt is on the trail even though the run was withheld...
    assert "agent_output" in result["records"]
    assert "validation_result" in result["records"]
    # ...and `revision` is absent, because the failure happened before that line — the same
    # place the original inline block would have stopped.
    assert "revision" not in result["records"]
