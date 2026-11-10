"""The Claude door's `crypto_readiness` reply contract v1 (CRYPTO_DESIGN_REVIEW_V0.1 방안 1, Thomas 2026-11-10).

States and counts in closed vocabulary; never the board's text, a detail, an id, a hash, an amount or an env value.
Every store is a synthetic tmp_path; every secret-looking string is a fake marker.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from runtime.mvp_runtime import read_bridge, store_reads
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.crypto import execution_stage, readiness_model
from runtime.mvp_runtime.errors import ControlBlocked, MvpRuntimeError

NOW = "2026-11-10T03:00:00Z"
FAKE = "FAKE-SECRET-7f3a"
FAKE_PATH = "/root/fake-state/crypto/x.json"
CLAUDE = read_bridge.PROFILES[read_bridge.CLAUDE_PROFILE]


def _board(**over):
    components = {name: {"ok": True, "reason": "OK_CODE", "source": "recorded", "detail": FAKE}
                  for name in readiness_model.READINESS_COMPONENTS}
    components["execution_stage"] = {"ok": False, "reason": "STAGE_PAPER", "stage": "PAPER"}
    components["live_gate_open"] = {"ok": None, "reason": "RECORD_STALE"}
    board = {
        "as_of": NOW, "infrastructure_ready": False, "env_scope": "this_process", "env_out_of_scope": True,
        "checks": [{"check": "live_trading_opt_in", "ok": False, "detail": FAKE}],
        "live_armed_strategies": {"known": True, "armed": 0, "occupying": 15, "error": FAKE},
        "recorded_gate": {"known": True, "open": False, "status": FAKE, "recorded_at": "2026-10-07T16:58:52Z",
                          "age_seconds": 12.5, "stale": True},
        "live_entry_possible": False,
        "readiness": {"model": "readiness_state.v2", "blocking": [f"execution_stage:{FAKE}"], "unknown": [f"live_gate_open:{FAKE}"],
                      "components": components},
        "execution_stage": {"stage": "PAPER", "valid": True, "reason_code": None, "recorded_stage": "PAPER",
                            "stage_id": "stage_" + "a" * 20, "record_sha256": "b" * 64, "policy_version": "1.7.0",
                            "approval_id": FAKE, "anchor_behind": False, "enforced": True, "admits_entry": False},
        "venue_contract": {"error": FAKE, "recorded": True, "status": FAKE, "verified_at": NOW, "age_seconds": 3.0,
                           "stale": True, "version_current": True, "usable": False, "failed_checks": [FAKE],
                           "symbols": ["BTCUSDT"]},
        "guard_dry_run_status": FAKE, "submitted_today": 3,
    }
    board.update(over)
    return board


def test_the_lists_are_the_lanes_own():
    assert store_reads.CRYPTO_READINESS_COMPONENTS == readiness_model.READINESS_COMPONENTS
    assert store_reads.CRYPTO_EXECUTION_STAGES == execution_stage.LADDER


def test_it_carries_states_and_counts_and_nothing_else():
    data = store_reads.project_crypto_readiness(_board(), now=NOW)
    assert data["projection"] == "v1" and data["available"] is True
    assert data["live_entry_possible"] is False
    assert data["blocking"] == ["execution_stage"] and data["unknown"] == ["live_gate_open"]
    assert data["components"]["execution_stage"] == {"ok": False, "reason": "STAGE_PAPER"}
    assert data["components"]["live_gate_open"] == {"ok": None, "reason": "RECORD_STALE"}
    assert data["execution_stage"] == {"stage": "PAPER", "valid": True, "admits_entry": False,
                                       "anchor_behind": False, "policy_version": "1.7.0"}
    assert data["armed"] == {"known": True, "armed": 0, "occupying": 15}
    assert data["recorded_gate"] == {"known": True, "open": False, "stale": True,
                                     "recorded_at": "2026-10-07T16:58:52Z", "age_seconds": 12.5}
    assert data["venue_contract"] == {"recorded": True, "stale": True, "usable": False, "version_current": True}
    blob = json.dumps(data)
    for marker in (FAKE, FAKE_PATH, "stage_" + "a" * 20, "b" * 64, "submitted_today", "BTCUSDT", "detail"):
        assert marker not in blob


def test_the_reply_text_is_written_from_the_projection_alone():
    out = store_reads.read_crypto_readiness(_board, now=NOW, projection="v1")
    assert out["action"] == "CRYPTO_READINESS_READ"
    assert out["reply"].splitlines() == [
        "live entry possible: no", "blocking: execution_stage", "unknown from here: live_gate_open",
        "execution stage: PAPER", "armed LIVE: 0 of 15 occupying",
        "recorded gate: closed at 2026-10-07T16:58:52Z (stale)"]
    assert FAKE not in out["reply"]


def test_a_value_outside_the_vocabulary_is_dropped_and_the_reply_says_partial():
    board = _board()
    board["readiness"]["components"]["risk_ready"] = {"ok": False, "reason": f"free text {FAKE}"}
    board["readiness"]["components"]["a_component_added_later"] = {"ok": True, "reason": "X_CODE"}
    board["execution_stage"]["reason_code"] = f"not a code {FAKE}"
    data = store_reads.project_crypto_readiness(board, now=NOW)
    assert data["projection"] == "v1-partial"
    assert data["blocking"] == ["execution_stage", "risk_ready"]
    assert data["components"]["risk_ready"] == {"ok": False}
    assert "a_component_added_later" not in data["components"] and "reason_code" not in data["execution_stage"]
    assert FAKE not in json.dumps(data)


def test_an_unknown_stage_or_a_missing_board_never_reads_as_a_stage():
    data = store_reads.project_crypto_readiness(_board(execution_stage={"stage": "LIVE_CANARY"}), now=NOW)
    assert "execution_stage" not in data and data["projection"] == "v1-partial"
    gone = store_reads.project_crypto_readiness(None, now=NOW)
    assert gone == {"available": False, "as_of": NOW, "projection": "v1-partial"}


def test_a_missing_component_makes_the_reply_partial():
    board = _board()
    del board["readiness"]["components"]["venue_ready"]
    data = store_reads.project_crypto_readiness(board, now=NOW)
    assert data["projection"] == "v1-partial" and "venue_ready" not in data["components"]


def test_a_component_added_later_is_dropped_and_marks_the_reply_partial():
    board = _board()
    board["readiness"]["components"]["a_component_added_later"] = {"ok": True, "reason": "X_CODE"}
    data = store_reads.project_crypto_readiness(board, now=NOW)
    assert data["projection"] == "v1-partial" and "a_component_added_later" not in data["components"]
    assert len(data["components"]) == len(store_reads.CRYPTO_READINESS_COMPONENTS)


def test_what_the_process_cannot_see_stays_unknown():
    data = store_reads.project_crypto_readiness(_board(live_entry_possible=None), now=NOW)
    assert data["live_entry_possible"] is None
    assert store_reads.read_crypto_readiness(lambda: _board(live_entry_possible=None), now=NOW,
                                             projection="v1")["reply"].startswith("live entry possible: unknown")
    odd = store_reads.project_crypto_readiness(_board(live_entry_possible="yes"), now=NOW)
    assert odd["live_entry_possible"] is None and odd["projection"] == "v1-partial"


def test_the_closed_shape_refuses_a_smuggled_field():
    data = store_reads.project_crypto_readiness(_board(), now=NOW)
    for extra in ({"reply_detail": FAKE}, {"armed": {"known": True, "error": FAKE}},
                  {"execution_stage": {"stage": "PAPER", "stage_id": FAKE}}):
        with pytest.raises(ControlBlocked) as exc:
            store_reads._checked({**data, **extra}, store_reads._CRYPTO_READINESS_SCHEMA)
        assert exc.value.reason_code == "REPLY_SCHEMA_INVALID"


def _read(tmp_path, **kw):
    return read_bridge.apply_read({"command": "crypto_readiness"}, control_store=ControlStore(tmp_path),
                                  ledger=None, now=NOW, repo_root=tmp_path, profile=CLAUDE, **kw)


def test_the_claude_door_answers_it_through_the_contract_on_an_empty_state_root(tmp_path):
    assert "crypto_readiness" in CLAUDE.reads and "crypto_readiness" not in read_bridge._PROFILE_HELD_READS
    out = _read(tmp_path)
    assert out["ok"] is True and out["data"]["projection"] in ("v1", "v1-partial")
    assert out["data"]["live_entry_possible"] is not True          # nothing is armed on an empty root
    assert out["data"]["execution_stage"]["stage"] == "READ_ONLY"  # no ledger reads READ_ONLY (decision 9)
    assert set(out["data"]) <= set(store_reads._CRYPTO_READINESS_SCHEMA["properties"])
    assert out["reply"].startswith("live entry possible: ")


def test_the_door_reads_the_trading_process_record_not_its_own_environment(tmp_path, monkeypatch):
    for name in ("MVP_LIVE_TRADING", "MVP_LIVE_TRADING_CONFIRM"):
        monkeypatch.setenv(name, "real")
    opted = _read(tmp_path)["data"]
    for name in ("MVP_LIVE_TRADING", "MVP_LIVE_TRADING_CONFIRM"):
        monkeypatch.delenv(name)
    plain = _read(tmp_path)["data"]
    # Without the env the gate component is the recorded one: unknown on a root with no record, never "closed"
    # read off this container's own empty environment.
    assert plain["components"]["live_gate_open"]["ok"] is None
    assert "live_gate_open" in plain["unknown"] and "live_gate_open" not in plain["blocking"]
    assert opted["execution_stage"] == plain["execution_stage"]


def test_a_board_that_cannot_be_built_is_refused_without_its_message(tmp_path, monkeypatch):
    def broken(*args, **kwargs):
        raise MvpRuntimeError("CRYPTO_STORE_UNREADABLE", f"could not parse {FAKE_PATH}: {FAKE}")
    monkeypatch.setattr(read_bridge.domain_console, "apply_domain_command", broken)
    with pytest.raises(ControlBlocked) as exc:
        _read(tmp_path)
    assert exc.value.reason_code == "READ_UNAVAILABLE"
    assert FAKE not in str(exc.value) and FAKE_PATH not in str(exc.value)


def test_without_a_profile_the_read_is_the_boards_own_unchanged(tmp_path):
    out = read_bridge.apply_read({"command": "crypto_readiness"}, control_store=ControlStore(tmp_path), ledger=None,
                                 now=NOW, repo_root=tmp_path)
    assert "infrastructure_ready" in out["data"] and "projection" not in out["data"]


@pytest.mark.skipif(sys.platform == "win32" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                    reason="needs POSIX permissions and a non-root user")
def test_it_builds_on_a_read_only_state_root(tmp_path):
    state = tmp_path / ".runtime_governance_state"
    (state / "crypto").mkdir(parents=True)
    os.chmod(state / "crypto", 0o555)
    os.chmod(state, 0o555)
    os.chmod(tmp_path, 0o555)
    try:
        out = read_bridge.apply_read({"command": "crypto_readiness"}, control_store=ControlStore(tmp_path),
                                     ledger=None, now=NOW, repo_root=tmp_path, profile=CLAUDE)
    finally:
        for path in (tmp_path, state, state / "crypto"):
            os.chmod(path, 0o755)
    assert out["ok"] is True
    assert sorted(p.name for p in state.rglob("*")) == ["crypto"]            # nothing was written
