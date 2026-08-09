"""Ledger retention tests — bound the active files without ever destroying evidence.

The record ledger reached 56MB on the live host and only grows. The danger in fixing that
is obvious: this is an evidence store, and the easy implementation ("drop the old rows") is
``audit_concealment``, a BLOCK-tier prohibited action. So the property under test is not
"the file got smaller" but **"nothing was lost"** — every row is either still in the active
file or byte-identical in an archive beside it.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import retention
from runtime.mvp_runtime.errors import PersistenceError
from runtime.mvp_runtime.store import (
    AUDIT_FILE,
    BLOCKS_FILE,
    CONTROL_FILE,
    RECORDS_FILE,
    LedgerStore,
)

NOW = "2026-07-26T07:00:00Z"


def _store(tmp_path) -> LedgerStore:
    return LedgerStore(tmp_path / "ledger")


def _fill(store: LedgerStore, filename: str, rows: int) -> list[str]:
    path = store.root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"n": index, "pad": "x" * 50}, sort_keys=True) for index in range(rows)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return lines


def _read_lines(path) -> list[str]:
    return [l for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# --- nothing is destroyed ----------------------------------------------------

def test_every_rotated_row_survives_byte_identical(tmp_path):
    """The whole point. Rows move; they do not disappear, and they are not re-serialized."""
    store = _store(tmp_path)
    original = _fill(store, RECORDS_FILE, 500)

    summary = retention.rotate_file(store, RECORDS_FILE, keep_rows=100, now=NOW)

    assert summary["rotated"] == 400 and summary["kept"] == 100
    archived = _read_lines(store.root / summary["archive"])
    retained = _read_lines(store.root / RECORDS_FILE)
    assert archived + retained == original          # same rows, same bytes, same order


def test_the_active_file_keeps_the_newest_rows(tmp_path):
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 50)
    retention.rotate_file(store, RECORDS_FILE, keep_rows=5, now=NOW)
    kept = [json.loads(l)["n"] for l in _read_lines(store.root / RECORDS_FILE)]
    assert kept == [45, 46, 47, 48, 49]


def test_rotation_is_a_no_op_below_the_limit(tmp_path):
    store = _store(tmp_path)
    original = _fill(store, RECORDS_FILE, 10)
    summary = retention.rotate_file(store, RECORDS_FILE, keep_rows=100, now=NOW)
    assert summary["rotated"] == 0 and summary["archive"] is None
    assert _read_lines(store.root / RECORDS_FILE) == original


def test_an_absent_ledger_is_not_a_failure(tmp_path):
    store = _store(tmp_path)
    store.root.mkdir(parents=True, exist_ok=True)
    assert retention.rotate_file(store, RECORDS_FILE, now=NOW)["rotated"] == 0


def test_a_second_rotation_never_overwrites_the_first_archive(tmp_path):
    """Same second, same filename — the archive must not silently replace evidence."""
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 300)
    first = retention.rotate_file(store, RECORDS_FILE, keep_rows=100, now=NOW)
    _fill(store, RECORDS_FILE, 300)
    second = retention.rotate_file(store, RECORDS_FILE, keep_rows=100, now=NOW)
    assert first["archive"] != second["archive"]
    assert (store.root / first["archive"]).is_file()


# --- the two ledgers that may never be rotated -------------------------------

@pytest.mark.parametrize("filename", [AUDIT_FILE, CONTROL_FILE])
def test_protected_ledgers_are_refused_with_their_own_reason(tmp_path, filename):
    """Not a blanket rule: the audit chain and the kill switch's recovery source are
    excluded for different reasons, and the refusal says which."""
    store = _store(tmp_path)
    _fill(store, filename, 500)
    with pytest.raises(PersistenceError) as exc:
        retention.rotate_file(store, filename, keep_rows=10, now=NOW)
    assert exc.value.reason_code == "LEDGER_PROTECTED_FROM_ROTATION"
    assert filename in exc.value.reason
    # Untouched.
    assert len(_read_lines(store.root / filename)) == 500


def test_rotate_all_leaves_the_protected_ledgers_alone(tmp_path):
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 300)
    _fill(store, AUDIT_FILE, 300)
    _fill(store, CONTROL_FILE, 300)

    summary = retention.rotate_all(store, keep_rows=50, now=NOW)

    assert summary["failures"] == []                      # protected files are not attempted
    assert len(_read_lines(store.root / AUDIT_FILE)) == 300
    assert len(_read_lines(store.root / CONTROL_FILE)) == 300
    assert len(_read_lines(store.root / RECORDS_FILE)) == 50


def test_a_full_rotation_leaves_the_protected_files_byte_identical(tmp_path):
    """Stronger than "not rotated": the bytes must not move at all. The audit chain's
    integrity is a property of its exact contents, so anything less is not a guarantee."""
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 300)
    _fill(store, AUDIT_FILE, 300)
    _fill(store, CONTROL_FILE, 300)
    before = {name: (store.root / name).read_bytes() for name in (AUDIT_FILE, CONTROL_FILE)}

    retention.rotate_all(store, keep_rows=10, now=NOW)

    assert {name: (store.root / name).read_bytes() for name in before} == before


# --- unknown input -----------------------------------------------------------

def test_an_unknown_ledger_is_refused(tmp_path):
    with pytest.raises(PersistenceError) as exc:
        retention.rotate_file(_store(tmp_path), "secrets.jsonl", now=NOW)
    assert exc.value.reason_code == "LEDGER_UNKNOWN_FILE"


@pytest.mark.parametrize("keep", [0, -5, "many", None])
def test_a_nonsensical_keep_is_refused(tmp_path, keep):
    """keep_rows=0 would empty the active file — the one call that looks like deletion."""
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 20)
    with pytest.raises(PersistenceError) as exc:
        retention.rotate_file(store, RECORDS_FILE, keep_rows=keep, now=NOW)
    assert exc.value.reason_code == "LEDGER_INVALID_KEEP"
    assert len(_read_lines(store.root / RECORDS_FILE)) == 20


# --- reporting ---------------------------------------------------------------

def test_rotation_is_recorded_on_the_block_ledger(tmp_path):
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 300)
    retention.rotate_all(store, keep_rows=50, now=NOW)
    events = [b for b in store.read_blocks()
              if b.get("record_type") == retention.RETENTION_EVENT_TYPE]
    assert len(events) == 1
    assert events[0]["rotated_rows"] == 250
    assert events[0]["files"][0]["filename"] == RECORDS_FILE


def test_a_quiet_run_records_nothing(tmp_path):
    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 10)
    retention.rotate_all(store, keep_rows=100, now=NOW)
    assert store.read_blocks() == []


def test_memory_stays_bounded_on_a_large_ledger(tmp_path):
    """Rotation must not repeat the mistake it exists to clean up after: the dashboard
    OOM-killed by loading this same file."""
    import tracemalloc

    def peak_for(rows: int) -> int:
        store = _store(tmp_path / f"n{rows}")
        _fill(store, RECORDS_FILE, rows)
        tracemalloc.start()
        retention.rotate_file(store, RECORDS_FILE, keep_rows=100, now=NOW)
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
        return peak

    small = peak_for(20_000)        # ~1.4MB of lines
    large = peak_for(200_000)       # ~14MB — ten times the file
    # Bounded, not proportional: ten times the ledger must not cost ten times the memory.
    assert large < small * 2, f"peak scaled with the file ({small} -> {large})"
    assert large < 2 * 1024 * 1024, f"peak {large} suggests the ledger is being held in memory"


# --- the CLI -----------------------------------------------------------------

def test_cli_status_names_the_protected_ledgers(tmp_path, capsys):
    from runtime.mvp_runtime import ledger_cli

    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 5)
    assert ledger_cli.main(["status"], store=store) == 0
    out = capsys.readouterr().out
    assert "PROTECTED" in out and AUDIT_FILE in out and CONTROL_FILE in out


def test_cli_rotate_reports_what_moved(tmp_path, capsys):
    from runtime.mvp_runtime import ledger_cli

    store = _store(tmp_path)
    _fill(store, RECORDS_FILE, 300)
    assert ledger_cli.main(["rotate", "--keep", "50"], store=store, now=NOW) == 0
    assert "archived 250 row(s)" in capsys.readouterr().out


def test_cli_refuses_a_protected_ledger(tmp_path, capsys):
    from runtime.mvp_runtime import ledger_cli

    store = _store(tmp_path)
    _fill(store, AUDIT_FILE, 300)
    assert ledger_cli.main(["rotate", "--file", AUDIT_FILE], store=store, now=NOW) != 0
    assert len(_read_lines(store.root / AUDIT_FILE)) == 300


# --- scheduled rotation ------------------------------------------------------
#
# Safe to run unattended for exactly one reason: rotation archives and can delete nothing.
# These pin that the scheduled path inherits every guard rather than reimplementing any —
# including the kill switch, which binds it like every other scheduled execution.

def _rotate_schedule(tmp_path, *, request: str = "", now: str = NOW):
    from runtime.mvp_runtime import scheduler
    store = scheduler.ScheduleStore(tmp_path)
    store.add(scheduler.build_schedule(kind=scheduler.KIND_ROTATE, request=request,
                                       interval_seconds=3600, created_by="test", now=now))
    return store


def _due(now: str) -> str:
    from runtime.mvp_runtime import timeutil
    return timeutil.plus_seconds(now, 3601)


def test_a_scheduled_fire_rotates_and_reports_what_moved(tmp_path):
    from runtime.mvp_runtime import scheduler

    ledger = _store(tmp_path)
    _fill(ledger, RECORDS_FILE, 300)
    schedules = _rotate_schedule(tmp_path)

    summary = scheduler.run_due(schedules, now=_due(NOW), ledger=ledger, repo_root=tmp_path)

    assert summary["fired"] == 1
    assert "rotated=" in summary["results"][0]["status"]
    assert len(_read_lines(ledger.root / RECORDS_FILE)) == retention.DEFAULT_KEEP_ROWS \
        or len(_read_lines(ledger.root / RECORDS_FILE)) == 300      # below the default limit


def test_the_schedule_may_carry_its_own_row_limit(tmp_path):
    from runtime.mvp_runtime import scheduler

    ledger = _store(tmp_path)
    _fill(ledger, RECORDS_FILE, 300)
    schedules = _rotate_schedule(tmp_path, request="50")

    scheduler.run_due(schedules, now=_due(NOW), ledger=ledger, repo_root=tmp_path)

    assert len(_read_lines(ledger.root / RECORDS_FILE)) == 50


def test_an_unparseable_limit_falls_back_to_the_default_never_to_a_smaller_one(tmp_path):
    """Guessing a smaller number would archive more than asked — the lossier direction."""
    from runtime.mvp_runtime import scheduler

    ledger = _store(tmp_path)
    _fill(ledger, RECORDS_FILE, 300)
    schedules = _rotate_schedule(tmp_path, request="every-day-please")

    scheduler.run_due(schedules, now=_due(NOW), ledger=ledger, repo_root=tmp_path)

    # 300 < DEFAULT_KEEP_ROWS, so the default means "nothing to do" — nothing was archived.
    assert len(_read_lines(ledger.root / RECORDS_FILE)) == 300


def test_scheduled_rotation_never_touches_the_protected_ledgers(tmp_path):
    from runtime.mvp_runtime import scheduler

    ledger = _store(tmp_path)
    _fill(ledger, RECORDS_FILE, 300)
    _fill(ledger, AUDIT_FILE, 300)
    _fill(ledger, CONTROL_FILE, 300)
    before = {n: (ledger.root / n).read_bytes() for n in (AUDIT_FILE, CONTROL_FILE)}
    schedules = _rotate_schedule(tmp_path, request="10")

    scheduler.run_due(schedules, now=_due(NOW), ledger=ledger, repo_root=tmp_path)

    assert {n: (ledger.root / n).read_bytes() for n in before} == before


def test_the_kill_switch_stops_a_due_rotation(tmp_path):
    """Bound like every scheduled execution — `kill_blocks: scheduler_execution`."""
    from runtime.mvp_runtime import control, scheduler

    ledger = _store(tmp_path)
    _fill(ledger, RECORDS_FILE, 300)
    control_store = control.ControlStore(tmp_path / "control")
    control_store.save(control.ControlState(mode=control.KILLED, updated_by="tg-1",
                                            updated_at=NOW, reason="test"))
    schedules = _rotate_schedule(tmp_path, request="10")

    summary = scheduler.run_due(schedules, now=_due(NOW), ledger=ledger,
                                control_store=control_store, repo_root=tmp_path)

    assert summary["fired"] == 0 and summary["skipped"] == 1
    assert len(_read_lines(ledger.root / RECORDS_FILE)) == 300


def test_a_fire_without_a_ledger_skips_rather_than_crashing(tmp_path):
    from runtime.mvp_runtime import scheduler

    schedules = _rotate_schedule(tmp_path)
    summary = scheduler.run_due(schedules, now=_due(NOW), ledger=None, repo_root=tmp_path)
    assert summary["results"][0]["status"] == "skipped_no_ledger"


# --- readers follow rotation -------------------------------------------------
#
# Nothing is destroyed, and until now that was where the property stopped. A reader that
# stops at the active file makes an archived row invisible all the same, and the evidence
# is still on disk to prove it should not have been — which is the shape this section
# tests: not "was it kept" but "can the reader that needs it still see it".

def _proposal_line(family: str, *, created_at: str) -> str:
    from runtime.mvp_runtime.crypto import proposer

    return json.dumps({
        "kind": proposer.PROPOSAL_LEDGER_KIND, "trace_id": "t",
        "record": {"record_type": proposer.PROPOSAL_RECORD_TYPE, "created_at": created_at,
                   "proposals": [{"family": family, "accepted": True}]},
    }, sort_keys=True)


def _write_records(store: LedgerStore, lines: list[str]) -> None:
    """Append rows to the active record ledger, as a run would."""
    path = store.root / RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def test_the_archive_reader_sees_what_rotation_moved_and_iter_records_does_not(tmp_path):
    """The active file's horizon is a ROW COUNT; a window is a SPAN. Rotation decided which."""
    ledger = _store(tmp_path)
    _write_records(ledger, [_proposal_line(f"fam_{i}", created_at="2026-07-30T00:00:00Z")
                            for i in range(6)])
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=2, now="2026-08-06T00:00:00Z")

    assert len(list(ledger.iter_records())) == 2
    everything = list(ledger.iter_records_with_archive())
    assert len(everything) == 6
    # Oldest first, archives ahead of the active file — the append order rotation split up.
    assert [r["record"]["proposals"][0]["family"] for r in everything] == [
        f"fam_{i}" for i in range(6)]


def test_the_bound_skips_only_archives_wholly_before_it(tmp_path):
    """`appended_since` bounds which FILES are opened, never which rows are yielded — the
    archive grows forever and a daily reader must not grow with it."""
    ledger = _store(tmp_path)
    _write_records(ledger, [_proposal_line(f"old_{i}", created_at="2026-06-01T00:00:00Z")
                            for i in range(4)])
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=1, now="2026-06-02T00:00:00Z")
    _write_records(ledger, [_proposal_line(f"new_{i}", created_at="2026-07-30T00:00:00Z")
                            for i in range(4)])
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=1, now="2026-08-06T00:00:00Z")

    families = [r["record"]["proposals"][0]["family"]
                for r in ledger.iter_records_with_archive(appended_since="2026-07-09T00:00:00Z")]
    # old_0..old_2 went into the JUNE archive, stamped before the bound — never opened.
    assert not {"old_0", "old_1", "old_2"} & set(families), "the June archive was opened"
    # old_3 survived that rotation and was still in the active file when the AUGUST one
    # archived it, so it arrives in a file the bound must open. That is the contract: the
    # bound drops whole FILES that cannot hold an in-window row, and the caller's own
    # window is what refuses this row.
    assert sorted(families) == ["new_0", "new_1", "new_2", "new_3", "old_3"]
    # And with no bound the same call still reaches everything ever written.
    assert len(list(ledger.iter_records_with_archive())) == 8


def test_an_unreadable_archive_name_is_read_rather_than_skipped(tmp_path):
    """This method exists because a count under-reported, so the uncertain case must ADD
    rows. A name that does not parse as a rotation stamp is a name we cannot date, not a
    file we may drop."""
    ledger = _store(tmp_path)
    _write_records(ledger, [_proposal_line("kept", created_at="2026-07-30T00:00:00Z")])
    archive = ledger.root / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "records.hand-copied.jsonl").write_text(
        _proposal_line("undated", created_at="2026-07-30T00:00:00Z") + "\n", encoding="utf-8")

    families = {r["record"]["proposals"][0]["family"]
                for r in ledger.iter_records_with_archive(appended_since="2026-07-09T00:00:00Z")}
    assert families == {"undated", "kept"}


def test_the_proposer_backlog_counts_across_a_rotation(tmp_path):
    """The live defect, end to end.

    Measured 2026-08-08: `BACKLOG_WINDOW_DAYS = 30` and the proposer could see nine days,
    because four in-window proposal records had rotated out of the active file. The backlog
    it throttles on fell 15 -> 11 between 08-05 and 08-06 for that reason alone — a drop that
    reads as families being installed and was the ledger being tidied.

    The error direction is the bad one for a throttle: fewer counted, cap not reached, fires
    proceed that the design meant to skip.
    """
    from runtime.mvp_runtime import timeutil
    from runtime.mvp_runtime.crypto import proposer

    now = "2026-08-08T06:00:00Z"
    ledger = _store(tmp_path)
    _write_records(ledger, [_proposal_line(f"fam_{i}", created_at="2026-07-30T00:00:00Z")
                            for i in range(6)])
    retention.rotate_file(ledger, RECORDS_FILE, keep_rows=2, now="2026-08-06T00:00:00Z")
    window_start = timeutil.plus_minutes(now, -proposer.BACKLOG_WINDOW_DAYS * 24 * 60)

    # Every one of the six is inside the 30-day window; only two survived rotation.
    assert proposer.count_unreviewed_backlog(ledger.iter_records(), [], now=now) == 2
    assert proposer.count_unreviewed_backlog(
        ledger.iter_records_with_archive(appended_since=window_start), [], now=now) == 6
