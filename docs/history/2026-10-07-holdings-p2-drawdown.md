# P2: the Toss and Binance accounts as one KRW total, with an alert-only drawdown line at -20% from peak

- **The decision (Thomas 2026-10-07):** alert only. The drawdown limit is -20% from peak, there is no
  asset-class weight cap, and USDT is converted at Toss's display mid-rate (USDT taken as 1 USD). The
  P2 gate "P1 operation record" was waived, because P1 had run about an hour when Thomas asked for it.
- **What this delivers:** `holdings/combined.py`.
  - The `holdings_refresh` fire reads the Binance futures snapshot file
    (`crypto/account_snapshot.json`) after the Toss read and converts its margin balance with the
    rate that same read used. One rate per fire, never stored. It then adds the result to the Toss
    total and keeps a peak (`holdings_peak.json`).
  - It tells Thomas once on each edge: breached, and back under the limit. The told state moves only
    after delivery, the `breaker_watch` posture.
  - `--reset-peak` in `scripts/holdings_board.py` forgets the peak, behind the `state_guard` door.
- **Why it is shaped this way:**
  - The Binance side is a file read, never an import (`EXPANSION_READINESS_REVIEW_V0.1.md` Q4). The
    record type, the stale window and the read fields are pinned against `crypto/account_store.py`,
    and the test fixture is written by `account_store.refresh_snapshot` itself.
  - Peak and drawdown are judged only on a complete, fresh pair. Otherwise the state is `unknown` and
    the peak stays where it was, so a missing part cannot read as a fall and a partial total cannot
    raise the peak.
  - The Toss feed now reads the rate once every read, not only when something is in USD, so the two
    KRW figures on one board share a rate.
- **Corrections recorded:**
  - The P2 owner is `holdings/`, not `risk_limits.py` / `live_budget.py`, because nothing is
    blocked.
  - Alert-only P2 fits the research pause's "measurement and display" exemption. `CLAUDE.md`'s exempt
    line now names it.
- **What it cannot do:** the snapshots carry no cash flows, so a withdrawal reads as a drawdown.
  `--reset-peak` after a deposit or withdrawal is the documented answer, and the alert says so.
