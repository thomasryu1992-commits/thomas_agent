# 2026-09-30 — Blog: the web search looks for the target keyword, not the drafting brief

`pipeline.run_task` searched the request text (`normalized_goal or raw_request`). For the blog
content run, that text is a 1,800-character drafting brief, mostly about paragraphs, JSON and
lengths. Four manual runs in a row ('스티커제작업체', '명함만들기', 'CHATGPT사용법',
'방수스티커제작') came back with posts titled "문단", "문단 작성법", "국어 교과서 중심 문장
쓰기" and a GPT-prompt paste. The one earlier run that got relevant hits ('캡컷 사용법') got
them by luck of the keyword leading the query. #1048's "use the evidence's specifics" had no
specifics to use.

`run_task` takes an opt-in `search_query`, which replaces the request text as the web query
when given and not blank. The blog content run passes its target keyword. Every other caller,
and the blog's research and revision runs, are unchanged. `normalized_goal` is left alone,
because it also names the analysis record's goal and the correction-learning line.
