"""R5 memory-candidate promotion tests (CANDIDATE -> VALIDATED).

Promotion is an explicit operator action only: the run pipeline never promotes
(automatic_runtime_promotion_allowed is false), so these are pure/store-level and need no
Core, except the no-auto-promotion end-to-end check.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from runtime.mvp_runtime import control
from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import MemoryBlocked
from runtime.mvp_runtime.memory import (
    CANDIDATE_STATUS,
    PROMOTED_STATUS,
    VALIDATED_SCOPE,
    VALIDATED_STATUS,
    promote_candidate,
)
from runtime.mvp_runtime.store import AUDIT_FILE, LedgerStore
from runtime.mvp_runtime.working_memory import WorkingMemoryStore, find_candidate, mark_promoted
from scripts.promote_memory_candidate import (  # noqa: E402
    _render_candidate_list,
    _retention_state,
    main as promote_main,
)


def _read_audit(ledger: LedgerStore) -> list[dict]:
    path = ledger.root / AUDIT_FILE
    if not path.is_file():
        return []
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]

from tests._helpers import requires_local_core
NOW = "2026-07-16T09:00:00Z"

# Originating-task provenance the pipeline now stamps on every candidate; promotion is audited
# against it (R5.4). data_sensitivity must be an audit-schema sensitivity enum value.
_ORIGIN = {
    "task_id": "task_origin1", "task_revision": 1, "trace_id": "trace_origin1",
    "core_context_binding_id": "ccb-origin1", "data_sensitivity": "INTERNAL",
}


def _candidate(**overrides):
    c = {
        "candidate_id": "memcand_x1", "candidate_type": "reusable_knowledge",
        "scope": "task_working_memory", "status": CANDIDATE_STATUS, "validated": False,
        "promotable": False, "content": "recurring-revenue model works", "created_at": NOW,
        "origin": dict(_ORIGIN),
    }
    c.update(overrides)
    return c


def test_promote_produces_validated_entry():
    v = promote_candidate(_candidate(), promoted_by="Thomas", reason="confirmed", now=NOW)
    assert v["status"] == VALIDATED_STATUS and v["scope"] == VALIDATED_SCOPE
    assert v["disposition"] == "EXECUTE_AND_REPORT"
    assert v["source_candidate_id"] == "memcand_x1"
    assert v["promoted_by"] == "Thomas" and v["promotion_reason"] == "confirmed"
    assert v["validated_memory_id"].startswith("valmem_")


@pytest.mark.parametrize("bad, code", [
    ({"status": "VALIDATED"}, "NOT_A_CANDIDATE"),        # already validated
    ({"scope": "related_validated_memory"}, "NOT_A_CANDIDATE"),
    ({"content": ""}, "INVALID_CANDIDATE"),
])
def test_promote_rejects_non_candidate(bad, code):
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(**bad), promoted_by="Thomas", reason="x", now=NOW)
    assert exc.value.reason_code == code


def test_promote_requires_operator_and_reason():
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(), promoted_by="  ", reason="x", now=NOW)
    assert exc.value.reason_code == "MISSING_OPERATOR"
    with pytest.raises(MemoryBlocked) as exc:
        promote_candidate(_candidate(), promoted_by="Thomas", reason="", now=NOW)
    assert exc.value.reason_code == "MISSING_REASON"


def test_validated_store_is_separate_from_candidates(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate()])
    store.append_validated([promote_candidate(_candidate(), promoted_by="Thomas", reason="ok", now=NOW)])
    assert [c["status"] for c in store.read_all()] == [CANDIDATE_STATUS]
    assert [v["status"] for v in store.read_validated()] == [VALIDATED_STATUS]


# --- candidate lifecycle: the PROMOTED retirement marker ---------------------

def test_mark_promoted_retires_the_candidate(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate()])
    assert find_candidate(store, "memcand_x1") is not None
    mark_promoted(store, _candidate(), validated_memory_id="valmem_1", now=NOW)
    # The latest entry for the id is the PROMOTED marker: the candidate is no longer live.
    assert find_candidate(store, "memcand_x1") is None
    statuses = [e["status"] for e in store.read_all() if e["candidate_id"] == "memcand_x1"]
    assert statuses == [CANDIDATE_STATUS, PROMOTED_STATUS]


def test_find_candidate_is_latest_wins_across_statuses(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    store.append([_candidate(content="old content")])
    store.append([_candidate(content="new content")])
    live = find_candidate(store, "memcand_x1")
    assert live is not None and live["content"] == "new content"


# --- operator promotion tool ------------------------------------------------

def test_operator_tool_promotes_and_audits(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate()])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"],
        store=store, ledger=ledger, control_store=ControlStore(tmp_path), now=NOW,
    )
    assert rc == 0
    validated = store.read_validated()
    assert len(validated) == 1 and validated[0]["source_candidate_id"] == "memcand_x1"

    # R5.4: the promotion is reported as its own OTHER/MEMORY_PROMOTED audit event, anchored to
    # the originating task and chained onto the ledger (genesis tip is None on a fresh ledger).
    events = _read_audit(ledger)
    assert len(events) == 1
    ev = events[0]
    assert ev["event_type"] == "OTHER"
    assert set(["MEMORY_PROMOTED", "EXECUTE_AND_REPORT", "SOURCE_CANDIDATE_memcand_x1"]) <= set(
        ev["event"]["reason_codes"])
    assert ev["event"]["outcome"] == "RECORDED"
    assert ev["actor"] == {"actor_type": "thomas", "actor_id": "Thomas",
                           "role_id": None, "role_version": None, "assignment_id": None}
    assert ev["task_id"] == _ORIGIN["task_id"] and ev["trace_id"] == _ORIGIN["trace_id"]
    assert ev["subject"]["subject_id"] == validated[0]["validated_memory_id"]
    assert ev["lineage"]["previous_event_sha256"] is None


def test_operator_tool_chains_onto_ledger_tip(tmp_path):
    """A second promotion chains onto the first event's hash — tamper-evident across actions."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(), _candidate(candidate_id="memcand_x2", content="a second finding")])
    cs = ControlStore(tmp_path)
    assert promote_main(["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "a"],
                        store=store, ledger=ledger, control_store=cs, now=NOW) == 0
    assert promote_main(["--candidate-id", "memcand_x2", "--promoted-by", "Thomas", "--reason", "b"],
                        store=store, ledger=ledger, control_store=cs, now=NOW) == 0
    events = _read_audit(ledger)
    assert len(events) == 2
    assert events[1]["lineage"]["previous_event_sha256"] == events[0]["integrity"]["event_sha256"]


def test_operator_tool_without_origin_fails_closed(tmp_path):
    """A candidate lacking origin provenance cannot be audited, so promotion fails closed and
    writes nothing — neither a validated entry nor an audit event."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(origin=None)])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"],
        store=store, ledger=ledger, control_store=ControlStore(tmp_path), now=NOW,
    )
    assert rc == 1
    assert store.read_validated() == []
    assert _read_audit(ledger) == []
    # And nothing retired the candidate either — the failure wrote nothing at all.
    assert find_candidate(store, "memcand_x1") is not None


def test_operator_tool_unknown_candidate_fails(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    rc = promote_main(
        ["--candidate-id", "nope", "--promoted-by", "Thomas", "--reason", "x"],
        store=store, ledger=ledger, control_store=ControlStore(tmp_path), now=NOW,
    )
    assert rc == 2
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


def test_operator_tool_requires_all_fields(tmp_path):
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate()])
    rc = promote_main(["--candidate-id", "memcand_x1"], store=store, ledger=ledger,
                      control_store=ControlStore(tmp_path), now=NOW)
    assert rc == 2
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


def test_operator_tool_retires_the_candidate_and_refuses_a_second_promotion(tmp_path):
    """The same physical action through the script door now consumes the candidate too:
    re-running the promotion finds no live candidate instead of minting a duplicate."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    cs = ControlStore(tmp_path)
    store.append([_candidate()])
    args = ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"]
    assert promote_main(args, store=store, ledger=ledger, control_store=cs, now=NOW) == 0
    assert find_candidate(store, "memcand_x1") is None
    rc = promote_main(args, store=store, ledger=ledger, control_store=cs, now=NOW)
    assert rc == 2
    assert len(store.read_validated()) == 1          # still exactly one VALIDATED entry
    assert len(_read_audit(ledger)) == 1


def test_operator_tool_promotes_the_latest_copy_of_a_re_appended_id(tmp_path):
    """Latest-wins, same as the ask/spend lookup: a first-match scan promoted the OLDEST
    copy of a re-appended candidate id — content the operator was not looking at."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(content="old superseded content")])
    store.append([_candidate(content="current content")])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "confirmed"],
        store=store, ledger=ledger, control_store=ControlStore(tmp_path), now=NOW,
    )
    assert rc == 0
    assert store.read_validated()[0]["content"] == "current content"


def test_operator_tool_refuses_an_expired_candidate(tmp_path, capsys):
    """Retention (§12.4) holds on the script door too — consumption already refused this."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    store.append([_candidate(expires_at="2026-07-16T08:00:00Z")])       # before NOW
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "x"],
        store=store, ledger=ledger, control_store=ControlStore(tmp_path), now=NOW,
    )
    assert rc == 1
    assert "CANDIDATE_EXPIRED" in capsys.readouterr().err
    assert store.read_validated() == []
    assert _read_audit(ledger) == []


def test_operator_tool_refuses_while_killed(tmp_path, capsys):
    """Kill-switch bound like the R10 consume: promotion mutates VALIDATED memory."""
    store = WorkingMemoryStore(tmp_path / "wm")
    ledger = LedgerStore(tmp_path / "ledger")
    cs = ControlStore(tmp_path)
    control.apply_command(cs, "kill", actor="op", now=NOW)
    store.append([_candidate()])
    rc = promote_main(
        ["--candidate-id", "memcand_x1", "--promoted-by", "Thomas", "--reason", "x"],
        store=store, ledger=ledger, control_store=cs, now=NOW,
    )
    assert rc == 1
    assert "RUNTIME_KILLED" in capsys.readouterr().err
    assert store.read_validated() == []
    assert _read_audit(ledger) == []
    assert find_candidate(store, "memcand_x1") is not None              # nothing retired


# --- no auto-promotion (governance) -----------------------------------------

@requires_local_core
def test_pipeline_never_promotes(tmp_path):
    from runtime.mvp_runtime.pipeline import run_task
    from runtime.mvp_runtime.worker import MockProvider

    wm = WorkingMemoryStore(tmp_path / "wm")
    run_task("이 사업 아이디어를 분석해줘: 구독형 반려동물 사료", provider=MockProvider(), working_memory=wm, now=NOW)
    assert wm.read_all()            # candidates were created
    assert wm.read_validated() == []  # but the run never promoted anything (no auto-promotion)


# --- what the listing has to say for a reader to act correctly on it ----------
#
# The weekly decision digest reads `--list` verbatim. On 2026-09-14 it recommended promoting
# "24 candidates" that were 28 rows collapsing to 13 distinct findings, none of which carried
# `promotable: true`; 20 were four copies each of five 2026-07-16 boilerplate lines. The old
# listing printed one line per row and a bare count, so none of that was visible. A listing
# whose reader cannot act correctly on it is the defect, not the reader.

def _listing_row(cid, content, *, promotable=False, created="2026-09-13T01:00:00Z",
                 expires=None, ctype="reusable_knowledge"):
    row = {"candidate_id": cid, "candidate_type": ctype, "content": content,
           "promotable": promotable, "created_at": created, "status": "CANDIDATE"}
    if expires is not None:
        row["expires_at"] = expires
    return row


def test_the_listing_collapses_copies_and_counts_distinct_findings():
    rows = [_listing_row(f"memcand_dup{i}", "same finding", created="2026-07-16T01:00:00Z")
            for i in range(4)]
    rows.append(_listing_row("memcand_one", "a distinct finding", promotable=True,
                             expires="2026-12-01T00:00:00Z"))
    text = _render_candidate_list(rows, "2026-09-14T00:00:00Z")
    assert "5 row(s) -> 2 distinct" in text
    assert "1 promotable" in text
    assert "x4" in text                      # the four copies are one line carrying their count
    assert text.count("same finding") == 1
    # What can be acted on leads, because the first lines are the ones a summary quotes.
    assert text.index("memcand_one") < text.index("memcand_dup")


def test_the_listing_refuses_to_imply_a_promotion_that_would_be_refused():
    """The exact sentence the digest could not infer: every row said CANDIDATE and nothing
    said none of them could be promoted."""
    text = _render_candidate_list([_listing_row("memcand_np", "x")], "2026-09-14T00:00:00Z")
    assert "0 promotable" in text
    assert "Nothing here is promotable" in text


def test_undated_rows_are_not_reported_as_live():
    """`is_expired` folds "no expiry" into "not expired" — correct for retention, which must
    never surprise-delete what it did not stamp, and wrong for a reader: those are precisely
    the rows `memory_prune` can never remove. On this host 20 of 28 rows carried no TTL and
    every prune reported `pruned:0`."""
    live = _listing_row("memcand_live", "live one", expires="2026-12-01T00:00:00Z")
    undated = _listing_row("memcand_undated", "undated one")
    gone = _listing_row("memcand_gone", "expired one", expires="2026-08-01T00:00:00Z")
    assert _retention_state(live, "2026-09-14T00:00:00Z") == "live"
    assert _retention_state(undated, "2026-09-14T00:00:00Z") == "no-ttl"
    assert _retention_state(gone, "2026-09-14T00:00:00Z") == "expired"
    text = _render_candidate_list([live, undated, gone], "2026-09-14T00:00:00Z")
    assert "1 expired, 1 undated" in text
    assert "cannot be pruned" in text
