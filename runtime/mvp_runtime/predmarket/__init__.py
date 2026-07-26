"""Prediction-market pipeline (PM phases) — Kalshi and Polymarket.

Roadmap: ``docs/PREDICTION_MARKET_ROADMAP_V0.1.md``. The second trading domain after the
crypto pipeline, and deliberately built out of the same parts rather than beside them: the
venue adapters differ, every governed chokepoint (the Safety-Flag Gate, degrade-and-audit,
the kill switch, the P5 packet) is the one that already exists.

Phasing, and where the money enters:

- **PM1 — observe only.** Read public market data, match the same event across both venues
  (operator-confirmed, never auto-trusted), and record fee-adjusted observations. No
  account, no funds, no external effect. This package starts here.
- **PM2 — paper.** Pessimistic fills, hold to resolution, zero external effect.
- **PM3 — approval-gated live orders.** Rides the existing
  ``LIVE_EXECUTION_GOVERNANCE_V0.1.md`` packet (P5 ``FINANCIAL_APPROVED_TRADING_USE``,
  registered budget, ``p5_policy_gate``) plus a per-order R9 approval. **No second
  financial-governance track**, and nothing in this package can reach it.
"""

from __future__ import annotations
