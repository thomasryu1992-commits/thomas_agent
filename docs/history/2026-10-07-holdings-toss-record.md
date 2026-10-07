# The holdings board's first account moves from KIS to Toss Securities; appendix B drafts the record

- **The decision (Thomas 2026-10-07):** "토스만 쓰려고 해." The board's first account is Toss
  Securities, and no KIS key is issued.
- **What this PR adds:** a decision section and appendix B (Toss Securities regulatory record, draft)
  in `docs/proposals/MULTI_ASSET_EXPANSION_V0.1.md`, and `STATUS.md` regenerated. The status moves to
  PARTIALLY DECIDED, because appendix B has two places for Thomas (the judgement and its strength).
  No code changes.
- **What the record rests on:** Toss's official docs, read 2026-10-07: the overview, the FAQ, and
  `openapi.json`.
  - The data policy limits use to the investor's own trading purpose and forbids distribution to
    third parties, even when non-commercial. Appendix A's external-send boundary carries over
    unchanged.
  - The OAuth scopes are empty, so the same client opens orders and read-only stays enforced in code.
  - The allowed-IP list is mandatory.
  - Only one token is valid per client, and reissuing one revokes the previous one. So
    scheduler-maint is the one issuer, and the terminal defaults to the stored snapshot.
- **What follows, derived here and correctable by Thomas:** the Toss feed lands in `holdings/` behind
  the existing renders, store, `/holdings` verb and scheduler-maint wiring. The KIS feed and its six
  compose variables are removed whole in that same PR. Nothing KIS was ever enabled: no `.env`
  entry and no schedule.
- **Appendix B completed in the same PR (Thomas 2026-10-07):** the judgement is "querying one's own
  account through the Toss Securities Open API is something this project may operate". Its strength
  is provisional, and the server's IP is on Toss's allowed list. The status is DECIDED, and the Toss
  feed may start.
