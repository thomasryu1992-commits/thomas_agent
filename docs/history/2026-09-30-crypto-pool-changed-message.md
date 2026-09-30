# The promotion door's refusal says to look before repeating (follow-up to PR-S3)

- **What changed:** the text of `STRATEGY_POOL_CHANGED`, the refusal the promotion door's install
  gives when the pool changed after the door read it. It said "run the same command again". It now
  says to look at what changed first, and names the case that matters.
- **Why:** the independent review of PR-S3 (#1082) pointed out that the likeliest writer in between is
  the cycle disarming a LIVE entry whose allowance was spent. If the refused promotion was a restate
  of that very entry, running it again unchanged re-arms it on the same approval. The refusal is
  where the operator learns something moved, so that is where to say it.
- **What did not change:** when the door refuses, what it writes (nothing), and that the approval is
  not consumed. The door test now also checks that the refusal tells the operator to look first.
- **Seen after PR-S3 was deployed:** the cycle's lifecycle step rewrote the pool file on the first
  fire (17:14:40Z), stamping `updated_at` with no transition applied, so the file's digest can change
  on a fire with nothing of substance changed. A promotion that meets that write is refused and
  should simply be run again; the text now leaves that judgement to the operator.
