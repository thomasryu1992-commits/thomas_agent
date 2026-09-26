# `deferred/` is frozen in place, and the Deferred gate stays — its retirement was a misread precedent

- **The decision as recommended:** system review D7 proposed moving `deferred/` to `historical/` "by the
  G2 precedent" and retiring the Deferred gate scope. Thomas adopted it on 2026-09-26, at lowest priority.
- **What mapping the change found:** the gate is the only runner of checks the active tree depends on —
  `validate_i0_5_1_runtime_promotion_readiness.py` pins the runtime validation workflow's least-privilege
  tokens, the `.gitattributes` LF rules and the CI evidence collector's credential ban; required-field parity
  for the 24 contracts marked `required_field_parity: true` (the active parity check delegates them); and
  about 250 fail-closed cases across five detailed validators. And G2 was not a move to `historical/`: it
  deleted an implementation under a deferral and kept the deferred design and this gate. The gate costs one
  non-required job of about 12 seconds.
- **What Thomas chose instead (same day):** freeze, don't retire. `deferred/README.md` says it is frozen and
  why it stays gated; `tests/test_deferred_architecture.py` pins each family's contract count, on top of the
  validator's existing pin of the five families, so growth needs a change that names the decision reopening it.
- **The wrong sentences D7 named, fixed:** six `docker-compose.yml` comments (and one test docstring) still
  said a capability needs a mounted grant — retired 2026-08-10; the control-channel boundary's "Kill Switch
  state is not mutated" now says it describes the deferred family, not `control.py`; the contract-parity
  validator no longer claims to check Execution Request/Result, which the Deferred gate does.
- **Not done:** the four `05_REGISTRIES/I0_5_{2,3,4,5}_*_REVIEW_ONLY.yaml` still name files §G2 deleted. They
  are deferred records the gate checks for existence only; G2 left them as they are, and so does this.
