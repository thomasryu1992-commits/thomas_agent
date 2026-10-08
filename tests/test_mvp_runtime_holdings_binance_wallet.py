"""The holdings lane's Binance spot + Simple Earn read (appendix C of MULTI_ASSET_EXPANSION_V0.1.md).

Pinned here:
- the gate: its own env var opens it, nothing else does, and withdrawing it stops egress;
- read-only by construction: one host, four GET paths, no order method;
- the money math: classes, the LD duplicate rule, unpriced assets counted and never zero;
- fail-closed shapes: spot failure fails the read, Earn failure is ``None``, no rate converts nothing;
- the combined block (P2): the wallet joins the total at the Toss rate, and with its gate open it is a
  required part — an unread wallet makes the total incomplete and leaves the peak alone;
- what leaves: KRW totals only, no USDT figure, no asset name;
- the import graph: ``holdings/`` imports nothing from ``crypto/`` (Q4 of EXPANSION_READINESS_REVIEW).
"""

from __future__ import annotations

import ast
import hashlib
import hmac
import io
import json
import pathlib
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime.crypto import account as crypto_account
from runtime.mvp_runtime.errors import SafetyGateBlocked
from runtime.mvp_runtime.holdings import binance_wallet, board, combined, toss_account
from runtime.mvp_runtime.holdings.binance_wallet import (
    API_KEY_ENV,
    API_SECRET_ENV,
    BINANCE_BASE_URL,
    BINANCE_WALLET_ENV,
    BINANCE_WALLET_ON,
    FLEXIBLE_PATH,
    LOCKED_PATH,
    PRICES_PATH,
    SPOT_ACCOUNT_PATH,
    BinanceWalletFeed,
    NoWalletFeed,
    read_wallet,
    select_wallet_feed,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-07T09:00:00Z"
API_KEY = "binance-key-DONOTLEAK"
API_SECRET = "binance-secret-DONOTLEAK"
RATE = 1357.0

SPOT = {"balances": [
    {"asset": "BTC", "free": "0.01", "locked": "0.00"},
    {"asset": "USDT", "free": "100.0", "locked": "0"},
    {"asset": "LDBTC", "free": "0.02", "locked": "0"},     # flexible Earn listed again: skipped
    {"asset": "LDO", "free": "10", "locked": "0"},         # a real coin, not a duplicate: counted
    {"asset": "ZZZ", "free": "5", "locked": "0"},          # no USDT price: counted as unpriced
    {"asset": "ETH", "free": "0", "locked": "0"},          # zero: dropped
]}
FLEXIBLE = {"rows": [{"asset": "BTC", "totalAmount": "0.02"}, {"asset": "USDC", "totalAmount": "50"}], "total": 2}
LOCKED = {"rows": [{"asset": "ETH", "amount": "1"}], "total": 1}
PRICES = [{"symbol": "BTCUSDT", "price": "80000"}, {"symbol": "ETHUSDT", "price": "3000"},
          {"symbol": "LDOUSDT", "price": "2"}, {"symbol": "USDCUSDT", "price": "1"}]


class _Response(io.BytesIO):
    def __init__(self, body):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Venue:
    """A fake Binance, routed by (host, path). ``fail`` maps a path to an exception."""

    def __init__(self, *, spot=None, flexible=None, locked=None, prices=None, fail=None):
        self.sent: list = []
        self.answers = {
            SPOT_ACCOUNT_PATH: SPOT if spot is None else spot,
            FLEXIBLE_PATH: FLEXIBLE if flexible is None else flexible,
            LOCKED_PATH: LOCKED if locked is None else locked,
            PRICES_PATH: PRICES if prices is None else prices,
        }
        self.fail = dict(fail or {})

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        parsed = urllib.parse.urlparse(request.full_url)
        if parsed.path in self.fail:
            raise self.fail[parsed.path]
        if parsed.path not in self.answers:
            raise AssertionError(f"request to an unexpected path: {parsed.path}")
        return _Response(self.answers[parsed.path])

    def requests(self) -> list[tuple[str, str, str]]:
        out = []
        for r in self.sent:
            parsed = urllib.parse.urlparse(r.full_url)
            out.append((r.get_method(), f"{parsed.scheme}://{parsed.hostname}", parsed.path))
        return out


def _http_error(url: str, status: int, code: int) -> urllib.error.HTTPError:
    body = json.dumps({"code": code, "msg": API_SECRET}).encode()
    return urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(body))


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(API_KEY_ENV, API_KEY)
    monkeypatch.setenv(API_SECRET_ENV, API_SECRET)


@pytest.fixture
def gate_open(monkeypatch, creds):
    monkeypatch.setenv(BINANCE_WALLET_ENV, BINANCE_WALLET_ON)


def _venue(monkeypatch, **kwargs) -> _Venue:
    fake = _Venue(**kwargs)
    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", fake)
    return fake


def _read(monkeypatch, **kwargs):
    fake = _venue(monkeypatch, **kwargs)
    snapshot, reason = read_wallet()
    return snapshot, reason, fake


# --- the gate ---------------------------------------------------------------------------

def test_without_the_env_var_the_feed_is_inert(creds):
    assert isinstance(select_wallet_feed(), NoWalletFeed)
    assert read_wallet() == (None, "NOT_CONFIGURED")


def test_the_env_var_alone_opens_the_gate(gate_open):
    assert isinstance(select_wallet_feed(), BinanceWalletFeed)


def test_neither_the_account_feed_nor_the_toss_opt_in_opens_this_one(monkeypatch, creds):
    monkeypatch.setenv(crypto_account.ACCOUNT_FEED_ENV, "binance_futures_account")
    monkeypatch.setenv(toss_account.TOSS_ACCOUNT_ENV, toss_account.TOSS_ACCOUNT_ON)
    assert isinstance(select_wallet_feed(), NoWalletFeed)


def test_a_directly_constructed_feed_cannot_open_a_socket(monkeypatch, gate_open):
    fake = _venue(monkeypatch)
    with pytest.raises(SafetyGateBlocked):
        BinanceWalletFeed().wallet_snapshot(timeout_seconds=1)
    assert fake.sent == []


def test_withdrawing_the_env_var_stops_egress(monkeypatch, gate_open):
    feed = select_wallet_feed()
    fake = _venue(monkeypatch)
    monkeypatch.delenv(BINANCE_WALLET_ENV)
    with pytest.raises(SafetyGateBlocked):
        feed.wallet_snapshot(timeout_seconds=1)
    assert fake.sent == []


# --- read-only by construction ----------------------------------------------------------

def test_the_feed_makes_only_the_four_get_requests_to_one_host(monkeypatch, gate_open):
    _, _, fake = _read(monkeypatch)
    assert {(m, base, path) for m, base, path in fake.requests()} == {
        ("GET", BINANCE_BASE_URL, SPOT_ACCOUNT_PATH),
        ("GET", BINANCE_BASE_URL, FLEXIBLE_PATH),
        ("GET", BINANCE_BASE_URL, LOCKED_PATH),
        ("GET", BINANCE_BASE_URL, PRICES_PATH),
    }
    assert binance_wallet.ALLOWED_REQUESTS == {(BINANCE_BASE_URL, p) for p in
                                               (SPOT_ACCOUNT_PATH, FLEXIBLE_PATH, LOCKED_PATH, PRICES_PATH)}


def test_a_path_outside_the_list_is_refused_before_a_socket(monkeypatch, gate_open):
    fake = _venue(monkeypatch)
    feed = select_wallet_feed()
    from runtime.mvp_runtime.errors import ToolBlocked
    with pytest.raises(ToolBlocked):
        feed._get(BINANCE_BASE_URL, "/sapi/v1/simple-earn/flexible/redeem", signed=True, what="x",
                  timeout_seconds=1)
    assert fake.sent == []


def test_the_feed_has_no_order_capability():
    forbidden = ("order", "trade", "redeem", "subscribe", "transfer", "withdraw", "place", "cancel")
    for cls in (BinanceWalletFeed, NoWalletFeed):
        public = [name for name in dir(cls) if not name.startswith("_")]
        assert not [n for n in public if any(word in n.lower() for word in forbidden)], cls


def test_signed_reads_carry_the_key_header_and_a_valid_signature(monkeypatch, gate_open):
    _, _, fake = _read(monkeypatch)
    signed = [r for r in fake.sent if urllib.parse.urlparse(r.full_url).hostname == "api.binance.com"
              and urllib.parse.urlparse(r.full_url).path != PRICES_PATH]
    assert signed
    for request in signed:
        assert request.get_header("X-mbx-apikey") == API_KEY
        query = urllib.parse.urlparse(request.full_url).query
        payload, signature = query.rsplit("&signature=", 1)
        assert signature == hmac.new(API_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()


def test_public_reads_carry_no_key(monkeypatch, gate_open):
    _, _, fake = _read(monkeypatch)
    public = [r for r in fake.sent if urllib.parse.urlparse(r.full_url).path == PRICES_PATH]
    assert public and all(r.get_header("X-mbx-apikey") is None for r in public)
    assert all("signature=" not in r.full_url for r in public)


# --- the money math ---------------------------------------------------------------------

def test_classes_count_spot_and_earn_and_skip_the_ld_duplicate(monkeypatch, gate_open):
    snapshot, reason, _ = _read(monkeypatch)
    assert reason is None
    # spot: BTC 0.01*80000=800, USDT 100, LDO 10*2=20 (other); LDBTC skipped; ZZZ unpriced; ETH zero dropped
    assert snapshot.spot_usdt == {"btc": 800.0, "eth": 0.0, "stable": 100.0, "other": 20.0}
    # earn: BTC 0.02*80000=1600, USDC 50, ETH 1*3000
    assert snapshot.earn_usdt == {"btc": 1600.0, "eth": 3000.0, "stable": 50.0, "other": 0.0}
    assert snapshot.unpriced_assets == 1


def test_an_ld_asset_without_a_flexible_twin_is_a_real_coin(monkeypatch, gate_open):
    snapshot, _, _ = _read(monkeypatch, flexible={"rows": [], "total": 0})
    # LDBTC has no flexible BTC row now, so it is kept — and it has no price, so it is unpriced.
    assert snapshot.unpriced_assets == 2


def test_an_unpriced_asset_is_warned_by_count_never_by_name(monkeypatch, gate_open):
    snapshot, _, _ = _read(monkeypatch)
    assert any("1 asset(s) with no USDT price" in w for w in snapshot.warnings)
    assert not any("ZZZ" in w for w in snapshot.warnings)


def test_a_bad_row_is_counted_and_never_read_as_zero(monkeypatch, gate_open):
    spot = {"balances": [*SPOT["balances"], {"asset": "SOL", "free": "x", "locked": "0"}, "not a row"]}
    locked = {"rows": [{"asset": "ETH", "amount": "1"}, {"asset": "", "amount": "2"}], "total": 2}
    snapshot, _, _ = _read(monkeypatch, spot=spot, locked=locked)
    assert snapshot.invalid_rows == 3 and snapshot.earn_truncated is False


def test_an_earn_list_longer_than_the_page_budget_is_truncated(monkeypatch, gate_open):
    rows = [{"asset": "USDT", "totalAmount": "1"}] * binance_wallet.EARN_PAGE_SIZE
    snapshot, _, _ = _read(monkeypatch, flexible={"rows": rows})   # no total, always a full page
    assert snapshot.earn_truncated is True
    assert snapshot.earn_usdt["stable"] == binance_wallet.EARN_PAGE_SIZE * binance_wallet.EARN_MAX_PAGES


def test_earn_pages_until_the_total(monkeypatch, gate_open):
    rows = [{"asset": "USDT", "totalAmount": "1"}] * binance_wallet.EARN_PAGE_SIZE
    pages = iter([{"rows": rows, "total": 150}, {"rows": rows[:50], "total": 150}])
    fake = _venue(monkeypatch)
    original = fake.__call__

    def routed(request, timeout=None):
        if urllib.parse.urlparse(request.full_url).path == FLEXIBLE_PATH:
            fake.sent.append(request)
            return _Response(next(pages))
        return original(request, timeout)

    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", routed)
    snapshot, _ = read_wallet()
    assert snapshot.earn_usdt["stable"] == 150.0 + 0.0
    assert [urllib.parse.urlparse(r.full_url).path for r in fake.sent].count(FLEXIBLE_PATH) == 2


# --- fail-closed shapes -----------------------------------------------------------------

def test_a_spot_failure_fails_the_whole_read(monkeypatch, gate_open):
    url = f"{BINANCE_BASE_URL}{SPOT_ACCOUNT_PATH}"
    snapshot, reason, _ = _read(monkeypatch, fail={SPOT_ACCOUNT_PATH: _http_error(url, 401, -2015)})
    assert snapshot is None and reason == "BINANCE_WALLET_FORBIDDEN"


def test_an_earn_failure_is_none_not_zero(monkeypatch, gate_open):
    url = f"{BINANCE_BASE_URL}{LOCKED_PATH}"
    snapshot, reason, _ = _read(monkeypatch, fail={LOCKED_PATH: _http_error(url, 400, -1002)})
    assert reason is None and snapshot.earn_usdt is None
    assert any("earn positions unavailable" in w for w in snapshot.warnings)


def test_errors_never_carry_the_signed_url_or_a_secret(monkeypatch, gate_open):
    url = f"{BINANCE_BASE_URL}{SPOT_ACCOUNT_PATH}"
    for failure in (_http_error(url, 401, -2015), urllib.error.URLError(f"failed {url}?signature=abc")):
        _venue(monkeypatch, fail={SPOT_ACCOUNT_PATH: failure})
        with pytest.raises(Exception) as caught:
            select_wallet_feed().wallet_snapshot(timeout_seconds=1)
        text = str(caught.value)
        assert API_SECRET not in text and API_KEY not in text and "signature" not in text


def test_missing_credentials_name_the_variables_and_never_a_value(monkeypatch, gate_open):
    monkeypatch.delenv(API_SECRET_ENV)
    _venue(monkeypatch)
    snapshot, reason = read_wallet()
    assert snapshot is None and reason == "NO_API_KEY"


# --- the combined block (P2): KRW at the Toss rate, the wallet a required part when configured ----

TOSS_VIEW = {"known_total_krw": 1_000_000.0, "partial": False}


def _futures_file(root: pathlib.Path, margin: float = 76.0, as_of: str = NOW) -> None:
    path = root / combined.BINANCE_SNAPSHOT_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"record_type": combined.BINANCE_RECORD_TYPE, "configured": True, "asset": "USDT",
                                "margin_balance": margin, "as_of": as_of}), encoding="utf-8")


def _combine(tmp_path, wallet, status, rate=RATE):
    import dataclasses
    if wallet is not None:   # read at the fire's time, as the Toss side is (H4-min coherence)
        wallet = dataclasses.replace(wallet, collected_at=NOW)
    return combined.combine(TOSS_VIEW, usd_krw_rate=rate, root=tmp_path, now=NOW, state_dir=tmp_path / "h",
                            wallet=wallet, wallet_status=status, toss_as_of=NOW, toss_reconciliation_failures=[])


def test_the_wallet_joins_the_total_at_the_toss_rate(monkeypatch, gate_open, tmp_path):
    snapshot, _, _ = _read(monkeypatch, spot={"balances": [r for r in SPOT["balances"] if r["asset"] != "ZZZ"]})
    assert snapshot.unpriced_assets == 0
    _futures_file(tmp_path)
    block = _combine(tmp_path, snapshot, combined.WALLET_OK)
    assert set(block) == combined.COMBINED_KEYS
    assert block["crypto_wallet_status"] == "ok" and block["complete"] is True
    assert block["crypto_spot_krw"] == pytest.approx(920.0 * RATE)
    assert block["crypto_earn_krw"] == pytest.approx(4650.0 * RATE)
    assert block["crypto_classes_krw"]["btc"] == pytest.approx(2400.0 * RATE)
    assert block["combined_total_krw"] == pytest.approx(1_000_000.0 + (76.0 + 920.0 + 4650.0) * RATE)


def test_a_closed_wallet_gate_leaves_the_declared_scope_incomplete(tmp_path):
    """H2 (Thomas 2026-10-08): the gate off no longer means "not part of the portfolio". Spot and Earn are
    declared, so they are excluded, the NAV is incomplete and the peak is not touched — though every
    source whose gate is on answered."""
    _futures_file(tmp_path)
    block = _combine(tmp_path, None, combined.WALLET_NOT_CONFIGURED)
    assert block["source_fetch_complete"] is True
    assert block["coverage_complete"] is False and block["checks"]["coverage"] == combined.FAIL
    assert block["portfolio_nav_complete"] is False and block["complete"] is False
    assert block["combined_total_krw"] is None and block["crypto_spot_krw"] is None
    assert block["sources"][combined.SOURCE_SPOT] == {"included": False, "reason": combined.EXCLUDED_GATE_OFF}
    assert block["sources"][combined.SOURCE_EARN] == {"included": False, "reason": combined.EXCLUDED_GATE_OFF}
    assert block["drawdown_state"] == combined.STATE_UNKNOWN
    assert not (tmp_path / "h" / combined.PEAK_FILENAME).exists()
    lines = board.render_combined(block)
    assert not [line for line in lines if "crypto spot" in line]
    assert any("INCOMPLETE" in line and "coverage" in line for line in lines)
    assert any("binance_spot (gate_off)" in line for line in lines)


@pytest.mark.parametrize("wallet_kind, status", [("none", "failed (BINANCE_WALLET_FORBIDDEN)"),
                                                 ("earn_unread", "earn_unread"), ("no_rate", "no_rate")])
def test_an_unread_wallet_makes_the_total_incomplete_and_leaves_the_peak(
        monkeypatch, gate_open, tmp_path, wallet_kind, status):
    _futures_file(tmp_path)
    url = f"{BINANCE_BASE_URL}{LOCKED_PATH}"
    snapshot, _, _ = _read(monkeypatch, fail={LOCKED_PATH: _http_error(url, 400, -1002)} if wallet_kind == "earn_unread" else None)
    wallet = None if wallet_kind == "none" else snapshot
    block = _combine(tmp_path, wallet, status if wallet is None else combined.WALLET_OK,
                     rate=None if wallet_kind == "no_rate" else RATE)
    assert block["crypto_wallet_status"] == status
    assert block["complete"] is False and block["combined_total_krw"] is None
    assert block["drawdown_state"] == combined.STATE_UNKNOWN
    assert not (tmp_path / "h" / combined.PEAK_FILENAME).exists()
    assert any(f"n/a ({status})" in line for line in board.render_combined(block))


def test_the_block_carries_no_usdt_figure_and_no_asset(monkeypatch, gate_open, tmp_path):
    snapshot, _, _ = _read(monkeypatch)
    _futures_file(tmp_path)
    text = json.dumps(_combine(tmp_path, snapshot, combined.WALLET_OK))
    for usdt in ("920.0", "4650.0", "80000"):
        assert usdt not in text, usdt
    for asset in ("BTC", "ETH", "LDO", "ZZZ", "USDC"):
        assert asset not in text, asset


def test_every_wallet_figure_is_krw_not_usdt(monkeypatch, gate_open, tmp_path):
    """The string check above passes whatever the unit; this one fails if a refactor forgets the multiply:
    every non-zero wallet figure must be the USDT amount times the rate, never the USDT amount itself."""
    snapshot, _, _ = _read(monkeypatch)
    block = _combine(tmp_path, snapshot, combined.WALLET_OK)
    figures = [block["crypto_spot_krw"], block["crypto_earn_krw"], *block["crypto_classes_krw"].values()]
    usdt = [sum(snapshot.spot_usdt.values()), sum(snapshot.earn_usdt.values()),
            *(snapshot.spot_usdt[k] + snapshot.earn_usdt[k] for k in block["crypto_classes_krw"])]
    for krw_value, usdt_value in zip(figures, usdt):
        assert krw_value == pytest.approx(usdt_value * RATE)


def test_the_refresh_reads_the_wallet_and_stores_only_krw(monkeypatch, gate_open, tmp_path):
    from runtime.mvp_runtime.holdings import store
    from runtime.mvp_runtime.holdings.model import HoldingsSnapshot, MarketTotals
    toss = HoldingsSnapshot(
        account="****8901", broker="toss",
        domestic=MarketTotals(market="domestic", holdings_value_krw=900_000.0, unrealized_pnl_krw=0.0, cash_krw=100_000.0),
        overseas=MarketTotals(market="overseas", holdings_value_krw=0.0, unrealized_pnl_krw=0.0, cash_krw=0.0),
        holdings=(), collected_at=NOW, latency_ms=1, usd_krw_rate=RATE,
    )
    monkeypatch.setattr(store, "read_holdings", lambda **_: (toss, None))
    monkeypatch.setattr(store, "_feed", lambda: None)
    _venue(monkeypatch)
    _futures_file(tmp_path)
    assert store.refresh(now=NOW, root=tmp_path)["status"].startswith("holdings snapshot: refreshed")
    body = json.loads(store.local_path(tmp_path).read_text(encoding="utf-8"))
    # The fixture holds one coin with no USDT price: read, shown, and never called a complete NAV (H2).
    assert body["combined"]["crypto_wallet_status"] == combined.WALLET_UNPRICED
    assert body["combined"]["checks"]["valuation"] == combined.FAIL
    assert body["combined"]["portfolio_nav_complete"] is False
    assert body["combined"]["crypto_spot_krw"] == pytest.approx(920.0 * RATE)
    assert str(RATE) not in json.dumps(body)
    assert "crypto spot" in store.load_local_view(now=NOW, root=tmp_path)
    text, data = store.load_holdings_view(now=NOW, root=tmp_path)    # H3: the doors see no wallet figure
    assert "crypto spot" not in text and "crypto mix" not in text
    assert "crypto_spot_krw" not in data["combined"] and "crypto_classes_krw" not in data["combined"]


def test_a_wallet_failure_still_lands_the_toss_snapshot(monkeypatch, gate_open, tmp_path):
    from runtime.mvp_runtime.holdings import store
    monkeypatch.setattr(binance_wallet, "read_wallet", lambda **_: (_ for _ in ()).throw(RuntimeError("boom")))
    from runtime.mvp_runtime.holdings.model import HoldingsSnapshot, MarketTotals
    toss = HoldingsSnapshot(account="****8901", broker="toss",
                            domestic=MarketTotals(market="domestic", holdings_value_krw=1.0, unrealized_pnl_krw=0.0, cash_krw=0.0),
                            overseas=MarketTotals(market="overseas", holdings_value_krw=0.0, unrealized_pnl_krw=0.0, cash_krw=0.0),
                            holdings=(), collected_at=NOW, latency_ms=1, usd_krw_rate=RATE)
    monkeypatch.setattr(store, "read_holdings", lambda **_: (toss, None))
    monkeypatch.setattr(store, "_feed", lambda: None)
    assert store.refresh(now=NOW, root=tmp_path)["status"].startswith("holdings snapshot: refreshed")
    body = json.loads(store.local_path(tmp_path).read_text(encoding="utf-8"))
    assert body["combined"]["crypto_wallet_status"] == "failed (RuntimeError)"
    assert body["domestic_stock_krw"] == 1.0 and body["combined"]["complete"] is False


# --- the import graph and the shared names ----------------------------------------------

def test_the_holdings_lane_imports_nothing_from_crypto():
    """Q4 of EXPANSION_READINESS_REVIEW_V0.1.md, as an import-graph property: holdings/ reads the risk
    lane's account snapshot as a file and repeats the key names; it never loads a crypto/ module."""
    offenders = []
    for path in (ROOT / "runtime" / "mvp_runtime" / "holdings").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if "crypto" in module.split(".") or any(a.name == "crypto" for a in node.names):
                    offenders.append(path.name)
            elif isinstance(node, ast.Import) and any("crypto" in a.name.split(".") for a in node.names):
                offenders.append(path.name)
    assert offenders == []


def test_the_repeated_names_agree_with_crypto():
    assert (API_KEY_ENV, API_SECRET_ENV) == (crypto_account.READ_API_KEY_ENV, crypto_account.READ_API_SECRET_ENV)


def test_the_gate_on_without_the_key_reads_nothing_and_the_total_is_incomplete(monkeypatch, tmp_path):
    """H1-a (2026-10-07): scheduler-maint no longer receives the account key pair. Switching the wallet
    gate on there before a dedicated read-only key exists (H1-b) must read nothing — no socket — and
    cost the combined total its completeness, never move the peak."""
    monkeypatch.setenv(BINANCE_WALLET_ENV, BINANCE_WALLET_ON)
    monkeypatch.delenv(API_KEY_ENV, raising=False)
    monkeypatch.delenv(API_SECRET_ENV, raising=False)

    def _no_socket(*_a, **_k):
        raise AssertionError("a socket was opened without a key")

    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", _no_socket)
    assert binance_wallet.read_wallet() == (None, "NO_API_KEY")
    _futures_file(tmp_path)
    block = _combine(tmp_path, None, "failed (NO_API_KEY)")
    assert block["complete"] is False and block["drawdown_state"] == combined.STATE_UNKNOWN
    assert not (tmp_path / "h" / combined.PEAK_FILENAME).exists()
