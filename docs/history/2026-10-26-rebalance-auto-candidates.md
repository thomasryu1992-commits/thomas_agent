# Rebalancing candidates are picked automatically (RB2'')

- **Why:** RB2' had Thomas read a screening report and approve three or four ETFs per class. Thomas replaced
  that with an automatic pick (RB2'', 2026-10-26): no manual setup step, and no choices offered without data
  behind them.
- **Who picks:** the holdings fire in `thomas-scheduler-maint`, the one container with the Toss key, through
  `store.refresh_auto_candidates`. It uses the same screen as the report (`candidates.collect` → `rank` →
  `auto_picks`). The market data stays in the process. Only the picked instrument ids are written, to the local
  `holdings_auto_candidates.json`, which is in the hook's file regex.
- **When:** once a KST month. It also re-screens at once when an automatic pick is no longer ACTIVE on Toss: one
  `stock_info` read per fire watches the current picks. A row the answer leaves out is not taken as a delisting,
  or a quiet API would re-screen every hour.
- **The rule:**
  - the three most-traded (20-session mean of close × volume) of each class's decided-exposure ETFs;
  - bonds: three Korean 3y plus the most-traded unhedged US 7–10y, so the calculator's even fill keeps the
    decided quarter. No Korean 3y means no bond pick, never a US-only one.
  - A class the screen finds nothing tradable for keeps its last pick, and the notice says so.
- **Global equity is the decided "global index"** (total world / ACWI / all-world). The report's earlier rule
  also matched the S&P 500, a US-only index; that is gone. Ex-US, style, dividend, ESG and size variants are out
  by name, as are KOSPI 200 variants (high dividend, equal weight, sector) and gold miners.
- **Thomas's override:** `--approve-candidates` lists live in the classification file and win their class at
  once (`store.effective_candidates`). The automatic pick never screens over a class he set. `CLASS=` hands the
  class back. A manual pick that stops trading is told (count only), not replaced.
- **Why a separate file:** the classification file's `mapping_version` moves on every write and is recorded in
  the baseline log. A monthly automatic write there would move it for no change of Thomas's.
- **Notice:** "[보유 자산] 리밸런싱 후보 자동 선정(월 점검|거래 불가 후보 교체) — 자산군 N개". It names no
  instrument and is marked only after delivery.
- **The report** (`scripts.rebalance_candidates`) stays as an optional view. It now ends with what the fire
  picks.
- **Win32 skip ceiling** 689 → 690: one new bash-only guard case.
- **Checked:**
  - the pool rules (S&P 500, ex-US, high-dividend, miners out);
  - picks by trading value, and the bond 3 + 1 with the hedged one passed over;
  - monthly only;
  - re-screen on a delisting, while a missing row is not one;
  - an override wins at once and is never screened over, and a withdrawn override returns to the pick;
  - an empty screen keeps the last pick;
  - the notice holds counts only, once, after delivery;
  - the fire calls the pick.
  - Ten mutations were each caught.
- **Unverified until the first live fire:** the name rules meet Toss's real ETF names for the first time there.
  A class with no match keeps nothing (no pick yet), and `--rebalance` says "no candidates yet" for it.
