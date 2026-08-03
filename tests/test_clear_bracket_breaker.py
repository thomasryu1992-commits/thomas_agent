"""The operator's reset for the bracket breaker.

The breaker holds the live entry door shut and nothing expires it, so this script is the only
way back apart from a protective bracket actually resting at the venue. What it must not become
is a convenient button: the reset demands a written reason precisely because the failure it
follows was one nobody had explained yet.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import live_leg
from runtime.mvp_runtime.crypto.live_order import LiveBracketFailureBreaker
from runtime.mvp_runtime.crypto.live_pnl import LIVE_TRADING_FLAGS, LIVE_TRADING_PROVIDER_ID
from runtime.mvp_runtime.safety_gate import Authorization
from scripts import clear_bracket_breaker as cbb

NOW = "2026-08-02T08:20:44Z"

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


@pytest.fixture
def tripped(tmp_path, monkeypatch):
    """A breaker at its limit, with the durable implementation wired in — tests hold no grant,
    so the script's own selection would hand back the inert one."""
    breaker = LiveBracketFailureBreaker(root=tmp_path, authorization=_LIVE_AUTH)
    for _ in range(2):
        breaker.record_failure(
            symbol="ETHUSDT", status=live_leg.ENTRY_NAKED_CLOSED, at=NOW,
            reason_codes=[live_leg.BRACKET_FAILED],
            error_detail=[{"leg": "stop", "error": "ORDER_REJECTED", "error_detail": "-2021"}],
        )
    monkeypatch.setattr(cbb, "select_live_bracket_breaker", lambda **kw: breaker)
    return tmp_path


def test_show_reports_the_state_and_writes_nothing(tripped, capsys):
    assert cbb.main(["--show", "--root", str(tripped)]) == cbb.EXIT_OK
    out = capsys.readouterr().out
    assert "TRIPPED" in out
    assert "ETHUSDT" in out and "-2021" in out
    assert "live entries are REFUSED" in out
    # Still tripped: --show is a read.
    assert cbb.main(["--show", "--root", str(tripped)]) == cbb.EXIT_OK
    assert "TRIPPED" in capsys.readouterr().out


def test_clearing_requires_a_reason_and_an_operator(tripped, capsys):
    assert cbb.main(["--root", str(tripped)]) == cbb.EXIT_REFUSED
    assert cbb.main(["--cleared-by", "thomas", "--root", str(tripped)]) == cbb.EXIT_REFUSED
    assert cbb.main(["--reason", "looked at it", "--root", str(tripped)]) == cbb.EXIT_REFUSED
    # And none of those refusals cleared anything.
    capsys.readouterr()
    cbb.main(["--show", "--root", str(tripped)])
    assert "TRIPPED" in capsys.readouterr().out


def test_a_reasoned_clear_reopens_the_door(tripped, capsys):
    code = cbb.main([
        "--cleared-by", "thomas", "--reason", "venue -2021; stop distance fixed",
        "--root", str(tripped),
    ])
    assert code == cbb.EXIT_OK
    out = capsys.readouterr().out
    assert "cleared" in out
    assert "TRIPPED" not in out


def test_clearing_an_already_clear_breaker_says_so(tmp_path, capsys):
    code = cbb.main([
        "--cleared-by", "thomas", "--reason", "nothing to do", "--root", str(tmp_path),
    ])
    assert code == cbb.EXIT_OK
    assert "nothing to clear" in capsys.readouterr().out
