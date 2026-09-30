# 2026-09-30 — Site names pointed at, a hosted call's timeout made wall clock, keyword places in the shape

Scored on candidate-1079 (인스타 광고 하는법 ≈66, 현수막당일제작 ≈62, 캔바 사용법 ≈62). First
drafts passed length 3 of 3 after #1078. Three remaining problems, one commit each.

1. **A cited site's name in the body.** '현수막당일제작' wrote "네모디 같은 곳" next to two shops
   it had made anonymous.
   - **Detection:** `blog_draft.source_site_name` takes the short Korean name a page title ends
     with ("… | 네모디"), and `site_names_in_body` finds it in the body. The target keyword's own
     name is exempt.
   - **Advisory, not a gate:** a shop cannot be told from a tool mechanically, so POST.md points
     at the name. Across 27 packages it found 비즈하우스, 마플 and 네모디 (shops) and 워드바이스
     AI and 페이퍼팔 (tools).
2. **A hosted call's timeout is wall clock.** This is shared code, used by every role.
   - **The gap:** `urlopen(timeout=…)` bounds each socket operation, not the call. A server
     writing a byte now and then held a call past its timeout; reproduced locally, timeout=2
     against one byte a second took 6 s. `FailoverProvider` splits its budget per member
     assuming wall clock, so one slow member could leave the next "not_tried".
   - **The symptom it matches:** the '캔바 사용법' revision was blocked with
     `PROVIDER_CHAIN_EXHAUSTED` (openrouter unparseable, google "the call's time budget ran out
     before this member") in an 856 s fire. The ledger keeps no timing for that blocked call, so
     whether openrouter trickled there is inference. The mechanism is what was fixed.
   - **The fix:** `_read_within` runs the read on a daemon thread joined for the call's time.
     Past it, the call raises `PROVIDER_TRANSPORT` "exceeded the call's time budget", which fails
     over as transport. A 429/503 or `json_validate_failed` retry is made only if it fits in what
     is left of the call; before, it got a fresh full timeout, up to 2T+5 s.
   - **Blast radius:** tightening only. A call that finishes inside its time is unchanged.
3. **The keyword's places in the shape.** '캔바 사용법' used its keyword once, and the revision
   that would have fixed it was blocked. `_draft_shape(target)` marks the intro's first paragraph
   and each section's first with "'키워드' 1회", six at most, which is the ceiling. It is the
   first request only; the revision keeps the target-free `_DRAFT_SHAPE` and its named-paragraph
   keyword ask.
