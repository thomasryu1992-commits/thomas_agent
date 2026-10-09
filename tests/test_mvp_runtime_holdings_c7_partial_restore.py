"""C7: what a restore that mixes points in time does to the H6d-min ledger and its state (synthetic only).

The ledger is the authority and the state file a cache with the Binance read cursors in it. A ledger put
back from an older backup while the state keeps its newer cursors loses every flow between the two that
lies outside the one-day re-read: the next read starts after it, the exception it carried disappears,
and the readiness still says PASS. The fix is a design (``docs/proposals/HOLDINGS_LEDGER_CHECKPOINT_V0.1.md``),
not built yet. Each test here is one case of that design's table (C7-1 … C7-10):

- the cases the code already handles are asserted as they must stay;
- the cases it does not handle are pinned as they behave **today**, in a test whose name starts with
  ``test_defect_``. When the checkpoint lands, those assertions flip to the refusal the design names.
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
def test_c7_4_a_ledger_restored_within_the_overlap_recovers(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    restore_ledger_only(live, before)
    now = later(T2, hours=1)
    fire(live, now, P2)                                                            # the one-day re-read finds it
    assert keys(live).count("pay:p2") == 1
    assert_whole(live, now, 1)


def test_defect_c7_4_a_ledger_restored_past_the_overlap_loses_the_flow_and_still_passes(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)                                              # the cursor moves 3 days on
    assert checks(live, later(T2, days=3))[2] == 1
    restore_ledger_only(live, before)
    now = later(T2, days=3, hours=1)
    fire(live, now, P2)
    # Today: the flow is gone, its exception with it, and nothing says so. Design: refused with
    # HOLDINGS_CASH_FLOW_LEDGER_TAMPERED (the ledger is shorter than the state's checkpoint).
    assert "pay:p2" not in keys(live)
    assert checks(live, now) == ("PASS", "PASS", 0)


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
def test_defect_c7_6_a_ledger_of_another_history_with_the_same_length_passes(live, tmp_path):
    other = backup_point(live, tmp_path, "other")
    fire(live, T2, P2)
    fire(other, T2, pay("p3", at="2026-10-09T04:40:00Z"))                         # a history the venue never had
    assert len(ledger(live)) == len(ledger(other))
    assert ledger(live)[-1]["sha256"] != ledger(other)[-1]["sha256"]
    fire(live, later(T2, days=3), P2)
    restore_ledger_only(live, other)
    now = later(T2, days=3, hours=1)
    fire(live, now, P2)
    # Today: P2 is lost, a flow from the other history stands in its place, and both checks pass.
    # Design: refused (the line at the state's checkpoint is not the line the state recorded).
    assert "pay:p2" not in keys(live) and "pay:p3" in keys(live)
    assert checks(live, now) == ("PASS", "PASS", 1)


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


# C7-8: every H6d state on file today has no checkpoint; nothing relates its cursors to the ledger.
def test_defect_c7_8_a_state_without_a_checkpoint_is_read_as_consistent(live, tmp_path):
    before = backup_point(live, tmp_path)
    fire(live, T2, P2)
    fire(live, later(T2, days=3), P2)
    restore_ledger_only(live, before)
    state = cash_flows.load_state(live)
    assert not {"ledger_checkpoint", "checkpoint"} & set(state)
    # Today: PASS before any write, though the cursors are three days past this ledger. Design: the
    # consistency check reads NOT_VERIFIED until the operator adopts the ledger tip (no automatic trust).
    assert checks(live, later(T2, days=3, hours=1))[:2] == ("PASS", "PASS")


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
