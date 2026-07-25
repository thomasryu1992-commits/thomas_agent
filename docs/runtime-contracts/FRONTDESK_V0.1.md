# Conversational Frontdesk v0.1 (F2)

**Status:** Active runtime capability (role activated by explicit Thomas decision D2, 2026-07-25)
**Owner:** Thomas
**Authority:** None. This document describes an implementation; the role's authority
boundaries live in `03_ROLE_CONTRACTS/CONVERSATION_FRONTDESK_ROLE.md` and the canonical
Governance Policy owns permission/effect.
**Design context:** `docs/proposals/CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md` Part ①.

Thomas talks to the Telegram channel in plain language; the front desk turns each message
into exactly one governed action. It holds the conversation; it never holds authority.

## The turn loop

```
plain text → (kill-switch gate, marker parse — both BEFORE any model call)
           → front-desk model call (gated provider, 1 call, FRONTDESK_TOKEN_ALLOWANCE)
           → closed frontdesk_turn.v0.1 extracted from the shared analysis JSON
           → validate → dispatch one of seven turns → reply
```

| turn | runtime action |
|---|---|
| `SUBMIT_TASK` | F1 `enqueue` (origin `FRONTDESK`), after the verbatim check |
| `QUERY_STATUS` / `QUERY_HISTORY` / `QUERY_RESULT` | the `registry_console` appliers — the same code as `/tasks` `/history` `/result` |
| `CANCEL_TASK` | the `/cancel` applier (kill-switch bound inside it, QUEUED only) |
| `CLARIFY` / `CHAT_REPLY` | reply only; nothing happens |

**Deterministic data beats narration.** For the QUERY/CANCEL turns the model's
`reply_text` is dropped and the console's own rendering is sent: a narration of
coordination state can be stale or invented, and the listing cannot. The conversational
door does not get its own account of the truth — it *is* `/tasks`.

**Deterministic intent never waits on a model.** `/verbs` and `!중요`-marked requests
bypass the front desk entirely; only unmarked plain text is a conversation turn.

## Reuse (what this deliberately did NOT build)

- **No new provider surface.** The turn rides in the shared analysis JSON
  (`recommendation.turn`) that every gated provider already parses — the R7.2 triage
  precedent. `MVP_FRONTDESK_PROVIDER` has exactly `MVP_VALIDATOR_PROVIDER`'s chain
  semantics: per-member grants, a chain never silently shrinks, env var alone opens
  nothing (D3 stays per-machine).
- **No new store.** Session context is R5 working memory under the role's own
  `frontdesk_session` scope: expiring (12h), prunable by the same retention pass, readable
  by no other role — and the reverse read is prohibited too (the front desk cannot read
  `task_working_memory`; results travel only through `QUERY_RESULT`).
- **No new console.** Query/cancel dispatch calls the F1 appliers.

## Fail directions (each chosen once)

- **Provider failure → degrade to the F1 path.** `FRONTDESK_DEGRADED` is audited and the
  raw message continues down the plain enqueue exactly as if the feature were off —
  conversation dies, the channel lives, no message is lost (the `SEARCH_DEGRADED`
  direction). A greeting queued during an outage is a `/cancel` away; a lost request is
  gone forever. That asymmetry decides the direction.
- **Invalid turn → downgrade to `CHAT_REPLY`.** `FRONTDESK_TURN_INVALID` audited, nothing
  submitted, the model's own summary is the reply. The contract's
  `invalid_turn_downgrade`, executable.
- **Verbatim mismatch → no submission.** `SUBMIT_TASK.request_text` must be the
  operator's words: a normalized substring (whitespace is the one liberty — Telegram
  wraps lines; characters are not) of the current message or one in the session window.
  A paraphrase never becomes the pipeline's input under Thomas's name
  (`FRONTDESK_VERBATIM_MISMATCH`); multi-turn intent ("아까 그거 분석해줘") is served by
  quoting the earlier message verbatim, which the same window supplies.
- **Kill switch → the conversation LLM stops.** Enforced at the channel's existing
  plain-text gate, before any model call, once — this module owns no second copy of the
  rule.

## Activation is load-bearing

`select_frontdesk_provider` refuses (`FRONTDESK_ROLE_INACTIVE` / `_HASH_MISMATCH` /
`_UNRESOLVED`) unless the registry carries `conversation.frontdesk` as **active**,
**non-routable**, with a **matching definition hash**. A provider env var against a
candidate role fails at startup — the env var is a request; the registry's D2 flip is the
grant. The flip itself was made by updating the pinning test
(`test_activation_is_a_separate_explicit_decision`), which now pins `active` so a future
deactivation is equally deliberate.

## Audit

Every turn appends one `frontdesk_turn.v0` block event (kind, outcome, model id, token
usage), best-effort like the operator-probe events; SUBMIT's durable record is the
registry entry + the run's own ledger trail, and QUERY events are read-only (the
`/status` precedent). Known limit, stated: the turn's model call carries no per-call
PermissionDecision — a permdec binds to a task and no task exists yet at conversation
time. The precedent is the channel's other ALLOW actions (acks, sends, probe records);
a session-task concept that could host one is deliberately out of scope.

## Deliberately excluded

- Front-desk tool/search use, memory promotion, approval handling — the contract's
  `unsupported_capabilities`, enforced by there being no code path and no schema field.
- Inferring `important`/`independent_validation` from tone (contract: words only).
- A queue-jumping priority for conversational submissions — FIFO is FIFO.
