# 2026-09-30 — Blog draft: use the evidence's specifics, by name

A read-through of `bcp_c82a3c17ded878ca24ea` ('캡컷 사용법', `ready_for_review`) found prose true
of any editing app: "load, cut, add captions, save". The app was named once, and none of the menu
names in its own sources [S1]/[S3]/[S4] were used. The request's only rule about evidence was the
prohibition ("근거 블록에 없는 수치·가격·출처를 지어내지 마라"). Alone, it pushed the drafts toward
statements too general to be wrong.

`content_request` now carries `EVIDENCE_SPECIFICS_ASK`: in each section, write at least one
specific from the evidence blocks (a menu, button or feature name, a procedure step, a setting) by
its own name, and cite it in `sources`. Do not fill paragraphs with statements true of any
tool. Still invent no name or value the evidence does not have.

Limit, recorded here: the evidence is Tavily's short `content` field, five hits of about 100~300
characters. More of it (`search_depth`, raw content) is a change to the shared search tool and
its free-tier credits, and is out of this change's scope.
