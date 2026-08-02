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

The **live-trading and account** variables are in scope, and they moved twice on 2026-07-27 —
which is why the reasoning is written down rather than the conclusion. First they were pinned
as reaching NEITHER service, because `docs/DEPLOYMENT.md` said credentials belong in the
terminal of the human placing an order and that rule lived only in prose. Then Thomas decided
the opposite for the scheduler: forward them, so the readiness board and the account read
answer on the service without a per-session export.

So the assertion below inverts for the scheduler and HOLDS for the operator. Both halves are
load-bearing. The scheduler is where the crypto and prediction paths run, so it is where the
account read is useful; the operator loop reads no market or account data, and handing it a
key that can trade would widen a blast radius for nothing — the same reason the signed
prediction credential is scheduler-only.

Adding a new operator capability means adding it below — which forces the deploy question
to be answered at authoring time, not discovered on a server that quietly does nothing.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from runtime.mvp_runtime import consumption, frontdesk, operator, providers, tools, workspace
from runtime.mvp_runtime.crypto import account, live_execution, live_order, live_pnl

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

# The five selectors the removed prediction-market path read. Named as literals precisely
# because the module that used to define them is gone: the point of the test below is that
# NOTHING re-wires these, and a constant imported from the deleted package could not say so.
REMOVED_PREDMARKET_SELECTORS = (
    "MVP_KALSHI_MARKET_DATA",
    "MVP_POLYMARKET_MARKET_DATA",
    "MVP_BINANCE_PREDICTION",
    "BINANCE_PREDICTION_API_KEY",
    "BINANCE_PREDICTION_API_SECRET",
)

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



# The live-trading surface. Forwarded to the SCHEDULER (Thomas decision, 2026-07-27) and
# withheld from the OPERATOR. Named from the code's own constants so a rename cannot silently
# empty this list.
#
# What forwarding costs, kept here rather than deleted with the prohibition it used to justify:
# in a service's environment these outlive the session that set them and come back on every
# restart (`restart: unless-stopped`), readable by anything that can inspect the container.
# That was the argument for withholding them; it did not become wrong when the decision went
# the other way, it became a cost Thomas accepted. A future reader reversing this again should
# see what was traded.
#
# And the key is ONE key. `docs/DEPLOYMENT.md` derives the order credentials from the account
# ones (`MVP_LIVE_ORDER_API_KEY="$BINANCE_ACCOUNT_API_KEY"`), so the account variables are not
# a safer subset one could forward while withholding the rest: `account.py` is read-only by
# construction, but that is a property of THIS code, not of the key, which carries
# futures-trading permission at the venue. Splitting it into a genuinely read-only venue key
# is what would make the two separable.
#
# This comment used to end "Env alone opens nothing — which is why forwarding is a cost rather
# than a capability." **That sentence is no longer true, and the two changes that falsified it
# both landed after it was written**, so it is corrected here rather than quietly deleted:
#
#   1. cycle routing shipped (LP5.3 step 3, 2026-07-28): a scheduled crypto run now reaches the
#      order path through `crypto/live_route.py`. The old claim that no autonomous entry point
#      could get there is gone; what survives is that exactly ONE module may, pinned by
#      `test_the_cycle_reaches_the_live_order_path_through_exactly_one_module`;
#   2. the per-machine `live_trading` grant was removed (Thomas, 2026-07-28): the env opt-in
#      `MVP_LIVE_TRADING=real` IS the gate now.
#
# Together those mean the `.env` file this test guards is, by itself, the difference between a
# scheduler that trades paper and one that trades money. Forwarding is therefore a CAPABILITY,
# not merely a cost, and this list is a correspondingly bigger deal than when it was written.
#
# What still stands between a forwarded scheduler and an autonomous order: the confirmation
# phrase, the registered budget, the canary evidence, both kill switches, the loss breaker, and
# the single-chokepoint property above. Each has its own row on the readiness board and each
# has its own test. None of them is the env file.
LIVE_TRADING_SURFACE = {
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


@pytest.mark.parametrize("env_var", REMOVED_PREDMARKET_SELECTORS)
@pytest.mark.parametrize("service", ["scheduler", "operator"])
def test_no_service_is_handed_a_prediction_market_selector(service, env_var):
    """The lane was removed 2026-08-02 (Korean domestic regulation) and must not come back
    by accident. Compose forwards only what its `environment:` block names, so a name here
    is the whole difference between a stale key in `.env` reaching a container and reaching
    nothing — which makes this block, not the deleted code, the thing worth pinning. The
    inverse of the test that used to stand here, and for the same reason: nothing errors
    either way, so only a test says which state the deployment is in."""
    assert env_var not in _service_environment(service), \
        f"{service} is still handed {env_var}; the prediction-market lane was removed"


# --- the live-trading surface must reach neither service --------------------------------

@pytest.mark.parametrize("env_var, what", sorted(LIVE_TRADING_SURFACE.items()))
def test_the_scheduler_receives_the_live_trading_surface(env_var, what):
    """Thomas decision 2026-07-27: forwarded, so the board and the account read answer on the
    service. Asserted rather than assumed for the reason the whole file exists — Compose
    forwards only what its `environment:` block names, and every one of these fails closed
    without noticing that the variable never arrived."""
    environment = _service_environment("scheduler")
    assert env_var in environment, f"the scheduler never receives {env_var} ({what})"
    # From the host `.env`, defaulting to empty (= the inert, fail-closed path). A committed
    # literal here would put a credential in source, which no decision authorizes.
    assert environment[env_var] == "${%s:-}" % env_var


@pytest.mark.parametrize("env_var, what", sorted(LIVE_TRADING_SURFACE.items()))
def test_the_operator_receives_none_of_it(env_var, what):
    """The half that did NOT reverse. The operator loop reads no market or account data, so a
    key that can trade buys it nothing and widens a blast radius — the same reasoning that
    keeps the signed prediction credential scheduler-only. Checked per variable because
    per-service drift is the failure this file exists for (#245)."""
    assert env_var not in _service_environment("operator"), (
        f"the operator service would receive {env_var} ({what}), which it has no code path for"
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


def test_the_list_covers_the_whole_live_surface():
    """A guard against the list quietly shrinking: these are the variables a live order needs,
    and every one must be named above. A rename that emptied the list would otherwise leave
    every test in this section vacuously green — in BOTH directions now, since the same list
    drives the scheduler's forwarding requirement and the operator's prohibition."""
    assert len(LIVE_TRADING_SURFACE) == 8
    for env_var in LIVE_TRADING_SURFACE:
        assert env_var.startswith(("MVP_", "BINANCE_")), env_var
