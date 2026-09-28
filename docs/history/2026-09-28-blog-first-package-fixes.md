# The first blog package's two small defects: a lost revision reason, and its own choice shown as a candidate

- **What was measured:** the lane's first package (`bcp_ae0b4edc2b8c4477cba3`, 2026-09-28,
  `사업자등록증 발급`) came out `needs_edit` with a 730-character body. Two small defects showed
  in the record.
  - **The revision reason was lost.** Its one revision blocked, and all the record kept was
    `REVISION_BLOCKED:IDEATION_REVISION_BLOCKED`, the lane's own outer code. The pipeline's
    reason (`PROVIDER_ERROR`, and which chain member failed how) sat in the run result and was
    not recorded anywhere after it.
  - **The choice read as a candidate.** `POST.md` listed the chosen keyword as a plain "후보",
    because the queue spells it `사업자등록증 발급` and Search Ad spells it `사업자등록증발급`.
- **What changed:**
  - `_run` raises with the inner reason code and the block message (≤ 300 characters), in the
    text and in `data`.
  - A blocked revision records `REVISION_BLOCKED:<inner code>` and the message as
    `quality.revision_detail`, an optional v0.2 field. `POST.md` shows it.
  - `POST.md` marks the chosen keyword by `normalize_keyword` equality.
- **Not changed:** the short draft itself. Length is a model-quality question (groq's
  `gpt-oss-120b` spent most of 4,124 output tokens reasoning, while openrouter 429 and google 503
  persisted), and it is handled separately.
