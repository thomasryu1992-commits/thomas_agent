"""§8.5 — routing to more than one activated Role.

`research.general` and `translation.general` were activated by explicit Thomas decision on
2026-07-27. Activation alone routes nothing: every task asked for the capabilities only
`general.specialist` has, so the new Roles were reachable by nobody. These tests pin the two
halves that make activation mean something — a request kind selects capabilities, and the
selected Role runs against *its own* output contract rather than the business analyst's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import pipeline
from runtime.mvp_runtime.errors import PlannerBlocked
from runtime.mvp_runtime.intake import build_task
from runtime.mvp_runtime.paths import repo_root
from runtime.mvp_runtime.planner import (
    DEFAULT_SPECIALIST_ROLE_ID,
    REQUEST_KIND_ANALYSIS,
    REQUEST_KIND_CAPABILITIES,
    capabilities_for_request_kind,
    classify_task,
    load_resolved_roles,
    role_output_spec,
    select_role,
)

REPO = Path(repo_root())
NOW = "2026-07-27T09:00:00Z"
ACTIVATED = ("research.general", "translation.general")


def _task(**overrides):
    return build_task(raw_request="샘플 요청입니다.", now=NOW, **overrides)


# --- the activation itself ---------------------------------------------------

def _registry():
    return yaml.safe_load((REPO / "03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("role_id", ACTIVATED)
def test_the_activated_roles_are_active_and_routable(role_id):
    entry = next(r for r in _registry()["roles"] if r["role_id"] == role_id)
    assert (entry["status"], entry["routable"]) == ("active", True)


def test_the_live_trading_role_was_not_swept_in():
    """The approval was for two P3 read-only specialists. `execution.live_trader` is P5 with
    `external_action_allowed: true` — activating it is a live-trading decision with canaries and
    per-machine grants behind it, and must never ride along on a routing change."""
    entry = next(r for r in _registry()["roles"] if r["role_id"] == "execution.live_trader")
    assert (entry["status"], entry["routable"]) == ("candidate", False)


def test_activation_widened_no_authority():
    """A status flip must not have carried a permission change with it."""
    for role_id in ACTIVATED:
        role = next(r for r in load_resolved_roles(REPO)["roles"] if r["role_id"] == role_id)
        assert role["permission_ceiling"] == "P3", role_id


# --- request kind -> capabilities -> exactly one Role ------------------------

def test_every_request_kind_resolves_to_exactly_one_role():
    """The invariant that keeps this table honest against the LIVE registry.

    `select_role` fails closed on both sides — no Role covering the set (NO_ROUTABLE_ROLE) and
    more than one (AMBIGUOUS_ROLE). Activating a Role whose capabilities overlap an existing
    kind therefore breaks here, in CI, rather than at run time on a real request."""
    resolved = load_resolved_roles(REPO)
    selected = {}
    for kind, capabilities in REQUEST_KIND_CAPABILITIES.items():
        role = select_role(resolved, required_capabilities=capabilities,
                           required_permission_level="P2")
        selected[kind] = role["role_id"]
    assert selected == {
        REQUEST_KIND_ANALYSIS: DEFAULT_SPECIALIST_ROLE_ID,
        "research": "research.general",
        "translation": "translation.general",
    }


def test_no_kind_is_the_behavior_the_runtime_already_had():
    decision = classify_task(_task())
    assert decision["required_capabilities"] == ["research", "analysis"]
    assert decision["request_kind"] == REQUEST_KIND_ANALYSIS
    assert "request_kind_analysis" not in decision["classification"]["classification_reasons"]


def test_a_kind_changes_the_capabilities_and_says_so_in_the_record():
    decision = classify_task(_task(), request_kind="translation")
    assert decision["required_capabilities"] == ["translation", "ambiguity_disclosure"]
    assert "request_kind_translation" in decision["classification"]["classification_reasons"]


def test_an_unknown_kind_is_refused_rather_than_analyzed():
    """The fail-closed direction that matters: silently analyzing a request someone asked to
    have translated is a wrong answer delivered confidently."""
    with pytest.raises(PlannerBlocked) as exc:
        capabilities_for_request_kind("summarise")
    assert exc.value.reason_code == "UNKNOWN_REQUEST_KIND"
    with pytest.raises(PlannerBlocked):
        classify_task(_task(), request_kind="summarise")


def test_a_kind_names_capabilities_never_a_role():
    """The layering: the Registry decides which Role covers a capability set. A role id in this
    table would let a routing change bypass the registry that is supposed to own it."""
    role_ids = {r["role_id"] for r in load_resolved_roles(REPO)["roles"]}
    for capabilities in REQUEST_KIND_CAPABILITIES.values():
        assert not (set(capabilities) & role_ids)


# --- the selected Role runs against its own contract -------------------------

def _definition(role_id):
    role = next(r for r in load_resolved_roles(REPO)["roles"] if r["role_id"] == role_id)
    from runtime.mvp_runtime.planner import load_role_definition
    return load_role_definition(REPO, role)


def test_the_business_analyst_keeps_the_default_prompt_and_output_shape():
    """(None, None) means the worker's own defaults — so the business-analysis path, including
    §10.4 perspectives, is byte-identical to before §8.5."""
    plan = {"role_definition": _definition(DEFAULT_SPECIALIST_ROLE_ID),
            "task": {}, "role_assignment": {}}
    assert pipeline._role_execution(plan) == (None, None)


@pytest.mark.parametrize("role_id", ACTIVATED)
def test_another_role_runs_against_its_own_declared_contract(role_id):
    """The half that makes activation real. Without it a translation request would be routed to
    translation.general and then asked for revenue potential, and validated for key findings it
    never promised."""
    definition = _definition(role_id)
    plan = {
        "role_definition": definition,
        "task": {"scope": {"primary_objective": "obj"},
                 "request": {"raw_request": "번역해줘"},
                 "context": {"active_core_rule_ids": ["MVP_RULE_005"]}},
        "role_assignment": {"role_scope": {"assigned_capabilities": ["translation"],
                                           "role_objective": "obj"}},
    }
    prompt, keys = pipeline._role_execution(plan)

    assert set(keys) == set(role_output_spec(definition))
    assert role_id in prompt
    # Not the business-analysis prompt: no revenue priorities, no §10.4 perspectives.
    assert "revenue_potential" not in prompt
    assert "perspectives" not in prompt
    # And not the trial prompt either — a routed run is not isolated and must not claim to be.
    assert "no tools, no web access" not in prompt


def test_the_perspective_check_does_not_apply_to_a_role_that_never_promised_it():
    """§10.4's check is scoped by the role contract. `translation.general` declares no
    `perspectives`, so its keys drive validation instead — asserted here because the two
    features landed one after the other and this is exactly where they could collide."""
    assert "perspectives" not in role_output_spec(_definition("translation.general"))
    assert "perspectives" in role_output_spec(_definition(DEFAULT_SPECIALIST_ROLE_ID))


# --- the entry point ---------------------------------------------------------

def test_the_cli_parses_the_kind_and_leaves_the_request_intact():
    from runtime.mvp_runtime.cli import _extract_request_kind

    kind, rest, err = _extract_request_kind(["--kind", "translation", "번역해줘", "please"])
    assert (kind, rest, err) == ("translation", ["번역해줘", "please"], None)

    assert _extract_request_kind(["아무거나"]) == (None, ["아무거나"], None)


def test_the_cli_refuses_a_kind_with_no_value():
    from runtime.mvp_runtime.cli import _extract_request_kind

    assert _extract_request_kind(["--kind"])[2] is not None
    assert _extract_request_kind(["--kind", "--important"])[2] is not None


def test_the_cli_does_not_keep_its_own_list_of_kinds():
    """An unknown kind must fail in planning, where the Registry decides — not in an argv
    parser holding a second copy of the vocabulary that could disagree with it."""
    from runtime.mvp_runtime.cli import _extract_request_kind

    kind, _, err = _extract_request_kind(["--kind", "not_a_kind", "x"])
    assert (kind, err) == ("not_a_kind", None)


# --- the provider has to be able to answer the Role's contract ---------------

class _NetworkProvider:
    model_id = "hosted.example"
    model_version = "1"
    network_egress = True

    def generate(self, prompt, *, max_output_tokens, timeout_seconds):  # pragma: no cover
        raise AssertionError("must not be reached")


def test_the_business_analyst_keeps_whatever_provider_it_was_given():
    from runtime.mvp_runtime.worker import MockProvider

    provider = MockProvider()
    assert pipeline._provider_for_role(provider, None) is provider
    hosted = _NetworkProvider()
    assert pipeline._provider_for_role(hosted, None) is hosted


def test_a_routed_role_gets_a_provider_shaped_to_its_contract():
    from runtime.mvp_runtime.worker import MockProvider, MockTrialProvider

    spec = role_output_spec(_definition("translation.general"))
    chosen = pipeline._provider_for_role(MockProvider(), spec)

    assert isinstance(chosen, MockTrialProvider)
    analysis = chosen.generate("p", max_output_tokens=100, timeout_seconds=5).analysis
    assert set(spec).issubset(analysis)


def test_a_hosted_provider_refuses_by_name_instead_of_producing_a_confusing_revise():
    """The hosted response schemas are strict (`additionalProperties: false`) and name only the
    business-analysis keys, so a hosted model *cannot* return `translated_text`. Left alone the
    run would come back missing the key and fail its own output check — reading like a model
    quality problem when it is a transport contract problem. Making the schema role-aware is
    the remaining piece of §8.5; until then this refuses at the door."""
    from runtime.mvp_runtime.errors import WorkerBlocked

    spec = role_output_spec(_definition("translation.general"))
    with pytest.raises(WorkerBlocked) as exc:
        pipeline._provider_for_role(_NetworkProvider(), spec)
    assert exc.value.reason_code == "ROLE_OUTPUT_CONTRACT_UNSUPPORTED_BY_PROVIDER"
    assert "translated_text" in exc.value.reason


def test_the_hosted_schemas_really_do_name_only_the_analysis_keys():
    """Pins the premise of the refusal above. If a future change makes the response schema
    role-aware, this fails and the refusal should be revisited rather than left in place."""
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA

    assert _ANALYSIS_JSON_SCHEMA["additionalProperties"] is False
    assert "translated_text" not in _ANALYSIS_JSON_SCHEMA["properties"]
