# The PROTECTION_UNKNOWN escalation proposal: a per-position clock, a fan-out halt, then a HARD halt

- **What was found:** a live position whose protective legs cannot be read is held and recorded,
  and nothing else. It sets no pass `halt`, it is not an incident, and it sends no message. The API
  error breaker does not bound it. `fetch_order` counts as a read-class call, but reconciliation's
  account read resets that streak first on every pass. `LIVE_BRACKET_IDS_MISSING` makes no call at
  all, and a refusal outside `API_ERROR_VENUE_CODES` does not count.
- **What was delivered:** `docs/proposals/PROTECTION_UNKNOWN_ESCALATION_V0.1.md` (DRAFT), with
  `STATUS.md` regenerated from it. It proposes three levels:
  - U0: a durable per-position clock;
  - U1 at 30 min (immediately for missing bracket ids): one message, and the pass halts its
    fan-out;
  - U2 at 60 min: the runtime tightens the control state to HARD, which only the operator loosens.
  It works out the system review's residue 2.
- **Deliberately not done:** no code, and no close or re-placed bracket on UNKNOWN. The four
  decisions (D1–D4) are Thomas's. D2, the runtime's authority to tighten to HARD, is new authority
  on the money path.
