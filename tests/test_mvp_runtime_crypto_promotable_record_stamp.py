"""The promotion door refuses a candidate row that carries no self-hash (2026-09-23).

`pool_state.read_candidates` recomputes every `record_sha256` it finds and refuses one that does not
match, but a row without the field reads as legacy and passes. That is also what a stamped row looks
like once the field is removed. On this machine the unstamped rows are a closed set: the first 41
lines of the store, one 2026-07-16 import, all already pool members. So the gate refuses nothing a
promotion can reach today, and every case below asserts on a fixture rather than on the store.
"""

from __future__ import annotations

import pytest

from runtime.mvp_runtime.crypto import pool, pool_admission, pool_state
from runtime.mvp_runtime.crypto.pool_admission import RECORD_STAMP_FIELD, assert_promotable_record_stamp
from runtime.mvp_runtime.errors import ToolError
from runtime.read_only_kernel import integrity


def _row(cid, *, stamped=True, **extra):
    row = {"candidate_id": cid, **extra}
    if stamped:
        row[RECORD_STAMP_FIELD] = integrity.sha256_record(row)
    return row


# --- what the gate takes and what it stops -------------------------------------

def test_a_stamped_row_passes():
    assert_promotable_record_stamp([_row("cand_ok")])


def test_a_row_with_no_stamp_is_refused():
    with pytest.raises(ToolError) as exc:
        assert_promotable_record_stamp([_row("cand_legacy", stamped=False)])
    assert exc.value.reason_code == "CANDIDATE_RECORD_UNSTAMPED"


def test_an_explicit_null_stamp_is_refused_like_an_absent_one():
    """`read_candidates` skips verification on null, so null is exactly the downgrade case."""
    with pytest.raises(ToolError) as exc:
        assert_promotable_record_stamp([{"candidate_id": "cand_null", RECORD_STAMP_FIELD: None}])
    assert exc.value.reason_code == "CANDIDATE_RECORD_UNSTAMPED"


def test_the_refusal_names_the_rows_and_the_escape():
    with pytest.raises(ToolError) as exc:
        assert_promotable_record_stamp([_row("cand_fine"), _row("cand_bare", stamped=False)])
    assert "cand_bare" in exc.value.reason and "cand_fine" not in exc.value.reason
    assert "--allow-unstamped-record" in exc.value.reason


# --- why the door is needed: what the reader does and does not check ------------

def _write(root, rows):
    import json

    path = pool_state.candidates_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_the_reader_refuses_an_edited_stamped_row(tmp_path):
    row = _row("cand_x", strategy_rule_hash="h")
    _write(tmp_path, [{**row, "strategy_rule_hash": "edited"}])
    with pytest.raises(ToolError) as exc:
        pool_state.read_candidates(tmp_path)
    assert exc.value.reason_code == "CANDIDATES_TAMPERED"


def test_the_reader_passes_the_same_edit_once_the_stamp_is_removed(tmp_path):
    """The downgrade this door closes: remove the field and the reader has nothing to check."""
    row = _row("cand_x", strategy_rule_hash="h")
    edited = {k: v for k, v in row.items() if k != RECORD_STAMP_FIELD}
    edited["strategy_rule_hash"] = "edited"
    _write(tmp_path, [edited])
    assert pool_state.read_candidates(tmp_path)[0]["strategy_rule_hash"] == "edited"
    with pytest.raises(ToolError):
        assert_promotable_record_stamp(pool_state.read_candidates(tmp_path))


# --- the backlog and the listing have to agree with the door --------------------

def test_the_axis_is_counted_in_the_backlog_breakdown():
    assert "unstamped" in pool.BACKLOG_REFUSAL_AXES
    axes = pool.BACKLOG_REFUSAL_AXES
    assert axes.index("derivation") < axes.index("unstamped") < axes.index("cost_basis")


def test_the_escape_is_on_the_roster_next_to_the_derivation_gate():
    from runtime.mvp_runtime.crypto import promotion

    flags = [g.escape_flag for g in promotion.PROMOTION_GATES]
    assert flags.index("allow_unstamped_record") == flags.index("allow_quarantined_derivation") + 1


def _listing_row(cid, *, stamped, rule_hash):
    row = {"candidate_id": cid, "strategy_id": "S001", "generation_id": "GEN-001",
           "strategy_rule_hash": rule_hash,
           "strategy_spec": {"strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1h"},
           "champion_score": 0.9, "backtest_evidence": {"closed_count": 100, "expectancy": 0.05}}
    if stamped:
        row[RECORD_STAMP_FIELD] = integrity.sha256_record(row)
    return row


def test_the_listing_names_unstamped_rows_outside_the_pool(monkeypatch, capsys):
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates",
                        lambda root: [_listing_row("cand_bare", stamped=False, rule_hash="hash-bare")])
    monkeypatch.setattr(prom.pool_state, "load_active_pool", lambda root: {"active_strategies": []})
    assert prom.main(["--list"]) == 0
    out = capsys.readouterr().out
    assert "CANDIDATE_RECORD_UNSTAMPED" in out and "--allow-unstamped-record" in out


def test_the_listing_stays_quiet_when_every_unstamped_row_is_already_in_the_pool(monkeypatch, capsys):
    """The real store: the 41 unstamped rows are all pool members, so a line here would fire every
    morning about rows nobody can promote."""
    from scripts import promote_strategy_candidates as prom

    monkeypatch.setattr(prom.pool_store, "read_candidates",
                        lambda root: [_listing_row("cand_member", stamped=False, rule_hash="hash-member"),
                                      _listing_row("cand_new", stamped=True, rule_hash="hash-new")])
    monkeypatch.setattr(prom.pool_state, "load_active_pool",
                        lambda root: {"active_strategies": [{"strategy_rule_hash": "hash-member"}]})
    assert prom.main(["--list"]) == 0
    assert "CANDIDATE_RECORD_UNSTAMPED" not in capsys.readouterr().out


def test_the_stamp_field_is_the_one_the_store_writes(tmp_path):
    """Named at the door, written by the store: pinned together so a rename cannot leave the door
    reading a field nothing writes (which would refuse every row)."""
    spec = {"strategy_family": "breakout", "symbol_scope": ["BTCUSDT"], "timeframe": "1h"}
    pool_state.append_candidates([{"strategy_id": "S1", "generation_id": "GEN-1",
                                   "strategy_rule_hash": "h1", "strategy_spec": spec}], root=tmp_path)
    [written] = pool_state.read_candidates(tmp_path)
    assert isinstance(written.get(pool_admission.RECORD_STAMP_FIELD), str)
    assert_promotable_record_stamp([written])
