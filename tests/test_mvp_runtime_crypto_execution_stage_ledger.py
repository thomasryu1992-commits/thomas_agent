"""The execution stage's anti-rollback ledger (Thomas 2026-09-30, EXECUTION_STAGE_ANTI_ROLLBACK_V0.1
D1 a / D2 / D3 a / D4).

The stage used to be one overwritable file whose witness (a CONSUMED approval) stays valid forever, so
putting back an older copy, or restoring the whole state directory from the daily backup, undid a
demotion and passed every check. These tests pin what replaced it:
- the ledger's tip is the stage;
- the anchor, kept out of the backup, vouches for it;
- the write order never reads above what was witnessed;
- the only way back up after a rollback is a BOOTSTRAP."""

from __future__ import annotations

import json
import shutil

import pytest

from runtime.mvp_runtime.crypto import execution_stage as es
from runtime.mvp_runtime.errors import ToolError
from runtime.read_only_kernel import integrity

from tests.test_mvp_runtime_crypto_execution_stage import (
    LATER, NOW, _Approvals, _ledger_rows, _paper, _register, _resolve, _testnet,
)


def _demote(tmp_path, approvals, target, *, now=LATER):
    status = _resolve(tmp_path, approvals, now)
    record = es.demote_record(status, target=target, registered_by="thomas", reason="stop", now=now)
    warnings = es.append_stage_record(record, tmp_path)
    return _resolve(tmp_path, approvals, now), warnings


def _snapshot(tmp_path, name="snap"):
    """What the daily backup takes: the state directory, WITHOUT the anchor (D1 a)."""
    source = es.ledger_path(tmp_path).parent
    target = tmp_path.parent / f"{tmp_path.name}-{name}"
    shutil.copytree(source, target)
    (target / es.ANCHOR_FILENAME).unlink(missing_ok=True)
    return target


# --- the tip is the stage, and the anchor vouches for it ---------------------------------------

def test_each_transition_appends_one_chained_row_and_moves_the_anchor(tmp_path):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    rows = _ledger_rows(tmp_path)
    assert [r["record"]["stage"] for r in rows] == ["PAPER", "SIGNED_TESTNET"]
    assert [r["seq"] for r in rows] == [0, 1]
    assert rows[1]["prev_row_sha256"] == rows[0]["row_sha256"]
    assert es.read_anchor(tmp_path)["seq"] == 1
    assert json.loads(es.stage_path(tmp_path).read_text(encoding="utf-8"))["stage"] == "SIGNED_TESTNET"


# --- R1: one file put back -----------------------------------------------------------------------

def test_an_old_ledger_put_back_after_a_demotion_reads_rolled_back(tmp_path):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    before = es.ledger_path(tmp_path).read_text(encoding="utf-8")
    demoted, _ = _demote(tmp_path, approvals, "READ_ONLY")
    assert demoted.stage == "READ_ONLY" and demoted.valid
    es.ledger_path(tmp_path).write_text(before, encoding="utf-8")      # the copy someone kept
    status = _resolve(tmp_path, approvals, LATER)
    assert (status.stage, status.reason_code) == ("READ_ONLY", es.STAGE_ROLLED_BACK)
    assert not status.allows(es.PURPOSE_TESTNET)


# --- R2: the whole directory restored from the daily backup ----------------------------------------

def test_untarring_the_backup_over_the_live_directory_reads_rolled_back(tmp_path):
    """The anchor is not in the archive, so it survives an untar-over at its newer row."""
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    backup = _snapshot(tmp_path)
    _demote(tmp_path, approvals, "SHADOW")
    state = es.ledger_path(tmp_path).parent
    for item in backup.iterdir():
        shutil.copy2(item, state / item.name)
    status = _resolve(tmp_path, approvals, LATER)
    assert (status.stage, status.reason_code) == ("READ_ONLY", es.STAGE_ROLLED_BACK)


def test_wiping_then_restoring_the_backup_reads_anchor_missing(tmp_path):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    backup = _snapshot(tmp_path)
    _demote(tmp_path, approvals, "SHADOW")
    state = es.ledger_path(tmp_path).parent
    shutil.rmtree(state)
    shutil.copytree(backup, state)
    status = _resolve(tmp_path, approvals, LATER)
    assert (status.stage, status.reason_code) == ("READ_ONLY", es.STAGE_ANCHOR_MISSING)


def test_after_a_rollback_the_only_way_up_is_a_bootstrap_which_starts_a_new_chain(tmp_path):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    before = es.ledger_path(tmp_path).read_text(encoding="utf-8")
    _demote(tmp_path, approvals, "READ_ONLY")
    es.ledger_path(tmp_path).write_text(before, encoding="utf-8")
    rolled = _resolve(tmp_path, approvals, LATER)
    with pytest.raises(ToolError) as refused:
        es.plan_transition(rolled, target="SIGNED_TESTNET", registered_by="t", reason="r")
    assert refused.value.reason_code == es.STAGE_BOOTSTRAP_ONLY_SHADOW_OR_PAPER
    boot = _register(tmp_path, approvals, rolled, "PAPER", approval_id="approval_reboot", now=LATER,
                     attestation="after the restore")
    assert (boot.stage, boot.valid) == ("PAPER", True)
    rows = _ledger_rows(tmp_path)
    assert [r["seq"] for r in rows] == [0]
    assert rows[0]["record"]["evidence"]["replaced_reason_code"] == es.STAGE_ROLLED_BACK
    assert len(list(es.ledger_path(tmp_path).parent.glob("execution_stage_ledger.replaced-*.jsonl"))) == 1


def test_a_climb_cannot_extend_a_chain_its_anchor_does_not_vouch_for(tmp_path):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    content = es.plan_transition(paper, target="SIGNED_TESTNET", registered_by="t", reason="r")
    granted = approvals.grant("approval_tn", content)
    record = es.record_from_approved(content, status_now=paper, approval_id="approval_tn",
                                     action_fingerprint=granted["action_fingerprint"], now=NOW)
    es.anchor_path(tmp_path).unlink()
    with pytest.raises(ToolError) as refused:
        es.assert_appendable(record, tmp_path)
    assert refused.value.reason_code == es.STAGE_LEDGER_NEEDS_BINDING


# --- the write order: never above what was witnessed -----------------------------------------------

def test_a_demotion_whose_anchor_write_fails_still_takes_effect_and_asks_for_a_sync(tmp_path, monkeypatch):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)

    def _refuse(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(es, "write_anchor", _refuse)
    demoted, warnings = _demote(tmp_path, approvals, "SHADOW")
    monkeypatch.undo()
    assert (demoted.stage, demoted.valid, demoted.anchor_behind) == ("SHADOW", True, True)
    assert any(es.STAGE_ANCHOR_WRITE_FAILED in w for w in warnings)
    with pytest.raises(ToolError) as refused:
        es.plan_transition(demoted, target="PAPER", registered_by="t", reason="r")
    assert refused.value.reason_code == es.STAGE_ANCHOR_SYNC_REQUIRED
    assert es.sync_anchor(tmp_path, now=LATER)["changed"] is True
    assert _resolve(tmp_path, approvals, LATER).anchor_behind is False


def test_an_anchor_two_rows_behind_reads_read_only_and_sync_restores_it(tmp_path, monkeypatch):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    monkeypatch.setattr(es, "write_anchor", lambda *a, **k: (_ for _ in ()).throw(OSError("full")))
    _demote(tmp_path, approvals, "PAPER")
    _demote(tmp_path, approvals, "SHADOW")
    monkeypatch.undo()
    assert _resolve(tmp_path, approvals, LATER).reason_code == es.STAGE_ANCHOR_BEHIND
    es.sync_anchor(tmp_path, now=LATER)
    assert (_resolve(tmp_path, approvals, LATER).stage, _resolve(tmp_path, approvals, LATER).valid) == ("SHADOW", True)


def test_a_crash_after_the_spend_and_before_the_row_leaves_the_old_stage(tmp_path, monkeypatch):
    approvals = _Approvals()
    paper = _paper(tmp_path, approvals)
    monkeypatch.setattr(es, "_atomic_write", lambda *a, **k: (_ for _ in ()).throw(OSError("crash")))
    content = es.plan_transition(paper, target="SIGNED_TESTNET", registered_by="t", reason="r")
    granted = approvals.grant("approval_tn", content)
    record = es.record_from_approved(content, status_now=paper, approval_id="approval_tn",
                                     action_fingerprint=granted["action_fingerprint"], now=NOW)
    with pytest.raises(OSError):
        es.append_stage_record(record, tmp_path)
    monkeypatch.undo()
    assert _resolve(tmp_path, approvals).stage == "PAPER"


# --- the sync cannot launder a rollback ------------------------------------------------------------

@pytest.mark.parametrize("damage", ["missing", "tampered", "off_chain"])
def test_sync_refuses_an_anchor_a_restore_would_leave(tmp_path, damage):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    if damage == "missing":
        es.anchor_path(tmp_path).unlink()
    elif damage == "tampered":
        es.anchor_path(tmp_path).write_text("{}", encoding="utf-8")
    else:
        before = es.ledger_path(tmp_path).read_text(encoding="utf-8")
        _demote(tmp_path, approvals, "READ_ONLY")
        es.ledger_path(tmp_path).write_text(before, encoding="utf-8")
    with pytest.raises(ToolError) as refused:
        es.sync_anchor(tmp_path, now=LATER)
    assert refused.value.reason_code == es.STAGE_ANCHOR_SYNC_REFUSED


# --- chain integrity ---------------------------------------------------------------------------------

def _write_rows(tmp_path, rows):
    es.ledger_path(tmp_path).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


@pytest.mark.parametrize("damage", ["edited_middle", "dropped_middle", "reordered"])
def test_a_broken_chain_reads_read_only(tmp_path, damage):
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    _demote(tmp_path, approvals, "PAPER")
    rows = _ledger_rows(tmp_path)
    if damage == "edited_middle":
        rows[1]["appended_at"] = "2026-01-01T00:00:00Z"
    elif damage == "dropped_middle":
        del rows[1]
    else:
        rows[0], rows[1] = rows[1], rows[0]
    _write_rows(tmp_path, rows)
    status = _resolve(tmp_path, approvals, LATER)
    assert (status.stage, status.reason_code) == ("READ_ONLY", es.STAGE_LEDGER_BROKEN)


def test_one_approval_cannot_witness_two_rows(tmp_path):
    """A forger who re-appends an old approved row, with every hash recomputed, is still refused."""
    approvals = _Approvals()
    _testnet(tmp_path, approvals)
    rows = _ledger_rows(tmp_path)
    replay = {k: v for k, v in rows[1].items() if k != "row_sha256"}
    replay.update(seq=2, prev_row_sha256=rows[1]["row_sha256"])
    replay["record"] = dict(replay["record"], previous_stage="SIGNED_TESTNET", transition="REBIND")
    body = {k: v for k, v in replay["record"].items() if k != "record_sha256"}
    replay["record"]["record_sha256"] = integrity.sha256_record(body)
    replay["row_sha256"] = integrity.sha256_record(replay)
    _write_rows(tmp_path, [*rows, replay])
    es.write_anchor(tmp_path, replay, now=NOW)
    assert _resolve(tmp_path, approvals).reason_code == es.STAGE_LEDGER_BROKEN


# --- genesis: the machine the ledger arrives on ------------------------------------------------------

def test_a_machine_with_only_the_old_file_reads_ledger_missing_and_boots_by_bootstrap(tmp_path):
    """D3 a: the existing BOOTSTRAP at PAPER starts the chain; no migration writes one unapproved."""
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    es.ledger_path(tmp_path).unlink()
    es.anchor_path(tmp_path).unlink()
    legacy = _resolve(tmp_path, approvals)
    assert (legacy.stage, legacy.reason_code, legacy.recorded_stage) == (
        "READ_ONLY", es.STAGE_LEDGER_MISSING, "PAPER")
    boot = _register(tmp_path, approvals, legacy, "PAPER", approval_id="approval_genesis",
                     attestation="the ledger arrives")
    assert (boot.stage, boot.valid) == ("PAPER", True)
    assert _ledger_rows(tmp_path)[0]["record"]["evidence"]["replaced_reason_code"] == es.STAGE_LEDGER_MISSING


def test_the_read_never_raises_on_a_damaged_anchor(tmp_path):
    approvals = _Approvals()
    _paper(tmp_path, approvals)
    es.anchor_path(tmp_path).write_text("[not an anchor", encoding="utf-8")
    status = _resolve(tmp_path, approvals)
    assert (status.stage, status.reason_code) == ("READ_ONLY", es.STAGE_ANCHOR_TAMPERED)
