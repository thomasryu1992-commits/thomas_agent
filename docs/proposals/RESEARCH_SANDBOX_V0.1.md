# 제안: 연구 샌드박스 — 비밀이 필요한 수집과 비밀 없는 계산을 나눈다 (v0.1)

**상태:** DECIDED 2026-10-20 — 아래 기본값으로 정했다(Thomas 2026-10-20). 이 문서가 짓는 것은 네 단계다. 1 연구 데이터 내보내기, 2 샌드박스 도구, 3 Coinalyze 일간 데이터 저장, 4 샌드박스에서 OI 측정을 끝까지 돌리는 검증. 진행은 §8에 적는다.

**결정:** `결정: 연구 샌드박스 — 수집/계산 분리, 기본값(§7)으로 설계·구현을 순서대로 진행 (Thomas 2026-10-20: "검증을 진행하고 문제가 없다면 구현까지 순서대로 진행해줘")`.
그 전에 Thomas가 둔 방향 두 가지가 있다.
- "기존 문제는 env API를 함부로 쓰는 것이었는데, 그걸 막다가 측정과 자동화 자체가 안 되게 됐다."
- 유연성을 설계 단계에서 해결하라.

**기준:** origin/main `fa214229`, 2026-10-20. 호스트 수치는 같은 날 `claude` 계정에서 잰 값이다.

## 0. 한 줄

Claude가 비밀을 보지 않고도 측정을 혼자 반복하게 한다. 키가 필요한 수집은 지금처럼 운영이 고정된 코드로 한다. 계산은 키도 네트워크도 실거래 경로도 없는 샌드박스에서, Claude가 병합 전 코드까지 자유롭게 돌린다.

## 1. 문제

- **C6b가 지키려던 것:** 비밀 값(`.env`의 키)이 Claude에게 닿지 않는 것, 그리고 실거래 경로와 holdings에 닿지 않는 것이다.
- **같이 잃은 것:** C6b는 docker 권한을 거두면서 Claude가 계산을 실행시키는 능력까지 거뒀다. 둘이 docker 권한 하나에 묶여 있었기 때문이다.
- **2026-10-20에 실제로 일어난 일:** OI 미래 참조 측정 하나에 Thomas의 터미널 작업이 필요했다. 배포 두 번, 측정 실행, 결과 파일 복사였다.
  - 측정 스크립트를 운영 스케줄러 컨테이너 안에서 돌렸다. 이 컨테이너에는 메모리 상한이 없다.
  - 그 스크립트가 피처 조립 경로를 거치며 OI 저장소에 쓸 수 있다는 것도 뒤늦게 확인했다(§3).
- **root로 되돌리는 것은 답이 아니다.** 키·실거래·holdings 노출과 실수의 피해 범위가 함께 돌아온다. 같은 날 root 작업 중 사고가 두 건 있었다.

## 2. 원칙

**비밀이 필요한 것은 데이터를 받아 오는 순간뿐이다. 계산에는 비밀이 필요 없다.**

| | 수집 평면 | 계산 평면 (샌드박스) |
|---|---|---|
| 하는 일 | 연구 기록 내보내기, 키가 필요한 벤더 데이터 저장 | 측정, 재채점, 분석. 병합 전 코드 포함 |
| 코드 | 고정. PR → 배포로만 바뀐다 | 자유. Claude의 작업 트리 |
| 비밀 | 있다(운영 그대로) | 없다 |
| 네트워크 | 운영 그대로 | 막는다. 공개 데이터는 따로 속도를 조절해 받는다(§4.2) |
| 실행 계정 | root cron, 운영 서비스 | `claude` |

## 3. 검증 (2026-10-20, 이 문서를 쓰기 전에 잰 것)

| 항목 | 결과 | 의미 |
|---|---|---|
| 호스트 자원 | 2코어. 메모리 3.9GB 중 여유 1.16GB, 스왑 3.3GB 사용 중. 부하 평균 1.3 | 샌드박스는 상한이 있어야 한다 |
| 사용자 권한 자원 상한 | `systemd-run --user --scope`가 적용된다. `MemoryMax=150M`에서 400MB 할당은 종료(137), 50MB는 통과. `CPUQuota=25%`에서 실측 0.26 | root 없이 상한을 걸 수 있다 |
| 사용자 권한 네트워크 차단 | 안 된다. `unshare -rn`은 거부된다(`apparmor_restrict_unprivileged_userns=1`). `IPAddressDeny=any`는 user scope에서 무시된다(요청 200) | 강제 차단은 root 설치가 필요하다 |
| Python 소켓 차단 | `sitecustomize`의 `connect` 차단이 동작한다(urllib 요청이 막힘) | 규율 수준의 차단은 바로 된다 |
| 피처 프레임 메모리 (CPU 50%) | 4h 6,000봉 94MB·2.7초. 1h 24,000봉 280MB·10.5초. **15m 96,000봉은 900MB 상한에서 종료** | 15m은 운영에서든 샌드박스에서든 지금 구조로 돌리지 않는다 |
| Python 3.12 | `/opt/thomas-deploy/1bad6dc1…/python/bin/python3.12`(3.12.13, CR-8b로 들여온 것)를 `claude`가 실행할 수 있다. ssl 정상 | 운영과 같은 버전의 가상환경을 새로 내려받지 않고 만든다 |
| 수집기 메서드 | `collect`, `funding_history`, `derivative_price_klines`(바이낸스 공개), `open_interest_history`, `liquidation_history`(Coinalyze, 키 필요) | 키가 필요한 것은 Coinalyze 둘뿐이다 |
| Coinalyze 일간 데이터 | 매번 조회만 하고 저장하지 않는다. 시간 단위 OI(`open_interest_1h.jsonl`)만 저장된다 | 3단계에서 저장을 더한다 |
| 캔들 아카이브 | Hyperliquid 전용이다(`candle_archive`). 바이낸스 크립토 캔들은 저장하지 않는다 | 바이낸스 캔들은 공개 데이터로 받는다(§4.2) |
| 공개 데이터 접근 | `claude` 계정에서 `fapi/v1/klines`, `openInterestHist`, `data.binance.vision`이 모두 200 | 키 없이 받을 수 있다 |
| 외부 한도 | Coinalyze: 키당 분당 40회, 초과 시 429. 바이낸스: IP 단위 가중치이고, 주문·계좌 조회도 같은 `fapi.binance.com`·같은 IP를 쓴다. 캔들 페이징에 속도 조절이 없다 | 샌드박스는 Coinalyze를 부르지 않는다. 공개 데이터는 예산 안에서만 받는다 |
| 측정 경로의 쓰기 | `attach_feeds`가 `accumulate`와 무관하게 `oi_store.record_intraday_oi`를 부른다 | `measure_oi_lookahead`는 엄밀히 읽기 전용이 아니었다. 샌드박스는 운영 상태에 쓸 수 없다 |

## 4. 설계

### 4.1 연구 데이터 내보내기 (수집 평면)

- **누가 언제:** root cron이 1시간마다 호스트의 `python3`로 `scripts/ops/export_research_lake.py`를 돌린다. 측정 출력을 `/var/lib/thomas-measure/`에 두는 기존 방식(P-2)과 같은 구조다.
- **어디로:** `/var/lib/thomas-measure/research-lake/<UTC 스냅샷 id>/`.
  - 파일은 root:claude 0640, 폴더는 0750이다.
  - 임시 폴더에 다 쓴 다음 이름을 바꾼다. 그래서 반쯤 쓴 스냅샷은 보이지 않는다.
  - `current`가 최신 스냅샷을 가리킨다. 최근 3개만 남긴다.
- **무엇을:** 상태 폴더의 `crypto/` 아래 **허용 목록에 있는 연구 파일만** 복사한다. 목록은 코드 상수이고, 테스트가 고정한다.
  - **포함 (연구 기록):**
    - 후보: `strategy_candidates.jsonl`
    - forward cohort: `forward_cohorts.jsonl`, `forward_cohort_outcomes.jsonl`, `forward_cohort_positions.json`, `forward_cohort_nulls.jsonl`, `forward_cohort_null_outcomes.jsonl`, `forward_cohort_null_positions.json`
    - 트라이얼: `forward_trial_outcomes.jsonl`, `forward_trial_positions.json`, `forward_trial_null_outcomes.jsonl`, `forward_trial_null_positions.json`, `hypothesis_trial_closes.jsonl`
    - forward 장부: `forward_outcomes.jsonl`, `forward_positions.json`
    - 페이퍼·반사실: `paper_outcomes.jsonl`, `paper_position.json`, `counterfactual_outcomes.jsonl`, `counterfactual_positions.json`
    - 시장 데이터 저장소: `open_interest_1h.jsonl`, `positioning_history.jsonl`, `orderbook_depth.jsonl`
    - 3단계 뒤 추가: Coinalyze 일간 저장소
  - **제외 (목록에 없으므로 복사되지 않음):**
    - 계좌·실거래: `account_*`, `live_*`, `live_trading_budget.json`, `crypto_risk_limits.json`, `pre_order_risk_snapshots.jsonl`
    - 실행 단계: `execution_stage*`
    - 거래소 계약·테스트넷: `venue_contract*`, `signed_testnet_cycles.jsonl`, `stop_slippage_probe_plan.json`
    - 운영 표시와 잠금: 각종 `*_marks.json`, `*_refresh.json`, `*.lock`, `live_protection_watch*`
    - `active_strategy_pool.json`: 승인과 무장 상태를 담는다. 필요해지면 따로 정한다.
    - **`crypto/` 밖은 전부 제외한다.** holdings·운영 상태·승인·비밀이 여기에 해당한다.
- **목록 파일:** 스냅샷마다 `manifest.json`을 둔다. 파일별 크기, sha256, 원본 수정 시각, 내보낸 시각, 원본 상태 폴더 경로가 들어간다. 샌드박스 결과는 이 스냅샷 id를 기록한다.
- **안전:**
  - 읽기만 한다. 원본에 쓰지 않고, 잠금 파일도 만들지 않는다.
  - 복사 중에 스케줄러가 추가한 줄은 다음 스냅샷에 들어간다. 잘린 마지막 줄은 그대로 복사될 수 있으니, 읽는 쪽이 건너뛴다.
  - 크기 상한을 둔다(기본 2GB). 넘으면 그 회차를 건너뛰고 사유를 기록한다.

### 4.2 공개 시장 데이터 캐시 (비밀 없는 수집)

- 바이낸스 캔들·펀딩·파생 가격은 키가 필요 없다. 이 데이터는 `claude` 계정이 받아 `~/research/cache/`에 쌓는다.
- **IP 예산:** 운영과 같은 IP이므로 예산 안에서만 받는다.
  - 요청 가중치를 직접 세고, 분당 600(바이낸스 분당 한도 2,400의 25%)을 넘기지 않는다.
  - 응답 헤더 `X-MBX-USED-WEIGHT-1M`이 1,200(50%)을 넘으면 그 회차를 멈춘다.
  - 429·418을 받으면 그 자리에서 멈추고, 다시 시도하지 않는다.
  - 팩토리 창(매일 08:00–09:00 UTC)에는 받지 않는다.
- **캐시:** 이미 받은 구간은 다시 받지 않는다. 처음 한 번만 깊이(timeframe별 factory 깊이)만큼 받고, 그 뒤로는 새 봉만 덧붙인다.
- **Coinalyze는 부르지 않는다.** 키가 없고, 한도를 운영과 나눠 쓰기 때문이다. 일간 OI·청산은 3단계 뒤 연구 데이터 저장소에서 읽는다.

### 4.3 샌드박스 실행 (계산 평면)

- **래퍼:** `scripts/research/sandbox_run.sh <스냅샷 id|current> -- <명령>`. 하는 일은 다음과 같다.
  - `systemd-run --user --scope -p MemoryMax=700M -p MemorySwapMax=0 -p CPUQuota=50%`에 `nice -n 10`, `ionice -c3`을 붙여 실행한다.
  - `PYTHONPATH`의 맨 앞에 소켓 차단 `sitecustomize`를 둔다. 유닉스 소켓만 허용한다.
  - 3.12 가상환경 `~/research/venv`(§3의 3.12.13으로 만든 것)을 쓴다.
  - 환경 변수에 `RESEARCH_LAKE=<스냅샷 경로>`를 넘기고, `.env`나 키는 넘기지 않는다. 샌드박스 프로세스의 환경을 비우고 필요한 것만 넣는다.
  - 결과는 `~/research/runs/<run id>/`에 둔다. 명령, git sha, 작업 트리 dirty 여부, 스냅샷 id, 시작·종료 시각, 종료 코드, 최대 메모리를 남긴다.
- **상한 값의 근거:** 1h 프레임은 280MB이고, 묶음 4h(5심볼)는 470MB에 재생 여유분이 든다. 700MB면 들어가고, 여유 1.16GB 중 약 450MB가 운영에 남는다.
- **15m은 돌리지 않는다.** 래퍼가 `--timeframe 15m` 인자를 거부한다. 피처 계산을 바꾸기 전까지 그렇다.
- **수집기:** `scripts/research/lake.py`가 `MarketDataCollector`와 같은 메서드를 구현한다.
  - `collect`, `funding_history`, `derivative_price_klines`는 §4.2의 캐시에서 읽는다.
  - `open_interest_history`, `liquidation_history`는 연구 데이터 저장소에서 읽는다(3단계 뒤).
  - 기존 측정 스크립트는 이 수집기를 주입받아 그대로 돈다. 운영 코드는 바꾸지 않는다.

### 4.4 결과의 두 등급

- **탐색:** 샌드박스에서 자유롭게 돈다. 판정·승인·운영 결정의 근거로 쓰지 않는다.
- **공식:** 판정이나 기록에 쓰는 측정은 다음을 모두 지킨다.
  - **병합된 코드**로 돈다(작업 트리가 dirty가 아니고, sha가 origin/main에 있음).
  - **고정된 스냅샷 id**로 다시 돈다.
  - 사전 등록이 있으면 그 커밋보다 뒤에 돈다.
  - run 기록에 위 셋이 남는다. 래퍼가 `--official`일 때 이 조건을 검사하고, 어긋나면 거부한다.

## 5. 하지 않는 것

- 키·`.env`·거래 경로·holdings는 샌드박스에 들어오지 않는다. 연구 데이터 저장소에도 들어가지 않는다.
- 샌드박스는 운영 상태에 쓰지 않는다. 운영 상태 폴더를 마운트하거나 읽지 않고, 내보낸 사본만 읽는다.
- 15m 측정은 하지 않는다.
- 실시간 동작(주문, 라이브 라우팅)은 샌드박스에서 보지 않는다. 그것은 기존 읽기 통로(`claude_read.py`)의 영역이다.
- 이 문서는 판정 규칙을 바꾸지 않는다.

## 6. 위험과 대응

| 위험 | 대응 |
|---|---|
| 바이낸스 IP 한도를 운영과 나눠 쓰다 418 차단이 실거래 경로까지 번짐 | §4.2: 분당 가중치 600 이하, 헤더 50%에서 멈춤, 429·418에서 즉시 멈춤, 팩토리 창 제외, 캐시로 재요청 없음 |
| Coinalyze 키 한도 소진 | 샌드박스는 Coinalyze를 부르지 않는다. 3단계 저장은 운영의 기존 호출 결과를 저장하는 것이라 호출 수가 늘지 않는다 |
| CPU·메모리 경합 | 메모리 700MB 상한(넘으면 그 프로세스만 종료), CPU 50%, nice 10, 15m 거부 |
| 비밀이 출력으로 샘 | 샌드박스에 비밀이 없다 |
| 데이터 지연 | 내보내기 주기 1시간. 연구에는 충분하다 |
| 3.12 가상환경이 실행기 경로에 묶임 | 실행기를 업그레이드해 경로가 바뀌면 `scripts/research/make_venv.sh`로 다시 만든다 |
| 네트워크 차단이 규율 수준 | 샌드박스 코드는 Claude 자신의 코드다. 강제 차단은 root가 systemd 템플릿(`PrivateNetwork=yes`)과 polkit 규칙을 설치하면 된다. 필요해지면 따로 정한다 |

## 7. 기본값 (이 값으로 정했다)

| 항목 | 값 |
|---|---|
| 내보내기 방식·주기 | root cron, 매시 17분. 최근 스냅샷 3개 보존. 크기 상한 2GB |
| 허용 목록 | §4.1 포함 목록. 바꾸려면 PR |
| 공개 데이터 예산 | 분당 가중치 600, 헤더 1,200에서 멈춤, 팩토리 창 08:00–09:00 UTC 제외 |
| 샌드박스 상한 | 메모리 700MB(스왑 0), CPU 50%, nice 10, ionice idle |
| 네트워크 | Python 소켓 차단(규율 수준). 강제 차단은 보류 |
| Coinalyze 일간 저장 | 운영이 이미 받는 일간 OI·청산을 저장소에 쌓는다(3단계) |
| 탐색과 공식 | §4.4 |

## 8. 단계와 진행

| 단계 | 하는 일 | 누가 | 완료 기준 |
|---|---|---|---|
| 1 | `scripts/ops/export_research_lake.py`와 시험. Thomas가 root crontab에 한 줄을 더한다 | Claude(PR), Thomas(설치) | `current` 스냅샷이 생기고, `claude`가 읽을 수 있으며, 목록 밖 파일이 없다 |
| 2 | `scripts/research/`의 `make_venv.sh`, `sandbox_run.sh`, `fetch_public.py`, `lake.py`와 시험 | Claude(PR) | 래퍼가 상한·소켓 차단·기록을 지키고, 공개 데이터 수집이 예산을 지킨다 |
| 3 | 런타임이 Coinalyze 일간 OI·청산을 저장소에 쌓는다. 내보내기 목록에 추가한다 | Claude(PR), Thomas(배포) | 저장소가 차고 스냅샷에 들어간다 |
| 4 | `measure_oi_lookahead`를 샌드박스에서 끝까지 돌린다(4h·1h) | Claude | Thomas 손 없이 결과와 판정이 기록된다 |

**진행 (2026-10-20):** 1단계부터 시작한다.
