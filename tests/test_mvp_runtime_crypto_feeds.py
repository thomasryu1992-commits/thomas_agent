"""C9 derivative-feed tests — funding paging, the Coinalyze gate, degrade semantics.

Under test: funding rides the existing binance_futures grant and pages backward like
the source collector; Coinalyze is its own provider (own key, own grant — env alone
fails closed, the key is read by name); a fetch that FAILS and a feed that is NOT
CONFIGURED both leave every column indeterminate, never a constant (the pre-C9 0-fill
for funding and `liquidation_spike_ratio` went 2026-08-05 — see
`test_an_unconfigured_feed_is_indeterminate_like_a_failed_one`); and the factory can
now mint funding_fade specs."""

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


def test_select_coinalyze_env_alone_opens_the_feed(monkeypatch, tmp_path):
    """The environment is the gate (Thomas 2026-08-10): the opt-in alone selects the real
    feed — no grant record backs it; revocation is unsetting the variable."""
    monkeypatch.setenv(LIQUIDATION_FEED_ENV, COINALYZE)
    assert isinstance(select_liquidation_feed(now=NOW, root=tmp_path), CoinalyzeLiquidationFeed)


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
    # Open interest rides the same feed object, so the null feed reports it absent too.
    # The derivative price series report absent for a different reason: this snapshot has no
    # candles and no timeframe, so there is no grid to request them at or join them onto.
    # `positioning` and `orderbook` report not_accumulating rather than a status, because this
    # call did not opt into durable accumulation — the routing-marks rule: a caller that keeps no
    # state keeps no store either. Both are asserted here rather than only the older one, because
    # this call passes no `root`: a store that wrote on the default path would put real state in
    # the repo's own state directory, and this assertion is the closest thing to a guard against
    # that. See the per-store test files for the opted-in paths.
    assert status == {
        "funding": "ok", "liquidations": "absent", "open_interest": "absent",
        "mark_prices": "absent", "index_prices": "absent", "premium_index": "absent",
        "positioning": "not_accumulating", "orderbook": "not_accumulating",
    }
    assert "funding" in snapshot
    assert "liquidations" not in snapshot and "open_interest" not in snapshot
    assert not {"mark_prices", "index_prices", "premium_index"} & set(snapshot)


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
    """One batch is a rotating WINDOW onto the family library, not the whole of it, so cover a
    full pass rather than assuming a single generation lands on funding_fade.

    **Mined at 4h now, not 1d, and the move is the finding rather than a fixture tweak.** This
    asked for 1d, which was right while every timeframe replayed the same 500-day span. It is
    not now: `MIN_FACTORY_BARS` floors 1d at 2,000 BARS — 2,000 days — while the funding fetch
    reaches ~1,067 (`market_data.funding_history_days`), so `factory._funding_feed_reaches`
    refuses the family there. Keeping the old assertion would be asserting that a family may be
    mined over a window its feed covers half of."""
    specs = []
    for n in range(6):
        specs.extend(generate_batch(f"GEN-{n:03d}", seed=5, timeframe="4h")["specs"])
    families = {s["strategy_family"] for s in specs}
    assert {"funding_fade_long", "funding_fade_short"} <= families
    # And the gate is what moved it: 1d cannot mint them however many passes are taken.
    assert not any(
        f.startswith("funding_fade")
        for n in range(6)
        for f in (s["strategy_family"] for s in generate_batch(
            f"GEN-{n:03d}", seed=5, timeframe="1d")["specs"])
    )
    for spec_dict in specs:
        if spec_dict["strategy_family"].startswith("funding_fade"):
            verdict = validate_strategy(StrategySpec.from_dict(spec_dict))
            assert verdict["approved_for_backtest"] is True
