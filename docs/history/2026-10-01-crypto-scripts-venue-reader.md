# The two order-key operator scripts hold the venue reader (crypto refactor plan PR-15 follow-up)

- **What changed:** `scripts/list_resting_orders.py` (reads what is resting at the venue) and
  `scripts/diagnose_bracket_leg.py` (asks `/order/test` about one bracket leg) select
  `live_execution.select_venue_reader` instead of the order adapter. PR-15 gave the venue contract
  that reader, which has no method that sends or cancels. These two scripts only ever read and
  validate, so their capability now matches their use as well. Without `MVP_LIVE_TRADING=real` both
  still get the inert dry-run adapter and say so. The refusal line now names the venue reader.
- **The script roster is stricter:** selecting the order adapter is no longer a non-sending callee.
  A script whose only exchange call is `select_order_adapter` is now classed `EXCHANGE_WRITE`,
  because the object it holds can send whatever it calls today. `ORDER_KEY_READ` means the venue
  reader, validation, or the venue contract's checks. The synthetic derivation test gains that case.
- **Rosters:** the egress roster's two script entries name `select_venue_reader`. No entry was added
  or removed, and the counts are unchanged.
- **Tests:** the scripts' tests patch the reader's selector, and the bracket diagnosis's three
  validator tests build the reader. Reverting either script to the order adapter fails the egress
  roster and that script's own tests.
- **Evidence:** the runtime is untouched (scripts and tests only), so there is no record comparison.
  The full suite passes. Neither script runs in the trading fire, and Claude does not run them.
