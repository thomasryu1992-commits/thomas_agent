"""The venue READ plane, apart from the WRITE plane (H1-b, Thomas 2026-10-08).

Observation used to need execution permission: the resting-orders board read through the live write
gate's venue reader with the order key, so closing ``MVP_LIVE_TRADING`` (R1) blinded it, and the account
snapshot signed its reads with the account key, which is the order key. H1-b splits the account feed
into two credential planes behind its one gate (``MVP_ACCOUNT_FEED``):

- READ signs with a dedicated venue key issued with "Enable Reading" only (``BINANCE_READ_API_*``);
- TRADING keeps the account pair for the write plane's own reads (live leg, emergency close, probe).

What these pin, each with its negative:

- every caller names its plane, and which plane each caller uses;
- a READ feed never falls back to a trading key, even when one is set;
- with the live write gate CLOSED, the READ plane still reads the account, the positions and both
  resting-order lists, and the write plane still selects the dry-run adapter;
- the read feed has no write surface: no order, cancel, close, transfer, leverage or margin method, and
  a path outside its GET allowlist is refused before a socket opens;
- the holdings wallet read uses the READ pair only, GET only, and its gate is never set by compose.

**Nothing here opens a socket to a venue**: ``urlopen`` is intercepted.
"""

from __future__ import annotations

import ast
import json
import pathlib
import urllib.parse

import pytest
import yaml
from tests._helpers import make_gate_authorization

from runtime.mvp_runtime.crypto import account
from runtime.mvp_runtime.crypto import live_execution
from runtime.mvp_runtime.errors import ToolBlocked, ToolError
from runtime.mvp_runtime.holdings import binance_wallet

REPO = pathlib.Path(__file__).resolve().parents[1]
_AUTH = make_gate_authorization(flags=account._NETWORK_FLAGS, provider_id=account.BINANCE_ACCOUNT)
READ_KEY, TRADING_KEY, ORDER_KEY = "read-key-value", "trading-key-value", "order-key-value"


class _Response:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


ACCOUNT_BODY = {
    "totalWalletBalance": "100", "totalMarginBalance": "101", "availableBalance": "90",
    "totalUnrealizedProfit": "1",
    "positions": [{"symbol": "BTCUSDT", "positionAmt": "0.001", "entryPrice": "60000",
                   "unrealizedProfit": "1", "leverage": "5", "positionSide": "BOTH"},
                  {"symbol": "ETHUSDT", "positionAmt": "0", "leverage": "5"}],
}
PLAIN_ORDER = {"symbol": "BTCUSDT", "side": "SELL", "type": "LIMIT", "clientOrderId": "c1",
               "price": "61000", "status": "NEW", "reduceOnly": True}
ALGO_ORDER = {"symbol": "BTCUSDT", "side": "SELL", "orderType": "STOP_MARKET", "clientAlgoId": "a1",
              "triggerPrice": "59000", "closePosition": True, "algoStatus": "NEW", "algoId": 7}


@pytest.fixture
def venue(monkeypatch):
    """Answer by path; record (method, path, key header) for every request."""
    seen: list[tuple[str, str, str]] = []
    answers = {account.ACCOUNT_PATH: ACCOUNT_BODY, account.INCOME_PATH: [],
               account.OPEN_ORDERS_PATH: [PLAIN_ORDER], account.ALGO_OPEN_ORDERS_PATH: [ALGO_ORDER]}

    def fake_urlopen(request, timeout=None):
        path = urllib.parse.urlparse(request.full_url).path
        seen.append((request.get_method(), path, request.headers.get("X-mbx-apikey")))
        return _Response(answers[path])

    monkeypatch.setattr(account.urllib.request, "urlopen", fake_urlopen)
    return seen


@pytest.fixture
def write_gate_closed_all_keys_set(monkeypatch):
    """The machine after R1 and H1-b: the live write gate closed, the account-feed gate open, and every
    key present, so a fallback would have something to fall back to."""
    monkeypatch.delenv(live_execution.LIVE_TRADING_ENV, raising=False)
    monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    monkeypatch.setenv(account.READ_API_KEY_ENV, READ_KEY)
    monkeypatch.setenv(account.READ_API_SECRET_ENV, "read-secret")
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, TRADING_KEY)
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "trading-secret")
    monkeypatch.setenv(live_execution.ORDER_API_KEY_ENV, ORDER_KEY)
    monkeypatch.setenv(live_execution.ORDER_API_SECRET_ENV, "order-secret")


# --- the planes and their credentials ---------------------------------------------------------

def test_each_plane_has_its_own_pair_and_the_read_pair_is_no_trading_name():
    assert account.PLANE_CREDENTIALS == {
        account.PLANE_READ: ("BINANCE_READ_API_KEY", "BINANCE_READ_API_SECRET"),
        account.PLANE_TRADING: ("BINANCE_ACCOUNT_API_KEY", "BINANCE_ACCOUNT_API_SECRET"),
    }
    trading_names = {account.ACCOUNT_API_KEY_ENV, account.ACCOUNT_API_SECRET_ENV,
                     live_execution.ORDER_API_KEY_ENV, live_execution.ORDER_API_SECRET_ENV}
    assert not set(account.PLANE_CREDENTIALS[account.PLANE_READ]) & trading_names


def test_every_selection_names_its_plane():
    with pytest.raises(TypeError):
        account.select_account_feed()                      # no default plane
    with pytest.raises(TypeError):
        account.read_account()
    with pytest.raises(TypeError):
        account.BinanceFuturesAccountFeed(authorization=_AUTH)


@pytest.mark.parametrize("gate_open", [True, False], ids=["gate-open", "gate-closed"])
def test_an_unknown_plane_is_refused_whether_or_not_the_gate_is_open(monkeypatch, gate_open):
    if gate_open:
        monkeypatch.setenv(account.ACCOUNT_FEED_ENV, account.BINANCE_ACCOUNT)
    else:
        monkeypatch.delenv(account.ACCOUNT_FEED_ENV, raising=False)
    for plane in ("READ", "write", "", "trading "):
        with pytest.raises(ToolBlocked) as exc:
            account.select_account_feed(plane=plane)
        assert exc.value.reason_code == "ACCOUNT_PLANE_UNKNOWN"


# --- no fallback ----------------------------------------------------------------------------------

def test_a_read_feed_never_falls_back_to_a_trading_key(monkeypatch, venue, write_gate_closed_all_keys_set):
    """The read pair is missing; the account pair and the order key are both set. The read refuses,
    names only the read variables, and opens no socket."""
    monkeypatch.delenv(account.READ_API_KEY_ENV)
    monkeypatch.delenv(account.READ_API_SECRET_ENV)
    feed = account.select_account_feed(plane=account.PLANE_READ)
    for call in (lambda: feed.account_snapshot(timeout_seconds=1),
                 lambda: feed.open_orders(timeout_seconds=1),
                 lambda: feed.algo_open_orders(timeout_seconds=1)):
        with pytest.raises(ToolError) as exc:
            call()
        assert exc.value.reason_code == "NO_API_KEY"
        assert account.READ_API_KEY_ENV in str(exc.value) and account.ACCOUNT_API_KEY_ENV not in str(exc.value)
    assert venue == []
    snapshot, record = account.read_account(plane=account.PLANE_READ, timeout_seconds=1)
    assert snapshot is None and record["error_reason_code"] == "NO_API_KEY"


def test_the_trading_plane_does_not_borrow_the_read_key_either(monkeypatch, venue, write_gate_closed_all_keys_set):
    monkeypatch.delenv(account.ACCOUNT_API_KEY_ENV)
    monkeypatch.delenv(account.ACCOUNT_API_SECRET_ENV)
    feed = account.select_account_feed(plane=account.PLANE_TRADING)
    with pytest.raises(ToolError) as exc:
        feed.account_snapshot(timeout_seconds=1)
    assert exc.value.reason_code == "NO_API_KEY" and venue == []


# --- the read plane sees with the write gate closed -------------------------------------------------

def test_with_the_write_gate_closed_the_read_plane_reads_and_the_write_plane_cannot_send(
        venue, write_gate_closed_all_keys_set):
    assert isinstance(live_execution.select_order_adapter(), live_execution.DryRunOrderAdapter)
    feed = account.select_account_feed(plane=account.PLANE_READ)
    assert feed.network_egress is True

    snapshot = feed.account_snapshot(timeout_seconds=1)
    assert [p.symbol for p in snapshot.positions] == ["BTCUSDT"]          # positions: the zero row dropped
    assert snapshot.margin_balance == 101.0
    plain = feed.open_orders(timeout_seconds=1)
    algo = feed.algo_open_orders(timeout_seconds=1)
    assert [o["clientOrderId"] for o in plain] == ["c1"]
    assert len(algo) == 1 and algo[0]["symbol"] == "BTCUSDT"

    assert {method for method, _, _ in venue} == {"GET"}
    assert {path for _, path, _ in venue} <= account.READ_PATHS
    assert {key for _, _, key in venue} == {READ_KEY}                      # never the trading or order key


def test_the_algo_list_filters_by_symbol_after_the_read(venue, write_gate_closed_all_keys_set):
    feed = account.select_account_feed(plane=account.PLANE_READ)
    assert feed.algo_open_orders("ETHUSDT", timeout_seconds=1) == []
    assert len(feed.algo_open_orders("BTCUSDT", timeout_seconds=1)) == 1


def test_a_malformed_order_list_is_not_nothing_resting(monkeypatch, write_gate_closed_all_keys_set):
    monkeypatch.setattr(account.urllib.request, "urlopen", lambda request, timeout=None: _Response({"oops": 1}))
    feed = account.select_account_feed(plane=account.PLANE_READ)
    for call in (feed.open_orders, feed.algo_open_orders):
        with pytest.raises(ToolError):
            call(timeout_seconds=1)


# --- no write surface ------------------------------------------------------------------------------

WRITE_SURFACE = ("submit", "cancel_order", "close_position", "transfer", "set_leverage", "set_margin_type",
                 "set_margin_mode", "validate_order", "change_leverage", "subscribe", "redeem")


@pytest.mark.parametrize("cls", [account.BinanceFuturesAccountFeed, account.NoAccountFeed,
                                 binance_wallet.BinanceWalletFeed])
def test_the_read_clients_have_no_write_surface(cls):
    assert [name for name in WRITE_SURFACE if hasattr(cls, name)] == []
    assert not issubclass(cls, live_execution.BinanceFuturesVenueReader)


@pytest.mark.parametrize("path", ["/fapi/v1/order", "/fapi/v1/algoOrder", "/fapi/v1/leverage",
                                  "/fapi/v1/marginType", "/fapi/v1/allOpenOrders", "/fapi/v1/algoOpenOrders",
                                  "/fapi/v1/order/test", "/sapi/v1/asset/transfer"])
def test_a_path_outside_the_get_allowlist_is_refused_before_a_socket(monkeypatch, venue,
                                                                     write_gate_closed_all_keys_set, path):
    feed = account.select_account_feed(plane=account.PLANE_READ)
    with pytest.raises(ToolBlocked) as exc:
        feed._signed_get(path, {}, timeout_seconds=1)
    assert exc.value.reason_code == "ACCOUNT_PATH_REFUSED"
    assert venue == []


def test_the_read_allowlist_is_gets_of_reads_only():
    assert account.READ_PATHS == {"/fapi/v2/account", "/fapi/v1/income", "/fapi/v1/userTrades",
                                  "/fapi/v1/openOrders", "/fapi/v1/openAlgoOrders"}
    source = (REPO / "runtime/mvp_runtime/crypto/account.py").read_text(encoding="utf-8")
    assert 'method="GET"' in source and 'method="POST"' not in source and '"DELETE"' not in source


# --- which caller reads on which plane --------------------------------------------------------------

# Observation reads with the read-only key; the write plane's own reads keep the trading pair, so an
# emergency close does not start depending on a key the write plane does not own.
EXPECTED_PLANES = {
    # observation
    ("runtime/mvp_runtime/crypto/account.py", "main"): "PLANE_READ",
    ("runtime/mvp_runtime/crypto/account.py", "read_fee_rates"): "PLANE_READ",
    ("runtime/mvp_runtime/crypto/account_store.py", "refresh_snapshot"): "PLANE_READ",
    ("runtime/mvp_runtime/crypto/dashboard.py", "main"): "PLANE_READ",
    ("runtime/mvp_runtime/crypto/live_readiness.py", "build_readiness"): "PLANE_READ",
    ("scripts/list_resting_orders.py", "main"): "PLANE_READ",
    # the write plane's own reads
    ("runtime/mvp_runtime/crypto/live_route.py", "_read_leg_facts"): "PLANE_TRADING",
    ("runtime/mvp_runtime/crypto/live_route.py", "_settle_or_protect"): "PLANE_TRADING",
    ("runtime/mvp_runtime/crypto/live_route.py", "run_emergency_close"): "PLANE_TRADING",
    ("scripts/run_slippage_probe.py", "run_fire"): "PLANE_TRADING",
    # the pass-through: read_account hands its own required argument to select_account_feed
    ("runtime/mvp_runtime/crypto/account.py", "read_account"): "plane",
}


def _plane_calls() -> dict[tuple[str, str], set[str]]:
    found: dict[tuple[str, str], set[str]] = {}
    for base in ("runtime", "scripts"):
        for path in sorted((REPO / base).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for node in ast.walk(func):
                    if not isinstance(node, ast.Call):
                        continue
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if name not in ("select_account_feed", "read_account"):
                        continue
                    plane = next((kw.value for kw in node.keywords if kw.arg == "plane"), None)
                    label = (plane.attr if isinstance(plane, ast.Attribute)
                             else getattr(plane, "id", repr(plane)))
                    key = (str(path.relative_to(REPO)), func.name)
                    found.setdefault(key, set()).add(label)
    return found


def test_every_account_read_names_the_plane_it_was_assigned():
    actual = {k: ",".join(sorted(v)) for k, v in _plane_calls().items()}
    assert actual == EXPECTED_PLANES, (
        "an account read appeared, moved or changed plane. Observation reads on PLANE_READ; only the "
        "write plane's own reads may use PLANE_TRADING. Assign it here, in the same PR."
    )


# --- the holdings wallet read: the read pair, GET only, gate never set by compose -------------------

def test_the_wallet_reads_the_read_pair_and_never_a_trading_name():
    assert (binance_wallet.API_KEY_ENV, binance_wallet.API_SECRET_ENV) == account.PLANE_CREDENTIALS[account.PLANE_READ]
    source = (REPO / "runtime/mvp_runtime/holdings/binance_wallet.py").read_text(encoding="utf-8")
    for name in ("BINANCE_ACCOUNT_API", "MVP_LIVE_ORDER_API", "MVP_TESTNET_ORDER_API"):
        assert f'"{name}' not in source, f"the wallet names a trading credential: {name}"


def test_the_wallet_does_not_fall_back_either(monkeypatch):
    monkeypatch.setenv(binance_wallet.BINANCE_WALLET_ENV, binance_wallet.BINANCE_WALLET_ON)
    monkeypatch.delenv(binance_wallet.API_KEY_ENV, raising=False)
    monkeypatch.delenv(binance_wallet.API_SECRET_ENV, raising=False)
    monkeypatch.setenv(account.ACCOUNT_API_KEY_ENV, TRADING_KEY)
    monkeypatch.setenv(account.ACCOUNT_API_SECRET_ENV, "trading-secret")

    def _no_socket(*_a, **_k):
        raise AssertionError("a socket was opened without the read key")

    monkeypatch.setattr(binance_wallet.urllib.request, "urlopen", _no_socket)
    assert binance_wallet.read_wallet() == (None, "NO_API_KEY")


def test_compose_never_switches_the_wallet_gate_on():
    """The one-off H1-b probe sets MVP_BINANCE_WALLET in its own `docker exec` process only. A probe that
    succeeds is not an activation: no service's compose environment may give the gate a value."""
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text(encoding="utf-8"))
    for name, spec in compose["services"].items():
        value = (spec.get("environment") or {}).get(binance_wallet.BINANCE_WALLET_ENV)
        assert value in (None, "${%s:-}" % binance_wallet.BINANCE_WALLET_ENV), (name, value)
