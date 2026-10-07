"""The holdings lane: the Toss feed, its two renders, the snapshot store, the fire and the doors.

What is pinned here, and why each one is a decision rather than a detail:

1. **Its own gate.** ``MVP_TOSS_ACCOUNT=toss`` and nothing else opens it; ``MVP_ACCOUNT_FEED`` (the live
   money path's account) cannot. The runtime reaches the lane only through the scheduler fire and the
   console verb — D4's "no door reads it", as an import-graph property.
2. **Read-only.** The feed reaches the token endpoint and four reads, and has no method whose name
   could place, cancel or trade.
3. **The external-send boundary** (appendices A and B). The aggregate view's key set is exact, and no
   symbol, name, per-symbol number or exchange rate reaches it or the stored file.
4. **One token issuer.** Toss revokes the previous token on every issuance: a ``token-revoked`` 401 is
   one reissue and one retry, a second 401 is a degraded read, never a loop.
5. **Secrets.** No client secret, token or full account number in any render, warning or error.

The fake responses are the examples in Toss's own ``openapi.json`` (005930 and AAPL). No test opens a
socket: ``urlopen`` is replaced in the module namespace.
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import re
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime import domain_console, read_bridge, schedule_delegation, scheduler
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.holdings import allocation, board, combined, store, toss_account
from runtime.mvp_runtime.holdings.model import NoHoldingsFeed
from runtime.mvp_runtime.holdings.toss_account import (
    ACCOUNTS_PATH,
    BUYING_POWER_PATH,
    EXCHANGE_RATE_PATH,
    HOLDINGS_PATH,
    TOKEN_PATH,
    TOSS_ACCOUNT_ENV,
    TOSS_ACCOUNT_ON,
    TOSS_ACCOUNT_SEQ_ENV,
    TOSS_CLIENT_ID_ENV,
    TOSS_CLIENT_SECRET_ENV,
    TOSS_PROVIDER,
    TossHoldingsFeed,
    read_holdings,
    select_holdings_feed,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
NOW = "2026-10-07T09:00:00Z"
LATER = "2026-10-07T13:30:00Z"

CLIENT_ID = "c_01HXYZCLIENTDONOTLEAK"
CLIENT_SECRET = "s_secretdonotleak0002"
TOKEN = "eyJ0b2tlbi1kby1ub3QtbGVhaw"
ACCOUNT_NO = "12345678901"

# Toss's own openapi.json examples.
HOLDINGS = {
    "totalPurchaseAmount": {"krw": "6500000", "usd": "1553"},
    "marketValue": {"amount": {"krw": "7200000", "usd": "1785"},
                    "amountAfterCost": {"krw": "7050000", "usd": "1771.43"}},
    "profitLoss": {"amount": {"krw": "700000", "usd": "232"}, "amountAfterCost": {"krw": "550000", "usd": "218.43"},
                   "rate": "0.1179", "rateAfterCost": "0.0983"},
    "dailyProfitLoss": {"amount": {"krw": "100000", "usd": "25"}, "rate": "0.0141"},
    "items": [
        {"symbol": "005930", "name": "삼성전자", "marketCountry": "KR", "currency": "KRW", "quantity": "100",
         "lastPrice": "72000", "averagePurchasePrice": "65000",
         "marketValue": {"purchaseAmount": "6500000", "amount": "7200000", "amountAfterCost": "7050000"},
         "profitLoss": {"amount": "700000", "amountAfterCost": "550000", "rate": "0.1077", "rateAfterCost": "0.0846"},
         "dailyProfitLoss": {"amount": "100000", "rate": "0.0141"}, "cost": {"commission": "14400", "tax": "135600"}},
        {"symbol": "AAPL", "name": "Apple Inc.", "marketCountry": "US", "currency": "USD", "quantity": "10",
         "lastPrice": "178.5", "averagePurchasePrice": "155.3",
         "marketValue": {"purchaseAmount": "1553", "amount": "1785", "amountAfterCost": "1771.43"},
         "profitLoss": {"amount": "232", "amountAfterCost": "218.43", "rate": "0.1494", "rateAfterCost": "0.1406"},
         "dailyProfitLoss": {"amount": "25", "rate": "0.0142"}, "cost": {"commission": "3.57", "tax": "10"}},
    ],
}
ACCOUNTS = [{"accountNo": ACCOUNT_NO, "accountSeq": 1, "accountType": "BROKERAGE"}]
CASH = {"KRW": "5000000", "USD": "100"}
RATE = {"baseCurrency": "USD", "quoteCurrency": "KRW", "rate": "1380.5", "midRate": "1375",
        "basisPoint": "40", "rateChangeType": "UP",
        "validFrom": "2026-03-25T09:30:00+09:00", "validUntil": "2026-03-25T09:31:00+09:00"}


class _Response(io.BytesIO):
    def __init__(self, body: dict):
        super().__init__(json.dumps(body).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(url: str, status: int, code: str = "") -> urllib.error.HTTPError:
    body = json.dumps({"error": {"requestId": "r1", "code": code, "message": CLIENT_SECRET}}).encode()
    return urllib.error.HTTPError(url, status, "error", {}, io.BytesIO(body))


class _Toss:
    """A fake Toss: routes by path, records every request. ``fail`` maps a path to a list of
    exceptions handed out (one per call) before that path answers normally."""

    def __init__(self, *, accounts=None, holdings=None, cash=None, rate=None, fail=None):
        self.sent: list = []
        self.accounts = ACCOUNTS if accounts is None else accounts
        self.holdings = HOLDINGS if holdings is None else holdings
        self.cash = CASH if cash is None else cash
        self.rate = RATE if rate is None else rate
        self.fail = {path: list(errors) for path, errors in (fail or {}).items()}
        self.tokens_issued = 0

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        parsed = urllib.parse.urlparse(request.full_url)
        if self.fail.get(parsed.path):
            raise self.fail[parsed.path].pop(0)
        if parsed.path == TOKEN_PATH:
            self.tokens_issued += 1
            return _Response({"access_token": f"{TOKEN}{self.tokens_issued}", "token_type": "Bearer",
                              "expires_in": 86400})
        if parsed.path == ACCOUNTS_PATH:
            return _Response({"result": self.accounts})
        if parsed.path == HOLDINGS_PATH:
            return _Response({"result": self.holdings})
        if parsed.path == BUYING_POWER_PATH:
            currency = urllib.parse.parse_qs(parsed.query)["currency"][0]
            return _Response({"result": {"currency": currency, "cashBuyingPower": self.cash[currency]}})
        if parsed.path == EXCHANGE_RATE_PATH:
            return _Response({"result": self.rate})
        raise AssertionError(f"request to an unexpected path: {parsed.path}")

    def paths(self) -> list[str]:
        return [urllib.parse.urlparse(r.full_url).path for r in self.sent]


@pytest.fixture(autouse=True)
def _fresh_feed_cache(monkeypatch):
    monkeypatch.setattr(store, "_cached_feed", None)


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(TOSS_CLIENT_ID_ENV, CLIENT_ID)
    monkeypatch.setenv(TOSS_CLIENT_SECRET_ENV, CLIENT_SECRET)
    monkeypatch.delenv(TOSS_ACCOUNT_SEQ_ENV, raising=False)


@pytest.fixture
def gate_open(monkeypatch, creds):
    monkeypatch.setenv(TOSS_ACCOUNT_ENV, TOSS_ACCOUNT_ON)


def _toss(monkeypatch, **kwargs) -> _Toss:
    fake = _Toss(**kwargs)
    monkeypatch.setattr(toss_account.urllib.request, "urlopen", fake)
    return fake


def _leaks(text: str) -> list[str]:
    return [s for s in (CLIENT_ID, CLIENT_SECRET, TOKEN, ACCOUNT_NO) if s in text]


# --- the gate ---------------------------------------------------------------------------

def test_without_the_env_var_the_board_is_inert(monkeypatch, creds):
    monkeypatch.delenv(TOSS_ACCOUNT_ENV, raising=False)
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)
    assert read_holdings() == (None, "NOT_CONFIGURED")


def test_the_env_var_alone_opens_the_gate(gate_open):
    feed = select_holdings_feed()
    assert isinstance(feed, TossHoldingsFeed)
    assert feed.provider_id == TOSS_PROVIDER


def test_a_different_value_does_not_open_the_gate(monkeypatch, creds):
    monkeypatch.setenv(TOSS_ACCOUNT_ENV, "kis")
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)


def test_the_live_account_feeds_opt_in_does_not_open_this_one(monkeypatch, creds):
    monkeypatch.delenv(TOSS_ACCOUNT_ENV, raising=False)
    monkeypatch.setenv("MVP_ACCOUNT_FEED", "binance_futures_account")
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)


def test_a_directly_constructed_feed_cannot_open_a_socket(monkeypatch, gate_open):
    fake = _toss(monkeypatch)
    with pytest.raises(SafetyGateBlocked):
        TossHoldingsFeed().holdings_snapshot(timeout_seconds=1)
    assert fake.sent == []


def test_withdrawing_the_env_var_stops_egress(monkeypatch, gate_open):
    fake = _toss(monkeypatch)
    feed = select_holdings_feed()
    monkeypatch.delenv(TOSS_ACCOUNT_ENV)
    with pytest.raises(SafetyGateBlocked) as excinfo:
        feed.holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "ENV_OPT_IN_WITHDRAWN"
    assert fake.sent == []


# --- read-only and the import graph -----------------------------------------------------

def test_the_feed_reaches_only_the_token_and_four_reads(monkeypatch, gate_open):
    fake = _toss(monkeypatch)
    read_holdings()
    assert set(fake.paths()) == {TOKEN_PATH, ACCOUNTS_PATH, HOLDINGS_PATH, BUYING_POWER_PATH, EXCHANGE_RATE_PATH}
    assert {r.get_method() for r in fake.sent if urllib.parse.urlparse(r.full_url).path != TOKEN_PATH} == {"GET"}
    assert {urllib.parse.urlparse(r.full_url).netloc for r in fake.sent} == {"openapi.tossinvest.com"}


def test_the_token_request_is_form_encoded(monkeypatch, gate_open):
    fake = _toss(monkeypatch)
    read_holdings()
    token_request = fake.sent[0]
    assert token_request.get_header("Content-type") == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(token_request.data.decode())["grant_type"] == ["client_credentials"]


def test_account_reads_carry_the_account_seq_not_the_account_number(monkeypatch, gate_open):
    fake = _toss(monkeypatch)
    read_holdings()
    headers = {r.get_header("X-tossinvest-account") for r in fake.sent
               if urllib.parse.urlparse(r.full_url).path in (HOLDINGS_PATH, BUYING_POWER_PATH)}
    assert headers == {"1"}


def test_the_feed_has_no_order_capability():
    forbidden = ("order", "submit", "cancel", "trade", "buy", "sell", "place")
    for cls in (TossHoldingsFeed, NoHoldingsFeed):
        public = [name for name in dir(cls) if not name.startswith("_")]
        assert not [n for n in public if any(word in n.lower() for word in forbidden)], cls


# The two dispatch sites, exactly: the scheduler's `holdings_refresh` fire and the console's
# `/holdings` verb. Both import the lane inside a function — the core's dispatch shape.
HOLDINGS_DISPATCH_SITES = {"runtime/mvp_runtime/scheduler.py", "runtime/mvp_runtime/domain_console.py"}


def _holdings_imports(path: pathlib.Path) -> tuple[bool, bool]:
    tree = ast.parse(path.read_text(encoding="utf-8"))

    def names_holdings(node: ast.AST) -> bool:
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            return "holdings" in module.split(".") or (
                node.level > 0 and not module and any(a.name == "holdings" for a in node.names)
            )
        if isinstance(node, ast.Import):
            return any("holdings" in a.name.split(".") for a in node.names)
        return False

    return any(names_holdings(n) for n in tree.body), any(names_holdings(n) for n in ast.walk(tree))


def test_the_runtime_reaches_the_holdings_lane_only_through_its_two_dispatch_sites():
    importers, module_level = set(), set()
    for path in (ROOT / "runtime").rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith("runtime/mvp_runtime/holdings/"):
            continue
        at_module, anywhere = _holdings_imports(path)
        if anywhere:
            importers.add(rel)
        if at_module:
            module_level.add(rel)
    assert module_level == set()
    assert importers == HOLDINGS_DISPATCH_SITES


# --- the account ------------------------------------------------------------------------

def test_one_brokerage_account_is_chosen_without_configuration(monkeypatch, gate_open):
    _toss(monkeypatch)
    snapshot, reason = read_holdings()
    assert reason is None and snapshot.account == "****8901"


def test_several_brokerage_accounts_refuse_rather_than_guess(monkeypatch, gate_open):
    two = ACCOUNTS + [{"accountNo": "99999999999", "accountSeq": 2, "accountType": "BROKERAGE"}]
    fake = _toss(monkeypatch, accounts=two)
    assert read_holdings() == (None, "TOSS_ACCOUNT_AMBIGUOUS")
    assert HOLDINGS_PATH not in fake.paths()


def test_the_account_seq_setting_picks_one(monkeypatch, gate_open):
    two = ACCOUNTS + [{"accountNo": "99999999999", "accountSeq": 2, "accountType": "BROKERAGE"}]
    monkeypatch.setenv(TOSS_ACCOUNT_SEQ_ENV, "2")
    _toss(monkeypatch, accounts=two)
    snapshot, _ = read_holdings()
    assert snapshot.account == "****9999"


def test_an_account_seq_that_matches_nothing_refuses(monkeypatch, gate_open):
    monkeypatch.setenv(TOSS_ACCOUNT_SEQ_ENV, "7")
    _toss(monkeypatch)
    assert read_holdings() == (None, "TOSS_ACCOUNT_NOT_FOUND")


# --- credentials, the token, errors -----------------------------------------------------

def test_missing_credentials_name_the_variables_and_never_a_value(monkeypatch, gate_open):
    monkeypatch.delenv(TOSS_CLIENT_SECRET_ENV)
    fake = _toss(monkeypatch)
    assert read_holdings() == (None, "NO_API_KEY")
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert TOSS_CLIENT_SECRET_ENV in str(excinfo.value)
    assert _leaks(str(excinfo.value)) == []
    assert fake.sent == []


def test_a_revoked_token_is_reissued_once_and_the_call_retried_once(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/api/v1/holdings"
    fake = _toss(monkeypatch, fail={HOLDINGS_PATH: [_http_error(url, 401, "token-revoked")]})
    snapshot, reason = read_holdings()
    assert reason is None and snapshot is not None
    assert fake.paths().count(TOKEN_PATH) == 2
    assert fake.paths().count(HOLDINGS_PATH) == 2


def test_a_second_401_is_a_degraded_read_never_a_loop(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/api/v1/holdings"
    fake = _toss(monkeypatch, fail={HOLDINGS_PATH: [_http_error(url, 401, "token-revoked"),
                                                   _http_error(url, 401, "token-revoked")]})
    assert read_holdings() == (None, "TOSS_REJECTED")
    assert fake.paths().count(TOKEN_PATH) == 2
    assert fake.paths().count(HOLDINGS_PATH) == 2


def test_a_non_token_401_is_not_retried(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/api/v1/holdings"
    fake = _toss(monkeypatch, fail={HOLDINGS_PATH: [_http_error(url, 401, "login-user-not-found")]})
    assert read_holdings() == (None, "TOSS_REJECTED")
    assert fake.paths().count(TOKEN_PATH) == 1


def test_an_unregistered_ip_is_named(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/api/v1/accounts"
    _toss(monkeypatch, fail={ACCOUNTS_PATH: [_http_error(url, 403, "edge-blocked")]})
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "TOSS_FORBIDDEN"
    assert "edge-blocked" in str(excinfo.value)
    assert _leaks(str(excinfo.value)) == []  # the error body's message is never echoed


def test_a_token_rejection_is_one_attempt_and_its_body_is_never_read(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/oauth2/token"
    fake = _toss(monkeypatch, fail={TOKEN_PATH: [_http_error(url, 401, "invalid_client")]})
    assert read_holdings() == (None, "TOSS_TOKEN_REJECTED")
    assert fake.paths() == [TOKEN_PATH]


def test_a_transport_failure_says_nothing_about_the_request(monkeypatch, gate_open):
    _toss(monkeypatch, fail={TOKEN_PATH: [urllib.error.URLError("refused by openapi.tossinvest.com")]})
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "TOOL_TRANSPORT"
    assert "tossinvest" not in str(excinfo.value)


# --- the money math ---------------------------------------------------------------------

def test_overseas_amounts_are_converted_with_the_mid_rate(monkeypatch, gate_open):
    _toss(monkeypatch)
    snapshot, _ = read_holdings()
    assert snapshot.domestic.holdings_value_krw == 7_200_000
    assert snapshot.domestic.cash_krw == 5_000_000
    assert snapshot.overseas.holdings_value_krw == 1785 * 1375
    assert snapshot.overseas.cash_krw == 100 * 1375
    assert snapshot.overseas.unrealized_pnl_krw == 232 * 1375
    assert snapshot.warnings == ()


def test_one_rate_per_read_even_with_no_dollar(monkeypatch, gate_open):
    """P2 converts the Binance USDT balance with the rate this read used, so the rate is read every
    time — once, never twice — and rides the snapshot in-process only."""
    domestic_only = json.loads(json.dumps(HOLDINGS))
    domestic_only["totalPurchaseAmount"]["usd"] = None
    domestic_only["marketValue"]["amount"]["usd"] = None
    domestic_only["profitLoss"]["amount"]["usd"] = None
    domestic_only["items"] = domestic_only["items"][:1]
    fake = _toss(monkeypatch, holdings=domestic_only, cash={"KRW": "5000000", "USD": "0"})
    snapshot, _ = read_holdings()
    assert fake.paths().count(EXCHANGE_RATE_PATH) == 1
    assert snapshot.overseas.holdings_value_krw == 0
    assert snapshot.usd_krw_rate == 1375
    assert "usd_krw_rate" not in board.aggregate_view(snapshot)


def test_an_unreadable_rate_leaves_the_overseas_side_absent_not_guessed(monkeypatch, gate_open):
    url = "https://openapi.tossinvest.com/api/v1/exchange-rate"
    _toss(monkeypatch, fail={EXCHANGE_RATE_PATH: [_http_error(url, 500, "internal")]})
    snapshot, _ = read_holdings()
    assert snapshot.overseas.holdings_value_krw is None
    assert snapshot.domestic.holdings_value_krw == 7_200_000
    assert any("exchange rate unavailable" in w for w in snapshot.warnings)
    assert board.aggregate_view(snapshot)["partial"] is True


def test_a_missing_field_is_absent_and_warned_never_zero(monkeypatch, gate_open):
    holdings = json.loads(json.dumps(HOLDINGS))
    del holdings["marketValue"]["amount"]["krw"]
    _toss(monkeypatch, holdings=holdings)
    snapshot, _ = read_holdings()
    assert snapshot.domestic.holdings_value_krw is None
    assert any("marketValue.amount.krw" in w for w in snapshot.warnings)


# --- the two renders --------------------------------------------------------------------

PRICE_REVEALING = ("005930", "삼성전자", "AAPL", "Apple", "72000", "178.5", "1375", "1785")


def _revealed(text: str) -> list[str]:
    """The price-revealing tokens present as whole tokens. A number is matched with digit boundaries:
    the example's one domestic holding makes its total 7200000, which contains the price 72000 as a
    substring without revealing it."""
    return [t for t in PRICE_REVEALING if re.search(rf"(?<![\d.]){re.escape(t)}(?![\d])", text)]


def _snapshot(monkeypatch):
    _toss(monkeypatch)
    snapshot, _ = read_holdings()
    return snapshot


def test_the_aggregate_view_has_exactly_the_decided_keys(monkeypatch, gate_open):
    view = board.aggregate_view(_snapshot(monkeypatch))
    assert set(view) == board.AGGREGATE_KEYS
    assert view["known_total_krw"] == 7_200_000 + 1785 * 1375 + 5_000_000 + 100 * 1375
    assert view["partial"] is False
    assert round(sum(view["weights"].values())) == 100


def test_nothing_that_reveals_a_price_or_the_rate_reaches_the_aggregate(monkeypatch, gate_open):
    snapshot = _snapshot(monkeypatch)
    outward = board.render_aggregate(snapshot) + json.dumps(board.aggregate_view(snapshot), ensure_ascii=False)
    assert _revealed(outward) == []
    assert _leaks(outward) == []


def test_the_full_board_shows_the_positions(monkeypatch, gate_open):
    text = board.render_full(_snapshot(monkeypatch))
    assert "005930" in text and "AAPL" in text
    assert _leaks(text) == []


def test_an_unavailable_board_says_why():
    assert "NOT_CONFIGURED" in board.render_aggregate(None)
    assert "TOSS_REJECTED" in board.render_full(None, reason_code="TOSS_REJECTED")


# --- the snapshot store -----------------------------------------------------------------

def test_the_snapshot_holds_the_aggregate_and_nothing_else(monkeypatch, gate_open, tmp_path):
    _toss(monkeypatch)
    assert store.refresh_snapshot(now=NOW, root=tmp_path).startswith("holdings snapshot: refreshed")
    raw = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    body = json.loads(raw)
    assert set(body) == board.AGGREGATE_KEYS | {"record_type", "as_of", "written_at", "combined", "allocation"}
    assert set(body["combined"]) == combined.COMBINED_KEYS
    assert set(body["allocation"]) == allocation.ALLOCATION_KEYS
    for row in body["allocation"]["classes"].values():
        assert set(row) == allocation.CLASS_ROW_KEYS
    assert _revealed(raw) == []
    assert _leaks(raw) == []


def test_fires_share_one_token_while_the_gate_stays_open(monkeypatch, gate_open, tmp_path):
    fake = _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    store.refresh_snapshot(now=LATER, root=tmp_path)
    assert fake.paths().count(TOKEN_PATH) == 1
    assert fake.paths().count(ACCOUNTS_PATH) == 1  # the account is resolved once per process


def test_closing_the_gate_drops_the_cached_feed(monkeypatch, gate_open, tmp_path):
    fake = _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    monkeypatch.delenv(TOSS_ACCOUNT_ENV)
    assert store.refresh_snapshot(now=LATER, root=tmp_path) == "holdings snapshot: no broker account configured"
    assert store._cached_feed is None
    monkeypatch.setenv(TOSS_ACCOUNT_ENV, TOSS_ACCOUNT_ON)
    store.refresh_snapshot(now=LATER, root=tmp_path)
    assert fake.paths().count(TOKEN_PATH) == 2


def test_a_failed_read_keeps_the_last_good_snapshot(monkeypatch, gate_open, tmp_path):
    _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    good = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    monkeypatch.setattr(store, "_cached_feed", None)
    _toss(monkeypatch, fail={TOKEN_PATH: [urllib.error.URLError("down")]})
    status = store.refresh_snapshot(now=LATER, root=tmp_path)
    assert status == "holdings snapshot: degraded (TOOL_TRANSPORT); kept the previous one"
    assert store.snapshot_path(tmp_path).read_text(encoding="utf-8") == good
    assert json.loads(store.refresh_mark_path(tmp_path).read_text())["attempted_at"] == LATER


def test_the_board_says_when_there_is_no_snapshot(tmp_path):
    text, data = store.load_holdings_view(now=NOW, root=tmp_path)
    assert "no snapshot yet" in text
    assert data == {"available": False, "reason_code": store.HOLDINGS_SNAPSHOT_MISSING, "last_attempt": None}


def test_an_old_snapshot_shows_its_number_and_says_it_is_stale(monkeypatch, gate_open, tmp_path):
    _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    as_of = json.loads(store.snapshot_path(tmp_path).read_text())["as_of"]
    text, data = store.load_holdings_view(now="2099-01-01T00:00:00Z", root=tmp_path)
    assert data["stale"] is True and "STALE" in text
    assert "7,200,000 KRW" in text and "(toss)" in text
    fresh_text, fresh = store.load_holdings_view(now=as_of, root=tmp_path)
    assert fresh["stale"] is False and "STALE" not in fresh_text


def test_an_unreadable_snapshot_raises_rather_than_rendering_empty(tmp_path):
    path = store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        store.load_holdings_view(now=NOW, root=tmp_path)
    assert excinfo.value.reason_code == store.HOLDINGS_SNAPSHOT_UNREADABLE


# --- the fire and the doors -------------------------------------------------------------

def _holdings_schedule() -> scheduler.Schedule:
    return scheduler.Schedule(
        schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS,
        request="", interval_seconds=3600, enabled=True, created_by="test",
        created_at=NOW, next_run_at=NOW,
    )


def _fire(tmp_path):
    return scheduler._execute(
        _holdings_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )


def test_the_maintenance_fire_writes_the_snapshot(monkeypatch, gate_open, tmp_path):
    _toss(monkeypatch)
    assert _fire(tmp_path).startswith("holdings snapshot: refreshed")
    assert store.snapshot_path(tmp_path).exists()


def test_an_unconfigured_fire_is_a_status_line_not_a_failure(monkeypatch, creds, tmp_path):
    monkeypatch.delenv(TOSS_ACCOUNT_ENV, raising=False)
    assert _fire(tmp_path) == "holdings snapshot: no broker account configured"


def test_the_kind_is_maintenance_and_not_the_assistants_to_change():
    assert scheduler.KIND_HOLDINGS in scheduler.MAINTENANCE_KINDS
    assert scheduler.KIND_HOLDINGS not in scheduler.RISK_KINDS
    assert scheduler.KIND_HOLDINGS in schedule_delegation.FINANCIAL_KINDS


def test_the_operator_verb_renders_the_snapshot(monkeypatch, gate_open, tmp_path):
    _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    command = domain_console.parse_domain_command("/holdings")
    assert command == ("HOLDINGS", None)
    outcome = domain_console.apply_domain_command(command, operator_id="op", now=NOW, repo_root=tmp_path)
    assert outcome["action"] == "HOLDINGS_STATUS"
    assert outcome["data"]["available"] is True
    assert _revealed(outcome["reply"] + json.dumps(outcome["data"], ensure_ascii=False)) == []


def test_the_assistant_read_is_dormant_until_the_policy_lists_it():
    assert "holdings_status" in read_bridge._READS
    assert "holdings_status" in read_bridge.POLICY_GATED_READS


# --- the script -------------------------------------------------------------------------

def test_the_script_renders_the_stored_snapshot_without_a_call(monkeypatch, gate_open, tmp_path, capsys):
    from scripts import holdings_board

    _toss(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    fake = _toss(monkeypatch)
    assert holdings_board.main([]) == 0
    out = capsys.readouterr().out
    assert "known total" in out and "005930" not in out
    assert fake.sent == []


def test_the_scripts_live_read_says_it_revoked_the_lanes_token(monkeypatch, gate_open, capsys):
    from scripts import holdings_board

    _toss(monkeypatch)
    assert holdings_board.main(["--full"]) == 0
    out = capsys.readouterr().out
    assert "005930" in out
    assert holdings_board.LIVE_READ_NOTICE in out


def test_the_scripts_live_read_blocks_on_a_failure(monkeypatch, gate_open, capsys):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    from scripts import holdings_board

    _toss(monkeypatch, fail={TOKEN_PATH: [urllib.error.URLError("down")]})
    assert holdings_board.main(["--full"]) == EXIT_BLOCKED
    assert "TOOL_TRANSPORT" in capsys.readouterr().out



# --- P2: the combined total and the drawdown (alert only) --------------------------------

from runtime.mvp_runtime import operator as operator_mod  # noqa: E402
from runtime.mvp_runtime.crypto import account as crypto_account  # noqa: E402
from runtime.mvp_runtime.crypto import account_store  # noqa: E402
from runtime.mvp_runtime.crypto import state as crypto_state  # noqa: E402

# Toss's example at 1375: 7,200,000 + 1785*1375 + 5,000,000 + 100*1375 KRW.
TOSS_TOTAL = 7_200_000 + 1785 * 1375 + 5_000_000 + 100 * 1375


def _binance_file(root, *, margin=1000.0, as_of=NOW, asset="USDT", configured=True, monkeypatch=None):
    """Write the Binance snapshot the way its authority does: account_store.refresh_snapshot over a
    real AccountSnapshot — so a format change in crypto/ breaks these tests, not the board silently."""
    snap = crypto_account.AccountSnapshot(
        asset=asset, wallet_balance=margin, margin_balance=margin, available_balance=margin,
        unrealized_pnl=0.0, positions=[], realized_windows={}, source="test", collected_at=as_of)
    record = crypto_account.snapshot_record(snap, feed=crypto_account.NoAccountFeed(), now=as_of)
    record["configured"] = configured
    monkeypatch.setattr(crypto_account, "read_account", lambda **_kw: (snap, record))
    assert account_store.refresh_snapshot(now=as_of, root=root) == "account snapshot: refreshed"


def _toss_view(partial=False, total=TOSS_TOTAL):
    return {"known_total_krw": total, "partial": partial}


def test_the_binance_side_mirrors_its_format_authority():
    assert combined.BINANCE_RECORD_TYPE == account_store.RECORD_TYPE
    assert combined.BINANCE_STALE_SECONDS == account_store.STALE_AFTER_SECONDS
    assert combined.BINANCE_SNAPSHOT_REL == f"{crypto_state.STATE_REL}/{account_store.SNAPSHOT_FILENAME}"


def test_the_fields_read_are_exactly_these_and_the_authority_writes_them(monkeypatch, tmp_path):
    assert combined.BINANCE_SNAPSHOT_FIELDS == {"record_type", "configured", "asset", "margin_balance", "as_of"}
    _binance_file(tmp_path, margin=1234.5, monkeypatch=monkeypatch)
    written = json.loads((tmp_path / combined.BINANCE_SNAPSHOT_REL).read_text())
    assert combined.BINANCE_SNAPSHOT_FIELDS <= set(written)
    assert combined.read_binance(tmp_path, now=NOW) == (1234.5, NOW, "ok")


@pytest.mark.parametrize("kwargs, status", [
    ({"as_of": "2026-10-07T05:00:00Z"}, "stale"),
    ({"asset": "BUSD"}, "not_usdt"),
    ({"configured": False}, "not_configured"),
])
def test_an_unusable_binance_file_says_why(monkeypatch, tmp_path, kwargs, status):
    _binance_file(tmp_path, monkeypatch=monkeypatch, **kwargs)
    assert combined.read_binance(tmp_path, now=NOW)[2] == status


def test_an_absent_binance_file_is_absent(tmp_path):
    assert combined.read_binance(tmp_path, now=NOW) == (None, None, "absent")


def test_the_first_complete_total_initializes_the_peak_with_no_verdict(monkeypatch, tmp_path):
    _binance_file(tmp_path, margin=1000.0, monkeypatch=monkeypatch)
    block = combined.combine(_toss_view(), usd_krw_rate=1375, root=tmp_path, now=NOW, state_dir=tmp_path / "h")
    assert set(block) == combined.COMBINED_KEYS
    assert block["combined_total_krw"] == TOSS_TOTAL + 1000 * 1375
    assert block["drawdown_state"] == combined.STATE_INITIALIZED
    assert combined.alert(block, told=combined.STATE_CLEAR, as_of=NOW) is None


def test_a_rise_moves_the_peak_and_a_fall_past_the_limit_breaches(monkeypatch, tmp_path):
    hdir = tmp_path / "h"
    _binance_file(tmp_path, margin=1000.0, monkeypatch=monkeypatch)
    combined.combine(_toss_view(), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    up = combined.combine(_toss_view(total=20_000_000), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    assert up["peak_total_krw"] == 21_000_000 and up["drawdown_state"] == combined.STATE_CLEAR
    down = combined.combine(_toss_view(total=15_000_000), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    assert down["peak_total_krw"] == 21_000_000          # a fall never lowers the peak
    assert down["drawdown_pct"] == round((16_000_000 / 21_000_000 - 1) * 100, 2)
    assert down["drawdown_state"] == combined.STATE_BREACHED
    state, text = combined.alert(down, told=combined.STATE_CLEAR, as_of=NOW)
    assert state == combined.STATE_BREACHED and "-23.8%" in text and "--reset-peak" in text
    assert combined.alert(down, told=combined.STATE_BREACHED, as_of=NOW) is None   # told once


def test_a_partial_or_stale_pair_is_unknown_and_leaves_the_peak_alone(monkeypatch, tmp_path):
    hdir = tmp_path / "h"
    _binance_file(tmp_path, margin=1000.0, monkeypatch=monkeypatch)
    combined.combine(_toss_view(), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    peak_before = json.loads((hdir / combined.PEAK_FILENAME).read_text())
    partial = combined.combine(_toss_view(partial=True, total=1), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    assert partial["drawdown_state"] == combined.STATE_UNKNOWN and partial["combined_total_krw"] is None
    late = "2026-10-07T12:00:00Z"                           # the Binance file is now 3 h old
    stale = combined.combine(_toss_view(total=1), usd_krw_rate=1000, root=tmp_path, now=late, state_dir=hdir)
    assert stale["drawdown_state"] == combined.STATE_UNKNOWN and stale["crypto_status"] == "stale"
    no_rate = combined.combine(_toss_view(), usd_krw_rate=None, root=tmp_path, now=NOW, state_dir=hdir)
    assert no_rate["drawdown_state"] == combined.STATE_UNKNOWN and no_rate["crypto_status"] == "no_rate"
    assert json.loads((hdir / combined.PEAK_FILENAME).read_text()) == peak_before
    assert combined.alert(stale, told=combined.STATE_BREACHED, as_of=NOW) is None  # unknown never tells


def test_recovery_is_told_once_after_a_breach(monkeypatch, tmp_path):
    hdir = tmp_path / "h"
    _binance_file(tmp_path, margin=0.0, monkeypatch=monkeypatch)
    combined.combine(_toss_view(total=10_000_000), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    back = combined.combine(_toss_view(total=9_000_000), usd_krw_rate=1000, root=tmp_path, now=NOW, state_dir=hdir)
    state, text = combined.alert(back, told=combined.STATE_BREACHED, as_of=NOW)
    assert state == combined.STATE_CLEAR and "한도 안으로" in text


def test_refresh_stores_the_combined_block_without_the_rate(monkeypatch, gate_open, tmp_path):
    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    _toss(monkeypatch)
    result = store.refresh(now=NOW, root=tmp_path)
    assert result["status"] == "holdings snapshot: refreshed; combined initialized"
    raw = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    block = json.loads(raw)["combined"]
    assert block["crypto_futures_krw"] == 100 * 1375 and block["crypto_status"] == "ok"
    assert block["combined_total_krw"] == TOSS_TOTAL + 100 * 1375
    assert _revealed(raw) == []
    text, data = store.load_holdings_view(now=NOW, root=tmp_path)
    assert "--- all accounts ---" in text and "peak initialized" in text
    assert data["combined"]["drawdown_state"] == combined.STATE_INITIALIZED


def _breach_setup(monkeypatch, tmp_path):
    """A stored peak far above what the fake Toss account and a zero Binance balance now add up to."""
    hdir = store.state_dir(tmp_path)
    hdir.mkdir(parents=True, exist_ok=True)
    (hdir / combined.PEAK_FILENAME).write_text(json.dumps({"peak_total_krw": TOSS_TOTAL * 2, "peak_at": NOW}))
    _binance_file(tmp_path, margin=0.0, monkeypatch=monkeypatch)
    _toss(monkeypatch)
    return hdir


def test_the_fire_tells_a_breach_once_and_marks_it_after_delivery(monkeypatch, gate_open, tmp_path):
    hdir = _breach_setup(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")
    monkeypatch.setattr(operator_mod, "notify_operator", lambda channel, text, **_kw: sent.append(text))
    assert _fire(tmp_path).endswith("drawdown breached told")
    assert len(sent) == 1 and "-50.0%" in sent[0]
    assert combined.read_told(hdir) == combined.STATE_BREACHED
    assert not _fire(tmp_path).endswith("told") and len(sent) == 1        # not told twice


def test_an_undelivered_alert_is_offered_again(monkeypatch, gate_open, tmp_path):
    hdir = _breach_setup(monkeypatch, tmp_path)
    from runtime.mvp_runtime.errors import OperatorBlocked

    def _down(channel, text, **_kw):
        raise OperatorBlocked("CHANNEL_DOWN", "down")

    monkeypatch.setattr(operator_mod, "select_operator_channel", lambda **_kw: "channel")
    monkeypatch.setattr(operator_mod, "notify_operator", _down)
    assert _fire(tmp_path).endswith("drawdown breached not sent:CHANNEL_DOWN")
    assert combined.read_told(hdir) == combined.STATE_CLEAR                 # the mark did not move


def test_reset_peak_forgets_the_peak_and_the_told_state(monkeypatch, tmp_path, capsys):
    from scripts import holdings_board

    hdir = store.state_dir(tmp_path)
    hdir.mkdir(parents=True)
    (hdir / combined.PEAK_FILENAME).write_text("{}")
    combined.write_told(hdir, combined.STATE_BREACHED, now=NOW)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", lambda *a, **k: None)
    assert holdings_board.main(["--reset-peak"]) == 0
    assert not (hdir / combined.PEAK_FILENAME).exists()
    assert combined.read_told(hdir) == combined.STATE_CLEAR
    assert "holdings_peak.json" in capsys.readouterr().out


def test_reset_peak_refuses_a_foreign_root_run(monkeypatch, tmp_path):
    from runtime.mvp_runtime.errors import MvpRuntimeError
    from scripts import holdings_board

    def _refuse(*_a, **_k):
        raise MvpRuntimeError("STATE_FOREIGN_ROOT_RUN", "run it in the container")

    monkeypatch.setattr(holdings_board, "assert_not_foreign_root_run", _refuse)
    monkeypatch.setattr(store, "_repo_root", lambda: tmp_path)
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    assert holdings_board.main(["--reset-peak"]) == EXIT_BLOCKED


# --- Q9: allocation against the target, display only (TOTAL_ASSET_ALLOCATION §11.1) ----------------

from runtime.mvp_runtime.holdings import binance_wallet  # noqa: E402

FUTURES_OK = {"crypto_status": "ok", "crypto_futures_krw": 0.0, "crypto_wallet_status": "not_configured",
              "crypto_classes_krw": None}


def test_the_targets_and_bands_are_the_decided_ones():
    assert sum(allocation.TARGET_PCT.values()) == 100.0
    assert set(allocation.TARGET_PCT) == set(allocation.CLASSES)
    assert {g: allocation.band_pp(sum(allocation.TARGET_PCT[c] for c in m))
            for g, m in allocation.BAND_GROUPS.items()} == {
        "equity": 5.0, "bonds": 5.0, "gold": 3.25, "coin": 0.625, "engine_margin": 0.0}
    assert allocation.CASH not in {c for m in allocation.BAND_GROUPS.values() for c in m}  # §6: no cash band


def test_the_table_ships_empty_and_maps_only_known_classes():
    assert dict(allocation.CLASSIFICATION) == {}                       # Thomas 2026-10-07
    assert set(allocation.WALLET_CLASS_MAP) == set(binance_wallet.CLASSES) - {binance_wallet.CLASS_OTHER}
    assert allocation.WALLET_CLASS_MAP[binance_wallet.CLASS_STABLE] == allocation.CASH   # Thomas 2026-10-07


def test_an_unclassified_symbol_gives_a_partial_total_and_no_verdict(monkeypatch, gate_open):
    snapshot = _snapshot(monkeypatch)
    block = allocation.allocate(snapshot, board.aggregate_view(snapshot), FUTURES_OK)
    assert set(block) == allocation.ALLOCATION_KEYS
    assert block["complete"] is False and block["bands"] is None and block["outside_band"] is None
    assert block["unclassified_count"] == 2
    assert block["unclassified_krw"] == 7_200_000 + 1785 * 1375
    assert block["classes"]["cash"]["krw"] == 5_000_000 + 100 * 1375
    assert all(row["weight_pct"] is None for row in block["classes"].values())
    assert "no band verdict" in block["notes"][0]
    assert any("gate off" in note for note in block["notes"])


def _classified(monkeypatch):
    monkeypatch.setattr(allocation, "CLASSIFICATION",
                        {"005930": allocation.DOMESTIC_EQUITY, "AAPL": allocation.GLOBAL_EQUITY})


def test_a_complete_total_judges_each_band(monkeypatch, gate_open):
    _classified(monkeypatch)
    snapshot = _snapshot(monkeypatch)
    wallet = {"crypto_status": "ok", "crypto_futures_krw": 1_000_000.0, "crypto_wallet_status": "ok",
              "crypto_classes_krw": {"btc": 300_000.0, "eth": 100_000.0, "stable": 200_000.0, "other": 0.0}}
    block = allocation.allocate(snapshot, board.aggregate_view(snapshot), wallet)
    total = TOSS_TOTAL + 1_000_000 + 600_000
    assert block["complete"] is True and block["total_krw"] == total
    assert block["classes"]["coin"]["krw"] == 400_000                  # BTC and ETH
    assert block["classes"]["cash"]["krw"] == 5_000_000 + 100 * 1375 + 200_000
    assert block["cash_stablecoin_krw"] == 200_000
    assert round(sum(r["weight_pct"] for r in block["classes"].values())) == 100
    bands = block["bands"]
    assert set(bands["equity"]) == allocation.BAND_ROW_KEYS
    assert bands["engine_margin"]["outside"] is True                   # target 0: any margin is drift (Q10)
    assert any("target is 0% by decision" in note for note in block["notes"])
    assert bands["bonds"]["outside"] is True and bands["bonds"]["drift_pp"] == -37.0
    assert block["outside_band"] == [g for g, r in bands.items() if r["outside"]]


@pytest.mark.parametrize("change, gap", [
    ({"crypto_status": "stale"}, "binance futures stale"),
    ({"crypto_wallet_status": "failed (BINANCE_REJECTED)"}, "binance wallet failed"),
    ({"crypto_wallet_status": "ok", "crypto_classes_krw": {"other": 5.0}}, "1 unclassified"),
])
def test_any_missing_part_withholds_the_verdict(monkeypatch, gate_open, change, gap):
    _classified(monkeypatch)
    snapshot = _snapshot(monkeypatch)
    block = allocation.allocate(snapshot, board.aggregate_view(snapshot), {**FUTURES_OK, **change})
    assert block["complete"] is False and block["outside_band"] is None
    assert gap in block["notes"][0]


def test_a_usd_holding_without_a_rate_withholds_the_verdict(monkeypatch, gate_open):
    import dataclasses

    _classified(monkeypatch)
    snapshot = dataclasses.replace(_snapshot(monkeypatch), usd_krw_rate=None)
    block = allocation.allocate(snapshot, board.aggregate_view(snapshot), FUTURES_OK)
    assert block["complete"] is False and "rate unread" in block["notes"][0]


def test_the_codes_to_classify_are_named_on_the_terminal_board_only(monkeypatch, gate_open, tmp_path):
    snapshot = _snapshot(monkeypatch)
    assert "005930, AAPL (add to holdings/allocation.py CLASSIFICATION)" in board.render_full(snapshot)
    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    store.refresh(now=NOW, root=tmp_path)
    text, data = store.load_holdings_view(now=NOW, root=tmp_path)
    assert "allocation vs target (display only)" in text and "codes on the full board" in text
    assert _revealed(text + json.dumps(data, ensure_ascii=False)) == []


def test_a_band_outside_never_alerts(monkeypatch, gate_open, tmp_path):
    """Q2 and Q4 were dropped as limits (2026-10-07): an outside band is a board fact, not a message."""
    _classified(monkeypatch)
    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    _toss(monkeypatch)
    result = store.refresh(now=NOW, root=tmp_path)
    block = json.loads(store.snapshot_path(tmp_path).read_text(encoding="utf-8"))["allocation"]
    assert block["complete"] is True and block["outside_band"]
    assert result["alert"] is None


def test_a_stored_block_renders_in_the_decided_order(monkeypatch, gate_open, tmp_path):
    """The store sorts JSON keys; the board must not show the classes alphabetically (2026-10-07)."""
    _classified(monkeypatch)
    _binance_file(tmp_path, margin=100.0, monkeypatch=monkeypatch)
    _toss(monkeypatch)
    store.refresh(now=NOW, root=tmp_path)
    text, _ = store.load_holdings_view(now=NOW, root=tmp_path)
    rows = [line.split(":")[0].strip() for line in text.split("allocation vs target")[1].splitlines()[1:8]]
    assert rows == [board._CLASS_LABELS[name] for name in allocation.CLASSES]
    bands_line = next(line for line in text.splitlines() if line.startswith("bands"))
    positions = [bands_line.index(f" {group} ") for group in allocation.BAND_GROUPS]
    assert positions == sorted(positions)
