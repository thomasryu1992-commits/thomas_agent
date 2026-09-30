# The live entry door's four stores move to their own module (crypto refactor plan PR-12)

- **What was there:** `live_order` (2,041 lines) held the order intent, the final guard, and four
  on-disk stores the entry door uses: the daily submission counter, the bracket-failure breaker, the
  API error breaker with its recording adapter, and the live entry marks.
- **What moved:** the four stores, whole, to `crypto/live_order_stores.py` (execution layer, 1,336
  lines). That is 81 definitions with identical text: each store's constants and reason codes, its
  reader, its durable class and its inert `DryRun*` twin. `live_order` (now 769 lines) re-exports all
  81 as the same objects.
- **What did not move, and why:**
  - **the final guard and the intent.** The guard reads none of the stores: it is handed what they
    say. It is untouched, which is the condition the plan put on this step;
  - **the four selectors** (`select_live_order_counter`, `select_live_bracket_breaker`,
    `select_live_api_breaker`, `select_live_entry_marks`). They are the `select_env_gated` call
    sites, and the env-gate roster in `test_mvp_runtime_safety_gate.py` names `live_order.py` for
    them. Leaving them where they are means that roster is not edited and no file joins it. The
    stores module selects nothing: each durable class re-checks its authorization on every write,
    as before.
- **Why this cut is clean:** measured on the file's read graph, the stores read nothing else in
  `live_order` and do not read each other, and nothing in `live_order` reads them except the
  selectors that construct them. No test patches a name on `live_order` that a store reads: the
  patches sit on the callers' bindings.
- **What else changed:** three tunables name `crypto/live_order_stores.py` as their owner; the layer
  map gains the module in execution; the diagnostic code index moves 28 rows to the new file;
  `live_order` drops the imports only the stores read.
- **Evidence that nothing changed:**
  - **the order path's requests.** The record comparison (`scripts/ops/crypto_record_capture.py`, one
    worktree, the commit before and after) covers 6,691 lane tests and 10,210 files, 355 of them the
    request log of every call to the order path's pure seams. Three files differ, all in the
    scheduler's measured `duration_ms` and the event hash over it;
  - **the stores themselves.** A digest on throwaway roots drives each store through 162 steps: the
    counter to its cap and past it on both sides of a UTC day boundary, each breaker to its limit,
    past it, and through a success and an operator clear, the notice inside and after its retry
    window, the recording adapter through a venue that fails until the latch, the marks through a
    spent bar, a claim's expiry, the position and exposure caps, a release by the wrong order and a
    cooldown; and each store with no authorization and with a damaged file. Every return, every
    refusal and every file written hash the same;
  - **mutation.** 38 mutants in the moved code, one rule each (the cap admits one more, a failure is
    not counted, the latch never sets, a claim never expires, no authorization re-check, and the
    rest): all 38 are killed by a named test, so the tests still bind to the code where it now lives;
  - the patch-reach census finds no patch on `live_order` read by a moved function, before or after;
  - the full suite passes.
- **Behaviour change:** none. The change ships with the next candidate. The plan lists this step as
  R4; its precondition (the earlier steps deployed, the first fire after PR-11 observed, and the
  earlier steps independently reviewed) was met on 2026-09-30 before it was started.
