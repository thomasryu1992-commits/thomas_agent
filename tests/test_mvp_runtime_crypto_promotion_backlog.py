"""The board says when work is waiting on a human, and when it can no longer be told.

Two things the daily report could always have said and did not. Promotion went untouched
from 2026-07-26 to 2026-07-29 while the factory kept minting every morning; the report went
out on each of those mornings reading ``warnings=0``. The cause was not the missing count
alone — the control channel's INBOUND half was dead for the same three days, so the three
``/approve`` messages sent at it reached nobody and their approvals expired unanswered. The
operator heartbeat passed throughout, because the loop really was turning.

Under test: the backlog counts what the promotion door would accept TODAY and nothing else
(a count of every candidate would rise forever and be ignored by the second week); one
lineage re-minted across generations counts once; and the channel warning fires on silence
measured from a RECORDED stamp, with the file's mtime as the weaker fallback it is named as.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import operator, timeutil
from runtime.mvp_runtime.crypto import paper, pool
from runtime.mvp_runtime.crypto.cost import (
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
)
from runtime.mvp_runtime.crypto.dashboard import build_status

NOW = "2026-07-29T12:00:00Z"


def _candidate(
    cid,
    *,
    family="trend_pullback",
    symbol="BTCUSDT",
    timeframe="4h",
    taker=DEFAULT_TAKER_FEE_BPS,
    maker=DEFAULT_MAKER_FEE_BPS,
    verdict="ROBUST",
    holdout="CONFIRMED",
    net_r=16.0,
    rule_hash=None,
):
    """One candidate row. Defaults describe a lineage the door would accept today.

    No ``robustness_score``/``trades_per_parameter``: with the components absent
    ``candidate_quality`` keeps the stored verdict, which is what lets a fixture state the
    verdict it means to test instead of reverse-engineering a score that produces it.
    """
    cost_summary = {
        "cost_model": {
            "taker_fee_bps": taker,
            "maker_fee_bps": maker,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
        },
        "total_net_r": net_r,
        "total_fee_cost_r": 2.0,
    }
    # A model with no maker rate had no maker leg, so it has no maker share either. Carrying
    # one anyway would describe a row the backtester cannot produce — and `expectancy_at`
    # refuses it, correctly, because rescaling a share whose old rate is unknown is a guess.
    if maker is not None:
        cost_summary["total_maker_fee_cost_r"] = 0.5
    return {
        "candidate_id": cid,
        "strategy_id": "S001",
        "generation_id": "GEN-001",
        "strategy_rule_hash": rule_hash or f"hash-{cid}",
        "strategy_spec": {
            "strategy_family": family,
            "symbol_scope": [symbol],
            "timeframe": timeframe,
        },
        "champion_score": 0.8,
        "backtest_evidence": {
            "robustness": {"verdict": verdict, "holdout_status": holdout},
            "closed_count": 40,
            "win_count": 18,
            "avg_win_R": 2.0,
            "avg_loss_R": 1.0,
            "expectancy": round(net_r / 40, 8),
            "cost_summary": cost_summary,
        },
    }


def _backlog(candidates, active=(), active_specs=()):
    members = [{"strategy_rule_hash": h} for h in active]
    members += [
        {"strategy_rule_hash": f"hash-live-{i}", "strategy_spec": spec}
        for i, spec in enumerate(active_specs)
    ]
    return pool.promotable_backlog(
        candidates=candidates,
        active_pool={"active_strategies": members},
    )


def test_the_backlog_counts_only_what_the_promotion_door_would_accept():
    """Every row here looks promotable in the listing; only one survives the same chain
    the door applies, and a count that included the rest would advertise work that BLOCKS."""
    candidates = [
        _candidate("cand_ok"),
        _candidate("cand_cheap", taker=2.5, family="breakout"),
        _candidate("cand_unproven", verdict="PROVISIONAL", family="macd_momentum"),
        _candidate("cand_failed_forward", holdout="CONTRADICTED", family="htf_trend_long"),
        _candidate("cand_negative", net_r=-8.0, family="oi_squeeze_long"),
    ]
    result = _backlog(candidates)
    assert result["count"] == 1
    assert result["candidate_ids"] == ["cand_ok"]


def test_conservative_evidence_is_backlog_because_the_door_promotes_it():
    """The gate refuses evidence scored CHEAPER than the venue charges, not evidence
    scored dearer. A backlog that dropped conservative rows would disagree with the door."""
    result = _backlog([_candidate("cand_conservative", taker=DEFAULT_TAKER_FEE_BPS, maker=None)])
    assert result["count"] == 1


def test_a_rule_the_pool_already_trades_is_not_waiting_on_anyone():
    candidates = [_candidate("cand_live", rule_hash="hash-live"), _candidate("cand_new")]
    result = _backlog(candidates, active=["hash-live"])
    assert result["candidate_ids"] == ["cand_new"]


def test_one_lineage_re_minted_every_generation_counts_once():
    """The factory re-mints the same family on the same context daily. Counting each
    re-mint would grow the backlog every morning while nothing new became promotable —
    the exact shape that makes a threshold fire forever and get ignored."""
    candidates = [
        _candidate(f"cand_gen{n}", family="breakdown_short", symbol="ETHUSDT", timeframe="4h")
        for n in range(4)
    ]
    assert _backlog(candidates)["count"] == 1

    # Same family on a DIFFERENT context is a different decision, not a re-mint.
    candidates.append(_candidate("cand_other", family="breakdown_short", symbol="ETHUSDT", timeframe="1h"))
    assert _backlog(candidates)["count"] == 2


def test_promoting_one_re_mint_does_not_leave_its_siblings_as_backlog():
    """The bug this rule exists for, caught on the real store the day it was written.

    Excluding pool members by rule hash while collapsing candidates by lineage counts the
    same slot twice: promote one re-mint and its siblings — same family, same context,
    a different hash — resurface as fresh backlog the next morning, forever. Measured on
    this machine minutes after a promotion: 7 reported, 4 actually waiting."""
    promoted = _candidate("cand_promoted", family="breakdown_short", symbol="ETHUSDT", timeframe="4h")
    sibling = _candidate("cand_sibling", family="breakdown_short", symbol="ETHUSDT", timeframe="4h")

    result = _backlog(
        [promoted, sibling],
        active=[promoted["strategy_rule_hash"]],
        active_specs=[promoted["strategy_spec"]],
    )
    assert result["count"] == 0


def test_a_slot_the_pool_already_fills_is_not_backlog_even_from_a_new_generation():
    """An operator who filled a (family, context) slot made that decision. A later,
    differently-parameterised mint of it is an upgrade to consider, not a queue forming."""
    result = _backlog(
        [_candidate("cand_new_gen", family="oi_squeeze_long", symbol="ETHUSDT", timeframe="1h")],
        active_specs=[{
            "strategy_family": "oi_squeeze_long",
            "symbol_scope": ["ETHUSDT"],
            "timeframe": "1h",
        }],
    )
    assert result["count"] == 0


def _write_candidates(root, candidates):
    state = paper.state_dir(root)
    state.mkdir(parents=True, exist_ok=True)
    with open(state / pool.CANDIDATES_FILENAME, "w", encoding="utf-8") as handle:
        for row in candidates:
            handle.write(json.dumps(row) + "\n")


def _register_operator(root):
    path = root / operator.REGISTRATION_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"operator_id": "7288953272", "chat_id": "7288953272", "approver": "Thomas"}),
        encoding="utf-8",
    )


def _write_cursor(root, *, updated_at=None):
    path = root / operator.OFFSET_STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"offset": 372055127}
    if updated_at is not None:
        payload["updated_at"] = updated_at
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_the_board_names_a_promotion_backlog_at_the_threshold(tmp_path):
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_candidates(tmp_path, [
        _candidate(f"cand_{n}", family=f"family_{n}") for n in range(pool.PROMOTION_BACKLOG_ALERT_THRESHOLD)
    ])
    _write_cursor(tmp_path, updated_at=NOW)

    status = build_status(tmp_path, now=NOW)
    assert status["promotion_backlog"]["count"] == pool.PROMOTION_BACKLOG_ALERT_THRESHOLD
    assert any("승격 대기" in w for w in status["warnings"])


def test_the_board_stays_quiet_below_the_threshold(tmp_path):
    """A warning that fires on one waiting candidate is a warning nobody reads by Friday."""
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_candidates(tmp_path, [_candidate("cand_one")])
    _write_cursor(tmp_path, updated_at=NOW)

    status = build_status(tmp_path, now=NOW)
    assert status["promotion_backlog"]["count"] == 1
    assert not any("승격 대기" in w for w in status["warnings"])


def test_the_board_names_a_control_channel_that_stopped_receiving(tmp_path):
    """The failure this exists for: outbound fine, inbound dead, three days, no surface."""
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_cursor(tmp_path, updated_at="2026-07-26T10:23:42Z")

    status = build_status(tmp_path, now=NOW)
    assert status["control_channel"]["silent_days"] == 3
    assert status["control_channel"]["last_inbound_source"] == "recorded"
    assert any("인바운드 3일 무응답" in w for w in status["warnings"])


def test_a_channel_that_answered_today_is_not_warned_about(tmp_path):
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_cursor(tmp_path, updated_at="2026-07-29T08:47:12Z")

    status = build_status(tmp_path, now=NOW)
    assert status["control_channel"]["silent_days"] == 0
    assert not any("인바운드" in w for w in status["warnings"])


def test_a_cursor_written_before_the_stamp_falls_back_to_the_file_time_and_says_so(tmp_path):
    """Deployed state files predate the stamp. Reporting mtime as if it were recorded
    would give a rebuild's timestamp the authority of a received message."""
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_cursor(tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["control_channel"]["last_inbound_source"] == "file_mtime"


def test_a_machine_with_no_registration_is_not_warned_about_an_inactive_channel(tmp_path):
    """Every fresh checkout and every mock-channel deployment is in this state, working
    exactly as configured. A permanent line on the board would be noise, not a signal."""
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert status["control_channel"]["last_inbound_at"] is None
    assert not any("인바운드" in w for w in status["warnings"])


def test_a_registered_operator_with_no_inbound_record_is_named(tmp_path):
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _register_operator(tmp_path)

    status = build_status(tmp_path, now=NOW)
    assert any("수신 기록 없음" in w for w in status["warnings"])


def test_saving_the_cursor_records_when_it_moved(tmp_path):
    """The cursor advances only on a fetched update, so its write time IS the last
    inbound. Recording it keeps that fact out of the filesystem's hands."""
    path = tmp_path / operator.OFFSET_STATE_REL
    channel = operator.TelegramChannel(state_path=path)
    channel._offset = 42
    channel._save_offset()

    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["offset"] == 42
    timeutil.parse_iso(written["updated_at"])  # raises if it is not the fixed UTC form

    # The extra key must not break the reader that fails closed on a malformed cursor.
    assert operator.TelegramChannel(state_path=path)._load_offset() == 42
    assert operator.last_inbound_at(tmp_path)["source"] == "recorded"


def test_an_unreadable_cursor_reports_no_known_inbound_rather_than_raising(tmp_path):
    """A diagnostic about a broken channel that dies on the broken case is not one."""
    path = tmp_path / operator.OFFSET_STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert operator.last_inbound_at(tmp_path) is None
