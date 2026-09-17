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


# --- the pre-order gate (PR2b) --------------------------------------------------------------------

class FakeSnapshotStore:
    """A venue's pre-order snapshot store, in memory: records every append, in order."""

    filesystem_write = True

    def __init__(self, venue=None, *, error=None):
        from runtime.mvp_runtime.crypto.state import VENUE_MAINNET

        self.venue = venue or VENUE_MAINNET
        self.appended: list[dict] = []
        self._error = error

    def append(self, snapshot):
        if self._error is not None:
            raise self._error
        if not any(s["pre_order_risk_snapshot_id"] == snapshot["pre_order_risk_snapshot_id"]
                   for s in self.appended):
            self.appended.append(dict(snapshot))
        return snapshot["risk_snapshot_sha256"]


def gate_stage():
    """A binding stage record at the live rung, with the approval the gate's profile requires."""
    from runtime.mvp_runtime.crypto.execution_stage import StageStatus

    return StageStatus(
        stage="LIVE_AUTONOMOUS", valid=True, reason_code=None, recorded_stage="LIVE_AUTONOMOUS",
        stage_id="stage_test", record_sha256="sha256:" + "5" * 64, approval_id="approval_stage_test",
    )


def approved_snapshot(intent, *, purpose=None, venue=None, now="2026-07-25T12:00:00Z"):
    """``(bound_intent, snapshot)``: a snapshot the real gate sealed for ``intent`` on passing
    checks and a whole profile — for tests whose subject is what happens AFTER the gate."""
    from runtime.mvp_runtime.crypto import pre_order_gate as g
    from runtime.mvp_runtime.crypto.execution_stage import (
        PURPOSE_AUTONOMOUS, PURPOSE_PROBE, PURPOSE_TESTNET,
    )
    from runtime.mvp_runtime.crypto.state import VENUE_MAINNET, VENUE_TESTNET

    purpose = purpose or PURPOSE_AUTONOMOUS
    authority = {
        PURPOSE_AUTONOMOUS: {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001",
                             "approval_id": "approval_arm_test"},
        PURPOSE_PROBE: {"kind": g.AUTHORITY_PROBE_PLAN, "batch_id": "batch_test",
                        "approval_id": "approval_probe_test"},
        PURPOSE_TESTNET: {"kind": g.AUTHORITY_TESTNET_CAPS, "max_order_notional_usdt": 50.0,
                          "max_daily_orders": 10},
    }[purpose]
    profile = g.approved_profile(
        purpose=purpose, stage=gate_stage(), authority=authority,
        budget=({"valid": True, "budget_id": "budget_test", "record_sha256": "sha256:" + "b" * 64}
                if purpose != PURPOSE_TESTNET else None),
        risk_limits=({"source": "default"} if purpose != PURPOSE_TESTNET else None),
    )
    lineage = {**{field: "x" for field in g.LINEAGE_FIELDS[purpose]},
               "order_intent_id": intent.get("order_intent_id")}
    snapshot = g.evaluate_pre_order_gate(
        intent, purpose=purpose,
        venue=venue or (VENUE_TESTNET if purpose == PURPOSE_TESTNET else VENUE_MAINNET),
        checks=[g.check("test_door", True)], profile=profile, lineage=lineage, facts={}, now=now,
    )
    assert snapshot["approved"], snapshot["failed_checks"]
    return g.bind_intent(intent, snapshot), snapshot
