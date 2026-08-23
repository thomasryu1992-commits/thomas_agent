"""`LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD` reads as the opposite of what it means.

Measured on the live board 2026-08-23 04:02. The cycle line said the live outcomes were
excluded from the risk guard, and at that moment the guard was reading
`live_out_e56cf310dcc07d9c6edd` at **+398.027R** — a row whose true value is -0.887R. The
exclusion keys off a MISSING `result_R`, so the row with the wrong number passes and the two
rows actually dropped were -0.0081 and +0.0171 USDT with no recorded risk at all. An operator
reading the name would conclude the known-bad row had been quarantined.

The code is not renamed: 2,285 rows in `records.jsonl` already carry it. Under test is the
board — the ledger string stays verbatim, and the line below it says what the string means.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto.dashboard import render_status_text

EXCLUDED = "LIVE_OUTCOMES_EXCLUDED_FROM_RISK_GUARD"


def _status(*codes):
    return {
        "created_at": "2026-08-23T04:02:00Z",
        "last_cycle": {
            "at": "2026-08-23T03:59:00Z",
            "verdict": "ALLOW",
            "route": "NO_ENTRY",
            "feeds": {},
            "degraded": False,
            "reason_codes": list(codes),
        },
    }


def _note_lines(text):
    return [line.strip() for line in text.splitlines() if line.strip().startswith("└")]


def test_the_ledger_string_is_printed_verbatim():
    """An operator grepping the ledger for what the board showed has to find it."""
    assert EXCLUDED in render_status_text(_status(EXCLUDED))


def test_the_note_denies_the_reading_that_the_bad_row_was_dropped():
    note = " ".join(_note_lines(render_status_text(_status(EXCLUDED))))
    # The whole point: a row WITH an R is never excluded, however wrong that R is.
    assert "R이 붙은 행은 그 값이 틀려도 빠지지 않는다" in note
    # And the money did not leave the accounting, only the R statistics.
    assert "일간 손실 브레이커" in note


def test_an_unnoted_code_gets_no_note():
    """A note under every code is a board an operator learns to scroll past."""
    text = render_status_text(_status("HTF_DEGRADED", "LIFECYCLE_TRANSITION"))
    assert _note_lines(text) == []
    assert "HTF_DEGRADED, LIFECYCLE_TRANSITION" in text


def test_a_repeated_code_is_one_condition_and_one_note():
    """Two identical notes read as two findings; the 사유 line still shows the row as written."""
    text = render_status_text(_status(EXCLUDED, EXCLUDED))
    assert len(_note_lines(text)) == 1
    assert f"사유 {EXCLUDED}, {EXCLUDED}" in text


def test_two_notes_each_name_their_code(monkeypatch):
    """With one note the line above is unambiguous; with two, nothing says which is which."""
    from runtime.mvp_runtime.crypto import dashboard
    monkeypatch.setitem(dashboard._REASON_NOTES, "OTHER_CODE", "다른 설명")
    notes = _note_lines(render_status_text(_status(EXCLUDED, "OTHER_CODE")))
    assert len(notes) == 2
    assert all(code in note for code, note in zip((EXCLUDED, "OTHER_CODE"), notes))


def test_no_reason_codes_prints_no_sudden_line():
    assert "사유" not in render_status_text(_status())


# --- what the guard actually dropped, not just that it dropped something ------------------

from runtime.mvp_runtime.crypto.live_pnl import excluded_outcomes_digest  # noqa: E402


def _rows(*amounts, reason="LIVE_OUTCOME_NO_RECORDED_RISK"):
    return [{"outcome_id": f"live_out_{i}", "symbol": "BTCUSDT",
             "realized_pnl_usdt": a, "reason": reason} for i, a in enumerate(amounts)]


def _excluded_status(digest, *codes):
    status = _status(*(codes or (EXCLUDED,)))
    status["last_cycle"]["live_outcomes_excluded"] = digest
    return status


def test_the_digest_is_fixed_size_whatever_it_summarises():
    """It rides a record appended every cycle; it must not grow with the live history."""
    small = excluded_outcomes_digest(_rows(-0.0081, 0.0171))
    large = excluded_outcomes_digest(_rows(*([0.5] * 400)))
    assert set(small) == set(large)
    assert all(not isinstance(v, (list, tuple)) for v in large.values())


def test_net_and_gross_are_both_kept_because_cancelling_rows_are_not_no_money():
    digest = excluded_outcomes_digest(_rows(-0.0081, 0.0171))
    assert digest["count"] == 2
    assert digest["realized_pnl_usdt"] == pytest.approx(0.0090)
    assert digest["abs_realized_pnl_usdt"] == pytest.approx(0.0252)


def test_an_uncomputable_amount_is_none_and_never_a_zero():
    """A zero reads as a flat trade. `None` reads as nobody could compute it."""
    digest = excluded_outcomes_digest(_rows(-0.5, None))
    assert digest["realized_pnl_usdt"] is None
    assert digest["abs_realized_pnl_usdt"] is None
    # The two facts that survive a missing amount, because they are still known.
    assert digest["count"] == 2
    assert digest["reasons"] == {"LIVE_OUTCOME_NO_RECORDED_RISK": 2}


def test_the_board_states_the_count_and_the_money():
    text = render_status_text(_excluded_status(excluded_outcomes_digest(_rows(-0.0081, 0.0171))))
    measured = [line for line in _note_lines(text) if "건" in line][0]
    assert "2건" in measured
    assert "+0.0090 USDT" in measured
    # Opposite signs, so the single figure understates what moved and the gross is shown too.
    assert "총 이동 0.0252" in measured


def test_the_gross_is_omitted_when_it_would_repeat_the_net():
    text = render_status_text(_excluded_status(excluded_outcomes_digest(_rows(-0.5, -0.25))))
    assert "총 이동" not in text


def test_an_uncomputable_total_says_so_on_the_board():
    text = render_status_text(_excluded_status(excluded_outcomes_digest(_rows(-0.5, None))))
    assert "금액 확인 불가" in text
    assert "0.0000 USDT" not in text


def test_the_measured_line_comes_before_the_note():
    notes = _note_lines(render_status_text(
        _excluded_status(excluded_outcomes_digest(_rows(-0.0081, 0.0171)))))
    assert "건" in notes[0] and "빠지지 않는다" in notes[1]


def test_a_record_without_the_field_renders_as_it_always_did():
    """Every cycle logged before this field existed, and every clean cycle since."""
    text = render_status_text(_status(EXCLUDED))
    assert len(_note_lines(text)) == 1
    assert EXCLUDED in text


def test_a_single_reason_does_not_restate_the_row_count():
    text = render_status_text(_excluded_status(excluded_outcomes_digest(_rows(-0.5, -0.25))))
    measured = [line for line in _note_lines(text) if "건" in line][0]
    assert measured.endswith("LIVE_OUTCOME_NO_RECORDED_RISK")


def test_a_second_reason_brings_the_counts_back():
    digest = excluded_outcomes_digest(_rows(-0.5) + _rows(-0.25, reason="OTHER_REASON"))
    measured = [line for line in _note_lines(render_status_text(_excluded_status(digest)))
                if "건" in line][0]
    assert "LIVE_OUTCOME_NO_RECORDED_RISK 1" in measured and "OTHER_REASON 1" in measured
