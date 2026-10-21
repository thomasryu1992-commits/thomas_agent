"""The research sandbox tools (RESEARCH_SANDBOX_V0.1.md §4.2–§4.3): the lake collector reads exactly what the
runtime's parser reads, the public fetch keeps its IP budget, and the runner keeps the sandbox closed.

Nothing here touches the network or the live state: fetches go through a fake, and every path is temporary.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

from runtime.mvp_runtime.crypto import market_data
from runtime.mvp_runtime.errors import ToolError
from scripts.research import fetch_public, lake

ROOT = Path(__file__).resolve().parents[1]
# The sandbox lives on the Linux host (systemd --user, AF_UNIX, bash). Its pure-Python parts are tested
# everywhere; the socket guard and the runner only where they can run.
linux_only = pytest.mark.skipif(sys.platform != "linux", reason="the research sandbox runs on the Linux host only")
H = 3_600_000
T0 = 1_760_000_000_000 - (1_760_000_000_000 % H)   # an hour boundary, ms


def _kline(open_ms: int, close: float, *, span: int = H) -> list:
    return [open_ms, "1.0", "2.0", "0.5", str(close), "10", open_ms + span - 1, "100", 5, "4", "40", "0"]


def _write(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


# --- the lake collector reads what the runtime's parser reads -------------------------------------

def test_candles_come_back_exactly_as_the_runtime_parses_them(tmp_path):
    rows = [_kline(T0 + i * H, 100.0 + i) for i in range(10)]
    _write(lake.klines_path(tmp_path, "BTCUSDT", "1h"), rows + [rows[3]])     # a repeated row
    snap = lake.LakeCollector(tmp_path).collect("BTCUSDT", "1h", limit=8, timeout_seconds=1)
    expected = [c for _, c in sorted(market_data.BinanceFuturesCollector()._parse(json.dumps(rows), now_ms=T0 + 100 * H))]
    assert snap.candles == expected[-8:] and not snap.is_synthetic and snap.source == lake.SOURCE
    assert snap.depth_capped is False
    assert lake.LakeCollector(tmp_path).collect("BTCUSDT", "1h", limit=50, timeout_seconds=1).depth_capped


def test_funding_and_derivative_prices_match_the_runtime_shapes(tmp_path):
    items = [{"symbol": "BTCUSDT", "fundingTime": T0 + i * 8 * H, "fundingRate": f"0.000{i}"} for i in range(1, 6)]
    _write(lake.funding_path(tmp_path, "BTCUSDT"), items)
    collector = lake.LakeCollector(tmp_path)
    funding = collector.funding_history("BTCUSDT", records=3, timeout_seconds=1)
    iso = market_data.BinanceFuturesCollector._iso
    assert funding == [{"timestamp": iso(int(i["fundingTime"])), "funding_rate": float(i["fundingRate"])} for i in items[-3:]]
    rows = [_kline(T0 + i * H, 50.0 + i) for i in range(5)]
    _write(lake.derivative_path(tmp_path, "mark", "BTCUSDT", "1h"), rows)
    marks = collector.derivative_price_klines("BTCUSDT", "1h", kind="mark", limit=4, timeout_seconds=1)
    assert marks == [{"timestamp": iso(T0 + i * H), "value": 50.0 + i} for i in range(1, 5)]


def test_an_empty_cache_is_a_loud_failure_and_coinalyze_is_absent(tmp_path):
    collector = lake.LakeCollector(tmp_path)
    for call in (lambda: collector.collect("BTCUSDT", "1h", limit=5, timeout_seconds=1),
                 lambda: collector.funding_history("BTCUSDT", records=5, timeout_seconds=1),
                 lambda: collector.derivative_price_klines("BTCUSDT", "1h", kind="index", limit=5, timeout_seconds=1)):
        with pytest.raises(ToolError) as exc:
            call()
        assert exc.value.reason_code == lake.LAKE_EMPTY
    for feed in (lake.LakeLiquidationFeed(None), lake.LakeLiquidationFeed(tmp_path)):
        for call in (lambda: feed.open_interest_history("BTCUSDT", days=10, timeout_seconds=1),
                     lambda: feed.liquidation_history("BTCUSDT", days=10, timeout_seconds=1)):
            with pytest.raises(ToolError) as exc:
                call()
            assert exc.value.reason_code == lake.LAKE_NO_COINALYZE


def _stored_daily(root: Path) -> tuple[Path, list, list]:
    """What the runtime's factory would have kept from one Coinalyze fetch, in a lake-shaped directory."""
    from runtime.mvp_runtime.crypto import coinalyze_daily_store, state
    days = [market_data.BinanceFuturesCollector._iso(T0 - (5 - i) * 24 * H) for i in range(5)]
    oi = [{"timestamp": t, "open_interest": 1000.0 + i} for i, t in enumerate(days)]
    liq = [{"timestamp": t, "long_liquidation": 1.0 + i, "short_liquidation": 2.0 + i} for i, t in enumerate(days)]
    result = coinalyze_daily_store.record("BTCUSDT", open_interest=oi, liquidations=liq,
                                          now="2026-10-21T00:00:00Z", root=root)
    assert result == {"status": "recorded", "written": {"open_interest": 5, "liquidations": 5}}
    return state.state_dir(root), oi, liq


def test_the_coinalyze_feed_serves_what_the_runtime_kept_in_the_vendor_shapes(tmp_path):
    directory, oi, liq = _stored_daily(tmp_path)
    feed = lake.LakeLiquidationFeed(directory)
    assert feed.feed_id != "none"   # attach_feeds and oi_store skip a feed whose id is "none"
    assert feed.open_interest_history("BTCUSDT", days=10, timeout_seconds=1) == oi
    assert feed.liquidation_history("btcusdt", days=3, timeout_seconds=1) == liq[-3:]
    with pytest.raises(ToolError) as exc:
        feed.open_interest_history("ETHUSDT", days=10, timeout_seconds=1)
    assert exc.value.reason_code == lake.LAKE_NO_COINALYZE
    with pytest.raises(ToolError) as exc:   # the hourly series is oi_store's, never served from the daily rows
        feed.open_interest_history("BTCUSDT", days=10, timeout_seconds=1, interval=market_data.OI_INTERVAL_1H)
    assert exc.value.reason_code == lake.LAKE_NO_COINALYZE


def test_attach_feeds_puts_the_lake_rows_on_the_frame_and_keeps_nothing(tmp_path):
    from runtime.mvp_runtime.crypto import coinalyze_daily_store, feed_assembly
    directory, oi, liq = _stored_daily(tmp_path / "lake")
    run_root = tmp_path / "run"
    snapshot: dict = {"symbol": "BTCUSDT", "timeframe": "1h"}
    codes, status = feed_assembly.attach_feeds(snapshot, collector=lake.LakeCollector(tmp_path / "cache"),
                                               liquidation_feed=lake.LakeLiquidationFeed(directory),
                                               now="2026-10-21T00:00:00Z", root=run_root)
    assert (status["open_interest"], status["liquidations"]) == ("ok", "ok")
    assert snapshot["open_interest"] == oi and snapshot["liquidations"] == liq
    assert "coinalyze_daily" not in status
    assert coinalyze_daily_store.read_rows("open_interest", root=run_root) == []


def test_install_swaps_the_selectors_and_the_mock_refuses(monkeypatch, tmp_path):
    for name in ("select_market_data_collector", "select_liquidation_feed"):
        monkeypatch.setattr(market_data, name, getattr(market_data, name))
    monkeypatch.setattr(market_data.MockMarketDataCollector, "collect", market_data.MockMarketDataCollector.collect)
    lake.install(cache=tmp_path, lake=None)
    assert isinstance(market_data.select_market_data_collector(now="x"), lake.LakeCollector)
    assert isinstance(market_data.select_liquidation_feed(now="x"), lake.LakeLiquidationFeed)
    with pytest.raises(ToolError) as exc:
        market_data.MockMarketDataCollector().collect("BTCUSDT", "1h", limit=5, timeout_seconds=1)
    assert exc.value.reason_code == lake.MOCK_REFUSED


# --- the public fetch keeps its budget ---------------------------------------------------------------

class FakeVenue:
    """Serves klines from a fixed history and records each request; never touches the network."""

    def __init__(self, history: list, *, used: int = 10, status: int = 200):
        self.history, self.used, self.status, self.urls = history, used, status, []

    def __call__(self, url: str):
        self.urls.append(url)
        if self.status != 200:
            return self.status, {}, ""
        from urllib.parse import parse_qs, urlparse
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        rows = self.history
        if "startTime" in q:
            rows = [r for r in rows if r[0] >= int(q["startTime"])]
            rows = rows[: int(q["limit"])]
        else:
            if "endTime" in q:
                rows = [r for r in rows if r[0] <= int(q["endTime"])]
            rows = rows[-int(q["limit"]):]
        return 200, {"X-MBX-USED-WEIGHT-1M": str(self.used)}, json.dumps(rows)


class Clock:
    def __init__(self, t: float):
        self.t, self.slept = t, 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.slept += s
        self.t += s


NOON = (T0 // 1000) - ((T0 // 1000) % 86400) + 12 * 3600   # a 12:00 UTC moment, outside the factory window


def _budget(venue, clock, **kw):
    return fetch_public.Budget(fetch=venue, clock=clock, sleep=clock.sleep, **kw)


def test_the_first_fill_pages_back_to_depth_and_keeps_only_closed_bars(tmp_path):
    now_ms = NOON * 1000
    history = [_kline(now_ms - (40 - i) * H, float(i)) for i in range(41)]   # the last one is still forming
    clock = Clock(NOON)
    path = lake.klines_path(tmp_path, "BTCUSDT", "1h")
    written = fetch_public.fill_klines(_budget(FakeVenue(history), clock), path, endpoint="klines",
                                       params={"symbol": "BTCUSDT", "interval": "1h"}, timeframe="1h",
                                       depth=25, page=10)
    stored = lake._by_open_time(lake.read_rows(path))
    assert written == len(stored) >= 25
    assert all(int(r[6]) < now_ms for r in stored)                             # forming bar never written


def test_a_later_fill_only_asks_forward_and_never_rewrites(tmp_path):
    now_ms = NOON * 1000
    history = [_kline(now_ms - (40 - i) * H, float(i)) for i in range(40)]
    path = lake.klines_path(tmp_path, "BTCUSDT", "1h")
    _write(path, history[:30])
    venue = FakeVenue(history)
    written = fetch_public.fill_klines(_budget(venue, Clock(NOON)), path, endpoint="klines",
                                       params={"symbol": "BTCUSDT", "interval": "1h"}, timeframe="1h", depth=25, page=10)
    assert written == 10 and all("startTime" in u for u in venue.urls)
    assert len(lake._by_open_time(lake.read_rows(path))) == 40 and len(path.read_text().splitlines()) == 40


def test_the_minute_budget_waits_instead_of_spending(tmp_path):
    clock = Clock(NOON)
    budget = _budget(FakeVenue([_kline(T0, 1.0)]), clock, per_minute=20)
    for _ in range(3):
        budget.get("klines", {"symbol": "BTCUSDT", "limit": 1500}, weight=10)
    assert clock.slept > 0 and budget.requests == 3


@pytest.mark.parametrize("venue,error", [
    (FakeVenue([], status=429), "429"),
    (FakeVenue([], status=418), "418"),
    (FakeVenue([_kline(T0, 1.0)], used=1300), "1300"),
])
def test_rate_limits_and_a_busy_ip_stop_the_run(venue, error):
    with pytest.raises(fetch_public.Stop, match=error):
        _budget(venue, Clock(NOON)).get("klines", {"symbol": "BTCUSDT", "limit": 10}, weight=1)


def test_the_factory_window_is_never_entered():
    eight_thirty = NOON - 3.5 * 3600
    venue = FakeVenue([_kline(T0, 1.0)])
    with pytest.raises(fetch_public.Stop, match="factory window"):
        _budget(venue, Clock(eight_thirty)).get("klines", {"symbol": "BTCUSDT", "limit": 10}, weight=1)
    assert venue.urls == []


def test_15m_is_refused(tmp_path, capsys):
    with pytest.raises(ValueError):
        fetch_public.run(_budget(FakeVenue([]), Clock(NOON)), tmp_path, symbols=["BTCUSDT"], timeframes=["15m"], kinds=[])
    assert fetch_public.main(["--cache", str(tmp_path), "--timeframe", "15m"]) == 2


def test_kline_weights_follow_the_venue_table():
    assert [fetch_public.kline_weight(n) for n in (50, 200, 1000, 1500)] == [1, 2, 5, 10]


# --- the sandbox has no network -----------------------------------------------------------------------

@linux_only
def test_the_sandbox_site_blocks_inet_and_keeps_unix_sockets(tmp_path):
    site = ROOT / "scripts" / "research" / "sandbox_site"
    code = (
        "import socket, os\n"
        "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
        "try:\n    s.connect(('127.0.0.1', 9)); print('INET OPEN')\n"
        "except PermissionError as e:\n    print('INET BLOCKED')\n"
        "path = os.environ['SOCK']\n"
        "srv = socket.socket(socket.AF_UNIX); srv.bind(path); srv.listen(1)\n"
        "c = socket.socket(socket.AF_UNIX); c.connect(path); print('UNIX OK')\n"
    )
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(site), "SOCK": str(tmp_path / "s.sock")}
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert out.stdout.split() == ["INET", "BLOCKED", "UNIX", "OK"], out.stderr


# --- the runner -----------------------------------------------------------------------------------------

def _user_systemd() -> bool:
    if sys.platform != "linux" or not shutil.which("systemd-run"):
        return False
    probe = subprocess.run(["systemd-run", "--user", "--scope", "-q", "true"], capture_output=True, timeout=30)
    return probe.returncode == 0


needs_user_systemd = pytest.mark.skipif(not _user_systemd(), reason="no user systemd here (CI); the runner needs it")


@pytest.fixture
def sandbox(tmp_path):
    lake_root = tmp_path / "lake"
    snap = lake_root / "20261020T000000Z"
    snap.mkdir(parents=True)
    (snap / "manifest.json").write_text("{}", encoding="utf-8")
    (snap / "strategy_candidates.jsonl").write_text('{"x": 1}\n', encoding="utf-8")
    (lake_root / "current").symlink_to("20261020T000000Z")
    code = tmp_path / "code"
    code.mkdir()
    subprocess.run(["git", "init", "-q", str(code)], check=True)
    (code / "f.txt").write_text("x", encoding="utf-8")
    subprocess.run(["git", "-C", str(code), "add", "."], check=True)
    subprocess.run(["git", "-C", str(code), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"], check=True)
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin" / "python").symlink_to(sys.executable)
    env = {**os.environ, "RESEARCH_LAKE_ROOT": str(lake_root), "RESEARCH_CACHE": str(tmp_path / "cache"),
           "RESEARCH_VENV": str(venv), "RESEARCH_RUNS": str(tmp_path / "runs")}
    return code, env, tmp_path / "runs"


RUNNER = str(ROOT / "scripts" / "research" / "sandbox_run.sh")


@needs_user_systemd
def test_a_run_sees_the_snapshot_records_itself_and_leaves_no_link(sandbox):
    code, env, runs = sandbox
    probe = "import os; print(os.path.exists('.runtime_governance_state/crypto/strategy_candidates.jsonl'), 'RESEARCH_CACHE' in os.environ, 'HOME' in os.environ)"
    out = subprocess.run([RUNNER, "--code", str(code), "--", "python", "-c", probe], env=env,
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.split() == ["True", "True", "True"]
    record = json.loads(next(runs.glob("*/run.json")).read_text(encoding="utf-8"))
    assert record["exit"] == 0 and record["lake_snapshot"] == "20261020T000000Z" and record["command"][0] == "python"
    assert not (code / ".runtime_governance_state").exists()


@needs_user_systemd
def test_the_runner_passes_no_secret_through(sandbox):
    code, env, _ = sandbox
    out = subprocess.run([RUNNER, "--code", str(code), "--", "python", "-c",
                          "import os; print(sorted(k for k in os.environ if 'KEY' in k or 'TOKEN' in k or 'SECRET' in k))"],
                         env={**env, "BINANCE_API_KEY": "shh", "SOME_TOKEN": "shh"}, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stdout + out.stderr


@needs_user_systemd
def test_the_memory_cap_kills_an_oversized_run(sandbox):
    code, env, _ = sandbox
    out = subprocess.run([RUNNER, "--code", str(code), "--", "python", "-c", "b = bytearray(400 * 1024 * 1024)"],
                         env={**env, "RESEARCH_MEMORY_MAX": "150M"}, capture_output=True, text=True, timeout=120)
    assert out.returncode != 0


@linux_only
def test_the_runner_refuses_15m_a_real_state_dir_and_a_dirty_official_run(sandbox):
    code, env, _ = sandbox
    out = subprocess.run([RUNNER, "--code", str(code), "--", "python", "-c", "1", "--timeframe", "15m"],
                         env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "15m" in out.stderr
    (code / ".runtime_governance_state").mkdir()
    out = subprocess.run([RUNNER, "--code", str(code), "--", "true"], env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "real directory" in out.stderr
    (code / ".runtime_governance_state").rmdir()
    (code / "f.txt").write_text("changed", encoding="utf-8")
    out = subprocess.run([RUNNER, "--official", "--code", str(code), "--", "true"], env=env,
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "clean tree" in out.stderr
