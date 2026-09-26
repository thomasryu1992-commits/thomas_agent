# A failover chain moves past a member that failed for a reason of its own, and says so

- **The defect:** a chain switched members on 429/503 alone. The two outages this repository recorded
  were a decommissioned Groq slug (HTTP 404, every path riding it down, 2026-08-29) and an empty model
  slug (nine silent days) — one member's configuration, which the next vendor does not share. A hung
  first member was also handed the whole 120 s budget, leaving nothing for the rest.
- **What changed (Thomas 2026-09-26, system review D1):** `providers.failover_kind` classifies a
  member's failure as configuration (no key, 401/403/404), unavailable (429/503), server (5xx),
  transport (timeout, dropped connection) or malformed (unparseable answer, persistent
  `json_validate_failed`); any of those moves the chain on. A request-shaped 4xx and a gate refusal
  still propagate. The HTTP helper now carries the status on the error (`data`) so the decision reads a
  number, not a message. Members but the last get `min(45 s, budget ÷ members)`; the last gets what is
  left. A chain that fails for mixed reasons says `PROVIDER_CHAIN_EXHAUSTED` with each member's reason
  instead of claiming everything was unavailable.
- **The condition it was adopted on:** every failover is kept (`failovers` on the invocation record —
  specialist, validator and triage), named in the reply above the footer (and in `/result`, which
  re-renders from the ledger), and counted in the weekly lane digest, where a `configuration` failover
  is flagged as a suspected key or slug fault. A run whose first member answers reads byte-identically
  to before.
- **Not done:** no real-time alert beyond the reply line and the digest.
