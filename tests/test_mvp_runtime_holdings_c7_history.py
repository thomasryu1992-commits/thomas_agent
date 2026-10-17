"""C7 PR-B, option C (Thomas 2026-10-17): named skips, Toss continuity, Binance coverage, history completeness.

``docs/proposals/HOLDINGS_LEDGER_CHECKPOINT_V0.1.md`` §2, §4 (a), §12.3 and §12.4. Synthetic only: every ledger
and state here lives in ``tmp_path``. What is pinned:

- a fire that records no Toss window names the stretch it skipped and why (``toss_window_skipped``), and a
  Binance source whose cursor fell behind the query depth names the stretch it never read
  (``flow_window_skipped``);
- the continuity verdict counts a named skip as explained and an unnamed gap as unexplained; skips never
  count as windows;
- ``history_completeness`` is PASS only with the checkpoint observed at or before the cutover, no detection,
  Toss CONTINUOUS and every Binance source covering the cutover — and each failing piece names its reason;
- option C's path: a lane whose checkpoint came after the cutover reads UNCHECKED until the cutover is moved
  past it, then PASS;
- the readers that predate PR-B are unchanged by the new lines (D1 condition 4).
"""

from __future__ import annotations

import shutil
from types import SimpleNamespace

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import binance_wallet, cash_flows
from tests._h6d_support import CURRENT_OK, Feed, Toss, collect, iso, ledger, ms, snap, toss_fire

DAY, HOUR = 86_400_000, 3_600_000
OLD = "2026-10-09T03:00:00Z"
T0 = "2026-10-09T04:00:00Z"


def at(base: str, hours: float = 0) -> str:
    return iso(ms(base) + int(hours * HOUR))


def no_cash(rate: float = 1400.0) -> SimpleNamespace:
    """A Toss snapshot with no cash figure: the fire records no window."""
    return SimpleNamespace(usd_krw_rate=rate, domestic=SimpleNamespace(cash_krw=None), overseas=SimpleNamespace(cash_krw=0.0))


def lane(tmp_path, *, cutover: str = OLD, cutover_now: str | None = None):
    """A lane with its cutover, one Binance read of every source, and a Toss cash point at the cutover's
    commit; then one window, so the windows start at the cutover's commit (0.5 h after the cutover itself)."""
    cash_flows.set_cutover(tmp_path, at=cutover, requested_by="test", now=cutover_now or cutover)
    collect(tmp_path, now=cutover_now or cutover)
    toss_fire(tmp_path, 1_000_000, cutover_now or cutover)
    return tmp_path


def history(d, now):
    rows, state = ledger(d), cash_flows.load_state(d)
    return cash_flows.history_completeness(rows, state, now=now)


# --- named skips ---------------------------------------------------------------------------------------

def test_a_fire_without_a_cash_figure_names_the_stretch_it_skipped(tmp_path):
    lane(tmp_path)
    toss_fire(tmp_path, 1_000_000, at(OLD, 1))             # a window, cutover..1
    cash_flows.collect_toss(Toss(), no_cash(), tmp_path, now=at(OLD, 1.5))
    cash_flows.collect_toss(Toss(), no_cash(), tmp_path, now=at(OLD, 2.5))
    toss_fire(tmp_path, 1_000_000, at(OLD, 3.5))           # the cash point before it had no figure: skipped too
    toss_fire(tmp_path, 1_000_000, at(OLD, 4.5))           # a window again
    skips = [r for r in ledger(tmp_path) if r["event"] == cash_flows.EVENT_TOSS_SKIPPED]
    assert [(r["window_start"], r["window_end"], r["reason"]) for r in skips] == [
        (at(OLD, 1), at(OLD, 1.5), "snapshot_unusable"), (at(OLD, 1.5), at(OLD, 2.5), "snapshot_unusable"),
        (at(OLD, 2.5), at(OLD, 3.5), "snapshot_unusable")]
    verdict = cash_flows.toss_continuity(ledger(tmp_path), now=at(OLD, 4.5))
    assert verdict["status"] == "CONTINUOUS" and verdict["skipped"] == 3 and verdict["windows"] == 2
    without = [r for r in ledger(tmp_path) if r["event"] != cash_flows.EVENT_TOSS_SKIPPED]
    assert cash_flows.toss_continuity(without, now=at(OLD, 4.5))["status"] == "UNEXPLAINED_GAP"


def test_a_cash_point_behind_the_ledger_names_the_stretch_after_the_last_window(tmp_path):
    lane(tmp_path)
    toss_fire(tmp_path, 1_000_000, at(OLD, 1.5))            # window 0.5..1.5
    state = cash_flows.load_state(tmp_path)
    state["toss"] = {**state["toss"], "at": at(OLD, 0.5)}  # a stale cache: the cash point behind the ledger
    cash_flows._save_state(tmp_path, state)
    toss_fire(tmp_path, 1_000_000, at(OLD, 2.5))
    toss_fire(tmp_path, 1_000_000, at(OLD, 3.5))            # window 2.5..3.5
    (skip,) = [r for r in ledger(tmp_path) if r["event"] == cash_flows.EVENT_TOSS_SKIPPED]
    assert (skip["window_start"], skip["window_end"], skip["reason"]) == (at(OLD, 1.5), at(OLD, 2.5),
                                                                          "cash_point_behind_ledger")
    assert cash_flows.toss_continuity(ledger(tmp_path), now=at(OLD, 3.5))["status"] == "CONTINUOUS"


def _rows(*spans, cutover=OLD):
    rows = [{"event": "cutover_set", "cutover_at": cutover, "source_event_key": "c"}]
    for i, (kind, a, b) in enumerate(spans):
        event = cash_flows.EVENT_TOSS_WINDOW if kind == "w" else cash_flows.EVENT_TOSS_SKIPPED
        rows.append({"event": event, "source_event_key": f"{kind}{i}", "window_start": a, "window_end": b, "epoch": 0})
    return rows


@pytest.mark.parametrize("spans, status", [
    ([("w", at(OLD, -1), at(OLD, 1)), ("w", at(OLD, 1), at(OLD, 2))], "CONTINUOUS"),
    ([("w", at(OLD, -1), at(OLD, 1)), ("w", at(OLD, 2), at(OLD, 3))], "UNEXPLAINED_GAP"),
    ([("w", at(OLD, -1), at(OLD, 1)), ("s", at(OLD, 1), at(OLD, 2)), ("w", at(OLD, 2), at(OLD, 3))], "CONTINUOUS"),
    ([("w", at(OLD, -1), at(OLD, 1)), ("s", at(OLD, 1), at(OLD, 1.5)), ("w", at(OLD, 2), at(OLD, 3))], "UNEXPLAINED_GAP"),
    ([("s", at(OLD, -1), at(OLD, 1)), ("w", at(OLD, 1), at(OLD, 2))], "CONTINUOUS"),       # a skip may lead
    ([("s", at(OLD, -1), at(OLD, 1))], "INCOMPLETE_EVIDENCE"),                            # skips alone: no window
    ([("w", at(OLD, 1), at(OLD, 2))], "INCOMPLETE_EVIDENCE"),                             # nothing before the first
])
def test_a_named_skip_explains_its_stretch_and_nothing_else(spans, status):
    verdict = cash_flows.toss_continuity(_rows(*spans), now=at(OLD, 3))
    assert verdict["status"] == status, verdict["reasons"]


def test_a_source_whose_cursor_fell_behind_the_depth_names_what_it_never_read(tmp_path):
    lane(tmp_path)
    source = "transfer_spot_to_futures"                       # depth 7 days
    state = cash_flows.load_state(tmp_path)
    old_cursor = ms(OLD) - 3 * DAY
    state["sources"][source]["cursor_ms"] = old_cursor
    cash_flows._save_state(tmp_path, state)
    now = at(OLD, 5 * 24)              # the other transfer source's cursor (the cutover) is still inside its depth
    collect(tmp_path, now=now)
    skips = [r for r in ledger(tmp_path) if r["event"] == cash_flows.EVENT_FLOW_SKIPPED]
    assert [(r["flow_source"], r["reason"]) for r in skips] == [(source, "beyond_query_depth")]
    assert skips[0]["window_start"] == iso(old_cursor) and ms(skips[0]["window_end"]) > old_cursor
    collect(tmp_path, now=at(now, 1))                        # no repeat once the cursor caught up
    assert len([r for r in ledger(tmp_path) if r["event"] == cash_flows.EVENT_FLOW_SKIPPED]) == 1


def test_the_first_read_of_each_source_records_where_its_reading_began(tmp_path):
    lane(tmp_path)
    sources = cash_flows.load_state(tmp_path)["sources"]
    assert set(sources) == set(binance_wallet.FLOW_SOURCES)
    began = {name: info["covered_from_ms"] for name, info in sources.items()}
    collect(tmp_path, now=at(OLD, 5))
    assert {n: i["covered_from_ms"] for n, i in cash_flows.load_state(tmp_path)["sources"].items()} == began


def test_a_skip_line_outside_its_form_is_never_written():
    for row in ({"event": cash_flows.EVENT_TOSS_SKIPPED, "window_start": OLD, "window_end": T0, "reason": "because"},
                {"event": cash_flows.EVENT_TOSS_SKIPPED, "window_start": OLD, "reason": "snapshot_unusable"},
                {"event": cash_flows.EVENT_FLOW_SKIPPED, "window_start": OLD, "window_end": T0,
                 "reason": "beyond_query_depth"}):
        with pytest.raises(ToolError) as e:
            cash_flows._validate(row)
        assert e.value.reason_code == cash_flows.EVENT_INVALID


# --- history completeness --------------------------------------------------------------------------------

def test_option_c_unchecked_until_the_cutover_moves_past_the_checkpoint_then_pass(tmp_path):
    # The lane's checkpoint was first observed after its cutover (the history before it is unverified) …
    lane(tmp_path, cutover=OLD, cutover_now=at(OLD, 2))
    for h in (3, 4):
        collect(tmp_path, now=at(OLD, h))
        toss_fire(tmp_path, 1_000_000, at(OLD, h))
    verdict, detail = history(tmp_path, at(OLD, 4))
    assert (verdict, detail["reason"]) == ("UNCHECKED", "checkpoint_after_cutover")
    # … Thomas moves the cutover past it (option C), the collectors run on, and the history since is whole.
    new = at(OLD, 4.5)
    cash_flows.set_cutover(tmp_path, at=new, requested_by="thomas", reason="C7 D2 option C", migrate=True, now=new)
    for h in (5, 6):
        collect(tmp_path, now=at(OLD, h))
        toss_fire(tmp_path, 1_000_000, at(OLD, h))
    verdict, detail = history(tmp_path, at(OLD, 6))
    assert (verdict, detail) == ("PASS", {"reason": None, "toss": "CONTINUOUS", "binance": "COMPLETE"})
    ready = cash_flows.readiness(tmp_path, now=at(OLD, 6), current=CURRENT_OK)
    assert ready["checks"]["history_completeness"] == "PASS" and ready["checkpoint_history"] == "VERIFIED"
    assert ready["checkpoint"] == "observed"                       # the basis never changes


def _whole(tmp_path):
    cash_flows.set_cutover(tmp_path, at=OLD, requested_by="test", now=at(OLD, -1))   # observed before the cutover
    collect(tmp_path, now=at(OLD, -0.5))
    toss_fire(tmp_path, 1_000_000, at(OLD, -0.5))
    for h in (1, 2):
        collect(tmp_path, now=at(OLD, h))
        toss_fire(tmp_path, 1_000_000, at(OLD, h))
    assert history(tmp_path, at(OLD, 2))[0] == "PASS"
    return tmp_path


def _state_edit(d, change):
    state = cash_flows.load_state(d)
    change(state)
    cash_flows._save_state(d, state)


@pytest.mark.parametrize("damage, reason", [
    (lambda d: _state_edit(d, lambda s: s.pop(cash_flows.CHECKPOINT_KEY)), "no_checkpoint"),
    (lambda d: _state_edit(d, lambda s: s[cash_flows.CHECKPOINT_KEY].update(observed_at=at(OLD, 0.5))),
     "checkpoint_after_cutover"),
    (lambda d: _state_edit(d, lambda s: s[cash_flows.CHECKPOINT_KEY].update(observed_at="not a time")),
     "checkpoint_after_cutover"),
    (lambda d: _state_edit(d, lambda s: s.update({cash_flows.DETECTION_MARKER_KEY: {"reason": "x"}})),
     "ledger_state_inconsistent"),
    (lambda d: _state_edit(d, lambda s: s[cash_flows.CHECKPOINT_KEY].update(lines=999)),
     "ledger_state_inconsistent"),
    (lambda d: _state_edit(d, lambda s: s["sources"]["pay"].update(covered_from_ms=ms(OLD) + 1)), "binance_incomplete"),
    (lambda d: _state_edit(d, lambda s: s["sources"]["pay"].pop("covered_from_ms")), "binance_incomplete"),
    (lambda d: _state_edit(d, lambda s: s["sources"]["pay"].update(cursor_ms=ms(OLD) - 1)), "binance_incomplete"),
    (lambda d: _state_edit(d, lambda s: s["sources"].pop("pay")), "binance_incomplete"),
])
def test_each_missing_piece_reads_unchecked_with_its_reason(tmp_path, damage, reason):
    d = _whole(tmp_path)
    damage(d)
    verdict, detail = history(d, at(OLD, 2))
    assert (verdict, detail["reason"]) == ("UNCHECKED", reason)


def test_a_toss_gap_after_the_cutover_reads_unchecked(tmp_path):
    d = _whole(tmp_path)
    _state_edit(d, lambda s: s.update(toss={**s["toss"], "at": at(OLD, 2.5)}))    # a cash point nobody recorded
    toss_fire(d, 1_000_000, at(OLD, 3))                                          # window 2.5..3: 2..2.5 uncovered
    verdict, detail = history(d, at(OLD, 3))
    assert (verdict, detail["reason"], detail["toss"]) == ("UNCHECKED", "toss_unexplained_gap", "UNEXPLAINED_GAP")


def test_a_binance_skip_after_the_cutover_reads_unchecked(tmp_path):
    d = _whole(tmp_path)
    _state_edit(d, lambda s: s["sources"]["transfer_futures_to_spot"].update(cursor_ms=ms(OLD) - 1))
    collect(d, now=at(OLD, 9 * 24))                                               # the cursor is 9 days behind
    toss_fire(d, 1_000_000, at(OLD, 9 * 24))
    verdict, detail = history(d, at(OLD, 9 * 24))
    assert (verdict, detail["reason"], detail["binance"]) == ("UNCHECKED", "binance_incomplete", "INCOMPLETE")


def test_no_cutover_is_unchecked(tmp_path):
    collect(tmp_path, now=T0)
    assert history(tmp_path, T0) == ("UNCHECKED", {"reason": "no_cutover", "toss": None, "binance": None})


def test_the_readiness_is_never_ready_without_the_history(tmp_path, monkeypatch):
    """Every other check PASS, history UNCHECKED: not ready (the open item of §12.4 closed by PR-B)."""
    d = lane(tmp_path, cutover=OLD, cutover_now=at(OLD, 2))
    monkeypatch.setattr(cash_flows, "toss_semantics", lambda rows, state, *, today_kst: ("PASS", "ok"))
    monkeypatch.setattr(cash_flows, "shadow_record", lambda rows: (OLD, [f"2026-10-{d:02d}" for d in range(9, 17)]))
    deposit = {"id": "d1", "coin": "USDT", "amount": "100.5", "status": 1, "insertTime": ms(OLD) - 600_000}
    collect(d, Feed(crypto_deposit=[deposit]), now="2026-10-17T00:00:00Z")      # a real row: schema VERIFIED
    ready = cash_flows.readiness(d, now="2026-10-17T00:00:00Z", current=CURRENT_OK)
    others = {k: v for k, v in ready["checks"].items() if k != "history_completeness"}
    assert set(others.values()) == {"PASS"}, {k: v for k, v in others.items() if v != "PASS"}
    assert ready["checks"]["history_completeness"] == "UNCHECKED" and ready["ready"] is False
    assert ready["checkpoint_history"] == "UNVERIFIED"


# --- the readers that predate PR-B (D1 condition 4) --------------------------------------------------------

def test_the_new_lines_change_no_reader_that_predates_them(tmp_path):
    d = _whole(tmp_path)
    cash_flows.collect_toss(Toss(), no_cash(), d, now=at(OLD, 3))                # a toss skip
    _state_edit(d, lambda s: s["sources"]["pay"].update(cursor_ms=ms(OLD) - 100 * DAY))
    collect(d, now=at(OLD, 4))                                                   # a flow skip
    rows = ledger(d)
    new = {cash_flows.EVENT_TOSS_SKIPPED, cash_flows.EVENT_FLOW_SKIPPED}
    assert {r["event"] for r in rows} >= new
    old = [r for r in rows if r["event"] not in new]
    for reader in (cash_flows.detected_exceptions, cash_flows.open_exceptions, cash_flows.effective_status,
                   cash_flows.shadow_record, cash_flows.cutover_at, cash_flows.cutover_generation, cash_flows.toss_epoch,
                   cash_flows.lifecycle, cash_flows.post_cutover_exceptions):
        assert reader(rows) == reader(old), reader.__name__
    assert (cash_flows.toss_evidence(rows, epoch=0, today_kst="2026-10-09")
            == cash_flows.toss_evidence(old, epoch=0, today_kst="2026-10-09"))
    summary = cash_flows.summary(d)
    assert summary["events"] == len(rows) and summary["external_flows"] == sum(r["event"] == "flow_recorded" for r in old)
