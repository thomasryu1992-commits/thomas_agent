"""Drift gate: a capability the runtime can select must be reachable on the deployed service.

The failure this closes was real and silent. `MVP_FRONTDESK_PROVIDER` shipped in the F2
runtime, but Compose passes only the variables its `environment:` block names — so putting
it in `.env` would have done *nothing*, and the conversational channel would have stayed
deterministic on the server with no error anywhere. Every gate env var is designed to fail
closed when its grant is missing; none of them can notice that the variable never arrived.

Scope, stated rather than assumed: this gate covers the **operator service's** capability
selectors — the surface F2 touches. The scheduler's crypto selectors (`MVP_MARKET_DATA`,
`MVP_LIVE_*`, `MVP_ACCOUNT_FEED`, …) are deliberately left to whoever owns that subsystem's
deployment intent; asserting a deployment shape for them from here would be guessing.

Adding a new operator capability means adding it below — which forces the deploy question
to be answered at authoring time, not discovered on a server that quietly does nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import consumption, frontdesk, operator, providers, tools, workspace

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_PATH = ROOT / "docker-compose.yml"

# Capability selectors the OPERATOR service's code paths read. Each must be passed through
# to that service, or the capability is unreachable there however correct the grant is.
OPERATOR_SELECTORS = {
    providers.HOSTED_PROVIDER_ENV: "the specialist's model provider chain",
    providers.VALIDATOR_PROVIDER_ENV: "the independent validator's own provider (R7.1)",
    frontdesk.FRONTDESK_PROVIDER_ENV: "the conversational front desk (F2)",
    tools.SEARCH_TOOL_ENV: "the read-only search tool (R3)",
    operator.OPERATOR_CHANNEL_ENV: "the real Telegram transport (R4)",
}

# Selectors deliberately NOT passed to the services, each with the reason it stays manual.
# Present so the list above cannot be read as "everything else was forgotten".
NOT_DEPLOYED = {
    workspace.WRITER_ENV: (
        "R8 controlled write is exercised through the operator-run intake CLI "
        "(--write-output), not the continuous loop; the loop never writes artifacts."
    ),
    consumption.ENV_VAR: (
        "R10 approval consumption is a deliberate operator act via approval_cli consume — "
        "a standing service-wide enablement is exactly what it must not become."
    ),
}


def _service_environment(service: str) -> dict:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    return compose["services"][service].get("environment") or {}


@pytest.mark.parametrize("env_var, what", sorted(OPERATOR_SELECTORS.items()))
def test_operator_service_receives_every_capability_selector(env_var, what):
    environment = _service_environment("operator")
    assert env_var in environment, (
        f"{env_var} ({what}) is not in the operator service's compose environment, so a "
        f"value in .env never reaches the container and the capability stays silently off"
    )
    # Passed through from the host .env, defaulting to empty (= the inert path), never
    # hardcoded to a value in committed source.
    assert environment[env_var] == "${%s:-}" % env_var


def test_deliberately_undeployed_selectors_stay_undeployed():
    """These are operator acts, not service settings. If one is ever added to a service,
    that is a governance decision and this test is where it gets made deliberately."""
    for service in ("operator", "scheduler"):
        environment = _service_environment(service)
        for env_var, reason in NOT_DEPLOYED.items():
            assert env_var not in environment, f"{env_var} must stay manual: {reason}"


def test_the_front_desk_is_operator_only():
    """The scheduler holds no conversation — giving it a front-desk provider would put a
    conversational LLM on a service with no one to talk to and no channel to answer on."""
    assert frontdesk.FRONTDESK_PROVIDER_ENV not in _service_environment("scheduler")
