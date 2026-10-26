# 2026-10-26 — Blog department v0.2: integrate what can run inside, outsource the rest

**What changed.** `docs/proposals/BLOG_DEPARTMENT_V0.1.md` is revised to v0.2 (same file). v0.1 §3 kept every
production step outside the runtime and gave the organisation observation and feedback only. Thomas 2026-10-26
set the rule instead: steps that can run inside the system move inside, and the rest stay outside as outsourced
work that the system instructs and hears back from.

**Why it is shaped this way.**
- **The split test is what blocks a container.** Three things do: the subscription-logged-in Claude CLI with its
  skills (drafting), a logged-in browser (publishing, Naver/GSC stats), and Thomas's own review of saved Naver posts.
  An HTTP read does not block integration; it needs a named env-gated capability (§7 N1).
- **Moving is the refactor, and it moves the running code.** The retired runtime lane failed as a parallel
  re-implementation. v0.2 ports the vault code into one lane package and switches a step only after a shadow run
  shows equal output on the same input (the `kw_pool` shadow is the precedent).
- **Two interfaces, no new runtime write into the vault.** Instructions go out through one read verb on the
  existing `claude-read-bridge` (pulled by the drafting cron). Reports come back as a per-run JSON in the vault,
  read through the read-only mount #1321 already added. After switchover, no instruction means no drafting.
- **R2 is reversed.** v0.1 recommended the vault `tools/` as the code's home. With integration as the goal, the
  integrated steps' code belongs in this repository under PR, CI and gates. Mac-only tools stay in the vault.

**Verified against the sources.** Every external host in §7 N1 was taken from the code (`kw_pipeline.py`,
`blog_taxonomy.py`, `blog-preflight.py`, `blog-weekly.py`). Every file in appendix A is assigned in §3-2.

**Decided the same day.** Thomas approved §7 as recommended ("승인: BLOG-DEPT v0.2 §7 권고대로"): N1, S1, L2, L3, R1–R3. **Still open:** the P0 deploy. #1320 merged into
main mid-deploy on 2026-10-26, so the second preflight stopped. Nothing was promoted.
