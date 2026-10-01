# The venue contract holds an adapter that cannot place or cancel an order (crypto refactor plan PR-15)

- **The finding (S-2):** the venue contract sentinel runs inside the scheduler's trading fire and asks
  the venue with the order key: `/order/test` (which creates nothing), the position mode, and the
  orders it holds. To do that it built the whole mainnet order adapter, `submit` and `cancel_order`
  included, so its capability was wider than its use. Thomas chose (D-5, 2026-09-30) to cover it
  first with the egress roster (PR-02) and to narrow the adapter in the execution-layer step.
- **What changed:**
  - `live_execution.BinanceFuturesVenueReader` is the order adapter's read-and-validate half: the
    host allowlist, the authorization re-check, the signing, `validate_order`, `position_mode`,
    `fetch_order`, `open_orders` and `algo_open_orders`, moved verbatim;
  - `BinanceFuturesOrderAdapter` subclasses it and adds only `submit` and `cancel_order`, so every
    caller of the order adapter gets the same methods as before;
  - `select_venue_reader` builds the reader on the order adapter's own gate (same switch, flags,
    provider and key; the dry-run adapter without the opt-in), and `VenueReader` is its protocol;
  - `venue_contract.refresh_verification` selects the reader instead of the order adapter. A
    `submit` added there now fails on a method its object does not have, as well as on the roster.
- **What did not change:** what the sentinel asks, in what order, and what it records. The order
  adapter's behaviour, its gate and every other caller. The egress roster changed by name only:
  `validate_order`'s signed POST is keyed on the reader's class, `select_venue_reader` is a door, and
  the refresh's entry names it. The env-gate roster is unedited (`live_execution.py` was already on it).
- **Left as they are:** the operator scripts `list_resting_orders.py` (reads resting orders) and
  `diagnose_bracket_leg.py` (validates one request) still take the order adapter. They are doors
  outside the trading fire, pinned by the script roster. Moving them to the reader is a follow-up,
  and it adds `live_execution.select_venue_reader` to the script roster's non-sending callees.
- **Tests:** `test_mvp_runtime_crypto_venue_reader.py` pins the reader's surface (the five, no
  `submit`, no `cancel_order`), the order adapter as the reader plus those two and nothing else,
  the selector in both states of the opt-in, and that the refresh with no adapter given selects the
  reader and never calls `select_order_adapter`. The venue contract's three tests that drive the real
  class's methods now build the reader, which is what the refresh holds.
- **Evidence:**
  - the methods moved verbatim: every base line of `live_execution.py` is in the new file except the
    old class line and its docstring;
  - the record comparison over 6,728 lane tests, request logs included: 4 files differ, all of them
    the scheduler's measured durations;
  - the patch-reach census on `live_execution` finds no test that patches one of its globals that
    its own functions read, on either commit. The patches that matter here are on the class and the
    selector, and the tests above pin those;
  - 7 mutants (the reader gains a cancel or says no egress, the selector builds the order adapter or
    an inert default that claims egress or loses the gate's authorization, the refresh selects the
    order adapter again, the order adapter overrides a read) are each killed by a named test;
  - an independent review found no behaviour difference, and the full suite passes.
- **Not done:** the plan's evidence for PR-13~15 includes one signed testnet cycle. It has not run for
  PR-13, PR-14 or this PR: the stage is PAPER and the testnet opt-in is off, so the guard refuses it
  (2026-09-30, as designed). Thomas chose to go ahead without it.
