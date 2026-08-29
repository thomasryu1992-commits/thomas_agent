"""Shared test scaffolding — the one definition of the local-Core skip guard.

Seventeen test files each carried their own ``LOCAL_POINTER`` + ``requires_local_core``
copy; the next change to how a local Core activation is detected would have meant
seventeen edits (or, worse, sixteen). Import from here instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from runtime.mvp_runtime.binding import DEFAULT_POINTER_REL

REPO_ROOT = Path(__file__).resolve().parents[1]
LOCAL_POINTER = REPO_ROOT / DEFAULT_POINTER_REL

# Binding-dependent tests skip on a core-neutral checkout; they run on any machine with a
# local Core activation (see the `verify` skill, "Core activation (local, per-machine)").
#
# Not on CI, despite what this comment used to say. Both workflows that run pytest
# (mvp-runtime-tests, thomas-agent-runtime-validation) call
# scripts/ci_activate_core_for_tests.py first, precisely so these run rather than skip —
# that script's own docstring says so. The checkout this skips on is a *developer's*: run
# the suite without a local activation and ~195 tests quietly vanish, which is worth
# knowing before reading a green local run as full coverage.
requires_local_core = pytest.mark.skipif(
    not LOCAL_POINTER.is_file(), reason="no local Core activation")


class FakeResp:
    """The canonical fake urlopen response body five transport-test files carried verbatim.

    Two more files keep their own variants deliberately (their tests exercise a different
    response surface); a variant belongs beside the test that needs it, a verbatim copy
    does not.
    """

    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def patch_urlopen(monkeypatch, payload_or_exc):
    """Route ``urllib.request.urlopen`` to a canned payload or a raised exception."""
    def fake_urlopen(request, timeout):
        if isinstance(payload_or_exc, Exception):
            raise payload_or_exc
        return FakeResp(payload_or_exc)
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)


def make_gate_authorization(*, flags, provider_id, **overrides):
    """One gate ``Authorization`` for tests (named so pytest cannot collect it): the standard inert tail (``sha256:test``, a
    far-future expiry, the state-dir evidence ref) under the caller's capability head.

    Twenty-plus files carried the tail as a four-line literal; the next change to the
    Authorization shape — a new required field, a different evidence rule — lands here
    once instead of in every copy. ``overrides`` exists for the handful of tests that
    deliberately vary the tail (a past expiry, a different hash).
    """
    from runtime.mvp_runtime.safety_gate import Authorization

    fields = {
        "flags": tuple(flags),
        "provider_id": provider_id,
        "activation_sha256": "sha256:test",
        "expires_at": "2999-01-01T00:00:00Z",
        "evidence_ref": ".runtime_governance_state/evidence.md",
    }
    fields.update(overrides)
    return Authorization(**fields)
