# The hosted chain puts Google AI Studio first; OpenRouter moves second

- **Decision (Thomas, 2026-10-05):** scorecard Q2 (a) — `MVP_HOSTED_PROVIDER=google_ai_studio,openrouter,groq`
  (`docs/proposals/SYSTEM_SCORECARD_V0.1.md` §4.1, §5).
- **Why:** in the week the failover record covers, OpenRouter — first since 2026-07-24 — failed over
  on 151 of 160 content and research runs (429/503 after its retry, and 20 malformed blog drafts), so
  nearly every call spent OpenRouter's retry and budget share before Google answered. Its first
  place existed for M2 tiering, and `MVP_OPENROUTER_TIERS` is unset on the host.
- **What changed on the host:** one `.env` line, backed up first; `pipeline-worker` and `operator`
  (the only services that read the variable) recreated on the same image, nothing else restarted.
  A real analysis request then came back from Google with no failover in 5.9 s.
- **What changed in the repo:** the locked-decision line in `CLAUDE.md` and `docs/DEPLOYMENT.md`
  name the new order. The DEPLOYMENT sentence that still said failover fires only on 503/429 now
  names the D1 rule.
- **What it does not do:** no code change. The chain rules are unchanged: an unknown or duplicate
  member still fails the whole chain closed, and every failover is still recorded. The blog's
  OpenRouter model override now applies only on failover; it answered 3 of 104 runs before.
