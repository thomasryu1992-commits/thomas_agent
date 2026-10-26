# 2026-10-26 — Blog department P2: the system re-checks each reported draft with the vault's own rules

**What.** BLOG_DEPARTMENT v0.2 P2, under Thomas 2026-10-26 "승인: BLOG-DEPT v0.2 §7 권고대로" (R3: P1–P5 after the
P0 deploy, which landed the same day as `candidate-1326`).
- **`runtime/mvp_runtime/blog/`** is the department's first lane package. `preflight.py`, `variety.py` and
  `taxonomy.py` are **generated** from the vault's `tools/blog-preflight.py`, `blog_variety.py` and `blog_taxonomy.py`
  by `scripts/blog_vendor.py`. `verify.py` runs them on a reported draft and compares the verdict with the one the
  drafting cron reported.
- **Scheduler kind `blog_verify`** (maintenance, daily) records one `blog_verification` per run report from the last
  three KST days. Each draft in the record is a short id and a state (`compared` / `changed` / `no_hash` /
  `no_codes` / `missing` / `outside` / `error:*`), plus whether the codes agree and which ones differ.
- **The weekly report** adds a "[검수 대조]" line.
- **scheduler-maint mounts** `content/_assets` (capture metadata, Y3) and `analytics/keywords` (clusters.tsv /
  entities.tsv, M0–M7 and V16), read-only.
- **Host side (server scripts git `9c37a6f`):** the run report now carries per-checker rule-code counts and the
  sha256 of the bytes it checked.

**Why it is shaped this way.**
- **Generated, not hand-copied, and not run from the vault.** Running the vault's files in the container would
  have been equality by construction. But it would execute code the `claude` account can rewrite, without review,
  inside scheduler-maint, which holds the broker key. A hand copy drifts the day the vault tool changes. So
  `blog_vendor.py` cuts the code mechanically, and `--check` says when the vault has moved on. Regenerating is a PR.
- **What the generator removes, and only that:**
  - every URL opener: `_resolves` becomes the vault's own offline answer, and `fetch` and `check_live` go;
  - the taxonomy's writers and CLI;
  - the `sys.path` imports;
  - import-time path lookups, replaced by `configure(vault_root)`.

  A test parses the generated files and pins no network module, no `urlopen`, and no write-mode `open`.
  The generated files keep the vault's style (no type hints, `print`-based verdicts read through
  `redirect_stdout`). Normalising them would be a rewrite, which is what v0.2 §4 rules out until P8.
- **Compare only like with like.**
  - A draft is compared only if its bytes still hash to what the cron checked. The 10-26 slot fix and hand edits
    otherwise make every "difference" a timing artefact.
  - `X2` (link liveness) is dropped from both sides: it needs the network, which stays outsourced (v0.2 §7 N1).
- **A path from a report is data from outside.** Anything that does not resolve under `<vault>/content` is
  `outside` and never opened.

**Verified.**
- **Equality on the real vault:** all 317 posts, generated port vs the vault's own checkers run offline
  (`BLOG_PREFLIGHT_OFFLINE=1`). The rule codes agreed on 317/317 (243 s).
- **Sensitivity of that check:** with one rule mutated in the port (the Naver paragraph limit, 150 → 100),
  13 of 15 sampled posts differed. The port was restored and `blog_vendor.py --check` reported OK.
- **Tests:** 17 new cases. Mount and preflight floor tests updated. The scheduler suites pass.

**Host steps after deploy (Thomas):** register the daily fire, for example at about 10:00 KST, after the 08:02
drafting run:
`docker exec thomas-scheduler python -m runtime.mvp_runtime.scheduler_cli add --kind blog_verify --interval-seconds 86400 --reason "BLOG_DEPARTMENT v0.2 P2"`.
The first reports with codes and hashes are the ones written from 2026-10-27 on. Earlier reports read as `no_hash`.

**P2's switch condition (v0.2 §4, row rewritten here):** two weeks in which every draft the system could compare
agrees on all rule codes except X2. A difference is recorded with its codes, and its cause goes in the next history
file.
