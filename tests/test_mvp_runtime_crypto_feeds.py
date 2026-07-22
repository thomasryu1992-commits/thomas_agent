"""C9 derivative-feed tests — funding paging, the Coinalyze gate, degrade semantics.

Under test: funding rides the existing binance_futures grant and pages backward like
the source collector; Coinalyze is its own provider (own key, own grant — env alone
fails closed, the key is read by name); a fetch that FAILS leaves the snapshot key
present-and-empty (indeterminate features, never constants) while a feed that is NOT
CONFIGURED keeps the legacy constants; and the factory can now mint funding_fade
specs."""

from __future__ import annotations

import json
import urllib.error

import pytest

from runtime.mvp_runtime import safety_gate
from runtime.mvp_runtime.crypto.cycle import attach_feeds, run_crypto_cycle
from runtime.mvp_runtime.crypto.factory import generate_batch, validate_strategy
from runtime.mvp_runtime.crypto.market_data import (
    COINALYZE,
    LIQUIDATION_FEED_ENV,
    BinanceFuturesCollector,
    CoinalyzeLiquidationFeed,
    MockMarketDataCollector,
    NoLiquidationFeed,
    select_liquidation_feed,
)
from runtime.mvp_runtime.crypto.paper import DryRunPaperStore
from runtime.mvp_runtime.crypto.strategy import StrategySpec
from runtime.mvp_runtime.control import ControlStore
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization, build_activation_record

NOW = "2026-07-22T12:00:00Z"

_BINANCE_AUTH = Authorization(
    flags=(NETWORK_ACCESS,), provider_id="binance_futures", activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)
_COINALYZE_AUTH = Authorization(
    flags=(NETWORK_ACCESS,), provider_id=COINALYZE, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


class _FakeResp:
    def __init__(self, payload: str):
        self._payload = payload.encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_urlopen_pages(monkeypatch, pages):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        payload = pages[min(len(calls) - 1, len(pages) - 1)]
        if isinstance(payload, Exception):
            raise payload
        return _FakeResp(payload)

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return calls


# --- funding (binance grant) --------------------------------------------------

def test_mock_funding_is_deterministic():
    a = MockMarketDataCollector().funding_history("BTCUSDT", records=10, timeout_seconds=5)
    b = MockMarketDataCollector().funding_history("BTCUSDT", records=10, timeout_seconds=5)
    assert a == b and len(a) == 10
    assert all({"timestamp", "funding_rate"} <= set(r) for r in a)


def test_binance_funding_requires_authorization():
    with pytest.raises(SafetyGateBlocked):
        BinanceFuturesCollector().funding_history("BTCUSDT", records=10, timeout_seconds=5)


def test_binance_funding_pages_backward(monkeypatch):
    page2 = json.dumps([{"fundingTime": 1_000_000 + i * 100, "fundingRate": "0.0001"} for i in range(3)])
    page1 = json.dumps([{"fundingTime": 2_000_000 + i * 100, "fundingRate": "0.0002"} for i in range(3)])
    calls = _patch_urlopen_pages(monkeypatch, [page1, page2, json.dumps([])])
    rows = BinanceFuturesCollector(authorization=_BINANCE_AUTH).funding_history(
        "BTCUSDT", records=6, timeout_seconds=5
    )
    assert len(rows) == 6
    # Oldest first, and the second request walked endTime backward.
    assert rows[0]["funding_rate"] == 0.0001 and rows[-1]["funding_rate"] == 0.0002
    assert "endTime" in calls[1]


def test_binance_funding_malformed_fails_closed(monkeypatch):
    _patch_urlopen_pages(monkeypatch, ['{"not": "a list"}'])
    with pytest.raises(ToolError) as exc:
        BinanceFuturesCollector(authorization=_BINANCE_AUTH).funding_history(
            "BTCUSDT", records=5, timeout_seconds=5
        )
    assert exc.value.reason_code == "MALFORMED_RESULT"


# --- liquidations (coinalyze — its own provider) ------------------------------

def test_direct_coinalyze_cannot_fetch_unauthorized(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", "test-key-not-real")
    with pytest.raises(SafetyGateBlocked) as exc:
        CoinalyzeLiquidationFeed().liquidation_history("BTCUSDT", days=10, timeout_seconds=5)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_coinalyze_no_key_fails_closed(monkeypatch):
    monkeypatch.delenv("COINALYZE_API_KEY", raising=False)
    with pytest.raises(ToolError) as exc:
        CoinalyzeLiquidationFeed(authorization=_COINALYZE_AUTH).liquidation_history(
            "BTCUSDT", days=10, timeout_seconds=5
        )
    assert exc.value.reason_code == "NO_API_KEY"


def test_coinalyze_parses_and_drops_forming_day(monkeypatch):
    import time as _time

    now_s = int(_time.time())
    today = (now_s // 86400) * 86400
    payload = json.dumps([{
        "symbol": "BTCUSDT_PERP.A",
        "history": [
            {"t": today - 2 * 86400, "l": 100.0, "s": 50.0},
            {"t": today - 86400, "l": 200.0, "s": 70.0},
            {"t": today, "l": 999.0, "s": 999.0},  # still-forming current day
        ],
    }])
    monkeypatch.setenv("COINALYZE_API_KEY", "test-key-not-real")
    _patch_urlopen_pages(monkeypatch, [payload])
    rows = CoinalyzeLiquidationFeed(authorization=_COINALYZE_AUTH).liquidation_history(
        "BTCUSDT", days=10, timeout_seconds=5
    )
    assert [r["long_liquidation"] for r in rows] == [100.0, 200.0]  # forming day dropped


def test_coinalyze_transport_error_is_generic(monkeypatch):
    monkeypatch.setenv("COINALYZE_API_KEY", "secret-value")
    _patch_urlopen_pages(monkeypatch, [urllib.error.URLError("refused")])
    with pytest.raises(ToolError) as exc:
        CoinalyzeLiquidationFeed(authorization=_COINALYZE_AUTH).liquidation_history(
            "BTCUSDT", days=10, timeout_seconds=5
        )
    assert exc.value.reason_code == "TOOL_TRANSPORT"
    assert "secret-value" not in str(exc.value)


def test_select_liquidation_feed_defaults_to_none(monkeypatch):
    monkeypatch.delenv(LIQUIDATION_FEED_ENV, raising=False)
    assert isinstance(select_liquidation_feed(), NoLiquidationFeed)


def test_select_coinalyze_env_alone_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv(LIQUIDATION_FEED_ENV, COINALYZE)
    with pytest.raises(SafetyGateBlocked) as exc:
        select_liquidation_feed(now=NOW, root=tmp_path)
    assert exc.value.reason_code == "ACTIVATION_MISSING"


def test_select_coinalyze_with_activation(monkeypatch, tmp_path):
    (tmp_path / ".runtime_governance_state").mkdir()
    evidence_rel = ".runtime_governance_state/coinalyze_gate_approval.md"
    (tmp_path / evidence_rel).write_text("operator decision evidence", encoding="utf-8")
    record = build_activation_record(
        flags=[NETWORK_ACCESS], provider_id=COINALYZE,
        activated_at="2026-07-01T00:00:00Z", expires_at="2026-12-31T23:59:59Z",
        evidence_ref=evidence_rel, authority_level="P1",
    )
    path = safety_gate.activation_path(tmp_path, COINALYZE)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setenv(LIQUIDATION_FEED_ENV, COINALYZE)
    assert isinstance(select_liquidation_feed(now=NOW, root=tmp_path), CoinalyzeLiquidationFeed)


# --- attach_feeds degrade semantics -------------------------------------------

class _BrokenFundingCollector(MockMarketDataCollector):
    def funding_history(self, symbol, *, records, timeout_seconds):
        raise ToolError("TOOL_TRANSPORT", "funding endpoint down")


class _NoFundingCollector(MockMarketDataCollector):
    funding_history = property()  # attribute access raises → treated as absent


def test_attach_feeds_ok_and_absent():
    snapshot = {"symbol": "BTCUSDT", "candles": []}
    reasons, status = attach_feeds(
        snapshot, collector=MockMarketDataCollector(), liquidation_feed=NoLiquidationFeed(), now=NOW,
    )
    assert reasons == []
    assert status == {"funding": "ok", "liquidations": "absent"}
    assert "funding" in snapshot and "liquidations" not in snapshot


def test_attach_feeds_failure_is_present_and_empty():
    snapshot = {"symbol": "BTCUSDT", "candles": []}
    reasons, status = attach_feeds(
        snapshot, collector=_BrokenFundingCollector(), liquidation_feed=None, now=NOW,
    )
    assert reasons == ["FUNDING_DEGRADED"]
    assert status["funding"] == "degraded"
    assert snapshot["funding"] == []  # key present + empty → NaN-honest features


def test_cycle_survives_degraded_funding(tmp_path):
    record = run_crypto_cycle(
        collector=_BrokenFundingCollector(), store=DryRunPaperStore(),
        now=NOW, root=tmp_path, control_store=ControlStore(tmp_path),
    )
    assert "FUNDING_DEGRADED" in record["reason_codes"]
    assert record["feeds"]["funding"] == "degraded"
    assert record["report_text"]  # the cycle completed


# --- factory can mint funding specs -------------------------------------------

def test_funding_fade_templates_generate_and_validate():
    batch = generate_batch("GEN-001", seed=5, count=12, timeframe="1d")
    families = {s["strategy_family"] for s in batch["specs"]}
    assert {"funding_fade_long", "funding_fade_short"} <= families
    for spec_dict in batch["specs"]:
        if spec_dict["strategy_family"].startswith("funding_fade"):
            verdict = validate_strategy(StrategySpec.from_dict(spec_dict))
            assert verdict["approved_for_backtest"] is True
