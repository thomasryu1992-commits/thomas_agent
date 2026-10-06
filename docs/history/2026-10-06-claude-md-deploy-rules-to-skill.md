# CLAUDE.md's Deploying section keeps the rules; the deploy skill holds the procedure

- **Why:** CLAUDE.md is loaded into every session's context on every turn, and most sessions never
  deploy. The Deploying section carried the full command block, a paragraph of reasoning per rule, and
  the one-off-script command (about 4,000 characters) that the `deploy` skill also walked through.
- **What changed:** CLAUDE.md now states each deploy rule in one line, at the same standing as
  before, and points to the skill. The skill gained the one-off-script command and a "Why each rule
  exists" section carrying the reasoning that left CLAUDE.md, so nothing was dropped. Its first
  paragraph no longer says the rules live only in CLAUDE.md.
- **What it does not do:** no rule was loosened or removed, and the skill's step order is unchanged.
  `scripts/ops/deploy_preflight.py` is unchanged.
