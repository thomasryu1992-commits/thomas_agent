"""The registered C4 risk-breaker limits (``crypto_risk_limits.v0.1``).

Under test, in the order the risk actually runs:

- the **defaults are unchanged** — an unconfigured runtime judges on exactly the numbers it
  judged on while they were hardcoded, and every guard verdict says which limits judged it;
- the **relaxation bounds hold** — a limit past its bound is refused at build time, refused
  again on read, and (belt and suspenders) fails the guard closed if it reaches it anyway;
- an **unusable record is a refusal, never a fallback to the defaults** — tampered, unparseable,
  half-windowed records and legacy records outside their window all raise, and the cycle turns
  each into no-new-position;
- a **record no longer lapses by itself** (2026-09-15, PR1r) — one built today carries no window
  and stands at any time; one registered before is still held to the window it carries, and the
  board (``limits_status``) always says what the resolver does;
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
    kw = dict(limits=limits, registered_by="thomas", registered_at=NOW)
    kw.update(overrides)
    return rl.build_risk_limits_record(**kw)


def _register(root, **overrides):
    record = _build(**overrides)
    rl.write_registered_limits(record, root=root)
    return record


def _legacy_record(*, valid_from=FROM, valid_until=UNTIL, drop=(), limits=None):
    """The pre-PR1r builder's shape, window included, hashed raw — nothing today can build it.
    ``drop`` removes top-level keys BEFORE hashing, so the result verifies: it is a damaged
    shape, not a tampered file."""
    from runtime.read_only_kernel import integrity

    body = {
        "schema_version": rl.RISK_LIMITS_SCHEMA_VERSION, "limits_id": "risklimits_0123456789abcdef0123",
        "limits": {**_LIMITS, **(limits or {})},
        "valid_from": valid_from, "valid_until": valid_until,
        "registered_by": "thomas", "registered_at": FROM,
    }
    for key in drop:
        del body[key]
    body["record_sha256"] = integrity.sha256_record(body)
    return body


def _write_raw(root, record):
    path = rl.limits_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record), encoding="utf-8")


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
        rl.build_risk_limits_record(registered_by="thomas", registered_at=NOW, **bad)
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
    record lapsing. Uncertain -> BLOCK, and the message says how to get back to the defaults.
    Only a record registered before 2026-09-15 can lapse now (PR1r); it still does."""
    _write_raw(tmp_path, _legacy_record(limits={"daily_max_loss_r": -1.0}))
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now="2026-09-30T00:00:00Z")
    assert exc.value.reason_code == rl.LIMITS_EXPIRED
    assert "delete the record" in exc.value.reason


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
    _write_raw(tmp_path, _legacy_record())
    status = rl.limits_status(tmp_path, now="2026-09-30T00:00:00Z")
    assert status["valid"] is False and status["error"] == rl.LIMITS_EXPIRED
    assert (status["valid_from"], status["valid_until"]) == (FROM, UNTIL)


def test_status_shows_the_effective_numbers_of_a_current_record(tmp_path):
    record = _register(tmp_path, limits={"max_consecutive_losses": 5})
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["valid"] is True and status["limits_id"] == record["limits_id"]
    assert status["effective"]["max_consecutive_losses"] == 5
    assert status["effective"]["source"] == guards.SOURCE_REGISTERED
    assert status["registered_at"] == NOW and status["drawdown_rebase_excluded_count"] is None


def test_status_counts_what_a_drawdown_rebase_sets_aside(tmp_path):
    """A rebase forgets losses, and with no window it stands as long as the numbers do — so the
    board and ``--show`` name it beside them rather than leaving it inside the file."""
    _register(tmp_path, drawdown_baseline_rebase={"excluded_strategy_ids": ["cand:c9", "cand:c3"], "reason": "r"})
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["valid"] is True and status["drawdown_rebase_excluded_count"] == 2


# --- the validity window (retired 2026-09-15, PR1r — a stored one is still honoured) ----------
#
# A relaxation used to lapse at the end of its window (30 days by default). Thomas retired the
# window with the canary door: a record built today carries none and judges until it is
# re-registered or deleted. A record registered before carries both ends inside its self-hash and
# is STILL held to them. Ignoring a stored window would bring a lapsed relaxation back into force
# on an archive restore, or after a rollback re-registered one with the old script.

def test_a_record_built_today_carries_no_window():
    record = _build()
    assert "valid_from" not in record and "valid_until" not in record
    rl._validate(record)


def test_the_builder_no_longer_takes_a_window():
    """A caller still passing one must not believe it is recorded."""
    with pytest.raises(TypeError):
        _build(valid_from=FROM, valid_until=UNTIL)


def test_limits_id_seeds_on_registered_at():
    """One id per registration, as before: `breaker_watch` reports a change of id as a limits
    swap, and the window that used to tell two registrations of the same numbers apart is gone."""
    a = _build(registered_at="2026-09-15T00:00:00Z")
    b = _build(registered_at="2026-09-16T00:00:00Z")
    assert a["limits"] == b["limits"]
    assert a["limits_id"] != b["limits_id"] and a["record_sha256"] != b["record_sha256"]
    assert _build()["limits_id"] == _build()["limits_id"], "the seed is deterministic"


@pytest.mark.parametrize("now", ["2000-01-01T00:00:00Z", NOW, "2099-12-31T23:59:59Z"])
def test_a_windowless_record_stands_at_any_time(tmp_path, now):
    record = _register(tmp_path, limits={"max_consecutive_losses": 5})
    resolved = rl.resolve_risk_limits(tmp_path, now=now)
    assert resolved.source == guards.SOURCE_REGISTERED and resolved.limits_id == record["limits_id"]
    assert resolved.max_consecutive_losses == 5
    status = rl.limits_status(tmp_path, now=now)
    assert status["valid"] is True and status["error"] is None
    assert status["valid_from"] is None and status["valid_until"] is None


@pytest.mark.parametrize("now", [FROM, "2026-08-15T00:00:00Z", UNTIL])
def test_a_legacy_record_inside_its_window_is_still_valid(tmp_path, now):
    """Both ends inclusive, as before."""
    _write_raw(tmp_path, _legacy_record(limits={"max_consecutive_losses": 5}))
    resolved = rl.resolve_risk_limits(tmp_path, now=now)
    assert resolved.source == guards.SOURCE_REGISTERED and resolved.max_consecutive_losses == 5
    status = rl.limits_status(tmp_path, now=now)
    assert status["valid"] is True and (status["valid_from"], status["valid_until"]) == (FROM, UNTIL)


@pytest.mark.parametrize("now", ["2026-07-29T23:59:59Z", "2026-08-30T00:00:01Z", "2099-12-31T23:59:59Z"])
def test_a_legacy_record_outside_its_window_still_refuses(tmp_path, now):
    """Before it opens and after it closes. CRYPTO_RISK_LIMITS_EXPIRED stays live: this is the
    revival the rule exists to refuse."""
    _write_raw(tmp_path, _legacy_record())
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now=now)
    assert exc.value.reason_code == rl.LIMITS_EXPIRED
    status = rl.limits_status(tmp_path, now=now)
    assert status["valid"] is False and status["error"] == rl.LIMITS_EXPIRED
    assert status["effective"] is None


@pytest.mark.parametrize("shape", [
    pytest.param({"drop": ("valid_until",)}, id="only_valid_from"),
    pytest.param({"drop": ("valid_from",)}, id="only_valid_until"),
    pytest.param({"valid_from": UNTIL, "valid_until": FROM}, id="closes_before_it_opens"),
    pytest.param({"valid_from": FROM, "valid_until": FROM}, id="never_opens"),
])
def test_half_a_window_or_one_that_never_opens_reads_invalid(tmp_path, shape):
    """Fail closed, on read. The record verifies and is schema-valid (both ends are optional
    there, and a schema cannot order them), so the read-side check is the only thing between
    this shape and a breaker. Half a window must not read as no window and stand forever."""
    record = _legacy_record(**shape)
    rl._validate(record)
    _write_raw(tmp_path, record)
    with pytest.raises(ToolError) as exc:
        rl.read_registered_limits(tmp_path)
    assert exc.value.reason_code == rl.LIMITS_INVALID
    for now in (FROM, "2026-08-15T00:00:00Z", "2099-01-01T00:00:00Z"):
        with pytest.raises(ToolError) as exc:
            rl.resolve_risk_limits(tmp_path, now=now)
        assert exc.value.reason_code == rl.LIMITS_INVALID
        status = rl.limits_status(tmp_path, now=now)          # reports, never raises
        assert status["registered"] is True and status["valid"] is False
        assert status["error"] == rl.LIMITS_INVALID


def _write_shape(root, shape):
    if shape == "windowless":
        _register(root, limits={"max_consecutive_losses": 5})
    elif shape == "legacy_inside_its_window":
        _write_raw(root, _legacy_record(limits={"max_consecutive_losses": 5}))
    elif shape == "legacy_past_its_window":
        _write_raw(root, _legacy_record(valid_from="2026-06-01T00:00:00Z", valid_until="2026-07-01T00:00:00Z"))
    elif shape == "legacy_with_half_a_window":
        _write_raw(root, _legacy_record(drop=("valid_until",)))
    elif shape == "tampered":
        _register(root)
        data = json.loads(rl.limits_path(root).read_text(encoding="utf-8"))
        data["limits"]["max_consecutive_losses"] = 10       # hand-loosened, hash left alone
        rl.limits_path(root).write_text(json.dumps(data), encoding="utf-8")
    elif shape == "out_of_bounds":
        # Schema-valid and verifying, but incoherent: daily looser than weekly (a cross-field
        # bound only the code enforces). No window, so only the bounds can refuse it.
        _write_raw(root, _legacy_record(drop=("valid_from", "valid_until"),
                                        limits={"daily_max_loss_r": -5.5, "weekly_max_loss_r": -5.0}))
    elif shape == "legacy_past_its_window_and_out_of_bounds":
        _write_raw(root, _legacy_record(valid_from="2026-06-01T00:00:00Z", valid_until="2026-07-01T00:00:00Z",
                                        limits={"daily_max_loss_r": -5.5, "weekly_max_loss_r": -5.0}))
    elif shape == "unreadable":
        state_dir(root).mkdir(parents=True, exist_ok=True)
        rl.limits_path(root).write_text("{not json", encoding="utf-8")
    else:
        assert shape == "nothing_registered"


@pytest.mark.parametrize(("shape", "expected_error"), [
    ("nothing_registered", None),
    ("windowless", None),
    ("legacy_inside_its_window", None),
    ("legacy_past_its_window", rl.LIMITS_EXPIRED),
    ("legacy_with_half_a_window", rl.LIMITS_INVALID),
    ("tampered", rl.LIMITS_TAMPERED),
    ("out_of_bounds", rl.LIMITS_INVALID),
    ("legacy_past_its_window_and_out_of_bounds", rl.LIMITS_EXPIRED),
    ("unreadable", rl.LIMITS_UNREADABLE),
])
def test_limits_status_says_what_resolve_risk_limits_does(tmp_path, shape, expected_error):
    """The board, ``--show``, the cycle, ``breaker_watch`` and the probe must not disagree about
    one file. ``valid`` is whether the resolver returns, ``error`` the code it raises, and
    ``effective`` the very numbers it returns — for every shape a record on disk can have."""
    _write_shape(tmp_path, shape)
    try:
        resolved, raised = rl.resolve_risk_limits(tmp_path, now=NOW).as_record(), None
    except ToolError as exc:
        resolved, raised = None, exc.reason_code
    assert raised == expected_error
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["valid"] is (raised is None)
    assert status["error"] == raised
    assert status["effective"] == resolved
    assert status["registered"] is (shape != "nothing_registered")


def test_the_schema_still_declares_the_legacy_window_and_no_longer_requires_it():
    schema = json.loads((_repo_root() / "schemas" / rl.RISK_LIMITS_SCHEMA_FILE).read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    for field in ("valid_from", "valid_until"):
        assert field in schema["properties"], f"{field} dropped: every legacy record turns schema-invalid"
        assert field not in schema["required"]
    rl._validate(_legacy_record())                      # the legacy shape, with its window
    rl._validate(_build())                              # the shape built today, without one


def test_stripping_the_window_from_a_legacy_record_breaks_its_hash(tmp_path):
    """Why nothing migrates a legacy record to the windowless shape: the window is inside the
    self-hash, so the only way to shed it is to register a new record."""
    record = _legacy_record()
    del record["valid_from"], record["valid_until"]
    _write_raw(tmp_path, record)
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now=NOW)
    assert exc.value.reason_code == rl.LIMITS_TAMPERED
    assert rl.limits_status(tmp_path, now=NOW)["error"] == rl.LIMITS_TAMPERED


# --- the registration CLI -------------------------------------------------------------------

def test_the_register_script_refuses_the_retired_valid_days_flag_and_writes_nothing(tmp_path, monkeypatch,
                                                                                    capsys):
    """An old invocation fails closed: argparse exits 2 before anything is built or written."""
    import importlib

    reg = importlib.import_module("scripts.register_crypto_risk_limits")
    written: list[object] = []
    monkeypatch.setattr(reg.risk_limits, "write_registered_limits", lambda *a, **k: written.append(1))
    with pytest.raises(SystemExit) as exc:
        reg.main(["--registered-by", "thomas", "--root", str(tmp_path),
                  "--max-consecutive-losses", "4", "--valid-days", "3"])
    assert exc.value.code == 2
    assert written == []
    assert "--valid-days" in capsys.readouterr().err
    assert not rl.limits_path(tmp_path).exists()


def test_the_register_script_writes_a_windowless_record_and_reports_it(tmp_path, monkeypatch, capsys):
    """The post-write report used to subscript the window AFTER the record had landed, so a
    windowless record would have been written and then reported as a traceback."""
    import importlib

    reg = importlib.import_module("scripts.register_crypto_risk_limits")
    monkeypatch.setattr(reg, "assert_not_foreign_root_run", lambda root=None, **kw: None)
    assert reg.main(["--registered-by", "thomas", "--root", str(tmp_path), "--max-consecutive-losses", "5"]) == 0
    record = rl.read_registered_limits(tmp_path)
    assert "valid_from" not in record and "valid_until" not in record
    out = capsys.readouterr().out
    assert f"registered crypto risk limits {record['limits_id']}" in out
    assert "changed:  max_consecutive_losses" in out
    assert "expiry:   none" in out and "valid:" not in out
    for now in ("2000-01-01T00:00:00Z", "2099-12-31T23:59:59Z"):
        assert rl.resolve_risk_limits(tmp_path, now=now).max_consecutive_losses == 5


@pytest.mark.parametrize(("valid_until", "valid", "error"), [
    ("2099-01-01T00:00:00Z", "True", None),
    ("2026-02-01T00:00:00Z", "False", rl.LIMITS_EXPIRED),
], ids=["inside_its_window", "past_its_window"])
def test_show_prints_a_legacy_record_with_its_registration_and_window(tmp_path, capsys, valid_until, valid,
                                                                      error):
    import importlib

    reg = importlib.import_module("scripts.register_crypto_risk_limits")
    _write_raw(tmp_path, _legacy_record(valid_from="2026-01-01T00:00:00Z", valid_until=valid_until))
    assert reg.main(["--show", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"by thomas at {FROM}" in out
    assert f"valid:       {valid} (legacy window 2026-01-01T00:00:00Z .. {valid_until})" in out
    if error:
        assert f"error:       {error}" in out and "effective:" not in out
    else:
        assert "effective:   risk/trade 0.01, daily -2.0R" in out and "error:" not in out


def test_show_prints_a_windowless_record_and_its_rebase(tmp_path, capsys):
    import importlib

    reg = importlib.import_module("scripts.register_crypto_risk_limits")
    _register(tmp_path, drawdown_baseline_rebase={"excluded_strategy_ids": ["cand:c3", "cand:c9"], "reason": "r"})
    before = rl.limits_path(tmp_path).read_bytes()
    assert reg.main(["--show", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert f"by thomas at {NOW}" in out
    assert "valid:       True (no expiry" in out and "legacy window" not in out
    assert "rebase:      the drawdown baseline excludes 2 lineage key(s)" in out
    assert rl.limits_path(tmp_path).read_bytes() == before, "--show registers nothing"


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
        "excluded_strategy_ids": ["cand:c9", "cand:c3"], "reason": "15m pool retired 2026-07-31",
    })
    resolved = rl.resolve_risk_limits(tmp_path, now=NOW)
    assert resolved.drawdown_excluded_strategy_ids == ("cand:c3", "cand:c9")   # sorted at build
    assert resolved.as_record()["drawdown_excluded_strategy_ids"] == ["cand:c3", "cand:c9"]


def test_a_rebase_without_a_reason_is_refused():
    """A rebase is a mechanism for forgetting losses. A record that cannot say whose and why
    leaves a ledger nobody can re-read."""
    with pytest.raises(ToolError) as exc:
        _build(drawdown_baseline_rebase={"excluded_strategy_ids": ["cand:c1"], "reason": "  "})
    assert exc.value.reason_code == rl.LIMITS_INVALID and "reason is required" in str(exc.value)


# Each case names the check that refuses it: every one raises LIMITS_INVALID, and a later check
# refusing in an earlier one's place would hide that one (review of PR3b-3 — the display-id
# refusal did, while these cases named display ids).
@pytest.mark.parametrize("block,message", [
    ({"excluded_strategy_ids": [], "reason": "empty"}, "must be a non-empty list"),
    ({"excluded_strategy_ids": ["cand:c1", "cand:c1"], "reason": "duplicated"}, "contains duplicates"),
    ({"excluded_strategy_ids": ["", "cand:c1"], "reason": "blank id"}, "must be a non-empty list"),
    ({"excluded_strategy_ids": "cand:c1", "reason": "not a list"}, "must be a non-empty list"),
    ({"excluded_strategy_ids": ["S1"], "reason": "a display id"}, "these are not lineage keys: S1"),
])
def test_a_malformed_rebase_block_is_refused(block, message):
    with pytest.raises(ToolError) as exc:
        _build(drawdown_baseline_rebase=block)
    assert exc.value.reason_code == rl.LIMITS_INVALID and message in str(exc.value)


def test_the_rebase_block_is_covered_by_the_records_self_hash(tmp_path):
    """Tamper evidence, which is the reason to reuse this record rather than invent one:
    editing the exclusion list after registration invalidates the hash and fails closed."""
    record = _register(tmp_path, drawdown_baseline_rebase={
        "excluded_strategy_ids": ["cand:c3"], "reason": "retired",
    })
    tampered = {**record}
    tampered["drawdown_baseline_rebase"] = {
        "excluded_strategy_ids": ["cand:c3", "cand:c4"], "reason": "retired",
    }
    rl.limits_path(tmp_path).write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ToolError):
        rl.resolve_risk_limits(tmp_path, now=NOW)


def test_the_rebase_block_does_not_rename_the_record():
    """`limits_id` identifies the NUMBERS. Two records with the same breakers over different
    retirement sets are the same limits applied to different populations, so the id is stable
    and the self-hash — which does cover the block — is what distinguishes them."""
    plain = _build()
    rebased = _build(drawdown_baseline_rebase={"excluded_strategy_ids": ["cand:c3"], "reason": "r"})
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

# The poisons a hand edit can put in a record that no hash can be computed over: a NaN (the JSON
# token is accepted by the parser, refused by the canonical hash), a secret-shaped key (refused by
# the secret scan), and pathological nesting.
_UNHASHABLE = {
    "nan": '{"limits": {"daily_max_loss_r": NaN}, "record_sha256": "sha256:0"}',
    "secret_key": '{"api_secret": "x", "record_sha256": "sha256:0"}',
    "deep_nesting": "[" * 5000 + "]" * 5000,
}


@pytest.mark.parametrize("poison", list(_UNHASHABLE))
def test_an_unhashable_record_is_a_typed_refusal_in_every_reader(tmp_path, poison):
    """Review of #873: a bare ValueError / IntegrityError escaped `resolve_risk_limits`, and the
    cycle only catches the typed refusal — so the cycle aborted before the live leg could manage
    open positions."""
    path = rl.limits_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_UNHASHABLE[poison], encoding="utf-8")
    with pytest.raises(ToolError) as exc:
        rl.resolve_risk_limits(tmp_path, now=NOW)
    assert exc.value.reason_code in (rl.LIMITS_UNREADABLE, rl.LIMITS_TAMPERED)
    status = rl.limits_status(tmp_path, now=NOW)
    assert status["valid"] is False and status["error"] in (rl.LIMITS_UNREADABLE, rl.LIMITS_TAMPERED)
