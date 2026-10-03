# The data-gap review names `suggestions` in the format instruction

- **Symptom:** the weekly `crypto_data_review` failed on 2026-10-03 with
  `DATA_REVIEW_PERSISTENTLY_DEGRADED`. Three of the four fires from 2026-09-12 to 2026-10-03
  degraded with "no parseable suggestions". Only 2026-09-19 returned suggestions (5 accepted).
  The model was Groq `openai/gpt-oss-120b` and the prompt was v3 every time. The answers were
  well-formed and finished with `stop`.
- **Cause:** the 2026-09-28 blog failure, in another lane. The review's prompt asked for
  `suggestions` inside the analysis envelope. The providers then appended their format
  instruction, which came last and listed the analysis keys alone. Groq runs plain
  `json_object`, so that instruction is the only shape it is held to, and answers that followed
  it dropped `suggestions`. The shared parser checks only the three required analysis keys, so
  this reached the review as an analysis with zero suggestions. The 2026-08-10 v3 fix measured
  how often the answer was MALFORMED (0/11). It never measured whether `suggestions` arrived.
- **What changed:**
  - **The review binds its key.** `data_review` binds `SUGGESTIONS_OUTPUT_SPEC` through
    `bind_role_output_keys`, the mechanism the blog lane got in #1017. The format instruction now
    names `suggestions` and its fields. The schema-enforcing vendors (Google, OpenRouter strict)
    emit it. An answer without it is `MALFORMED_RESPONSE`, which a failover chain moves past.
    The prompt version is `v4`; the prompt text is v3's.
  - **A new Role key kind.** `providers` gains `objects(<field>, ...)`, a list of records whose
    fields are strings. The format instruction names the fields. OpenAI strict closes each
    record with `additionalProperties: false`; Google's dialect leaves the keyword out, because
    it rejects it. Unbound runs and the older kinds produce byte-identical output.
  - **The record says what came back.** A degraded record whose answer parsed now keeps the
    answer's key names in `answer_keys`, never its content. The 2026-10-03 diagnosis had to infer
    this from the code path, because no raw answer is stored.
- **Not changed:** the stall rule, the review's schedule, and the analysis runs' instruction and
  schemas.
