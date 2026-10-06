"""The holdings lane's KIS feed and its two renders — P1 of MULTI_ASSET_EXPANSION_V0.1.md.

What is pinned here, and why each one is a decision rather than a detail:

1. **Its own gate.** ``MVP_KIS_ACCOUNT=kis`` and nothing else opens it; ``MVP_ACCOUNT_FEED`` (the live
   money path's account) cannot. And nothing under ``runtime/`` outside this package imports it —
   D4's "no door reads it", as an import-graph property.
2. **Read-only.** The feed reaches the token endpoint and two balance inquiries, and has no method
   whose name could place, cancel or trade.
3. **The external-send boundary** (appendix A, Thomas 2026-10-02). The aggregate view's key set is
   exact, and no symbol, name or per-symbol number reaches it.
4. **Secrets and the token.** No key, secret, token or full account number in any render, warning
   or error; one token per process; a token rejection is never retried.

No test opens a socket: ``urlopen`` is replaced in the module namespace.
"""

from __future__ import annotations

import ast
import io
import json
import pathlib
import urllib.error
import urllib.parse

import pytest

from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.holdings import board, kis_account
from runtime.mvp_runtime.holdings.kis_account import (
    DOMESTIC_BALANCE_PATH,
    KIS_ACCOUNT_ENV,
    KIS_ACCOUNT_NO_ENV,
    KIS_ACCOUNT_ON,
    KIS_ACCOUNT_PRODUCT_CODE_ENV,
    KIS_APP_KEY_ENV,
    KIS_APP_SECRET_ENV,
    KIS_PROVIDER,
    KIS_SERVER_ENV,
    MAX_PAGES,
    OVERSEAS_BALANCE_PATH,
    TOKEN_PATH,
    KisHoldingsFeed,
    NoHoldingsFeed,
    read_holdings,
    select_holdings_feed,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]

APP_KEY = "PSappkey-do-not-leak-0001"
APP_SECRET = "secret-do-not-leak-0002"
ACCOUNT_NO = "50123456"
PRODUCT_CODE = "01"
TOKEN = "eyJ0b2tlbi1kby1ub3QtbGVhaw"

# Distinctive strings, so "does it leak" is a substring check that cannot pass by accident.
DOMESTIC_SYMBOL = "005930"
DOMESTIC_NAME = "SAMSUNGELEC-TESTNAME"
OVERSEAS_SYMBOL = "QQQTESTSYM"


def _domestic_page(*, rows=None, summary=None, fk="", nk=""):
    return {
        "rt_cd": "0",
        "msg_cd": "KIOK0000",
        "output1": rows if rows is not None else [{
            "pdno": DOMESTIC_SYMBOL, "prdt_name": DOMESTIC_NAME, "hldg_qty": "10",
            "pchs_avg_pric": "70000", "prpr": "43219", "evlu_amt": "712340",
            "evlu_pfls_amt": "12340",
        }],
        "output2": summary if summary is not None else [{
            "scts_evlu_amt": "712340", "dnca_tot_amt": "1000000",
            "evlu_pfls_smtl_amt": "12340", "tot_evlu_amt": "1712340",
        }],
        "ctx_area_fk100": fk,
        "ctx_area_nk100": nk,
    }


def _overseas_page():
    return {
        "rt_cd": "0",
        "msg_cd": "KIOK0000",
        "output1": [{
            "pdno": OVERSEAS_SYMBOL, "cblc_qty13": "3", "ovrs_now_pric1": "512.34",
            "frcr_evlu_amt2": "2100000", "evlu_pfls_amt2": "50000", "buy_crcy_cd": "USD",
        }],
        "output2": [{"crcy_cd": "USD"}],
        "output3": {"evlu_amt_smtl_amt": "2100000", "tot_evlu_pfls_amt": "50000"},
    }


class _Response(io.BytesIO):
    def __init__(self, body: dict, tr_cont: str = ""):
        super().__init__(json.dumps(body).encode("utf-8"))
        self.headers = {"tr_cont": tr_cont}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Venue:
    """A fake KIS: routes by path, records every request it is sent."""

    def __init__(self, *, domestic=None, overseas=None, token=None):
        self.sent: list = []
        self.domestic = domestic or [(_domestic_page(), "")]
        self.overseas = overseas
        self.token = token if token is not None else {"access_token": TOKEN, "expires_in": 86400}
        self._domestic_index = 0

    def __call__(self, request, timeout=None):
        self.sent.append(request)
        path = urllib.parse.urlparse(request.full_url).path
        if path == TOKEN_PATH:
            if isinstance(self.token, Exception):
                raise self.token
            return _Response(self.token)
        if path == DOMESTIC_BALANCE_PATH:
            body, tr_cont = self.domestic[min(self._domestic_index, len(self.domestic) - 1)]
            self._domestic_index += 1
            return _Response(body, tr_cont)
        if path == OVERSEAS_BALANCE_PATH:
            if isinstance(self.overseas, Exception):
                raise self.overseas
            return _Response(self.overseas or _overseas_page())
        raise AssertionError(f"request to an unexpected path: {path}")

    def paths(self) -> list[str]:
        return [urllib.parse.urlparse(r.full_url).path for r in self.sent]


@pytest.fixture
def creds(monkeypatch):
    monkeypatch.setenv(KIS_APP_KEY_ENV, APP_KEY)
    monkeypatch.setenv(KIS_APP_SECRET_ENV, APP_SECRET)
    monkeypatch.setenv(KIS_ACCOUNT_NO_ENV, ACCOUNT_NO)
    monkeypatch.setenv(KIS_ACCOUNT_PRODUCT_CODE_ENV, PRODUCT_CODE)
    monkeypatch.delenv(KIS_SERVER_ENV, raising=False)


@pytest.fixture
def gate_open(monkeypatch, creds):
    monkeypatch.setenv(KIS_ACCOUNT_ENV, KIS_ACCOUNT_ON)


def _venue(monkeypatch, **kwargs) -> _Venue:
    venue = _Venue(**kwargs)
    monkeypatch.setattr(kis_account.urllib.request, "urlopen", venue)
    return venue


def _leaks(text: str) -> list[str]:
    return [s for s in (APP_KEY, APP_SECRET, TOKEN, ACCOUNT_NO) if s in text]


# --- the gate ---------------------------------------------------------------------------

def test_without_the_env_var_the_board_is_inert(monkeypatch, creds):
    monkeypatch.delenv(KIS_ACCOUNT_ENV, raising=False)
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)
    assert read_holdings() == (None, "NOT_CONFIGURED")


def test_the_env_var_alone_opens_the_gate(monkeypatch, gate_open):
    feed = select_holdings_feed()
    assert isinstance(feed, KisHoldingsFeed)
    assert feed.provider_id == KIS_PROVIDER


def test_a_different_value_does_not_open_the_gate(monkeypatch, creds):
    monkeypatch.setenv(KIS_ACCOUNT_ENV, "real")
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)


def test_the_live_account_feeds_opt_in_does_not_open_this_one(monkeypatch, creds):
    """The money path's account selector and this one are different gates (module docstring)."""
    monkeypatch.delenv(KIS_ACCOUNT_ENV, raising=False)
    monkeypatch.setenv("MVP_ACCOUNT_FEED", "binance_futures_account")
    assert isinstance(select_holdings_feed(), NoHoldingsFeed)


def test_a_directly_constructed_feed_cannot_open_a_socket(monkeypatch, gate_open):
    venue = _venue(monkeypatch)
    with pytest.raises(SafetyGateBlocked):
        KisHoldingsFeed().holdings_snapshot(timeout_seconds=1)
    assert venue.sent == []


def test_withdrawing_the_env_var_stops_egress(monkeypatch, gate_open):
    venue = _venue(monkeypatch)
    feed = select_holdings_feed()
    monkeypatch.delenv(KIS_ACCOUNT_ENV)
    with pytest.raises(SafetyGateBlocked) as excinfo:
        feed.holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "ENV_OPT_IN_WITHDRAWN"
    assert venue.sent == []


# --- the server and the hosts -----------------------------------------------------------

def test_the_default_server_is_the_real_one(monkeypatch, gate_open):
    venue = _venue(monkeypatch)
    snapshot, _ = read_holdings()
    assert snapshot.server == "real"
    assert {urllib.parse.urlparse(r.full_url).netloc for r in venue.sent} == {
        "openapi.koreainvestment.com:9443"
    }


def test_demo_reads_the_demo_host_with_demo_transaction_ids(monkeypatch, gate_open):
    monkeypatch.setenv(KIS_SERVER_ENV, "demo")
    venue = _venue(monkeypatch)
    snapshot, _ = read_holdings()
    assert snapshot.server == "demo"
    assert {urllib.parse.urlparse(r.full_url).netloc for r in venue.sent} == {
        "openapivts.koreainvestment.com:29443"
    }
    tr_ids = {r.get_header("Tr_id") for r in venue.sent if r.get_header("Tr_id")}
    assert tr_ids == {"VTTC8434R", "VTRP6504R"}


def test_an_unknown_server_refuses_before_any_socket(monkeypatch, gate_open):
    monkeypatch.setenv(KIS_SERVER_ENV, "staging")
    venue = _venue(monkeypatch)
    assert read_holdings() == (None, "KIS_SERVER_UNKNOWN")
    assert venue.sent == []


# --- read-only --------------------------------------------------------------------------

def test_the_feed_reaches_only_the_token_and_the_two_balance_inquiries(monkeypatch, gate_open):
    venue = _venue(monkeypatch)
    read_holdings()
    assert set(venue.paths()) <= {TOKEN_PATH, DOMESTIC_BALANCE_PATH, OVERSEAS_BALANCE_PATH}
    assert [r.get_method() for r in venue.sent if urllib.parse.urlparse(r.full_url).path != TOKEN_PATH] \
        == ["GET", "GET"]


def test_the_feed_has_no_order_capability():
    """Blunt on purpose, like the crypto account feed's guard: no public name that could act."""
    forbidden = ("order", "submit", "cancel", "trade", "buy", "sell", "place")
    for cls in (KisHoldingsFeed, NoHoldingsFeed):
        public = [name for name in dir(cls) if not name.startswith("_")]
        assert not [n for n in public if any(word in n.lower() for word in forbidden)], cls


# The two dispatch sites, exactly: the scheduler's `holdings_refresh` fire and the console's
# `/holdings` verb. Both import the lane inside a function — the core's dispatch shape
# (`test_mvp_runtime_domain_isolation.py`) — and both reach only `holdings.store`.
HOLDINGS_DISPATCH_SITES = {"runtime/mvp_runtime/scheduler.py", "runtime/mvp_runtime/domain_console.py"}


def _holdings_imports(path: pathlib.Path) -> tuple[bool, bool]:
    """(imports holdings at module level, imports it anywhere)."""
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

    module_level = any(names_holdings(node) for node in tree.body)
    anywhere = any(names_holdings(node) for node in ast.walk(tree))
    return module_level, anywhere


def test_the_runtime_reaches_the_holdings_lane_only_through_its_two_dispatch_sites():
    """D4: "no door reads it" — no judgement, no promotion, no routing, nothing in `crypto/`. The
    scheduler fire and the console verb are the only importers, and only function-locally."""
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
    assert not [rel for rel in importers if rel.startswith("runtime/mvp_runtime/crypto/")]


# --- credentials, token, errors ---------------------------------------------------------

def test_missing_credentials_name_the_variables_and_never_a_value(monkeypatch, gate_open):
    monkeypatch.delenv(KIS_APP_SECRET_ENV)
    venue = _venue(monkeypatch)
    snapshot, reason = read_holdings()
    assert (snapshot, reason) == (None, "NO_API_KEY")
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert KIS_APP_SECRET_ENV in str(excinfo.value)
    assert _leaks(str(excinfo.value)) == []
    assert TOKEN_PATH not in venue.paths()


def test_one_token_serves_the_whole_read(monkeypatch, gate_open):
    venue = _venue(monkeypatch)
    feed = select_holdings_feed()
    feed.holdings_snapshot(timeout_seconds=1)
    feed.holdings_snapshot(timeout_seconds=1)
    assert venue.paths().count(TOKEN_PATH) == 1


def test_a_token_rejection_is_one_attempt_and_says_nothing_sent(monkeypatch, gate_open):
    body = io.BytesIO(json.dumps({"error_description": APP_KEY}).encode())
    venue = _venue(monkeypatch, token=urllib.error.HTTPError(
        "https://openapi.koreainvestment.com:9443/oauth2/tokenP", 403, "Forbidden", {}, body))
    snapshot, reason = read_holdings()
    assert (snapshot, reason) == (None, "KIS_REJECTED")
    assert venue.paths() == [TOKEN_PATH]
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert _leaks(str(excinfo.value)) == []
    assert "koreainvestment" not in str(excinfo.value)


def test_a_transport_failure_says_nothing_about_the_request(monkeypatch, gate_open):
    _venue(monkeypatch, token=urllib.error.URLError("refused by openapi.koreainvestment.com"))
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "TOOL_TRANSPORT"
    assert "koreainvestment" not in str(excinfo.value)


def test_a_kis_refusal_carries_its_message_code(monkeypatch, gate_open):
    _venue(monkeypatch, domestic=[({"rt_cd": "1", "msg_cd": "EGW00123", "msg1": ACCOUNT_NO}, "")])
    with pytest.raises(ToolError) as excinfo:
        select_holdings_feed().holdings_snapshot(timeout_seconds=1)
    assert excinfo.value.reason_code == "KIS_REJECTED"
    assert "EGW00123" in str(excinfo.value)
    assert _leaks(str(excinfo.value)) == []


# --- parsing ----------------------------------------------------------------------------

def test_a_read_maps_onto_kis_own_totals(monkeypatch, gate_open):
    _venue(monkeypatch)
    snapshot, reason = read_holdings()
    assert reason is None
    assert snapshot.account == "****3456-01"
    assert snapshot.domestic.holdings_value_krw == 712340
    assert snapshot.domestic.cash_krw == 1_000_000
    assert snapshot.overseas.holdings_value_krw == 2_100_000
    assert {h.symbol for h in snapshot.holdings} == {DOMESTIC_SYMBOL, OVERSEAS_SYMBOL}
    assert snapshot.warnings == ()


def test_a_missing_field_is_absent_and_warned_never_zero(monkeypatch, gate_open):
    _venue(monkeypatch, domestic=[(_domestic_page(summary=[{"scts_evlu_amt": "712340"}]), "")])
    snapshot, _ = read_holdings()
    assert snapshot.domestic.cash_krw is None
    assert any("dnca_tot_amt" in w for w in snapshot.warnings)


def test_losing_the_overseas_side_narrows_the_answer(monkeypatch, gate_open):
    _venue(monkeypatch, overseas=urllib.error.URLError("down"))
    snapshot, reason = read_holdings()
    assert reason is None
    assert snapshot.overseas is None
    assert snapshot.domestic.holdings_value_krw == 712340
    assert any("overseas balance unavailable (TOOL_TRANSPORT)" in w for w in snapshot.warnings)


def test_pagination_follows_the_continuation_and_is_capped(monkeypatch, gate_open):
    venue = _venue(monkeypatch, domestic=[(_domestic_page(fk="FK1", nk="NK1"), "M")])
    snapshot, _ = read_holdings()
    domestic = [r for r in venue.sent if urllib.parse.urlparse(r.full_url).path == DOMESTIC_BALANCE_PATH]
    assert len(domestic) == MAX_PAGES
    assert domestic[0].get_header("Tr_cont") == ""
    assert domestic[1].get_header("Tr_cont") == "N"
    assert "CTX_AREA_FK100=FK1" in domestic[1].full_url
    assert any(f"stopped after {MAX_PAGES} pages" in w for w in snapshot.warnings)


# --- the two renders --------------------------------------------------------------------

def _snapshot(monkeypatch):
    _venue(monkeypatch)
    snapshot, _ = read_holdings()
    return snapshot


def test_the_aggregate_view_has_exactly_the_decided_keys(monkeypatch, gate_open):
    view = board.aggregate_view(_snapshot(monkeypatch))
    assert set(view) == board.AGGREGATE_KEYS
    assert view["known_total_krw"] == 712340 + 2_100_000 + 1_000_000
    assert view["partial"] is True
    assert round(sum(view["weights"].values())) == 100


def test_nothing_that_reveals_a_price_reaches_the_aggregate(monkeypatch, gate_open):
    snapshot = _snapshot(monkeypatch)
    outward = board.render_aggregate(snapshot) + json.dumps(board.aggregate_view(snapshot))
    for leaked in (DOMESTIC_SYMBOL, DOMESTIC_NAME, OVERSEAS_SYMBOL, "43219", "512.34", "USD"):
        assert leaked not in outward, leaked
    assert _leaks(outward) == []


def test_the_full_board_shows_the_positions(monkeypatch, gate_open):
    text = board.render_full(_snapshot(monkeypatch))
    assert DOMESTIC_SYMBOL in text and OVERSEAS_SYMBOL in text
    assert _leaks(text) == []


def test_an_unavailable_board_says_why():
    assert "NOT_CONFIGURED" in board.render_aggregate(None)
    assert "KIS_REJECTED" in board.render_full(None, reason_code="KIS_REJECTED")


# --- the script -------------------------------------------------------------------------

def test_the_script_defaults_to_the_aggregate(monkeypatch, gate_open, capsys):
    from scripts import holdings_board

    _venue(monkeypatch)
    assert holdings_board.main([]) == 0
    out = capsys.readouterr().out
    assert "known total" in out
    assert DOMESTIC_SYMBOL not in out


def test_the_script_treats_an_unconfigured_board_as_normal(monkeypatch, creds, capsys):
    from scripts import holdings_board

    monkeypatch.delenv(KIS_ACCOUNT_ENV, raising=False)
    assert holdings_board.main(["--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {"available": False, "reason_code": "NOT_CONFIGURED"}


def test_the_script_blocks_on_a_failed_read(monkeypatch, gate_open, capsys):
    from runtime.mvp_runtime.cli_common import EXIT_BLOCKED
    from scripts import holdings_board

    _venue(monkeypatch, token=urllib.error.URLError("down"))
    assert holdings_board.main([]) == EXIT_BLOCKED
    assert "TOOL_TRANSPORT" in capsys.readouterr().out


# --- P1-b: the snapshot store, the maintenance fire, the doors ---------------------------

from runtime.mvp_runtime import domain_console, read_bridge, schedule_delegation, scheduler  # noqa: E402
from runtime.mvp_runtime.holdings import store  # noqa: E402

NOW = "2026-10-02T09:00:00Z"
LATER = "2026-10-02T13:30:00Z"


@pytest.fixture(autouse=True)
def _fresh_feed_cache(monkeypatch):
    monkeypatch.setattr(store, "_cached_feed", None)


def test_the_snapshot_holds_the_aggregate_and_nothing_else(monkeypatch, gate_open, tmp_path):
    _venue(monkeypatch)
    assert store.refresh_snapshot(now=NOW, root=tmp_path) == "holdings snapshot: refreshed"
    raw = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    assert set(json.loads(raw)) == board.AGGREGATE_KEYS | {"record_type", "as_of", "written_at"}
    for leaked in (DOMESTIC_SYMBOL, DOMESTIC_NAME, OVERSEAS_SYMBOL, "43219", "512.34"):
        assert leaked not in raw, leaked
    assert _leaks(raw) == []


def test_fires_share_one_token_while_the_gate_stays_open(monkeypatch, gate_open, tmp_path):
    venue = _venue(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    store.refresh_snapshot(now=LATER, root=tmp_path)
    assert venue.paths().count(TOKEN_PATH) == 1


def test_closing_the_gate_drops_the_cached_feed(monkeypatch, gate_open, tmp_path):
    venue = _venue(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    monkeypatch.delenv(KIS_ACCOUNT_ENV)
    assert store.refresh_snapshot(now=LATER, root=tmp_path) == "holdings snapshot: no KIS account configured"
    assert store._cached_feed is None
    monkeypatch.setenv(KIS_ACCOUNT_ENV, KIS_ACCOUNT_ON)
    store.refresh_snapshot(now=LATER, root=tmp_path)
    assert venue.paths().count(TOKEN_PATH) == 2


def test_a_failed_read_keeps_the_last_good_snapshot(monkeypatch, gate_open, tmp_path):
    _venue(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    good = store.snapshot_path(tmp_path).read_text(encoding="utf-8")
    monkeypatch.setattr(store, "_cached_feed", None)
    _venue(monkeypatch, token=urllib.error.URLError("down"))
    status = store.refresh_snapshot(now=LATER, root=tmp_path)
    assert status == "holdings snapshot: degraded (TOOL_TRANSPORT); kept the previous one"
    assert store.snapshot_path(tmp_path).read_text(encoding="utf-8") == good
    assert json.loads(store.refresh_mark_path(tmp_path).read_text())["attempted_at"] == LATER


def test_the_board_says_when_there_is_no_snapshot(tmp_path):
    text, data = store.load_holdings_view(now=NOW, root=tmp_path)
    assert "no snapshot yet" in text
    assert data == {"available": False, "reason_code": store.HOLDINGS_SNAPSHOT_MISSING, "last_attempt": None}


def test_an_old_snapshot_shows_its_number_and_says_it_is_stale(monkeypatch, gate_open, tmp_path):
    _venue(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    as_of = json.loads(store.snapshot_path(tmp_path).read_text())["as_of"]
    text, data = store.load_holdings_view(now="2099-01-01T00:00:00Z", root=tmp_path)
    assert data["stale"] is True and "STALE" in text
    assert "3,812,340 KRW" in text
    fresh_text, fresh = store.load_holdings_view(now=as_of, root=tmp_path)
    assert fresh["stale"] is False and "STALE" not in fresh_text


def test_an_unreadable_snapshot_raises_rather_than_rendering_empty(tmp_path):
    path = store.snapshot_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as excinfo:
        store.load_holdings_view(now=NOW, root=tmp_path)
    assert excinfo.value.reason_code == store.HOLDINGS_SNAPSHOT_UNREADABLE


def _holdings_schedule() -> scheduler.Schedule:
    return scheduler.Schedule(
        schedule_id="schedule_holdings_test", kind=scheduler.KIND_HOLDINGS,
        request="", interval_seconds=3600, enabled=True, created_by="test",
        created_at=NOW, next_run_at=NOW,
    )


def test_the_maintenance_fire_writes_the_snapshot(monkeypatch, gate_open, tmp_path):
    _venue(monkeypatch)
    status = scheduler._execute(
        _holdings_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )
    assert status == "holdings snapshot: refreshed"
    assert store.snapshot_path(tmp_path).exists()


def test_an_unconfigured_fire_is_a_status_line_not_a_failure(monkeypatch, creds, tmp_path):
    monkeypatch.delenv(KIS_ACCOUNT_ENV, raising=False)
    status = scheduler._execute(
        _holdings_schedule(), now=NOW, ledger=None, working_memory=None,
        programization=None, repo_root=tmp_path, executor=lambda **_: {},
    )
    assert status == "holdings snapshot: no KIS account configured"


def test_the_kind_is_maintenance_and_not_the_assistants_to_change():
    assert scheduler.KIND_HOLDINGS in scheduler.MAINTENANCE_KINDS
    assert scheduler.KIND_HOLDINGS not in scheduler.RISK_KINDS
    assert scheduler.KIND_HOLDINGS in schedule_delegation.FINANCIAL_KINDS


def test_the_operator_verb_renders_the_snapshot(monkeypatch, gate_open, tmp_path):
    _venue(monkeypatch)
    store.refresh_snapshot(now=NOW, root=tmp_path)
    command = domain_console.parse_domain_command("/holdings")
    assert command == ("HOLDINGS", None)
    outcome = domain_console.apply_domain_command(command, operator_id="op", now=NOW, repo_root=tmp_path)
    assert outcome["action"] == "HOLDINGS_STATUS"
    assert outcome["data"]["available"] is True
    assert DOMESTIC_SYMBOL not in outcome["reply"]


def test_the_assistant_read_is_dormant_until_the_policy_lists_it():
    assert "holdings_status" in read_bridge._READS
    assert "holdings_status" in read_bridge.POLICY_GATED_READS
