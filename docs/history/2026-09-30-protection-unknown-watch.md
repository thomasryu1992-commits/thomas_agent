# PROTECTION_UNKNOWN escalates: a per-position clock, an entry hold at 30 minutes, a HARD halt at 60

- **Decision:** Thomas 2026-09-30, `PROTECTION_UNKNOWN_ESCALATION_V0.1.md` D1–D4 as recommended.
- **Why:** a live position whose protective legs cannot be read was held and recorded, and nothing
  else: no message and no effect on other entries. The API error breaker could not bound it,
  because reconciliation's account read resets the read streak every pass, and a missing bracket id
  makes no call at all.
- **What changed:**
  - `crypto/protection_watch.py` keeps a wall-clock timer per position in
    `crypto/live_protection_watch.json` (D4: not on the live position book).
  - `live_route` advances it on an UNKNOWN read, clears it on a definite one, and prunes positions
    that left the book. Levels:
    - **U0**: the first UNKNOWN pass; hold and time exit as before.
    - **U1**: at 30 minutes, or at once for a record with no bracket id. One operator message, and
      `LIVE_PROTECTION_UNKNOWN_PERSISTING` holds every new live entry.
    - **U2**: at 60 minutes. The runtime tightens the control state to HARD through
      `control.apply_command` (actor `system:protection_watch`, raise-only, recorded on the control
      ledger), once per episode. Only the authenticated operator loosens it.
  - Never a close on UNKNOWN (D3).
  - The two thresholds are registered tunables.
- **One departure from the document:** it had U1 set `record["halt"]`, the cycle's fan-out halt.
  That skips every remaining context, and with them the settlement, protection and time exit of
  every position on another symbol, for the length of the episode. U1 holds new entries instead
  (`entries_blocking`, read before the entry decision). The intent is kept, and nothing is trapped.
- **Scope:**
  - The U1 hold covers the autonomous leg. The operator's slippage probe goes through
    `live_order.evaluate_live_order_guard`, so U1 does not stop it; U2's HARD halt does, at the
    adapter.
  - The hold recomputes the level at the pass's clock, so context order does not delay it.
  - Restoring the literal fan-out halt is one line in `_escalate_unknown_protection`.
- **Failure directions:** an unreadable watch store reads every UNKNOWN position as U1 (never U2)
  and holds entries, with only a cycle-record code (`LIVE_PROTECTION_WATCH_UNREADABLE`) and no message; a
  failed write still reads U1; a HARD halt that did not land is retried next
  pass.
- **Tests:** 17 in `test_mvp_runtime_crypto_protection_watch.py`, covering:
  - the clock, the skipped pass and IDS_MISSING;
  - the edge-triggered message and the HARD halt owed once;
  - clearing, pruning, and no file when nothing is watched;
  - the failure directions;
  - U1 not halting the fan-out;
  - U2 placing HARD, and tightening only;
  - the entry hold with positions still managed.

  The two existing time-exit tests that take the UNKNOWN path now pass their own state root.
- **Not built:** the readiness board line (display machinery, paused under review D3).
- **Effect today:** none. No live position is open and the stage is PAPER.
