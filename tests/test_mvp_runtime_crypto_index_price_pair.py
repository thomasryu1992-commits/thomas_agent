"""The index price series is keyed on the PAIR, and sending `symbol` cost it every cycle.

`derivative_price_klines` built one parameter set for all three endpoints, naming the
instrument `symbol=`. Two of the three accept that. `indexPriceKlines` does not — the index is
quoted per PAIR (one index for BTCUSDT however many contracts settle against it), and the venue
enforces the distinction:

    markPriceKlines    symbol=BTCUSDT  -> 200
    premiumIndexKlines symbol=BTCUSDT  -> 200
    indexPriceKlines   symbol=BTCUSDT  -> 400 {"code":-1102,"msg":"Mandatory parameter 'pair'
                                               was not sent, was empty/null, or malformed."}
    indexPriceKlines   pair=BTCUSDT    -> 200

Measured against the live venue 2026-07-30. Because the other two succeeded, every cycle
reported `index_prices: degraded` beside `mark_prices: ok` — a PARTIAL failure, which reads as
a feed blip rather than as a malformed request, and had gone unnoticed since the series landed.

The cost was not an outage. `index_price` and `mark_index_basis_bps` fell to indeterminate, so
the fabricated constant-0.0 basis this whole series exists to remove was replaced by a
permanently absent one rather than by a real measurement — half of C13's purpose, silently.

Under test: each endpoint is asked with the key it actually requires, the mapping cannot drift
from the path table, and a request never carries both keys.
"""

from __future__ import annotations

import json
import urllib.parse

import pytest

from runtime.mvp_runtime.crypto import market_data as md
from runtime.mvp_runtime.safety_gate import NETWORK_ACCESS, Authorization

_AUTH = Authorization(
    flags=(NETWORK_ACCESS,), provider_id=md.BINANCE_FUTURES, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return self._payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _kline_rows(count=3):
    """Venue kline shape: open time, O, H, L, C, ... — only open time and close are read."""
    rows = []
    for i in range(count):
        open_ms = 1_780_000_000_000 + i * 900_000
        rows.append([open_ms, "1.0", "1.0", "1.0", f"{100 + i}.0", "0",
                     open_ms + 899_999, "0", 0, "0", "0", "0"])
    return json.dumps(rows)


def _captured_params(monkeypatch, kind, *, symbol="BTCUSDT"):
    """The query parameters of the first request `derivative_price_klines` actually sends."""
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return _FakeResp(_kline_rows())

    monkeypatch.setattr(md.urllib.request, "urlopen", fake_urlopen)
    collector = md.BinanceFuturesCollector(authorization=_AUTH)
    collector.derivative_price_klines(symbol, "15m", kind=kind, limit=3, timeout_seconds=5)
    assert seen, "no request was sent"
    query = urllib.parse.urlparse(seen[0]).query
    return {k: v[0] for k, v in urllib.parse.parse_qs(query).items()}, seen[0]


def test_the_index_series_is_asked_for_by_pair(monkeypatch):
    """The bug. `symbol=` here is a 400 from the venue, every cycle, forever."""
    params, url = _captured_params(monkeypatch, "index")
    assert params.get("pair") == "BTCUSDT"
    assert "symbol" not in params
    assert "indexPriceKlines" in url


@pytest.mark.parametrize("kind", ["mark", "premium"])
def test_the_per_contract_series_are_still_asked_for_by_symbol(monkeypatch, kind):
    """These two were never broken, and the fix must not "unify" them onto the index's key."""
    params, _ = _captured_params(monkeypatch, kind)
    assert params.get("symbol") == "BTCUSDT"
    assert "pair" not in params


@pytest.mark.parametrize("kind", ["mark", "index", "premium"])
def test_a_request_names_the_instrument_exactly_once(monkeypatch, kind):
    """Both keys would be a request that happens to work until the venue tightens validation."""
    params, _ = _captured_params(monkeypatch, kind)
    assert sum(1 for key in ("symbol", "pair") if key in params) == 1


def test_every_kind_answers_both_questions():
    """The mapping sits beside the path table so a new kind cannot land with one half filled in
    — which is exactly how this one shipped: the path was added, the key was assumed."""
    assert set(md.DERIVATIVE_KLINE_SYMBOL_PARAMS) == set(md.DERIVATIVE_KLINE_PATHS)
    assert set(md.DERIVATIVE_KLINE_SYMBOL_PARAMS.values()) <= {"symbol", "pair"}


def test_paging_keeps_the_right_key(monkeypatch):
    """The parameter set is rebuilt each page, so a fix applied to only the first would leave
    every backfill page malformed — and a 400 mid-walk degrades the same way a 400 up front
    does, which would have made this look fixed."""
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request.full_url)
        return _FakeResp(_kline_rows(2))

    monkeypatch.setattr(md.urllib.request, "urlopen", fake_urlopen)
    collector = md.BinanceFuturesCollector(authorization=_AUTH)
    collector.derivative_price_klines("BTCUSDT", "15m", kind="index", limit=50, timeout_seconds=5)

    assert len(seen) > 1, "this test needs more than one page to be about paging"
    for url in seen:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        assert "pair" in params and "symbol" not in params
