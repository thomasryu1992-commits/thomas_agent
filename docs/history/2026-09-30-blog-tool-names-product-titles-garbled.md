# 2026-09-30 — Blog draft: tools named, product titles not copied, garbled titles caught

Scored on candidate-1074/1075 (스티커만들기 ≈50, 영업신고증 발급 ≈62, 배너입간판 ≈55). Three
fixes, one commit each.

1. **A tool must be named.** With "앱·AI 도구 이름은 써도 된다" (may), '스티커만들기'
   (`bcp_b8dcea9bca60d9a7a883`) cited Canva's and Adobe Firefly's pages and still called them
   "온라인 서비스" and "특정 앱" throughout. `VENDOR_NAME_ASK` now says tools are not businesses:
   a tool from the evidence is named, and blurring it is forbidden.
2. **A product title is not copied.** '배너입간판' (`bcp_6625fde02d5be7682185`) carried the shop
   listings' search-engine copy ("매장광고판 카페입간판", "패트지 현수막제작", "플랜카드제작") into
   its sentences. The request asks for the kind of product, briefly ('A형 철제 입간판').
3. **A mis-decoded title is caught.** gov.kr's EUC-KR page reached the lane already garbled:
   "ǰ û | οȳ  û | 24" was printed as the source title of '영업신고증 발급'
   (`bcp_5e11bdec987364f7116c`). The Hangul is gone before the lane sees it, so nothing can be
   decoded back.
   - `blog_draft.looks_garbled` flags two or more characters from blocks that Korean and English
     text never use: Latin Extended-A/B, IPA, Greek, and Armenian through Thaana.
   - `readable_title` falls back to the URL's host, and `_evidence_notes` skips a garbled snippet.
   - Over the ledger's 319 hit titles, exactly the two gov.kr ones are flagged; the five flagged
     snippets are that page and a binary download.
   - The shared search tool and the worker's prompt context are left as they are, because other
     lanes use them.
