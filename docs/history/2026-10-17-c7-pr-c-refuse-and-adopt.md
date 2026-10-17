# C7 PR-C: a mismatch refuses the write, and Thomas adopts a ledger in two steps

**What changed:**
- `runtime/mvp_runtime/holdings/cash_flows.py` and `scripts/holdings_board.py` (`--adopt-ledger`, `--confirm`);
- one new test file, three existing C7 test files and the guard test;
- the LOCAL_ONLY guard (one word, separate commit).

**Approval:** `승인: C7 PR-C 구현 및 PR 생성(불일치 시 쓰기 거부, 두 단계 채택 명령; 병합·배포 제외) (Thomas 2026-10-17)`.
Design: `HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` §3, §7, §9, §10 (D1 and D3, Thomas 2026-10-11).

**Refusal (§3, D1: write refusal is switched on in PR-C only).**
- `_commit` refuses with `LEDGER_TAMPERED` when either holds:
  - the ledger does not continue the state's checkpoint (shorter, replaced, malformed);
  - a told exception key is missing from the ledger.

  The check runs under both locks, before anything is built, and writes nothing to the ledger or the state.
  Nothing is laid on a mixed history: the collectors, `mark_fire_verified`, `set_cutover` and `resolve` all
  go through `_commit`.
- The message names the detector and the way out, `holdings_board --adopt-ledger`.
- A marker that PR-A's detect-only code left in the state also refuses. A detection never clears itself;
  only an adoption clears it.
- PR-C writes no marker of its own: a refused write writes nothing.

**What the alert sees (PR-D).**
- `blocked_state` now judges the refusal itself: category `checkpoint`, reason code `LEDGER_TAMPERED`.
- The key is the checkpoint the refusal stands on: the state's, else the marker's, else the ledger's end.
  It is never the state's bytes, because readiness and the told records still save the state while the
  writes refuse.
- The key carries `refused`. It differs from PR-A's detect-only key, so a detection told before PR-C is
  told once more, as the refusal it now is.
- The text now says the writes were refused and that only Thomas's two-step adoption reopens them.
- The PR-A wording ("쓰기는 계속") is gone.

**Adoption (§7, D3).**
- **Step one** (`adoption_preview`) writes nothing. It prints:
  - the ledger's length and the first 8 hex characters of its last hash;
  - the checkpoint it would replace;
  - the detector;
  - how many told keys the ledger lost.
- **Step two** (`adopt_ledger`) takes those 8 characters back as `--confirm`. Under the state lock and the
  ledger lock, with the chain re-verified, it refuses unless the ledger still ends there.
- It writes one `ledger_adopted` line, with:
  - adopted end;
  - replaced checkpoint;
  - detector;
  - `told_keys_dropped` (a count, no digests);
  - requester, reason and time.

  The line erases nothing.
- Its key is `ledger_adopted:<end hash>`. Run again on the same end, it writes no line and only finishes
  the state (§7.5).
- If the process stops between the line and the state save, the next write finishes the adoption. That
  happens when the last adoption line names exactly the checkpoint the state still holds (§7.6). Until
  then the lane does not read BLOCKED.
- Both steps need an interactive terminal, `--by` and `--reason`, and a run that is not a foreign root run.
  All of that is checked before the ledger is read.
- Adoption is refused for:
  - a broken chain (`LEDGER_TAMPERED` from `verify`; a chain is not adopted);
  - an empty ledger;
  - a pending pre-H6d import;
  - a ledger that continues its checkpoint ("nothing to adopt").
- No output names an amount, an asset or an event key.

**What an adoption leaves, and what it costs.**
- The state after an adoption:
  - no marker;
  - the told keys the ledger lost, dropped (otherwise the second detector fires again at once);
  - the checkpoint at the new end, with `basis: observed`.
- **`observed_at` restarts at the adoption.** The adopted history is not the one first observed. If the
  old `observed_at` were carried, an adoption alone could make `history_completeness` PASS, which D3 forbids.
- The consequence: after any adoption the history reads `UNCHECKED`
  (`checkpoint_after_cutover`) until Thomas moves the cutover past the adoption
  (`--cash-flow-cutover AT --migrate --reason`), and the 7-day shadow observation starts again.
- What the cursors re-read after an adoption comes back:
  - C7-4 inside the overlap: P2 is found;
  - C7-4 past the overlap: P2 stays gone;
  - C7-6: P3 stays and P2 is not re-read.

  Under option C nothing else is re-read (§12.5).

**Guard (separate commit).**
- `--adopt-ledger` is added to the LOCAL_ONLY guard's list of terminal-only `holdings_board` modes, and to
  `TERMINAL_ONLY` in its test.
- #1212's rule (`test_every_holdings_board_mode_but_the_one_table_is_terminal_only`) makes this mandatory
  for any new mode. D3 says Claude never runs the adoption.
- The change only tightens (one more mode denied). It is named here and in the PR because it is a guard
  change.

**Found while building it.**
- A ledger that vanished beside a state that remembers its lines is now BLOCKED (a shorter ledger, §3).
  PR-D's test had pinned it as "not BLOCKED, not cleared". It is updated: told as the refusal, never as a
  clearing.
- An empty ledger cannot be adopted: there is no end to confirm. A vanished ledger is answered by restoring
  it, not by an adoption. This is left as it is.
- While debugging one test, the guard refused an inline Python command that imported the holdings package.
  It was not retried another way; the cause was found by reading the code (the fixture's cutover line
  carried the wall clock).

**Tests.**
- New `tests/test_mvp_runtime_holdings_c7_adopt.py`, 25 cases:
  - both steps;
  - a wrong confirmation, and a ledger changed after step one;
  - a rerun, and a stop between the line and the save (finished by the next write, or by step two again);
  - told keys dropped, and a PR-A marker cleared;
  - the history held until the cutover moves;
  - the refusal and its clearing told once each;
  - the operator conditions refused before any read;
  - nothing to adopt, an empty ledger, a broken chain;
  - the line's closed form, and readers that predate it unchanged;
  - the command's two runs with no amount, and its refusal without a terminal.
- C7-4 and C7-6 take §9's final expectations. The PR-A checkpoint tests become refusal tests.
- Fault injection (scratch): each of 22 injected faults made a test fail. The first pass missed one
  ("adoption keeps the marker"); a test was added and it is caught.

**Not changed.**
- No deploy.
- No real ledger, state or exchange read.
- No adoption run anywhere.
- No readiness key added.
- The C7 proposal's §12 is not edited here; its record follows the merge.
