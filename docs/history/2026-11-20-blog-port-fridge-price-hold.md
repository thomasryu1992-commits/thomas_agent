# 2026-11-20 — The fridge price keyword waits on the fridge decision; the kw_pipeline port follows the vault

**What changed in the vault (production).**
- **The leak.** The fridge family has waited on a Thomas decision since 11-11: `AFF_HOLD["영업용냉장고"]` in vault
  `tools/kw_pipeline.py`. The hold is keyed by the Mac issuing tool's `kind()`. But `kind('영업용 냉장고 가격')` is
  '업소용냉장고가격', not '업소용냉장고', so that keyword fell outside the hold.
  - It stood first in the affiliate stock's 대기 section.
  - The Mac's `affiliate_link.py --auto` searches that section in order, so it would have searched a fridge keyword
    before the decision.
  - The Mac session found this on 11-20 (handoff-1120-photobox).
- **The fix.** The keyword gets its own `AFF_HOLD` entry with the same reason (vault `tools/kw_pipeline.py`, committed
  in the vault's 2bdf4792).
  - The stock was regenerated the same day: 대기 is now 샤오미 포토프린터 1s, 매장 cctv and 포토박스.
  - The `kind()` handling of "가격" belongs to the Mac tool and was not changed.

**This PR.**
- **The port.** `scripts/blog_vendor.py` regenerates `runtime/mvp_runtime/blog/kw_pipeline.py` from the vault.
  - The diff is the `AFF_HOLD` entry and the generated-from marks; `--check` is OK.
  - Until this merges, the P4a replay marks new bundles `generator_stale`.
- **The test.**
  `test_the_fridge_price_keyword_waits_on_the_fridge_decision_in_the_port` runs the generated `aff_families`. It asserts
  that both fridge keywords come out 보류 and that 포토박스 stays 대기.
  - It fails against the previous port.
