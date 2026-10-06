# A run that reached no model proposes no working memory

- **Why:** `fix/a-mock-is-not-knowledge` (2026-08-24) found 190 of this host's 316 working-memory candidate
  rows to be `MockProvider`'s five canned findings, 38 copies each, written while `MVP_HOSTED_PROVIDER` was
  unset. #777 took half of that branch — invocation metadata says `model_invocation` — and left the other
  half: the mock still proposed candidates. Thomas chose to finish it (2026-10-06, local-branch review).
- **What changed:** `worker.run_analysis_worker` computes `invoked_model` once (the same fact
  `invocation_metadata["model_invocation"]` and `cli_common.gate_banners` use) and proposes memory
  candidates only when it is true. A mock's findings are a constant, not knowledge; the record still says
  why the list is empty.
- **Tests:** `test_a_run_that_reached_no_model_proposes_no_memory` (same fixture: a model-declaring mock
  proposes, the plain mock does not). Tests of the memory path itself — proposal, accumulation, feedback,
  the MEMORY_CANDIDATE_CREATED audit event, the CLI's injected stores — now run on `tests._helpers.ModelMock`,
  the mock declared as a model; the audit chain test also checks a plain mock run carries no such event.
- **What it does not do:** no change when a real provider answers (production); existing mock-written rows
  in the store are not cleaned here.
