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
    env_only_authorization,
    select_env_gated,
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


@pytest.mark.parametrize("bad", ["brave\n", "Brave", "../evil", "a/b", "", "-leading"])
def test_provider_id_pattern_is_anchored_at_both_ends(tmp_path, bad):
    """`$` also matches before a trailing newline, so "brave\\n" passed the pattern and
    would have produced a grant filename containing a newline."""
    with pytest.raises(SafetyGateBlocked) as exc:
        activation_path(tmp_path, bad)
    assert exc.value.reason_code == "INVALID_PROVIDER_ID"


@pytest.mark.parametrize("reserved", ["con", "nul", "com1", "lpt9", "aux.json"])
def test_windows_reserved_device_names_are_refused(tmp_path, reserved):
    """Windows resolves these to devices wherever they appear as a basename, so a grant
    filed under one would not be a normal file. Named explicitly rather than left to fail
    confusingly at open() time."""
    with pytest.raises(SafetyGateBlocked) as exc:
        activation_path(tmp_path, reserved)
    assert exc.value.reason_code == "INVALID_PROVIDER_ID"


def test_real_provider_ids_still_resolve(tmp_path):
    for provider in ("google_ai_studio", "brave_search", "telegram", "workspace.writer",
                     "approval_consumption"):
        assert activation_path(tmp_path, provider).name == f"{provider}.json"


def test_deleting_the_activation_record_revokes_a_live_grant(tmp_path):
    """The whole point of the re-stat: a long-running process holding a grant must stop at
    its next egress once the operator deletes the record — expiry was previously the only
    thing that could stop it."""
    _write_activation(tmp_path, _valid_record(tmp_path))
    auth = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert auth.activation_path is not None
    assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)  # live

    activation_path(tmp_path, PROVIDER).unlink()                                     # revoke
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_REVOKED"


def test_replacing_the_activation_record_invalidates_the_old_grant(tmp_path):
    """A replaced/edited record no longer matches the granted hash: the old in-memory
    Authorization must not keep operating on the strength of a grant that changed."""
    _write_activation(tmp_path, _valid_record(tmp_path))
    auth = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)

    _write_activation(tmp_path, _valid_record(tmp_path, expires_at="2027-06-30T00:00:00Z"))
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_CHANGED"
    # The current record itself still authorizes fresh — re-authorize is the remedy.
    fresh = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert_authorization(fresh, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)


def test_corrupting_the_activation_record_stops_a_live_grant(tmp_path):
    _write_activation(tmp_path, _valid_record(tmp_path))
    auth = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    activation_path(tmp_path, PROVIDER).write_text("{not json", encoding="utf-8")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_CHANGED"


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


def test_activation_with_malformed_timestamp_cannot_be_minted():
    """Lexicographic time compares are only correct for the fixed Z form; a '+00:00' or
    sub-second grant could compare wrong in either direction (including never-expires)."""
    with pytest.raises(SafetyGateBlocked) as exc:
        build_activation_record(
            flags=[NETWORK_ACCESS], provider_id="telegram",
            activated_at="2026-07-01T00:00:00+00:00", expires_at="2026-12-31T23:59:59Z",
            evidence_ref="ev.md", authority_level="P2",
        )
    assert exc.value.reason_code == "ACTIVATION_MALFORMED"


def test_escaping_evidence_ref_fails_closed(tmp_path):
    """An absolute or '..' evidence_ref would let ANY existing file on the machine satisfy
    the evidence-exists check; the ref must stay inside the repository."""
    record = build_activation_record(
        flags=[NETWORK_ACCESS], provider_id="telegram",
        activated_at="2026-07-01T00:00:00Z", expires_at="2999-12-31T23:59:59Z",
        evidence_ref="../outside-evidence.md", authority_level="P2",
    )
    (tmp_path.parent / "outside-evidence.md").write_text("x", encoding="utf-8")
    path = activation_path(tmp_path, "telegram")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(SafetyGateBlocked) as exc:
        authorize([NETWORK_ACCESS], provider_id="telegram", now="2026-07-16T00:00:00Z", root=tmp_path)
    assert exc.value.reason_code == "EVIDENCE_INVALID"


# --- select_env_gated: the live-trading exception ----------------------------------
#
# Thomas removed the per-machine grant for live trading on 2026-07-28: `MVP_LIVE_TRADING=real`
# is now the whole gate for that one capability. These tests pin BOTH halves of that decision —
# that the env alone opens it, and that nothing else can reach the weaker door.


def test_env_gated_returns_the_inert_default_without_the_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("PROBE_GATE_ENV", raising=False)
    built = []
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: built.append(auth) or "capable",
    )
    assert got == "inert"
    assert built == []


@pytest.mark.parametrize("value", ["", "   ", "something-else", "REALLY", "true", "1"])
def test_env_gated_opens_for_the_exact_value_and_nothing_near_it(value, monkeypatch, tmp_path):
    """Dropping the grant makes this string the entire gate, so 'close enough' must not open
    it. `true` and `1` are in the list on purpose: they are what someone reaches for when they
    believe they are setting a boolean."""
    monkeypatch.setenv("PROBE_GATE_ENV", value)
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: "capable",
    )
    assert got == "inert"


def test_env_gated_opens_with_no_activation_record_anywhere(monkeypatch, tmp_path):
    """The point of the change, stated as a test: the opt-in alone builds the capable thing.
    `tmp_path` holds no activations directory at all, and this must still succeed — under
    `select_gated` the identical call raised ACTIVATION_MISSING."""
    monkeypatch.setenv("PROBE_GATE_ENV", "  REAL  ")
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: auth,
    )
    assert isinstance(got, Authorization)
    assert got.provider_id == "probe"
    assert got.flags == (NETWORK_ACCESS,)


def test_env_gated_authorization_still_re_checks_the_env_at_egress(monkeypatch, tmp_path):
    """Defense in depth survives the change in weakened form. The grant file was re-read at
    every egress so deleting it revoked mid-flight; there is no file now, so the re-check
    re-reads the env. Same shape, and it still fails closed."""
    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    auth = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: None,
        gated_factory=lambda a: a,
    )
    assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="probe", now=NOW)

    monkeypatch.setenv("PROBE_GATE_ENV", "no")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="probe", now=NOW)
    assert exc.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


def test_env_gated_authorization_is_still_bound_to_its_provider_and_flags(monkeypatch):
    """Removing the grant removed one requirement, not the others. An env-only authorization
    must not become a skeleton key: wrong provider and insufficient flags still block."""
    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    auth = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: None,
        gated_factory=lambda a: a,
    )
    with pytest.raises(SafetyGateBlocked) as wrong_provider:
        assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="other", now=NOW)
    assert wrong_provider.value.reason_code == "PROVIDER_NOT_AUTHORIZED"
    with pytest.raises(SafetyGateBlocked) as missing_flag:
        assert_authorization(
            auth, required_flags=(NETWORK_ACCESS, MODEL_INVOCATION), provider_id="probe", now=NOW
        )
    assert missing_flag.value.reason_code == "FLAG_NOT_ENABLED"


def test_the_env_only_gate_has_exactly_the_capabilities_thomas_named():
    """The containment test, and the reason this is a separate function rather than a flag on
    `select_gated`. Thomas has relaxed the gate for THREE capabilities — live trading
    (2026-07-28), the candle archive (2026-08-04) and the Naver research lane
    (2026-08-09); a later change that quietly
    moves model_invocation or a search tool onto the same weaker door would be invisible in
    review. Every call site is listed here, so adding one is a decision someone has to make on
    purpose.

    Both spellings are collected, and that is not defensive padding. The first version matched
    only `ast.Attribute` — `safety_gate.select_env_gated(...)` — so a caller written
    `from ..safety_gate import select_env_gated` and then called bare is an `ast.Name` and scored
    ZERO. That import style is the idiomatic one in this repo: `workspace.py`, `providers.py`,
    `tools.py`, `operator.py`, `crypto/market_data.py` and `live_order.py` itself all use it.
    A containment test the common idiom walks straight through contains nothing.

    `env_only_authorization` is pinned by the same sweep, because it is public and a caller can
    build one directly and hand it to any capable constructor without naming `select_env_gated`
    at all — the same weaker door reached by a different handle.
    """
    import ast
    import pathlib

    watched = {"select_env_gated", "env_only_authorization"}
    repo = pathlib.Path(__file__).resolve().parents[1]
    callers = set()
    for path in (repo / "runtime").rglob("*.py"):
        if path.name == "safety_gate.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            named = (
                (isinstance(node, ast.Attribute) and node.attr in watched)
                or (isinstance(node, ast.Name) and node.id in watched)
                or (isinstance(node, ast.ImportFrom)
                    and any(a.name in watched for a in node.names))
            )
            if named:
                callers.add(path.relative_to(repo).as_posix())
    assert callers == {
        "runtime/mvp_runtime/crypto/live_execution.py",   # the order adapter
        "runtime/mvp_runtime/crypto/live_pnl.py",         # the realized-P&L ledger
        "runtime/mvp_runtime/crypto/live_position.py",    # the position book
        "runtime/mvp_runtime/crypto/live_order.py",       # the daily submission counter
        "runtime/mvp_runtime/crypto/live_promotion.py",   # the canary evidence registry
        # The candle archive (Thomas, 2026-08-04) — the second capability, and the first that
        # is not live trading. Added on purpose, which is what this test exists to force.
        #
        # The grant's 30-day TTL cannot bound a job that must run for months against a ROLLING
        # window: a renewal gap is not a pause, it is a hole nothing can fill. Half of live
        # trading's argument does not transfer (nothing here can be trapped open) and neither
        # does its compensating control (no kill switch) — but the other side of the ledger is
        # the side live trading did not have. This reads PUBLIC candles with no key, sends
        # nothing, orders nothing, and feeds nothing. `select_candle_archive_collector` carries
        # the full reasoning.
        "runtime/mvp_runtime/crypto/market_data.py",      # the candle archive collector
        # The Naver research lane (Thomas, 2026-08-09) — the third capability, and the second
        # that is not trading. Added on purpose, which is what this test exists to force.
        #
        # Closest to the candle archive's argument, not live trading's: nothing here can be
        # trapped open, so expiry is merely disruptive rather than dangerous. What the 30-day
        # TTL breaks is a WEEKLY schedule — one renewal gap silently costs a week of content
        # ideas, with no error anywhere, and the operator discovers it by noticing an absence.
        # Unlike the candle archive this one does send credentials (Naver's keyword API is not
        # public), which is the side of the ledger that argues the other way and was weighed:
        # the capability is read-only, orders nothing, publishes nothing, and its worst failure
        # is a missing draft. `naver_research`'s module docstring carries the full reasoning.
        "runtime/mvp_runtime/naver_research.py",          # the blog content lane's research tools
    }, callers


def test_the_containment_sweep_sees_a_bare_name_call(tmp_path):
    """The sweep's own blind spot, checked directly rather than trusted.

    Written as a unit on the matcher because the real test asserts an exact caller set — it can
    only fail on a NEW caller, so nothing in it would notice that the matcher had stopped seeing
    a whole calling convention. This is the assertion that would have caught it."""
    import ast

    watched = {"select_env_gated", "env_only_authorization"}
    module = ast.parse(
        "from ..safety_gate import select_env_gated\n"
        "def pick():\n"
        "    return select_env_gated(env_var='X', opt_in_value='real', flags=(),\n"
        "                            provider_id='p', default_factory=None, gated_factory=None)\n"
    )
    hits = [
        node for node in ast.walk(module)
        if (isinstance(node, ast.Attribute) and node.attr in watched)
        or (isinstance(node, ast.Name) and node.id in watched)
        or (isinstance(node, ast.ImportFrom) and any(a.name in watched for a in node.names))
    ]
    assert hits, "a `from ... import select_env_gated` caller must not be invisible to the sweep"


def test_the_suite_isolates_every_gate_opt_in_env_var():
    """`tests/conftest.py`'s `_GATE_ENV_VARS` is the suite's whole defense against inheriting
    the operator's gate opt-ins, and it was maintained by hand — one entry per remembered
    capability. A verified probe (2026-08-09) showed what that is worth: with the operator's
    opt-ins exported, every listed var read as None while MVP_CANDLE_ARCHIVE='hyperliquid'
    and MVP_MARKET_DATA='binance_futures' leaked straight through. The archive is env-only,
    so the leak hands any test that reaches its selector a REAL egress-capable collector
    holding a genuine authorization; the grant-gated vars contain nothing on the machine
    that matters either, because the operator's machine is exactly where the grants exist.

    So the list's floor is derived from the selector call sites themselves: every
    ``env_var=`` handed to ``select_gated`` / ``select_env_gated`` / ``select_gated_chain``
    anywhere in ``runtime/`` must appear in ``_GATE_ENV_VARS``. A new gated capability whose
    author forgets the conftest entry fails here instead of shipping the same hole again.

    Subset, not equality, on purpose: a conftest entry for a retired capability is a
    harmless no-op delenv, and requiring equality would couple removing a capability to a
    test that exists for the opposite direction.

    Same fail-closed posture as the callers sweep above: an ``env_var=`` this sweep cannot
    resolve to a string is an assertion failure, never a silent skip."""
    import ast
    import pathlib

    from tests import conftest as suite_conftest

    selectors = {"select_gated", "select_env_gated", "select_gated_chain"}
    repo = pathlib.Path(__file__).resolve().parents[1]
    modules = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in (repo / "runtime").rglob("*.py")
    }

    def own_constants(tree: ast.Module) -> dict[str, str]:
        # Module-level `NAME = "literal"` — the repo's idiom for env var names.
        return {
            target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)
        }

    constants = {path: own_constants(tree) for path, tree in modules.items()}

    def imported_constants(path: pathlib.Path, tree: ast.Module) -> dict[str, str]:
        # One `from <module> import NAME` hop — `trial.py` borrows consumption's ENV_VAR and
        # the live surface shares `live_pnl`'s LIVE_TRADING_ENV this way.
        resolved: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            base = path.parents[node.level - 1] if node.level else repo
            source = base.joinpath(*node.module.split(".")).with_suffix(".py")
            source_constants = constants.get(source, {})
            for alias in node.names:
                if alias.name in source_constants:
                    resolved[alias.asname or alias.name] = source_constants[alias.name]
        return resolved

    opt_ins: dict[str, str] = {}
    for path, tree in modules.items():
        if path.name == "safety_gate.py":
            continue  # the selectors' own bodies pass env_var through; they are not opt-ins
        names = {**imported_constants(path, tree), **constants[path]}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not ((isinstance(func, ast.Attribute) and func.attr in selectors)
                    or (isinstance(func, ast.Name) and func.id in selectors)):
                continue
            site = f"{path.relative_to(repo).as_posix()}:{node.lineno}"
            for keyword in node.keywords:
                if keyword.arg != "env_var":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    opt_ins.setdefault(value.value, site)
                elif isinstance(value, ast.Name) and value.id in names:
                    opt_ins.setdefault(names[value.id], site)
                else:
                    raise AssertionError(
                        f"{site} passes env_var= in a form this sweep cannot resolve to a "
                        "string; use a literal or a module-level constant so the isolation "
                        "check can see the opt-in"
                    )

    for known in ("MVP_LIVE_TRADING", "MVP_CANDLE_ARCHIVE", "MVP_NAVER_RESEARCH",
                  "MVP_MARKET_DATA"):
        assert known in opt_ins, f"the sweep stopped seeing {known} — its matcher went blind"
    missing = {var: site for var, site in sorted(opt_ins.items())
               if var not in suite_conftest._GATE_ENV_VARS}
    assert not missing, (
        "gate opt-in env var(s) missing from tests/conftest.py _GATE_ENV_VARS — the suite "
        f"would inherit these from the operator's shell: {missing}"
    )


# --- the env-only gate cannot be reached from a grant record ----------------------

def test_a_grant_record_cannot_smuggle_itself_onto_the_env_gate(tmp_path):
    """The egress re-check must not be selectable by the content of the record it re-checks.

    `assert_authorization` used to branch on `evidence_ref.startswith("env_only:")`, and
    `evidence_ref` is copied verbatim out of the operator's activation record. The sentinel
    passes the path validation — no drive, not absolute, no `..` — and a file of that name is
    legal on Linux, so a record could name itself onto the env branch and skip its own file
    re-read. Deleting the grant then stopped revoking it, on providers this decision
    deliberately KEPT on a grant (`binance_futures_account`).
    """
    smuggled = "env_only:MVP_LIVE_TRADING=real"
    _write_activation(tmp_path, _valid_record(tmp_path, evidence_rel=smuggled))
    authorization = authorize(FLAGS, provider_id=PROVIDER, now=NOW, root=tmp_path)
    assert authorization.evidence_ref == smuggled
    assert authorization.env_gate is None, "a record must never reach the env-only branch"

    # The grant file is the revocation mechanism; deleting it must still stop the next egress,
    # whatever the record called itself.
    activation_path(tmp_path, PROVIDER).unlink()
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(authorization, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_REVOKED"


def test_the_env_gate_field_is_what_drives_the_recheck(monkeypatch):
    """...and it is set only by `env_only_authorization`, never by `authorize`."""
    authorization = env_only_authorization(
        flags=(NETWORK_ACCESS,), provider_id="p", env_var="MVP_X", opt_in_value="real",
    )
    assert authorization.env_gate == ("MVP_X", "real")

    monkeypatch.setenv("MVP_X", "real")
    assert_authorization(authorization, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)

    monkeypatch.setenv("MVP_X", "true")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(authorization, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)
    assert exc.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


def test_the_opt_in_value_is_matched_case_insensitively_on_both_sides(monkeypatch):
    """Only the env value was normalised. A caller passing "REAL" would leave the gate shut
    forever with nothing saying why — and the egress re-check has to agree with the selector."""
    monkeypatch.setenv("MVP_X", "real")
    built = select_env_gated(
        env_var="MVP_X", opt_in_value="REAL", flags=(NETWORK_ACCESS,), provider_id="p",
        default_factory=lambda: "inert", gated_factory=lambda auth: auth,
    )
    assert built != "inert", "an uppercase opt_in_value must still open the gate"
    assert_authorization(built, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)
