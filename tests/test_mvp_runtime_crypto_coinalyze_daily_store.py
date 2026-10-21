"""The daily Coinalyze store (RESEARCH_SANDBOX_V0.1.md step 3): keeps what was fetched, asks for nothing new,
and only the scheduler's factory and the live cycle write it."""

from __future__ import annotations

import ast
import json
from pathlib import Path

from runtime.mvp_runtime.crypto import coinalyze_daily_store as store
from runtime.mvp_runtime.crypto.feed_assembly import attach_feeds, attach_mining_legs
from runtime.mvp_runtime.crypto.market_data import MockMarketDataCollector, collect_market_data

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-10-20T12:00:00Z"
LATER = "2026-10-20T19:00:00Z"   # past the 6 h throttle


def _oi(days):
    return [{"timestamp": f"2026-10-{d:02d}T00:00:00Z", "open_interest": 1000.0 + d} for d in days]


def _liq(days):
    return [{"timestamp": f"2026-10-{d:02d}T00:00:00Z", "long_liquidation": float(d), "short_liquidation": 2.0 * d}
            for d in days]


def test_record_keeps_both_series_once_and_then_only_what_is_new(tmp_path):
    first = store.record("btcusdt", open_interest=_oi(range(1, 6)), liquidations=_liq(range(1, 6)), now=NOW, root=tmp_path)
    assert first == {"status": "recorded", "written": {"open_interest": 5, "liquidations": 5}}
    assert store.record("BTCUSDT", open_interest=_oi(range(1, 7)), liquidations=None, now=NOW,
                        root=tmp_path) == {"status": "skipped_fresh"}
    later = store.record("BTCUSDT", open_interest=_oi(range(1, 8)), liquidations=_liq(range(1, 8)), now=LATER, root=tmp_path)
    assert later["written"] == {"open_interest": 2, "liquidations": 2}
    rows = store.read_rows("open_interest", symbol="BTCUSDT", root=tmp_path)
    assert [r["timestamp"][:10] for r in rows] == [f"2026-10-{d:02d}" for d in range(1, 8)]
    assert rows[-1]["open_interest"] == 1007.0 and rows[0]["symbol"] == "BTCUSDT"
    assert len(store.path("open_interest", tmp_path).read_text().splitlines()) == 7   # nothing rewritten


def test_a_row_that_fails_its_hash_or_shape_is_not_read(tmp_path):
    store.append_rows("open_interest", _oi([1, 2]) + [{"timestamp": "x", "open_interest": "NaN-text"}],
                      symbol="ETHUSDT", root=tmp_path)
    target = store.path("open_interest", tmp_path)
    lines = target.read_text().splitlines()
    tampered = json.loads(lines[0])
    tampered["open_interest"] = 9.9
    target.write_text("\n".join([json.dumps(tampered), lines[1], "{torn"]) + "\n", encoding="utf-8")
    assert [r["timestamp"][:10] for r in store.read_rows("open_interest", root=tmp_path)] == ["2026-10-02"]


def test_record_never_raises(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("not a directory", encoding="utf-8")   # the state root cannot be created
    result = store.record("BTCUSDT", open_interest=_oi([1]), liquidations=_liq([1]), now=NOW, root=blocker)
    assert result["status"] == "degraded"


class _CountingFeed:
    feed_id = "coinalyze_market_data"

    def __init__(self):
        self.calls = []

    def liquidation_history(self, symbol, *, days, timeout_seconds):
        self.calls.append("liq")
        return _liq([1, 2, 3])

    def open_interest_history(self, symbol, *, days, timeout_seconds, interval="daily"):
        self.calls.append(("oi", interval))
        return _oi([1, 2, 3])


def _snapshot():
    snap, _ = collect_market_data("BTCUSDT", "4h", collector=MockMarketDataCollector(), now=NOW, limit=50)
    return snap


def test_attach_feeds_records_only_when_asked_and_asks_the_vendor_nothing_extra(tmp_path):
    quiet = _CountingFeed()
    attach_feeds(_snapshot(), collector=MockMarketDataCollector(), liquidation_feed=quiet, now=NOW, root=tmp_path)
    assert not store.path("open_interest", tmp_path).exists()
    keeping = _CountingFeed()
    _, status = attach_feeds(_snapshot(), collector=MockMarketDataCollector(), liquidation_feed=keeping, now=NOW,
                             root=tmp_path, record_vendor_daily=True)
    assert status["coinalyze_daily"] == "recorded"
    assert [c for c in keeping.calls if c == "liq" or c[0] == "oi" and c[1] == "daily"] == \
           [c for c in quiet.calls if c == "liq" or c[0] == "oi" and c[1] == "daily"]   # same daily asks
    assert len(store.read_rows("liquidations", root=tmp_path)) == 3


def test_mining_legs_pass_the_choice_through_and_default_to_read_only(tmp_path):
    snap = _snapshot()
    attach_mining_legs(snap, collector=MockMarketDataCollector(), timeframe="4h", now=NOW, root=tmp_path,
                       liquidation_feed=_CountingFeed())
    assert not store.path("open_interest", tmp_path).exists()
    attach_mining_legs(_snapshot(), collector=MockMarketDataCollector(), timeframe="4h", now=NOW, root=tmp_path,
                       liquidation_feed=_CountingFeed(), record_vendor_daily=True)
    assert store.read_rows("open_interest", root=tmp_path)


def test_only_the_factory_and_the_live_cycle_opt_in():
    """Who may write the store: an enumeration, so a script that starts writing it has to be added here."""
    writers = set()
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "record_vendor_daily" and isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        writers.add(path.relative_to(ROOT).as_posix())
    assert writers == {"runtime/mvp_runtime/scheduler.py", "runtime/mvp_runtime/crypto/cycle.py"}
