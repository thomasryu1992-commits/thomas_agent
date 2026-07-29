"""Step 6 tests — the registered live-trading budget (``live_trading_budget.v0.1``).

Under test: a budget is self-hashed and schema-valid; every money-safety refusal the caps
encode is enforced at build time (a cap above the 200 USDT ceiling is refused not clamped, a
zero/negative cap is refused, the window must open before it closes); a read is verified (a
tampered or unparseable budget raises); and registering one grants nothing.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_budget as lb
from runtime.mvp_runtime.crypto.live_pnl import state_dir
from runtime.mvp_runtime.errors import ToolError

NOW = "2026-07-25T00:00:00Z"
FROM = "2026-07-25T00:00:00Z"
UNTIL = "2026-08-25T00:00:00Z"

_CAPS = dict(
    max_order_notional_usdt=60.0,
    absolute_max_notional_usdt=200.0,
    max_daily_order_count=2,
    max_open_notional_usdt=120.0,
    daily_loss_limit_usdt=20.0,
    min_clean_canary_orders=3,
)


def _build(**overrides):
    kw = dict(caps=dict(_CAPS), symbol_allowlist=["BTCUSDT"], valid_from=FROM, valid_until=UNTIL,
              registered_by="thomas", registered_at=NOW)
    caps_over = overrides.pop("caps", None)
    if caps_over is not None:
        kw["caps"] = {**dict(_CAPS), **caps_over}
    kw.update(overrides)
    return lb.build_live_trading_budget_record(**kw)


# --- build: shape + self-hash ------------------------------------------------

def test_build_produces_a_self_hashed_schema_valid_record():
    rec = _build()
    assert rec["schema_version"] == "live_trading_budget.v0.1"
    assert rec["budget_id"].startswith("budget_")
    assert rec["record_sha256"].startswith("sha256:")
    assert rec["venue"] == "binance_futures"
    assert rec["caps"] == _CAPS


def test_build_uppercases_and_trims_symbols():
    rec = _build(symbol_allowlist=[" btcusdt ", "ethusdt"])
    assert rec["symbol_allowlist"] == ["BTCUSDT", "ETHUSDT"]


def test_budget_id_and_hash_change_when_a_cap_changes():
    """Changing a limit is a new record, never a silent edit."""
    a = _build()
    b = _build(caps={"max_order_notional_usdt": 55.0})
    assert a["budget_id"] != b["budget_id"]
    assert a["record_sha256"] != b["record_sha256"]


# --- build: fail-closed refusals ---------------------------------------------

def test_per_order_cap_above_the_ceiling_is_refused_not_clamped():
    with pytest.raises(ToolError) as exc:
        _build(caps={"max_order_notional_usdt": 250.0})
    assert exc.value.reason_code == lb.BUDGET_INVALID


def test_absolute_ceiling_above_the_hard_ceiling_is_refused():
    with pytest.raises(ToolError) as exc:
        _build(caps={"absolute_max_notional_usdt": 250.0})
    assert exc.value.reason_code == lb.BUDGET_INVALID


@pytest.mark.parametrize("cap", list(_CAPS))
def test_zero_cap_is_refused(cap):
    with pytest.raises(ToolError) as exc:
        _build(caps={cap: 0})
    assert exc.value.reason_code == lb.BUDGET_INVALID


def test_negative_cap_is_refused():
    with pytest.raises(ToolError):
        _build(caps={"daily_loss_limit_usdt": -5.0})


def test_non_integer_count_is_refused():
    with pytest.raises(ToolError):
        _build(caps={"max_daily_order_count": 2.5})


def test_empty_symbol_allowlist_is_refused():
    with pytest.raises(ToolError) as exc:
        _build(symbol_allowlist=["   "])
    assert exc.value.reason_code == lb.BUDGET_INVALID


def test_window_must_open_before_it_closes():
    with pytest.raises(ToolError):
        _build(valid_from=UNTIL, valid_until=FROM)


def test_unsupported_venue_is_refused():
    with pytest.raises(ToolError):
        _build(venue="kraken_futures")


def test_missing_cap_key_is_refused():
    caps = {k: v for k, v in _CAPS.items() if k != "daily_loss_limit_usdt"}
    with pytest.raises(ToolError):
        lb.build_live_trading_budget_record(
            caps=caps, symbol_allowlist=["BTCUSDT"], valid_from=FROM, valid_until=UNTIL,
            registered_by="t", registered_at=NOW)


# --- write + verified read ---------------------------------------------------

def test_roundtrip(tmp_path):
    rec = _build()
    path = lb.write_registered_budget(rec, root=tmp_path)
    assert path == state_dir(tmp_path) / lb.LIVE_BUDGET_FILENAME
    assert lb.read_registered_budget(tmp_path)["budget_id"] == rec["budget_id"]


def test_no_registered_budget_reads_as_none(tmp_path):
    assert lb.read_registered_budget(tmp_path) is None


def test_tampered_budget_refuses(tmp_path):
    lb.write_registered_budget(_build(), root=tmp_path)
    path = lb.budget_path(tmp_path)
    data = json.loads(path.read_text())
    data["caps"]["max_order_notional_usdt"] = 199.0     # edited after hashing
    path.write_text(json.dumps(data))
    with pytest.raises(ToolError) as exc:
        lb.read_registered_budget(tmp_path)
    assert exc.value.reason_code == lb.BUDGET_TAMPERED


def test_unparseable_budget_refuses(tmp_path):
    target = state_dir(tmp_path)
    target.mkdir(parents=True, exist_ok=True)
    (target / lb.LIVE_BUDGET_FILENAME).write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        lb.read_registered_budget(tmp_path)
    assert exc.value.reason_code == lb.BUDGET_UNREADABLE


# --- budget_status -----------------------------------------------------------

def test_status_not_registered(tmp_path):
    st = lb.budget_status(tmp_path, now=NOW)
    assert st["registered"] is False and st["valid"] is False and st["error"] is None


def test_status_valid_within_window(tmp_path):
    lb.write_registered_budget(_build(), root=tmp_path)
    st = lb.budget_status(tmp_path, now="2026-08-01T00:00:00Z")
    assert st["registered"] is True and st["valid"] is True
    assert st["caps"] == _CAPS and st["record_sha256"].startswith("sha256:")


def test_status_outside_window_is_invalid(tmp_path):
    lb.write_registered_budget(_build(), root=tmp_path)
    st = lb.budget_status(tmp_path, now="2026-09-01T00:00:00Z")
    assert st["registered"] is True and st["valid"] is False
    assert st["error"] == "OUTSIDE_VALIDITY_WINDOW"


def test_status_fails_closed_on_a_tampered_budget(tmp_path):
    lb.write_registered_budget(_build(), root=tmp_path)
    path = lb.budget_path(tmp_path)
    data = json.loads(path.read_text())
    data["caps"]["daily_loss_limit_usdt"] = 999.0
    path.write_text(json.dumps(data))
    st = lb.budget_status(tmp_path, now=NOW)
    assert st["registered"] is True and st["valid"] is False and st["error"] == lb.BUDGET_TAMPERED


# --- loader ------------------------------------------------------------------

def test_limits_from_budget_maps_caps_and_leaves_confirmation_to_the_operator():
    lim = lb.limits_from_budget(_build())
    assert lim.max_order_notional_usdt == 60.0 and lim.max_daily_order_count == 2
    assert lim.absolute_max_notional_usdt == 200.0 and lim.min_clean_canary_orders == 3
    # confirmation + manual_kill are operator env, never budget-registered.
    assert lim.confirmation == "" and lim.manual_kill_switch is False


# --- registration grants nothing ---------------------------------------------

def test_registering_a_budget_does_not_enable_trading(tmp_path, monkeypatch):
    """A registered budget is a record, not a permission. Since LP4 the order path exists, so the
    thing that must stay untouched is the *authority*: registering a budget does not throw the
    live-trading switch, so the order adapter stays inert and nothing can be sent.

    The switch became `MVP_LIVE_TRADING=real` alone on 2026-07-28, which makes this test matter
    MORE, not less: with the grant gone there is one thing left that separates a configured
    machine from a trading one, and a record written by a script must not be it. The delenv is
    explicit rather than inherited so that is visible."""
    from runtime.mvp_runtime.crypto.live_execution import DryRunOrderAdapter, select_order_adapter
    monkeypatch.delenv("MVP_LIVE_TRADING", raising=False)
    lb.write_registered_budget(_build(), root=tmp_path)
    adapter = select_order_adapter(now=NOW, root=tmp_path)
    assert isinstance(adapter, DryRunOrderAdapter)          # switch off => inert
    assert adapter.network_egress is False                  # cannot reach a venue
