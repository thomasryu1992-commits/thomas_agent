# 2026-09-30 — Providers: a response whose text is null is MALFORMED, not a crash

A blog manual run on candidate-1038 ended as `BRIDGE_ERROR` (pipeline-worker ref 1-0001) after
252 s. During the revision, an OpenAI-shaped member (`_OpenAICompatibleProvider`: openrouter or
groq) answered a well-formed response with `"content": null`. This is typical of a reasoning
model that spent its tokens thinking. `_parse_hosted_response` passed that `None` to
`_strip_code_fences`, and the resulting `AttributeError` was not in the body guard's except
tuple. The usage guard below it already caught `AttributeError`. So the error escaped raw
instead of becoming `MALFORMED_RESPONSE`, which `failover_kind` moves past (review D1). One
member's empty answer ended the whole run.

The body guard now catches `AttributeError` too. A null text (OpenAI shape or Google shape) is
`MALFORMED_RESPONSE`, and a chain moves on to its next member. The new tests fail on the
unfixed code with the production traceback.
