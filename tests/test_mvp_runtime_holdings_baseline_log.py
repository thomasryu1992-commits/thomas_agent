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
