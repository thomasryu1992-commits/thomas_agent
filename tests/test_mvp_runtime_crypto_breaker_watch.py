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
    assert "TRIPPED" in result["text"] and "daily_loss_limit_breached" in result["text"]


def test_releasing_announces(tmp_path):
    """The transition this was built for."""
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    first = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert first["state"]["allow_new_position"] is False
    _seed(tmp_path, _outcome(-1.2, at="2026-07-29T00:00:00Z"))   # yesterday: today is clear
    result = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert result["changed"] is True and "RELEASED" in result["text"]
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
    Asserted across all four transitions, because the wording is per-branch."""
    _seed(tmp_path, _outcome(0.5))
    first = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]           # first report
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))
    tripped = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]         # TRIPPED
    _seed(tmp_path, *[_outcome(-1.2) for _ in range(5)])
    changed = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]         # reasons changed
    _seed(tmp_path, _outcome(-1.2, at="2026-07-29T00:00:00Z"))
    released = breaker_watch.run_breaker_watch(tmp_path, now=NOW)["text"]        # RELEASED

    for text in (first, tripped, changed, released):
        assert text.splitlines()[0].startswith("CRYPTO LIVE ")
        assert "scope    : LIVE entries only" in text
        assert "new positions refused" not in text and "new positions allowed" not in text
    # The breaker is one lock on the door, not the door. Gate 0 refuses throughout these
    # fixtures (no acknowledgement, no sample), so the DOOR line stays REFUSED even on the
    # cycle where the breaker releases — which is the sentence an operator needs.
    assert "DOOR     : live entries REFUSED" in released
    assert "TRIPPED" in tripped


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


# --- Gate 0: the half that shuts with nobody doing anything --------------------

def _sign(root, ids, *, days=7, at=NOW):
    from datetime import datetime, timedelta
    from runtime.mvp_runtime.crypto import live_candidate_ack as lca

    until = (datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(days=days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    lca.write_ack(lca.build_ack_record(
        acknowledged_strategy_ids=list(ids), reason="checked the dashboard", valid_from=at,
        valid_until=until, registered_by="thomas", registered_at=at), root=root)


def _pool(root, *ids):
    from tests.test_mvp_runtime_crypto_cycle import _always_spec, _install_pool

    specs = []
    for sid in ids:
        spec = dict(_always_spec())
        spec["strategy_id"] = sid
        specs.append(spec)
    _install_pool(root, *specs)


def test_an_expiring_acknowledgement_announces_itself(tmp_path):
    """The transition this channel exists for. Nobody DOES an expiry — time passes — so without
    an edge-triggered message the operator finds out from the absence of trades."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    _sign(tmp_path, ["S_A"], days=1)

    opened = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert opened["state"]["gate0_ack_applies"] is True
    assert "DOOR     : live entries OPEN" in opened["text"]

    later = breaker_watch.run_breaker_watch(tmp_path, now="2026-08-05T00:00:00Z")
    assert later["changed"] is True
    assert "CRYPTO LIVE ENTRY CLOSED" in later["text"]
    assert "no longer applies (outside_validity_window)" in later["text"]


def test_a_pool_change_closes_the_door_and_says_which_way_it_closed(tmp_path):
    """`expired` and `the pool moved under it` want different responses — one is re-signed, the
    other re-judged — so the message names which happened rather than only that it did."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    _sign(tmp_path, ["S_A"])
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)

    _pool(tmp_path, "S_A", "S_B")            # a promotion the signature never saw
    after = breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    assert after["changed"] is True
    assert "CRYPTO LIVE ENTRY CLOSED" in after["text"]
    assert "routable_pool_changed_since_signature" in after["text"]


def test_a_released_breaker_does_not_claim_the_door_is_open(tmp_path):
    """The two locks are reported separately on purpose: an operator told "breaker released"
    while Gate 0 still refuses has been told something true and useless."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert state["allow_new_position"] is True        # the breaker is clear...
    assert state["live_entry_open"] is False          # ...and the door is not
    assert "DOOR     : live entries REFUSED" in breaker_watch.render_text(state, None)


def test_the_numbers_moving_under_an_unchanged_door_stay_quiet(tmp_path):
    """Still an edge trigger. Gate 0 joining the key must not turn this into a trade feed."""
    _seed(tmp_path, _outcome(0.5))
    _pool(tmp_path, "S_A")
    breaker_watch.run_breaker_watch(tmp_path, now=NOW)
    _seed(tmp_path, _outcome(0.5), _outcome(0.4))
    assert breaker_watch.run_breaker_watch(tmp_path, now=NOW)["changed"] is False
