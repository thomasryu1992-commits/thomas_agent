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
           → closed frontdesk_turn.v0.3 extracted from the shared analysis JSON
           → validate → dispatch one of ten turns → reply
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

**Deterministic intent never waits on a model.** `/verbs` and `!중요`/`!번역`-marked requests
bypass the front desk entirely; only unmarked plain text is a conversation turn.

## v0.4 — `clarification_texts`, or: `CLARIFY` was a dead end

`CLARIFY` asked a question and ended the turn. When Thomas answered, his answer was just a new
message — and the verbatim rule (a submission must be a substring of **one** message) made the
combination he actually meant the only thing that could not be submitted. Measured before
building anything, with "Prediction 데이터 분석해줘" → *(CLARIFY)* → "7일":

| submitted | verbatim check | outcome |
|---|---|---|
| the original alone | passes | the period is lost |
| the answer alone | passes | meaningless by itself |
| **both** | **rejected** | what he meant |

`SUBMIT_TASK.payload.clarification_texts` carries his follow-up messages verbatim; the runtime
appends them to `request_text` with newlines and submits that.

- **The verbatim rule is not weakened — it runs per segment.** Checking the composed string
  would pass a paraphrase glued between two real quotes; submitting only the segments that
  passed would silently drop the very answer this exists to carry. **One failure refuses the
  whole submission.**
- **Nothing of the front desk's own is added** — no labels, no `clarification:` prefix. Every
  character submitted is one Thomas typed. What changed is *which* of his words go in, never
  *whose*.
- **The receipt quotes the assembly.** Composing is the one thing he cannot see from his own
  scrollback — he said two things and one request went in — so when more than one segment
  survives, the composed text is echoed *before* the pipeline runs. A wrong assembly is a
  `/cancel`, not a wrong answer.
- Segments are **deduplicated across the whole request**, first occurrence winning. Found by a
  test rather than by reasoning: the first implementation deduplicated `clarification_texts`
  and `request_text` separately, so a model that echoed the request into the list produced it
  twice. A repeat is never *dangerous* (every segment is still his words) but it reaches the
  specialist as an emphasis he did not write.
- **No registry change.** An earlier sketch of this item assumed a `task_registry_entry` state
  (`WAITING_FOR_INPUT`) and a resume path. That was the wrong shape: the clarification happens
  *before* anything is queued, so there is no entry to suspend — and a state nothing can
  produce is worse than no state. The pipeline still receives one complete request, once.

## v0.3 — `request_kind`, or: the conversation could reach one Role

The marker parse above runs *before* the front desk and the front desk runs only for an
**unmarked** message. So until v0.3 a conversational `SUBMIT_TASK` queued with no kind and
routed to the analysis Role — of the six activated Roles, five were reachable only by typing
`!번역`/`!조사`/`!콘텐츠`/`!개발`, which is the path that skips the conversation. "Talk to it
like a chat" and "use the Roles that were built" were the same sentence and could not both be
true.

`SUBMIT_TASK.payload.request_kind` closes that, and it is a routing **signal**, not a routing
**decision**: it names capabilities, the Role Registry alone maps those to a Role, and Prime
still owns classification, permission level, validation requirement and selection. There is
still no payload field that can name a Role, a tool, a provider or a permission — asserted
directly (`test_no_turn_can_name_a_role_or_reach_one_directly`).

Three lists must agree — the prompt's, the schema enum, and `planner.REQUEST_KIND_CAPABILITIES`
— or a submission dies at validation or at the queue's far end, where the operator would see
only a downgrade. They are built from one dict and pinned by a test.

The honest cost, stated: `REQUEST_KIND_CAPABILITIES`'s own comment says a kind should come
from an explicit marker because *inferring* one is a guess, and a wrong guess routes work to a
Role with a different output contract. That reasoning still holds; the risk is now taken
**visibly** rather than avoided:

- the prompt binds the choice to Thomas's **words** (the `important` discipline), and
  anything unclear is `null` → analysis;
- the queue receipt **names the kind it read**, and arrives *before* the pipeline runs — so a
  misread costs a `/cancel`, not a wrong-shaped answer twenty seconds later;
- an unroutable kind is **refused, never defaulted** — the front desk asks
  `capabilities_for_request_kind` rather than keeping a second, more lenient copy of that rule.

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
