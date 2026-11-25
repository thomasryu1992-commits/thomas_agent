# 2026-11-25 — Blog department: three ports regenerated to the vault's current rules

**What.** `scripts/blog_vendor.py --check` reported five ports as stale. Two belong to #1480 (preflight, variety). This PR
regenerates the other three, which had drifted since vault edits Thomas approved on 2026-11-21 (Thomas 2026-11-25:
"낡은 사본 3개 재생성 PR도 진행해줘").

- **`tistory_queue.py`**: `NEW_PER_DAY` 3 → 1. Tistory-only posts go out one a day until the AdSense re-application
  (vault `analytics/티스토리-애드센스-보강안-2026-11-21.md`, decision 3). The slot check (`blog/slot.py`) judged a reported
  slot against three a day, so a second new post on a day passed `new_cap` although the queue no longer places one.
- **`affiliate_link.py`**: the microphone kind folds place and bundle words (노래방, 강의용, 수업용, 유선, 세트) into one kind
  (Mac `handoff-1121-mic-kind`), so the same product is not issued under two keywords. A pin microphone stays its own kind.
- **`kw_pipeline.py`**: only the header commit and the `GENERATED_FROM_SHA256` line moved. The code is the same.

**Tests.** Two slot tests assumed three new posts a day and failed against the regenerated queue. They now take the day's
room from the queue's own constants, so they pass at one a day and at three a day (checked against both ports):

- a slot the queue would give passes every check, and one more new post on that day fails `new_cap`;
- regular posts fill the day exactly to its cap and still pass, which shows a fast post is not counted.

A new test pins the microphone kinds on the generated port. No repo test covered `affiliate_link.kind` before.

**Checked.** `blog_vendor.py --check` reports only `preflight.py` and `variety.py` as stale on this branch, and those are
#1480's. `pytest -k blog` passes locally (Python 3.14), and the release gate passes in check-only mode.

**Not in this PR.** No deploy. The Tistory queue itself runs from the vault file, which has used one a day since 11-21.
The port only feeds the slot shadow.
