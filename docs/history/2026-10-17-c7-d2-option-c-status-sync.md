# C7 D2 and PR-B, and Option C C3b and C4 decisions, recorded in their owning documents

**What changed** (documents only).

**Approval:** `승인: 문서 후속 PR 생성(C7 §12 D2·E-1·PR-B 기록, Option C §4.25 갱신, STATUS.md 재생성; 병합 제외) (Thomas 2026-10-17)`.

On 2026-10-17 several decisions and merges were recorded only in history files and PR descriptions:
- C7 D2 = option C, taken after E-1;
- C7 PR-B #1235;
- C3a-3 #1229, C3b #1234 and the C4 plan #1232;
- CR-8b and D-8.

This PR copies them into the documents that own them.

- **`HOLDINGS_LEDGER_CHECKPOINT_V0.1.md` (C7).**
  - The status moves to DECIDED, since D1–D4 are all decided. PR-C is the build that remains.
  - D2 gains its 2026-10-17 decision line.
  - §12.4's open decision (readiness PASS beside an UNVERIFIED checkpoint) is marked closed by PR-B.
  - A new §12.5 holds, verbatim:
    - the E-1 approval;
    - the D2 decision;
    - the PR-B implementation approval;
    - the PR-B merge approval.
  - §12.5 also records:
    - the E-1 result, as status words only;
    - what PR-B built;
    - the path to production. It is not taken, and each step is a separate approval.
- **`HOLDINGS_C7_D2_DECISION_PACKET_V0.1.md`.** DRAFT → DECIDED, with the original status kept as a
  historical line.
- **`THOMAS_AGENT_TARGET_ARCHITECTURE_V0.1.md`.** The C7 row, D-A5, G1b and the "what blocks now" row
  now say D2 is decided and PR-B is merged.
- **`HOST_PRIVILEGE_SEPARATION_V0.1.md` (Option C).**
  - New 2026-10-17 status entry.
  - §4.25.1 gains, verbatim:
    - the C3a-3 merge;
    - the C3b implementation and merge;
    - the C4 plan merge;
    - CR-8b and D-8;
    - the C4 workflow approvals.
  - §4.25.2 marks C3a-3 and C3b merged.
  - D-8's row in §8 records the decision.
  - A new §4.25.4 records:
    - what C3b is;
    - the CR-8b build check (python-build-standalone release `20260807` has the 3.12.13 x86_64
      `install_only` build);
    - that systemd on hosted runners is NOT_VERIFIED.
- **`HOST_PRIVILEGE_SEPARATION_C4_PLAN_V0.1.md`.**
  - DRAFT → PARTIALLY DECIDED.
  - P-2, P-3 and P-4 are marked met.
  - D-8, C4-RES and C3b are marked decided.
  - CLOCK-SOURCE and RUNNER-FETCH stay open.
- `docs/proposals/STATUS.md` is regenerated from the status lines.

**Not changed.**
- No decision was made or reworded.
- No deploy, cutover move, install, or CI change.
- Claude read no ledger, state file or `.env`. The E-1 result is quoted as Thomas shared it.
