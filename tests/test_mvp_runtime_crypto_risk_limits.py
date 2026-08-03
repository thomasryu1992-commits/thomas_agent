"""The registered C4 risk-breaker limits (``crypto_risk_limits.v0.1``).

Under test, in the order the risk actually runs:

- the **defaults are unchanged** — an unconfigured runtime judges on exactly the numbers it
  judged on while they were hardcoded, and every guard verdict says which limits judged it;
- the **relaxation bounds hold** — a limit past its bound is refused at build time, refused
  again on read, and (belt and suspenders) fails the guard closed if it reaches it anyway;
- an **unusable record is a refusal, never a fallback to the defaults** — tampered, unparseable
  and lapsed records all raise, and the cycle turns each into no-new-position;
- **registering changes behaviour and nothing else** — no grant, no enablement.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from runtime.mvp_runtime.crypto import guards, risk_limits as rl
from runtime.mvp_runtime.crypto.live_pnl import state_dir
from runtime.mvp_runtime.errors import ToolError
from runtime.mvp_runtime.paths import repo_root as _repo_root

NOW = "2026-07-30T00:00:00Z"
FROM = "2026-07-30T00:00:00Z"
UNTIL = "2026-08-30T00:00:00Z"

_LIMITS = dict(
    risk_per_trade=0.01,
    daily_max_loss_r=-2.0,
    weekly_max_loss_r=-5.0,
    max_consecutive_losses=3,
    max_drawdown_pct=-10.0,
)


def _build(**overrides):
    limits = {**dict(_LIMITS), **overrides.pop("limits", {})}
    kw = dict(limits=limits, valid_from=FROM, valid_until=UNTIL,
              registered_by="thomas", registered_at=NOW)
    kw.update(overrides)
    return rl.build_risk_limits_record(**kw)


def _register(root, **overrides):
    record = _build(**overrides)
    rl.write_registered_limits(record, root=root)
    return record


def _loss(pnl_r: float, at: str) -> dict:
    return {"outcome_closed": True, "result_R": pnl_r, "created_at_utc": at}


# --- the defaults, which are the whole safety story for an unconfigured machine -------------

def test_nothing_registered_resolves_to_the_module_defaults(tmp_path):
    assert rl.resolve_risk_limits(tmp_path, now=NOW) == guards.DEFAULT_RISK_LIMITS


def test_the_defaults_are_still_the_source_numbers():
    """A rename or a stray edit to a breaker default has to fail a test, not ship quietly."""
    d = guards.DEFAULT_RISK_LIMITS
    assert (d.risk_per_trade, d.daily_max_loss_r, d.weekly_max_loss_r,
            d.max_consecutive_losses, d.max_drawdown_pct) == (0.01, -2.0, -5.0, 3, -10.0)
    assert d.source == guards.SOURCE_DEFAULT and d.limits_id is None
    assert d.problems() == []


def test_the_verdict_records_which_limits_judged_it(tmp_path):
    verdict = guards.run_risk_guard([], now=NOW)
    assert verdict["limits"]["source"] == guards.SOURCE_DEFAULT
    assert verdict["limits"]["daily_max_loss_r"] == -2.0
    assert verdict["limits"]["drawdown_limit_r"] == 10.0      # -10% at 1% risk
    assert verdict["limits"]["limits_id"] is None


def test_a_registered_set_judges_and_names_its_record(tmp_path):
    record = _register(tmp_path, limits={"daily_max_loss_r": -1.0})
    limits = rl.resolve_risk_limits(tmp_path, now=NOW)
    assert limits.source == guards.SOURCE_REGISTERED
    assert limits.limits_id == record["limits_id"]
    assert limits.record_sha256 == record["record_sha256"]

    # -1.5R in one day is inside the -2.0 default and breaches the registered -1.0.
    verdict = guards.run_risk_guard([_loss(-1.5, NOW)], now=NOW, limits=limits)
    assert verdict["allow_new_position"] is False
    assert "daily_loss_limit_breached" in verdict["problems"]
    assert guards.run_risk_guard([_loss(-1.5, NOW)], now=NOW)["allow_new_position"] is True


def test_a_relaxed_limit_actually_relaxes(tmp_path):
    """The point of the record: -4R breaches the default and passes an authorized -6R."""
    _register(tmp_path, limits={"daily_max_loss_r": -6.0, "weekly_max_loss_r": -15.0})
    limits = rl.resolve_risk_limits(tmp_path, now=NOW)
    history = [_loss(-4.0, NOW)]
    assert guards.run_risk_guard(history, now=NOW)["allow_new_position"] is False
    assert guards.run_risk_guard(history, now=NOW, limits=limits)["allow_new_position"] is True


def test_risk_per_trade_moves_the_drawdown_limit(tmp_path):
    """The drawdown breaker is a percent mapped through risk-per-trade; both are configurable."""
    _register(tmp_path, limits={"risk_per_trade": 0.02, "max_drawdown_pct": -20.0})
    limits = rl.resolve_risk_limits(tmp_path, now=NOW)
    assert guards._drawdown_limit_r(limits) == 10.0            # -20% at 2% = 10R, same as default
    assert guards.run_risk_guard([], now=NOW, limits=limits)["drawdown_limit_r"] == 10.0


# --- the bounds: refused, never clamped ------------------------------------------------------

@pytest.mark.parametrize("limits", [
    {"risk_per_trade": 0.03},                 # above the 0.02 ceiling
    {"risk_per_trade": 0.0},                  # zero is not a limit
    {"risk_per_trade": -0.01},
    {"daily_max_loss_r": -6.5},               # below the -6.0 floor
    {"daily_max_loss_r": 0.0},                # a non-negative loss limit never trips
    {"daily_max_loss_r": 1.0},
    {"weekly_max_loss_r": -15.5},
    {"max_consecutive_losses": 0},            # would read as always-breached
    {"max_consecutive_losses": 11},
    {"max_drawdown_pct": -25.5},
    {"max_drawdown_pct": 0.0},
])
def test_a_limit_outside_its_bound_is_refused_at_build(limits):
    with pytest.raises(ToolError) as exc:
        _build(limits=limits)
    assert exc.value.reason_code == rl.LIMITS_INVALID
    # The refusal names the offending number rather than saying "invalid".
    assert any(str(v) in exc.value.reason for v in limits.values())


def test_the_schema_bounds_and_the_code_bounds_agree():
    """The bounds are written twice — in guards.py and in the closed schema — so they can drift.

    They are duplicated on purpose (the schema must refuse a hand-written file on its own, and
    the code must refuse limits that never touched a file), which makes this test the thing that
    keeps the duplication honest: change one number and it fails here rather than in production,
    where the looser of the two would quietly become the real bound."""
    schema = json.loads((_repo_root() / "schemas" / rl.RISK_LIMITS_SCHEMA_FILE).read_text())
    props = schema["properties"]["limits"]["properties"]
    assert props["risk_per_trade"]["maximum"] == guards.MAX_RISK_PER_TRADE
    assert props["daily_max_loss_r"]["minimum"] == guards.MIN_DAILY_MAX_LOSS_R
    assert props["weekly_max_loss_r"]["minimum"] == guards.MIN_WEEKLY_MAX_LOSS_R
    assert props["max_consecutive_losses"]["maximum"] == guards.MAX_MAX_CONSECUTIVE_LOSSES
    assert props["max_drawdown_pct"]["minimum"] == guards.MIN_MAX_DRAWDOWN_PCT
    # And the schema is closed, so a record cannot carry a limit the guard never reads.
    assert schema["additionalProperties"] is False
    assert schema["properties"]["limits"]["additionalProperties"] is False
    assert set(props) == set(_LIMITS)


def test_a_daily_limit_looser_than_the_weekly_one_is_refused():
    """Incoherent, not merely lax: the weekly breaker would trip first in every history."""
    with pytest.raises(ToolError) as exc:
        _build(limits={"daily_max_loss_r": -5.5, "weekly_max_loss_r": -5.0})
    assert exc.value.reason_code == rl.LIMITS_INVALID
    assert "looser" in exc.value.reason


def test_the_bounds_are_one_sided_so_tightening_is_always_allowed():
    """Every bound sits on the loosening side; an arbitrarily strict set must build."""
    record = _build(limits={"risk_per_trade": 0.0001, "daily_max_loss_r": -0.1,
                            "weekly_max_loss_r": -0.1, "max_consecutive_losses": 1,
                            "max_drawdown_pct": -0.1})
    assert record["limits"]["max_consecutive_losses"] == 1


def test_out_of_bounds_limits_fail_the_guard_closed_without_the_record_layer():
    """The bounds are enforced where the numbers are USED, not only where they are parsed."""
    rogue = guards.RiskLimits(daily_max_loss_r=-99.0)
    verdict = guards.run_risk_guard([], now=NOW, limits=rogue)
    assert verdict["allow_new_position"] is False
    assert verdict["problems"] == [guards.RISK_LIMITS_INVALID_PROBLEM]
    assert "-99.0" in verdict["risk_history_error"]


def test_a_non_whole_consecutive_loss_count_is_refused():
    with pytest.raises(ToolError) as exc:
        _build(limits={"max_consecutive_losses": 3.5})
    assert exc.value.reason_code == rl.LIMITS_INVALID


@pytest.mark.parametrize("bad", [{"limits": {"daily_max_loss_r": "soon"}}, {"limits": {}}])
def test_a_malformed_limits_mapping_is_refused(bad):
    with pytest.raises(ToolError) as exc:
        rl.build_risk_limits_record(valid_from=FROM, valid_until=UNTIL, registered_by="thomas",
                                    registered_at=NOW, **bad)
    assert exc.value.reason_code == rl.LIMITS_INVALID


def test_the_window_must_open_before_it_closes():
    with pytest.raises(ToolError) as exc:
        _build(valid_from=UNTIL, valid_until=FROM)
    assert exc.value.reason_code == rl.LIMITS_INVALID


def test_an_operator_identity_is_required():
    with pytest.raises(ToolError) as exc:
        _build(registered_by="   ")
    assert exc.value.reason_code == rl.LIMITS_INVALID


# --- the verified read: an unusable record refuses, it does not fall back --------------------

def test_a_record_is_self_hashed_and_schema_valid():
    record = _build()
    assert record["schema_version"] == rl.RISK_LIMITS_SCHEMA_VERSION
    assert record["limits_id"].startswith("risklimits_")
    assert record["record_sha256"].startswith("sha256:")


def test_the_id_and_hash_derive_from_the_numbers():
    """Changing a limit is a new record, never a silent edit."""
    a, b = _build(), _build(limits={"daily_max_loss_r": -1.0})
    assert a["limits_id"] != b["limits_id"] and a["record_sha256"] != b["record_sha256"]


def test_a_tampered_record_raises_rather_than_resolving(tmp_path):
    _register(tmp_path)
    path = rl.limits_path(tmp_path)
    data = json.loads(path.read_text())
    data["limits"]["daily_max_loss_r"] = -6.0          # hand-loosened, hash left alone
    path.write_text(json.dumps(data))
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now=NOW)
    assert exc.value.reason_code == rl.LIMITS_TAMPERED


def test_an_unparseable_record_raises(tmp_path):
    state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    rl.limits_path(tmp_path).write_text("{not json")
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now=NOW)
    assert exc.value.reason_code == rl.LIMITS_UNREADABLE


def test_a_lapsed_record_refuses_instead_of_reverting_to_the_defaults(tmp_path):
    """The fail-open trap this design exists to avoid.

    An operator who TIGHTENED a breaker must not have it loosened back to the default by the
    record lapsing. Uncertain -> BLOCK, and the message says how to get back to the defaults."""
    _register(tmp_path, limits={"daily_max_loss_r": -1.0})
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now="2026-09-30T00:00:00Z")
    assert exc.value.reason_code == rl.LIMITS_EXPIRED
    assert "delete the record" in exc.value.reason


def test_a_record_that_predates_its_window_also_refuses(tmp_path):
    _register(tmp_path)
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now="2026-07-01T00:00:00Z")
    assert exc.value.reason_code == rl.LIMITS_EXPIRED


def test_a_written_record_that_slipped_past_the_bounds_is_refused_on_read(tmp_path):
    """Schema and code bounds agree today; if a file ever gets past the schema, code still wins."""
    with pytest.raises(ToolError):
        rl.limits_from_record({"limits_id": "risklimits_" + "0" * 20,
                               "limits": {**_LIMITS, "daily_max_loss_r": -50.0}})


def test_refusing_to_write_a_record_that_fails_its_own_hash(tmp_path):
    record = dict(_build())
    record["record_sha256"] = "sha256:" + "0" * 64
    with pytest.raises(ToolError) as exc:
        rl.write_registered_limits(record, root=tmp_path)
    assert exc.value.reason_code == rl.LIMITS_TAMPERED


# --- the status board -----------------------------------------------------------------------

def test_status_on_a_fresh_machine_reports_valid_defaults(tmp_path):
    """Nothing registered is a SUPPORTED state, so it reports valid with the defaults shown."""
    status = rl.limits_status(tmp_path, now=NOW)
    assert status == {"registered": False, "valid": True, "error": None, "limits_id": None,
                      "effective": guards.DEFAULT_RISK_LIMITS.as_record()}


def test_status_names_the_error_rather_than_reporting_comfortable_defaults(tmp_path):
    state_dir(tmp_path).mkdir(parents=True, exist_ok=True)
    rl.limits_path(tmp_path).write_text("{not json")
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["registered"] is True and status["valid"] is False
    assert status["error"] == rl.LIMITS_UNREADABLE and status["effective"] is None


def test_status_reports_a_lapsed_record_as_invalid(tmp_path):
    _register(tmp_path)
    status = rl.limits_status(tmp_path, now="2026-09-30T00:00:00Z")
    assert status["valid"] is False and status["error"] == rl.LIMITS_EXPIRED


def test_status_shows_the_effective_numbers_of_a_current_record(tmp_path):
    record = _register(tmp_path, limits={"max_consecutive_losses": 5})
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["valid"] is True and status["limits_id"] == record["limits_id"]
    assert status["effective"]["max_consecutive_losses"] == 5
    assert status["effective"]["source"] == guards.SOURCE_REGISTERED


# --- what registering does NOT do ------------------------------------------------------------

def test_registering_limits_grants_nothing(tmp_path):
    """It is a record, not a permission: no grant, no safety flag, no enablement anywhere."""
    _register(tmp_path, limits={"daily_max_loss_r": -6.0, "weekly_max_loss_r": -15.0})
    written = {p.name for p in state_dir(tmp_path).iterdir()}
    assert written == {rl.RISK_LIMITS_FILENAME}
    source = (rl.__file__,)
    text = open(source[0], encoding="utf-8").read()
    for forbidden in ("authorize(", "select_gated", "MVP_LIVE_TRADING", "safety_gate"):
        assert forbidden not in text, f"the limits record must not touch {forbidden}"


# --- the drawdown baseline rebase block (#405) ---------------------------------

def test_a_record_without_a_rebase_block_carries_no_exclusion(tmp_path):
    """Backward compatible by construction: every record registered before the mechanism
    existed resolves to an empty exclusion, which is the pre-rebase behaviour exactly."""
    _register(tmp_path)
    resolved = rl.resolve_risk_limits(tmp_path, now=NOW)
    assert resolved.drawdown_excluded_strategy_ids == ()


def test_the_rebase_block_rides_from_the_record_to_the_limits(tmp_path):
    _register(tmp_path, drawdown_baseline_rebase={
        "excluded_strategy_ids": ["S9", "S3"], "reason": "15m pool retired 2026-07-31",
    })
    resolved = rl.resolve_risk_limits(tmp_path, now=NOW)
    assert resolved.drawdown_excluded_strategy_ids == ("S3", "S9")   # sorted at build
    assert resolved.as_record()["drawdown_excluded_strategy_ids"] == ["S3", "S9"]


def test_a_rebase_without_a_reason_is_refused():
    """A rebase is a mechanism for forgetting losses. A record that cannot say whose and why
    leaves a ledger nobody can re-read."""
    with pytest.raises(ToolError) as exc:
        _build(drawdown_baseline_rebase={"excluded_strategy_ids": ["S1"], "reason": "  "})
    assert exc.value.reason_code == rl.LIMITS_INVALID


@pytest.mark.parametrize("block", [
    {"excluded_strategy_ids": [], "reason": "empty"},
    {"excluded_strategy_ids": ["S1", "S1"], "reason": "duplicated"},
    {"excluded_strategy_ids": ["", "S1"], "reason": "blank id"},
    {"excluded_strategy_ids": "S1", "reason": "not a list"},
])
def test_a_malformed_rebase_block_is_refused(block):
    with pytest.raises(ToolError) as exc:
        _build(drawdown_baseline_rebase=block)
    assert exc.value.reason_code == rl.LIMITS_INVALID


def test_the_rebase_block_is_covered_by_the_records_self_hash(tmp_path):
    """Tamper evidence, which is the reason to reuse this record rather than invent one:
    editing the exclusion list after registration invalidates the hash and fails closed."""
    record = _register(tmp_path, drawdown_baseline_rebase={
        "excluded_strategy_ids": ["S3"], "reason": "retired",
    })
    tampered = {**record}
    tampered["drawdown_baseline_rebase"] = {
        "excluded_strategy_ids": ["S3", "S4"], "reason": "retired",
    }
    rl.limits_path(tmp_path).write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ToolError):
        rl.resolve_risk_limits(tmp_path, now=NOW)


def test_the_rebase_block_does_not_rename_the_record():
    """`limits_id` identifies the NUMBERS. Two records with the same breakers over different
    retirement sets are the same limits applied to different populations, so the id is stable
    and the self-hash — which does cover the block — is what distinguishes them."""
    plain = _build()
    rebased = _build(drawdown_baseline_rebase={"excluded_strategy_ids": ["S3"], "reason": "r"})
    assert plain["limits_id"] == rebased["limits_id"]
    assert plain["record_sha256"] != rebased["record_sha256"]


# --- Gate 0's operator acknowledgement — removed 2026-08-03 ---------------------
#
# Eight tests stood here, over `crypto/live_candidate_ack.py` and its schema. They pinned a
# careful design: opt-in and absent by default, bound to the routable set by EXACT equality so
# a promotion voids it, expiring, self-hashed, and reporting rather than raising on a tampered
# record. None of that was wrong. What was wrong is what it was for — overriding a gate that
# could not be satisfied — so the module, the schema, the signing script and these tests are
# deleted together rather than left as machinery with nothing to override.
#
# `docs/proposals/GATE0_CANNOT_BE_SATISFIED_V0.1.md` has the measurement. They lived in this
# file because they shared the "self-hashed, expiring, per-machine record" idiom with
# `risk_limits`; that idiom stays, and the tests above it are untouched.
