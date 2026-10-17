"""C7: what a restore that mixes points in time does to the H6d-min ledger and its state (synthetic only).

The ledger is the authority and the state file a cache with the Binance read cursors in it. A ledger put
back from an older backup while the state keeps its newer cursors loses every flow between the two that
lies outside the one-day re-read: the next read starts after it, the exception it carried disappears,
and the readiness still says PASS. The fix is a design (``docs/proposals/HOLDINGS_LEDGER_CHECKPOINT_V0.1.md``)
built in steps. Each test here is one case of that design's table (C7-1 … C7-11):

- the cases the code already handles are asserted as they must stay;
- the cases the checkpoint catches — C7-4 and C7-6 — assert PR-C's refusal (§9's final expectations): the
  write raises LEDGER_TAMPERED and leaves both files byte for byte, the readiness FAILs, PR-D's alert says
  the writes stopped, and only Thomas's two-step adoption reopens them. What an adoption then recovers is
  what the cursors re-read; under option C nothing else is re-read (§12.5);
- the cases PR-B (history completeness, option C) now reads — C7-8 and C7-11 — assert that the history is
  UNCHECKED and the readiness not ready, though ``ledger_state_consistency`` keeps its meaning and reads PASS.
  Nothing is recovered: under option C there is no re-read; the operator moves the cutover (§12.4).
"""

from __future__ import annotations

import json
import shutil

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows
from tests._h6d_support import CURRENT_OK, Feed, iso, ledger, ms, pay, set_cutover

T1, T2 = "2026-10-09T04:00:00Z", "2026-10-09T05:00:00Z"
P2 = pay("p2", at="2026-10-09T04:30:00Z")             # the flow written after the backup point
DAY = 86_400_000


class VenueFeed(Feed):
    """Answers only the rows inside the asked window, as the venue does; ``Feed`` ignores the window."""

    def flow_history(self, source, *, start_ms, end_ms, timeout_seconds=5):
        rows = super().flow_history(source, start_ms=start_ms, end_ms=end_ms)
        return [r for r in rows if start_ms <= int(r.get("transactionTime") or r.get("insertTime") or 0) <= end_ms]


def fire(d, now, *pays):
    return cash_flows.collect_binance(VenueFeed(pay=list(pays)), d, now=now, now_ms=ms(now))


def later(at, days=0, hours=0):
    return iso(ms(at) + days * DAY + hours * 3_600_000)


def checks(d, now):
    ready = cash_flows.readiness(d, now=now, current=CURRENT_OK)
    return ready["checks"]["ledger_chain"], ready["checks"]["ledger_state_consistency"], ready["exceptions"]


def keys(d):
    return [r.get("source_event_key") for r in ledger(d)]


def backup_point(d, tmp_path, name="backup"):
    """A copy of the whole holdings directory, as one archive would hold it."""
    target = tmp_path / name
    shutil.copytree(d, target)
    return target


def restore_ledger_only(d, backup):
    shutil.copyfile(cash_flows.ledger_path(backup), cash_flows.ledger_path(d))


def restore_state_only(d, backup):
    shutil.copyfile(cash_flows.state_path(backup), cash_flows.state_path(d))


@pytest.fixture
def live(tmp_path):
    """The cutover, one clean fire (the backup point is taken after it), then P2 written at T2."""
    d = tmp_path / "live"
    set_cutover(d)
    fire(d, T1)
    cash_flows.mark_fire_verified(d, now=T1)
    return d


def files(d):
    return {p.name: p.read_bytes() for p in sorted(d.iterdir()) if p.is_file() and not p.name.endswith(".lock")}


def assert_refused(d, now, reason, *pays):
    """PR-C: the mixed restore refuses the write — nothing written, consistency FAIL, the alert says so."""
    before = files(d)
    with pytest.raises(ToolError) as refused:
        fire(d, now, *pays)
    assert refused.value.reason_code == cash_flows.LEDGER_TAMPERED and reason in str(refused.value)
    assert files(d) == before                                                       # both files, byte for byte
    chain, consistency, _ = checks(d, now)
    assert (chain, consistency) == ("PASS", "FAIL")
    assert cash_flows.DETECTION_MARKER_KEY not in cash_flows.load_state(d)          # PR-C writes no marker
    judged = cash_flows.blocked_state(d)
    assert (judged["blocked"], judged["category"], judged["reason_code"]) == (True, "checkpoint",
                                                                               cash_flows.LEDGER_TAMPERED)
    text = cash_flows.blocked_alert(d, now=now)["alert"][1]
    assert cash_flows.LEDGER_TAMPERED in text and "거부" in text and "쓰기는 계속" not in text


def adopt(d, now):
    shown = cash_flows.adoption_preview(d, interactive=True, requested_by="thomas", reason="restored ledger")
    return cash_flows.adopt_ledger(d, confirm=shown["confirm"], requested_by="thomas", reason="restored ledger",
                                   interactive=True, now=now)


def assert_whole(d, now, exceptions):
    chain, consistency, open_ = checks(d, now)
    assert (chain, consistency, open_) == ("PASS", "PASS", exceptions)
    assert len(keys(d)) == len(set(keys(d)))                                       # no duplicate event


# C7-1: ledger and state from the same moment.
def test_c7_1_a_normal_pair_stays_whole(live):
    fire(live, T2, P2)
    fire(live, later(T2, hours=1), P2)                                             # the overlap re-reads P2
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, later(T2, hours=1), 1)


# C7-2: the append landed, the process stopped before the state was saved.
def test_c7_2_a_stop_between_the_append_and_the_save_recovers(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    restore_state_only(live, before)                                               # the save never happened
    assert_whole(live, T2, 1)
    fire(live, later(T2, hours=1), P2)
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, later(T2, hours=1), 1)


# C7-3: the ledger grew over several fires while the state stayed at an older save.
def test_c7_3_a_state_behind_a_longer_ledger_recovers(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2, pay("p3", at=later(T2, days=3, hours=-1)))
    restore_state_only(live, before)                                               # an older cache, the ledger kept
    now = later(T2, days=3, hours=1)
    fire(live, now, P2, pay("p3", at=later(T2, days=3, hours=-1)))
    assert keys(live).count("pay:p2") == 1 and keys(live).count("pay:p3") == 1
    assert_whole(live, now, 2)


# C7-4: only the ledger is put back from an older backup; the state keeps its newer cursors.
def test_c7_4_a_ledger_restored_within_the_overlap_is_refused_then_adopted_and_the_re_read_finds_the_flow(live,
                                                                                                         tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    restore_ledger_only(live, before)
    now = later(T2, hours=1)
    # D1 (Thomas 2026-10-11): a shorter ledger has no cause but a restore, so this too is refused, at the
    # price of one operator adoption.
    assert_refused(live, now, cash_flows.CHECKPOINT_SHORTER, P2)
    adopt(live, later(T2, hours=2))
    fire(live, later(T2, hours=3), P2)                                             # the one-day re-read finds it
    assert keys(live).count("pay:p2") == 1
    assert checks(live, later(T2, hours=3))[:2] == ("PASS", "PASS")


def test_c7_4_a_ledger_restored_past_the_overlap_is_refused_and_an_adoption_does_not_bring_the_flow_back(live,
                                                                                                         tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)                                              # the cursor moves 3 days on
    assert checks(live, later(T2, days=3))[2] == 1
    restore_ledger_only(live, before)
    now = later(T2, days=3, hours=1)
    assert_refused(live, now, cash_flows.CHECKPOINT_SHORTER, P2)
    p4 = pay("p4", at=later(T2, days=3, hours=1))
    assert_refused(live, later(T2, days=3, hours=2), cash_flows.CHECKPOINT_SHORTER, P2, p4)   # nothing laid on it
    adopt(live, later(T2, days=3, hours=3))
    fire(live, later(T2, days=3, hours=4), P2, p4)
    # Option C re-reads nothing: P2 lies outside the cursors' overlap and stays gone; the writes resume.
    assert "pay:p2" not in keys(live) and "pay:p4" in keys(live)
    ready = cash_flows.readiness(live, now=later(T2, days=3, hours=4), current=CURRENT_OK)
    assert ready["checks"]["history_completeness"] == "UNCHECKED"


# C7-5: ledger and state put back together, from the same archive.
def test_c7_5_a_restore_of_both_from_one_point_recovers(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)
    for path in (cash_flows.ledger_path, cash_flows.state_path):
        shutil.copyfile(path(before), path(live))
    now = later(T2, days=3, hours=1)
    fire(live, now, P2)                                                            # the old cursor re-reads 3 days
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, now, 1)


# C7-6: two restores mixed — the same number of lines, a different last line, a chain that verifies.
def test_c7_6_a_ledger_of_another_history_with_the_same_length_is_refused(live, tmp_path):
    other = backup_point(live, tmp_path, "other")
    fire(live, T2, P2)
    fire(other, T2, pay("p3", at="2026-10-09T04:40:00Z"))                         # a history the venue never had
    assert len(ledger(live)) == len(ledger(other))
    assert ledger(live)[-1]["sha256"] != ledger(other)[-1]["sha256"]
    fire(live, later(T2, days=3), P2)
    restore_ledger_only(live, other)
    now = later(T2, days=3, hours=1)
    # The line at the state's checkpoint is not the line the state recorded. Refused; nothing repaired.
    assert_refused(live, now, cash_flows.CHECKPOINT_REPLACED, P2)
    adopt(live, later(T2, days=3, hours=2))
    fire(live, later(T2, days=3, hours=3), P2)
    # §9: after the adoption P3 stays in the ledger (an adoption erases nothing) and P2 is not re-read.
    assert "pay:p2" not in keys(live) and "pay:p3" in keys(live)


# C7-7: a cursor ahead of the ledger's newest event is a quiet day, not a loss. A detector built on the
# cursor would refuse this; the checkpoint compares the ledger to itself and lets it pass.
def test_c7_7_a_cursor_ahead_of_the_last_event_is_a_quiet_day(live):
    fire(live, T2, P2)
    for day in (1, 2, 3):
        fire(live, later(T2, days=day), P2)
    state = cash_flows.load_state(live)
    newest = max(int(r.get("event_ms") or 0) for r in ledger(live))
    assert state["sources"]["pay"]["cursor_ms"] > newest + 2 * DAY
    assert_whole(live, later(T2, days=3), 1)


def strip_checkpoint(d):
    """A state saved before PR-A — every H6d state on file when PR-A ships — has no checkpoint."""
    state = cash_flows.load_state(d)
    state.pop(cash_flows.CHECKPOINT_KEY, None)
    cash_flows.state_path(d).write_text(json.dumps(state), encoding="utf-8")


# C7-8: a state with no checkpoint; nothing relates its cursors to the ledger.
def test_c7_8_a_state_without_a_checkpoint_reads_its_history_unchecked(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)
    restore_ledger_only(live, before)
    strip_checkpoint(live)
    state = cash_flows.load_state(live)
    assert not {"ledger_checkpoint", "checkpoint"} & set(state)
    # PR-B (D2: absent reads UNCHECKED): the consistency check keeps its meaning and passes, but the history
    # since the cutover is UNCHECKED, so the readiness is not ready.
    now = later(T2, days=3, hours=1)
    assert checks(live, now)[:2] == ("PASS", "PASS")
    ready = cash_flows.readiness(live, now=now, current=CURRENT_OK)
    assert (ready["checkpoint"], ready["checkpoint_history"]) == ("absent", "UNVERIFIED")
    assert ready["checks"]["history_completeness"] == "UNCHECKED" and ready["history"]["reason"] == "no_checkpoint"
    assert ready["ready"] is False


# C7-9: a ledger whose chain is broken is refused, before and after a write; nothing is written.
def test_c7_9_a_broken_chain_is_refused_and_left_as_it_is(live):
    fire(live, T2, P2)
    path = cash_flows.ledger_path(live)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["edited"] = True                                                           # a middle line, hash kept
    lines[1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    files = (path.read_bytes(), cash_flows.state_path(live).read_bytes())
    with pytest.raises(ToolError) as exc:
        fire(live, later(T2, hours=1), P2)
    assert exc.value.reason_code == cash_flows.LEDGER_TAMPERED
    assert (path.read_bytes(), cash_flows.state_path(live).read_bytes()) == files
    chain, consistency, _ = checks(live, later(T2, hours=1))
    assert (chain, consistency) == ("FAIL", "FAIL")


# C7-10: re-reading after any restore appends nothing twice.
@pytest.mark.parametrize("restore", ["state_only", "both"])
def test_c7_10_a_re_read_after_a_restore_appends_nothing_twice(live, tmp_path, restore):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    if restore == "state_only":
        restore_state_only(live, before)
    else:
        for path in (cash_flows.ledger_path, cash_flows.state_path):
            shutil.copyfile(path(before), path(live))
    for hours in (1, 2, 3):
        fire(live, later(T2, hours=hours), P2)
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, later(T2, hours=3), 1)


# C7-D: a refused write today reaches no one. The exception alert is computed after ``summary``, which
# verifies the ledger first; with the chain broken the whole block raises, and the board keeps only the
# exception's class name. The design (§5) pairs every refusal with its own message; this pins the gap.
def test_c7_d_a_refused_write_is_told_once_by_the_blocked_check(monkeypatch, tmp_path):
    from runtime.mvp_runtime.holdings import store
    from tests.test_mvp_runtime_holdings_cash_flows import _store_fire

    d = store.state_dir(tmp_path)
    cash_flows.set_cutover(d, at="2026-10-09T02:00:00Z", requested_by="test")
    feed = Feed(pay=[pay("p1", at="2026-10-09T02:30:00Z")])
    first = _store_fire(monkeypatch, tmp_path, flows=feed)
    assert first["exception_alert"] is not None                                   # a working ledger tells
    path = cash_flows.ledger_path(d)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["edited"] = True
    lines[1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    second = _store_fire(monkeypatch, tmp_path, flows=feed, now="2026-10-09T04:00:00Z")
    # refresh's own messages all need a readable ledger, so it still offers none of them ...
    assert (second["alert"], second["readiness_alert"], second["exception_alert"]) == (None, None, None)
    with pytest.raises(ToolError) as exc:
        cash_flows.verify(d)
    assert exc.value.reason_code == cash_flows.LEDGER_TAMPERED
    # ... and since PR-D the BLOCKED check, read-only and on its own, tells it once, by its reason code.
    snapshot = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert snapshot["cash_flows"]["error"] == cash_flows.LEDGER_TAMPERED
    blocked = store.blocked_check(now="2026-10-09T04:00:00Z", root=tmp_path)
    assert blocked["alert"] is not None and cash_flows.LEDGER_TAMPERED in blocked["alert"][1]
    assert store.mark_blocked_told(blocked["alert"][0], now="2026-10-09T04:00:00Z", root=tmp_path)
    assert store.blocked_check(now="2026-10-09T05:00:00Z", root=tmp_path)["alert"] is None


# C7-11: the state file is gone (C2). Without cursors the next read reaches back the first-read lookback
# (7 days) less the overlap, so a ledger whose tip is younger than that recovers by itself …
def test_c7_11_a_missing_state_recovers_a_ledger_inside_the_first_lookback(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)
    restore_ledger_only(live, before)
    cash_flows.state_path(live).unlink()
    now = later(T2, days=3, hours=1)
    fire(live, now, P2)
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, now, 1)


# … and one whose tip is older than that loses what lies in between. Since PR-B its history reads UNCHECKED.
def test_c7_11_a_missing_state_with_a_ledger_older_than_the_lookback_reads_its_history_unchecked(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=10), P2)
    restore_ledger_only(live, before)
    cash_flows.state_path(live).unlink()
    now = later(T2, days=10, hours=1)
    fire(live, now, P2)
    # The flow is still gone (option C re-reads nothing), and the consistency check still passes; the new state's
    # checkpoint was first observed after the cutover, so the history before it is UNVERIFIED: not ready.
    assert "pay:p2" not in keys(live)
    assert checks(live, now) == ("PASS", "PASS", 0)
    ready = cash_flows.readiness(live, now=now, current=CURRENT_OK)
    assert ready["checks"]["history_completeness"] == "UNCHECKED"
    assert ready["history"]["reason"] == "checkpoint_after_cutover" and ready["ready"] is False
