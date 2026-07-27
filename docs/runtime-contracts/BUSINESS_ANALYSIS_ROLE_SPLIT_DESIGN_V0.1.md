# business.analysis vs general.specialist — Role Split Design Record v0.1

**Status:** PROPOSED — `business.analysis` stays a **candidate**, and is **deprioritized**.
**Owner:** Thomas
**Authority:** None. The Role Registry owns status and routability; the Governance Policy owns
permission. This record explains a decision, it does not enact one.
**Decided:** 2026-07-27 — Thomas: *"사업분석은 굳이 지금 안 해도 되는데."*

## The decision, so it is not re-opened

**Not now, and not because it is blocked — because it is not worth the spend.** Four options were
put up (widen `general.specialist`'s output contract and retire the candidate; run the Candidate
Trial; activate on direct authority; leave it); the answer was that business analysis does not
need doing right now at all.

That is a **priority** answer, not a classification one, and it settles more than the four options
did: none of the work below gets started, including the trial. The runtime keeps doing what it
does today, which is the point of the next section.

**Do not re-open this from the routing table.** The absence of a `business` request kind is this
decision. If the question arises again, the thing that changed should be named first — see *What
would make this a priority* — because nothing in the analysis below is expected to change on its
own.

## The question

`research.general`, `translation.general`, `content.general` and `development.general` were
activated on 2026-07-27 and given request kinds. `business.analysis` was not. Its capabilities —
`opportunity_analysis`, `option_comparison`, `revenue_potential_assessment`,
`downside_risk_assessment`, `small_validation_design` — sit on top of the MVP's **core** use
case, which `general.specialist` already serves. So it is the one activation that is not a
routing question.

## The answer was already written, in the place nobody reads

`03_ROLE_CONTRACTS/ROLES/CANDIDATES/BUSINESS_ANALYSIS_ROLE.md` states it in its own body:

> 현재 General Specialist가 초기 사업 분석을 수행한다. 반복 사례를 통해 별도 평가 기준의
> 유효성이 확인되면 Candidate Trial을 거쳐 활성화를 검토한다.

and its front matter turns that sentence into two gates:

```yaml
activation_conditions:
- business_analysis_tasks_repeat
- dedicated_scoring_or_evidence_rules_are_validated
```

So the split was decided when the contract was written. What was missing is that it lived in one
sentence at the bottom of a candidate contract, while the question kept being asked at the
routing table — which is why this record exists, and why the routing table now points at it.

## The boundary, in operational terms

The distinction is not "who knows about business". It is **what the run produces**:

| | `general.specialist` (active) | `business.analysis` (candidate) |
|---|---|---|
| Input | one idea | several options |
| Deliverable | an analysis | a comparison and a decision |
| Output contract | `key_findings`, `evidence_quality`, `unresolved_questions`, `perspectives` | `opportunity_summary`, `options`, `revenue_assessment`, `downside_risks`, `validation_plan` |
| Revenue / risk | judged as two of the three §10.4 perspectives | scored as first-class fields |
| Ends with | findings and disclosed limitations | a recommendation and a validation plan |

Read that way the two are not duplicates: today's role evaluates **one** idea and discloses what
it could not verify; the candidate compares **options** and designs the cheapest experiment that
would settle the choice. The overlap is in capability *names*, not in deliverables.

Which means the honest statement of today's behaviour is: **"analyse this business idea" is
`general.specialist`'s job, and it is the right role for it** — not a stopgap. There is no
`business` request kind, and its absence is this decision rather than an omission.

## Against §13, the architecture's own separation test

§13: consider a separate Agent when **three or more** answers are YES.

| | Criterion | Verdict | Why |
|---|---|---|---|
| 1 | Different specialist knowledge? | marginal | Both evaluate business ideas. The candidate's edge is a scoring method that does not exist yet — which is exactly what gate 2 of its own contract asks for. |
| 2 | Different goal? | **YES** | Analysis of one idea vs. comparison of options plus a validation plan. |
| 3 | Requires independent review? | **YES** | And on *different* triggers: `material_business_recommendation` / `strategic_resource_allocation`, not `general.specialist`'s five conditions. |
| 4 | Different data permission? | no | Identical memory scopes, identical P3 ceiling. |
| 5 | Separate performance metrics? | no | None defined. Nothing measures "was the recommendation any good". |
| 6 | Conflict of interest? | no | — |

**Two solid YES of six.** Below §13's threshold — and the two clear NOs (1 and 5) are the same
two things the contract's own gates ask for. The architecture's criteria and the contract's gates
agree, so this is not a close call resolved by preference.

## What would make this a priority

Neither of these is being waited on or measured. They are here so that a future *"should we
revisit?"* has an answer sharper than a re-reading of the same contracts:

- **A real request the runtime cannot serve.** Someone asks for three options compared with a
  validation plan, and the answer today is a single-idea analysis. That is the gap this role would
  fill; until someone actually wants it, filling it is speculative — §16's guardrail exactly.
- **The two contract gates below**, which remain the formal bar whenever the priority question is
  answered differently.

## The contract's own gates, and how to check each

- **`business_analysis_tasks_repeat`** — the programization counter already counts repeated work,
  but read it carefully before quoting it: `build_pattern_signature` keys on `role_id` plus the
  pipeline shape, **not** on what the request was about. Because the MVP's only use case *is*
  business-idea analysis, that count is a usable proxy today — but it is a proxy by coincidence of
  the use case, not by design, and it stops being one the moment a second kind of work routes to
  `general.specialist`. Read it with `programization_cli`.
- **`dedicated_scoring_or_evidence_rules_are_validated`** — nothing satisfies this today. What
  exists is `general.specialist`'s: `EVALUATION_PRIORITIES` (Core `MVP_RULE_005`) and the §10.4
  perspectives. A separate role needs its own scoring rules **and** evidence that they beat the
  current ones; §13 criterion 5 is that same requirement wearing a different name.

Both must hold. One without the other is repetition with no better method, or a method with
nothing to apply it to.

Worth naming, since it is the reason the gates cannot resolve themselves: **gate 1 cannot be
satisfied by waiting.** §12 asks whether the existing Agent handles the work badly, and nobody has
tried — there is no way to ask this runtime for an option comparison today, so no evidence of
failure can accumulate. The Candidate Trial exists precisely to break that circularity. It is not
being run, by the decision above; that is a choice about spend, not a claim that the evidence
exists.

## What activation would cost, if it is ever picked up

Not a plan — a price list, recorded while the context is fresh so a later session does not
rediscover it:

1. **A Candidate Trial first.** Its own contract requires one ("Candidate Trial을 거쳐") — a
   stronger bar than the four roles activated on 2026-07-27, which went in on Thomas's direct
   authority with no trial record. That asymmetry is deliberate and should not be levelled down:
   this is the one role whose activation changes what the core use case does.
2. **A request kind whose capabilities are role-unique.** `opportunity_analysis` and
   `small_validation_design` qualify; a set leaning on shared names would be `AMBIGUOUS_ROLE`.
3. **A decision about the default.** With both roles routable, an unmarked "analyse this business
   idea" still goes to `general.specialist`. If that is wrong, the default changes — a bigger
   change than the activation itself.
4. **A fixture role for the trial suite.** `business.analysis` is now the last non-live candidate,
   so `tests/test_mvp_runtime_trial.py` rests on it staying one. Activating it means giving those
   tests a role of their own instead of borrowing a production one.

## Not decided here

Whether `general.specialist` should eventually *shed* business analysis. It holds it today
because it is the MVP use case; if the candidate ever activates, the two contracts compared above
are the split, not a hand-off.
