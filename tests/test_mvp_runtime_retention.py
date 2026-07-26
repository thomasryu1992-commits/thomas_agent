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
