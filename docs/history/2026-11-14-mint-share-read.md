# 2026-11-14 — the mint-share read, once: within range on both timeframes

**Shipped (a record; no code):** `docs/proposals/MINT_REALLOCATION_PRE_WINDOW_V0.1.md` gains "11-14 읽기의 결과"
and moves to IMPLEMENTED.

**What was read.** The mechanical check registered on 2026-10-25 (Q4, Q4a) and given its reading rules on
2026-11-12 (Q5): over the fires of 2026-10-24 to 11-13, does the tilt produce the share it was designed to? The
six families' share of mints came to 35.2% at 1h (expected 36.0%) and 37.2% at 4h (expected 37.5%), both inside
the ±10-point band, and every fire wrote eight rows. That is result A of the table written before the read.

**Choices that are not obvious.**

- *The read is the first output and only that.* It was run by the one-shot command in the document, against the
  first snapshot dated 2026-11-14, and its output file now stops the command from running again. The tool was not
  edited between its last change (2026-11-12, checked on made-up rows) and the read.
- *A pass says the rotation works as designed and nothing else.* It does not say the six families are better. The
  two registered revert conditions stand, and the tilt is not leaned further.
- *The window's last day ran newer code, and the record says so.* #1462 and #1463 were merged and deployed about
  five hours before the last fires. They touch the trial channel only; the one trial row at 4h is counted apart,
  as rule 5 says.
