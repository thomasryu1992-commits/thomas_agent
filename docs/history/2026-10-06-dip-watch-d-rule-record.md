# The D-rule dip watch is recorded as refuted; its code is not kept

- **What:** `docs/proposals/DIP_WATCH_D_RULE_V0.1.md`, a RECORD of the 2026-09-23 conservative re-test of the
  BTC dip-buy rule mined from BitMEX fills. Until today it existed only on a local, never-PR'd branch
  (`feat/ynd-dip-watch`, shelved by its author) beside an 858-line observation lane.
- **Finding it keeps:** the rule survives conservative fills (5 bp penetration, no take-profit in the fill
  minute, 5-minute delay; OOS 2022+) only with BitMEX XBTUSD as the premium input, and BitMEX settled XBTUSD
  on 2026-09-16 and closed on 2026-09-22. Coinbase, Binance perp and Deribit substitutes all fail the same test.
- **Decision (Thomas, 2026-10-06):** keep the measurement so the idea is not re-tried blind; drop the code
  (commit `bbd82410`, listed in the host's deleted-branch record). The re-run scripts are outside the repo in
  `/root/ynd-research/`. No runtime, schedule or template change; D3 still holds.
