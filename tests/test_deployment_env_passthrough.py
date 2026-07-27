"""Drift gate: a capability the runtime can select must be reachable on the deployed service.

The failure this closes was real and silent. `MVP_FRONTDESK_PROVIDER` shipped in the F2
runtime, but Compose passes only the variables its `environment:` block names — so putting
it in `.env` would have done *nothing*, and the conversational channel would have stayed
deterministic on the server with no error anywhere. Every gate env var is designed to fail
closed when its grant is missing; none of them can notice that the variable never arrived.

Scope, stated rather than assumed: this gate covers the **operator service's** capability
selectors — the surface F2 touches — plus the **scheduler's prediction-market** selectors,
whose owner claimed that deployment intent (PM1, 2026-07-26) after hitting this exact failure:
grants minted, key in `.env`, container restarted, and the runtime still reading mocks because
Compose forwarded nothing. The scheduler's crypto selectors (`MVP_MARKET_DATA`, `MVP_LIVE_*`,
`MVP_ACCOUNT_FEED`, …) remain deliberately out of scope; asserting a deployment shape for them
from here would be guessing.

Adding a new operator capability means adding it below — which forces the deploy question
to be answered at authoring time, not discovered on a server that quietly does nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import consumption, frontdesk, operator, providers, tools, workspace
from runtime.mvp_runtime.predmarket import market_data as predmarket_data

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

# Capability selectors the SCHEDULER service's prediction-market path reads. The `pm_scan`
# kind runs in the scheduler, so this is where an observation run needs them; the operator
# service reads none of them and must not be handed the signed credential.
PREDMARKET_SCHEDULER_SELECTORS = {
    predmarket_data.KALSHI_ENV: "Kalshi market data (PM1)",
    predmarket_data.POLYMARKET_ENV: "Polymarket market data (PM1)",
    predmarket_data.BINANCE_ENV: "Binance prediction market data (PM1)",
    predmarket_data.BINANCE_API_KEY_ENV: "the signed Binance prediction key",
    predmarket_data.BINANCE_API_SECRET_ENV: "the signed Binance prediction secret",
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


@pytest.mark.parametrize("env_var, what", sorted(PREDMARKET_SCHEDULER_SELECTORS.items()))
def test_the_scheduler_receives_every_prediction_market_selector(env_var, what):
    """The failure this was added for, in full: three grants minted, the key in `.env`, the
    container rebuilt and restarted — and the runtime still read mocks, because Compose
    forwards only what its `environment:` block names. Nothing errored. Every one of these
    selectors fails closed by design, and none can notice that the variable never arrived."""
    environment = _service_environment("scheduler")
    assert env_var in environment, f"scheduler never receives {env_var} ({what})"


def test_the_signed_prediction_credential_stays_out_of_the_operator_service():
    """The operator loop reads no prediction market data, so handing it a signed key would
    widen a blast radius for nothing — the same reason the account key is its own grant."""
    environment = _service_environment("operator")
    for secret in (predmarket_data.BINANCE_API_KEY_ENV, predmarket_data.BINANCE_API_SECRET_ENV):
        assert secret not in environment, f"operator service should not receive {secret}"
