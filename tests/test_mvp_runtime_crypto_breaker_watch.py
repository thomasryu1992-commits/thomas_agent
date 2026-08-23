"""The C4 breaker's transition watch (``crypto_breaker_watch``).

Under test: it reports the same verdict the LIVE leg acts on and says so — paper answers to no
loss breaker, so a message claiming "new positions refused" would describe a stopped runtime
that is in fact trading; it speaks on the EDGE and stays quiet otherwise; the first fire always
announces; an undelivered announcement is not marked as said; and the render names the
mixed-basis caveat only when the window has one.
"""

from __future__ import annotations

import json

import pytest

from runtime.read_only_kernel import integrity

from runtime.mvp_runtime.crypto import breaker_watch, guards, risk_limits
from runtime.mvp_runtime.crypto.paper import (
    OUTCOMES_FILENAME, PAPER_PROVENANCE, state_dir,
)
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-30T12:00:00Z"


def _outcome(result_r, *, at=NOW, r_basis="intent_net_of_costs"):
    """A stored outcome, self-hashed — `read_outcomes` verifies every line."""
    record = {
        "outcome_closed": True, "result_R": result_r, "created_at_utc": at,
        "provenance": PAPER_PROVENANCE, "r_basis": r_basis, "symbol": "BTCUSDT",
        "timeframe": "15m", "strategy_id": "S1",
    }
    return record


def _seed_paper(root, *outcomes):
    """Paper rows. They no longer reach the breaker — kept because the watch still reports the
    r_basis mix and the paper/live row split off this store."""
    path = state_dir(root) / OUTCOMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, outcome in enumerate(outcomes):
        record = {**outcome, "outcome_id": integrity.short_id("out", {"i": str(i)})}
        record["record_sha256"] = integrity.sha256_record(record)
        lines.append(json.dumps(record) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


def _seed(root, *outcomes):
    """Write LIVE outcomes — the only rows the loss breakers judge now.

    Takes the same ``(result_r, at)`` shape the paper seeder did, and converts: the live ledger
    stores USDT plus the risk it was taken against, and `live_outcomes_for_analysis` derives R
    from the pair. Written through `build_live_outcome_record` so every row is self-hashed and
    shaped exactly as `live_leg.execute_live_exit` writes it — a hand-made row fails the
    verified read and reports `risk_history_unreadable`, which is a refusal but never the one a
    test here means."""
    from runtime.mvp_runtime.crypto.live_pnl import build_live_outcome_record
    from runtime.mvp_runtime.crypto.live_pnl import state_dir as live_state_dir

    target = live_state_dir(root)
    target.mkdir(parents=True, exist_ok=True)
    risk = 10.0
    lines = []
    for i, outcome in enumerate(outcomes):
        record = build_live_outcome_record(
            realized_pnl_usdt=float(outcome["result_R"]) * risk,
            symbol="BTCUSDT", side="SELL", quantity=0.001,
            position_id=f"live_p{i}", risk_usdt=risk,
            now=str(outcome["created_at_utc"]),
        )
        lines.append(json.dumps(record, ensure_ascii=False) + "\n")
    (target / "live_outcomes.jsonl").write_text("".join(lines), encoding="utf-8")


# --- what it reports -----------------------------------------------------------

def test_a_clear_book_reports_allowed(tmp_path):
    _seed(tmp_path, _outcome(0.5))
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert state["allow_new_position"] is True and state["problems"] == []
    assert state["limits"]["source"] == guards.SOURCE_DEFAULT


def test_it_reports_the_same_verdict_the_live_leg_would_act_on(tmp_path):
    """A watch that assembled its inputs differently would eventually report a state the
    runtime is not in, so it runs the real guard against the real limits."""
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))          # -2.4R today, daily limit -2.0
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    from runtime.mvp_runtime.crypto.live_pnl import live_outcomes_for_analysis, read_live_outcomes
    live, _ = live_outcomes_for_analysis(read_live_outcomes(tmp_path))
    direct = guards.run_risk_guard(live, now=NOW, limits=risk_limits.resolve_risk_limits(tmp_path, now=NOW))
    assert state["status"] == direct["status"] == "BLOCK_NEW_POSITION"
    assert state["problems"] == direct["problems"] == ["daily_loss_limit_breached"]


def test_an_unusable_limits_record_propagates_rather_than_reading_as_normal(tmp_path):
    """The cycle refuses entries in this state; a watch must not report it as merely clear."""
    _seed(tmp_path, _outcome(0.5))
    state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    risk_limits.limits_path(tmp_path).write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError):
        breaker_watch.evaluate(tmp_path, now=NOW)


# --- when it speaks ------------------------------------------------------------

def test_the_first_fire_always_announces(tmp_path):
    """Silence on a fresh deploy is indistinguishable from a watch that is not running."""
    _seed(tmp_path, _outcome(0.5))
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert result["changed"] is True and result["previous"] is None
    assert "first report" in result["text"]


def test_an_unchanged_verdict_is_silent(tmp_path):
    _seed(tmp_path, _outcome(0.5))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    again = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert again["changed"] is False and again["text"] == ""
    assert "breaker_unchanged" in breaker_watch.status_line(again)


def test_moving_numbers_alone_do_not_announce(tmp_path):
    """Keyed on the verdict, not the R: a watch that fired on every settlement is a trade feed."""
    _seed(tmp_path, _outcome(0.5))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, _outcome(0.5), _outcome(0.9))            # still clear, different numbers
    assert breaker_watch.run_breaker_watch(tmp_path, now=NOW)["changed"] is False


def test_tripping_announces(tmp_path):
    _seed(tmp_path, _outcome(0.5))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert result["changed"] is True
    # The door, not the lock. Until Gate 0 was removed this said "TRIPPED": the breaker moving
    # and the door moving were different events, because Gate 0 could hold the door shut under
    # a clear breaker. With one lock left they are the same event and the door is the honest
    # name for it — an operator acts on whether entries happen, not on which lock moved.
    assert "CRYPTO LIVE ENTRY CLOSED" in result["text"]
    assert "daily_loss_limit_breached" in result["text"]


def test_releasing_announces(tmp_path):
    """The transition this was built for."""
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    first = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert first["state"]["allow_new_position"] is False
    _seed(tmp_path, _outcome(-1.2, at="2026-07-29T00:00:00Z"))   # yesterday: today is clear
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert result["changed"] is True and "CRYPTO LIVE ENTRY OPEN" in result["text"]
    assert "previous : BLOCK_NEW_POSITION" in result["text"]


def test_a_new_reason_announces_even_while_still_blocked(tmp_path):
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, *[_outcome(-1.2) for _ in range(5)])      # daily AND weekly now
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert result["changed"] is True and "reasons changed" in result["text"]
    assert "weekly_loss_limit_breached" in result["text"]


# --- the marker ----------------------------------------------------------------

def test_persist_false_runs_the_comparison_and_writes_nothing(tmp_path):
    """What the scheduler uses, so a failed DELIVERY does not record the news as told."""
    _seed(tmp_path, _outcome(0.5))
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW, persist=False)
    assert result["changed"] is True
    assert breaker_watch.read_mark(tmp_path) is None
    # ...so the next fire still has the announcement to make.
    assert breaker_watch.run_breaker_watch(tmp_path, now=NOW, persist=False)["changed"] is True


def test_a_corrupt_marker_costs_one_redundant_announcement_not_the_watch(tmp_path):
    _seed(tmp_path, _outcome(0.5))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    breaker_watch.mark_path(tmp_path).write_text("{not json", encoding="utf-8")
    assert breaker_watch.read_mark(tmp_path) is None
    assert breaker_watch.run_breaker_watch(tmp_path, now=NOW)["changed"] is True


# --- the render ----------------------------------------------------------------

def test_the_render_is_ascii(tmp_path):
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"].encode("ascii")


def test_every_headline_names_the_live_leg_and_never_claims_the_runtime_is_stopped(tmp_path):
    """The regression this guards: `paper_trade_verdict` took the paper leg off the loss
    breakers, so an unqualified "new positions refused" now announces a stopped runtime that is
    still opening paper positions — and an operator has no way to catch that from the outside.
    Asserted across all three transitions, because the wording is per-branch. It was four
    until Gate 0 was removed (2026-08-03) and the door stopped being able to disagree with the
    breaker, which made the fourth branch unreachable."""
    _seed(tmp_path, _outcome(0.5))
    first = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]           # first report
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    closed = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]          # door CLOSED
    _seed(tmp_path, *[_outcome(-1.2) for _ in range(5)])
    changed = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]         # reasons changed
    _seed(tmp_path, _outcome(-1.2, at="2026-07-29T00:00:00Z"))
    opened = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]          # door OPEN

    for text in (first, closed, changed, opened):
        assert text.splitlines()[0].startswith("CRYPTO LIVE ")
        assert "scope    : LIVE entries only" in text
        assert "new positions refused" not in text and "new positions allowed" not in text
    # The door now moves with the breaker, so the releasing cycle reports an OPEN door. That is
    # the sentence an operator acts on, and it is the one this file exists to keep honest.
    assert "DOOR     : live entries OPEN" in opened
    assert "DOOR     : live entries REFUSED" in closed


def test_the_render_separates_the_judged_rows_from_the_ones_that_are_not(tmp_path):
    """A paper book that used to BE the ruling now sits beside it, labelled. Watching
    `-19.35R` simply vanish between two reports is how an operator concludes the breaker broke."""
    _seed_paper(tmp_path, _outcome(-1.2), _outcome(-1.2), _outcome(-1.2))
    _seed(tmp_path, _outcome(-0.5))
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert (state["own_closed"], state["live_closed"]) == (3, 1)
    assert state["judged_rows"] == 1
    text = breaker_watch.render_text(state, None)
    assert "rows     : 1 live closed (judged) | 3 paper (not judged)" in text
    assert "INERT" not in text


def test_an_empty_live_history_is_reported_as_inert_not_as_clear(tmp_path):
    """The state the machine is in until live trades, and the one place a breaker channel can
    mislead by being accurate: every number reads clear because there is nothing to judge."""
    _seed_paper(tmp_path, _outcome(-1.2), _outcome(-1.2))
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert state["allow_new_position"] is True and state["judged_rows"] == 0
    text = breaker_watch.render_text(state, None)
    assert "rows     : 0 live closed (judged) | 2 paper (not judged)" in text
    assert "the loss breakers are INERT, not satisfied" in text


def test_the_row_split_does_not_make_the_watch_speak(tmp_path):
    """Added to the state, so it must not join the change key — settlements move it constantly
    and a watch that fired on them is the trade feed this was built not to be."""
    _seed(tmp_path, _outcome(0.5))
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, _outcome(0.5), _outcome(0.4))              # one more live row, same verdict
    again = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert again["state"]["live_closed"] == 2 and again["changed"] is False


def test_the_mixed_basis_caveat_appears_only_when_the_window_has_one(tmp_path):
    """Rendered directly rather than through a transition: the caveat is a property of the
    WINDOW, and whether the verdict happened to flip is a different question. Seeded on the
    PAPER store, which is where the r_basis mix is read from — it no longer feeds the verdict,
    and the caveat is about the rows, not the ruling."""
    _seed_paper(tmp_path, _outcome(0.5), _outcome(0.4))
    clean = breaker_watch.render_text(breaker_watch.evaluate(tmp_path, now=NOW), None)
    assert "r_basis  : intent_net_of_costs 2" in clean
    assert "mixed R bases" not in clean

    _seed_paper(tmp_path, _outcome(0.5), _outcome(0.4, r_basis="intent"), _outcome(-1.2))
    mixed = breaker_watch.render_text(breaker_watch.evaluate(tmp_path, now=NOW), None)
    assert "mixed R bases" in mixed
    assert "intent 1" in mixed and "intent_net_of_costs 2" in mixed


# --- the door ------------------------------------------------------------------
#
# This section was "Gate 0: the half that shuts with nobody doing anything" and carried eight
# more tests over the operator acknowledgement — its expiry, the pool change that voided it,
# the two-day warning before a lapse, and the three causes of a zero sample. All of them
# described a second lock that is gone (2026-08-03), so they are deleted with it rather than
# rewritten: there is no acknowledgement to expire and no sample to explain.
#
# What survives is the pair below, because they were never about Gate 0 — they are about the
# door agreeing with the state, and staying an EDGE trigger rather than a trade feed.


def _pool(root, *ids):
    from tests.test_mvp_runtime_crypto_cycle import _always_spec, _install_pool

    specs = []
    for sid in ids:
        spec = dict(_always_spec())
        spec["strategy_id"] = sid
        specs.append(spec)
    _install_pool(root, *specs)


def test_a_released_breaker_now_opens_the_door(tmp_path):
    """The inverse of what stood here. This test asserted that a clear breaker leaves the door
    REFUSED, because Gate 0 was the second lock and it refused. Gate 0 is gone (2026-08-03), so
    the door has one lock and a clear breaker IS an open door — the assertion flips rather than
    the test being deleted, because the property it guards (the render agrees with the state)
    is the same one, and it is worth knowing if those two ever disagree again."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert state["allow_new_position"] is True
    assert state["live_entry_open"] is True
    assert "DOOR     : live entries OPEN" in breaker_watch.render_text(state, None)


def test_the_numbers_moving_under_an_unchanged_door_stay_quiet(tmp_path):
    """Still an edge trigger. Gate 0 joining the key must not turn this into a trade feed."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, _outcome(0.5), _outcome(0.4))


# --- the gap between announcements -------------------------------------------
#
# The blind spot is structural, not a bug in the comparison: this watch is an edge trigger that
# compares now against the last state it ANNOUNCED, and it fires hourly. A breaker that trips
# and releases inside one hour shows the same verdict at both ends. On 2026-08-21 the daily
# limit breached at 03:59:13Z, the limits record went unusable at 04:14:00Z and a new record
# cleared it at 04:29:00Z; the 03:58 and 04:58 fires both read NORMAL and both stayed quiet.

import json as _json  # noqa: E402

from runtime.mvp_runtime.crypto import breaker_watch as _bw  # noqa: E402


def _cycle(at, status="ALLOW", problems=(), limits_id="risklimits_aaaa"):
    return {"kind": "crypto_cycle", "trace_id": at, "record": {
        "created_at": at, "verdict_status": status, "verdict_problems": list(problems),
        "risk_limits": {"limits_id": limits_id}}}


def _ledger(tmp_path, rows):
    path = tmp_path / ".runtime_governance_state" / "runtime_ledger" / "records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(_json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


# Deliberately not `NOW`: this file already has one at module scope, and rebinding it here
# would silently retime every test above — which is exactly what the first draft of this block
# did, and five of them failed on the shadowed value rather than on anything they test.
GAP_NOW = "2026-08-21T04:58:00Z"
GAP_LAST_SPOKE = "2026-08-19T08:58:56Z"


def test_the_watch_names_the_transitions_its_hourly_fire_missed(tmp_path):
    """The 2026-08-21 episode, to shape. Both ends NORMAL, a shut door in the middle."""
    _ledger(tmp_path, [
        _cycle("2026-08-21T03:58:43Z"),
        _cycle("2026-08-21T03:59:13Z", "NO_NEW_POSITION", ["daily_loss_limit_breached"]),
        _cycle("2026-08-21T04:29:00Z"),
    ])
    missed, coverage = _bw.transitions_since(tmp_path, since=GAP_LAST_SPOKE, now=GAP_NOW)
    assert coverage["read"] is True
    assert [m["to_status"] for m in missed] == ["NO_NEW_POSITION", "ALLOW"]
    assert missed[0]["to_problems"] == ["daily_loss_limit_breached"]


def test_a_limits_record_swap_is_a_transition_of_its_own(tmp_path):
    """Three of this year's breaker releases were the bar moving, not the streak recovering. A
    list that showed only the verdict would report the release with no cause."""
    _ledger(tmp_path, [
        _cycle("2026-08-21T03:58:00Z", limits_id="risklimits_16426bf1"),
        _cycle("2026-08-21T04:29:00Z", limits_id="risklimits_b794afbb"),
    ])
    missed, _ = _bw.transitions_since(tmp_path, since=GAP_LAST_SPOKE, now=GAP_NOW)
    assert any(m.get("limits_swapped_to") == "risklimits_b794afbb" for m in missed)


def test_a_quiet_gap_produces_nothing(tmp_path):
    """The normal case, and it must stay the normal case — an empty list here is what keeps
    this from becoming the trade feed the module's docstring argues against."""
    _ledger(tmp_path, [_cycle("2026-08-21T03:58:00Z"), _cycle("2026-08-21T04:29:00Z")])
    missed, coverage = _bw.transitions_since(tmp_path, since=GAP_LAST_SPOKE, now=GAP_NOW)
    assert missed == []
    assert coverage["rows"] == 2


def test_a_first_fire_does_not_replay_history(tmp_path):
    """With no mark the watch already announces unconditionally. Replaying a week into that
    first message is the burst this module spends four paragraphs arguing against."""
    _ledger(tmp_path, [
        _cycle("2026-08-21T03:59:13Z", "NO_NEW_POSITION", ["daily_loss_limit_breached"]),
        _cycle("2026-08-21T04:29:00Z"),
    ])
    missed, coverage = _bw.transitions_since(tmp_path, since=None, now=GAP_NOW)
    assert missed == []
    assert coverage["read"] is False


def test_a_window_older_than_the_reach_says_so_instead_of_reading_empty(tmp_path):
    """"Nothing found" and "nothing happened" are different, and `ledger_rotate` makes the
    first one common: a month-old mark points past what the active file still holds."""
    _ledger(tmp_path, [_cycle("2026-08-21T04:29:00Z")])
    _missed, coverage = _bw.transitions_since(
        tmp_path, since="2026-06-01T00:00:00Z", now=GAP_NOW)
    assert coverage["truncated"] is True


def test_an_unreadable_ledger_is_not_reported_as_a_quiet_gap(tmp_path):
    _missed, coverage = _bw.transitions_since(tmp_path, since=GAP_LAST_SPOKE, now=GAP_NOW)
    assert coverage["read"] is False        # no file at all
    assert coverage["since"] == GAP_LAST_SPOKE


def test_a_missed_transition_is_the_one_new_reason_to_speak():
    """And it is not a number. `has_changed` still ignores every R on the state, which is what
    keeps the two silence tests above green."""
    base = {"allow_new_position": True, "problems": [], "live_entry_open": True}
    assert _bw.has_changed(base, dict(base)) is False
    assert _bw.has_changed(dict(base, weekly_pnl_r=-9.9), dict(base)) is False
    assert _bw.has_changed(
        dict(base, missed_transitions=[{"at": "x", "to_status": "NO_NEW_POSITION"}]),
        dict(base)) is True


def test_the_status_line_carries_the_numbers_on_every_fire():
    """The scheduler's `fired` event stores this string, so writing the numbers here gives the
    breakers an hourly time series with no new store. On 2026-08-21 the weekly figure jumped
    from 43R to 441R between two fires and neither line recorded either number."""
    line = _bw.status_line({"changed": False, "state": {
        "status": "NORMAL", "daily_pnl_r": 0.0, "weekly_pnl_r": 441.08,
        "consecutive_losses": 3, "drawdown_r": -2.15, "judged_rows": 15,
        "limits": {"max_consecutive_losses": 10, "drawdown_limit_r": 10.0,
                   "limits_id": "risklimits_b794afbb"}}})
    assert "weekly=441.08" in line
    assert "streak=3/10" in line
    assert "dd=-2.15/10.0" in line
    assert "limits=risklimits_b794afbb" in line


def test_the_guard_owns_the_problem_vocabulary():
    """A watch that re-spelled these would drift silently the first time a fifth was added."""
    from runtime.mvp_runtime.crypto import guards as _guards

    assert "daily_loss_limit_breached" in _guards.RISK_GUARD_PROBLEMS
    assert _guards.RISK_LIMITS_UNUSABLE_PROBLEM in _guards.RISK_GUARD_PROBLEMS
