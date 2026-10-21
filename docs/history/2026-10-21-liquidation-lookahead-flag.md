# Liquidation lookahead: the INVALID reason and its fix instant

- **Why:** the point-in-time contract test (#1281) found that the daily liquidations had the OI lookahead. The
  rows were labelled with the day's start, carried the day's total, and were aligned on the label. #1281 fixed
  the alignment. Evidence minted before that fix went live is INVALID by the same reasoning as the OI rows.
  Under E1 (#1280) that takes one reason and one instant.
- **What:**
  - `candidate_ranking.LIQUIDATION_FIX_EFFECTIVE_AT = "2026-10-21T09:04:45Z"`. This is the restart after the
    `candidate-1282` deploy (thomas-scheduler StartedAt 09:04:44.514Z), rounded up like `OI_FIX_EFFECTIVE_AT`.
  - `liquidation_lookahead_affected` follows the OI rule on `LIQUIDATION_LOOKAHEAD_FEATURES`: an entry that reads
    one of them, any timeframe but 1d, minted before the instant. Both predicates now share
    `_sub_daily_entry_reads`.
  - `REASON_LIQUIDATION_LOOKAHEAD` is INVALID, so the row is out of parents, centres and observation entry,
    ranks last, and its CONFIRMED is capped.
  - The LIVE door (`assert_live_tier_confirmed`) now refuses on `evidence_invalid` instead of on the OI flag
    alone. A new INVALID reason closes it without an edit there; the message names the reasons.
  - `LIQUIDATION_FIX_EFFECTIVE_AT` joins the judgement fingerprint (`NOT_INDEXED`). This follows decision 2's
    reasoning for the OI instant: it turns evidence into a verdict.
- **Size at lake `20261021T081702Z`:** 1 row, a 1h spec with no CONFIRMED. No re-score: one row does not pay
  for an E2 run.
- **Checked:** the standing reason, the LIVE door refusal and its message, and a 1d or post-fix lineage still
  arming. Mutations fail a test: the reason removed, and the door reading only the OI flag.
