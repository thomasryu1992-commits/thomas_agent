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
ACTIVATED = ("research.general", "translation.general",
             "content.general", "development.general")


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
        "content": "content.general",
        "development": "development.general",
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


# --- the hosted schema is role-aware (§8.5, second increment) ----------------

TRANSLATION_KEYS = {"translated_text", "terminology_notes", "ambiguity_notes"}


def _spec():
    return role_output_spec(_definition("translation.general"))


def test_the_unbound_schemas_are_untouched():
    """The module constants stay the analysis shape, so every existing caller — the validator,
    the triage, an ordinary analysis run — keeps the body it had."""
    from runtime.mvp_runtime.providers import (
        _ANALYSIS_JSON_SCHEMA, _ANALYSIS_RESPONSE_SCHEMA,
        analysis_json_schema, analysis_response_schema,
    )

    assert analysis_json_schema() is _ANALYSIS_JSON_SCHEMA
    assert analysis_response_schema() is _ANALYSIS_RESPONSE_SCHEMA
    assert analysis_json_schema({}) is _ANALYSIS_JSON_SCHEMA


def test_a_bound_schema_asks_for_the_roles_keys_in_both_dialects():
    from runtime.mvp_runtime.providers import analysis_json_schema, analysis_response_schema

    for schema in (analysis_json_schema(_spec()), analysis_response_schema(_spec())):
        assert TRANSLATION_KEYS.issubset(schema["properties"])
        assert TRANSLATION_KEYS.issubset(schema["required"])
        # The analysis keys are still there — a Role adds, it does not replace.
        assert {"summary", "facts", "perspectives"}.issubset(schema["required"])


def test_deriving_a_schema_does_not_mutate_the_shared_one():
    """A mutation would leak one Role's keys into every later run, including the validator's."""
    from runtime.mvp_runtime.providers import _ANALYSIS_JSON_SCHEMA, analysis_json_schema

    before = len(_ANALYSIS_JSON_SCHEMA["required"])
    analysis_json_schema(_spec())
    analysis_json_schema(_spec())
    assert len(_ANALYSIS_JSON_SCHEMA["required"]) == before
    assert not TRANSLATION_KEYS & set(_ANALYSIS_JSON_SCHEMA["properties"])


def test_binding_returns_a_copy_and_carries_the_authorization():
    """A copy, because the provider is selected once per process: a run binding a Role must not
    change what the next run asks for. Same Authorization object, so binding grants nothing and
    the egress check still runs against the real grant."""
    from runtime.mvp_runtime.providers import GoogleAIStudioProvider

    base = GoogleAIStudioProvider(authorization=None)
    bound = base.bind_role_output_keys(_spec())

    assert bound is not base
    assert base._role_output_spec is None
    assert set(bound._role_output_spec) == TRANSLATION_KEYS
    assert bound._authorization is base._authorization
    assert bound.model_id == base.model_id


def test_the_openrouter_body_carries_the_bound_keys():
    """OpenRouter is the strict one — `additionalProperties: false` is exactly what made the
    fold-in necessary rather than optional."""
    from runtime.mvp_runtime.providers import OpenRouterProvider

    bound = OpenRouterProvider(authorization=None).bind_role_output_keys(_spec())
    schema = bound._response_format()["json_schema"]["schema"]

    assert schema["additionalProperties"] is False
    assert TRANSLATION_KEYS.issubset(schema["required"])


def test_groq_needs_no_fold_in_and_says_why():
    """Groq constrains no keys (`json_object`), so a bound Role changes nothing about its body —
    the prompt already asks, and the vendor does not reject what it does not enforce."""
    from runtime.mvp_runtime.providers import GroqProvider

    bound = GroqProvider(authorization=None).bind_role_output_keys(_spec())
    assert bound._response_format() == {"type": "json_object"}


def test_a_failover_chain_binds_every_member():
    """Binding only the first would work until the first 503, then quietly serve an answer
    shaped for a different Role — a bug that only appears during an outage."""
    from runtime.mvp_runtime.providers import FailoverProvider, GoogleAIStudioProvider, GroqProvider

    chain = FailoverProvider([GoogleAIStudioProvider(authorization=None),
                              GroqProvider(authorization=None)])
    bound = chain.bind_role_output_keys(_spec())

    assert bound is not chain
    assert all(set(p._role_output_spec) == TRANSLATION_KEYS for p in bound._providers)
    assert bound.model_id == chain.model_id


def test_a_chain_with_an_unbindable_member_fails_closed():
    """The locked provider decision already forbids a chain that silently shrinks; the same
    rule applies to one that silently serves an unbound member."""
    from runtime.mvp_runtime.errors import ProviderError
    from runtime.mvp_runtime.providers import FailoverProvider, GroqProvider

    chain = FailoverProvider([GroqProvider(authorization=None), _NetworkProvider()])
    with pytest.raises(ProviderError) as exc:
        chain.bind_role_output_keys(_spec())
    assert exc.value.reason_code == "ROLE_BINDING_UNSUPPORTED"


def test_the_pipeline_now_binds_a_hosted_provider_instead_of_refusing():
    """The refusal above was the honest placeholder; this is the fix it named."""
    from runtime.mvp_runtime.providers import GoogleAIStudioProvider

    chosen = pipeline._provider_for_role(GoogleAIStudioProvider(authorization=None), _spec())
    assert set(chosen._role_output_spec) == TRANSLATION_KEYS


def test_an_unbindable_network_provider_is_still_refused():
    """Fail-closed is preserved for anything that cannot ask for the Role's keys."""
    from runtime.mvp_runtime.errors import WorkerBlocked

    with pytest.raises(WorkerBlocked) as exc:
        pipeline._provider_for_role(_NetworkProvider(), _spec())
    assert exc.value.reason_code == "ROLE_OUTPUT_CONTRACT_UNSUPPORTED_BY_PROVIDER"


# --- the second activation round (content / development) ---------------------

def test_business_analysis_was_held_back():
    """Deliberately still a candidate, and the reasoning is a document rather than a memory:
    `docs/runtime-contracts/BUSINESS_ANALYSIS_ROLE_SPLIT_DESIGN_V0.1.md`. Its own contract gates
    activation on repeated cases plus validated scoring rules, and §13 scores the split at two of
    six — so `general.specialist` holds the MVP's core use case because it is the right role for
    it, not as a stopgap."""
    entry = next(r for r in _registry()["roles"] if r["role_id"] == "business.analysis")
    assert (entry["status"], entry["routable"]) == ("candidate", False)
    assert "business" not in REQUEST_KIND_CAPABILITIES


def test_the_split_decision_is_written_down_and_still_says_candidate():
    """The record is the whole point of the decision; a routing change that activated the role
    without updating it would leave the next reader with a confident, false explanation."""
    record = (REPO / "docs/runtime-contracts/BUSINESS_ANALYSIS_ROLE_SPLIT_DESIGN_V0.1.md")
    text = record.read_text(encoding="utf-8")
    assert "**Status:** PROPOSED" in text
    for gate in ("business_analysis_tasks_repeat", "dedicated_scoring_or_evidence_rules_are_validated"):
        assert gate in text, gate


def test_the_contract_gates_the_record_quotes_are_the_contract_s_own():
    """Quoted gates must match the live contract, or the record explains a rule that moved."""
    import yaml

    front = yaml.safe_load(
        (REPO / "03_ROLE_CONTRACTS/ROLES/CANDIDATES/BUSINESS_ANALYSIS_ROLE.md")
        .read_text(encoding="utf-8").split("---", 2)[1])
    assert set(front["activation_conditions"]) == {
        "business_analysis_tasks_repeat", "dedicated_scoring_or_evidence_rules_are_validated",
    }


def test_no_kind_leans_on_a_capability_its_role_shares():
    """The rule that keeps `select_role` able to tell Roles apart, checked against the live
    registry rather than trusted: `content.general` shares `drafting` with `general.specialist`,
    so a content kind asking for `drafting` alone would be AMBIGUOUS_ROLE. Every set must name
    at least one capability its Role does not share with any other routable Role."""
    resolved = load_resolved_roles(REPO)
    routable = {r["role_id"]: set(r["capabilities"]) for r in resolved["roles"] if r["routable"]}
    for kind, capabilities in REQUEST_KIND_CAPABILITIES.items():
        owners = [rid for rid, caps in routable.items() if set(capabilities) <= caps]
        assert len(owners) == 1, f"{kind} is covered by {owners}"


def test_drafting_really_is_shared_so_the_rule_above_is_not_hypothetical():
    resolved = load_resolved_roles(REPO)
    caps = {r["role_id"]: set(r["capabilities"]) for r in resolved["roles"]}
    assert "drafting" in caps["general.specialist"] & caps["content.general"]
