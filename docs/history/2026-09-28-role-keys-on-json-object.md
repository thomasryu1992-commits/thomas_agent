# A bound Role's keys are named in the format instruction, and an answer without them fails over

- **What was measured:** the blog lane's first manual fires on 2026-09-28 both stopped at
  `IDEATION_CONTENT_BLOCKED: VALIDATION_REVISE`. The chain went openrouter (HTTP 429), then
  google_ai_studio (HTTP 503), then groq. Groq answered the analysis keys and returned all four
  `content.general` keys (`content_draft`, `target_audience`, `channel_constraints`,
  `publishing_risks`) absent, and the required-sections check withheld the run. This happened
  twice.
- **Why groq, and only groq:**
  - Google (`responseSchema`) and OpenRouter (strict `json_schema`) enforce the bound Role's keys
    server-side.
  - Groq runs plain `json_object` on purpose, because its schema support is model-dependent and a
    rejected body is a non-retryable 4xx. Its only statement of the shape is the appended format
    instruction.
  - That instruction said "Return ONLY a single JSON object with these keys: <the 13 analysis
    keys>". The Role's keys appeared earlier in the prompt, and the last word contradicted them.
- **What changed (`providers.py`):**
  - `response_instruction(role_output_spec)` appends the bound Role's keys and their types to that
    instruction. Unbound, the text is byte-identical, so analysis runs do not change.
  - `_parse_hosted_response` treats an answer missing a bound Role's key as `MALFORMED_RESPONSE`.
    Missing means absent, null, or a blank string; an empty array is an answer.
  - That is a failure class the chain already fails over on (Thomas 2026-09-26, D1). A member that
    drops the deliverable now hands the call to the next member instead of delivering an analysis
    with no deliverable.
- **Deliberately not changed:**
  - Groq's `response_format` stays `json_object`. Strict `json_schema` there is model-dependent,
    and a rejected body does not fail over.
  - When groq is the last member and still drops the keys, the run now blocks as a provider error
    naming the missing keys, instead of a REVISE naming "missing sections".
