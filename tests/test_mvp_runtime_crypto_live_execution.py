"""LP4 order-adapter tests (increments 1 + 2a).

Under test: the intent→request mapping (reduceOnly carried faithfully, the conditional bracket
types, the venue's closePosition/quantity/reduceOnly exclusivity and client-order-id charset);
the reconcile comparison and its status vocabulary; the submit+reconcile orchestration
(reconcile-first, never blind-retry; guard-approval belt-and-suspenders); the Safety-Flag gate
selection (inert by default, env alone fails closed); the **signed transport** (signature and
timestamp present, secret never in the URL, -2013 is NOT_FOUND rather than an error, any other
rejection raises so it becomes UNRECONCILABLE, a transport failure never leaks the signed URL);
and the actual-fill facts LP5 needs. **Nothing here opens a socket** — ``urlopen`` is intercepted.
"""

from __future__ import annotations

import io
import json

import pytest

from runtime.mvp_runtime.crypto import live_execution as lx
from runtime.mvp_runtime.crypto.live_order import (
    build_live_order_intent,
    enrich_order_identity,
)
from runtime.mvp_runtime.crypto.live_pnl import (
    LIVE_TRADING_ENV,
    LIVE_TRADING_FLAGS,
    LIVE_TRADING_PROVIDER_ID,
    REAL_LIVE_TRADING,
)
from runtime.mvp_runtime.crypto.live_promotion import RECONCILED, build_canary_order_record
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolError
from runtime.mvp_runtime.safety_gate import Authorization

NOW = "2026-07-25T00:00:00Z"
APPROVED = {"approved": True}

_LIVE_AUTH = Authorization(
    flags=LIVE_TRADING_FLAGS, provider_id=LIVE_TRADING_PROVIDER_ID, activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z", evidence_ref=".runtime_governance_state/evidence.md",
)


def _intent(*, reduce_only=False, **kw):
    plan = {"direction": kw.pop("direction", "LONG")}
    intent = build_live_order_intent(
        plan, symbol=kw.pop("symbol", "BTCUSDT"), quantity=kw.pop("quantity", 0.001),
        notional_usdt=kw.pop("notional_usdt", 55.0), now=NOW, reduce_only=reduce_only,
        close_reason="stop_loss" if reduce_only else None,
    )
    return enrich_order_identity(intent)


class _FakeAdapter:
    """A scripted transport: returns a fixed venue order (or raises) so the orchestration's
    reconcile branches can be exercised without a venue."""
    tool_id, tool_version = "fake", "0"

    def __init__(self, *, venue_order=None, submit_raises=None, fetch_raises=None):
        self.venue_order, self.submit_raises, self.fetch_raises = venue_order, submit_raises, fetch_raises
        self.submitted = None

    def submit(self, order_request, *, timeout_seconds=10):
        self.submitted = dict(order_request)
        if self.submit_raises:
            raise ToolError(self.submit_raises, "submit boom")
        return {"accepted": True}

    def fetch_order(self, symbol, client_order_id, *, timeout_seconds=10):
        if self.fetch_raises:
            raise ToolError(self.fetch_raises, "fetch boom")
        return self.venue_order


def _venue_order_from(intent, **overrides):
    order = {
        "symbol": intent["symbol"], "side": intent["side"], "status": "FILLED",
        "executedQty": intent["quantity"], "reduceOnly": intent["reduce_only"],
        "orderId": "venue-123",
    }
    order.update(overrides)
    return order


# --- build_order_request -----------------------------------------------------

def test_request_maps_the_intent_and_is_market():
    req = lx.build_order_request(_intent())
    assert req["side"] == "BUY" and req["type"] == "MARKET"
    assert req["reduceOnly"] is False and req["newClientOrderId"].startswith("TAI_")


def test_request_carries_reduce_only_from_the_intent():
    """The structural close boundary: a reduceOnly intent produces a reduceOnly request."""
    req = lx.build_order_request(_intent(reduce_only=True))
    assert req["reduceOnly"] is True and req["side"] == "SELL"   # closing a LONG sells


@pytest.mark.parametrize("mutate, why", [
    (lambda i: {**i, "symbol": ""}, "no symbol"),
    (lambda i: {**i, "side": "LONG"}, "bad side"),
    (lambda i: {**i, "quantity": 0}, "zero qty"),
    (lambda i: {**i, "client_order_id": ""}, "no client_order_id"),
    (lambda i: {**i, "order_type_exchange": "LIMIT"}, "not market"),
])
def test_malformed_intent_is_refused(mutate, why):
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(mutate(_intent()))
    assert exc.value.reason_code == lx.MALFORMED_INTENT, why


# --- reconcile_order ---------------------------------------------------------

def test_reconcile_matching_order_is_reconciled():
    intent = _intent()
    status, problems = lx.reconcile_order(intent, _venue_order_from(intent))
    assert status == RECONCILED and problems == []


def test_reconcile_missing_order_is_not_found():
    status, problems = lx.reconcile_order(_intent(), None)
    assert status == lx.NOT_FOUND and problems


@pytest.mark.parametrize("override, needle", [
    ({"symbol": "ETHUSDT"}, "symbol"),
    ({"side": "SELL"}, "side"),
    ({"status": "NEW"}, "status"),
    ({"executedQty": 0.002}, "executedQty"),
    ({"reduceOnly": True}, "reduceOnly"),
])
def test_reconcile_names_every_divergence(override, needle):
    intent = _intent()
    status, problems = lx.reconcile_order(intent, _venue_order_from(intent, **override))
    assert status == lx.MISMATCH and any(needle in p for p in problems)


# --- submit_and_reconcile orchestration --------------------------------------

def test_dry_run_reconciles_end_to_end():
    res = lx.submit_and_reconcile(_intent(), adapter=lx.DryRunOrderAdapter(), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == RECONCILED and res["mismatches"] == []
    assert res["submit_error"] is None and res["exchange_order_id"].startswith("dryrun-")


def test_refuses_an_unapproved_guard_verdict():
    for verdict in ({"approved": False}, {}, None):
        with pytest.raises(ToolError) as exc:
            lx.submit_and_reconcile(_intent(), adapter=lx.DryRunOrderAdapter(), guard_verdict=verdict, now=NOW)
        assert exc.value.reason_code == lx.GUARD_NOT_APPROVED


def test_a_lost_submit_reconciles_to_not_found():
    intent = _intent()
    res = lx.submit_and_reconcile(intent, adapter=_FakeAdapter(venue_order=None), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.NOT_FOUND


def test_an_ambiguous_submit_that_landed_still_reconciles():
    """The reconcile-first rule: a submit that raised (timeout) but actually landed is learned
    from the venue read, not assumed lost."""
    intent = _intent()
    adapter = _FakeAdapter(venue_order=_venue_order_from(intent), submit_raises="TOOL_TRANSPORT")
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["submit_error"] == "TOOL_TRANSPORT" and res["reconcile_status"] == RECONCILED


def test_a_failed_reconcile_query_is_unreconcilable():
    res = lx.submit_and_reconcile(
        _intent(), adapter=_FakeAdapter(fetch_raises="TOOL_TRANSPORT"), guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.UNRECONCILABLE and res["exchange_order_id"] is None


def test_a_mismatched_fill_is_surfaced():
    intent = _intent()
    adapter = _FakeAdapter(venue_order=_venue_order_from(intent, executedQty=0.002))
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.MISMATCH


def test_a_reconciled_result_makes_a_clean_canary_record():
    """The whole point: a RECONCILED result with no mismatch is a clean canary; anything else is not."""
    intent = _intent()
    res = lx.submit_and_reconcile(intent, adapter=lx.DryRunOrderAdapter(), guard_verdict=APPROVED, now=NOW)
    record = build_canary_order_record(
        reconcile_status=res["reconcile_status"], symbol=res["symbol"],
        exchange_order_id=res["exchange_order_id"], client_order_id=res["client_order_id"],
        mismatches=res["mismatches"], notional_usdt=55.0, now=NOW)
    assert record["clean"] is True


# --- the gate: inert by default, real path not implemented -------------------

def test_selection_is_inert_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(LIVE_TRADING_ENV, raising=False)
    assert isinstance(lx.select_order_adapter(now=NOW, root=tmp_path), lx.DryRunOrderAdapter)


def test_env_alone_now_opens_the_gate(tmp_path, monkeypatch):
    """Thomas, 2026-07-28: the env opt-in IS the gate for live trading — no grant record.

    This test used to assert the exact opposite (`ACTIVATION_MISSING`), and the inversion is
    the change, not a slip. `tmp_path` holds no activations directory, so nothing but the
    variable is authorizing this."""
    monkeypatch.setenv(LIVE_TRADING_ENV, REAL_LIVE_TRADING)
    adapter = lx.select_order_adapter(now=NOW, root=tmp_path)
    assert isinstance(adapter, lx.BinanceFuturesOrderAdapter)
    assert adapter.network_egress is True


def test_real_adapter_refuses_without_authorization():
    with pytest.raises(SafetyGateBlocked):
        lx.BinanceFuturesOrderAdapter(authorization=None).submit({"newClientOrderId": "x"})


# --- the chokepoint ---------------------------------------------------------------------
#
# LP5.3 step 3 replaced `test_no_autonomous_entry_point_reaches_the_live_order_path`.
#
# That test pinned "no scheduled or operator-triggered run can reach the order path at all",
# and it was the right invariant for as long as the cycle had no live leg. Cycle routing is
# the deliberate decision to stop asserting it, and this is the reviewable half of that
# decision.
#
# What replaces it is NARROWER AND STILL LOAD-BEARING: the path from an autonomous entry point
# to a live order goes through exactly one module. That keeps "which code can start a live
# order" a question with a single answer — the property the old test was really protecting —
# while allowing the one caller the design record sequenced. It also fails loudly on the two
# ways this could rot: a second module growing an order path, and an entry point reaching
# around the chokepoint.
#
# The gate itself is tested where it belongs (`test_mvp_runtime_crypto_live_route.py`): without
# `MVP_LIVE_TRADING=real` the routing reads nothing and sends nothing.

# The modules that can reach a venue with an order. ``live_pnl`` is deliberately absent: the
# cycle reads live OUTCOMES so the risk guard can see live losses, and reading a result is not
# reaching the order path.
LIVE_ORDER_MODULES = frozenset({"live_execution", "live_position", "live_leg"})

# The autonomous entry points, and what each may import from the live stack. Only the cycle may
# reach it, and only through the chokepoint.
ENTRY_POINTS = {
    "runtime/mvp_runtime/crypto/cycle.py": frozenset({"live_route", "live_pnl"}),
    "runtime/mvp_runtime/scheduler.py": frozenset(),
    "runtime/mvp_runtime/pipeline.py": frozenset(),
    "runtime/mvp_runtime/operator.py": frozenset(),
    # Added on main while this branch was open, and carried across deliberately: the domain
    # console reads the crypto stack from a chat verb. It is reached only through
    # `operator.py`, which is already here — but this scan is per-FILE, so the import that
    # matters would sit in the console and never show up in the operator. A chat verb is the
    # surface where "just one more argument" is most tempting, and it is one token away.
    "runtime/mvp_runtime/domain_console.py": frozenset(),
}

# The same list under the name `main` gave it, because `tests/test_mvp_runtime_domain_console.py`
# imports it to assert the console is covered. Derived rather than restated: two hand-kept
# copies is precisely the drift the comment on that assertion warns about.
AUTONOMOUS_ENTRY_POINTS = list(ENTRY_POINTS)

CHOKEPOINT = "runtime/mvp_runtime/crypto/live_route.py"


def _imported_modules(path) -> set[str]:
    """Every module this file imports, by last path component.

    Deliberately the import graph rather than a substring search over the source. The first
    version of this check was textual and failed on its own subject's *comments* — a file that
    documents why it must not reach the order path would have been reported as reaching it,
    which trains the next author to delete the explanation rather than keep the property.
    """
    import ast

    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = (node.module or "").rsplit(".", 1)[-1]
            if base:
                modules.add(base)
            # ``from . import live_leg`` names the module in the alias list, not the module field.
            modules.update(alias.name.rsplit(".", 1)[-1] for alias in node.names)
    return modules


def test_the_cycle_reaches_the_live_order_path_through_exactly_one_module():
    """No autonomous entry point imports the order path directly; the cycle reaches it only
    via ``live_route``, and the other entry points not at all."""
    from pathlib import Path

    from runtime.mvp_runtime.paths import repo_root

    offenders = []
    for rel, allowed in ENTRY_POINTS.items():
        path = Path(repo_root()) / rel
        if not path.is_file():
            continue
        imported = _imported_modules(path)
        direct = sorted(imported & LIVE_ORDER_MODULES)
        if direct:
            offenders.append(f"{rel} imports the order path directly: {direct}")
        if "live_route" in imported and "live_route" not in allowed:
            offenders.append(f"{rel} may not reach the live stack at all")
    assert offenders == [], (
        "the live order path must be reachable from exactly one module. Adding a second "
        f"caller is the same size of decision as adding the first: {offenders}"
    )


def test_the_chokepoint_is_the_only_runtime_module_that_imports_the_executing_leg():
    """The other half: ``live_route`` is a chokepoint only while nothing else runs the leg.

    Scoped to ``runtime/`` — ``scripts/place_canary_order.py`` is the deliberate operator door
    and reaches ``live_execution`` on purpose, one canary at a time."""
    from pathlib import Path

    from runtime.mvp_runtime.paths import repo_root

    root = Path(repo_root())
    importers = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "runtime").rglob("*.py")
        if path.name not in {"live_leg.py", "live_route.py"}
        and "live_leg" in _imported_modules(path)
    )
    assert importers == [], (
        f"{importers} import the executing leg directly, bypassing the chokepoint that makes "
        "'which code can start a live order' answerable"
    )


def test_real_adapter_refuses_without_credentials(monkeypatch):
    """Authorized by the gate but no order key configured => refuses by NAME, never a value,
    and never opens a socket."""
    monkeypatch.delenv(lx.ORDER_API_KEY_ENV, raising=False)
    monkeypatch.delenv(lx.ORDER_API_SECRET_ENV, raising=False)
    adapter = lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH)
    with pytest.raises(ToolError) as exc:
        adapter.submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.NO_ORDER_API_KEY
    assert lx.ORDER_API_KEY_ENV in exc.value.reason        # the name is reportable
    assert "secret" not in exc.value.reason.lower().replace("api_secret", "")


def test_real_adapter_rejects_a_disallowed_host():
    with pytest.raises(ToolError) as exc:
        lx.BinanceFuturesOrderAdapter(base_url="https://evil.example.com", authorization=_LIVE_AUTH)
    assert exc.value.reason_code == "ORDER_HOST_NOT_ALLOWED"


# --- increment 2a: conditional order types (the LP5 bracket) ------------------

def _cond_intent(order_type, **kw):
    intent = dict(_intent())
    intent["order_type_exchange"] = order_type
    intent.setdefault("stop_price", 59000.0)
    intent.update(kw)
    return intent


@pytest.mark.parametrize("order_type", ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
def test_conditional_order_carries_the_stop_price(order_type):
    req = lx.build_order_request(_cond_intent(order_type, reduce_only=True))
    assert req["type"] == order_type and req["stopPrice"] == 59000.0
    assert req["reduceOnly"] is True and req["quantity"] > 0


@pytest.mark.parametrize("order_type", ["STOP_MARKET", "TAKE_PROFIT_MARKET"])
def test_conditional_order_without_a_stop_price_is_refused(order_type):
    intent = _cond_intent(order_type)
    intent["stop_price"] = 0
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(intent)
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_working_type_is_validated_and_passed_through():
    req = lx.build_order_request(_cond_intent("STOP_MARKET", working_type="MARK_PRICE"))
    assert req["workingType"] == "MARK_PRICE"
    with pytest.raises(ToolError):
        lx.build_order_request(_cond_intent("STOP_MARKET", working_type="LAST_PRICE"))


def test_close_position_excludes_quantity_and_reduce_only():
    """Venue rule: closePosition=true is mutually exclusive with BOTH quantity and reduceOnly."""
    req = lx.build_order_request(_cond_intent("STOP_MARKET", close_position=True, reduce_only=True))
    assert req["closePosition"] == "true"
    assert "quantity" not in req and "reduceOnly" not in req


def test_close_position_is_refused_on_a_market_order():
    """closePosition is a Close-All conditional behaviour; on MARKET the venue would reject it."""
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "close_position": True})
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_unsupported_order_type_is_refused():
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "order_type_exchange": "LIMIT"})
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_client_order_id_charset_is_enforced():
    """The venue's own charset rule — a bad character would be rejected at the venue."""
    with pytest.raises(ToolError) as exc:
        lx.build_order_request({**_intent(), "client_order_id": "bad id!"})
    assert exc.value.reason_code == lx.MALFORMED_INTENT
    # 37 chars exceeds the venue's 36 limit.
    with pytest.raises(ToolError):
        lx.build_order_request({**_intent(), "client_order_id": "A" * 37})


# --- increment 2a: the signed transport (urlopen intercepted, no network) -----

class _FakeResponse:
    def __init__(self, payload):
        self._raw = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _http_error(code, msg):
    import urllib.error
    body = json.dumps({"code": code, "msg": msg}).encode("utf-8")
    return urllib.error.HTTPError("https://redacted", 400, msg, {}, io.BytesIO(body))


@pytest.fixture
def order_creds(monkeypatch):
    monkeypatch.setenv(lx.ORDER_API_KEY_ENV, "test-key")
    monkeypatch.setenv(lx.ORDER_API_SECRET_ENV, "test-secret")


def _adapter():
    return lx.BinanceFuturesOrderAdapter(authorization=_LIVE_AUTH)


def test_submit_signs_the_request_and_never_sends_the_secret(monkeypatch, order_creds):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        seen["headers"] = dict(request.headers)
        return _FakeResponse({"orderId": 99, "status": "NEW"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    body = _adapter().submit(lx.build_order_request(_intent()))
    assert body["orderId"] == 99
    assert seen["method"] == "POST" and "/fapi/v1/order?" in seen["url"]
    # Signed, timestamped, and the key rides in the header — never the secret anywhere.
    assert "signature=" in seen["url"] and "timestamp=" in seen["url"]
    assert seen["headers"].get("X-mbx-apikey") == "test-key"
    assert "test-secret" not in seen["url"]


def test_fetch_order_queries_by_client_order_id(monkeypatch, order_creds):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["method"] = request.method
        return _FakeResponse({"orderId": 7, "status": "FILLED", "executedQty": "0.001"})

    monkeypatch.setattr(lx.urllib.request, "urlopen", fake_urlopen)
    order = _adapter().fetch_order("BTCUSDT", "TAI_X")
    assert order["orderId"] == 7 and seen["method"] == "GET"
    assert "origClientOrderId=TAI_X" in seen["url"]


def test_order_does_not_exist_is_none_not_an_error(monkeypatch, order_creds):
    """Venue code -2013 is a truthful NOT_FOUND, so it must be None — not UNRECONCILABLE."""
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-2013, "Order does not exist.")))
    assert _adapter().fetch_order("BTCUSDT", "TAI_X") is None


def test_any_other_query_rejection_raises_so_it_becomes_unreconcilable(monkeypatch, order_creds):
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-1021, "Timestamp out of recvWindow.")))
    with pytest.raises(ToolError) as exc:
        _adapter().fetch_order("BTCUSDT", "TAI_X")
    assert exc.value.reason_code == lx.ORDER_REJECTED


def test_a_rejected_submit_raises_with_the_venue_reason(monkeypatch, order_creds):
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-2019, "Margin is insufficient.")))
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_REJECTED and "-2019" in exc.value.reason


def test_a_duplicate_client_order_id_is_reported_as_already_landed(monkeypatch, order_creds):
    """Idempotency working as designed: the original landed, so reconcile decides the outcome."""
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-4116, "clientOrderId is duplicated")))
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert "already landed" in exc.value.reason


def test_a_transport_failure_never_leaks_the_signed_url(monkeypatch, order_creds):
    import urllib.error

    def boom(*a, **k):
        raise urllib.error.URLError("connection reset to https://fapi.binance.com/...signature=SECRETSIG")

    monkeypatch.setattr(lx.urllib.request, "urlopen", boom)
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_TRANSPORT
    assert "signature" not in exc.value.reason and "SECRETSIG" not in exc.value.reason


def test_an_unparseable_body_is_refused(monkeypatch, order_creds):
    class _Bad(_FakeResponse):
        def __init__(self):
            self._raw = b"not json"

    monkeypatch.setattr(lx.urllib.request, "urlopen", lambda *a, **k: _Bad())
    with pytest.raises(ToolError) as exc:
        _adapter().submit(lx.build_order_request(_intent()))
    assert exc.value.reason_code == lx.ORDER_MALFORMED_RESULT


# --- increment 2a: the fill facts LP5 needs ----------------------------------

def test_reconcile_result_carries_the_actual_fill():
    intent = _intent()
    adapter = _FakeAdapter(venue_order={**_venue_order_from(intent),
                                        "avgPrice": "60000.5", "cumQuote": "60.0005"})
    res = lx.submit_and_reconcile(intent, adapter=adapter, guard_verdict=APPROVED, now=NOW)
    assert res["fill"]["avg_price"] == 60000.5 and res["fill"]["cum_quote"] == 60.0005
    assert res["order_type"] == "MARKET"


def test_an_unreconciled_order_reports_an_unknown_fill_not_a_free_trade():
    """None, never 0.0 — a missing fill price must not read as a costless trade."""
    res = lx.submit_and_reconcile(
        _intent(), adapter=_FakeAdapter(fetch_raises="TOOL_TRANSPORT"),
        guard_verdict=APPROVED, now=NOW)
    assert res["reconcile_status"] == lx.UNRECONCILABLE
    assert res["fill"] == {"avg_price": None, "executed_qty": None, "cum_quote": None}


def test_the_readiness_board_constant_agrees_with_the_import_graph():
    """B1: the readiness board exists so an answer cannot drift from what the code enforces, and
    its prose still claimed "LP5 is unbuilt" for a day after LP5 shipped. Status now lives in a
    computed row backed by this constant — and the constant is pinned to the same import graph
    the chokepoint test above checks, so the two cannot disagree.

    The chain is followed rather than asserted: an entry point reaches the chokepoint, and the
    chokepoint reaches the executing leg. Checking only the first link would report WIRED for a
    ``live_route`` that had been gutted; checking only the second would report WIRED for a
    chokepoint nothing calls."""
    from pathlib import Path

    from runtime.mvp_runtime.crypto.live_readiness import AUTONOMOUS_ROUTING_WIRED
    from runtime.mvp_runtime.paths import repo_root

    root = Path(repo_root())
    reaches_chokepoint = any(
        "live_route" in _imported_modules(root / rel)
        for rel in ENTRY_POINTS
        if (root / rel).is_file()
    )
    chokepoint = root / CHOKEPOINT
    chokepoint_sends = chokepoint.is_file() and "live_leg" in _imported_modules(chokepoint)
    wired = reaches_chokepoint and chokepoint_sends

    assert AUTONOMOUS_ROUTING_WIRED == wired, (
        "live_readiness.AUTONOMOUS_ROUTING_WIRED disagrees with the real import graph; "
        "the board would report a build state that is not true"
    )


# --- the LIMIT take-profit leg (2026-07-28) --------------------------------------

def _limit_intent(**over):
    return {
        "status": "ORDER_INTENT_CREATED", "symbol": "BTCUSDT", "side": "SELL",
        "order_type_exchange": "LIMIT", "price": 62000.0, "time_in_force": "GTC",
        "quantity": 0.001, "reduce_only": True, "close_position": False,
        "client_order_id": "TAI_BTCUSDT_TP_abc", "connectivity_test": False, **over,
    }


def test_a_limit_carries_price_time_in_force_quantity_and_reduce_only():
    req = lx.build_order_request(_limit_intent())
    assert req["type"] == "LIMIT"
    assert req["price"] == 62000.0
    assert req["timeInForce"] == "GTC"
    assert req["quantity"] == 0.001
    assert req["reduceOnly"] is True
    # Not a conditional order: no trigger, no workingType.
    assert "stopPrice" not in req and "workingType" not in req


def test_a_limit_without_a_price_is_refused():
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(_limit_intent(price=None))
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_a_limit_without_a_time_in_force_is_refused():
    """The venue makes it mandatory, and defaulting it would be this module deciding how long
    real money rests at a price."""
    for bad in (None, "FOREVER"):
        with pytest.raises(ToolError) as exc:
            lx.build_order_request(_limit_intent(time_in_force=bad))
        assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_close_position_is_refused_on_a_limit():
    """closePosition is documented for the two _MARKET conditional types only. That constraint
    is why the target leg has to be sized from the actual fill."""
    with pytest.raises(ToolError) as exc:
        lx.build_order_request(_limit_intent(close_position=True))
    assert exc.value.reason_code == lx.MALFORMED_INTENT


def test_the_dry_run_adapter_rests_a_limit_rather_than_filling_it():
    """A LIMIT waits for the market to reach its price. Echoing FILLED would let a dry run
    confirm a target that has not been reached."""
    adapter = lx.DryRunOrderAdapter()
    req = lx.build_order_request(_limit_intent())
    adapter.submit(req)
    assert adapter.fetch_order("BTCUSDT", req["newClientOrderId"])["status"] == "NEW"


# --- open_orders: seeing what the runtime did not place -------------------------

class _Resp(io.BytesIO):
    """A urlopen context manager over a canned JSON body."""
    def __enter__(self):
        return self
    def __exit__(self, *exc):
        return False


def _ok(payload):
    return lambda *a, **k: _Resp(json.dumps(payload).encode("utf-8"))


def test_open_orders_returns_what_is_resting(monkeypatch, order_creds):
    """The blind spot this closes: `fetch_order` can only answer about an id the runtime already
    holds, and the venue's conditional-order cap is counted over everything — including orders
    this runtime never placed."""
    monkeypatch.setattr(lx.urllib.request, "urlopen", _ok([
        {"clientOrderId": "OTHER_1", "type": "STOP_MARKET", "status": "NEW"},
        {"clientOrderId": "TAI_ETHUSDT_TP_x", "type": "LIMIT", "status": "NEW"},
    ]))
    orders = _adapter().open_orders("ETHUSDT")
    assert [o["clientOrderId"] for o in orders] == ["OTHER_1", "TAI_ETHUSDT_TP_x"]


def test_open_orders_is_empty_when_nothing_rests(monkeypatch, order_creds):
    monkeypatch.setattr(lx.urllib.request, "urlopen", _ok([]))
    assert _adapter().open_orders("ETHUSDT") == []


def test_a_refused_open_orders_query_raises_rather_than_reading_as_empty(monkeypatch, order_creds):
    """"Nothing is resting" and "I could not find out" must never arrive as the same answer to a
    question about live exposure."""
    monkeypatch.setattr(lx.urllib.request, "urlopen",
                        lambda *a, **k: (_ for _ in ()).throw(_http_error(-1021, "Timestamp out of recvWindow.")))
    with pytest.raises(ToolError) as exc:
        _adapter().open_orders("ETHUSDT")
    assert exc.value.reason_code == lx.ORDER_REJECTED and "-1021" in exc.value.reason


def test_open_orders_places_and_cancels_nothing(monkeypatch, order_creds):
    """Read-only, asserted on the HTTP verb rather than trusted from the name."""
    seen = {}

    def _capture(request, *a, **k):
        seen["method"] = request.method
        seen["url"] = request.full_url
        return _Resp(b"[]")

    monkeypatch.setattr(lx.urllib.request, "urlopen", _capture)
    _adapter().open_orders("ETHUSDT")
    assert seen["method"] == "GET"
    assert lx.OPEN_ORDERS_PATH in seen["url"]


def test_the_dry_run_adapter_reports_its_own_resting_orders():
    """Parity: the inert adapter answers the same question, so the orchestration above it can be
    tested without a venue."""
    adapter = lx.DryRunOrderAdapter()
    adapter.submit(lx.build_order_request(_intent()))
    assert len(adapter.open_orders()) == 1
    assert adapter.open_orders("NOTHINGUSDT") == []
    adapter.cancel_order("BTCUSDT", lx.build_order_request(_intent())["newClientOrderId"])
    assert adapter.open_orders() == []
