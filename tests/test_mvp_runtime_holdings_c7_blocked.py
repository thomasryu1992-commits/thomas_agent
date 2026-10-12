"""C7 PR-D (HOLDINGS_LEDGER_CHECKPOINT_V0.1.md §6; Thomas 2026-10-11, review PR): BLOCKED, told on its own path.

A cash-flow ledger or state that every writer refuses used to send nothing: every other H6 message is
computed from a readable ledger (C7-D). The BLOCKED judgement reads the ledger and the state itself,
read-only, after the holdings fire — whatever refresh returned — and tells each failure once, its
clearing once, and a recurrence again. The told file moves only after a delivery. Synthetic only.
"""

from __future__ import annotations

import ast
import json
import pathlib
import re
from unittest import mock

import pytest

from runtime.mvp_runtime import operator as operator_mod
from runtime.mvp_runtime import scheduler
from runtime.mvp_runtime.errors import MvpRuntimeError, ToolError
from runtime.mvp_runtime.holdings import binance_wallet, cash_flows, combined, store
from runtime.mvp_runtime.holdings.model import HoldingsSnapshot, MarketTotals
from tests._h6d_support import CUTOVER, Feed, ms, pay, set_cutover

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-09T05:00:00Z"
LATER = "2026-10-09T06:00:00Z"
LATEST = "2026-10-09T07:00:00Z"


# --- synthetic lanes -------------------------------------------------------------------------------------

def lane(root: pathlib.Path) -> pathlib.Path:
    """A working H6 lane under ``root``: the cutover and one fire with a Pay row (an open exception). The
    cutover line is stamped with the wall clock, so it is pinned to the synthetic day: the ledger's first
    line is older than every told record here, as a real lane's is (the clearing reads it)."""
    d = store.state_dir(root)
    with mock.patch.object(cash_flows.timeutil, "utc_now_iso", return_value=CUTOVER):
        set_cutover(d)
    cash_flows.collect_binance(Feed(pay=[pay("p1")]), d, now=NOW, now_ms=ms(NOW))
    return d


def break_chain(d: pathlib.Path) -> None:
    path = cash_flows.ledger_path(d)
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[1])
    row["edited"] = True                                                      # a middle line, hash kept
    lines[1] = json.dumps(row)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def break_state(d: pathlib.Path) -> None:
    cash_flows.state_path(d).write_text("{not json", encoding="utf-8")


def make_state_unreadable(d: pathlib.Path) -> None:
    """Root reads any file, so an unreadable state is a directory in its place (reading raises OSError)."""
    path = cash_flows.state_path(d)
    path.unlink()
    path.mkdir()


def files(d: pathlib.Path) -> dict[str, bytes]:
    """Every file in the holdings directory but the told record, as bytes."""
    return {p.name: p.read_bytes() for p in sorted(d.iterdir())
            if p.is_file() and p.name != cash_flows.BLOCKED_TOLD_FILENAME}


def deliver(d: pathlib.Path, now: str) -> tuple[str, str] | None:
    """One check, delivered at once; the alert it carried."""
    result = cash_flows.blocked_alert(d, now=now)
    if result["alert"] is not None:
        assert cash_flows.mark_blocked_told(d, token=result["alert"][0], now=now) is True
    return result["alert"]


# --- the scheduler's holdings fire, with the real refresh -----------------------------------------------

def patch_full_fire(monkeypatch, root: pathlib.Path, *, now: str = NOW) -> None:
    """Everything refresh reads, synthetic: a Toss snapshot, the Binance futures file, the wallet, a quiet
    flow feed. No network, no real state."""
    futures = root / combined.BINANCE_SNAPSHOT_REL
    futures.parent.mkdir(parents=True, exist_ok=True)
    futures.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                   "margin_balance": 100.0, "as_of": now}), encoding="utf-8")
    zero = {name: 0.0 for name in binance_wallet.CLASSES}
    wallet = binance_wallet.WalletSnapshot(spot_usdt=zero, earn_usdt=dict(zero), unpriced_assets=0,
                                           collected_at=now, latency_ms=0)
    toss = HoldingsSnapshot(account="****", broker="toss",
                            domestic=MarketTotals(market="domestic", holdings_value_krw=0.0, unrealized_pnl_krw=0.0,
                                                  cash_krw=1_000_000.0),
                            overseas=MarketTotals(market="overseas", holdings_value_krw=0.0, unrealized_pnl_krw=0.0,
                                                  cash_krw=0.0),
                            holdings=(), collected_at=now, latency_ms=1, usd_krw_rate=1400.0)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (toss, None))
    monkeypatch.setattr(store, "_feed", lambda: None)
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_: (wallet, None))
    capable = binance_wallet.BinanceWalletFeed()
    monkeypatch.setattr(capable, "flow_history", Feed().flow_history)
    monkeypatch.setattr(binance_wallet, "select_wallet_feed", lambda: capable)


class Channel:
    def __init__(self, monkeypatch):
        self.sent: list[str] = []
        self.down = False
        monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_: object())
        monkeypatch.setattr(operator_mod, "notify_operator", self._notify)

    def _notify(self, _channel, text, **_kw):
        if self.down:
            raise MvpRuntimeError("OPERATOR_CHANNEL_DOWN", "down")
        self.sent.append(text)

    def blocked(self) -> list[str]:
        return [t for t in self.sent if "BLOCKED" in t]


def holdings_fire(root: pathlib.Path, now: str = NOW) -> str:
    schedule = scheduler.Schedule(schedule_id="s", kind=scheduler.KIND_HOLDINGS, request="", interval_seconds=3600,
                                  enabled=True, created_by="test", created_at=now, next_run_at=now)
    return scheduler._execute(schedule, now=now, ledger=None, working_memory=None, programization=None,
                              repo_root=root, executor=lambda **_: {})


# --- 1–8: what is BLOCKED ----------------------------------------------------------------------------------

def test_1_a_broken_chain_is_blocked_with_its_reason_code(tmp_path):
    d = lane(tmp_path)
    assert cash_flows.blocked_state(d) == {"blocked": False, "verified_since": CUTOVER}
    break_chain(d)
    judged = cash_flows.blocked_state(d)
    assert judged["blocked"] and judged["reason_code"] == cash_flows.LEDGER_TAMPERED
    assert judged["category"] == "verify" and judged["key"].startswith("v1:state:verify:")


def test_2_a_broken_chain_and_a_broken_state_are_one_failure(tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    break_state(d)
    assert deliver(d, NOW) is not None
    assert deliver(d, LATER) is None                                          # told once


def test_3_a_missing_state_alone_is_the_recovery_path_and_with_a_broken_chain_is_blocked(tmp_path):
    d = lane(tmp_path)
    cash_flows.state_path(d).unlink()
    assert cash_flows.blocked_state(d) == {"blocked": False, "verified_since": CUTOVER}  # C7-11 re-reads
    break_chain(d)
    assert cash_flows.blocked_state(d)["key"] == "v1:state:verify:missing"


def test_4_an_unparsable_state_is_blocked_at_load_state(tmp_path):
    d = lane(tmp_path)
    break_state(d)
    judged = cash_flows.blocked_state(d)
    assert (judged["category"], judged["reason_code"]) == ("load_state", cash_flows.LEDGER_TAMPERED)


def test_5_an_unreadable_state_is_blocked_and_keyed_as_unreadable(tmp_path):
    d = lane(tmp_path)
    make_state_unreadable(d)
    judged = cash_flows.blocked_state(d)
    assert (judged["category"], judged["key"]) == ("load_state", "v1:state:load_state:unreadable")


def _toss_read_fails(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (_ for _ in ()).throw(RuntimeError("toss down")))


def _broker_not_configured(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (None, "NOT_CONFIGURED"))


def _degraded(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (None, "TOSS_SYNTHETIC_DOWN"))


def _mark_not_written(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    real = store._write_json

    def write(path, body, *, code, label):
        if label == "holdings refresh mark":
            raise OSError("mark")
        return real(path, body, code=code, label=label)
    monkeypatch.setattr(store, "_write_json", write)


def _not_disclosed(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    monkeypatch.setattr(store.disclosure, "external_view", lambda *_a, **_k: (_ for _ in ()).throw(ValueError("x")))


def _not_persisted(monkeypatch, root):
    patch_full_fire(monkeypatch, root)
    real = store._write_json

    def write(path, body, *, code, label):
        if label == "holdings snapshot":
            raise OSError("snapshot")
        return real(path, body, code=code, label=label)
    monkeypatch.setattr(store, "_write_json", write)


EARLY_RETURNS = {"toss read failed": (_toss_read_fails, "read failed"),
                 "no broker": (_broker_not_configured, "no broker account configured"),
                 "degraded": (_degraded, "degraded"),
                 "mark not written": (_mark_not_written, "mark not written"),
                 "not disclosed": (_not_disclosed, "not disclosed"),
                 "not persisted": (_not_persisted, "not persisted")}


# 6 and 7: an early return of refresh and a broken ledger at once — the alert is not lost.
@pytest.mark.parametrize("path", sorted(EARLY_RETURNS))
def test_6_7_every_early_return_of_refresh_still_tells_a_broken_ledger(monkeypatch, tmp_path, path):
    d = lane(tmp_path)
    break_chain(d)
    setup, said = EARLY_RETURNS[path]
    setup(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    status = holdings_fire(tmp_path)
    assert said in status and "h6 blocked told" in status
    assert len(channel.blocked()) == 1 and cash_flows.LEDGER_TAMPERED in channel.blocked()[0]


def test_8_a_lock_is_never_read_as_a_broken_ledger(monkeypatch, tmp_path):
    d = lane(tmp_path)
    with cash_flows._state_lock(d):                                           # a writer holds the state
        assert not cash_flows.blocked_state(d)["blocked"]                     # the judgement takes no lock
    patch_full_fire(monkeypatch, tmp_path)
    monkeypatch.setattr(cash_flows, "collect_binance", lambda *_a, **_k: (_ for _ in ()).throw(
        ToolError(cash_flows.STATE_LOCKED, "busy")))
    channel = Channel(monkeypatch)
    status = holdings_fire(tmp_path)
    snapshot = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert snapshot["cash_flows"]["error"] == cash_flows.STATE_LOCKED            # the code, not "ToolError"
    assert channel.blocked() == [] and "h6 blocked" not in status


# --- 9–17: told, cleared, told again ----------------------------------------------------------------------

def test_9_10_the_first_alert_is_once_and_a_standing_failure_is_not_repeated(tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    first = deliver(d, NOW)
    assert first is not None and first[0].startswith("blocked|v1:state:verify:")
    assert [deliver(d, at) for at in (LATER, LATEST)] == [None, None]


def test_11_an_undelivered_alert_is_offered_again(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    channel.down = True
    assert "h6 blocked not sent:OPERATOR_CHANNEL_DOWN" in holdings_fire(tmp_path)
    assert not cash_flows.blocked_told_path(d).exists()                       # nothing recorded
    channel.down = False
    assert "h6 blocked told" in holdings_fire(tmp_path, LATER) and len(channel.blocked()) == 1
    assert "h6 blocked" not in holdings_fire(tmp_path, LATEST)               # and then not again


def test_12_13_a_told_record_that_cannot_be_written_is_never_counted_and_the_alert_repeats(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)

    def fails(*_a, **_k):
        raise OSError("disk")
    monkeypatch.setattr(cash_flows, "mark_blocked_told", fails)
    status = holdings_fire(tmp_path)
    assert "h6 blocked told not recorded:OSError" in status and not re.search(r"h6 blocked told(;|$)", status)
    assert not cash_flows.blocked_told_path(d).exists()
    holdings_fire(tmp_path, LATER)
    assert len(channel.blocked()) == 2                                         # repeated, never hidden


def test_14_a_cleared_failure_is_told_once(tmp_path):
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    deliver(d, NOW)
    cash_flows.ledger_path(d).write_bytes(good)                               # the ledger is put right
    cleared = deliver(d, LATER)
    assert cleared is not None and cleared[0].startswith("resolved|") and "해소" in cleared[1]
    assert deliver(d, LATEST) is None


def test_15_a_recurrence_after_the_clearing_is_told_again_even_with_the_same_key(tmp_path):
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    first = deliver(d, NOW)
    cash_flows.ledger_path(d).write_bytes(good)
    deliver(d, LATER)                                                          # the clearing is told
    break_chain(d)                                                             # nothing wrote in between
    again = deliver(d, LATEST)
    assert again is not None and again[0] == first[0]                          # same key, told again


def test_a_failure_that_changes_its_check_while_standing_is_told_again_a_known_repeat(tmp_path):
    """The chain is judged before the state, so a state failure that a chain break then overtakes changes
    the key: told again with no clearing between, and once more when only the state failure is left. A
    repeat, never a miss — pinned so a reviewer sees the count is intended."""
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_state(d)
    assert deliver(d, NOW)[0].startswith("blocked|v1:state:load_state:")
    break_chain(d)
    assert deliver(d, LATER)[0].startswith("blocked|v1:state:verify:")       # second, nothing cleared
    cash_flows.ledger_path(d).write_bytes(good)
    assert deliver(d, LATEST)[0].startswith("blocked|v1:state:load_state:")  # third: the state still broken
    assert deliver(d, "2026-10-09T08:00:00Z") is None


def test_16_a_restart_keeps_the_told_record_and_an_unreadable_one_repeats_rather_than_hides(tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    deliver(d, NOW)
    assert cash_flows.blocked_alert(d, now=LATER)["alert"] is None             # read back from disk
    cash_flows.blocked_told_path(d).write_text("{broken", encoding="utf-8")
    result = cash_flows.blocked_alert(d, now=LATER)
    assert result["alert"] is not None and result["note"].startswith("told record unreadable")


def test_17_the_key_carries_its_version(tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    assert cash_flows.blocked_state(d)["key"].startswith(cash_flows.BLOCKED_KEY_STATE + ":")
    assert cash_flows.mark_blocked_told(d, token="unknown|x", now=NOW) is False  # a token it does not know


# --- 18–24: order, isolation, nothing else moves -----------------------------------------------------------

def test_18_a_fire_on_a_broken_ledger_saves_no_state_so_the_key_holds(monkeypatch, tmp_path):
    """The key rests on the fire's order: summary() fails before update_readiness and mark_fire_verified,
    the only writers that save the state without verifying the ledger. Pinned here, not assumed."""
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    Channel(monkeypatch)
    before = cash_flows.state_path(d).read_bytes()
    key = cash_flows.blocked_state(d)["key"]
    holdings_fire(tmp_path)
    holdings_fire(tmp_path, LATER)
    assert cash_flows.state_path(d).read_bytes() == before
    assert cash_flows.blocked_state(d)["key"] == key


def test_19_23_24_the_judgement_and_its_record_touch_nothing_else(tmp_path):
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    snapshot = files(d)
    deliver(d, NOW)
    assert files(d) == snapshot                                               # ledger, state, peak, told
    cash_flows.ledger_path(d).write_bytes(good)
    snapshot = files(d)
    deliver(d, LATER)
    assert files(d) == snapshot
    assert not (d / combined.ALERT_MARK_FILENAME).exists()                    # the drawdown mark untouched
    state = cash_flows.load_state(d)
    assert not (state.get("readiness") or {}).get("ready")                   # nothing advanced toward H6b/H6c


def test_20_nothing_that_identifies_leaves(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    status = holdings_fire(tmp_path)
    key = cash_flows.blocked_state(d)["key"]
    digest = key.rsplit(":", 1)[1]
    text = channel.blocked()[0]
    outward = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    for shown in (text, status, outward):
        assert digest not in shown and key not in shown
    assert "p1" not in text and "USDT" not in text and "1,000,000" not in text
    cash_flows.blocked_told_path(d).write_text("{broken", encoding="utf-8")   # the note reaches the status line
    note = cash_flows.blocked_alert(d, now=LATER)["note"]
    assert note and digest not in note and key not in note


def test_21_a_detection_marker_is_blocked_on_its_checkpoint_not_on_the_state(tmp_path):
    d = lane(tmp_path)
    marker = {"checkpoint": {"lines": 3, "sha256": "ab" * 32}}

    def plant(state, _after):
        state[cash_flows.DETECTION_MARKER_KEY] = marker
    cash_flows._commit(d, now=NOW, rows=lambda _e, _s: [], patch=plant)
    judged = cash_flows.blocked_state(d)
    assert (judged["category"], judged["key"]) == ("marker", "v2:ckpt:3:" + "ab" * 8)
    assert judged["reason_code"] == cash_flows.LEDGER_TAMPERED               # PR-A decides its own code
    deliver(d, NOW)
    cash_flows.collect_binance(Feed(pay=[pay("p2", at="2026-10-09T05:30:00Z")]), d, now=LATER, now_ms=ms(LATER))
    assert cash_flows.blocked_alert(d, now=LATER)["alert"] is None            # writes go on; still one alert

    def malformed(state, _after):
        state[cash_flows.DETECTION_MARKER_KEY] = {"checkpoint": "?"}
    cash_flows._commit(d, now=LATEST, rows=lambda _e, _s: [], patch=malformed)
    assert cash_flows.blocked_state(d)["key"] == "v2:ckpt:malformed"


def test_22_the_blocked_path_reaches_no_trading_code():
    for rel in ("runtime/mvp_runtime/holdings/cash_flows.py", "runtime/mvp_runtime/holdings/store.py"):
        tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
        modules = [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
        assert not [m for m in modules if "crypto" in m or "live" in m], rel


def test_the_board_names_the_reason_code_not_the_class(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    store.refresh(now=NOW, root=tmp_path)
    snapshot = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))
    assert snapshot["cash_flows"]["error"] == cash_flows.LEDGER_TAMPERED


def test_no_lane_is_not_blocked(tmp_path):
    assert cash_flows.blocked_state(store.state_dir(tmp_path)) == {"blocked": False, "verified_since": None}
    assert store.blocked_check(now=NOW, root=tmp_path) == {"alert": None, "note": None}


def test_a_judgement_that_cannot_run_is_blocked_not_fine(monkeypatch, tmp_path):
    d = lane(tmp_path)
    monkeypatch.setattr(cash_flows, "legacy_import_refusal", lambda *_a: (_ for _ in ()).throw(KeyError("x")))
    assert cash_flows.blocked_state(d)["category"] == "check"
    monkeypatch.setattr(cash_flows, "blocked_alert", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("bug")))
    assert store.blocked_check(now=NOW, root=tmp_path)["alert"] is not None


def test_the_told_file_stays_out_of_every_door():
    """The told record joins the LOCAL_ONLY files: only the lane's own modules name it."""
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path.name == "cash_flows.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == cash_flows.BLOCKED_TOLD_FILENAME for n in ast.walk(tree)):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# --- review 2026-10-12, P0: nothing to verify is not a verification ----------------------------------------

def _standing(d: pathlib.Path) -> dict:
    told, note = cash_flows.read_blocked_told(d)
    assert note is None
    return told


def test_case_a_a_first_run_sends_nothing_and_initializes_as_before(monkeypatch, tmp_path):
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    status = holdings_fire(tmp_path)
    d = store.state_dir(tmp_path)
    assert "refreshed" in status and "h6 blocked" not in status
    assert channel.blocked() == [] and not cash_flows.blocked_told_path(d).exists()
    assert cash_flows.state_path(d).exists()                                  # refresh initialized the lane


def test_case_b_both_files_gone_after_a_told_failure_is_never_told_as_cleared(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    assert "h6 blocked told" in holdings_fire(tmp_path)
    told = _standing(d)
    cash_flows.ledger_path(d).unlink()
    cash_flows.state_path(d).unlink()
    assert cash_flows.blocked_alert(d, now=LATER) == {"alert": None, "note": "clearing withheld (nothing verified)"}
    status = holdings_fire(tmp_path, LATER)                                   # refresh rebuilds a state only
    assert "h6 blocked clearing withheld (nothing verified)" in status
    assert len(channel.blocked()) == 1 and not [t for t in channel.blocked() if "해소" in t]
    assert _standing(d) == told                                               # still standing, not resolved
    assert told["key"] not in status


def test_case_b_a_ledger_begun_after_the_failure_is_not_its_clearing(tmp_path):
    """Refresh starts a new chain where the old one vanished; its first line is newer than the told
    failure, so it verifies but is not the ledger that failed."""
    d = lane(tmp_path)
    break_chain(d)
    deliver(d, NOW)
    cash_flows.ledger_path(d).unlink()
    cash_flows.state_path(d).unlink()
    cash_flows.collect_binance(Feed(pay=[pay("p9", at="2026-10-09T06:30:00Z")]), d, now=LATEST, now_ms=ms(LATEST))
    assert cash_flows.blocked_state(d) == {"blocked": False, "verified_since": LATEST}
    result = cash_flows.blocked_alert(d, now=LATEST)
    assert result == {"alert": None, "note": "clearing withheld (the ledger began after the failure was told)"}
    assert _standing(d)["resolved_told_at"] is None


def test_case_b_a_ledger_gone_beside_a_readable_state_is_not_a_clearing(tmp_path):
    """The missing ledger verifies vacuously (an empty chain): not BLOCKED, and not verified either."""
    d = lane(tmp_path)
    break_chain(d)
    deliver(d, NOW)
    cash_flows.ledger_path(d).unlink()
    assert cash_flows.blocked_state(d) == {"blocked": False, "verified_since": None}
    assert deliver(d, LATER) is None and _standing(d)["resolved_told_at"] is None


def test_case_c_a_ledger_put_right_is_told_cleared_exactly_once(monkeypatch, tmp_path):
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    holdings_fire(tmp_path)
    cash_flows.ledger_path(d).write_bytes(good)                               # restored from the good copy
    assert "h6 blocked told" in holdings_fire(tmp_path, LATER)
    holdings_fire(tmp_path, LATEST)
    holdings_fire(tmp_path, "2026-10-09T08:00:00Z")
    cleared = [t for t in channel.blocked() if "해소" in t]
    assert len(channel.blocked()) == 2 and len(cleared) == 1
    assert _standing(d)["resolved_told_at"] == LATER


def test_case_d_a_check_that_raises_never_reads_as_cleared(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    deliver(d, NOW)
    key = _standing(d)["key"]

    def raises(*_a, **_k):
        raise RuntimeError("verify could not run")
    monkeypatch.setattr(cash_flows, "verify", raises)
    token, _text = cash_flows.blocked_alert(d, now=LATER)["alert"]
    assert token.startswith(f"blocked|{cash_flows.BLOCKED_KEY_STATE}:check:")
    assert cash_flows.mark_blocked_told(d, token=token, now=LATER) is True
    told = _standing(d)
    assert told["key"] != key and told["resolved_told_at"] is None            # a known repeat, never a clearing
    monkeypatch.setattr(cash_flows, "blocked_alert", raises)                  # the judgement itself fails
    token, text = store.blocked_check(now=LATEST, root=tmp_path)["alert"]
    assert token.startswith("blocked|") and "해소" not in text


def test_case_e_a_missing_state_beside_a_verified_ledger_clears_by_c7_11(tmp_path):
    """C7-11 stays a recovery path: the ledger's chain is re-derived, its first line predates the told
    failure, and a missing state is a fresh start of the cache. The verification ran; it was not skipped."""
    d = lane(tmp_path)
    break_state(d)
    deliver(d, NOW)
    cash_flows.state_path(d).unlink()
    judged = cash_flows.blocked_state(d)
    assert judged == {"blocked": False, "verified_since": CUTOVER}
    cleared = deliver(d, LATER)
    assert cleared is not None and cleared[0].startswith("resolved|v1:state:load_state:")


def test_case_e_a_missing_state_with_an_unverified_ledger_is_not_cleared(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_state(d)
    deliver(d, NOW)
    cash_flows.state_path(d).unlink()
    monkeypatch.setattr(cash_flows, "verify", lambda _d: [])                  # nothing was actually verified
    assert deliver(d, LATER) is None and _standing(d)["resolved_told_at"] is None


# --- review 2026-10-12, P1: the told record under overlapping fires ---------------------------------------

def test_a_late_clearing_never_overwrites_a_newer_failure(tmp_path):
    """Fire A judges the clearing; fire B, later, finds a new failure and records it first. A's late write
    is refused: the record keeps B's failure standing instead of reading as cleared."""
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    deliver(d, NOW)
    cash_flows.ledger_path(d).write_bytes(good)
    late_token = cash_flows.blocked_alert(d, now=LATER)["alert"][0]           # A: resolved|K, not yet recorded
    break_state(d)
    newer = cash_flows.blocked_alert(d, now=LATEST)["alert"][0]               # B: a different failure
    assert cash_flows.mark_blocked_told(d, token=newer, now=LATEST) is True
    assert cash_flows.mark_blocked_told(d, token=late_token, now=LATER) is False
    told = _standing(d)
    assert told["key"] == newer.split("|", 1)[1] and told["resolved_told_at"] is None


def test_a_late_failure_record_never_hides_a_recurrence(tmp_path):
    """A stale BLOCKED record landing after its clearing was recorded would make the next recurrence of
    the same key read as already told. It is refused, so the recurrence is told."""
    d = lane(tmp_path)
    good = cash_flows.ledger_path(d).read_bytes()
    break_chain(d)
    stale = cash_flows.blocked_alert(d, now=NOW)["alert"][0]                  # A delivers, its write stalls
    deliver(d, NOW)                                                           # B: the same failure, recorded
    cash_flows.ledger_path(d).write_bytes(good)
    deliver(d, LATER)                                                         # the clearing, recorded
    assert cash_flows.mark_blocked_told(d, token=stale, now=NOW) is False
    break_chain(d)
    assert deliver(d, LATEST) is not None                                     # the recurrence is told


def test_a_clearing_for_a_key_that_is_not_standing_is_refused(tmp_path):
    d = lane(tmp_path)
    assert cash_flows.mark_blocked_told(d, token="resolved|v1:state:verify:0000", now=NOW) is False
    assert not cash_flows.blocked_told_path(d).exists()
    assert cash_flows.mark_blocked_told(d, token="other|x", now=NOW) is False


def test_two_overlapping_fires_send_twice_at_least_once_not_exactly_once(tmp_path):
    """Both judge the same failure before either records it: both deliver. A duplicate, never a silence —
    the told record cannot make a send and its record one step, so exactly-once is not claimed."""
    d = lane(tmp_path)
    break_chain(d)
    first = cash_flows.blocked_alert(d, now=NOW)["alert"]
    second = cash_flows.blocked_alert(d, now=LATER)["alert"]
    assert first is not None and second is not None and first[0] == second[0]
    assert cash_flows.mark_blocked_told(d, token=first[0], now=NOW) is True
    assert cash_flows.mark_blocked_told(d, token=second[0], now=LATER) is True
    assert cash_flows.blocked_alert(d, now=LATEST)["alert"] is None           # quiet once recorded


def test_concurrent_told_writes_leave_one_whole_record_and_no_tmp(tmp_path):
    import threading
    d = lane(tmp_path)
    tokens = [f"blocked|v1:state:verify:{i:016x}" for i in range(8)]
    stamps = [f"2026-10-09T06:00:0{i}Z" for i in range(8)]
    gate = threading.Barrier(len(tokens))
    errors: list[BaseException] = []

    def write(token: str, now: str) -> None:
        gate.wait()
        try:
            cash_flows.mark_blocked_told(d, token=token, now=now)
        except BaseException as exc:  # noqa: BLE001 — collected and asserted below
            errors.append(exc)
    threads = [threading.Thread(target=write, args=pair) for pair in zip(tokens, stamps)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []                                                       # serialized: no replace collides
    told = _standing(d)                                                       # parses: never a torn file
    newest = max(range(len(stamps)), key=lambda i: stamps[i])
    assert (told["key"], told["told_at"]) == (tokens[newest].split("|", 1)[1], stamps[newest])  # newest wins
    assert [p.name for p in d.iterdir() if p.name.endswith(".tmp")] == []


def test_a_replace_that_fails_raises_keeps_the_record_and_leaves_no_tmp(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    deliver(d, NOW)
    before = cash_flows.blocked_told_path(d).read_bytes()

    def refuse(self, target):
        raise PermissionError("replace refused")
    monkeypatch.setattr(pathlib.Path, "replace", refuse)
    with pytest.raises(PermissionError):
        cash_flows.mark_blocked_told(d, token="blocked|v1:state:verify:ffff", now=LATER)
    assert cash_flows.blocked_told_path(d).read_bytes() == before
    assert [p.name for p in d.iterdir() if p.name.endswith(".tmp")] == []


def test_the_fire_says_not_recorded_when_the_record_is_stale(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    Channel(monkeypatch)
    monkeypatch.setattr(cash_flows, "mark_blocked_told", lambda *_a, **_k: False)
    status = holdings_fire(tmp_path)
    assert "h6 blocked told not recorded:stale" in status and not re.search(r"h6 blocked told(;|$)", status)


# --- review 2026-10-12 (2): read, check and replace of the told record are one step under the state lock --

def _race(d: pathlib.Path, monkeypatch, *, stale: tuple[str, str], newer: tuple[str, str]) -> list[bool]:
    """Force the interleaving that a check-then-replace without a lock loses: the stale writer (an older
    ``now``) passes its check, then stalls before its replace while the newer writer runs to the end. A
    lock makes the newer writer wait instead; the stall is bounded, so either way both finish."""
    import threading
    stale_checked, newer_done = threading.Event(), threading.Event()
    real_write_text = pathlib.Path.write_text

    def write_text(self, *args, **kwargs):
        if self.name.endswith(".tmp") and threading.current_thread().name == "stale":
            stale_checked.set()
            newer_done.wait(timeout=1.0)                                          # without a lock B finishes here
        return real_write_text(self, *args, **kwargs)
    monkeypatch.setattr(pathlib.Path, "write_text", write_text)
    results: dict[str, bool] = {}

    def run(name: str, token: str, now: str) -> None:
        results[name] = cash_flows.mark_blocked_told(d, token=token, now=now)
        if name == "newer":
            newer_done.set()
    a = threading.Thread(target=run, name="stale", args=("stale", *stale))
    a.start()
    assert stale_checked.wait(timeout=5.0)
    b = threading.Thread(target=run, name="newer", args=("newer", *newer))
    b.start()
    a.join(timeout=10.0)
    b.join(timeout=10.0)
    return [results["stale"], results["newer"]]


def test_a_stale_blocked_record_racing_a_clearing_never_erases_it(monkeypatch, tmp_path):
    d = lane(tmp_path)
    break_chain(d)
    key = cash_flows.blocked_alert(d, now=NOW)["alert"][0].split("|", 1)[1]
    assert cash_flows.mark_blocked_told(d, token=f"blocked|{key}", now=NOW) is True
    _race(d, monkeypatch, stale=(f"blocked|{key}", LATER), newer=(f"resolved|{key}", LATEST))
    told = _standing(d)
    assert told["resolved_told_at"] == LATEST                                  # the clearing stands
    assert cash_flows.blocked_alert(d, now="2026-10-09T08:00:00Z")["alert"] is not None  # a recurrence is told


def test_a_stale_blocked_record_racing_a_newer_failure_never_replaces_it(monkeypatch, tmp_path):
    d = lane(tmp_path)
    _race(d, monkeypatch, stale=("blocked|v1:state:verify:aaaa", LATER),
          newer=("blocked|v1:state:load_state:bbbb", LATEST))
    told = _standing(d)
    assert (told["key"], told["told_at"]) == ("v1:state:load_state:bbbb", LATEST)


def test_a_told_write_that_cannot_get_the_lock_is_not_recorded_and_told_again(monkeypatch, tmp_path):
    """The lock keeps at-least-once: a busy lock is a failed write (STATE_LOCKED), never a told one."""
    from runtime.mvp_runtime import filelock
    from runtime.mvp_runtime.errors import PersistenceError
    d = lane(tmp_path)
    break_chain(d)
    patch_full_fire(monkeypatch, tmp_path)
    channel = Channel(monkeypatch)
    monkeypatch.setattr(filelock, "ACQUIRE_DEADLINE_SECONDS", 0.2)
    with cash_flows._state_lock(d):                                           # another writer holds the state
        with pytest.raises(PersistenceError) as refused:
            cash_flows.mark_blocked_told(d, token="blocked|v1:state:verify:cccc", now=NOW)
        assert refused.value.reason_code == cash_flows.STATE_LOCKED
        assert not cash_flows.blocked_told_path(d).exists()
        status = store.blocked_check(now=NOW, root=tmp_path)
        token, text = status["alert"]
        operator_mod.notify_operator(None, text)
        with pytest.raises(PersistenceError):
            store.mark_blocked_told(token, now=NOW, root=tmp_path)
    assert "h6 blocked told" in holdings_fire(tmp_path, LATER)                  # offered again, then recorded
    assert len(channel.blocked()) == 2 and _standing(d)["told_at"] == LATER
