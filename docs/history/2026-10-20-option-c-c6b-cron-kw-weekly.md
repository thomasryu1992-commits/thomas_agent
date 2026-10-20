# Option C C6b: the weekly keyword job stays with root (cron classification corrected)

**What changed:** records only.
- `HOST_PRIVILEGE_SEPARATION_C6B_CRON_V0.1.md`: row 12, the totals, and the status line.
- The execution packet's b6 and its P-4 list.
- `STATUS.md`, regenerated.

**Approval** (Thomas 2026-10-20): `승인: C6b cron 분류 문서 정정 PR 생성(12번 kw-weekly는 root 유지 — K-1 A, 실행 패킷 b6 대상 2줄로;
병합 제외)`.

**Why.** On 2026-10-19 row 12 (`kw-weekly.sh`) was classified as movable to `claude` once K-1 and K-2 were decided.
Preparing P-4 showed that the job runs `kw-pipeline.py run`, which needs the Naver search-ad key. K-1 A keeps that
key with root, so the job stays with root.

Only two jobs move at b6: loc-trend and the daily blog draft. The draft still calls `kw-pipeline.py slotc`; a
call-graph trace found that path never loads the key. The new totals are 11 root, 2 movable, 1 retired.
