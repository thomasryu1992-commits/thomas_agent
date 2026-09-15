"""Step 6 tests — the registered live-trading budget (``live_trading_budget.v0.1``).

Under test: a budget is self-hashed and schema-valid; every money-safety refusal the caps
encode is enforced at build time (a cap above the hard ceiling is refused not clamped, a
zero/negative cap is refused, the window must open before it closes); a read is verified (a
tampered or unparseable budget raises); and registering one grants nothing.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.crypto import live_budget as lb
from runtime.mvp_runtime.crypto.live_pnl import state_dir
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root

NOW = "2026-07-25T00:00:00Z"
FROM = "2026-07-25T00:00:00Z"
UNTIL = "2026-08-25T00:00:00Z"

_CAPS = dict(
    max_order_notional_usdt=60.0,
    absolute_max_notional_usdt=200.0,
    max_daily_order_count=2,
    max_open_notional_usdt=120.0,
    daily_loss_limit_usdt=20.0,
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
        _build(caps={"absolute_max_notional_usdt": lb.HARD_CEILING_USDT + 1})
    assert exc.value.reason_code == lb.BUDGET_INVALID


def test_hard_ceiling_is_the_operator_approved_number():
    """The single number a registered budget may never declare past, pinned literally.

    The refusal test above is written against the constant, so it stays green whatever the
    constant says. This one is the tripwire: moving the ceiling is a money decision, and it
    must show up as a deliberate edit here rather than a one-character constant bump nobody
    reviews. 200.0 at first bring-up (Thomas, 2026-07-23) → 500.0 (Thomas, 2026-08-08)."""
    assert lb.HARD_CEILING_USDT == 500.0


def test_schema_and_code_agree_on_the_hard_ceiling():
    """Two authorities pin this number and they must not drift.

    The builder refuses first so the error names the offending number; the schema is what
    makes a hand-edited record invalid. If one were raised and the other not, the lower of
    the two would silently become the real ceiling — and which one that is would depend on
    whether a record arrived through the builder or through the file."""
    schema = json.loads(
        (repo_root() / "schemas" / lb.LIVE_BUDGET_SCHEMA_FILE).read_text(encoding="utf-8")
    )
    ceiling = schema["properties"]["caps"]["properties"]["absolute_max_notional_usdt"]["maximum"]
    assert ceiling == lb.HARD_CEILING_USDT


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


# --- the retired canary cap (2026-09-15, PR1r) ----------------------------------
#
# Thomas removed the clean-canary promotion gate with the canary door, and the budget stopped
# requiring, writing and reading `caps.min_clean_canary_orders`. Records registered before carry
# it inside their self-hash (production's reads 4), under a closed `caps` object — so the schema
# keeps the property and only `required` let go of it. These pin that, so a later "cleanup" that
# deletes the property is caught here rather than by every entry refusing on a schema error.

def _legacy_record(**caps_extra):
    """The pre-PR1r builder's shape, hashed raw — nothing today can build it."""
    from runtime.read_only_kernel import integrity

    body = {
        "schema_version": "live_trading_budget.v0.1", "budget_id": "budget_0123456789abcdef0123",
        "venue": "binance_futures", "symbol_allowlist": ["BTCUSDT"],
        "caps": {**_CAPS, "min_clean_canary_orders": 4, **caps_extra},
        "valid_from": FROM, "valid_until": UNTIL, "registered_by": "thomas", "registered_at": NOW,
    }
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def _write_raw(root, record):
    path = lb.budget_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


def test_the_schema_still_declares_the_retired_cap_and_no_longer_requires_it():
    schema = json.loads(
        (repo_root() / "schemas" / lb.LIVE_BUDGET_SCHEMA_FILE).read_text(encoding="utf-8")
    )
    caps = schema["properties"]["caps"]
    assert caps["additionalProperties"] is False
    assert "min_clean_canary_orders" in caps["properties"]
    assert "min_clean_canary_orders" not in caps["required"]
    assert set(caps["required"]) == set(_CAPS) == set(lb._CAP_KEYS)


def test_a_budget_built_today_omits_the_retired_cap():
    rec = _build(caps={"min_clean_canary_orders": 3})     # a caller still passing it
    assert "min_clean_canary_orders" not in rec["caps"]
    assert rec["caps"] == _CAPS


def test_a_legacy_record_carrying_the_retired_cap_still_verifies_and_resolves(tmp_path):
    _write_raw(tmp_path, _legacy_record())
    assert lb.read_registered_budget(tmp_path)["caps"]["min_clean_canary_orders"] == 4
    st = lb.budget_status(tmp_path, now="2026-08-01T00:00:00Z")
    assert st["valid"] is True and st["error"] is None
    lim = lb.limits_from_budget(lb.read_registered_budget(tmp_path))
    assert lim.max_order_notional_usdt == 60.0 and lim.max_daily_order_count == 2


def test_stripping_the_retired_cap_from_a_legacy_record_breaks_its_hash(tmp_path):
    """Why nothing migrates the records on disk: the key is inside the self-hash."""
    record = _legacy_record()
    del record["caps"]["min_clean_canary_orders"]
    _write_raw(tmp_path, record)
    with pytest.raises(ToolError) as exc:
        lb.read_registered_budget(tmp_path)
    assert exc.value.reason_code == lb.BUDGET_TAMPERED
    assert lb.budget_status(tmp_path, now="2026-08-01T00:00:00Z")["error"] == lb.BUDGET_TAMPERED


def test_no_runtime_or_script_reads_the_retired_cap():
    """A subscript on it raises on every budget built today; an attribute read means the field
    came back. Either one on the live leg would raise before settle/protect. Walked as code, not
    text, so the prose explaining the retirement does not trip it."""
    import ast

    name = "min_clean_canary_orders"
    offenders = []
    for base in ("runtime", "scripts"):
        for path in sorted((repo_root() / base).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                subscripted = (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant)
                               and node.slice.value == name)
                if subscripted or (isinstance(node, ast.Attribute) and node.attr == name):
                    offenders.append(f"{path.relative_to(repo_root())}:{node.lineno}")
    assert offenders == []


def test_the_register_script_refuses_the_retired_flag_and_writes_nothing(tmp_path, monkeypatch, capsys):
    import importlib

    reg = importlib.import_module("scripts.register_live_trading_budget")
    written: list[object] = []
    monkeypatch.setattr(reg.live_budget, "write_registered_budget", lambda *a, **k: written.append(1))
    with pytest.raises(SystemExit) as exc:
        reg.main(["--registered-by", "thomas", "--root", str(tmp_path),
                  "--min-clean-canary-orders", "4"])
    assert exc.value.code == 2
    assert written == []
    assert "--min-clean-canary-orders" in capsys.readouterr().err


def test_the_register_script_writes_a_record_without_the_retired_cap(tmp_path, monkeypatch, capsys):
    import importlib

    reg = importlib.import_module("scripts.register_live_trading_budget")
    monkeypatch.setattr(reg, "assert_not_foreign_root_run", lambda root=None, **kw: None)
    assert reg.main(["--registered-by", "thomas", "--root", str(tmp_path)]) == 0
    record = lb.read_registered_budget(tmp_path)
    assert "min_clean_canary_orders" not in record["caps"]
    assert "canary" not in capsys.readouterr().out


# --- loader ------------------------------------------------------------------

def test_limits_from_budget_maps_caps_and_leaves_confirmation_to_the_operator():
    lim = lb.limits_from_budget(_build())
    assert lim.max_order_notional_usdt == 60.0 and lim.max_daily_order_count == 2
    assert lim.absolute_max_notional_usdt == 200.0
    assert not hasattr(lim, "min_clean_canary_orders")
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
