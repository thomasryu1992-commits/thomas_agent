"""Safety-Flag Gate tests — the enforced model/network chokepoint, env-only era.

Every path is fail-closed: without the exact environment opt-in the inert default is
selected and the capable implementation is never constructed; the egress re-check
re-reads the environment. The grant-record machinery and its tests were removed with it
(2026-08-30); the containment sweep below still watches for the retired names so a
reintroduction is a named decision. No network and no secrets are involved.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime.errors import SafetyGateBlocked
from runtime.mvp_runtime.safety_gate import (
    MODEL_INVOCATION,
    NETWORK_ACCESS,
    Authorization,
    assert_authorization,
    env_only_authorization,
    select_env_gated,
)

NOW = "2026-07-15T00:00:00Z"
PROVIDER = "google_ai_studio"
FLAGS = (MODEL_INVOCATION, NETWORK_ACCESS)


# --- assert_authorization(): egress re-check --------------------------------

def test_assert_rejects_non_authorization():
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(None, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "NOT_AUTHORIZED"


def test_assert_rejects_expired_since_grant():
    auth = Authorization(flags=FLAGS, provider_id=PROVIDER, activation_sha256="sha256:x",
                         expires_at="2026-07-14T00:00:00Z", evidence_ref="ev.md")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "ACTIVATION_EXPIRED"


def test_assert_rejects_wrong_provider():
    auth = Authorization(flags=FLAGS, provider_id="other", activation_sha256="sha256:x",
                         expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)
    assert exc.value.reason_code == "PROVIDER_NOT_AUTHORIZED"


def test_assert_passes_for_valid_grant():
    auth = Authorization(flags=FLAGS, provider_id=PROVIDER, activation_sha256="sha256:x",
                         expires_at="2099-01-01T00:00:00Z", evidence_ref="ev.md")
    assert_authorization(auth, required_flags=FLAGS, provider_id=PROVIDER, now=NOW)  # no raise


# --- select_env_gated: the live-trading exception ----------------------------------
#
# Thomas removed the per-machine grant for live trading on 2026-07-28: `MVP_LIVE_TRADING=real`
# is now the whole gate for that one capability. These tests pin BOTH halves of that decision —
# that the env alone opens it, and that nothing else can reach the weaker door.


def test_env_gated_returns_the_inert_default_without_the_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv("PROBE_GATE_ENV", raising=False)
    built = []
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: built.append(auth) or "capable",
    )
    assert got == "inert"
    assert built == []


@pytest.mark.parametrize("value", ["", "   ", "something-else", "REALLY", "true", "1"])
def test_env_gated_opens_for_the_exact_value_and_nothing_near_it(value, monkeypatch, tmp_path):
    """Dropping the grant makes this string the entire gate, so 'close enough' must not open
    it. `true` and `1` are in the list on purpose: they are what someone reaches for when they
    believe they are setting a boolean."""
    monkeypatch.setenv("PROBE_GATE_ENV", value)
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: "capable",
    )
    assert got == "inert"


def test_env_gated_opens_with_no_activation_record_anywhere(monkeypatch, tmp_path):
    """The point of the change, stated as a test: the opt-in alone builds the capable thing.
    `tmp_path` holds no activations directory at all, and this must still succeed — under
    `select_gated` the identical call raised ACTIVATION_MISSING."""
    monkeypatch.setenv("PROBE_GATE_ENV", "  REAL  ")
    got = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: "inert",
        gated_factory=lambda auth: auth,
    )
    assert isinstance(got, Authorization)
    assert got.provider_id == "probe"
    assert got.flags == (NETWORK_ACCESS,)


def test_env_gated_authorization_still_re_checks_the_env_at_egress(monkeypatch, tmp_path):
    """Defense in depth survives the change in weakened form. The grant file was re-read at
    every egress so deleting it revoked mid-flight; there is no file now, so the re-check
    re-reads the env. Same shape, and it still fails closed."""
    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    auth = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: None,
        gated_factory=lambda a: a,
    )
    assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="probe", now=NOW)

    monkeypatch.setenv("PROBE_GATE_ENV", "no")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="probe", now=NOW)
    assert exc.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


def test_env_gated_authorization_is_still_bound_to_its_provider_and_flags(monkeypatch):
    """Removing the grant removed one requirement, not the others. An env-only authorization
    must not become a skeleton key: wrong provider and insufficient flags still block."""
    monkeypatch.setenv("PROBE_GATE_ENV", "real")
    auth = select_env_gated(
        env_var="PROBE_GATE_ENV", opt_in_value="real", flags=(NETWORK_ACCESS,),
        provider_id="probe",
        default_factory=lambda: None,
        gated_factory=lambda a: a,
    )
    with pytest.raises(SafetyGateBlocked) as wrong_provider:
        assert_authorization(auth, required_flags=(NETWORK_ACCESS,), provider_id="other", now=NOW)
    assert wrong_provider.value.reason_code == "PROVIDER_NOT_AUTHORIZED"
    with pytest.raises(SafetyGateBlocked) as missing_flag:
        assert_authorization(
            auth, required_flags=(NETWORK_ACCESS, MODEL_INVOCATION), provider_id="probe", now=NOW
        )
    assert missing_flag.value.reason_code == "FLAG_NOT_ENABLED"


def test_the_env_only_gate_has_exactly_the_capabilities_thomas_named():
    """The containment test, inverted on 2026-08-10 without changing its job.

    It was written when env-only was the exception: Thomas had relaxed the gate for three
    capabilities (live trading 2026-07-28, the candle archive 2026-08-04, the Naver lane
    2026-08-09) and every further relaxation had to be added HERE, on purpose. On
    2026-08-10 Thomas retired per-machine grants and their renewal outright, so env-only
    is now the rule — and the test's job flips accordingly, in both directions:

    - the RETIRED sweep must stay empty. The machinery itself was removed on 2026-08-30,
      so today a hit means someone REINTRODUCED a name from the grant era (a vendored
      copy, a new definition, an import that would crash) — restoring grants is a
      deliberate, named decision or nothing;
    - the env-only caller list stays exact: a NEW capability still cannot slip onto the
      gate unreviewed — adding one still means editing this list, which is the decision
      surface this test has always existed to create.

    Both spellings are collected, and that is not defensive padding. The first version matched
    only `ast.Attribute` — `safety_gate.select_env_gated(...)` — so a caller written
    `from ..safety_gate import select_env_gated` and then called bare is an `ast.Name` and scored
    ZERO. That import style is the idiomatic one in this repo. A containment test the common
    idiom walks straight through contains nothing.

    `env_only_authorization` is pinned by the same sweep, because it is public and a caller can
    build one directly and hand it to any capable constructor without naming `select_env_gated`
    at all — the same weaker door reached by a different handle.
    """
    import ast
    import pathlib

    watched = {
        "select_env_gated", "select_env_gated_optional", "select_env_gated_chain",
        "env_only_authorization",
    }
    retired = {"select_gated", "select_gated_optional", "select_gated_chain", "authorize"}
    repo = pathlib.Path(__file__).resolve().parents[1]

    def hits(node: object, names: set) -> bool:
        return (
            (isinstance(node, ast.Attribute) and node.attr in names)
            or (isinstance(node, ast.Name) and node.id in names)
            or (isinstance(node, ast.ImportFrom)
                and any(a.name in names for a in node.names))
        )

    callers = set()
    grant_callers = set()
    for path in (repo / "runtime").rglob("*.py"):
        if path.name == "safety_gate.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if hits(node, watched):
                callers.add(path.relative_to(repo).as_posix())
            if hits(node, retired):
                grant_callers.add(path.relative_to(repo).as_posix())
    assert grant_callers == set(), (
        "the grant-backed selectors are retired (Thomas 2026-08-10); a new caller "
        f"reintroduces a renewal requirement and must be a named decision: {sorted(grant_callers)}"
    )
    assert callers == {
        # The three that left grants one at a time, each with its full reasoning kept at
        # the call site: live trading (2026-07-28 — a grant expiring while a position is
        # OPEN blocks the CLOSE path, and a halt that traps a position is what the close
        # exemptions exist to prevent), the candle archive (2026-08-04) and the Naver lane
        # (2026-08-09 — a renewal gap is a silent hole in a rolling collection).
        "runtime/mvp_runtime/crypto/live_execution.py",   # the order adapter
        "runtime/mvp_runtime/crypto/live_pnl.py",         # the realized-P&L ledger
        "runtime/mvp_runtime/crypto/live_position.py",    # the position book
        "runtime/mvp_runtime/crypto/live_order.py",       # the daily submission counter
        "runtime/mvp_runtime/crypto/live_promotion.py",   # the canary evidence registry
        "runtime/mvp_runtime/crypto/market_data.py",      # candle archive + market data + liquidation feed
        "runtime/mvp_runtime/naver_research.py",          # the blog content lane's research tools
        # Everything below moved on 2026-08-10 in one decision — Thomas retired grant
        # renewal for the rest of the roster ("다 지속적으로 돌릴 것들인데", the renewal
        # burden bought too little on capabilities meant to run indefinitely). Listed per
        # call site so the sweep still forces the NEXT capability to be named on purpose.
        "runtime/mvp_runtime/providers.py",               # hosted model chain + validator + M2 tiers
        "runtime/mvp_runtime/frontdesk.py",               # the conversational front desk's chain
        "runtime/mvp_runtime/tools.py",                   # the search tool
        "runtime/mvp_runtime/operator.py",                # the Telegram operator channel
        "runtime/mvp_runtime/workspace.py",               # the workspace writer
        "runtime/mvp_runtime/consumption.py",             # approval consumption
        "runtime/mvp_runtime/trial.py",                   # the candidate-trial spend
        "runtime/mvp_runtime/crypto/account.py",          # the venue account feed
        "runtime/mvp_runtime/crypto/paper.py",            # the durable paper store
    }, callers


def test_the_containment_sweep_sees_a_bare_name_call(tmp_path):
    """The sweep's own blind spot, checked directly rather than trusted.

    Written as a unit on the matcher because the real test asserts an exact caller set — it can
    only fail on a NEW caller, so nothing in it would notice that the matcher had stopped seeing
    a whole calling convention. This is the assertion that would have caught it."""
    import ast

    watched = {"select_env_gated", "env_only_authorization"}
    module = ast.parse(
        "from ..safety_gate import select_env_gated\n"
        "def pick():\n"
        "    return select_env_gated(env_var='X', opt_in_value='real', flags=(),\n"
        "                            provider_id='p', default_factory=None, gated_factory=None)\n"
    )
    hits = [
        node for node in ast.walk(module)
        if (isinstance(node, ast.Attribute) and node.attr in watched)
        or (isinstance(node, ast.Name) and node.id in watched)
        or (isinstance(node, ast.ImportFrom) and any(a.name in watched for a in node.names))
    ]
    assert hits, "a `from ... import select_env_gated` caller must not be invisible to the sweep"


def test_the_suite_isolates_every_gate_opt_in_env_var():
    """`tests/conftest.py`'s `_GATE_ENV_VARS` is the suite's whole defense against inheriting
    the operator's gate opt-ins, and it was maintained by hand — one entry per remembered
    capability. A verified probe (2026-08-09) showed what that is worth: with the operator's
    opt-ins exported, every listed var read as None while MVP_CANDLE_ARCHIVE='hyperliquid'
    and MVP_MARKET_DATA='binance_futures' leaked straight through. The archive is env-only,
    so the leak hands any test that reaches its selector a REAL egress-capable collector
    holding a genuine authorization; the grant-gated vars contain nothing on the machine
    that matters either, because the operator's machine is exactly where the grants exist.

    So the list's floor is derived from the selector call sites themselves: every
    ``env_var=`` handed to ``select_gated`` / ``select_env_gated`` / ``select_gated_chain``
    anywhere in ``runtime/`` must appear in ``_GATE_ENV_VARS``. A new gated capability whose
    author forgets the conftest entry fails here instead of shipping the same hole again.

    Subset, not equality, on purpose: a conftest entry for a retired capability is a
    harmless no-op delenv, and requiring equality would couple removing a capability to a
    test that exists for the opposite direction.

    Same fail-closed posture as the callers sweep above: an ``env_var=`` this sweep cannot
    resolve to a string is an assertion failure, never a silent skip."""
    import ast
    import pathlib

    from tests import conftest as suite_conftest

    selectors = {"select_gated", "select_env_gated", "select_gated_chain",
                 "select_env_gated_chain", "select_env_gated_optional"}
    repo = pathlib.Path(__file__).resolve().parents[1]
    modules = {
        path: ast.parse(path.read_text(encoding="utf-8"))
        for path in (repo / "runtime").rglob("*.py")
    }

    def own_constants(tree: ast.Module) -> dict[str, str]:
        # Module-level `NAME = "literal"` — the repo's idiom for env var names.
        return {
            target.id: node.value.value
            for node in tree.body if isinstance(node, ast.Assign)
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            for target in node.targets if isinstance(target, ast.Name)
        }

    constants = {path: own_constants(tree) for path, tree in modules.items()}

    def imported_constants(path: pathlib.Path, tree: ast.Module) -> dict[str, str]:
        # One `from <module> import NAME` hop — `trial.py` borrows consumption's ENV_VAR and
        # the live surface shares `live_pnl`'s LIVE_TRADING_ENV this way.
        resolved: dict[str, str] = {}
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom) or node.module is None:
                continue
            base = path.parents[node.level - 1] if node.level else repo
            source = base.joinpath(*node.module.split(".")).with_suffix(".py")
            source_constants = constants.get(source, {})
            for alias in node.names:
                if alias.name in source_constants:
                    resolved[alias.asname or alias.name] = source_constants[alias.name]
        return resolved

    opt_ins: dict[str, str] = {}
    for path, tree in modules.items():
        if path.name == "safety_gate.py":
            continue  # the selectors' own bodies pass env_var through; they are not opt-ins
        names = {**imported_constants(path, tree), **constants[path]}
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not ((isinstance(func, ast.Attribute) and func.attr in selectors)
                    or (isinstance(func, ast.Name) and func.id in selectors)):
                continue
            site = f"{path.relative_to(repo).as_posix()}:{node.lineno}"
            for keyword in node.keywords:
                if keyword.arg != "env_var":
                    continue
                value = keyword.value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    opt_ins.setdefault(value.value, site)
                elif isinstance(value, ast.Name) and value.id in names:
                    opt_ins.setdefault(names[value.id], site)
                else:
                    raise AssertionError(
                        f"{site} passes env_var= in a form this sweep cannot resolve to a "
                        "string; use a literal or a module-level constant so the isolation "
                        "check can see the opt-in"
                    )

    for known in ("MVP_LIVE_TRADING", "MVP_CANDLE_ARCHIVE", "MVP_NAVER_RESEARCH",
                  "MVP_MARKET_DATA"):
        assert known in opt_ins, f"the sweep stopped seeing {known} — its matcher went blind"
    missing = {var: site for var, site in sorted(opt_ins.items())
               if var not in suite_conftest._GATE_ENV_VARS}
    assert not missing, (
        "gate opt-in env var(s) missing from tests/conftest.py _GATE_ENV_VARS — the suite "
        f"would inherit these from the operator's shell: {missing}"
    )


# --- the env-only gate cannot be reached from a grant record ----------------------

def test_the_env_gate_field_is_what_drives_the_recheck(monkeypatch):
    """...and it is set only by `env_only_authorization`, never by `authorize`."""
    authorization = env_only_authorization(
        flags=(NETWORK_ACCESS,), provider_id="p", env_var="MVP_X", opt_in_value="real",
    )
    assert authorization.env_gate == ("MVP_X", "real")

    monkeypatch.setenv("MVP_X", "real")
    assert_authorization(authorization, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)

    monkeypatch.setenv("MVP_X", "true")
    with pytest.raises(SafetyGateBlocked) as exc:
        assert_authorization(authorization, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)
    assert exc.value.reason_code == "ENV_OPT_IN_WITHDRAWN"


def test_the_opt_in_value_is_matched_case_insensitively_on_both_sides(monkeypatch):
    """Only the env value was normalised. A caller passing "REAL" would leave the gate shut
    forever with nothing saying why — and the egress re-check has to agree with the selector."""
    monkeypatch.setenv("MVP_X", "real")
    built = select_env_gated(
        env_var="MVP_X", opt_in_value="REAL", flags=(NETWORK_ACCESS,), provider_id="p",
        default_factory=lambda: "inert", gated_factory=lambda auth: auth,
    )
    assert built != "inert", "an uppercase opt_in_value must still open the gate"
    assert_authorization(built, required_flags=(NETWORK_ACCESS,), provider_id="p", now=NOW)
