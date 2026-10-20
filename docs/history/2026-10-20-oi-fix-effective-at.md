# The OI fix instant is recorded: lineages minted after the deploy are judged normally

- **What:** `candidate_ranking.OI_FIX_EFFECTIVE_AT` = `2026-10-20T13:59:48Z`.
  - That is the next second after thomas-scheduler restarted on `candidate-1271` (StartedAt 13:59:47.909Z). Thomas
    deployed main `4dc3e689`, which carries #1269, #1270 and #1271.
  - The value is rounded up, so a row stamped inside that second counts as pre-fix, which only withholds.
- **Effect:** sub-daily OI lineages created at or after that instant are no longer flagged. Their holdout and forward
  record are judged like any other. Lineages created before it stay flagged (holdout CONFIRMED reads UNDERPOWERED; LIVE
  refuses).
- **Why a constant:** the stores are sealed and the flag is derived at read time, like `holdout_reused`. The
  instant is a fact of this host's deploy, recorded in code where the predicate reads it.
- **Test:** the real constant splits a mint at 13:59:47Z (flagged) from one at 13:59:48Z (not flagged).
