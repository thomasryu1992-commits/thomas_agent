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


def deep_order_book(mid=60000.0, *, received_at, levels=20, quantity=1_000_000.0, half_spread_bps=0.1):
    """A book deep and tight enough that no test order moves it (PR2d-3): ``levels`` levels a
    side, each holding ``quantity``, the best quotes ``half_spread_bps`` either side of ``mid``.
    ``received_at`` is when the book was in hand; the entry judges its age from it."""
    step = mid * half_spread_bps / 10_000.0
    return {
        "bids": [(mid - step * (i + 1), quantity) for i in range(levels)],
        "asks": [(mid + step * (i + 1), quantity) for i in range(levels)],
        "received_at": received_at,
    }


def healthy_optional_data(bar_time=None):
    """The optional data of a context whose legs all answered and whose feeds are fresh (PR2d-2):
    what `cycle.optional_data_health` returns for it at ``bar_time`` — the bar the decision is on,
    which the entry door checks — and what the door lets through."""
    return {"bar_time": bar_time, "bar_readable": bar_time is not None, "degraded": [], "stale": [],
            "missing": [], "feeds": {}}


def usable_venue_contract(symbols=("BTCUSDT",), *, verified_at):
    """A venue contract PASS as `venue_contract.entry_fact` reads one (PR4b): this code's contract
    version, verified at ``verified_at``, covering ``symbols``. What the entry doors' tests hand every
    door by default, so each test still closes exactly one; the contract door has its own tests."""
    from runtime.mvp_runtime.crypto import venue_contract as vc

    return {"recorded": True, "status": vc.STATUS_PASS, "contract_version": vc.CONTRACT_VERSION,
            "verified_at": verified_at, "symbols": list(symbols), "failed_checks": [],
            "record_sha256": "sha256:" + "c" * 64}


def record_venue_contract(root, symbols=("BTCUSDT",), *, verified_at, failed=()):
    """A real decided verification in ``root``, as the sentinel writes it (PR4b): PASS, or FAIL on the
    judged checks named in ``failed``. For the tests that read the record through the doors' own
    reader rather than hand the doors a fact."""
    from runtime.mvp_runtime.crypto import venue_contract as vc

    checks = [vc._check(check, vc.STATUS_FAIL if check in failed else vc.STATUS_PASS, expected="test")
              for check in vc.JUDGED_CHECKS]
    record = vc.build_record(status=vc.STATUS_FAIL if failed else vc.STATUS_PASS, checks=checks,
                             symbols=list(symbols), now=verified_at)
    vc._write_json(vc.contract_path(root), record, code="VENUE_CONTRACT_LOCKED", label="test record")
    return record


def gate_stage():
    """A binding stage record at the live rung, with the approval the gate's profile requires."""
    from runtime.mvp_runtime.crypto.execution_stage import StageStatus

    return StageStatus(
        stage="LIVE_AUTONOMOUS", valid=True, reason_code=None, recorded_stage="LIVE_AUTONOMOUS",
        stage_id="stage_test", record_sha256="sha256:" + "5" * 64, approval_id="approval_stage_test",
    )


# The artifact a test arm pairs with its candidate when the test names none (PR3a). A stand-in: a
# pool entry that must survive a real pool read carries a real stamp (`stamped_pool_entry`), and a
# test arming it passes that stamp as ``artifacts``.
ARM_TEST_ARTIFACT = "sha256:" + "a" * 64
_ARM_DEFAULT_ARTIFACTS = object()


def stamped_pool_entry(entry):
    """``entry`` as the promotion door installs one (PR3a): the artifact's carried parts (empty
    evidence unless the entry brings its own) and the stamp its content really hashes to, so a pool
    read accepts it. Stamp AFTER every change to the hashed fields, or the read refuses the pool."""
    from runtime.mvp_runtime.crypto import strategy_artifact as artifact_mod

    stamped = {**entry}
    stamped.setdefault(artifact_mod.ARTIFACT_FIELD, artifact_mod.carried_parts({}))
    stamped[artifact_mod.ARTIFACT_SHA256_FIELD] = artifact_mod.artifact_sha256(
        artifact_mod.from_pool_entry(stamped))
    return stamped


def live_arm_approval(candidate_ids=("cand_1",), rule_hashes=("deadbeef",), *,
                      artifacts=_ARM_DEFAULT_ARTIFACTS,
                      decided_at="2026-07-27T23:50:00Z", expires_at="2026-07-28T00:05:00Z",
                      live_tier="LIVE", **overrides):
    """An APPROVED promotion approval that arms ``candidate_ids`` at ``live_tier``, in the shape
    `approval.record_decision` leaves one and under the id `permission` derives from its fingerprint
    — for tests of the gate's order-time verification of an arm (PR2c-2b). ``overrides`` replace
    top-level fields after the id is derived.

    ``artifacts`` are the artifact hashes it pairs with ``candidate_ids`` (PR3a): by default
    :data:`ARM_TEST_ARTIFACT` for each; None signs no pairs, as an approval asked before v5 did."""
    from runtime.mvp_runtime import approval as approval_mod  # noqa: F401 — puts `lib/` on the path
    from runtime.mvp_runtime.permission import (
        STRATEGY_POOL_LIVE_TARGET_REF, STRATEGY_POOL_PAPER_TARGET_REF,
    )
    from runtime.read_only_kernel import integrity
    from lib.action_fingerprint import compute_action_fingerprint

    snapshot = {
        "schema_version": "action_fingerprint_payload.v0.1",
        "action_type": "crypto.strategy_pool.promotion",
        "permission_scope": "RUNTIME_GOVERNANCE",
        "target_ref": STRATEGY_POOL_LIVE_TARGET_REF if live_tier == "LIVE" else STRATEGY_POOL_PAPER_TARGET_REF,
        "task_id": "task_arm_test", "task_revision": 1, "core_context_binding_id": "ccb_arm_test",
        "requester_ref": "thomas.prime", "tool_id": None, "program_id": None,
        "data_scope": ["crypto.active_strategy_pool", "crypto.strategy_candidates"],
        "content_sha256": "sha256:" + "c" * 64, "amount_decimal": None, "currency": None,
        "normalized_parameters": {
            "candidate_ids": sorted(candidate_ids), "strategy_ids": ["S001"],
            "rule_hashes": sorted(rule_hashes), "keep_active": False, "live_tier": live_tier,
            **({} if artifacts is None else {"artifacts": sorted(
                [c, a] for c, a in zip(candidate_ids, (
                    [ARM_TEST_ARTIFACT] * len(candidate_ids)
                    if artifacts is _ARM_DEFAULT_ARTIFACTS else list(artifacts))))}),
        },
        "expires_at": expires_at,
    }
    fingerprint = compute_action_fingerprint(snapshot)
    record = {
        "approval_id": integrity.short_id("approval", {"action_fingerprint": fingerprint}),
        "status": "APPROVED",
        "action_fingerprint": fingerprint,
        "approved_action_snapshot": snapshot,
        "approver": {
            "required_approver": "Thomas", "approved_by": "Thomas", "verification_status": "VERIFIED",
            "identity_verification_method": "telegram_private_control_channel",
            "verification_ref": "telegram:private_chat:test:msg-1",
        },
        "decision": {"decision_reason": "Approved by Thomas on the verified control channel.",
                     "decided_at": decided_at},
        "validity": {"issued_at": "2026-07-27T23:45:00Z", "expires_at": expires_at},
    }
    record.update(overrides)
    return record


def approved_snapshot(intent, *, purpose=None, venue=None, now="2026-07-25T12:00:00Z", decided_at=None):
    """``(bound_intent, snapshot)``: a snapshot the real gate sealed for ``intent`` on passing
    checks and a whole profile — for tests whose subject is what happens AFTER the gate.

    Judged at ``decided_at``, by default the wall clock, so a send right after is within the age
    bound (PR2c-1) whatever ``now`` the test's fire runs at."""
    from runtime.mvp_runtime import timeutil
    from runtime.mvp_runtime.crypto import pre_order_gate as g
    from runtime.mvp_runtime.crypto.execution_stage import (
        PURPOSE_AUTONOMOUS, PURPOSE_PROBE, PURPOSE_TESTNET,
    )
    from runtime.mvp_runtime.crypto.state import VENUE_MAINNET, VENUE_TESTNET

    purpose = purpose or PURPOSE_AUTONOMOUS
    authority = {
        # An arm whose approval the route verified (PR2c-2b).
        PURPOSE_AUTONOMOUS: {"kind": g.AUTHORITY_LIVE_ARM, "strategy_id": "S001",
                             "approval_id": "approval_arm_test",
                             "approval_fingerprint": "sha256:" + "a" * 64,
                             g.LIVE_ARM_VERIFIED_FIELD: True},
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
        decided_at=decided_at or timeutil.utc_now_iso(),
    )
    assert snapshot["approved"], snapshot["failed_checks"]
    return g.bind_intent(intent, snapshot), snapshot
