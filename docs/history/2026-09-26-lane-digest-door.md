# The assistant gets the weekly lane digest as a dormant read, and the policy line that wakes it is Thomas's

- **The ask:** Thomas decided 2026-09-26 (system review D8) that a non-crypto lane with no evidence of
  use and quality by 2026-11-25 is reviewed for removal, and that the weekly digest is Hermes's to run.
  The digest existed only as `scripts.lane_digest`, in the scheduler container, and the assistant reads
  only through the read door, whose verbs are closed in the policy
  (`control_channel.assistant_read.verbs`, pinned both ways by `tests/test_policy_assistant_read_clause.py`).
- **What changed:** the read door carries `lane_digest` — the CLI's fold and text over N days (default 7,
  at most 31, a clamped window said so in the reply), counts only — **dormant**: it refuses by name as
  `CONTROL_VERB_NOT_GRANTED` until the committed policy lists it (`read_bridge.POLICY_GATED_READS`,
  `control.granted_read_verbs`), the switch door's pattern for `emergency_close`. The other reads never
  consult the policy. The pin test now holds "every listed verb is served, and a served verb that is not
  listed is dormant". Hermes gets `lane_digest(days)` (shim 2.15), a line in SOUL.md, and a fifth cron job,
  주간 레인 요약 (Mondays 00:10 UTC), that relays the counts unchanged.
- **The policy half is a draft, not an edit:** `docs/runtime-contracts/POLICY_1_6_1_DRAFT.md` and
  `scripts/ops/policy_bump_1_6_1.py` (decision Q2: written together, applied by Thomas), with
  `tests/test_policy_bump_1_6_1.py` checking the anchors, the written policy and the end-to-end switch
  before anyone runs it. The script also refuses on an APPROVED-unspent approval, which 1.6.0 checked by
  hand.
- **The cost, stated in the draft:** `assistant_read` is not a safety section, but the execution stage
  binds the policy version, so the deploy that carries 1.6.1 puts the stage at READ_ONLY — new live
  entries refused, the slippage probe included — until Thomas approves a REBIND.
- **Deliberately not done:** a scheduler report kind that would have sent the digest to Telegram with no
  policy change. Cheaper, but it would not be Hermes running it, which is what was decided; it is the
  fallback if the REBIND is judged not worth a weekly read.
