"""The C4 breaker's transition watch (``crypto_breaker_watch``).

Under test: it reports the same verdict the cycle would act on; it speaks on the EDGE and
stays quiet otherwise; the first fire always announces; an undelivered announcement is not
marked as said; and the render names the mixed-basis caveat only when the window has one.
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


def _seed(root, *outcomes):
    """Write the rows, stamping a unique id per row — the store refuses duplicates, and
    several of these cases deliberately seed identical R values."""
    path = state_dir(root) / OUTCOMES_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, outcome in enumerate(outcomes):
        record = {**outcome, "outcome_id": integrity.short_id("out", {"i": str(i)})}
        record["record_sha256"] = integrity.sha256_record(record)
        lines.append(json.dumps(record) + "\n")
    path.write_text("".join(lines), encoding="utf-8")


# --- what it reports -----------------------------------------------------------

def test_a_clear_book_reports_allowed(tmp_path):
    _seed(tmp_path, _outcome(0.5))
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    assert state["allow_new_position"] is True and state["problems"] == []
    assert state["limits"]["source"] == guards.SOURCE_DEFAULT


def test_it_reports_the_same_verdict_the_cycle_would_act_on(tmp_path):
    """A watch that assembled its inputs differently would eventually report a state the
    runtime is not in, so it runs the real guard against the real limits."""
    _seed(tmp_path, _outcome(-1.2), _outcome(-1.2))          # -2.4R today, daily limit -2.0
    state = breaker_watch.evaluate(tmp_path, now=NOW)
    from runtime.mvp_runtime.crypto.paper import read_outcomes, split_by_provenance
    own, _ = split_by_provenance(read_outcomes(tmp_path))
    direct = guards.run_risk_guard(own, now=NOW, limits=risk_limits.resolve_risk_limits(tmp_path, now=NOW))
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


def test_the_mixed_basis_caveat_appears_only_when_the_window_has_one(tmp_path):
    """Rendered directly rather than through a transition: the caveat is a property of the
    WINDOW, and whether the verdict happened to flip is a different question."""
    _seed(tmp_path, _outcome(0.5), _outcome(0.4))
    clean = breaker_watch.render_text(breaker_watch.evaluate(tmp_path, now=NOW), None)
    assert "r_basis  : intent_net_of_costs 2" in clean
    assert "mixed R bases" not in clean

    _seed(tmp_path, _outcome(0.5), _outcome(0.4, r_basis="intent"), _outcome(-1.2))
    mixed = breaker_watch.render_text(breaker_watch.evaluate(tmp_path, now=NOW), None)
    assert "mixed R bases" in mixed
    assert "intent 1" in mixed and "intent_net_of_costs 2" in mixed
