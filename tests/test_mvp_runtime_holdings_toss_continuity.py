"""C7 D2 (Thomas 2026-10-11): the Toss window continuity diagnostic, ``holdings_board --toss-continuity``.

Synthetic ledgers only, written through the real ``_commit`` (so the chain and the H6d-min field check
apply). The diagnostic reads; it writes nothing, changes no verdict, and names no amount. A gap between
windows is a fire that recorded none, not a lost record; nothing can explain one yet, so it is never
read as normal. Evidence it cannot judge is INCOMPLETE_EVIDENCE, never CONTINUOUS.
"""

from __future__ import annotations

import datetime
import json

import pytest

from runtime.mvp_runtime.cli_common import EXIT_BLOCKED, EXIT_OK
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import cash_flows

T0 = datetime.datetime(2026, 10, 9, 4, 0, tzinfo=datetime.timezone.utc)


def at(hours: float) -> str:
    return (T0 + datetime.timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


NOW = at(12)


def window(start: float, end: float, *, key: str | None = None, truncated: bool = False, epoch: int = 1) -> dict:
    s, e = at(start), at(end)
    return {"event": cash_flows.EVENT_TOSS_WINDOW, "source": "toss", "source_event_key": key or f"toss:window:{s}..{e}",
            "window_start": s, "window_end": e, "epoch": epoch, "reconciliation_fingerprint": "synthetic",
            "buy_orders": 0, "sell_orders": 0, "settlements": 0, "residual_status": {"KRW": "CLEAR", "USD": "CLEAR"},
            "orders_truncated": truncated, "exchange_pair": False, "order_hash_basis": "synthetic",
            "identity_stable": True, "orders_query": {}, "source_confidence": "RECONCILED",
            "accounting_status": "SHADOW_ONLY"}


def amendment(target_key: str, start: float, end: float) -> dict:
    return {"event": cash_flows.EVENT_TOSS_AMENDED, "source": "toss", "source_event_key": f"toss:amend:{target_key}:x",
            "amends": target_key, "kind": "late_fill", "window_start": at(start), "window_end": at(end)}


def write(state_dir, *rows) -> None:
    cash_flows._commit(state_dir, now=NOW, rows=lambda _existing, _state: list(rows))


def cutover(state_dir, hours: float = 0, *, migrate: bool = False) -> None:
    cash_flows.set_cutover(state_dir, at=at(hours), requested_by="thomas", migrate=migrate,
                           reason="synthetic" if migrate else None)


def judge(state_dir) -> dict:
    return cash_flows.toss_continuity_view(state_dir, now=NOW)


# A. every window joins the next at the same instant, from the cutover on
def test_a_windows_that_tile_the_stretch_are_continuous(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(-1, 1), window(1, 2), window(2, 3))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_CONTINUOUS and result["reasons"] == []
    assert (result["windows"], result["leading_hours"], result["trailing_hours"]) == (3, 0.0, 9.0)


# B. sharing the boundary instant is how the windows are built; a strict overlap is not
def test_b_a_shared_boundary_is_continuous_and_a_strict_overlap_is_incomplete(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 2), window(2, 4))
    assert judge(tmp_path)["status"] == cash_flows.TOSS_CONTINUOUS
    write(tmp_path, window(3, 5))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_INCOMPLETE_EVIDENCE
    assert result["overlaps"] == 1 and result["reasons"] == ["overlap"]


# C. a stretch no window covers is an unexplained gap, with its bounds and length
def test_c_an_uncovered_stretch_is_an_unexplained_gap(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(1, 2), window(3, 4))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_UNEXPLAINED_GAP
    assert result["gaps"] == [{"from": at(2), "to": at(3), "hours": 1.0}] and result["gap_hours"] == 1.0


# D. the same window twice (only a mixed restore makes one) is counted once and not read as normal
def test_d_a_duplicate_window_is_counted_once_and_is_incomplete(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(0, 1, key="toss:window:copy"), window(1, 2))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_INCOMPLETE_EVIDENCE
    assert (result["windows"], result["duplicates"], result["reasons"]) == (2, 1, ["duplicate_window"])


# E. an amendment withdraws a window from the evidence but not from the observed time
def test_e_an_amended_window_still_covers_its_time_and_an_orphan_amendment_does_not_pass(tmp_path):
    cutover(tmp_path, 0)
    first = window(0, 1)
    write(tmp_path, first, window(1, 2), amendment(first["source_event_key"], 0, 1))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_CONTINUOUS and result["amended_windows"] == 1
    write(tmp_path, amendment("toss:window:nowhere", 5, 6))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_INCOMPLETE_EVIDENCE and result["reasons"] == ["orphan_amendment"]


# F. windows that end before the cutover are counted apart and judge nothing
def test_f_windows_before_the_cutover_are_left_out(tmp_path):
    cutover(tmp_path, 5)
    write(tmp_path, window(0, 1), window(2, 3), window(4, 6), window(6, 7))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_CONTINUOUS
    assert (result["windows_before_cutover"], result["windows"], result["gaps"]) == (2, 2, [])


# G. a migrated cutover starts a new generation; the old one's gap is not this one's
def test_g_only_the_latest_cutover_generation_is_judged(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(2, 3), window(3, 6), window(6, 8))
    assert judge(tmp_path)["status"] == cash_flows.TOSS_UNEXPLAINED_GAP
    cutover(tmp_path, 4, migrate=True)
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_CONTINUOUS and result["cutover_generation"] == 2
    assert result["windows_before_cutover"] == 2 and result["leading_hours"] == 0.0


# H. the writer refuses a window without its fields; a field that is there but not a time is unreadable
def test_h_a_missing_field_is_refused_at_write_and_an_unreadable_bound_is_incomplete(tmp_path):
    cutover(tmp_path, 0)
    broken = window(0, 1)
    broken.pop("window_end")
    with pytest.raises(ToolError) as exc:
        write(tmp_path, broken)
    assert exc.value.reason_code == cash_flows.EVENT_INVALID
    odd = window(1, 2, key="toss:window:odd")
    odd["window_end"] = "not a time"
    write(tmp_path, window(0, 1), odd)
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_INCOMPLETE_EVIDENCE
    assert result["unreadable"] == 1 and result["reasons"] == ["window_unreadable"]


# I. a chain that does not verify is LEDGER_INVALID with its code, and the CLI exits blocked
def test_i_a_broken_chain_is_ledger_invalid(tmp_path, monkeypatch, capsys):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(1, 2))
    path = cash_flows.ledger_path(tmp_path)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["edited"] = True
    lines[1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_LEDGER_INVALID and result["reasons"] == [cash_flows.LEDGER_TAMPERED]
    board = _board(monkeypatch, tmp_path)
    assert board.main(["--toss-continuity"]) == EXIT_BLOCKED
    assert capsys.readouterr().out.startswith("toss continuity: LEDGER_INVALID")


# J. nothing to judge is UNCHECKED: no ledger at all, or windows with no cutover
def test_j_an_empty_ledger_or_no_cutover_is_unchecked(tmp_path):
    result = judge(tmp_path)
    assert (result["status"], result["reasons"]) == (cash_flows.TOSS_UNCHECKED, ["empty_ledger"])
    write(tmp_path, window(0, 1))
    result = judge(tmp_path)
    assert (result["status"], result["reasons"]) == (cash_flows.TOSS_UNCHECKED, ["no_cutover"])


# K. a downtime is one long window from the last cash point, not a gap
def test_k_a_stopped_observation_resumes_as_one_long_window(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(1, 9), window(9, 10))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_CONTINUOUS and result["longest_window_hours"] == 8.0


# L. evidence the check cannot judge: no window before the first one, a truncated order list, no window at all
def test_l_incomplete_past_evidence_is_never_continuous(tmp_path):
    cutover(tmp_path, 0)
    assert judge(tmp_path)["reasons"] == ["no_window_since_cutover"]
    write(tmp_path, window(3, 4), window(4, 5, truncated=True))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_INCOMPLETE_EVIDENCE
    assert result["reasons"] == ["no_window_before_first", "orders_truncated"] and result["leading_hours"] == 3.0


def test_a_gap_outranks_incomplete_evidence(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(2, 3), window(4, 5))
    result = judge(tmp_path)
    assert result["status"] == cash_flows.TOSS_UNEXPLAINED_GAP
    assert result["reasons"] == ["unexplained_gap", "no_window_before_first"]


def test_the_diagnostic_writes_nothing_and_adds_nothing_to_the_outward_summary(tmp_path):
    cutover(tmp_path, 0)
    write(tmp_path, window(0, 1), window(2, 3))
    files = (cash_flows.ledger_path(tmp_path).read_bytes(), cash_flows.state_path(tmp_path).read_bytes())
    before = cash_flows.summary(tmp_path)
    judge(tmp_path)
    assert (cash_flows.ledger_path(tmp_path).read_bytes(), cash_flows.state_path(tmp_path).read_bytes()) == files
    assert cash_flows.summary(tmp_path) == before
    assert not any("continuity" in key for key in before)


def _board(monkeypatch, state_dir):
    from scripts import holdings_board

    monkeypatch.setattr(holdings_board, "state_dir", lambda: state_dir)
    return holdings_board


def test_the_terminal_names_the_verdict_first_and_no_amount(tmp_path, monkeypatch, capsys):
    cutover(tmp_path, 0)
    residual = {"event": cash_flows.EVENT_TOSS_RESIDUAL, "source": "toss",
                "source_event_key": f"toss:residual:KRW:{at(1)}..{at(2)}", "pocket": "KRW",
                "window_start": at(1), "window_end": at(2), "residual": "987654321", "orders_truncated": False,
                "source_confidence": "UNRESOLVED", "valuation_status": "UNVALUED", "accounting_status": "SHADOW_ONLY"}
    write(tmp_path, window(0, 1), window(2, 3), residual)
    board = _board(monkeypatch, tmp_path)
    assert board.main(["--toss-continuity"]) == EXIT_OK
    out = capsys.readouterr().out
    first, *rest = out.splitlines()
    assert first == "toss continuity: UNEXPLAINED_GAP (cutover generation 1)"
    assert f"  gap      : {at(1)} .. {at(2)} (1.0 h)" in rest
    assert "987654321" not in out and "residual" not in out.lower()
