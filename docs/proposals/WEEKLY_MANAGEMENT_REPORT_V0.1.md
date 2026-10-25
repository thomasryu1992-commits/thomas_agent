# 제안: 주간 경영 보고 — 레인 요약·대기 승인·실패 스케줄·피드백 통계를 관제봇으로 (DRAFT v0.1)

**상태:** DRAFT 2026-10-25 — 매주 한 번 네 절(레인 요약, 대기 승인, 실패 스케줄, 피드백 통계)을 묶어 관제봇으로 보내는 스케줄 kind `management_report`의 설계다. Hermes가 은퇴하면서(#1305·#1307) 정기 보고를 보내는 주체가 없어졌고, 이 보고가 그 자리를 채운다. 결정 4개(Q1 발송 경로, Q2 보유자산 포함 여부, Q3 시각, Q4 피드백 통계 범위)를 기다린다. 이 문서는 구현·배포·권한을 승인하지 않는다.
**요청:** Thomas 2026-10-25 — "주간 경영 보고 스케줄 설계부터 시작해. 레인 요약, 대기 승인, 실패 스케줄, 피드백 통계를 묶어서 관제봇으로."
**근거:** 2026-10-25 회사형 설계 점검. 비서 역할 중 빠진 것 세 가지가 확인됐다. 정기 경영 보고가 없고, 스케줄로 돈 분석은 결과를 보내지 않으며(`scheduler.py`의 analysis_task 분기), 피드백은 개수 세기 말고는 다시 읽히지 않는다(`store.py`). 이 문서는 첫째를 채우고, 둘째와 셋째는 보고에 숫자로 드러낸다.

## 1. 무엇이 사라졌고, 무엇으로 채우나

Hermes의 cron 다섯 개(`integrations/hermes/config/cron-jobs.template.json`, #1305에서 제거) 가운데 셋이 정기 보고였다.

- 매일 아침 브리핑: 크립토 조회 5개를 묶은 것
- 주간 결정 상신: 작업 목록·메모리 후보·페이퍼 권고
- 주간 레인 요약: `lane_digest`를 전달한 것

D8(`SYSTEM_REVIEW_IMPROVEMENT_PLAN_V0.1.md`, 비크립토 레인 증거 기한, 검토일 11-25)의 증거도 이 주간 요약에 기대고 있었다.

이 보고는 셋째와 둘째를 하나로 묶는다. 첫째(크립토 일일 상태)는 이미 `crypto_report` kind가 대시보드를 보낸다. 그러니 여기서는 크립토를 한 줄로만 다룬다.

## 2. 보고의 내용

머리말 한 줄: 기간(KST, 지난 7일)과 런타임 상태(`control` 모드·정지 수준). 그 아래 네 절이 온다. 절마다 독립적으로 읽고, 한 절을 읽지 못하면 그 절에 `읽지 못함: <reason_code>`를 쓰고 나머지 절은 그대로 보낸다.

### 2.1 레인 요약

- **비크립토 레인:** `lane_digest.lane_digest(ledger, since, until)`를 그대로 쓴다. 역할별 실행 수, 전달·보류·차단, 응답한 모델, 페일오버가 나온다. 아카이브까지 읽으므로 회전한 기록도 셈에 들어간다. 렌더는 `lane_digest.render`를 줄여 쓰거나 같은 fold에서 짧은 표를 만든다.
- **크립토 레인:** `crypto.dashboard.build_status`에서 한 줄만 쓴다. 사이클 수, 경고 수, 실행 단계(READ_ONLY·PAPER 등). 어느 필드를 쓸지는 구현 때 실제 키를 보고 정하고, 대시보드 본문은 `crypto_report`의 몫으로 둔다.

### 2.2 대기 승인

`ApprovalStore.pending()` 가운데 `approval.is_expired`가 아닌 것을 보낸다. 줄마다 승인 id, 종류(target 접두사), 만료 시각만 쓴다. 이 범위는 `store_reads.read_approval_status`가 이미 내보내는 필드 안이다. 행동 스냅샷이나 fingerprint는 넣지 않는다. 결정은 그대로 관제봇의 `/approve`·`/reject`가 한다.

### 2.3 실패 스케줄

- **지금 실패 중인 것:** 스케줄 행의 `last_status`가 `failed:<CODE>`인 행. id, kind, 코드를 쓴다.
- **7일간 실패한 fire 수:** 스케줄별로 센다. 스케줄러 이벤트 파일은 2,000행에서 회전하므로(`retention.DEFAULT_KEEP_ROWS`) 활성 파일만으로는 7일이 안 될 수 있다. 그래서 실행 기록에 쓰는 아카이브 읽기(`iter_records_with_archive`)와 같은 방식으로 스케줄러 이벤트 아카이브를 읽는 함수를 하나 더한다. 실제로 읽은 기간이 7일보다 짧으면 그 기간을 그대로 적는다.
- **상태 문구:** 원문을 보내지 않는다. 실패 문구에는 예외 메시지가 들어갈 수 있다(`store_reads`의 projection v1이 같은 이유로 코드만 남긴다). 그래서 코드만 쓴다.

### 2.4 피드백 통계

- **집계:** 7일간 `/feedback` 건수를 GOOD·BAD·NOTE로 나눈다.
- **레인별:** 피드백 이벤트의 `trace_id`를 원장의 `role_assignment`와 이어 레인별로도 센다. `lane_digest`가 실행을 레인으로 묶는 것과 같은 키라 스키마를 바꾸지 않는다.
- **피드백 비율:** 같은 기간 전달된 결과 수 대비 피드백 수를 함께 쓴다.
- **이 절이 하지 않는 것:** 피드백을 다시 읽어 우선순위나 프롬프트에 쓰는 일은 이 절의 범위가 아니다(Q4).

## 3. 어떻게 도나

- **새 kind `management_report`:** maintenance 레인(scheduler-maint)에서 돈다. 늦어도 신선도만 잃고 돈은 잃지 않는 일이다. `KINDS`·`MAINTENANCE_KINDS`에 넣으면 kind 분할 테스트가 분류를 강제한다.
- **동작:** 읽기만 하고 알림 한 번을 보낸다. `crypto_report`(C13)와 같은 모양이고, 모델 호출과 외부 조회가 없다. 정책 조항도 필요 없다(C13 선례). 단, Q1의 A안은 아래처럼 env 게이트 호출 지점을 하나 더한다.
- **렌더:** 순수 함수 `render_management_report(sections) -> str`로 둔다. 텔레그램 4,096자 분할은 `TelegramChannel`이 이미 한다.
- **발송 실패:** fire 실패(`SchedulerBlocked`)로 올린다. `crypto_report`는 발송 실패를 상태 문자열로만 남기지만, 주에 한 번 오는 보고가 조용히 사라지면 안 된다. 그래서 기존 실패 전환 알림이 받게 한다. 그 알림은 지금 알림 봇으로 간다(Q1).
- **등록:** Thomas가 컨테이너 CLI로 `scheduler_cli add --kind management_report --interval-seconds 604800`을 한다. 스케줄은 간격 기반이고 첫 fire는 등록 시각 + 7일이다(`scheduler.build_schedule`의 `next_run_at`). 그래서 Q3의 요일·시각에 등록하면 다음 주 같은 시각부터 돈다.

## 4. 결정 (Thomas)

**Q1. 어느 봇으로 보내나.** 지금 스케줄러 알림은 관제봇이 아니라 알림 봇(옛 Hermes 봇)으로 간다. 두 scheduler 서비스의 `TELEGRAM_BOT_TOKEN`이 `${HERMES_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}`이기 때문이다(`docker-compose.yml`).

- **A (권고):** scheduler-maint에 관제봇 토큰을 다른 이름으로 하나 더 넘긴다. 예: `MVP_CONTROL_BOT_TOKEN: ${TELEGRAM_BOT_TOKEN:-}`. 이 kind만 `TelegramChannel(token_env=…)`로 그 이름을 읽어 보낸다.
  - 토큰을 쥔 서비스 집합은 바뀌지 않는다. `SECRET_OWNERSHIP`에서 `TELEGRAM_BOT_TOKEN`은 이미 scheduler-maint에 있다.
  - scheduler-maint는 `getUpdates`를 부르지 않으므로 operator의 폴링과 충돌하지 않는다.
  - 비공개 채팅의 chat id는 사용자 id라 등록 채팅은 두 봇에서 같다.
  - 바뀌는 것: compose 블록 하나, env 게이트 호출 지점 하나. 후자는 `test_the_env_only_gate_has_exactly_the_capabilities_thomas_named`에 이름을 올리는 거버넌스 변경이다.
- **B:** `.env`에서 `HERMES_BOT_TOKEN`을 지워 모든 레인 알림을 관제봇으로 되돌린다. 코드 변경은 없다. 대신 `hermes-notify.sh`(Mac 일일 루틴, 월요일 블로그 리포트, `at` 날짜 점검)가 같은 변수를 읽으므로 그 알림이 모두 끊긴다.
- **C:** 알림 봇으로 보낸다. 변경이 가장 적지만 요청한 관제봇이 아니다.

**Q2. 보유자산을 넣나.**
- **권고: 넣지 않는다.** 보유 숫자는 H3의 표 하나만 프로세스 밖으로 나갈 수 있고, 텔레그램 메시지도 그 "밖"이다(CLAUDE.md, holdings 규칙).
- 이 보고에는 `holdings_refresh`가 실패했을 때만 실패 스케줄 절에 이름이 나온다.
- 넣으려면 `holdings_status`가 내보내는 합계 표를 그대로 붙이는 안을 따로 정한다.

**Q3. 언제 보내나.** **권고: 월요일 09:00 KST(일요일 24:00 UTC).** 주말 동안의 실패와 대기 승인을 주초에 본다. 월요일 10:00·10:30의 블로그 주간 수집·리포트와 겹치지 않는다.

**Q4. 피드백 통계는 어디까지.** **권고: v1은 집계만** 한다(§2.4).
- 피드백을 다시 읽어 쓰는 일(E2 평가·E3 선호 학습, `operator_feedback.py` 주석)은 로드맵에 없다. 그래서 `REMAINING_WORK.md`에 항목으로 등록만 하고 이 PR에서는 짓지 않는다.
- 스케줄 분석 결과를 보내는 일(점검 공백 2)도 별도 결정으로 둔다. 이 보고는 그 수를 "전달 안 된 스케줄 분석 N건"으로만 보인다.

## 5. 하지 않는 것

- 모델 호출, 외부 조회, 새 정책 조항, 새 승인 종류를 넣지 않는다.
- 보유 금액·종목을 쓰지 않는다(Q2).
- 스케줄 상태 원문, 승인 스냅샷, 피드백 코멘트 원문을 보내지 않는다. 코멘트는 개수만 센다.
- 실패한 fire를 다시 돌리거나 승인을 대신 처리하지 않는다. 보고는 읽기만 한다.

## 6. 구현 계획 (Q1–Q4 결정 뒤, PR 하나)

1. `runtime/mvp_runtime/management_report.py`: 절별 수집 함수 네 개와 `render_management_report`.
2. `LedgerStore`: 스케줄러 이벤트를 아카이브까지 기간으로 읽는 함수(실행 기록 쪽 패턴 재사용).
3. `scheduler.py`: `KIND_MANAGEMENT_REPORT`, `MAINTENANCE_KINDS`, `_execute` 분기(발송 실패는 raise).
4. Q1=A이면: compose의 scheduler-maint에 변수 하나, `SECRET_OWNERSHIP`·`DEPLOYMENT.md` 표, env 게이트 목록 테스트.
5. 테스트:
   - 각 절을 고정 원장 픽스처로 검사한다.
   - 한 절이 실패해도 나머지가 나가는지 본다.
   - 상태 원문이 메시지에 들어가지 않는지 본다(예외 문구를 심은 픽스처로).
   - 4,096자를 넘는 입력이 분할되는지 본다.
   - 발송 실패가 fire 실패가 되는지 본다.
   - kind 분할 테스트를 갱신한다.
6. `docs/history/` 한 파일, D8 문서에 "주간 다이제스트 전달 주체 = management_report" 한 줄, 이 문서의 상태 줄 갱신.

호스트 단계(Thomas): 배포 뒤 `scheduler_cli add`로 등록하고, Q1=A이면 `.env` 변경은 필요 없다(같은 `TELEGRAM_BOT_TOKEN` 값을 다른 이름으로 넘길 뿐).

승인 한 줄 예: `승인: WMR-1 Q1=A Q2=제외 Q3=월09KST Q4=집계만, 구현·PR (Thomas 2026-10-25)`
