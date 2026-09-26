# A business analysis that did not pass is delivered under an "unverified" banner, not withheld

- **The defect:** only a PASS delivered. A REVISE for a quality reason — a missing section, thin
  grounding, an unstated risk — withheld the whole analysis and told Thomas to rewrite his request,
  though the defect was the model's; a reviewer whose provider failed blocked an analysis that had
  passed every automatic check. Neither is a safety reason, and an analysis moves no money (system
  review B6).
- **What changed (Thomas 2026-09-26, review D2, with its four details as recommended):** in the
  business-analysis lane (`general.specialist`), a REVISE — automatic, the reviewer's, or a revision
  that still REVISEs — and a reviewer outage (`PROVIDER_ERROR`, `RESPONSE_TRUNCATED`) on a task whose
  risk level did not mandate the review are delivered. The reply opens with a banner naming why it is
  unverified, and its footer no longer claims a validation it failed. The run concludes COMPLETED
  with `DELIVERED_UNVERIFIED` on the audit chain (and an `INDEPENDENT_VALIDATION_FAILED` event for an
  outage); a `delivery` ledger row lets `/result` re-render the same banner; the CLI exits 0; the lane
  digest counts these apart as 미검증 전달.
- **What still withholds:** every BLOCK (lineage, permission, secret, and the reviewer's own BLOCK or
  unparseable verdict); a citation of a source the run never issued (`validation.grounding_census`);
  every other lane; a reviewer outage where ORANGE/RED risk mandated the review; any other reviewer
  failure (budget, independence, binding).
- **Still PASS-only:** the controlled write (`--write-output`), working-memory accumulation and
  programization counting — an unverified analysis is read, never written down or learned from.
- **No schema or policy change:** the audit event's reason codes carry the new marker; the `delivery`
  row joins the ledger's record-kind roster the way `keyword_research` did.
