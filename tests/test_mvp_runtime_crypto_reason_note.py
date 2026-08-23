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
