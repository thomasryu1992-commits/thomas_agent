"""Option C deploy runner, C3a-2: the actor's records — consumption store, request states, audit
chain, latch — and the startup check that makes them agree before the runner listens.

All synthetic, in ``tmp_path``. Every test that opens a record needs POSIX descriptor opens
(``O_NOFOLLOW``, ``O_DIRECTORY``, ``dir_fd``, ``flock``) and skips without them.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from runtime.mvp_runtime.deploy_runner import (audit_chain, consumption, latch, private_files, reasons,
                                               request_state, startup)
from runtime.mvp_runtime.deploy_runner.approval_verify import SCHEMAS_DIR, VerifiedApproval
from runtime.mvp_runtime.deploy_runner.reasons import RunnerRefused

pytestmark = pytest.mark.skipif(not private_files.private_files_supported(),
                                reason="needs POSIX private-file opens (O_NOFOLLOW, dir_fd, flock)")

NOW = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc)
STAMP = "2026-10-16T12:00:00Z"
STORE_ID = "1" * 32
INSTALL_SHA = "e" * 40
H = "sha256:" + "4" * 64


def code_of(excinfo) -> str:
    return excinfo.value.reason_code


@pytest.fixture
def xdir(tmp_path):
    d = tmp_path / "actor"
    d.mkdir(mode=0o700)
    d.chmod(0o700)
    return d


def write_rows(path: Path, rows, mode: int = 0o600) -> None:
    path.write_bytes(b"".join(private_files.encode_row(r) for r in rows))
    path.chmod(mode)


def install_genesis(xdir: Path, store_id: str = STORE_ID) -> dict:
    row = consumption.genesis_row(store_id=store_id, install_sha=INSTALL_SHA, created_at=STAMP)
    write_rows(xdir / consumption.STORE_FILE, [row])
    return row


def approval(n: int = 0, *, nonce: str | None = None) -> VerifiedApproval:
    return VerifiedApproval(approval_id="dap_" + "abcdefghijklmnopqrstuvwxyz"[n] * 26,
                            nonce=nonce or f"{n:032x}", action="promote", approval_sha256=H,
                            trust_config_sha256=H, target={}, issued_at=NOW, expires_at=NOW)


def chained(rows):
    """Recompute seq, prev and hashes so a crafted store is internally consistent."""
    out, prev = [], None
    for i, r in enumerate(rows):
        r = {k: v for k, v in r.items() if k != "row_sha256"}
        r["seq"], r["prev_sha256"] = i, prev
        r["row_sha256"] = consumption.row_hash(r)
        out.append(r)
        prev = r["row_sha256"]
    return out


# --- consumption store --------------------------------------------------------------------------

def test_the_actor_never_creates_the_store(xdir):
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_GENESIS_MISSING"
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).consume(approval(), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    assert code_of(e) == "CONSUMPTION_STORE_UNAVAILABLE"
    assert not (xdir / consumption.STORE_FILE).exists()


def test_a_first_row_that_is_not_the_genesis_is_genesis_missing(xdir):
    g = install_genesis(xdir)
    row = {"type": "APPROVAL", "approval_id": approval().approval_id, "nonce": approval().nonce,
           "approval_sha256": H, "action": "promote", "request_id": "r1", "consumed_at": STAMP}
    write_rows(xdir / consumption.STORE_FILE, chained([row, g]))
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_GENESIS_MISSING"


def test_a_spent_approval_is_chained_after_the_genesis(xdir):
    install_genesis(xdir)
    store = consumption.ConsumptionStore(xdir)
    row = store.consume(approval(), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    state = store.read(expected_store_id=STORE_ID)
    assert row["seq"] == 1 and row["prev_sha256"] == state.rows[0]["row_sha256"]
    assert state.approval_ids == {approval().approval_id} and state.last == row


@pytest.mark.parametrize("second", [approval(0), approval(1, nonce=approval(0).nonce)])
def test_an_approval_id_or_nonce_is_spent_once(xdir, second):
    install_genesis(xdir)
    store = consumption.ConsumptionStore(xdir)
    store.consume(approval(0), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    with pytest.raises(RunnerRefused) as e:
        store.consume(second, request_id="r2", now=STAMP, expected_store_id=STORE_ID)
    assert code_of(e) == "APPROVAL_ALREADY_CONSUMED"
    assert len(store.read().rows) == 2


def test_a_store_for_another_store_id_is_refused(xdir):
    install_genesis(xdir)
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read(expected_store_id="2" * 32)
    assert code_of(e) == "STORE_ID_MISMATCH"
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).consume(approval(), request_id="r1", now=STAMP, expected_store_id="2" * 32)
    assert code_of(e) == "CONSUMPTION_STORE_UNAVAILABLE"


def _two_spent(xdir):
    install_genesis(xdir)
    store = consumption.ConsumptionStore(xdir)
    store.consume(approval(0), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    store.consume(approval(1), request_id="r2", now=STAMP, expected_store_id=STORE_ID)
    path = xdir / consumption.STORE_FILE
    return store, path, [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("damage", ["edit", "delete_middle", "reorder", "torn_tail", "missing_newline", "trailing_byte", "blank_line"])
def test_a_damaged_store_is_a_broken_chain_and_spends_nothing(xdir, damage):
    store, path, rows = _two_spent(xdir)
    raw = path.read_bytes()
    if damage == "edit":
        rows[1]["request_id"] = "r9"
        write_rows(path, rows)
    elif damage == "delete_middle":
        write_rows(path, [rows[0], rows[2]])
    elif damage == "reorder":
        write_rows(path, [rows[0], rows[2], rows[1]])
    elif damage == "torn_tail":
        path.write_bytes(raw[:-5])
    elif damage == "missing_newline":
        path.write_bytes(raw[:-1])  # the last row is whole JSON; only its line end is missing
    elif damage == "trailing_byte":
        path.write_bytes(raw[:-1] + b" ")  # a last line that does not end the way a whole write ends
    elif damage == "blank_line":
        path.write_bytes(raw + b"\n")
    before = path.read_bytes()
    with pytest.raises(RunnerRefused) as e:
        store.read()
    assert code_of(e) == "STORE_CHAIN_BROKEN"
    with pytest.raises(RunnerRefused) as e:
        store.consume(approval(2), request_id="r3", now=STAMP, expected_store_id=STORE_ID)
    assert code_of(e) == "CONSUMPTION_STORE_UNAVAILABLE"
    assert path.read_bytes() == before


def test_a_sequence_gap_alone_is_a_broken_chain(xdir):
    """Hashes and links all consistent; only the sequence number is wrong."""
    g = install_genesis(xdir)
    a = {"type": "APPROVAL", "approval_id": approval().approval_id, "nonce": approval().nonce,
         "approval_sha256": H, "action": "promote", "request_id": "r1", "consumed_at": STAMP,
         "seq": 5, "prev_sha256": g["row_sha256"]}
    a["row_sha256"] = consumption.row_hash(a)
    write_rows(xdir / consumption.STORE_FILE, [g, a])
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_CHAIN_BROKEN"


def test_a_wrong_link_alone_is_a_broken_chain(xdir):
    """Sequence and self-hash consistent; only the link to the previous row is wrong."""
    g = install_genesis(xdir)
    a = {"type": "APPROVAL", "approval_id": approval().approval_id, "nonce": approval().nonce,
         "approval_sha256": H, "action": "promote", "request_id": "r1", "consumed_at": STAMP,
         "seq": 1, "prev_sha256": H}
    a["row_sha256"] = consumption.row_hash(a)
    write_rows(xdir / consumption.STORE_FILE, [g, a])
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_CHAIN_BROKEN"


def test_a_consistently_rechained_duplicate_is_still_refused(xdir):
    g = install_genesis(xdir)
    a = {"type": "APPROVAL", "approval_id": approval().approval_id, "nonce": approval().nonce,
         "approval_sha256": H, "action": "promote", "request_id": "r1", "consumed_at": STAMP}
    write_rows(xdir / consumption.STORE_FILE, chained([g, a, dict(a, request_id="r2")]))
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_CHAIN_BROKEN"


def test_a_recovery_row_this_increment_does_not_implement_is_refused_not_guessed(xdir):
    g = install_genesis(xdir)
    r = {"type": "RESOLUTION", "resolution_id": "lres_" + "a" * 26, "nonce": "f" * 32, "document_sha256": H,
         "latch_id": "latch_" + "a" * 26, "consumed_at": STAMP}
    write_rows(xdir / consumption.STORE_FILE, chained([g, r]))
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).read()
    assert code_of(e) == "STORE_CHAIN_BROKEN"


@pytest.mark.parametrize("unsafe", ["group_readable", "symlink", "hardlink", "dir_open"])
def test_a_store_that_is_not_the_actors_private_file_is_unavailable(xdir, tmp_path, unsafe):
    install_genesis(xdir)
    path = xdir / consumption.STORE_FILE
    if unsafe == "group_readable":
        path.chmod(0o640)
    elif unsafe == "symlink":
        real = tmp_path / "elsewhere"
        path.rename(real)
        path.symlink_to(real)
    elif unsafe == "hardlink":
        os.link(path, tmp_path / "second-name")
    elif unsafe == "dir_open":
        xdir.chmod(0o755)
    for call in (lambda s: s.read(),
                 lambda s: s.consume(approval(), request_id="r1", now=STAMP, expected_store_id=STORE_ID)):
        with pytest.raises(RunnerRefused) as e:
            call(consumption.ConsumptionStore(xdir))
        assert code_of(e) == "CONSUMPTION_STORE_UNAVAILABLE"


def test_concurrent_spends_of_one_approval_succeed_exactly_once(xdir):
    install_genesis(xdir)
    results: list[str] = []
    gate = threading.Barrier(8)

    def spend(i):
        gate.wait()
        try:
            consumption.ConsumptionStore(xdir).consume(approval(0), request_id=f"r{i}", now=STAMP,
                                                       expected_store_id=STORE_ID)
            results.append("ok")
        except RunnerRefused as exc:
            results.append(exc.reason_code)

    threads = [threading.Thread(target=spend, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results.count("ok") == 1
    assert set(results) == {"ok", "APPROVAL_ALREADY_CONSUMED"}
    assert len(consumption.ConsumptionStore(xdir).read().rows) == 2


@pytest.mark.parametrize("failure", ["short", "oserror", "fsync"])
def test_a_failed_write_spends_nothing_and_says_so(xdir, monkeypatch, failure):
    install_genesis(xdir)
    path = xdir / consumption.STORE_FILE
    before = path.read_bytes()
    real_write, real_fsync = os.write, os.fsync
    if failure == "short":
        monkeypatch.setattr(private_files.os, "write", lambda fd, data: real_write(fd, data[:7]))
    elif failure == "oserror":
        monkeypatch.setattr(private_files.os, "write", lambda fd, data: (_ for _ in ()).throw(OSError("full")))
    else:
        monkeypatch.setattr(private_files.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("io")))
    with pytest.raises(RunnerRefused) as e:
        consumption.ConsumptionStore(xdir).consume(approval(), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    assert code_of(e) == "CONSUMPTION_WRITE_FAILED"
    monkeypatch.setattr(private_files.os, "write", real_write)
    monkeypatch.setattr(private_files.os, "fsync", real_fsync)
    assert path.read_bytes() == before


# --- request states -----------------------------------------------------------------------------

def test_a_new_request_is_claimed_and_a_resend_is_in_flight_with_no_ttl(xdir):
    rs = request_state.RequestStore(xdir)
    assert rs.begin("r1", H, now=STAMP).replay is None
    later = (NOW + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with pytest.raises(RunnerRefused) as e:
        rs.begin("r1", H, now=later)
    assert code_of(e) == "REQUEST_IN_FLIGHT"


def test_the_same_id_for_a_different_request_is_reused(xdir):
    rs = request_state.RequestStore(xdir)
    rs.begin("r1", H, now=STAMP)
    with pytest.raises(RunnerRefused) as e:
        rs.begin("r1", "sha256:" + "5" * 64, now=STAMP)
    assert code_of(e) == "REQUEST_ID_REUSED"


@pytest.mark.parametrize("state", [request_state.COMPLETED, request_state.REFUSED_BEFORE_CONSUME])
def test_a_finished_request_replays_its_recorded_reply(xdir, state):
    rs = request_state.RequestStore(xdir)
    rs.begin("r1", H, now=STAMP)
    reply = {"ok": False, "reason_code": "APPROVAL_EXPIRED", "reason": reasons.RESPONSE_TEXT["APPROVAL_EXPIRED"]}
    rs.finish("r1", H, state, now=STAMP, result=reply)
    assert rs.begin("r1", H, now=STAMP).replay == reply


@pytest.mark.parametrize("state,code", [(request_state.UNKNOWN, "EFFECT_STATE_UNKNOWN"),
                                        (request_state.PARTIAL, "FAILED_EFFECT_PARTIAL")])
def test_an_uncertain_request_is_never_run_again(xdir, state, code):
    rs = request_state.RequestStore(xdir)
    rs.begin("r1", H, now=STAMP)
    rs.finish("r1", H, state, now=STAMP, result=None)
    with pytest.raises(RunnerRefused) as e:
        rs.begin("r1", H, now=STAMP)
    assert code_of(e) == code


@pytest.mark.parametrize("call", [
    lambda rs: rs.finish("absent", H, request_state.COMPLETED, now=STAMP, result={}),
    lambda rs: rs.finish("r1", "sha256:" + "5" * 64, request_state.COMPLETED, now=STAMP, result={}),
    lambda rs: rs.finish("r1", H, request_state.CLAIMED, now=STAMP, result=None),
    lambda rs: rs.finish("r1", H, request_state.COMPLETED, now=STAMP, result=None),
    lambda rs: rs.mark_unknown("absent", now=STAMP),
])
def test_a_finish_the_caller_does_not_own_is_refused(xdir, call):
    rs = request_state.RequestStore(xdir)
    rs.begin("r1", H, now=STAMP)
    with pytest.raises(RunnerRefused) as e:
        call(rs)
    assert code_of(e) == "RUNNER_ERROR"


def test_a_request_record_that_cannot_be_read_never_reads_as_no_record(xdir):
    (xdir / request_state.REQUESTS_FILE).write_bytes(b'{"request_id":"r1"}\n')
    (xdir / request_state.REQUESTS_FILE).chmod(0o600)
    with pytest.raises(RunnerRefused) as e:
        request_state.RequestStore(xdir).begin("r2", H, now=STAMP)
    assert code_of(e) == "RUNNER_ERROR"


# --- audit chain --------------------------------------------------------------------------------

def _audit(xdir):
    return audit_chain.AuditLog(xdir, runner_install_sha=INSTALL_SHA)


def test_audit_rows_chain_and_read_back(xdir):
    log = _audit(xdir)
    assert log.read() == []
    a = log.append(execution_state="RUNNER_START", validation_result="NOT_REQUIRED", now=STAMP, store_id=STORE_ID)
    b = log.append(execution_state="RECEIVED", validation_result="NOT_REQUIRED", now=STAMP, request_id="r1")
    assert (a["seq"], b["seq"]) == (1, 2) and b["previous_event_sha256"] == a["event_sha256"]
    assert log.read() == [a, b]
    assert a["event_id"].startswith("dae_") and a["runner_install_sha"] == INSTALL_SHA


@pytest.mark.parametrize("damage", ["edit", "delete_middle", "reorder", "torn_tail"])
def test_a_damaged_audit_is_a_broken_chain_and_takes_no_more_rows(xdir, damage):
    log = _audit(xdir)
    for i in range(3):
        log.append(execution_state="RECEIVED", validation_result="NOT_REQUIRED", now=STAMP, request_id=f"r{i}")
    path = xdir / audit_chain.AUDIT_FILE
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    if damage == "edit":
        rows[1]["request_id"] = "r9"
        write_rows(path, rows)
    elif damage == "delete_middle":
        write_rows(path, [rows[0], rows[2]])
    elif damage == "reorder":
        write_rows(path, [rows[0], rows[2], rows[1]])
    else:
        path.write_bytes(path.read_bytes()[:-3])
    before = path.read_bytes()
    with pytest.raises(RunnerRefused) as e:
        log.read()
    assert code_of(e) == "AUDIT_CHAIN_BROKEN"
    with pytest.raises(RunnerRefused) as e:
        log.append(execution_state="RECEIVED", validation_result="NOT_REQUIRED", now=STAMP)
    assert code_of(e) == "AUDIT_WRITE_FAILED"
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [("previous_event_sha256", H), ("seq", 7)])
def test_an_audit_row_wrong_in_one_link_field_alone_is_a_broken_chain(xdir, field, value):
    log = _audit(xdir)
    for i in range(2):
        log.append(execution_state="RECEIVED", validation_result="NOT_REQUIRED", now=STAMP, request_id=f"r{i}")
    path = xdir / audit_chain.AUDIT_FILE
    rows = [json.loads(x) for x in path.read_text().splitlines()]
    rows[1][field] = value
    rows[1]["event_sha256"] = audit_chain.event_hash(rows[1])
    write_rows(path, rows)
    with pytest.raises(RunnerRefused) as e:
        log.read()
    assert code_of(e) == "AUDIT_CHAIN_BROKEN"


@pytest.mark.parametrize("kwargs", [
    {"execution_state": "LATCH_CLEARED"},
    {"execution_state": "RECEIVED", "reason_code": "made-up"},
    {"execution_state": "RECEIVED", "secret_value": "x"},
    {"execution_state": "RECEIVED", "request_id": "has space"},
])
def test_an_audit_row_outside_the_schema_is_never_written(xdir, kwargs):
    log = _audit(xdir)
    with pytest.raises(RunnerRefused) as e:
        log.append(validation_result="NOT_REQUIRED", now=STAMP, **kwargs)
    assert code_of(e) == "AUDIT_WRITE_FAILED"
    assert log.read() == []


# --- latch --------------------------------------------------------------------------------------

def test_a_latch_is_written_once_and_never_rewritten(xdir):
    lt = latch.Latch(xdir)
    assert lt.read() is None
    first = lt.set(reason_code="RECONCILED_UNKNOWN", request_id="r1", now=STAMP)
    raw = (xdir / latch.LATCH_FILE).read_bytes()
    second = lt.set(reason_code="AUDIT_CHAIN_BROKEN", request_id=None, now=STAMP)
    assert second == first == lt.read()
    assert (xdir / latch.LATCH_FILE).read_bytes() == raw
    assert first["latch_id"].startswith("latch_") and len(first["latch_id"]) == 32


@pytest.mark.parametrize("damage", ["garbage", "unknown_code", "open_mode", "symlink", "directory"])
def test_a_latch_that_cannot_be_read_with_certainty_blocks(xdir, tmp_path, damage):
    path = xdir / latch.LATCH_FILE
    good = {"latch_id": "latch_" + "a" * 26, "reason_code": "RECONCILED_UNKNOWN", "request_id": None,
            "created_at": STAMP}
    if damage == "garbage":
        path.write_bytes(b"{not json")
        path.chmod(0o600)
    elif damage == "unknown_code":
        write_rows(path, [dict(good, reason_code="NOT_A_CODE")])
    elif damage == "open_mode":
        write_rows(path, [good], mode=0o644)
    elif damage == "symlink":
        write_rows(tmp_path / "real", [good])
        path.symlink_to(tmp_path / "real")
    else:
        path.mkdir(mode=0o700)
    with pytest.raises(RunnerRefused) as e:
        latch.Latch(xdir).read()
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


# --- startup check ------------------------------------------------------------------------------

def _reconcile(xdir, now=NOW, **kw):
    return startup.reconcile(xdir, runner_install_sha=INSTALL_SHA, trust_config_sha256=H, now=now,
                             backstep_tolerance_s=5, **kw)


def _states(xdir):
    return [(r["execution_state"], r["reason_code"]) for r in _audit(xdir).read()]


def test_one_instance_at_a_time(xdir):
    fd = startup.acquire_instance_lock(xdir)
    try:
        with pytest.raises(RunnerRefused) as e:
            startup.acquire_instance_lock(xdir)
        assert code_of(e) == "INSTANCE_LOCK_HELD"
    finally:
        os.close(fd)
    os.close(startup.acquire_instance_lock(xdir))


def test_a_fresh_install_starts_unlatched_and_records_its_start(xdir):
    install_genesis(xdir)
    result = _reconcile(xdir)
    assert (result.latched, result.clock_trusted, result.store_id) == (False, True, STORE_ID)
    assert _states(xdir) == [("RUNNER_START", None)]
    assert _audit(xdir).read()[0]["store_id"] == STORE_ID
    assert startup.read_last_seen(xdir) == NOW


def test_a_missing_genesis_blocks_the_start_and_says_why_in_the_audit(xdir):
    with pytest.raises(RunnerRefused) as e:
        _reconcile(xdir)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"
    assert _states(xdir) == [("RUNNER_START", "STORE_GENESIS_MISSING")]


def test_a_store_swapped_for_another_install_blocks_the_start(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    install_genesis(xdir, store_id="2" * 32)
    with pytest.raises(RunnerRefused):
        _reconcile(xdir)
    assert _states(xdir)[-1] == ("RUNNER_START", "STORE_ID_MISMATCH")
    with pytest.raises(RunnerRefused):
        _reconcile(xdir, expected_store_id=STORE_ID)


def _consume_with_audit(xdir, request_id="r1", n=0, terminal=None):
    rs = request_state.RequestStore(xdir)
    rs.begin(request_id, H, now=STAMP)
    row = consumption.ConsumptionStore(xdir).consume(approval(n), request_id=request_id, now=STAMP,
                                                     expected_store_id=STORE_ID)
    log = _audit(xdir)
    log.append(execution_state="CONSUMED", validation_result="PASS", now=STAMP, request_id=request_id,
               consumption_seq=row["seq"], consumption_row_sha256=row["row_sha256"], store_id=STORE_ID)
    if terminal:
        log.append(execution_state=terminal, validation_result="PASS", now=STAMP, request_id=request_id)
        rs.finish(request_id, H, request_state.COMPLETED, now=STAMP, result={"ok": True, "data": None})
    return row


def test_a_completed_request_leaves_the_runner_unlatched(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    _consume_with_audit(xdir, terminal="COMPLETED")
    assert _reconcile(xdir).latched is False


def test_a_consumed_request_with_no_outcome_becomes_unknown_and_latches(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    _consume_with_audit(xdir)
    result = _reconcile(xdir)
    assert result.latched and "RECONCILED_UNKNOWN" in result.reasons
    assert ("RECONCILED_UNKNOWN", "RECONCILED_UNKNOWN") in _states(xdir)
    assert request_state.RequestStore(xdir).latest()["r1"]["state"] == request_state.UNKNOWN
    assert latch.Latch(xdir).read() is not None


def test_an_effect_whose_outcome_row_never_reached_the_audit_latches(xdir):
    """CASE E: the request record says completed, but the audit has no outcome after CONSUMED."""
    install_genesis(xdir)
    _reconcile(xdir)
    _consume_with_audit(xdir)
    request_state.RequestStore(xdir).finish("r1", H, request_state.COMPLETED, now=STAMP,
                                            result={"ok": True, "data": None})
    result = _reconcile(xdir)
    assert result.latched and ("RECONCILED_UNKNOWN", "RECONCILED_UNKNOWN") in _states(xdir)


def test_a_claim_that_never_reached_consumption_still_becomes_unknown(xdir):
    install_genesis(xdir)
    request_state.RequestStore(xdir).begin("r1", H, now=STAMP)
    result = _reconcile(xdir)
    assert result.latched
    with pytest.raises(RunnerRefused) as e:
        request_state.RequestStore(xdir).begin("r1", H, now=STAMP)
    assert code_of(e) == "EFFECT_STATE_UNKNOWN"


def test_a_spent_approval_the_audit_does_not_know_latches(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    consumption.ConsumptionStore(xdir).consume(approval(), request_id="r1", now=STAMP, expected_store_id=STORE_ID)
    result = _reconcile(xdir)
    assert result.latched and "RECONCILIATION_MISMATCH" in result.reasons


def test_an_audit_consumption_the_store_does_not_hold_latches(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    _audit(xdir).append(execution_state="CONSUMED", validation_result="PASS", now=STAMP, request_id="r1",
                        consumption_seq=1, consumption_row_sha256=H, store_id=STORE_ID)
    _audit(xdir).append(execution_state="COMPLETED", validation_result="PASS", now=STAMP, request_id="r1")
    assert "RECONCILIATION_MISMATCH" in _reconcile(xdir).reasons


def test_deleting_the_latch_file_does_not_clear_the_latch(xdir):
    """C2X-16: the records, not the file, decide."""
    install_genesis(xdir)
    _reconcile(xdir)
    _consume_with_audit(xdir)
    _reconcile(xdir)
    (xdir / latch.LATCH_FILE).unlink()
    result = _reconcile(xdir)
    assert result.latched and latch.Latch(xdir).read() is not None


def test_a_latch_file_on_clean_records_still_latches(xdir):
    install_genesis(xdir)
    latch.Latch(xdir).set(reason_code="AUDIT_CHAIN_BROKEN", request_id=None, now=STAMP)
    assert _reconcile(xdir).latched


def test_a_broken_audit_latches_and_is_not_written_further(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    path = xdir / audit_chain.AUDIT_FILE
    path.write_bytes(path.read_bytes()[:-2])
    before = path.read_bytes()
    result = _reconcile(xdir)
    assert result.latched and "AUDIT_CHAIN_BROKEN" in result.reasons
    assert path.read_bytes() == before
    assert latch.Latch(xdir).read()["reason_code"] == "AUDIT_CHAIN_BROKEN"


def test_an_unreadable_latch_blocks_the_start(xdir):
    install_genesis(xdir)
    (xdir / latch.LATCH_FILE).write_bytes(b"x")
    (xdir / latch.LATCH_FILE).chmod(0o600)
    with pytest.raises(RunnerRefused) as e:
        _reconcile(xdir)
    assert code_of(e) == "RUNNER_STARTUP_BLOCKED"


def test_a_clock_behind_the_last_seen_time_starts_no_new_effect(xdir):
    install_genesis(xdir)
    _reconcile(xdir, now=NOW)
    result = _reconcile(xdir, now=NOW - timedelta(seconds=60))
    assert result.clock_trusted is False
    assert startup.read_last_seen(xdir) == NOW
    assert _states(xdir)[-1] == ("RUNNER_START", "CLOCK_UNTRUSTED")
    assert _reconcile(xdir, now=NOW - timedelta(seconds=3)).clock_trusted is True
    _reconcile(xdir, now=NOW + timedelta(hours=1))
    assert startup.read_last_seen(xdir) == NOW + timedelta(hours=1)


# --- schemas ------------------------------------------------------------------------------------

def test_the_latch_schema_enum_is_the_code_table():
    schema = json.loads((SCHEMAS_DIR / "deploy_latch.v0.1.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]["reason_code"]["enum"]) == set(reasons.ALL_CODES)


def test_every_row_the_records_write_satisfies_its_schema(xdir):
    install_genesis(xdir)
    _reconcile(xdir)
    _consume_with_audit(xdir, terminal="COMPLETED")
    for row in consumption.ConsumptionStore(xdir).read().rows:
        consumption._validate(row, "STORE_CHAIN_BROKEN")
    for row in request_state.RequestStore(xdir).latest().values():
        request_state._validate(row)
    audit_chain.verify_chain(_audit(xdir).read())
