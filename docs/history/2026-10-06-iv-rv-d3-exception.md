# The IV–RV measurement is exempt from the crypto research pause; building it is a separate approval

- **What was decided.** Thomas exempted the IV–RV measurement of `MULTI_ASSET_EXPANSION_V0.1.md` §4.3
  from the D3 research pause on 2026-10-06. That is a read-only observation of implied minus realized
  volatility, and it was the one open question on that proposal that needed his answer.
- **Where it is written.**
  - `CLAUDE.md`'s exempt list carries the rule.
  - The proposal's new decision section carries the scope and what is not approved.
  - `SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md` D3-1 carries a pointer.
- **The boundary.** It is the same as D3-1 (2026-10-01): observe and display only. It is not a hypothesis,
  template or trial. It opens no option position, changes no judgement, and no door reads it.
- **What it does not approve.** Implied volatility is option market data the system does not collect
  today. Building the measurement means choosing a source. If that read is new egress, the source must
  be named at the env-only gate (`safety_gate.select_env_gated` and the call-site enumeration test) as
  its own governance change. Whether the source needs a regulatory record is asked then too.
- **Nothing was built.** No code, schema, constant or schedule changed.
