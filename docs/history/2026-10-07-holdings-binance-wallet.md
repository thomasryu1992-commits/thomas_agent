# Holdings board adds Binance spot and Simple Earn to the P2 total, on the account key shared with the risk lane (option A)

- **What this PR adds:**
  - `runtime/mvp_runtime/holdings/binance_wallet.py`, gated by `MVP_BINANCE_WALLET=binance_wallet`. One host
    (`api.binance.com`) and four GET paths: the spot account, Simple Earn flexible and locked positions (signed), and the
    public price list.
  - The `holdings_refresh` fire reads it after Toss. `holdings/combined.py` (P2, #1170) values it at the same Toss
    mid-rate as the futures margin and adds it to the combined total.
  - Four new combined keys, all in KRW or a status: `crypto_spot_krw`, `crypto_earn_krw`, `crypto_classes_krw`,
    `crypto_wallet_status`.
  - Compose forwards the gate and the account key pair to scheduler-maint.
  - Appendix C (the regulatory record; its judgement waits on Thomas) of `MULTI_ASSET_EXPANSION_V0.1.md`, and a dated
    correction under `EXPANSION_READINESS_REVIEW_V0.1.md` Q4 and in `combined.py`'s docstring.
- **The key decision and its cost (Thomas 2026-10-07):**
  - Option A, the existing `BINANCE_ACCOUNT_API_KEY` pair, was chosen over the recommended new read-only key.
  - `docs/BUILD_HISTORY.md` (2026-07-28) records that pair as the source of the order credentials, so it may carry
    futures-trading permission at the venue.
  - The deployment test pins that the maintenance lane shares exactly those two names. It still gets no live switch,
    no order key, no confirmation phrase and no account-feed selector.
  - The egress roster, the signer list and the env-gate roster each name the new module.
- **Why it rides the holdings fire:** a kind's lane is its classification. Running a wallet read on the risk lane,
  where the key was, would have meant calling a maintenance read a risk kind.
- **Rebuilt on P2 rather than beside it:**
  - The first version of this branch read the futures margin itself and valued Binance at Upbit's public KRW-USDT, to
    keep Toss's rate from being recoverable. P2 (#1170) merged first with Thomas's decision: Toss mid-rate, USDT as one
    dollar, one rate per fire.
  - This PR therefore follows P2 and drops both the Upbit read and its own futures read. The point about the rate is
    recorded as an open question in appendix C.
- **Completeness:**
  - With the wallet gate open, the wallet is a required part of the total. A failed read, unread Earn or a missing rate
    makes it incomplete, and the peak and the drawdown verdict stay where they were.
  - With the gate closed, the P2 total is unchanged.
  - A wallet failure never costs the Toss snapshot.
- **Unverified until the first live read:** `/sapi` has no testnet. Three things rest on the docs alone: the Earn
  fields (`totalAmount`, `amount`), the spot `LD<asset>` duplicate of flexible Earn (skipped only when a flexible row
  confirms it, because `LDO` is a real coin), and `omitZeroBalances`. The public price list was parsed live without
  keys (3,115 symbols).
- **Not turned on:** Thomas confirms appendix C, sets `MVP_BINANCE_WALLET` in `.env` and deploys.
