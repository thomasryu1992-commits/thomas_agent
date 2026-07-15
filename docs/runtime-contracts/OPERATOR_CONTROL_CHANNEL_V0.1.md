# Operator Control Channel (R4) — v0.1

**Status:** Active (MVP runtime). **Normative authority:** None — the canonical
`governance/GOVERNANCE_POLICY.yaml` `control_channel` block and `runtime/mvp_runtime/`
code remain authoritative; this document describes the runtime behavior.

The operator control channel lets **Thomas** submit a request over Telegram and get the
analysis back. It is the inbound/outbound surface for the same single-agent pipeline the
one-shot CLI drives — with a strict identity gate in front and the network transport behind
the Safety-Flag Gate.

## Identity gate (fail-closed)

Per the canonical policy: the primary channel is `TELEGRAM_PRIVATE_1_TO_1`, the required
approver is Thomas, and **both** a registered user id and a registered private-chat id must
match. `verify_control_channel` enforces exactly this and refuses everything else:

| Condition | Reason code |
|---|---|
| Not a private 1:1 chat (group / channel) | `NOT_PRIVATE_CHANNEL` |
| Forwarded message | `FORWARDED_MESSAGE` |
| Sender is not the registered operator | `UNREGISTERED_USER` |
| Message is not in the registered private chat | `CHAT_NOT_REGISTERED` |

An unverified message **runs no task**, and in the loop it is **silently dropped** — no
reply — so the bot never engages an unregistered sender.

## Safety-Flag Gate

The real Telegram transport makes outbound HTTPS calls, so it is gated exactly like the
model provider and the search tool. `select_operator_channel()` returns the network-free
`MockOperatorChannel` by default; it returns the real `TelegramChannel` only when
`MVP_OPERATOR_CHANNEL=telegram` **and** a valid local activation authorizes `network_access`.
`TelegramChannel` re-verifies the authorization at socket-open time (defense in depth) and
reads the bot token from an env var **by name** (the Telegram API places it in the URL path
over HTTPS; it is never logged, echoed, or included in an error). A read-only control channel
needs only `network_access`, not `model_invocation`.

## Registration (local, per-machine)

The single authorized operator is recorded at `.runtime_governance_state/operator_registration.json`
(gitignored, per-machine — like the Core pointer and safety-flag activation). It is loaded
fail-closed: with no registration the channel is inactive (`REGISTRATION_MISSING`).

```json
{ "operator_id": "<telegram-user-id>", "chat_id": "<private-chat-id>", "approver": "Thomas" }
```

Telegram ids are identifiers, not secrets; they are stored locally because they are
deployment-specific, not because they are sensitive.

## Running it

```bash
# One poll batch with the mock channel — a network-free smoke test:
python -m runtime.mvp_runtime.operator_cli

# Continuous, against real Telegram (needs the registration file, a bot token, and an activation):
python scripts/activate_safety_flag.py --provider-id telegram --flags network_access \
    --authority-level P2 --ttl-minutes 240 --reason "Operator decision: enable the Telegram control channel."
export TELEGRAM_BOT_TOKEN=...              # Windows: setx TELEGRAM_BOT_TOKEN ...
MVP_OPERATOR_CHANNEL=telegram python -m runtime.mvp_runtime.operator_cli --max-batches 0 --sleep-seconds 2
```

Without the registration file the loop exits `REGISTRATION_MISSING`; without the activation it
exits `ACTIVATION_MISSING`; without the token it fails closed `NO_BOT_TOKEN`. Every handled
request is persisted to the same append-only ledger as the one-shot CLI.

## Key modules

- `runtime/mvp_runtime/operator.py` — identity gate, registration, `OperatorChannel` /
  `MockOperatorChannel` / `TelegramChannel`, `select_operator_channel`, `handle_operator_message`,
  `run_operator_once`.
- `runtime/mvp_runtime/operator_cli.py` — the loop entrypoint.
- `scripts/activate_safety_flag.py` — activate the `network_access` flag for `telegram`.

## Not yet implemented

Emergency operator-console controls (`pause` / `stop_task` / `kill` / `status` / `audit` /
`recovery`) and control-channel **approval** handling (for any future `APPROVAL_REQUIRED`
action — the MVP is ALLOW-only today) are later increments. The policy already forbids new
high-risk approval creation from the local console.
