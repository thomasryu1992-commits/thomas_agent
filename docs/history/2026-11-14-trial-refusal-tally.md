# 2026-11-14 — a read of where the hypothesis channel's proposals fall

`scripts/trial_refusals.py`, `tests/test_trial_refusals.py`, the script roster.

**What it is for.** The model proposer's channel has two deterministic stages that turn proposals down: the
proposer's own evaluation (`parse`, `timeframe`, `form`, `validator`), and the trial screen on a cohort fire
(`known_family`, `duplicate_rule_hash`, `unsuppliable_feature`, the lattice, `scoring_error`, `no_trades`,
`pace_unreadable`, `too_slow`). Each stage records its reason. The reasons were read back only into the next
prompt; no read showed them side by side. The discovery loop's §7 had planned to find out "where proposals fall"
by pushing hand-written proposals through the screen on past data. Once the reworked channel was running
(2026-11-13), the real fires' refusals answer that, and what was missing was a way to see them.

**Choices that are not obvious.**

- *It does not reuse the proposer's refusal readers.* `recent_screen_refusals` keeps one reason per family and the
  newest twenty, which is right for a prompt and wrong for a count. `noting_screen_refusals` drops the minted
  entries, the fire's status and its time. The tally reads the fire's `trial` block itself and imports only the
  two ledger kinds and the two shape patterns.
- *The first number is how many were screened.* A stage that screened nothing has nothing to say about where
  proposals fall, so the counts of fires, of fires that were not asked, and of each fire status come before the
  reasons. Statuses are printed as recorded (held to a shape), not from a list, since older rows carry statuses
  today's code no longer writes.
- *Safe by construction, not by omission.* Both record kinds carry numbers a search could fit to (expectancy,
  scores, candidate evidence, a too-slow refusal's expected trades) and model-written text. The output is built
  from a closed set of keys, every name is held to a shape or counted as `(other)`, and a proposer rejection of
  the form `parse: <exception text>` keeps only its name. The test plants a sentinel in every other field and
  looks for it in the text and the JSON output.
- *Window on the record's own time.* The ledger's `appended_since` only bounds which archive files are opened, so
  each row is still filtered on its `created_at`. `--since` exists beside `--days` because a 30-day window mixes
  fires from before and after a deploy; the per-day lines show the same thing without a flag.
- *The usage exit code.* argparse exits 2 on a typo, and 2 is this runtime's BLOCKED. The parse is wrapped so a
  typo is `EXIT_USAGE`, as `claude_read.py` does.
- *READ, with no host-root guard.* It writes nothing. The roster holds that no READ script calls the guard.

**What it does not say.** A count of refusals by reason is not a judgement of the channel, and nothing here reads
a return. The numbers will be small for a while: one cohort fire a day screens at most three proposals.
