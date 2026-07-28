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
Compose forwarded nothing. `MVP_MARKET_DATA` and `MVP_PAPER_TRADING` are forwarded and stay
out of scope here — asserting a shape for them from this file would be guessing.

The **live-trading and account** variables are no longer out of scope, and the direction is
inverted: they must NOT reach either service. That intent is stated in `docs/DEPLOYMENT.md`
("Live trading env — a shell session, **never** the compose `.env`") and confirmed by Thomas
on 2026-07-27, and until now it lived only in that prose. One line added to a service's
`environment:` block would arm a permanently-restarting container with a key that can trade,
and nothing would have objected.

Adding a new operator capability means adding it below — which forces the deploy question
to be answered at authoring time, not discovered on a server that quietly does nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import consumption, frontdesk, operator, providers, tools, workspace
from runtime.mvp_runtime.crypto import account, live_execution, live_order, live_pnl
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



# The live-trading surface, which must reach NEITHER service. Named from the code's own
# constants so a rename cannot silently empty this list.
#
# Why a prohibition rather than an omission: the credentials are supposed to live in the
# terminal of the human placing an order, for the length of that session. In a service's
# environment they outlive the intent that set them and come back on every restart
# (`restart: unless-stopped`), readable by anything that can inspect the container.
#
# And the key is ONE key. `docs/DEPLOYMENT.md` derives the order credentials from the account
# ones (`MVP_LIVE_ORDER_API_KEY="$BINANCE_ACCOUNT_API_KEY"`), so the account variables are not
# a safer subset: `account.py` is read-only by construction, but that is a property of THIS
# code, not of the key, which carries futures-trading permission at the venue. Splitting it
# into a genuinely read-only venue key is the only thing that would change this answer, and it
# would be a deliberate edit to this list rather than a quiet compose change.
#
# Note what does NOT protect us today: no autonomous entry point can reach the order path
# (`test_no_autonomous_entry_point_reaches_the_live_order_path`), so a container holding these
# could not place an order right now. That is exactly why this is defence in depth — the day
# cycle routing lands, the credentials must not already be sitting there.
LIVE_TRADING_NEVER_DEPLOYED = {
    live_pnl.LIVE_TRADING_ENV: "the live-trading switch",
    live_order.CONFIRMATION_ENV: "the autonomous-trading confirmation phrase",
    live_order.CANARY_CONFIRMATION_ENV: "the canary confirmation phrase",
    live_execution.ORDER_API_KEY_ENV: "the order-signing key",
    live_execution.ORDER_API_SECRET_ENV: "the order-signing secret",
    account.ACCOUNT_FEED_ENV: "the account feed selector",
    account.ACCOUNT_API_KEY_ENV: "the account key — the same key that can trade",
    account.ACCOUNT_API_SECRET_ENV: "the account secret — the same secret that can trade",
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


# --- the live-trading surface must reach neither service --------------------------------

@pytest.mark.parametrize("env_var, what", sorted(LIVE_TRADING_NEVER_DEPLOYED.items()))
@pytest.mark.parametrize("service", ("operator", "scheduler"))
def test_no_service_receives_a_live_trading_variable(service, env_var, what):
    """The rule `docs/DEPLOYMENT.md` states, made enforceable.

    Checked per service rather than once, because per-service drift is the failure this whole
    file exists for: #245 was one selector reaching the operator and not the scheduler."""
    assert env_var not in _service_environment(service), (
        f"the {service} service would receive {env_var} ({what}). Live-trading credentials "
        f"belong in the terminal of the human placing an order, not in a service that "
        f"restarts forever — see docs/DEPLOYMENT.md."
    )


@pytest.mark.parametrize("service", ("operator", "scheduler"))
def test_no_service_bulk_forwards_the_environment(service):
    """The bypass the list above cannot see.

    Every check here reads the `environment:` block, so a service that instead declared
    `env_file: .env` would forward the WHOLE file — every variable the prohibition names —
    while each assertion above still passed. The prohibition has to cover the mechanism, not
    just the enumeration."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert "env_file" not in compose["services"][service], (
        f"the {service} service bulk-forwards an env file, which hands the container every "
        f"variable in it — including the live-trading credentials this file prohibits"
    )


def test_the_prohibition_covers_the_whole_live_surface():
    """A guard against the list quietly shrinking: these are the variables a live order needs,
    and every one of them must be named above. A rename that emptied the list would otherwise
    leave every test in this section vacuously green."""
    assert len(LIVE_TRADING_NEVER_DEPLOYED) == 8
    for env_var in LIVE_TRADING_NEVER_DEPLOYED:
        assert env_var.startswith(("MVP_", "BINANCE_")), env_var
