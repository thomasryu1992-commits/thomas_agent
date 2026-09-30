# 2026-09-30 — Blog fact checks: precise enough to be read

Three manual runs on candidate-1058 listed 6, 11 and 13 pre-publication checks. Most were not
claims at all:

- advice that merely said "가격" ("가격만 보고 고르지 마라"), "제한" ("개수를 제한해야") or
  "버전" ("구버전 쿠폰은 폐기하라");
- a table header row ("제작 플랫폼 | 기본 가격 및 수량 | …");
- a sentence cut at a decimal point: "31.25달러부터" became "…31." and "25달러부터 시작하는 등…";
- the model's "…판매한다." listed again as the detector's "…판매합니다.".

Changes in `blog_draft`:

- The sentence splitter treats a period between two digits as a decimal point.
- A bare price, limit or version word needs a number in the same sentence. Money amounts,
  "N회까지"-style limits and model or version numbers are caught as before.
- Table rows (`' | '`) are not claims.
- Duplicates are recognised with spacing, punctuation and the sentence ending removed
  (`_claim_key`).

On those three posts the detector now finds 2, 5 and 6 checks. Every price, limit and function
claim that carried a number is still there.
