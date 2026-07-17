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
    ACTIVATIONS_DIR_REL,
    MODEL_INVOCATION,
    NETWORK_ACCESS,
    Authorization,
    activation_path,
    assert_authorization,
    authorize,
    build_activation_record,
    select_gated,
)

NOW = "2026-07-15T00:00:00Z"
PROVIDER = "google_ai_studio"
FLAGS = (MODEL_INVOCATION, NETWORK_ACCESS)


def _write_activation(root, record: dict, *, as_provider: str | None = None) -> None:
    """Write a grant to its provider's own file. ``as_provider`` deliberately files the
    record under a DIFFERENT provider's name — the copied-file attack."""
    path = activation_path(root, as_provider or record["provider_id"])
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
    path = activation_path(tmp_path, PROVIDER)
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


def test_a_grant_for_another_provider_does_not_authorize_this_one(tmp_path):
    """Each provider is looked up by its own grant: another provider's activation is simply
    not this provider's activation."""
    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="some_other_provider"))
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_copying_a_grant_under_another_providers_name_authorizes_nothing(tmp_path):
    """The filename is only an index; the record's content is the authority. Copying
    google_ai_studio's grant to groq.json must not grant groq anything — and the inner
    provider_id is covered by the self-hash, so it cannot be edited to agree."""
    record = _valid_record(tmp_path, provider_id=PROVIDER)
    _write_activation(tmp_path, record, as_provider="some_other_provider")
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="some_other_provider", now=NOW, root=tmp_path)
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


# --- select_gated: the chokepoint every gated capability shares ---------------------


def test_select_gated_returns_the_inert_default_without_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("PROBE_GATE_ENV", raising=False)
    built = []
    got = select_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: built.append(auth) or "capable",
        root=tmp_path,
    )
    assert got == "inert"
    assert built == []  # the capable implementation was never constructed


@pytest.mark.parametrize("value", ["", "   ", "something-else", "REALLY"])
def test_select_gated_falls_back_to_inert_on_any_other_value(value, monkeypatch, tmp_path):
    """An unrecognised opt-in must fall back to inert, never to the capable path."""
    monkeypatch.setenv("PROBE_GATE_ENV", value)
    got = select_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: "capable",
        root=tmp_path,
    )
    assert got == "inert"


def test_select_gated_accepts_the_opt_in_case_insensitively(monkeypatch, tmp_path):
    """The value is normalised, so REAL/Real/real behave identically — and all of them
    still have to pass the gate."""
    monkeypatch.setenv("PROBE_GATE_ENV", "  REAL  ")
    with pytest.raises(SafetyGateBlocked):
        select_gated(
            env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
            provider_id="probe",
            default_factory=lambda: "inert",
            gated_factory=lambda auth: "capable",
            root=tmp_path,
        )


def test_select_gated_never_builds_the_capable_thing_when_the_gate_is_shut(monkeypatch, tmp_path):
    """The property the whole extraction exists for: opting in is not enough. With no
    activation record the gate raises and `gated_factory` is never called, so a capable
    implementation cannot come into existence unauthorized."""
    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    built = []
    with pytest.raises(SafetyGateBlocked) as exc:
        select_gated(
            env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
            provider_id="probe",
            default_factory=lambda: "inert",
            gated_factory=lambda auth: built.append("built") or "capable",
            root=tmp_path,
        )
    assert exc.value.reason_code == "ACTIVATION_MISSING"
    assert built == []


def test_select_gated_passes_the_authorization_to_the_factory(monkeypatch, tmp_path):
    """When the gate does open, the factory receives the grant — so the object it builds
    can re-verify it at egress (defense in depth)."""
    evidence = tmp_path / "evidence.md"
    evidence.write_text("operator decision", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS], provider_id="probe", authority_level="P2",
        evidence_ref="evidence.md",
        activated_at="2026-01-01T00:00:00Z", expires_at="2999-01-01T00:00:00Z",
    )
    path = activation_path(tmp_path, "probe")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")

    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    received = []
    got = select_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: received.append(auth) or "capable",
        now="2026-07-16T12:00:00Z", root=tmp_path,
    )
    assert got == "capable"
    assert len(received) == 1
    assert isinstance(received[0], Authorization)
    assert received[0].provider_id == "probe"
    assert NETWORK_ACCESS in received[0].flags


# --- multi-provider: the point of one grant per provider ----------------------


def test_two_providers_can_be_authorized_at_once(tmp_path):
    """The capability this layout exists for: a specialist on one vendor and a validator on
    another must BOTH hold a grant in the same run. A single shared record could not express
    it — it had one provider_id field."""
    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="google_ai_studio"))
    _write_activation(tmp_path, _valid_record(tmp_path, evidence_rel="ev2.md", provider_id="groq"))

    gemini = authorize(FLAGS, provider_id="google_ai_studio", now=NOW, root=tmp_path)
    groq = authorize(FLAGS, provider_id="groq", now=NOW, root=tmp_path)
    assert gemini.provider_id == "google_ai_studio"
    assert groq.provider_id == "groq"
    # Independent grants: different records, different evidence.
    assert gemini.activation_sha256 != groq.activation_sha256
    assert gemini.evidence_ref != groq.evidence_ref


def test_one_providers_grant_never_speaks_for_another(tmp_path):
    """Authorizing a second provider must not implicitly authorize a third."""
    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="google_ai_studio"))
    _write_activation(tmp_path, _valid_record(tmp_path, evidence_rel="ev2.md", provider_id="groq"))
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="cerebras", now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_one_corrupt_grant_does_not_disable_the_others(tmp_path):
    """Fail-closed per provider, not globally: a damaged Groq grant must not take Gemini
    down with it."""
    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="google_ai_studio"))
    broken = activation_path(tmp_path, "groq")
    broken.parent.mkdir(parents=True, exist_ok=True)
    broken.write_text("{not json", encoding="utf-8")

    assert authorize(FLAGS, provider_id="google_ai_studio", now=NOW, root=tmp_path).provider_id == "google_ai_studio"
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="groq", now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MALFORMED"


def test_expiring_one_grant_leaves_the_other_valid(tmp_path):
    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="google_ai_studio"))
    _write_activation(tmp_path, _valid_record(
        tmp_path, evidence_rel="ev2.md", provider_id="groq", expires_at="2026-07-10T00:00:00Z"))
    assert authorize(FLAGS, provider_id="google_ai_studio", now=NOW, root=tmp_path)
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="groq", now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_EXPIRED"


def test_grants_carry_different_flags_per_provider(tmp_path):
    """Each provider gets only what it needs: the writer needs filesystem_write and no
    network; a model provider the reverse."""
    from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE

    _write_activation(tmp_path, _valid_record(tmp_path, provider_id="google_ai_studio", flags=FLAGS))
    _write_activation(tmp_path, _valid_record(
        tmp_path, evidence_rel="ev2.md", provider_id="workspace.writer", flags=(FILESYSTEM_WRITE,)))

    writer = authorize((FILESYSTEM_WRITE,), provider_id="workspace.writer", now=NOW, root=tmp_path)
    assert set(writer.flags) == {FILESYSTEM_WRITE}
    # The writer's grant does not carry network — asking for it must fail closed.
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="workspace.writer", now=NOW, root=tmp_path)
    assert exc.value.reason_code == "FLAG_NOT_ENABLED"


# --- provider_id becomes a filename, so it is confined -----------------------


@pytest.mark.parametrize("bad", [
    "../escape", "a/b", "a\b", "/abs", "C:/x", "..", "", "  ", "UPPER", ".hidden", "x/../y",
])
def test_a_provider_id_can_name_a_grant_never_a_location(bad, tmp_path):
    with pytest.raises(SafetyGateBlocked) as exc:
        activation_path(tmp_path, bad)
    assert exc.value.reason_code == "INVALID_PROVIDER_ID"


def test_authorize_rejects_a_path_shaped_provider_id(tmp_path):
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize(FLAGS, provider_id="../../etc/passwd", now=NOW, root=tmp_path)
    assert exc.value.reason_code == "INVALID_PROVIDER_ID"


def test_build_rejects_a_path_shaped_provider_id():
    with pytest.raises(SafetyGateBlocked) as exc:
        build_activation_record(
            flags=[MODEL_INVOCATION], provider_id="../evil", activated_at=NOW,
            expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md", authority_level="P4",
        )
    assert exc.value.reason_code == "INVALID_PROVIDER_ID"


@pytest.mark.parametrize("good", ["google_ai_studio", "brave_search", "telegram", "workspace.writer", "groq"])
def test_every_real_provider_id_is_a_valid_grant_name(good, tmp_path):
    path = activation_path(tmp_path, good)
    assert path.name == f"{good}.json"
    assert path.parent == (tmp_path / ACTIVATIONS_DIR_REL).resolve()
