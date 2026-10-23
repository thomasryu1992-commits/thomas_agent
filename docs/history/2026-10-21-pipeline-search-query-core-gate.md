# Two search-query pipeline tests gated on a local Core, like their neighbours

- **Why:** `test_search_query_replaces_the_request_as_the_web_query` and
  `test_without_search_query_the_request_is_the_query_as_before` (added 2026-09-30, `66b52d8e`) run the full
  pipeline. The pipeline BLOCKs with `CORE_NOT_ACTIVATED` when no Core pointer exists. CI activates an
  ephemeral Core, so both passed there. Every other pipeline test in the file carries `@requires_local_core`;
  these two did not. On a checkout without Core activation (the `claude` account's since C6b), they failed
  rather than skipped, and every full local run had to deselect them.
- **What:** both tests now carry `@requires_local_core`. CI has a Core, so its skip counts do not move.
