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
MVP_OPERATOR_CHANNEL=telegram python -m runtime.mvp_runtime.operator_cli --max-batches 0 --long-poll-seconds 25
```

`--long-poll-seconds N` makes each `getUpdates` hold the connection open server-side until a
message arrives (up to N seconds) before returning — efficient continuous listening; the HTTP
timeout is extended past the hold so it is never aborted early. `0` (the default) returns
immediately (a short poll), which is what the mock smoke test and the tests use.

Without the registration file the loop exits `REGISTRATION_MISSING`; without the activation it
exits `ACTIVATION_MISSING`; without the token it fails closed `NO_BOT_TOKEN`. Every handled
request is persisted to the same append-only ledger as the one-shot CLI.

## Emergency console (pause / stop_task / kill / resume / status)

Per `governance/GOVERNANCE_POLICY.yaml` (`control_channel.local_operator_console` +
`kill_switch`), the operator can halt the runtime — over the verified Telegram channel **or**
directly on the host (an SSH emergency stop when the channel is down). Both act on one local,
gitignored control state (`.runtime_governance_state/operator_control_state.json`) that the loop
enforces, and both record a tamper-evident control event to the durable ledger
(`control_events.jsonl`).

| Command | Effect |
|---|---|
| `/status` | Read-only report of the control state (always allowed, even when KILLED). |
| `/pause` | Refuse new task requests until `/resume`. Reversible. |
| `/kill` | Block all new/pending execution; only status and audit reads remain. |
| `/resume` | Clear pause/kill → ACTIVE. Only the **authenticated operator** may resume. |
| `/stop <task_id>` | Record a stop request. The MVP runs tasks synchronously (nothing long-running to interrupt yet), so it is logged for audit and applies once R6 adds persistent tasks. |

Enforcement and fail-closed rules:

- Only `ACTIVE` lets the loop start a task; `PAUSED` and `KILLED` refuse task requests
  (`RUNTIME_PAUSED` / `RUNTIME_KILLED`) **before** any run.
- A **missing** control file means ACTIVE (a fresh deployment must not be bricked by absence);
  a **present but unreadable/invalid** file fails closed to **KILLED** — a corrupt safety state
  never silently re-enables execution.
- The agent/runtime can never disable or bypass the control (`agent_can_disable_or_bypass:
  false`): nothing on the run path clears a pause/kill, only an explicit operator command does,
  and an impostor who fails the identity gate can never `/resume`.

```bash
# Local host console (works without Telegram):
python -m runtime.mvp_runtime.console_cli status
python -m runtime.mvp_runtime.console_cli kill --reason "halt now"
python -m runtime.mvp_runtime.console_cli resume --reason "cleared"

# Over Telegram: the registered operator texts /status, /pause, /kill, /resume, /stop <id>.
```

## Key modules

- `runtime/mvp_runtime/operator.py` — identity gate, registration, `OperatorChannel` /
  `MockOperatorChannel` / `TelegramChannel`, `select_operator_channel`, `handle_operator_message`
  (console-command routing + PAUSED/KILLED enforcement), `run_operator_once`.
- `runtime/mvp_runtime/control.py` — `ControlState` / `ControlStore`, `parse_command`,
  `apply_command`; the local control state and its fail-closed semantics.
- `runtime/mvp_runtime/console_cli.py` — the local host emergency console.
- `runtime/mvp_runtime/operator_cli.py` — the loop entrypoint (defaults a `ControlStore`).
- `scripts/activate_safety_flag.py` — activate the `network_access` flag for `telegram`.

## Not yet implemented

Control-channel **approval** handling (for any future `APPROVAL_REQUIRED` action — the MVP is
ALLOW-only today) is a later increment; the policy already forbids new high-risk approval
creation from the local console. `audit` / `recovery` console verbs beyond `/status` and the
durable control-event log are also later.
