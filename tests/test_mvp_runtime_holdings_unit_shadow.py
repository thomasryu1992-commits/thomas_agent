"""H6b-shadow (Thomas 2026-10-28, ``docs/proposals/H6B_UNIT_SHADOW_V0.1.md`` S1–S5 all A).

Pinned here: the unit path starts at the first ready point at 100 (S1); only external deposits and withdrawals
move units, income and fees are the account's result (S2); a flow is valued at the prices of the point it lands
on, and an unpriceable one holds the path (S3); an open exception holds it and a later confirmation lands at its
own point, because the path is recomputed (S4); the door carries counts and never NAV/unit (S5); and no door
reads the NAV-point log. No real account, amount or file is read.
"""

from __future__ import annotations

import ast
import json
import pathlib

import pytest

from runtime.mvp_runtime import store_reads
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.holdings import board, cash_flows, unit_shadow
from tests._h6d_support import CUTOVER, iso, ledger, ms, set_cutover, toss_fire

ROOT = pathlib.Path(__file__).resolve().parents[1]
CUT = [{"event": cash_flows.EVENT_CUTOVER_SET, "cutover_at": "2026-10-20T00:00:00Z"}]
H = [iso(ms("2026-10-29T00:14:00Z") + i * 3_600_000) for i in range(6)]


def pt(at, nav, *, ready=True, rate="1400.0000", prices=None):
    return {"event": unit_shadow.EVENT_POINT, "at": at, "nav_krw": f"{nav:.0f}", "usd_krw_rate": rate,
            "asset_price_usdt": prices or {}, "h6_ready": ready}


def toss(end, residual, *, pocket="KRW", key=None):
    start = iso(ms(end) - 3_600_000)
    return {"event": cash_flows.EVENT_TOSS_RESIDUAL, "source_event_key": key or f"toss:residual:{pocket}:{start}..{end}",
            "pocket": pocket, "window_start": start, "window_end": end, "residual": str(residual)}


def res(row, kind):
    return {"event": cash_flows.EVENT_RESOLVED, "resolves": row["source_event_key"], "kind": kind}


def bflow(key, at_ms, direction, asset, amount, *, status=cash_flows.READY, eligible=True, held_reason=None):
    return {"event": cash_flows.EVENT_FLOW, "source_event_key": key, "direction": direction, "asset": asset,
            "amount": amount, "event_ms": at_ms, "accounting_status": status, "held_reason": held_reason,
            "accounting_eligibility": cash_flows.ELIGIBLE if eligible else cash_flows.OBSERVED_ONLY}


def run(points, rows=()):
    return unit_shadow.replay(points, [*CUT, *rows])


# --- S1: the start --------------------------------------------------------------------------------------------

def test_nothing_runs_until_a_ready_point():
    out = run([pt(H[0], 1_000_000, ready=False), pt(H[1], 1_100_000, ready=False)])
    assert out["state"] == unit_shadow.STATE_WAITING and "nav_per_unit" not in out


def test_the_path_starts_at_100_on_the_first_ready_point_and_follows_the_market():
    out = run([pt(H[0], 900_000, ready=False), pt(H[1], 1_000_000), pt(H[2], 1_100_000, ready=False)])
    assert out["started_at"] == H[1] and out["state"] == unit_shadow.STATE_RUNNING
    assert out["nav_per_unit"] == 110.0 and out["peak_nav_per_unit"] == 110.0 and out["drawdown_pct"] == 0.0


def test_a_flow_at_or_before_the_start_is_not_a_unit_flow():
    before = toss(H[1], 500_000)
    out = run([pt(H[1], 1_500_000), pt(H[2], 1_500_000)], [before, res(before, "deposit")])
    assert out["flows_applied"] == {"deposit": 0, "withdrawal": 0} and out["nav_per_unit"] == 100.0


# --- S2: what moves units -------------------------------------------------------------------------------------

def test_a_deposit_issues_units_and_leaves_nav_per_unit_where_the_market_put_it():
    deposit = toss(H[2], 500_000)
    out = run([pt(H[1], 1_000_000), pt(H[2], 1_600_000)], [deposit, res(deposit, "deposit")])
    assert out["nav_per_unit"] == 110.0 and out["flows_applied"] == {"deposit": 1, "withdrawal": 0}


def test_a_withdrawal_burns_units_and_each_flow_is_its_own():
    deposit, withdrawal = toss(H[2], 500_000), toss(H[2], -200_000, pocket="USD")
    withdrawal["residual"] = "-200.00"         # USD 200 at 1,400 = 280,000
    rows = [deposit, res(deposit, "deposit"), withdrawal, res(withdrawal, "withdrawal")]
    out = run([pt(H[1], 1_000_000), pt(H[2], 1_100_000 + 500_000 - 280_000)], rows)
    assert out["nav_per_unit"] == 110.0 and out["flows_applied"] == {"deposit": 1, "withdrawal": 1}


@pytest.mark.parametrize("kind", ["income", "fee", "read_incomplete", "semantics_mismatch"])
def test_income_fees_and_non_flows_are_the_accounts_own_result(kind):
    row = toss(H[2], 100_000 if kind != "fee" else -100_000)
    nav = 1_100_000 if kind != "fee" else 900_000
    out = run([pt(H[1], 1_000_000), pt(H[2], nav)], [row, res(row, kind)])
    assert out["flows_applied"] == {"deposit": 0, "withdrawal": 0}
    assert out["nav_per_unit"] == nav / 10_000


def test_binance_flows_count_when_ready_or_confirmed_and_never_before_the_cutover():
    at = ms(H[1]) + 600_000
    ready = bflow("b:1", at, "in", "USDT", "100")                                   # 140,000
    pre = bflow("b:2", at, "in", "USDT", "100", eligible=False)                     # observed only
    paid = bflow("b:3", at, "in", "USDT", "50", status=cash_flows.HELD,             # 70,000 once confirmed
                 held_reason="pay_direction_unverified")
    dup = bflow("b:4", at, "in", "USDT", "50", status=cash_flows.HELD, held_reason="possible_duplicate")
    rows = [ready, pre, paid, res(paid, "deposit"), dup, res(dup, cash_flows.EXCLUDE)]
    out = run([pt(H[1], 1_000_000), pt(H[2], 1_000_000 + 140_000 + 70_000)], rows)
    assert out["flows_applied"] == {"deposit": 2, "withdrawal": 0} and out["nav_per_unit"] == 100.0


def test_a_held_flow_nothing_confirmed_never_moves_units_even_once_it_is_no_exception():
    # A moved cutover puts the flow before the boundary, so it is no exception and holds nothing: only its
    # own status keeps it out of the units.
    held = bflow("b:h", ms(H[1]) + 600_000, "in", "USDT", "100", status=cash_flows.HELD, held_reason="pay_direction_unverified")
    moved = {"event": cash_flows.EVENT_CUTOVER_MIGRATED, "cutover_at": "2026-11-30T00:00:00Z"}
    out = run([pt(H[1], 1_000_000), pt(H[2], 1_140_000)], [moved, held])
    assert out["state"] == unit_shadow.STATE_RUNNING and out["flows_applied"]["deposit"] == 0
    assert out["nav_per_unit"] == 114.0


# --- S3: valuation --------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("flow", "point", "expected"), [
    ({"asset": "KRW", "amount": 5000}, {}, 5000.0),
    ({"asset": "USD", "amount": 10}, {"usd_krw_rate": "1400"}, 14_000.0),
    ({"asset": "FDUSD", "amount": "10"}, {"usd_krw_rate": "1400"}, 14_000.0),
    ({"asset": "BTC", "amount": "0.001"}, {"usd_krw_rate": "1400", "asset_price_usdt": {"BTC": "80000"}}, 112_000.0),
    ({"asset": "BTC", "amount": "0.001"}, {"usd_krw_rate": "1400"}, None),               # no price at that point
    ({"asset": "USDT", "amount": "10"}, {"usd_krw_rate": None}, None),                  # no rate
    ({"asset": "KRW", "amount": "-1"}, {}, None),
    ({"asset": "KRW", "amount": "x"}, {}, None),
])
def test_a_flow_is_valued_at_its_points_prices(flow, point, expected):
    value = unit_shadow.value_krw(flow, point)
    assert value == (pytest.approx(expected) if expected is not None else None)


def test_an_unpriceable_flow_holds_the_path_at_the_point_before_it():
    coin = bflow("b:z", ms(H[2]) + 60_000, "in", "ZZZ", "5")
    out = run([pt(H[1], 1_000_000), pt(H[2], 1_200_000), pt(H[3], 1_300_000), pt(H[4], 1_300_000)], [coin])
    assert out["state"] == unit_shadow.STATE_HELD and out["held_reason"] == unit_shadow.HELD_UNVALUED
    assert out["as_of"] == H[2] and out["nav_per_unit"] == 120.0 and out["held"] == 1
    assert out["h6c_askable"] is False


# --- S4: held, then recomputed --------------------------------------------------------------------------------

def test_an_open_exception_holds_and_its_confirmation_lands_at_its_own_point():
    deposit = toss(H[2], 500_000)
    points = [pt(H[1], 1_000_000), pt(H[2], 1_500_000), pt(H[3], 1_650_000)]
    held = run(points, [deposit])
    assert held["state"] == unit_shadow.STATE_HELD and held["held_reason"] == unit_shadow.HELD_OPEN_EXCEPTION
    assert held["nav_per_unit"] == 100.0 and held["as_of"] == H[1] and held["held"] == 1
    later = run(points, [deposit, res(deposit, "deposit")])
    assert later["state"] == unit_shadow.STATE_RUNNING and later["as_of"] == H[3]
    assert later["nav_per_unit"] == 110.0 and later["flows_applied"]["deposit"] == 1


def test_an_exception_from_before_the_start_does_not_hold():
    old = toss(H[0], 500_000)
    out = run([pt(H[0], 1_000_000, ready=False), pt(H[1], 1_000_000), pt(H[2], 1_000_000)], [old])
    assert out["state"] == unit_shadow.STATE_RUNNING and out["held"] == 0


def test_an_unreadable_nav_holds():
    out = run([pt(H[1], 1_000_000), {**pt(H[2], 1), "nav_krw": "x"}])
    assert out["state"] == unit_shadow.STATE_HELD and out["held_reason"] == unit_shadow.HELD_INVALID_NAV


def test_the_peak_is_the_unit_peak_and_a_deposit_after_a_fall_is_not_a_recovery():
    deposit = toss(H[4], 600_000)
    points = [pt(H[1], 1_000_000), pt(H[2], 1_200_000), pt(H[3], 900_000), pt(H[4], 1_500_000)]
    out = run(points, [deposit, res(deposit, "deposit")])
    assert out["peak_nav_per_unit"] == 120.0 and out["peak_at"] == H[2]
    assert out["nav_per_unit"] == 90.0 and out["drawdown_pct"] == -25.0


# --- S5: when H6c may be asked --------------------------------------------------------------------------------

def _days(n, *, withdrawal=True, held=False):
    days = [iso(ms("2026-10-29T00:14:00Z") + d * 86_400_000) for d in range(n + 1)]
    deposit, out_ = toss(days[1], 100_000), toss(days[2], -100_000)
    rows = [deposit, res(deposit, "deposit")] + ([out_, res(out_, "withdrawal")] if withdrawal else [])
    if held:
        rows.append(toss(days[-1], 7))
    navs = [1_000_000, 1_100_000, 1_000_000 if withdrawal else 1_100_000] + [1_000_000] * (n - 2)
    return run([pt(at, nav) for at, nav in zip(days, navs)], rows)


def test_h6c_is_askable_after_fourteen_days_with_a_deposit_and_a_withdrawal():
    out = _days(14)
    assert out["days"] == 14.0 and out["h6c_askable"] is True


@pytest.mark.parametrize(("n", "withdrawal", "held"), [(13, True, False), (14, False, False), (14, True, True)])
def test_h6c_waits_on_each_condition(n, withdrawal, held):
    assert _days(n, withdrawal=withdrawal, held=held)["h6c_askable"] is False


# --- the real ledger and the point log ------------------------------------------------------------------------

def test_a_rule_closed_toss_deposit_is_a_unit_flow_at_its_residual_not_the_round_amount(tmp_path):
    set_cutover(tmp_path, CUTOVER)
    toss_fire(tmp_path, 1_000_000, H[0])
    toss_fire(tmp_path, 1_200_050, H[1])
    rows = ledger(tmp_path)
    assert [r["requested_by"] for r in rows if r["event"] == cash_flows.EVENT_RESOLVED] == [cash_flows.QUIET_WINDOW_RULE]
    flows = unit_shadow.unit_flows(rows)
    assert [(f["kind"], f["asset"], f["amount"], f["at_ms"]) for f in flows] == [("deposit", "KRW", 200_050.0, ms(H[1]))]
    unit_shadow.record_point(tmp_path, at=H[0], nav_krw=1_000_000.0, usd_krw_rate=1400.0, asset_price_usdt={},
                             h6_ready=True)
    unit_shadow.record_point(tmp_path, at=H[1], nav_krw=1_200_050.0, usd_krw_rate=1400.0, asset_price_usdt={},
                             h6_ready=True)
    out = unit_shadow.shadow(tmp_path)
    assert out["nav_per_unit"] == 100.0 and out["flows_applied"]["deposit"] == 1


def test_a_point_is_one_chained_line_of_strings_and_a_tamper_breaks_the_shadow(tmp_path):
    for _ in range(2):
        unit_shadow.record_point(tmp_path, at=H[0], nav_krw=1_234_567.4, usd_krw_rate=1400.12346,
                                 asset_price_usdt={"BTC": 80_000.0, "ZZZ": 0.0}, h6_ready=False)
    rows = unit_shadow.verify(tmp_path)
    assert len(rows) == 1
    assert (rows[0]["nav_krw"], rows[0]["usd_krw_rate"], rows[0]["asset_price_usdt"]) == ("1234567", "1400.1235",
                                                                                          {"BTC": "80000"})
    path = unit_shadow.path(tmp_path)
    path.write_text(path.read_text(encoding="utf-8").replace("1234567", "1234568"), encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        unit_shadow.shadow(tmp_path)
    assert exc.value.reason_code == unit_shadow.NAV_POINTS_TAMPERED


def test_no_door_reads_the_point_log():
    allowed = {ROOT / "runtime" / "mvp_runtime" / "holdings" / n for n in ("unit_shadow.py", "store.py")}
    offenders = []
    for path in [*(ROOT / "runtime").rglob("*.py"), *(ROOT / "scripts").rglob("*.py")]:
        if path in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(n, ast.Constant) and n.value == unit_shadow.FILENAME for n in ast.walk(tree)) or any(
                isinstance(n, ast.ImportFrom) and ((n.module or "").endswith("unit_shadow")
                                                   or any(a.name == "unit_shadow" for a in n.names))
                for n in ast.walk(tree)):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == []


# --- what is shown --------------------------------------------------------------------------------------------

SHADOW = {"state": "running", "started_at": H[1], "as_of": H[3], "days": 3.24, "nav_per_unit": 101.2345,
          "peak_nav_per_unit": 102.5, "peak_at": H[2], "drawdown_pct": -1.23,
          "flows_applied": {"deposit": 1, "withdrawal": 0}, "held": 0, "held_reason": None, "h6c_askable": False,
          "method": cash_flows.ACCOUNTING_METHOD}


def _view(shadow):
    return {"available": True, "as_of": H[3], "age_seconds": 60.0, "stale": False, "partial": False,
            "cash_flows": None, "unit_shadow": shadow}


def test_the_door_carries_counts_and_never_the_unit_value():
    data = store_reads.project_holdings_status(_view(SHADOW), now=H[3])
    assert data["unit_shadow"] == {"state": "running", "h6c_askable": False, "days": 3.2, "deposits": 1,
                                   "withdrawals": 0, "held": 0}
    assert data["projection"] == store_reads.PROJECTION_V1
    text = json.dumps(data)
    assert "101.2" not in text and "102.5" not in text and "-1.23" not in text


def test_an_unknown_shadow_state_reads_unknown_and_partial():
    data = store_reads.project_holdings_status(_view({**SHADOW, "state": "something_new"}), now=H[3])
    assert data["unit_shadow"]["state"] == "UNKNOWN" and data["projection"] == store_reads.PROJECTION_PARTIAL


@pytest.mark.parametrize(("shadow", "expected"), [
    (SHADOW, "NAV/unit 101.23, -1.2% from unit peak 102.50"),
    ({"state": "waiting_ready"}, "waiting for H6b_shadow_ready"),
    ({"state": "broken", "error": "HOLDINGS_NAV_POINTS_TAMPERED"}, "not computed this fire (HOLDINGS_NAV_POINTS_TAMPERED)"),
    ({**SHADOW, "state": "held", "held_reason": "open_exception", "held": 2}, "held at"),
])
def test_the_board_line(shadow, expected):
    line = board.render_unit_shadow(shadow)
    assert line.startswith("unit shadow (H6b)") and expected in line
