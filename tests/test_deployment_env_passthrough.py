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

import re
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
DEPLOYMENT_DOC = ROOT / "docs" / "DEPLOYMENT.md"
# Every service in the file, read once: the bulk-forward prohibition below must cover a service
# the moment it is added, not when someone remembers to extend a tuple (the door services were
# outside the old tuple for a month).
_ALL_SERVICES = tuple(sorted(yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"]))

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
        "For the three LOOP services (operator, scheduler, scheduler-maint) the original "
        "reasoning stands: R8 controlled write is exercised through the operator-run intake "
        "CLI (--write-output), and none of these loops writes artifacts. The premise-reversal "
        "this entry once guarded against has HAPPENED, deliberately: the content lane renders "
        "its weekly package to files (§J decision 2, Thomas 2026-08-30), so the "
        "PIPELINE-WORKER — where the lane executes — now receives this selector "
        "(PIPELINE_WORKER_ENGINE_SURFACE below). The recorded reason this pin demanded is "
        "that decision; the loops' exclusion is unchanged."
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


# The Naver research lane (2026-08-09; placement settled 2026-08-10 by the plane separation,
# docs/proposals/CREDENTIAL_PLANE_SEPARATION_V0.1.md). Forwarded to the PIPELINE-WORKER only —
# the engine that runs the `research`/`content` kinds and the container a `--naver-keywords`
# CLI run must exec in. Withheld from the OPERATOR, from the SCHEDULER (PR-B removed #650's
# transitional copy), and from the DISPATCH-BRIDGE: the door that parses the assistant's
# frames holds no credential at all.
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
    for service in ("operator", "scheduler", "scheduler-maint"):
        environment = _service_environment(service)
        for env_var, reason in NOT_DEPLOYED.items():
            assert env_var not in environment, f"{env_var} must stay manual: {reason}"


def test_the_front_desk_is_operator_only():
    """Neither scheduler lane holds a conversation — giving one a front-desk provider would
    put a conversational LLM on a service with no one to talk to and no channel to answer on."""
    assert frontdesk.FRONTDESK_PROVIDER_ENV not in _service_environment("scheduler")
    assert frontdesk.FRONTDESK_PROVIDER_ENV not in _service_environment("scheduler-maint")


@pytest.mark.parametrize("env_var", REMOVED_PREDMARKET_SELECTORS)
@pytest.mark.parametrize("service", ["scheduler", "scheduler-maint", "operator", "pipeline-worker"])
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
def test_the_naver_lane_reaches_the_one_service_that_runs_it(env_var, what):
    """Without this the lane is unreachable on the server however correct `.env` is — and
    nothing errors, it just keeps returning mock keyword volumes.

    One service, by decision (plane separation PR-B): the pipeline-worker is the lane's only
    holder, settled before the Search Ad licence ever mints a value."""
    environment = _service_environment("pipeline-worker")
    assert env_var in environment, (
        f"{env_var} ({what}) is not in the pipeline-worker service's compose environment, so "
        f"a value in .env never reaches the container and the lane stays silently on mocks"
    )
    assert environment[env_var] == "${%s:-}" % env_var


@pytest.mark.parametrize("env_var", sorted(NAVER_RESEARCH_SURFACE))
@pytest.mark.parametrize("service", ["operator", "scheduler", "scheduler-maint"])
def test_no_other_service_is_handed_the_naver_ad_credential(service, env_var):
    """The withholding, pinned so it is a decision to reverse rather than a drift.

    The operator loop is not a lane consumer, and the scheduler stopped being its holder in
    the plane separation's PR-B: #666's keyword brief is CLI-opt-in (`--naver-keywords`), and
    that run belongs in the worker container, which holds the env — in this pair it degrades
    to Mock rows by design. Putting the Search Ad secret back on either service would put an
    account-wide, spend-capable credential beside the operator's channel or the Binance keys,
    which is the trade the live-trading block above declines for the same reason.
    (dispatch-bridge is covered by its exact-shape test above.)"""
    assert env_var not in _service_environment(service), (
        f"{service} is handed {env_var}; the Naver lane runs in pipeline-worker only and the "
        f"Search Ad secret is account-wide, not the read-only key its read-only use suggests"
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
    workspace.WRITER_ENV: "the R8 writer — the content lane's package files (§J decision 2)",
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


# The analysis chain and the search tool, removed from the SCHEDULER by PR-C of the plane
# separation Phase 2 and pinned absent here. Their only consumer on that service was an
# `analysis_task` fire, which now runs in the pipeline-worker; what stays is
# `MVP_VALIDATOR_PROVIDER` + `GROQ_API_KEY`, which `crypto_propose` and `crypto_data_review`
# still read in-process (`select_validator_provider` never falls back to the chain below,
# which is what let these two lists separate at all).
#
# Named as literals rather than from `providers`/`tools` constants on purpose: this is a
# prohibition, and a constant that got renamed would empty the list it guards — the same
# reason the removed prediction-market block above spells its five out.
SCHEDULER_WITHHELD_ENGINE_SURFACE = {
    "MVP_HOSTED_PROVIDER": "the analysis provider chain",
    "OPENROUTER_API_KEY": "the OpenRouter key — the assistant's own container spends from it",
    "GOOGLE_AI_STUDIO_API_KEY": "the Google AI Studio key",
    "MVP_OPENROUTER_MODEL": "the OpenRouter model slug",
    "MVP_SEARCH_TOOL": "the read-only search tool selector",
    "TAVILY_API_KEY": "the search key",
}


@pytest.mark.parametrize("env_var, what", sorted(SCHEDULER_WITHHELD_ENGINE_SURFACE.items()))
def test_the_scheduler_is_not_handed_the_analysis_chain_or_the_search_tool(env_var, what):
    """The service that holds the Binance account and order keys must not also hold a model
    credential it does not use. Checked per variable because per-service drift is the failure
    this file exists for, and because re-adding one is a decision that belongs here."""
    assert env_var not in _service_environment("scheduler"), (
        f"the scheduler is handed {env_var} ({what}); an analysis_task fire runs in the "
        f"pipeline-worker now, so this is blast radius with no consumer"
    )


@pytest.mark.parametrize("env_var", ["MVP_VALIDATOR_PROVIDER", "GROQ_API_KEY"])
def test_the_scheduler_holds_no_model_credential_at_all(env_var):
    """D6 finished what D4 started. These two were the last model credentials on the service
    that also holds the Binance account and order keys; their consumers — `crypto_propose`
    and `crypto_data_review` — now delegate their model call to the pipeline worker.

    Kept as its own test rather than folded into the list above because the reason differs:
    those six were never used here, these two were, and re-adding either would be a decision
    about where model responses get parsed rather than a passthrough oversight."""
    assert env_var not in _service_environment("scheduler"), (
        f"the scheduler is handed {env_var}; both of its model callers delegate now, so a "
        f"provider credential here is blast radius beside the venue keys with no consumer"
    )


def test_no_service_but_the_worker_holds_a_model_credential():
    """The end state of the plane separation, as one assertion over the whole file: exactly
    one service selects a model provider, and it is the one that holds no money variable.
    The operator is the deliberate exception — it runs the conversational front desk and its
    own analyses, which is a different plane from either of these."""
    import yaml as _yaml

    compose = _yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    model_vars = {"MVP_HOSTED_PROVIDER", "MVP_VALIDATOR_PROVIDER", "MVP_FRONTDESK_PROVIDER"}
    holders = {
        name for name, spec in compose["services"].items()
        if model_vars & set(spec.get("environment") or {})
    }
    assert holders == {"pipeline-worker", "operator"}, (
        f"services selecting a model provider are {sorted(holders)}; expected the worker and "
        f"the operator only — the scheduler holds the venue keys and must hold no model key"
    )


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


@pytest.mark.parametrize("service", _ALL_SERVICES)
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

def test_the_maintenance_lane_receives_the_candle_archive_switch():
    """The whole gate, so a missing line here is the whole failure.

    Every other selector in this file fails closed twice — the env var AND a mounted grant —
    so a variable Compose never forwarded still left a second thing visibly absent. The candle
    archive has no second thing: Thomas moved it to the env-only gate on 2026-08-04 (#496)
    because a 30-day TTL cannot bound a job that races a rolling window for months. So an
    unforwarded name here does not fail closed loudly; it reads as configured on the host and
    archives nothing, while the window it exists to outrun keeps moving.

    That is not hypothetical: the code shipped in #486 and this block did not name the
    variable until #512. The lane split moved it here from the scheduler service — the
    archive fire is a MAINTENANCE kind, so this is the service whose loop runs it.
    """
    environment = _service_environment("scheduler-maint")
    assert "MVP_CANDLE_ARCHIVE" in environment, (
        "scheduler-maint never receives MVP_CANDLE_ARCHIVE, so archiving cannot be switched on"
    )
    assert environment["MVP_CANDLE_ARCHIVE"] == "${MVP_CANDLE_ARCHIVE:-}"


@pytest.mark.parametrize("service", ["operator", "scheduler"])
def test_no_other_service_receives_the_candle_archive_switch(service):
    """The operator reads no market data, and the RISK-lane scheduler stopped being the
    archive's runner in the lane split — a name whose only consumer moved out is pure blast
    radius on the service that kept the venue keys. Pinned in both directions so the move
    cannot half-happen: the variable reaching the wrong service reads as configured and
    archives nothing."""
    assert "MVP_CANDLE_ARCHIVE" not in _service_environment(service)


# --- the maintenance lane (SCHEDULER_LANE_SPLIT_V0.1 §4): audited env, and no money -------

# The in-process consumers of the maintenance lane's fires: factory and null_control read
# the collection selectors; the archive its own axis (above); notification is outbound-only.
# analysis_task / report / propose / data_review delegate to the pipeline-worker, so no
# model credential belongs here — and none of the money surface ever does.
MAINTENANCE_LANE_SELECTORS = {
    "MVP_MARKET_DATA": "the factory/null_control candle collector",
    "MVP_LIQUIDATION_FEED": "their liquidation feed leg",
    "COINALYZE_API_KEY": "the liquidation feed's key",
    operator.OPERATOR_CHANNEL_ENV: "outbound-only failure/recovery notification",
}


@pytest.mark.parametrize("env_var, what", sorted(MAINTENANCE_LANE_SELECTORS.items()))
def test_the_maintenance_lane_receives_its_audited_selectors(env_var, what):
    """Without these the lane runs mocks and mines evidence-free noise — silently, which is
    this file's founding failure, on the second brand-new service it has had to guard."""
    environment = _service_environment("scheduler-maint")
    assert env_var in environment, (
        f"{env_var} ({what}) is not in scheduler-maint's compose environment, so a value in "
        f".env never reaches the lane that fires its consumer"
    )
    assert environment[env_var] == "${%s:-}" % env_var


def test_the_maintenance_lane_notifies_on_the_schedulers_bot():
    """The same fallback shape as the risk lane, asserted verbatim: notifications land where
    the operator is already looking, and this service never calls getUpdates, which is the
    property that makes sharing the bot safe."""
    environment = _service_environment("scheduler-maint")
    assert environment["TELEGRAM_BOT_TOKEN"] == (
        "${HERMES_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
    )
    assert environment["TELEGRAM_BOT_TOKEN"] == _service_environment("scheduler")["TELEGRAM_BOT_TOKEN"]


@pytest.mark.parametrize("env_var, what", sorted(LIVE_TRADING_SURFACE.items()))
def test_the_maintenance_lane_receives_none_of_the_live_surface(env_var, what):
    """The split's credential point, pinned per variable: the service that runs 6-minute
    fires does not sit next to money. Every maintenance consumer of these is zero — the
    account read, the readiness board, and the order path all live on the risk lane."""
    assert env_var not in _service_environment("scheduler-maint"), (
        f"scheduler-maint would receive {env_var} ({what}); the maintenance lane has no "
        f"money-path consumer and must hold no money-path credential"
    )


@pytest.mark.parametrize("env_var", sorted(SCHEDULER_WITHHELD_ENGINE_SURFACE)
                         + ["MVP_VALIDATOR_PROVIDER", "GROQ_API_KEY"])
def test_the_maintenance_lane_holds_no_model_credential(env_var):
    """Its model-calling kinds (analysis, report, propose, data review) all delegate to the
    pipeline-worker, exactly as they did from the unsplit scheduler — so a provider
    credential here would be the same no-consumer blast radius the plane separation removed
    from its sibling."""
    assert env_var not in _service_environment("scheduler-maint"), (
        f"scheduler-maint is handed {env_var}; every model call in this lane delegates to "
        f"the pipeline-worker"
    )


def test_the_lane_wiring_matches_the_heartbeat_each_service_probes():
    """The drift this closes: a lane flag and a healthcheck that disagree fail SLOWLY — the
    loop ticks one lane while Docker probes the other lane's heartbeat, and the container
    goes unhealthy minutes after a deploy that looked clean. Command and probe are pinned
    together, per service, from the compose file both are defined in."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    wiring = {
        "scheduler": ("risk", "scheduler-risk"),
        "scheduler-maint": ("maintenance", "scheduler-maintenance"),
    }
    for service, (lane, heartbeat_name) in wiring.items():
        spec = compose["services"][service]
        command = spec["command"]
        assert command[command.index("--lane") + 1] == lane, (
            f"{service} ticks --lane {command[command.index('--lane') + 1]!r}, expected {lane!r}"
        )
        assert spec["healthcheck"]["test"][-1] == heartbeat_name, (
            f"{service}'s healthcheck probes {spec['healthcheck']['test'][-1]!r}, "
            f"expected {heartbeat_name!r}"
        )


# ── Secret ownership matrix (PR2, 2026-09-04) ─────────────────────────────────────────────
#
# One secret source on the host (`.env`, root-owned, mode 0600) and per-service projection by
# `environment:` enumeration — the two halves of Thomas's 2026-09-03 decision. This matrix is
# the third half: for every secret-bearing variable the compose file can draw from `.env`, the
# exact set of services that receives it. Keyed by the NAME IN `.env` (the source), not by the
# name the container sees — the scheduler lanes receive the assistant's bot token under the
# container name `TELEGRAM_BOT_TOKEN`, and that is precisely the kind of aliasing a reader of
# the compose file misses.
#
# `TELEGRAM_BOT_TOKEN` (the control bot) is listed for the scheduler lanes because their value is
# `${HERMES_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}`: a host that has not set the assistant's token
# falls back to the control bot, so the control bot token CAN reach those two services. The
# matrix records what can reach a service, which is the question a leak asks.
SECRET_NAME = re.compile(r"(KEY|SECRET|TOKEN)")
_ENV_REFERENCE = re.compile(r"\$\{([A-Z0-9_]+)")

SECRET_OWNERSHIP: dict[str, frozenset[str]] = {
    "BINANCE_ACCOUNT_API_KEY": frozenset({"scheduler"}),
    "BINANCE_ACCOUNT_API_SECRET": frozenset({"scheduler"}),
    "COINALYZE_API_KEY": frozenset({"scheduler", "scheduler-maint"}),
    "GOOGLE_AI_STUDIO_API_KEY": frozenset({"operator", "pipeline-worker"}),
    "GROQ_API_KEY": frozenset({"operator", "pipeline-worker"}),
    "HERMES_BOT_TOKEN": frozenset({"hermes", "scheduler", "scheduler-maint"}),
    "MVP_LIVE_ORDER_API_KEY": frozenset({"scheduler"}),
    "MVP_LIVE_ORDER_API_SECRET": frozenset({"scheduler"}),
    "NAVER_APIHUB_KEY": frozenset({"pipeline-worker"}),
    "NAVER_APIHUB_KEY_ID": frozenset({"pipeline-worker"}),
    "NAVER_SEARCHAD_API_KEY": frozenset({"pipeline-worker"}),
    "NAVER_SEARCHAD_SECRET_KEY": frozenset({"pipeline-worker"}),
    "OPENROUTER_API_KEY": frozenset({"hermes", "operator", "pipeline-worker"}),
    "TAVILY_API_KEY": frozenset({"operator", "pipeline-worker"}),
    "TELEGRAM_BOT_TOKEN": frozenset({"operator", "scheduler", "scheduler-maint"}),
}


def _secret_holders_in_compose() -> dict[str, frozenset[str]]:
    """Every `${NAME` reference in any service's `environment:` whose NAME looks like a secret,
    mapped to the services that reference it. Reads the file the way Compose does — the
    interpolation SOURCE is what leaves `.env`, whatever the container calls it."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    holders: dict[str, set[str]] = {}
    for service, spec in compose["services"].items():
        environment = spec.get("environment") or {}
        assert isinstance(environment, dict), (
            f"{service} declares `environment:` as a list; the matrix reads mappings only, and "
            f"so does every assertion above — declare it as a mapping"
        )
        for value in environment.values():
            for name in _ENV_REFERENCE.findall(str(value)):
                if SECRET_NAME.search(name):
                    holders.setdefault(name, set()).add(service)
    return {name: frozenset(services) for name, services in holders.items()}


def test_every_secret_the_compose_file_can_draw_is_in_the_ownership_matrix():
    """Both directions, as one assertion: a secret referenced by a service the matrix does not
    name is a leak the enumeration tests above cannot see (they each know one variable); a
    matrix row no service references is a stale claim about the deployment."""
    assert _secret_holders_in_compose() == SECRET_OWNERSHIP


def test_the_bulk_forward_prohibition_covers_every_service():
    """`_ALL_SERVICES` is read from the file, so this can only fail if the file has no services —
    it is here to make the coverage claim explicit rather than implicit in a parametrize."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    assert set(_ALL_SERVICES) == set(compose["services"]) and len(_ALL_SERVICES) >= 8


def _documented_matrix() -> dict[str, frozenset[str]]:
    """The table under `## Secret boundary` in docs/DEPLOYMENT.md: `| NAME | a, b | ... |` (NAME in backticks).
    Only the second column (services in THIS compose file) is read; the assistant's container
    lives in another compose project until the harness is unified and is documented in prose."""
    text = DEPLOYMENT_DOC.read_text(encoding="utf-8")
    start = text.index("## Secret boundary")
    end = text.find("\n## ", start + 1)
    section = text[start:end if end > 0 else None]
    rows: dict[str, frozenset[str]] = {}
    for line in section.splitlines():
        match = re.match(r"^\| `([A-Z0-9_]+)` \| ([^|]*) \|", line)
        if match:
            services = {item.strip().strip("`") for item in match.group(2).split(",") if item.strip()}
            rows[match.group(1)] = frozenset(services)
    return rows


def test_the_ownership_matrix_is_documented_verbatim():
    """docs/DEPLOYMENT.md is where an operator looks; the test is where the truth is pinned.
    They must not drift — a matrix that lives in a test nobody reads is not a boundary
    anyone can check on the host."""
    assert _documented_matrix() == SECRET_OWNERSHIP


# ── The assistant as the ninth service (PR5, 2026-09-04) ──────────────────────────────────
#
# "One compose, runtimes stay split" (Thomas, 2026-09-03). What a compose file can express of
# the eight invariants in docs/HERMES_ORCHESTRATOR_ARCHITECTURE_V0.1.md is pinned here; the
# rest (uid gate, approval path, closed kind set) lives in the door modules and their tests.
ASSISTANT = "hermes"
LANES = ("scheduler", "scheduler-maint")


def _service(name: str) -> dict:
    return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"][name]


def test_the_assistant_is_an_image_not_a_build():
    """CI's `docker compose build` builds every service with a build context. The assistant's
    image is 4 GB and comes from another repository; a `build:` here would make every PR build
    it and would bake a foreign checkout into this file's smoke."""
    spec = _service(ASSISTANT)
    assert "build" not in spec and spec.get("image") == "hermes-agent"


def test_no_service_depends_on_any_other():
    """Scheduler survives Hermes failure; operator survives Hermes failure — and the assistant
    theirs. `depends_on` is the one compose key that would couple their lifecycles, so it is
    absent everywhere, not just on the assistant."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    coupled = {name for name, spec in compose["services"].items() if spec.get("depends_on")}
    assert coupled == set(), f"services declaring depends_on: {sorted(coupled)}"


def test_the_assistant_mounts_only_the_bridge_directory_of_the_state_root():
    """The four sockets are the only path from the assistant to the runtime. Mounting the
    state root itself would expose approvals, ledgers and control state; mounting the Docker
    socket would make the container host-root (removed 2026-08-29 and never to return)."""
    mounts = [str(v) for v in _service(ASSISTANT)["volumes"]]
    assert len(mounts) == 2, mounts
    # rsplit: the source may carry a `${VAR:-default}` with a colon inside; the target never does.
    sources = [m.rsplit(":", 1)[0].rstrip("/") for m in mounts]
    targets = [m.rsplit(":", 1)[1] for m in mounts]
    assert sum(1 for src, dst in zip(sources, targets) if src.endswith("/bridge") and dst == "/opt/bridge") == 1, mounts
    assert not any("docker.sock" in m for m in mounts), mounts
    assert not any(src.endswith(".runtime_governance_state") or src.endswith(".runtime_governance_state}") for src in sources), (
        f"the assistant mounts the runtime's state root itself: {mounts}"
    )


def test_the_assistant_holds_exactly_its_three_values_and_nothing_of_the_surfaces():
    """Hermes cannot read exchange / write secrets: its environment draws exactly the OpenRouter
    key and its own bot token from `.env` (the allowed-user id is not a secret), and none of the
    live-trading surface, the Naver credentials, or the doors' peer-gate variables — a peer-gate
    value here would let it impersonate a door's declared client on the worker socket."""
    environment = _service(ASSISTANT)["environment"]
    secrets = {
        name for value in environment.values()
        for name in _ENV_REFERENCE.findall(str(value)) if SECRET_NAME.search(name)
    }
    assert secrets == {"OPENROUTER_API_KEY", "HERMES_BOT_TOKEN"}, sorted(secrets)
    forbidden = set(LIVE_TRADING_SURFACE) | {"MVP_BRIDGE_CLIENT_UID", "MVP_BRIDGE_CLIENT_GID"}
    referenced = {name for value in environment.values() for name in _ENV_REFERENCE.findall(str(value))}
    assert not (forbidden & referenced) and not (forbidden & set(environment)), (
        f"the assistant is handed {sorted((forbidden & referenced) | (forbidden & set(environment)))}"
    )
    assert environment["HERMES_UID"] == "10000" and environment["HERMES_GID"] == "10000"


def test_memory_ceilings_sit_on_everything_but_the_money_path():
    """Thomas decision Q15-b: the two scheduler lanes are what the OOM killer must never pick,
    so they carry no ceiling and every other long-lived process does — the assistant, the
    worker, the operator. The doors are tiny and short-lived per request; no ceiling there."""
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    limited = {name for name, spec in compose["services"].items() if spec.get("mem_limit")}
    assert limited == {ASSISTANT, "pipeline-worker", "operator"}, sorted(limited)
    for lane in LANES:
        assert "mem_limit" not in compose["services"][lane] and "memswap_limit" not in compose["services"][lane]
