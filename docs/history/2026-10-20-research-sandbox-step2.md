# Research sandbox, step 2: the tools that let Claude compute without secrets or the live state

- **What** (`RESEARCH_SANDBOX_V0.1.md` §4.2–§4.3):
  - `scripts/research/lake.py`: `LakeCollector` serves candles, funding and mark / index / premium closes from a
    local cache. It parses them with the runtime's own `BinanceFuturesCollector._parse` and
    `_parse_derivative_page`.
    - `LakeLiquidationFeed` raises until the runtime stores the Coinalyze series (step 3), so OI features are
      None rather than stand-ins.
    - `install()` swaps the runtime's two selectors and makes the synthetic mock collector refuse.
  - `scripts/research/fetch_public.py` fills that cache from the public endpoints, inside an IP budget:
    - at most 600 weight per minute counted locally;
    - stop when the venue reports 1,200 used on the IP;
    - stop at the first 429 or 418 without retrying;
    - never inside 08:00–09:00 UTC;
    - forward-only after the first fill, closed bars only, 15m refused.
  - `scripts/research/sandbox_site/sitecustomize.py` refuses every non-AF_UNIX `connect`, and installs the lake
    when `RESEARCH_CACHE` is set.
  - `scripts/research/sandbox_run.sh` runs one command with these guards:
    - memory 700M with no swap, CPU 50%, nice 10, ionice idle (systemd `--user` scope);
    - an empty environment, so no key passes through;
    - the code tree's state directory linked to a per-run copy of the lake snapshot;
    - refusal of a real state directory, any 15m argument, and an `--official` run on a dirty or unmerged tree;
    - a lock per code tree;
    - a run record under `~/research/runs/`.
  - `scripts/research/make_venv.sh` builds a 3.12 venv from the interpreter the deploy runner already vetted (CR-8b).
- **Checked:**
  - 18 tests: parity with the runtime parser, funding and derivative shapes, loud empty cache, install and mock
    refusal, backward and forward fills, the minute budget, 429, 418, header stop, factory window, 15m, the
    socket guard, and the runner (snapshot visible, recorded, link removed, no secret passed, memory cap kills,
    refusals). The three runner tests skip where there is no user systemd, as in CI.
  - Mutation checks: network open, header stop off, environment not cleared, 15m allowed, mock allowed. Each
    fails a test.
  - Live smoke on this host (2026-10-20 17:51Z):
    - the venv came up as 3.12.13;
    - a real fetch of BTCUSDT 4h and 1d plus funding and the three price series took 44 requests in about 5 s;
    - inside the runner, a 6,000-bar 4h frame was built from the cache, with funding, mark, index and premium
      `ok`, OI and liquidations `absent`, and the network blocked;
    - the cache's last two closed 4h bars matched a fresh public read exactly, and the forming bar was not
      stored.
- **Scripts roster:** both research scripts are `READ`. They write no state; `fetch_public` writes only the
  sandbox cache under `~/research`.
