# 2026-11-12 — what each result of the 11-14 mint-share read leads to, written before the read

`docs/proposals/MINT_REALLOCATION_PRE_WINDOW_V0.1.md`, "2026-11-12 추가: 11-14 읽기의 결과별 다음 행동". Documents only.

**Why.** The read's criteria were registered on 2026-10-25 (1h 36%, 4h 37.5%, within 10 points, eight mints a fire,
one read). Nothing said what happens when it falls outside, here or in any document that waits on the read. A rule
chosen after the number is a rule fitted to it. Thomas asked for it to be written down first (2026-11-12, "적어 두기").

**What the read is.** A mechanical check that the tilt delivers the share it was designed to. Inside does not say the
six families are better; outside does not say they are worse. I had called an outside result "bad" when I proposed
this; the section corrects that.

**What was pinned, from the registered words only.**
- The number that decides is the share per timeframe, because the registration names timeframes. The reading tool
  printed context lines only, so it never printed that number; it now prints a line per timeframe (outside the repo,
  checked on fabricated rows, context lines unchanged).
- "One read" is the first printed shares over a snapshot taken after the last fire of 11-13. A missing snapshot or a
  crash before that is not a read. Diagnosis may reopen the rows; it never yields a second verdict.
- The window is by date. A window short of fires is still the registered read, labelled with its fire counts.

**What follows each result.** Outside is not a revert condition: the two registered ones stand (the H2 verdict on
2027-03-22, the next pre-window replay). Outside leads to a diagnosis from the code and, if it is a defect, a fix by
PR as #1300 was; a new confirmation is a new registered window. No correction made after the read turns outside into
inside. Causes are not listed, because the topup code was not traced for this.

**What waits on "after 11-14".** Sorted by one test: does it change the non-trial 1h/4h mints? The trial-channel PR
(F6), the design review's Q1 and the netting stage-1 proposal do not, so they open once the read is done, whatever it
says. Anything that changes templates, families or the rotation would wait for a re-registered window, if one is set.

**What was already seen of the window.** The six-family share is known for 2026-10-24 and three fires of 10-25, in the
document since 10-25, and was never computed after that. The same file was read for other work, though: row counts by
context and by derivation type (11-05, 11-10), row counts per day from 11-01 (11-10), the trial rows, and the ids and
mint times of the lineages in the forward book (11-12). My first draft said the rest of the window "was not read"; the
session's own command record says otherwise, and the section now lists what was looked at. No count by family was.

**One reading is the tool's, not the registration's.** The share leaves crossover rows out of both numerator and
denominator. The registered words drop the crossover COUNT as a criterion and compute the expectation at zero
crossover; they do not say the rows leave the share. The tool has done it that way since it was written on 11-10,
before any window share was computed. It is listed in Q5 as its own item.

**The read is made mechanical.** The first output goes to a file that refuses to be overwritten, over a snapshot
resolved to its named directory. The registered 36% and 37.5% decide; the pre-fix BTCUSDT 1h fires move the expectation
by at most 0.25 points and are a note, not a second verdict.

Open: Q5 (keep this reading and these actions), default yes. The read happens once on 11-14 under the registered
criteria whether or not Q5 is answered.
