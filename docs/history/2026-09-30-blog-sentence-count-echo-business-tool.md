# 2026-09-30 — Blog draft: sentence count in the shape, echoes pointed at, business vs tool

Scored on candidate-1076 (당근 비즈프로필 ≈65, 스티커소량제작 ≈58, 카카오톡 채널추가 ≈68). Three
fixes, one commit each.

1. **Sentence count in the shape.**
   - Over 21 first drafts, all from the same model (gemini-flash-lite), the paragraph count was
     17 every time. That count is carried by the shape and the plan.
   - "문단 하나는 4문장" was said in prose only, and it split the drafts in two:
     - about 4 sentences per paragraph gave 2,100–2,900 characters and passed;
     - about 3 gave 1,530–1,810 and fell under the 1,800 floor (10 of 21). Each of those cost a
       revision, and the revision padded.
   - `_DRAFT_SHAPE` now shows every planned paragraph with its count, as "도입 문단 1(4문장)" and
     "문단 1(4문장)". The request adds that these placeholders are not prose.
   - More paragraphs is not the fix. The 4-sentence drafts already run 160–180 characters per
     paragraph, and 22 of them would pass the 3,500 ceiling.
2. **Echoes pointed at, not gated.** '카카오톡 채널추가' closed paragraphs by restating the
   sentence before ("버튼을 누르는 순간 바로 등록이 끝납니다").
   - **Measure:** `blog_draft.echo_sentences` flags a sentence when 0.45 or more of its character
     pairs already appear in one earlier sentence of the same paragraph.
   - **Calibration:** across the 21 drafts it flags three sentences of that draft and none
     elsewhere. Most reworded echoes score lower, and any cut low enough to catch them flags
     sound sentences in good drafts.
   - **Result:** POST.md points at the flagged sentences for the editor, and it is not a
     failure. The grow ask now says a restatement is not added length.
3. **Business vs tool.** Once tools had to be named, '스티커소량제작' wrote "마플 같은 플랫폼",
   which is a print shop with an editor. `VENDOR_NAME_ASK` is now a set of labelled sentences
   (업체·도구·통신사·공공·상품명) built around one test: what the reader does there.
   - A place the reader pays to have something made or sold is a business, editor or not.
   - Software the reader operates is a tool.

Items 1 and 3 are prompt changes; their effect is for the next scored runs to show.
