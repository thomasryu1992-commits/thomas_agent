"""H6d-min — ledger idempotency and ledger/state concurrency (Thomas 2026-10-09, scenarios 10-20).

Exactly once comes from the key check under the ledger lock, not from append-only alone. Writers take the
state lock, then the ledger lock, re-read both and patch only what they own. A missing state file is
rebuilt from the ledger; a broken state file or ledger refuses; a ledger/state mismatch never passes.
The concurrent cases run in real processes (fork). Synthetic fixtures only.
"""

from __future__ import annotations

import json
import multiprocessing
import os

import pytest

from runtime.mvp_runtime import filelock
from runtime.mvp_runtime.errors import PersistenceError, ToolError
from runtime.mvp_runtime.holdings import cash_flows, chained_log
from tests._h6d_support import (CURRENT_OK, Feed, collect, events, ledger, order, pay, posix_only, ready_fire, resolve,
                                set_cutover, toss_fire)

T1, T2, T3, T4 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z", "2026-10-09T06:00:00Z", "2026-10-09T07:00:00Z"


def _fork():
    return multiprocessing.get_context("fork")


def _run(target, *args):
    process = _fork().Process(target=target, args=args)
    process.start()
    return process


def _join(*processes, timeout=30):
    for process in processes:
        process.join(timeout)
        assert not process.is_alive(), "a writer hung"
    return [process.exitcode for process in processes]


# 10
def test_append_unique_writes_a_key_once_and_append_is_unchanged(tmp_path):
    path = tmp_path / "log.jsonl"
    kw = {"record_type": "t.v1", "tamper_code": "T", "lock_code": "L", "label": "t"}
    assert len(chained_log.append_unique(path, build=lambda _e: [{"source_event_key": "a"}, {"source_event_key": "a"}],
                                         **kw)) == 1                                   # a batch duplicate
    assert chained_log.append_unique(path, build=lambda _e: [{"source_event_key": "a"}], **kw) == []
    with pytest.raises(ToolError):
        chained_log.append_unique(path, build=lambda _e: [{"other": 1}], **kw)          # no key, no line
    chained_log.append(path, build=lambda _p: {"source_event_key": "a"}, **kw)          # append keeps its contract
    assert [r["source_event_key"] for r in chained_log.verify(path, record_type="t.v1", tamper_code="T")] == ["a", "a"]


def _resolve_in_child(state_dir, barrier, results):
    barrier.wait()
    try:
        resolve(state_dir, "pay:p1", "deposit")
        results.put("ok")
    except ToolError as exc:
        results.put(exc.reason_code)


# 11
@posix_only
def test_two_processes_resolving_one_event_write_one_line(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    ctx = _fork()
    barrier, results = ctx.Barrier(2), ctx.Queue()
    _join(_run(_resolve_in_child, tmp_path, barrier, results), _run(_resolve_in_child, tmp_path, barrier, results))
    assert sorted([results.get(timeout=5), results.get(timeout=5)]) == sorted(["ok", cash_flows.RESOLVE_DUPLICATE])
    assert len(events(tmp_path, "flow_resolved")) == 1


class _PausingFeed(Feed):
    """Pauses inside the network read — outside every lock — until the other process is done."""

    def __init__(self, reading, proceed, **answers):
        super().__init__(**answers)
        self.reading, self.proceed = reading, proceed

    def flow_history(self, source, **kw):
        if not self.reading.is_set():
            self.reading.set()
            assert self.proceed.wait(20)
        return super().flow_history(source, **kw)


def _refresh_in_child(state_dir, reading, proceed):
    collect(state_dir, _PausingFeed(reading, proceed, pay=[pay("p1"), pay("p2", "6", at="2026-10-09T04:30:00Z")]),
            now=T2)


def _resolve_and_tell_in_child(state_dir, reading, proceed):
    assert reading.wait(20)
    told = cash_flows.update_readiness(state_dir, now=T1)            # p1 is told, then closed
    cash_flows.mark_exceptions_told(state_dir, token=told["exceptions_token"], now=T1)
    resolve(state_dir, "pay:p1", "deposit")
    proceed.set()


# 12 and 15
@posix_only
def test_a_refresh_and_a_resolve_at_once_lose_neither_and_a_stale_read_overwrites_nothing(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    owed = cash_flows.update_readiness(tmp_path, now=T1)
    assert owed["exceptions_untold"]
    ctx = _fork()
    reading, proceed = ctx.Event(), ctx.Event()
    codes = _join(_run(_refresh_in_child, tmp_path, reading, proceed),
                  _run(_resolve_and_tell_in_child, tmp_path, reading, proceed))
    assert codes == [0, 0]
    rows = ledger(tmp_path)
    assert [r["resolves"] for r in rows if r["event"] == "flow_resolved"] == ["pay:p1"]
    assert {r["source_event_key"] for r in rows if r["event"] == "flow_recorded"} == {"pay:p1", "pay:p2"}
    state = cash_flows.load_state(tmp_path)
    # The refresh read its state before the resolve and saved after it: the told set survived.
    assert state["exceptions"]["told_keys"] and state["last_binance_pass"]["at"] == T2
    after = cash_flows.update_readiness(tmp_path, now=T2)
    assert after["exceptions"] == 1 and after["exceptions_new"] == 1                   # p2 only: p1 was told
    assert cash_flows.readiness(tmp_path, now=T2)["checks"]["ledger_state_consistency"] == "PASS"


def _crash_after_append(state_dir, call):
    def crash(*_a, **_k):
        os._exit(3)
    cash_flows._save_state = crash
    call(state_dir)


# 13 and 14 (Binance)
@posix_only
def test_a_crash_after_the_append_and_a_restart_record_each_event_once(tmp_path):
    set_cutover(tmp_path)
    feed = Feed(pay=[pay("p1")])
    assert _join(_run(_crash_after_append, tmp_path, lambda d: collect(d, feed, now=T1))) == [3]
    assert [r["source_event_key"] for r in events(tmp_path, "flow_recorded")] == ["pay:p1"]
    assert "last_binance_pass" not in cash_flows.load_state(tmp_path)                  # the save never happened
    assert collect(tmp_path, feed, now=T2)["written"] == 0                             # the restart re-reads it
    keys = [r["source_event_key"] for r in ledger(tmp_path)]
    assert len(keys) == len(set(keys)) and keys.count("pay:p1") == 1


# 13 and 14 (Toss)
@posix_only
def test_a_crash_after_a_toss_window_is_never_counted_twice(tmp_path):
    cash_flows.start_semantics_epoch(tmp_path, requested_by="t", reason="r", change_ref="#1203",
                                     confirm=cash_flows.reconciliation_digest()[:8], interactive=True, now=T1)
    toss_fire(tmp_path, 1_000_000, T1)
    buy = order("b1", "BUY", "100000", "2026-10-09T04:30:00Z")
    assert _join(_run(_crash_after_append, tmp_path, lambda d: toss_fire(d, 900_000, T2, [buy]))) == [3]
    assert len(events(tmp_path, "toss_window_observed")) == 1
    toss_fire(tmp_path, 900_000, T3, [buy])            # the cache still says T1: the ledger has a later window
    toss_fire(tmp_path, 900_000, T4, [buy])
    windows = events(tmp_path, "toss_window_observed")
    assert [(w["window_start"], w["window_end"]) for w in windows] == [(T1, T2), (T3, T4)]
    assert cash_flows.toss_evidence(ledger(tmp_path), epoch=1, today_kst="2026-10-10")["buy_explained"] == 1


# 13 (the cache behind the ledger never passes)
@posix_only
def test_a_ledger_and_state_that_disagree_never_pass(tmp_path):
    assert _join(_run(_crash_after_append, tmp_path, lambda d: set_cutover(d))) == [3]
    assert events(tmp_path, "cutover_set") and not cash_flows.load_state(tmp_path).get("cutover_at")
    ready = cash_flows.readiness(tmp_path, now=T1, current=CURRENT_OK)
    assert ready["checks"]["ledger_state_consistency"] == "FAIL" and ready["ready"] is False
    collect(tmp_path, now=T1)                                                       # the next write repairs it
    assert cash_flows.readiness(tmp_path, now=T1, current=CURRENT_OK)["checks"]["ledger_state_consistency"] == "PASS"


# 15 (Toss: a stale cash point is not committed over a newer one)
def test_a_toss_commit_from_a_stale_cash_point_writes_nothing(tmp_path, monkeypatch):
    toss_fire(tmp_path, 1_000_000, T1)
    real = cash_flows.load_state
    calls = {"n": 0}

    def stale_then_real(state_dir):
        calls["n"] += 1
        body = real(state_dir)
        if calls["n"] == 1:      # the read phase sees an older cash point than the one on file
            body["toss"] = {**body["toss"], "at": "2026-10-09T03:30:00Z"}
        return body

    monkeypatch.setattr(cash_flows, "load_state", stale_then_real)
    result = toss_fire(tmp_path, 900_000, T2)
    assert result["written"] == 0 and "moved" in result["skipped"]
    monkeypatch.setattr(cash_flows, "load_state", real)
    assert real(tmp_path)["toss"]["at"] == T1 and events(tmp_path, "toss_window_observed") == []


# 16
def test_a_broken_ledger_refuses_every_writer_and_fails_the_readiness(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    path = cash_flows.ledger_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[-1])
    row["amount"] = "9999"
    path.write_text("\n".join([*lines[:-1], json.dumps(row)]) + "\n", encoding="utf-8")
    damaged = path.read_bytes()
    for call in (lambda: collect(tmp_path, Feed(pay=[pay("p2")]), now=T2),
                 lambda: resolve(tmp_path, "pay:p1", "deposit"),
                 lambda: toss_fire(tmp_path, 1, T2)):
        with pytest.raises(ToolError) as exc:
            call()
        assert exc.value.reason_code == cash_flows.LEDGER_TAMPERED
    assert path.read_bytes() == damaged
    ready = cash_flows.readiness(tmp_path, now=T2, current=CURRENT_OK)
    assert ready["checks"]["ledger_chain"] == "FAIL" and ready["ready"] is False


# 17
def test_a_broken_state_file_refuses_and_fails_the_readiness(tmp_path):
    set_cutover(tmp_path)
    cash_flows.state_path(tmp_path).write_text("{", encoding="utf-8")
    with pytest.raises(ToolError):
        collect(tmp_path, now=T1)
    with pytest.raises(ToolError):
        cash_flows.update_readiness(tmp_path, now=T1)
    ready = cash_flows.readiness(tmp_path, now=T1, current=CURRENT_OK)
    assert ready["checks"]["ledger_state_consistency"] == "FAIL" and ready["ready"] is False


# 18
def test_a_missing_state_file_is_rebuilt_from_the_ledger(tmp_path):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    cash_flows.mark_fire_verified(tmp_path, now=T1)
    told = cash_flows.update_readiness(tmp_path, now=T1)
    cash_flows.mark_exceptions_told(tmp_path, token=told["exceptions_token"], now=T1)
    resolve(tmp_path, "pay:p1", "deposit")
    cash_flows.state_path(tmp_path).unlink()
    ready = ready_fire(tmp_path, T2)
    assert cash_flows.load_state(tmp_path)["cutover_at"] == cash_flows.cutover_at(ledger(tmp_path))
    assert ready["checks"]["ledger_state_consistency"] == "PASS" and ready["checks"]["cutover_boundary"] == "PASS"
    assert ready["exceptions"] == 0                                                  # the resolution is in the ledger
    assert cash_flows.shadow_record(ledger(tmp_path)) == (T1, ["2026-10-09"])


def _recording(monkeypatch):
    held: list[str] = []
    order_seen: list[tuple[str, tuple[str, ...]]] = []
    real = filelock.locked

    def wrap(path, **kw):
        from contextlib import contextmanager

        @contextmanager
        def manager():
            order_seen.append((path.name, tuple(held)))
            with real(path, **kw):
                held.append(path.name)
                try:
                    yield
                finally:
                    held.remove(path.name)
        return manager()

    monkeypatch.setattr(cash_flows, "locked", wrap)
    monkeypatch.setattr(chained_log, "locked", wrap)
    return order_seen


# 19
def test_every_writer_takes_the_state_lock_before_the_ledger_lock(tmp_path, monkeypatch):
    seen = _recording(monkeypatch)
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    toss_fire(tmp_path, 1_000_000, T1)
    toss_fire(tmp_path, 1_000_000, T2)
    resolve(tmp_path, "pay:p1", "deposit")
    cash_flows.mark_fire_verified(tmp_path, now=T2)
    told = cash_flows.update_readiness(tmp_path, now=T2)
    cash_flows.mark_exceptions_told(tmp_path, token=told["exceptions_token"], now=T2)
    cash_flows.mark_readiness_told(tmp_path, now=T2)
    state, ledger_lock = "holdings_cash_flow_state.lock", "holdings_cash_flows.lock"
    ledger_takes = [held for name, held in seen if name == ledger_lock]
    assert ledger_takes and all(state in held for held in ledger_takes)          # always inside the state lock
    assert all(ledger_lock not in held for name, held in seen if name == state)   # never the reverse


def _hold(path, ready, release):
    with filelock.locked(path, code="HOLD", label="holder"):
        ready.set()
        release.wait(20)


# 20
@posix_only
def test_a_held_lock_turns_into_a_bounded_refusal_not_a_hang(tmp_path, monkeypatch):
    set_cutover(tmp_path)
    collect(tmp_path, Feed(pay=[pay("p1")]), now=T1)
    monkeypatch.setattr(filelock, "ACQUIRE_DEADLINE_SECONDS", 0.3)
    ctx = _fork()
    for lock, code, call in ((cash_flows.ledger_path(tmp_path).with_suffix(".lock"), cash_flows.LEDGER_LOCKED,
                              lambda: resolve(tmp_path, "pay:p1", "deposit")),
                             (cash_flows.state_path(tmp_path).with_suffix(".lock"), cash_flows.STATE_LOCKED,
                              lambda: collect(tmp_path, now=T2))):
        ready, release = ctx.Event(), ctx.Event()
        holder = _run(_hold, lock, ready, release)
        assert ready.wait(10)
        with pytest.raises(PersistenceError) as exc:
            call()
        assert exc.value.reason_code == code
        release.set()
        _join(holder)
    resolve(tmp_path, "pay:p1", "deposit")                                         # released: it goes through
    assert len(events(tmp_path, "flow_resolved")) == 1


# --- the pre-H6d import refuses a cache that does not say what H6b meant (found 2026-10-09) -------------

H6B_FIELDS = {"toss_evidence": {"buy_explained": 1, "sell_explained": 2, "activity_unexplained": 0,
                                "settlement_days": {"2026-10-10": {"windows": 3, "unexplained": 1}}},
              "shadow_success_dates": ["2026-10-10", "2026-10-09"], "first_verified_fire_at": "2026-10-09T04:00:00Z"}


def _h6b_state(tmp_path, **changes):
    """A state file as the H6b code (#1196) left it: the cutover in the ledger, the evidence in the cache."""
    set_cutover(tmp_path)
    body = json.loads(cash_flows.state_path(tmp_path).read_text(encoding="utf-8"))
    body.update(json.loads(json.dumps(H6B_FIELDS)))
    for name, value in changes.items():
        if name.startswith("toss_evidence."):
            body["toss_evidence"][name.split(".", 1)[1]] = value
        else:
            body[name] = value
    cash_flows.state_path(tmp_path).write_text(json.dumps(body), encoding="utf-8")


def _files(tmp_path):
    return (cash_flows.ledger_path(tmp_path).read_bytes(), cash_flows.state_path(tmp_path).read_bytes())


# H3-1, H3-5 (a well-formed import happens once)
def test_a_well_formed_pre_h6d_cache_is_imported_once_and_left_behind(tmp_path):
    _h6b_state(tmp_path)
    collect(tmp_path, now=T1)
    collect(tmp_path, now=T2)                                                         # a second writer
    rows = events(tmp_path, cash_flows.EVENT_LEGACY)
    assert len(rows) == 1
    assert rows[0]["toss_evidence"] == H6B_FIELDS["toss_evidence"]
    assert rows[0]["shadow_success_dates"] == ["2026-10-09", "2026-10-10"]
    assert rows[0]["first_verified_fire_at"] == "2026-10-09T04:00:00Z"
    state = cash_flows.load_state(tmp_path)
    assert not {"toss_evidence", "shadow_success_dates", "first_verified_fire_at"} & set(state)
    ready = cash_flows.readiness(tmp_path, now=T2, current=CURRENT_OK)
    assert ready["checks"]["ledger_state_consistency"] == "PASS"


MALFORMED = [
    ("count not a number", {"toss_evidence.buy_explained": "lots"}),                  # H3-2
    ("count negative", {"toss_evidence.sell_explained": -1}),
    ("count a boolean", {"toss_evidence.activity_unexplained": True}),
    ("count a fraction", {"toss_evidence.buy_explained": 1.5}),
    ("evidence not an object", {"toss_evidence": ["buy", 1]}),
    ("settlement days a list", {"toss_evidence.settlement_days": [["2026-10-10", 3]]}),  # H3-3
    ("settlement day not a date", {"toss_evidence.settlement_days": {"yesterday": {"windows": 1}}}),
    ("settlement row not an object", {"toss_evidence.settlement_days": {"2026-10-10": 3}}),
    ("settlement count not a number", {"toss_evidence.settlement_days": {"2026-10-10": {"windows": "x"}}}),
    ("shadow dates a string", {"shadow_success_dates": "2026-10-09"}),
    ("shadow date not a date", {"shadow_success_dates": ["2026-10-09", "monday"]}),
    ("first fire not a time", {"first_verified_fire_at": "yesterday"}),
]


# H3-2, H3-3, H3-4, H3-5
@pytest.mark.parametrize("case, changes", MALFORMED, ids=[c[0] for c in MALFORMED])
def test_a_malformed_pre_h6d_cache_is_refused_with_its_code_and_leaves_both_files(tmp_path, case, changes):
    _h6b_state(tmp_path, **changes)
    before = _files(tmp_path)
    for at in (T1, T2):                                                               # a retry refuses again
        with pytest.raises(ToolError) as exc:
            collect(tmp_path, now=at)
        assert exc.value.reason_code == cash_flows.LEDGER_TAMPERED
        assert _files(tmp_path) == before                                             # no partial import
    assert events(tmp_path, cash_flows.EVENT_LEGACY) == []
    ready = cash_flows.readiness(tmp_path, now=T2, current=CURRENT_OK)
    assert ready["checks"]["ledger_state_consistency"] == "FAIL" and ready["ready"] is False


def test_a_repaired_cache_then_imports_once(tmp_path):
    _h6b_state(tmp_path, **{"toss_evidence.buy_explained": "lots"})
    with pytest.raises(ToolError):
        collect(tmp_path, now=T1)
    body = json.loads(cash_flows.state_path(tmp_path).read_text(encoding="utf-8"))
    body["toss_evidence"]["buy_explained"] = 1                                        # the operator's repair
    cash_flows.state_path(tmp_path).write_text(json.dumps(body), encoding="utf-8")
    collect(tmp_path, now=T2)
    collect(tmp_path, now=T3)
    assert len(events(tmp_path, cash_flows.EVENT_LEGACY)) == 1
