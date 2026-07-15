"""Safety-Flag Gate tests — the enforced model/network chokepoint.

Every path is fail-closed: a missing, malformed, tampered, expired, wrong-provider, or
flag-insufficient activation record must raise SafetyGateBlocked. A valid,
integrity-consistent, evidence-backed record authorizes; tampering with any field breaks
the self-hash. No network and no secrets are involved.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import SafetyGateBlocked
from runtime.mvp_runtime.safety_gate import (
    ACTIVATION_REL,
    MODEL_INVOCATION,
    NETWORK_ACCESS,
    Authorization,
    assert_authorization,
    authorize,
    build_activation_record,
)

NOW = "2026-07-15T00:00:00Z"
PROVIDER = "google_ai_studio"
FLAGS = (MODEL_INVOCATION, NETWORK_ACCESS)


def _write_activation(root, record: dict) -> None:
    path = root / ACTIVATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def _valid_record(root, *, evidence_rel="ev.md", flags=FLAGS,
                  activated_at="2026-07-01T00:00:00Z", expires_at="2026-12-31T23:59:59Z",
                  provider_id=PROVIDER):
    (root / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    return build_activation_record(
        flags=list(flags), provider_id=provider_id, activated_at=activated_at,
        expires_at=expires_at, evidence_ref=evidence_rel, authority_level="P4",
    )


# --- authorize(): fail-closed paths -----------------------------------------

def test_missing_record_blocks(tmp_path):
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_unparseable_record_blocks(tmp_path):
    path = tmp_path / ACTIVATION_REL
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MALFORMED"


def test_missing_field_blocks(tmp_path):
    record = _valid_record(tmp_path)
    del record["expires_at"]
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MALFORMED"


def test_tampered_flags_break_self_hash(tmp_path):
    record = _valid_record(tmp_path, flags=(MODEL_INVOCATION,))
    # Hand-edit to grant an extra capability without recomputing the hash — must be caught.
    record["flags"] = [MODEL_INVOCATION, NETWORK_ACCESS]
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_TAMPERED"


def test_tampered_expiry_breaks_self_hash(tmp_path):
    record = _valid_record(tmp_path, expires_at="2026-07-16T00:00:00Z")
    record["expires_at"] = "2099-01-01T00:00:00Z"  # extend without re-signing
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_TAMPERED"


def test_expired_record_blocks(tmp_path):
    record = _valid_record(tmp_path, expires_at="2026-07-10T00:00:00Z")
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_EXPIRED"


def test_not_yet_active_blocks(tmp_path):
    record = _valid_record(tmp_path, activated_at="2026-08-01T00:00:00Z")
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_NOT_YET_ACTIVE"


def test_wrong_provider_blocks(tmp_path):
    record = _valid_record(tmp_path, provider_id="some_other_provider")
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "PROVIDER_NOT_AUTHORIZED"


def test_flag_not_enabled_blocks(tmp_path):
    record = _valid_record(tmp_path, flags=(MODEL_INVOCATION,))  # network not enabled
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "FLAG_NOT_ENABLED"


def test_missing_evidence_blocks(tmp_path):
    record = _valid_record(tmp_path)
    (tmp_path / "ev.md").unlink()  # evidence referenced but absent
    _write_activation(tmp_path, record)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "EVIDENCE_MISSING"


# --- authorize(): happy path -------------------------------------------------

def test_valid_activation_authorizes(tmp_path):
    _write_activation(tmp_path, _valid_record(tmp_path))
    auth = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert isinstance(auth, Authorization)
    assert auth.provider_id == PROVIDER
    assert set(auth.flags) == set(FLAGS)
    assert auth.activation_sha256.startswith("sha256:")


def test_build_rejects_unknown_flag():
    with pytest.raises(SafetyGateBlocked) as exc:
        build_activation_record(
            flags=["make_coffee"], provider_id=PROVIDER, activated_at=NOW,
            expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md", authority_level="P4",
        )
    assert exc.value.reason_code == "UNKNOWN_FLAG"


# --- assert_authorization(): egress re-check --------------------------------

def test_assert_rejects_non_authorization():
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(None, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_assert_rejects_expired_since_grant():
    auth = Authorization(flags=FLAGS, provider_id=PROVIDER, activation_sha256="sha256:x",
                         expires_at="2026-07-14T00:00:00Z", evidence_ref="ev.md")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_EXPIRED"


def test_assert_rejects_wrong_provider():
    auth = Authorization(flags=FLAGS, provider_id="other", activation_sha256="sha256:x",
                         expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "PROVIDER_NOT_AUTHORIZED"


def test_assert_passes_for_valid_grant():
    auth = Authorization(flags=FLAGS, provider_id=PROVIDER, activation_sha256="sha256:x",
                         expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md")
    assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)  # no raise
