"""Operator safety-flag activation helper tests (scripts/activate_safety_flag.py).

The helper writes a local, gitignored activation record; the same gate the runtime uses
must then authorize it. Everything runs against a tmp root (no repo state touched)."""

from __future__ import annotations

import json

from runtime.mvp_runtime import safety_gate
from scripts.activate_safety_flag import main


def _activation(tmp_path, provider_id="brave_search"):
    """The helper writes one grant per provider, so a record is looked up by its own name."""
    return safety_gate.activation_path(tmp_path, provider_id)


def test_helper_writes_a_gate_accepted_activation(tmp_path):
    rc = main([
        "--provider-id", "brave_search", "--flags", "network_access",
        "--authority-level", "P1", "--reason", "test decision",
        "--ttl-minutes", "60", "--root", str(tmp_path),
    ])
    assert rc == 0
    record = json.loads(_activation(tmp_path).read_text(encoding="utf-8"))
    assert record["provider_id"] == "brave_search"
    assert record["flags"] == ["network_access"]
    assert record["content_sha256"].startswith("sha256:")
    # The evidence file the record references exists (the gate checks this).
    assert (tmp_path / record["evidence_ref"]).is_file()
    # The runtime gate accepts it (defense in depth: the helper already self-verified).
    auth = safety_gate.authorize(
        ["network_access"], provider_id="brave_search", now=record["activated_at"], root=tmp_path
    )
    assert "network_access" in auth.flags


def test_helper_rejects_unknown_flag(tmp_path):
    rc = main([
        "--provider-id", "brave_search", "--flags", "teleportation",
        "--authority-level", "P1", "--reason", "bad", "--root", str(tmp_path),
    ])
    assert rc == 2
    assert not _activation(tmp_path).is_file()  # nothing written on failure


def test_helper_rejects_bad_authority_level(tmp_path):
    rc = main([
        "--provider-id", "brave_search", "--flags", "network_access",
        "--authority-level", "P9", "--reason", "bad", "--root", str(tmp_path),
    ])
    assert rc == 2


def test_activating_a_second_provider_leaves_the_first_untouched(tmp_path):
    """The reason grants are per-provider files: enabling the validator's vendor must not
    disturb, refresh, or revoke the specialist's already-granted vendor."""
    assert main([
        "--provider-id", "google_ai_studio", "--flags", "model_invocation,network_access",
        "--authority-level", "P2", "--reason", "specialist", "--ttl-minutes", "60",
        "--root", str(tmp_path),
    ]) == 0
    first_bytes = _activation(tmp_path, "google_ai_studio").read_bytes()

    assert main([
        "--provider-id", "groq", "--flags", "model_invocation,network_access",
        "--authority-level", "P2", "--reason", "validator", "--ttl-minutes", "60",
        "--root", str(tmp_path),
    ]) == 0

    # The first grant's bytes are exactly as they were...
    assert _activation(tmp_path, "google_ai_studio").read_bytes() == first_bytes
    # ...and the gate now authorizes BOTH independently.
    now = json.loads(_activation(tmp_path, "groq").read_text(encoding="utf-8"))["activated_at"]
    for provider in ("google_ai_studio", "groq"):
        auth = safety_gate.authorize(
            ["model_invocation", "network_access"], provider_id=provider, now=now, root=tmp_path,
        )
        assert auth.provider_id == provider


def test_helper_refuses_a_path_shaped_provider_id(tmp_path):
    rc = main([
        "--provider-id", "../evil", "--flags", "network_access",
        "--authority-level", "P1", "--reason", "traversal", "--ttl-minutes", "60",
        "--root", str(tmp_path),
    ])
    assert rc == 2
    assert not (tmp_path.parent / "evil.json").exists()
