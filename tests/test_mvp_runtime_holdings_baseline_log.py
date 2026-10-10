"""H5b — every baseline change leaves a chained line (Thomas 2026-10-08, MULTI_ASSET_EXPANSION H5b).

The standing baseline is recorded once (bootstrap) and never reset by the deploy; a reset needs a reason
and is logged before it removes anything; the fire that starts the new peak answers it; a scope change
and a first complete NAV log their own cause; a broken chain stops the block rather than going unnoticed;
no door reads the log.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import baseline_log, binance_wallet, combined

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-08T09:00:00Z"
LATER = "2026-10-08T10:00:00Z"


def _futures(root, at):
    path = root / combined.BINANCE_SNAPSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                "margin_balance": 100.0, "as_of": at}), encoding="utf-8")


def _fire(root, *, at=NOW, total=10_000_000.0, mapping_version=2):
    _futures(root, at)
    zero = {name: 0.0 for name in binance_wallet.CLASSES}
    wallet = binance_wallet.WalletSnapshot(spot_usdt=zero, earn_usdt=dict(zero), unpriced_assets=0,
                                           collected_at=at, latency_ms=0)
    return combined.combine({"known_total_krw": total, "partial": False}, usd_krw_rate=1000.0, root=root, now=at,
                            state_dir=root / "h", wallet=wallet, wallet_status=combined.WALLET_OK, toss_as_of=at,
                            toss_reconciliation_failures=[], mapping_version=mapping_version)


def _events(root):
    return [(row["event"], row.get("cause")) for row in baseline_log.verify(root / "h")]


def test_a_standing_peak_is_recorded_once_and_not_reset(tmp_path):
    hdir = tmp_path / "h"
    hdir.mkdir()
    standing = {"peak_total_krw": 12_345_678.9, "peak_at": "2026-10-08T08:33:54Z",
                "scope_version": combined.PORTFOLIO_SCOPE_VERSION}
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps(standing))
    block = _fire(tmp_path)
    _fire(tmp_path, at=LATER)
    assert _events(tmp_path) == [("baseline_set", "bootstrap")]
    row = baseline_log.verify(hdir)[0]
    assert row["new_baseline_krw"] == 12_345_679 and row["baseline_at"] == standing["peak_at"]
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text())["peak_at"] == standing["peak_at"]
    assert block["baseline_id"] == row["baseline_id"] and block["drawdown_state"] == combined.STATE_CLEAR


def test_a_first_complete_nav_logs_its_baseline(tmp_path):
    block = _fire(tmp_path)
    rows = baseline_log.verify(tmp_path / "h")
    assert [(r["event"], r["cause"]) for r in rows] == [("baseline_set", "first_complete")]
    assert rows[0]["new_baseline_krw"] == 10_100_000 and rows[0]["mapping_version"] == 2
    assert block["baseline_id"] == rows[0]["baseline_id"]


def test_a_reset_needs_a_reason_and_is_answered_by_the_next_baseline(tmp_path):
    hdir = tmp_path / "h"
    _fire(tmp_path)
    with pytest.raises(ToolError) as exc:
        combined.reset_peak(hdir, reason="  ", requested_by="thomas", now=NOW)
    assert exc.value.reason_code == baseline_log.BASELINE_RESET_REFUSED
    assert (hdir / combined.PEAK_FILENAME).exists()                 # nothing removed
    combined.reset_peak(hdir, reason="withdrew 5M", requested_by="thomas", now=NOW)
    assert not (hdir / combined.PEAK_FILENAME).exists()
    request = baseline_log.pending_reset(hdir)
    assert request["reason"] == "withdrew 5M" and request["previous_peak_krw"] == 10_100_000
    block = _fire(tmp_path, at=LATER, total=5_000_000.0)
    rows = baseline_log.verify(hdir)
    assert [(r["event"], r.get("cause")) for r in rows] == [
        ("baseline_set", "first_complete"), ("reset_requested", None), ("baseline_set", "after_reset")]
    assert rows[-1]["answers"] == request["baseline_id"] and rows[-1]["previous_peak_krw"] == 10_100_000
    assert rows[-1]["new_baseline_krw"] == 5_100_000 and block["drawdown_state"] == combined.STATE_INITIALIZED
    assert baseline_log.pending_reset(hdir) is None


def test_a_peak_from_another_scope_logs_a_scope_change(tmp_path):
    hdir = tmp_path / "h"
    hdir.mkdir()
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps({"peak_total_krw": 3_000_000.0, "peak_at": NOW}))
    _fire(tmp_path)
    row = baseline_log.verify(hdir)[-1]
    assert row["cause"] == "scope_change" and row["previous_peak_krw"] == 3_000_000
    assert row["previous_scope_version"] is None and row["scope_version"] == combined.PORTFOLIO_SCOPE_VERSION


def test_an_incomplete_fire_starts_no_baseline(tmp_path):
    _futures(tmp_path, NOW)
    combined.combine({"known_total_krw": 1.0, "partial": False}, usd_krw_rate=1000.0, root=tmp_path, now=NOW,
                     state_dir=tmp_path / "h", toss_as_of=NOW, toss_reconciliation_failures=[])
    assert not baseline_log.path(tmp_path / "h").exists()


def test_a_broken_chain_is_refused_and_no_baseline_moves(tmp_path):
    hdir = tmp_path / "h"
    _fire(tmp_path)
    combined.reset_peak(hdir, reason="r", requested_by="thomas", now=NOW)
    lines = baseline_log.path(hdir).read_text(encoding="utf-8").splitlines()
    edited = json.loads(lines[0])
    edited["new_baseline_krw"] = 1
    baseline_log.path(hdir).write_text("\n".join([json.dumps(edited), *lines[1:]]) + "\n", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        _fire(tmp_path, at=LATER)
    assert exc.value.reason_code == baseline_log.BASELINE_LOG_TAMPERED
    assert not (hdir / combined.PEAK_FILENAME).exists()             # the reset stays unanswered


def test_the_block_carries_the_id_and_no_door_reads_the_log(tmp_path):
    assert "baseline_id" in combined.COMBINED_KEYS
    allowed = {ROOT / "runtime" / "mvp_runtime" / "holdings" / n for n in ("baseline_log.py", "combined.py")}
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == baseline_log.FILENAME for n in ast.walk(tree)) or any(
                isinstance(n, ast.ImportFrom) and (n.module or "").endswith("baseline_log")
                or isinstance(n, ast.ImportFrom) and any(a.name == "baseline_log" for a in n.names)
                for n in ast.walk(tree)):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# A new baseline starts with nothing told (found 2026-10-09 after H2). ``reset_peak`` removes the told mark
# with the peak; the baselines ``combine`` starts itself (scope_change, first_complete) once did not, so a
# mark from the old baseline judged the new one: a "back within the limit" message for a baseline that
# never fell, and silence for a real breach whose state equalled the stale mark.

def _told_breached(hdir, *, peak=None):
    hdir.mkdir(parents=True, exist_ok=True)
    if peak is not None:
        (hdir / combined.PEAK_FILENAME).write_text(json.dumps(peak))
    combined.write_told(hdir, combined.STATE_BREACHED, now=NOW)


def _edge(root, block):
    return combined.alert(block, told=combined.read_told(root / "h"), as_of=NOW)


@pytest.mark.parametrize("peak, cause", [
    ({"peak_total_krw": 30_000_000.0, "peak_at": NOW}, "scope_change"),   # S1: a peak from another scope
    (None, "first_complete"),                                              # S2: no peak at all
], ids=["S1-scope-change", "S2-first-complete"])
def test_a_new_baseline_carries_no_told_mark_from_the_old_one(tmp_path, peak, cause):
    hdir = tmp_path / "h"
    _told_breached(hdir, peak=peak)
    first = _fire(tmp_path)
    row = baseline_log.verify(hdir)[-1]
    assert row["cause"] == cause and first["drawdown_state"] == combined.STATE_INITIALIZED
    standing = json.loads((hdir / combined.PEAK_FILENAME).read_text())
    assert (row["new_baseline_krw"], row["baseline_at"]) == (int(round(standing["peak_total_krw"])), standing["peak_at"])
    assert combined.read_told(hdir) == combined.STATE_CLEAR and _edge(tmp_path, first) is None
    second = _fire(tmp_path, at=LATER)                              # unchanged: clear, and nothing to say
    assert second["drawdown_state"] == combined.STATE_CLEAR and _edge(tmp_path, second) is None


def test_s3_a_real_breach_under_the_new_baseline_is_told(tmp_path):
    hdir = tmp_path / "h"
    _told_breached(hdir, peak={"peak_total_krw": 30_000_000.0, "peak_at": NOW})
    _fire(tmp_path)                                                  # new scope baseline: 10.1 M
    down = _fire(tmp_path, at=LATER, total=5_000_000.0)              # 5.1 M: about -50 %
    assert down["drawdown_state"] == combined.STATE_BREACHED
    edge = _edge(tmp_path, down)
    assert edge is not None and edge[0] == combined.STATE_BREACHED


def test_s4_an_explicit_reset_behaves_as_before(tmp_path):
    hdir = tmp_path / "h"
    _fire(tmp_path)
    combined.write_told(hdir, combined.STATE_BREACHED, now=NOW)
    assert combined.reset_peak(hdir, reason="withdrawal", requested_by="thomas", now=NOW) == [
        combined.PEAK_FILENAME, combined.ALERT_MARK_FILENAME]
    again = _fire(tmp_path, at=LATER, total=5_000_000.0)
    assert baseline_log.verify(hdir)[-1]["cause"] == "after_reset"
    assert again["drawdown_state"] == combined.STATE_INITIALIZED and _edge(tmp_path, again) is None
    down = _fire(tmp_path, at=LATER, total=2_000_000.0)
    assert _edge(tmp_path, down)[0] == combined.STATE_BREACHED


def test_a_stop_between_the_mark_and_the_peak_starts_the_baseline_again(monkeypatch, tmp_path):
    """The mark goes before the peak is written: a process stopped in between leaves no peak, so the next
    fire starts the baseline again (one more baseline line) and never judges it against the stale mark."""
    hdir = tmp_path / "h"
    _told_breached(hdir, peak={"peak_total_krw": 30_000_000.0, "peak_at": NOW})
    real = combined._write_peak

    def _stop(*_a, **_kw):
        raise OSError("stopped")

    monkeypatch.setattr(combined, "_write_peak", _stop)
    with pytest.raises(OSError):
        _fire(tmp_path)
    assert combined.read_told(hdir) == combined.STATE_CLEAR
    monkeypatch.setattr(combined, "_write_peak", real)
    block = _fire(tmp_path, at=LATER)
    assert block["drawdown_state"] == combined.STATE_INITIALIZED
    assert [cause for event, cause in _events(tmp_path) if event == "baseline_set"] == ["scope_change"] * 2
