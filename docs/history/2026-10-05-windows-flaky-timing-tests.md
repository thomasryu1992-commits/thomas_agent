# Three Windows-only flaky tests fixed at their cause, not retried

- **Why:** every Windows-only red of `MVP Runtime Tests` since 09-21 — 7 of 639 runs, two of them on
  `main` (#1099, #1116) — was one of three tests (`docs/proposals/SYSTEM_SCORECARD_V0.1.md` §4.3).
  None was a product defect, and under `strict` each cost a PR a re-run.
- **`test_a_throttle_retry_that_cannot_fit_in_the_call_is_not_made`:** the test server answered 429
  without reading the request body. `http.client` sends headers and body in two `send()` calls, so
  `{}` could still be in the kernel buffer at close; Windows answers that with a reset that discards
  the unread response, and the 429 surfaced as `PROVIDER_TRANSPORT` (or the retry never reached the
  server). The handler now reads `Content-Length` bytes first. `providers.py` is unchanged.
- **`test_a_hundred_submissions_…`:** A27's p95 < 2 s is a claim about the Linux service host
  (0.24 s measured locally). Windows CI's file I/O put it at 2.06–3.43 s with nothing wrong. The
  bound is 2 s on POSIX and 8 s on Windows; the exactly-once asserts are unchanged everywhere.
- **`test_a_fire_past_its_deadline_is_ended_with_evidence`** (and its evidence-write sibling): the
  5 s wait for a 0.2 s watchdog is a hang detector and returns the moment the watchdog acts; a
  loaded Windows runner once exceeded it. It is 30 s now.
- **Not done:** no runtime change. The reset explanation is the documented Windows TCP behaviour and
  fits both failure messages; it cannot be reproduced on the Linux host.
