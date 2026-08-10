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

from runtime.mvp_runtime import (
    consumption,
    frontdesk,
    naver_research,
    operator,
    providers,
    tools,
    workspace,
)
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


# The Naver research lane (2026-08-09; placement revised 2026-08-10 by the plane separation,
# docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md). Forwarded to the PIPELINE-WORKER (the
# engine that actually runs the `research`/`content` kinds) and — until PR-B of that proposal
# lands — still to the SCHEDULER (the placement #650 made for the weekly ideation; nothing
# consumes it there yet). Withheld from the OPERATOR and, since the split, from the
# DISPATCH-BRIDGE: the door that parses the assistant's frames holds no credential at all.
#
# Withheld rather than merely unneeded, and the reason is the credential, not the capability.
# The lane's USE is read-only — keyword volumes, a trend series, a competition count. The KEY
# is not: `NAVER_SEARCHAD_SECRET_KEY` signs against the ad account and the same signature
# reaches campaign-management endpoints that can change spend. So "it only reads" is a
# property of this code, not of the credential — the same shape as the live-trading note
# above, where `account.py` is read-only by construction while its key carries futures
# permission at the venue.
#
# And forwarding is a capability here, not a cost: `MVP_NAVER_RESEARCH` is the WHOLE gate
# (no mounted grant), so naming it in a service's block plus a value in `.env` is the entire
# difference between that service reading mocks and reaching Naver with the operator's ad
# credential. Adding a service to the list is that decision, made here.
NAVER_RESEARCH_SURFACE = {
    naver_research.NAVER_RESEARCH_ENV: "the whole gate — no grant behind it",
    naver_research.SEARCHAD_CUSTOMER_ID_ENV: "the ad account's customer id",
    naver_research.SEARCHAD_API_KEY_ENV: "the Search Ad access licence",
    naver_research.SEARCHAD_SECRET_KEY_ENV: "the signing secret — account-wide, not read-only",
    naver_research.APIHUB_KEY_ID_ENV: "the API HUB key id (search trend + blog search)",
    naver_research.APIHUB_KEY_ENV: "the API HUB key",
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
@pytest.mark.parametrize("service", ["scheduler", "operator", "pipeline-worker"])
def test_no_service_is_handed_a_prediction_market_selector(service, env_var):
    """The lane was removed 2026-08-02 (Korean domestic regulation) and must not come back
    by accident. Compose forwards only what its `environment:` block names, so a name here
    is the whole difference between a stale key in `.env` reaching a container and reaching
    nothing — which makes this block, not the deleted code, the thing worth pinning. The
    inverse of the test that used to stand here, and for the same reason: nothing errors
    either way, so only a test says which state the deployment is in."""
    assert env_var not in _service_environment(service), \
        f"{service} is still handed {env_var}; the prediction-market lane was removed"


@pytest.mark.parametrize("env_var, what", sorted(NAVER_RESEARCH_SURFACE.items()))
@pytest.mark.parametrize("service", ["scheduler", "pipeline-worker"])
def test_the_naver_lane_reaches_the_services_that_run_it(service, env_var, what):
    """Without this the lane is unreachable on the server however correct `.env` is — and
    nothing errors, it just keeps returning mock keyword volumes.

    The scheduler row is transitional: PR-B of the plane separation removes it, leaving the
    pipeline-worker as the lane's only holder before the Search Ad licence ever mints a
    value."""
    environment = _service_environment(service)
    assert env_var in environment, (
        f"{env_var} ({what}) is not in the {service} service's compose environment, so a "
        f"value in .env never reaches the container and the lane stays silently on mocks"
    )
    assert environment[env_var] == "${%s:-}" % env_var


@pytest.mark.parametrize("env_var", sorted(NAVER_RESEARCH_SURFACE))
def test_the_operator_is_not_handed_the_naver_ad_credential(env_var):
    """The withholding, pinned so it is a decision to reverse rather than a drift.

    The operator loop is not a lane consumer — the weekly ideation is a scheduler job and
    orchestrator-initiated research arrives at the dispatch door. Handing it the Search Ad
    secret would put an account-wide, spend-capable credential on a third service for no
    consumer, which is the trade the live-trading block above declines for the same reason."""
    assert env_var not in _service_environment("operator"), (
        f"operator is handed {env_var}; the Naver lane does not run there and the Search Ad "
        f"secret is account-wide, not the read-only key its read-only use suggests"
    )


# --- the plane separation: the door holds nothing, the worker holds the engine env ------

# The engine surface the plane separation moved out of dispatch-bridge verbatim. Selector
# names come from the code's own constants; the API-key names are literals for the same
# reason the removed-predmarket block's are — the modules keep them private, and the point
# here is the compose block, not the code.
PIPELINE_WORKER_ENGINE_SURFACE = {
    providers.HOSTED_PROVIDER_ENV: "the specialist's model provider chain",
    providers.VALIDATOR_PROVIDER_ENV: "the independent validator's own provider",
    providers.OPENROUTER_MODEL_ENV: "the exact OpenRouter model slug",
    "GOOGLE_AI_STUDIO_API_KEY": "the Google AI Studio key",
    "GROQ_API_KEY": "the Groq key",
    "OPENROUTER_API_KEY": "the OpenRouter key — shared with the operator's assistant",
    tools.SEARCH_TOOL_ENV: "the read-only search tool",
    "TAVILY_API_KEY": "the search key",
}


@pytest.mark.parametrize("env_var, what", sorted(PIPELINE_WORKER_ENGINE_SURFACE.items()))
def test_the_pipeline_worker_receives_the_engine_surface(env_var, what):
    """The other half of stripping the door: the worker must actually receive what execution
    needs, or every dispatch silently degrades to the mock pipeline with no error anywhere —
    this file's founding failure, on a brand-new service."""
    environment = _service_environment("pipeline-worker")
    assert env_var in environment, (
        f"{env_var} ({what}) is not in the pipeline-worker service's compose environment, so "
        f"a value in .env never reaches the engine and every dispatch runs the mock"
    )
    assert environment[env_var] == "${%s:-}" % env_var


def test_the_dispatch_door_carries_only_the_peer_variables():
    """The point of the split, pinned as an exact shape rather than a denial list: the door
    that parses the assistant's frames holds the two socket-peer variables and NOTHING else —
    the same silhouette as the read and switch doors. A denial list would go quiet the day a
    new credential is invented; an exact allowlist refuses it by construction."""
    environment = _service_environment("dispatch-bridge")
    assert set(environment) == {"MVP_BRIDGE_CLIENT_GID", "MVP_BRIDGE_CLIENT_UID"}, (
        f"dispatch-bridge's environment is {sorted(environment)}; the door must carry the two "
        f"peer variables only — execution env belongs to pipeline-worker"
    )


@pytest.mark.parametrize("env_var, what", sorted(LIVE_TRADING_SURFACE.items()))
def test_the_pipeline_worker_receives_none_of_the_live_surface(env_var, what):
    """The worker holds model, search, and Naver env by design — and must never hold money.
    A compromised worker can spend quota and reach research APIs; it must not be able to
    place an order. Same reasoning, same per-variable pin, as the operator's denial below."""
    assert env_var not in _service_environment("pipeline-worker"), (
        f"the pipeline-worker service would receive {env_var} ({what}); the engine runs "
        f"non-trading P3 kinds only and the money path stays scheduler-only"
    )


def test_the_pipeline_worker_socket_peers_are_the_runtime_itself():
    """Literals, not `${...}` interpolations, and pinned as such: a host `.env` that points
    the doors' gid at the assistant must not be able to point the engine's peers anywhere.
    10001 is the image's fixed runtime user; the dispatch door is this socket's only
    legitimate client and SO_PEERCRED enforces it."""
    environment = _service_environment("pipeline-worker")
    assert environment["MVP_BRIDGE_CLIENT_GID"] == "10001"
    assert environment["MVP_BRIDGE_CLIENT_UID"] == "10001"


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


@pytest.mark.parametrize("service", ("operator", "scheduler", "pipeline-worker"))
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


# --- the candle archive's own axis ---------------------------------------------------------

def test_the_scheduler_receives_the_candle_archive_switch():
    """The whole gate, so a missing line here is the whole failure.

    Every other selector in this file fails closed twice — the env var AND a mounted grant —
    so a variable Compose never forwarded still left a second thing visibly absent. The candle
    archive has no second thing: Thomas moved it to the env-only gate on 2026-08-04 (#496)
    because a 30-day TTL cannot bound a job that races a rolling window for months. So an
    unforwarded name here does not fail closed loudly; it reads as configured on the host and
    archives nothing, while the window it exists to outrun keeps moving.

    That is not hypothetical: the code shipped in #486 and this block did not name the
    variable until #512.
    """
    environment = _service_environment("scheduler")
    assert "MVP_CANDLE_ARCHIVE" in environment, (
        "the scheduler never receives MVP_CANDLE_ARCHIVE, so archiving cannot be switched on"
    )
    assert environment["MVP_CANDLE_ARCHIVE"] == "${MVP_CANDLE_ARCHIVE:-}"


def test_the_operator_does_not_receive_the_candle_archive_switch():
    """Scheduler-only, like every other collection selector. The operator loop reads no market
    data; handing it an archive switch would add egress for nothing."""
    assert "MVP_CANDLE_ARCHIVE" not in _service_environment("operator")
