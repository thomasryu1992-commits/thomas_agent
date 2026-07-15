"""Operator safety-flag activation helper tests (scripts/activate_safety_flag.py).

The helper writes a local, gitignored activation record; the same gate the runtime uses
must then authorize it. Everything runs against a tmp root (no repo state touched)."""

from __future__ import annotations

import json

from runtime.mvp_runtime import safety_gate
from scripts.activate_safety_flag import main


def _activation(tmp_path):
    return tmp_path / ".runtime_governance_state" / "safety_flag_activation.json"


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
