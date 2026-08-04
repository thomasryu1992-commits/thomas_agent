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
    DEFAULT_FUNDING_BPS_PER_INTERVAL,
    DEFAULT_MAKER_FEE_BPS,
    DEFAULT_SLIPPAGE_BPS,
    DEFAULT_TAKER_FEE_BPS,
    FUNDING_SOURCE_VENUE,
)
from runtime.mvp_runtime.crypto.dashboard import build_status

NOW = "2026-07-29T12:00:00Z"

# Sentinel: `bars_replayed=None` means "a row that records no window", which is a case under
# test, so it cannot double as "caller said nothing".
_MISSING = object()


def _candidate(
    cid,
    *,
    family="trend_pullback",
    symbol="BTCUSDT",
    timeframe="15m",
    closed=1500,
    taker=DEFAULT_TAKER_FEE_BPS,
    maker=DEFAULT_MAKER_FEE_BPS,
    verdict="ROBUST",
    holdout="CONFIRMED",
    net_r=16.0,
    rule_hash=None,
    bars_replayed=_MISSING,
):
    """One candidate row. Defaults describe a lineage the door would accept today.

    No ``robustness_score``/``trades_per_parameter``: with the components absent
    ``candidate_quality`` keeps the stored verdict, which is what lets a fixture state the
    verdict it means to test instead of reverse-engineering a score that produces it.

    15m and 1,500 closed trades because the backlog now also asks whether the LIFECYCLE could
    ever judge the lineage, and these fixtures exist to exercise the other filters. Measured on
    the real store 2026-07-30, the median 15m lineage closes ~3 trades a day and reaches the
    20-trade window in a week; a 4h one takes 123. Kept fast on purpose even though the cap
    rose to 130 on 2026-08-02 and a median 4h lineage now clears it: a default that sits near
    the boundary would make these cases fail on a filter they were not written to test the
    first time the boundary moves again.
    """
    cost_summary = {
        "cost_model": {
            "taker_fee_bps": taker,
            "maker_fee_bps": maker,
            "slippage_bps": DEFAULT_SLIPPAGE_BPS,
            "funding_bps_per_interval": DEFAULT_FUNDING_BPS_PER_INTERVAL,
            "funding_source": FUNDING_SOURCE_VENUE,
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
            "closed_count": closed,
            "win_count": int(closed * 0.45),
            "avg_win_R": 2.0,
            "avg_loss_R": 1.0,
            "expectancy": round(net_r / closed, 8) if closed else 0.0,
            "cost_summary": cost_summary,
            # Read from the factory's live target rather than written down, so these fixtures
            # follow the window when it moves — the same shape the promotion fixtures use.
            # Absent by default would read as UNRECORDED, which the door refuses, so every
            # "the door would accept this" case has to say what window it was scored over.
            "bars_replayed": (pool.expected_replayed_bars(timeframe)
                              if bars_replayed is _MISSING else bars_replayed),
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


# --- the chain has to name every axis the door refuses on --------------------

def test_the_backlog_does_not_count_what_the_depth_gate_refuses():
    """The counter filtered on cost basis and not on evidence depth, so a row whose window
    was never recorded was advertised as promotable and refused at the ask.

    Latent when found — no row in the real store passed every other filter AND failed this
    one — but not hypothetical: 41 rows there carry no window at all, and one of them
    becoming ROBUST is a matter of time. The failure is the one the cost-basis line in
    `promotable_backlog` is written to prevent, on the axis added seven minutes later.
    """
    result = _backlog([
        _candidate("cand_windowed"),
        _candidate("cand_no_window", family="breakout", bars_replayed=None),
    ])
    assert result["candidate_ids"] == ["cand_windowed"]


def test_a_shallow_row_is_still_backlog_because_the_door_still_promotes_it():
    """Only UNRECORDED is refused. A short window is a known error running against the
    candidate, so the door takes it — and a backlog that dropped those would disagree with
    the door in the other direction, hiding work an operator could actually do."""
    result = _backlog([_candidate("cand_shallow", bars_replayed=12)])
    assert result["count"] == 1


# --- can the lifecycle ever judge it? ------------------------------------------
#
# Every filter above asks whether the BACKTEST is believable. None asked whether a forward
# verdict is reachable, and on 2026-07-30 that gap emptied the pool: an operator retired 63 of
# 89 strategies — including all eleven promoted that day and the day before — because "at
# current rates a 1d lineage needs 122d and a 1h lineage 29d to reach the 20-trade WARNING
# window, so neither can ever be auto-demoted; today no own lineage exceeds 13 trades and zero
# strategies are eligible for any lifecycle rule". `route_entries` picks ONE strategy per
# context, so promoting a slow lineage does not add trades — it splits the same trades across
# more lineages, diluting the very stream the lifecycle needs.

def test_the_smallest_lifecycle_window_is_the_one_this_measures_against():
    """Restated rather than imported (module cycle). Pinned so a ladder change cannot leave
    the backlog measuring against a window no rule uses."""
    from runtime.mvp_runtime.crypto.lifecycle import DEFAULT_WINDOWS

    assert pool.LIFECYCLE_MIN_WINDOW_TRADES == min(DEFAULT_WINDOWS)


def test_days_to_the_window_comes_from_the_candidates_own_evidence():
    """1,500 trades over the 15m replay window is ~4 a day — under a week to twenty. Close to
    the measured median for 15m on the real store, which is why the fixture uses it."""
    fast = _candidate("cand_fast", timeframe="15m", closed=1500)
    assert pool.days_to_lifecycle_window(fast) == pytest.approx(4.7, abs=0.2)

    # 27 trades over a 350-day 4h replay is 0.08 a day: 259 days — the real 4h store's 75th
    # percentile, and the fixture moved there when the cap moved to 130 (2026-08-02). The
    # timeframe label is not what makes a lineage too slow; its own trade rate is. A 4h lineage
    # at the median (123 days) is now INSIDE the cap, which is the whole point of the change.
    slow = _candidate("cand_slow", timeframe="4h", closed=27, bars_replayed=2100)
    assert pool.days_to_lifecycle_window(slow) == pytest.approx(259.3, abs=1.0)


def test_a_lineage_the_runtime_could_not_grade_is_deferred_not_counted():
    result = _backlog([_candidate("cand_slow", timeframe="4h", closed=27, bars_replayed=2100)])
    assert result["count"] == 0
    assert [d["candidate_id"] for d in result["deferred_unjudgeable"]] == ["cand_slow"]
    assert result["deferred_unjudgeable"][0]["days_to_lifecycle_window"] > result["max_days_to_lifecycle_window"]


def test_deferred_is_named_never_silently_dropped():
    """A count that hid them would read as "nothing is waiting" when what is true is
    "nothing the runtime could grade is waiting"."""
    result = _backlog([
        _candidate("cand_fast"),
        _candidate("cand_slow", family="breakout", timeframe="4h", closed=27, bars_replayed=2100),
    ])
    assert result["candidate_ids"] == ["cand_fast"]
    assert len(result["deferred_unjudgeable"]) == 1


def test_evidence_that_cannot_state_a_rate_is_deferred_rather_than_assumed_fast():
    """A lineage that closed nothing over its replay window is the clearest case of one the
    lifecycle will never evaluate, so an unknowable rate must not read as fast enough."""
    result = _backlog([_candidate("cand_no_trades", closed=0)])
    assert result["count"] == 0

    no_window = _candidate("cand_no_window", bars_replayed=None)
    assert pool.days_to_lifecycle_window(no_window) is None


def test_an_unknown_timeframe_cannot_be_converted_and_is_deferred():
    weird = _candidate("cand_weird", timeframe="3h", bars_replayed=3000)
    assert pool.days_to_lifecycle_window(weird) is None


def test_the_board_says_how_many_are_waiting_on_a_faster_timeframe(tmp_path):
    pool.install_active_pool({"active_strategies": []}, root=tmp_path)
    _write_candidates(tmp_path, [
        _candidate("cand_slow", timeframe="4h", closed=27, bars_replayed=2100),
    ])
    _write_cursor(tmp_path, updated_at=NOW)

    status = build_status(tmp_path, now=NOW)
    assert status["promotion_backlog"]["count"] == 0
    assert len(status["promotion_backlog"]["deferred_unjudgeable"]) == 1


def test_the_cap_admits_the_horizon_the_operator_actually_promotes_at():
    """The defect that moved this number on 2026-08-02, pinned so it cannot come back.

    At 14 days the board reported `0 promotable` with 900 candidates on file — and every one
    of the five lineages the operator had promoted the day before sat ABOVE the cap (40.5,
    50.4, 81.4, 107.7, 127.3 days). It was refusing to advertise the exact class of thing the
    operator was choosing to run, so the queue it reported was empty for a reason that had
    nothing to do with whether work was waiting.

    The old value's premise was that 15m is the workhorse. The cost model killed that on the
    day: 15m netted -0.1845R/trade at the current basis against 4h's +0.0889R, so a cap
    admitting only 15m admitted only the timeframe that cannot pay for itself.

    Re-measured 2026-08-04 the economics read 15m -0.0866R, 1h +0.0241R, 4h +0.1083R, which
    does not touch either bound below — this cap is anchored on the horizon the operator
    promoted at, never on which timeframe pays. Stated so the next reader does not take the
    paragraph above for a current measurement."""
    promoted_horizons = (40.5, 50.4, 81.4, 107.7, 127.3)   # the real pool, 2026-07-31
    assert pool.MAX_DAYS_TO_LIFECYCLE_WINDOW >= max(promoted_horizons), (
        "the board would hide a lineage the operator has already chosen to run — a queue of "
        "zero that means 'not shown', not 'nothing waiting'"
    )
    # And still bounded: 1d's own FASTEST quartile is 341.5 days on the real store, so the
    # timeframe the original argument was written against stays out.
    assert pool.MAX_DAYS_TO_LIFECYCLE_WINDOW < 341.5, (
        "the cap reached 1d's fastest quartile — at that point it bounds nothing and the "
        "lifecycle ladder is decorative for every lineage it admits"
    )


def test_a_median_4h_lineage_is_judgeable_and_a_slow_one_is_not():
    """The cap is a statement about a lineage's own trade rate, not about its timeframe label.
    Both fixtures are 4h; only their rates differ, and the cap separates them."""
    median_4h = _candidate("cand_median_4h", timeframe="4h", closed=57, bars_replayed=2100)
    assert pool.days_to_lifecycle_window(median_4h) == pytest.approx(122.8, abs=1.5)
    assert _backlog([median_4h])["count"] == 1

    too_slow = _candidate("cand_slow_4h", timeframe="4h", closed=27, bars_replayed=2100)
    result = _backlog([too_slow])
    assert result["count"] == 0
    assert [d["candidate_id"] for d in result["deferred_unjudgeable"]] == ["cand_slow_4h"]
