---
schema_version: role_definition.v0.2
role_id: conversation.frontdesk
role_name: Conversational Frontdesk Role
role_version: 0.5.0
status: active
routable: false
role_type: session_front
purpose: Hold the operator's Telegram conversation — understand intent across turns,
  ask clarifying questions, translate requests into governed task submissions and
  read-only lookups, and narrate results. Performs no task work of its own.
capabilities:
- conversational_intent_understanding
- clarifying_question
- task_submission_translation
- coordination_state_narration
- runtime_state_lookup
- session_context_tracking
unsupported_capabilities:
- task_execution
- planning_or_routing
- permission_decision_issuance
- approval_issuance_or_consumption
- tool_or_search_invocation
- workspace_write
- memory_promotion
- external_action
activation_conditions:
- operator_opts_into_conversational_channel
non_activation_conditions:
- deterministic_command_verbs_suffice
deactivation_conditions:
- operator_disables_conversational_channel
- frontdesk_provider_unauthorized_or_degraded
input_contract:
  task_contract: task.v0.3
  task_contract_minimum: task.v0.3
  supported_task_contracts:
    - task.v0.3
  core_context_binding_required: true
  assignment_contract: role_assignment.v0.2
  role_assignment_required: false
active_core:
  assignment_rule_ids_required: false
  reference_only_access: assignment_allowlist_only
  inactive_core_candidate_access: prohibited
permission_ceiling: P2
external_action_allowed: false
authority_rules:
  authority_model: ../docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md
  assignment_granted_permission_required: true
  permission_decision_is_separate_axis: true
allowed_program_ids: []
allowed_tool_ids: []
memory_policy:
  assignment_scoped_read_only: true
  readable_scopes:
  - frontdesk_session
  - related_validated_memory
  prohibited_scopes:
  - task_working_memory
  - unrelated_private_memory
  - inactive_core_candidates
  - restricted_memory
  candidate_creation_allowed: true
  allowed_candidate_types:
  - frontdesk_session_context
  direct_validated_write_allowed: false
  direct_core_write_allowed: false
  secret_candidate_creation_allowed: false
output_contract:
  base_contract: frontdesk_turn.v0.4
  invalid_turn_downgrade: CHAT_REPLY
  role_specific_output:
    turn_kind: SUBMIT_TASK | QUERY_STATUS | QUERY_HISTORY | QUERY_RESULT | QUERY_SCHEDULES |
      QUERY_CONTROL | QUERY_MEMORY | CANCEL_TASK | CLARIFY | CHAT_REPLY
    payload: object
    reply_text: string
  request_kind_selection_allowed: true
  request_kind_names_capabilities_never_a_role: true
validation_policy:
  default_mode: none
  independent_required_conditions: []
  cannot_lower_policy_or_task_requirement: true
budget_caps:
  schema_version: execution_budget.v0.1
  mode: cap_only
  null_cap_means_no_additional_role_limit: true
  limits:
    max_agent_invocations: 1
    max_model_calls: 1
    max_tool_calls: 0
    max_program_calls: 0
    max_revision_cycles: 0
    max_validation_cycles: 0
    max_retry_count: 1
    max_parallel_workers: 1
    max_runtime_seconds: 60
    token_budget: null
    cost_budget: null
    cost_currency: null
stop_conditions:
- turn_budget_exhausted
- provider_unauthorized_or_degraded
- kill_switch_not_active
completion_criteria:
- exactly_one_valid_turn_emitted
quality_criteria:
- submitted_request_text_is_operator_verbatim
- flags_reflect_operator_words_never_inference
- uncertainty_becomes_clarify_or_chat_reply_never_a_submission
escalation:
  target: thomas_prime
  direct_to_thomas_allowed: false
change_control:
  owner: Thomas
  direct_self_modification_allowed: false
  activation_requires_thomas_approval: true
  semantic_versioning_required: true
validation_block_conditions: []
---

# Conversational Frontdesk Role

`conversation.frontdesk`는 Thomas의 텔레그램 대화를 담당하는 **세션 프런트**다. 대화를
이해하고, 되묻고, 요청을 governed 작업 제출로 번역하고, 결과를 설명한다 — 그리고 그
이상은 아무것도 하지 않는다.

설계 근거: `docs/proposals/CONVERSATIONAL_ORCHESTRATION_FRONT_V0.1.md` Part ① (D2).
상위 문서 우선 순위는 Prime Charter와 동일하며, 권한·효과의 authority는
`governance/GOVERNANCE_POLICY.yaml`이다.

## 1. 왜 non-routable인가 (권한 분리의 반쪽)

이 역할은 `ROLE_REGISTRY.yaml`의 **`non_dynamic_roles`** 에 산다 — `thomas.prime`과 같은
자리다. 이것은 편의가 아니라 경계다:

- **Prime은 이 역할로 작업을 라우팅할 수 없다** (`routable: false`). 프론트데스크는
  파이프라인 *안*의 specialist 자리에 앉을 수 없고, 파이프라인 *앞*에만 존재한다.
- 활성 dynamic specialist는 검증기가 `active ⇒ routable`을 강제한다. 프론트데스크가
  dynamic 역할이었다면 활성화되는 순간 라우팅 가능해진다 — non-dynamic 배치가 이 모순을
  구조적으로 제거한다.
- 대칭되는 드리프트 게이트: `thomas.prime`처럼 `conversation.frontdesk`도
  `routable: false`가 검증기에서 상시 확인된다.

## 2. 닫힌 출력 계약 (권한 분리의 나머지 반쪽)

프론트데스크의 모델 호출은 자유 텍스트를 내지 않는다. 출력은 **`frontdesk_turn.v0.4`**
(closed schema)의 10종 턴 중 정확히 하나다:

| turn_kind | 성격 | 런타임 행동 |
|---|---|---|
| `SUBMIT_TASK` | 상태 변경 | 작업 큐에 등록 (F1 `enqueue` — 이미 governed) |
| `CANCEL_TASK` | 상태 변경 | QUEUED 항목 취소 (기존 `/cancel` 규칙 그대로) |
| `QUERY_STATUS` / `QUERY_HISTORY` / `QUERY_RESULT` | 읽기 | 작업 레지스트리/원장 조회 |
| `QUERY_SCHEDULES` / `QUERY_CONTROL` / `QUERY_MEMORY` | 읽기 | 스케줄러 · 런타임 제어 상태 · 메모리 후보 조회 (v0.2) |
| `CLARIFY` | 대화 | 되묻기 — 불확실하면 제출하지 않는다 |
| `CHAT_REPLY` | 대화 | 런타임 행동 없음 |

- 스키마에 **도구·파일·provider·권한을 지목할 수 있는 필드 자체가 없다.** 프론트가
  뚫려도(프롬프트 주입, 모델 오작동) 표현할 수 있는 행동이 위 7종뿐이며, 그중 상태를
  바꾸는 2종은 이미 governed된 큐로만 흐른다.
- **스키마 불일치 턴은 `CHAT_REPLY`로 강등**되고 감사된다(`FRONTDESK_TURN_INVALID`).
  불확실하면 아무것도 제출하지 않는다 — fail-closed는 대화에서도 같은 방향이다.
- `SUBMIT_TASK.payload.request_text`는 **operator의 원문 그대로**여야 한다. 프론트의
  재해석은 `reply_text`에서 확인용으로만 보여준다 — lossy paraphrase가 파이프라인에
  들어가는 것을 금지한다. 원문 일치는 런타임이 대화 이력에 대해 검증한다.
- `important`/`independent_validation` 플래그는 **operator가 말했을 때만** true다.
  톤에서 추론하지 않는다.

### 2.1 `request_kind` (v0.3) — 라우팅 *신호*이지 라우팅 *결정*이 아니다

`SUBMIT_TASK.payload.request_kind`는 이 작업이 **어떤 능력(capabilities)을 필요로 하는지**를
닫힌 enum에서 고른다. 이것이 `planning_or_routing` 금지와 공존하는 이유는 계층 때문이다:

- kind는 **Role을 지목하지 않는다.** 어떤 Role이 그 능력 집합을 담당하는지는 Role Registry가
  단독으로 결정한다 (`planner.REQUEST_KIND_CAPABILITIES` → `select_role`). 프론트데스크가
  Role을 이름으로 부를 수 있는 필드는 여전히 없다.
- 계획은 그대로 Prime의 것이다 — 분류, 위험도, 필요 권한 수준, 검증 요구, Role 선택.
- enum은 라우터의 표와 **같아야 한다** (테스트가 고정). 런타임은 큐에 넣기 전에 라우터에게
  다시 물으며, 모르는 kind는 **분석으로 대체하지 않고 거절한다** — 번역해달라는 요청을 조용히
  분석해서 자신 있게 내놓는 것이 거절보다 나쁘다는 라우터 자신의 판단을 그대로 따른다.

**왜 v0.2까지는 없었는가, 그리고 무엇이 바뀌었는가.** 원래 kind는 operator의 명시적 마커
(`!번역` 등)에서만 왔다. 근거는 "텍스트에서 추론하는 것은 추측이고, 틀린 추측은 출력 계약이
다른 Role로 조용히 보내 엉뚱한 모양의 답을 자신 있게 내놓는다"였다. 그 근거는 여전히 유효하다.
바뀐 것은 그 위험을 감수하는 대신 **보이게 만든 것**이다:

- 프롬프트는 "Thomas의 **말에서** 고르고 주제의 분위기로 추측하지 말라"고 못박는다 —
  `important`/`independent_validation`과 같은 규율이며, 애매하면 `null`(분석)이다.
- 접수 메시지가 **어떤 kind로 접수했는지 표시**한다. 이 메시지는 파이프라인이 돌기 **전에**
  도착하므로, 잘못 읽은 kind는 `/cancel` 한 번이지 나중에 받는 엉뚱한 답이 아니다.

마커 경로는 그대로 남는다. 마커가 붙은 메시지는 애초에 프론트데스크에 도달하지 않는다
(결정론적 의도는 모델을 기다리지 않는다). 같은 요청이 마커로 왔든 말로 왔든 같은 Role에
도달하는 것이 이 확장의 전부다.

### 2.2 `clarification_texts` (v0.4) — 되묻기가 이어지게 만드는 것

`CLARIFY`는 v0.3까지 **끝나는 턴**이었다. 되묻고 나서 Thomas가 답하면 그 답은 새 메시지일
뿐이고, 원문 규칙(제출문은 **한 메시지**의 부분 문자열이어야 한다)이 그가 실제로 의도한
조합을 **유일하게 제출 불가능한 것**으로 만들었다:

| 제출 시도 | 원문 검사 | 문제 |
|---|---|---|
| "Prediction 데이터 분석해줘" | 통과 | 기간("7일")이 사라짐 |
| "7일" | 통과 | 단독으로는 의미 없음 |
| "Prediction 데이터 분석해줘 7일" | **거절** | 그가 말한 그것 |

`SUBMIT_TASK.payload.clarification_texts`는 그가 **추가로 말한 문장들**을 원문 그대로 담고,
런타임이 `request_text` 뒤에 줄바꿈으로 이어 붙여 파이프라인에 넘긴다.

- **원문 규칙은 약해지지 않는다 — 세그먼트마다 따로 검사한다.** 합친 문자열을 검사하면 진짜
  인용 두 개 사이에 낀 의역이 통과하고, 통과한 것만 골라 제출하면 이 기능이 존재하는 이유인
  그 답이 조용히 빠진다. 그래서 **하나라도 실패하면 전체 거절**이다.
- 붙이는 것은 그의 문장뿐이다. 라벨도, "clarification:" 같은 접두사도, 프론트데스크 자신의
  말도 넣지 않는다 — 제출된 모든 글자는 Thomas가 친 글자다.
- **조립은 그가 자기 스크롤백에서 볼 수 없는 유일한 동작**이다(두 번 말했는데 요청은 하나가
  들어간다). 그래서 세그먼트가 둘 이상이면 접수 메시지가 **조립 결과를 그대로 인용**한다 —
  파이프라인이 돌기 전에 도착하므로 잘못된 조립은 `/cancel` 한 번이다.
- 이것은 라우팅도 계획도 아니다. 바뀐 것은 **그의 말 중 무엇이 제출되는가**이지 **누가 쓴
  말인가**가 아니므로, `planning_or_routing` 금지와 무관하다.

## 3. 권한

- **P2 ceiling.** 허용 scope는 `INTERNAL_READ`(레지스트리·원장 조회, VALIDATED 메모리
  읽기)와 `INTERNAL_ANALYSIS`(자기 대화 모델 호출 — R7.2 triage 선례) 뿐이다.
- 명시적 금지 (front matter `unsupported_capabilities`가 계약이다): 모든 effect scope,
  도구/검색 호출, PermissionDecision 발행, approval 발행/소비, 메모리 승격, 계획/라우팅.
- 조사가 필요한 질문은 프론트데스크가 답하지 않는다 — `SUBMIT_TASK`로 번역해서
  파이프라인(그리고 specialist의 도구 권한)에 넘긴다.
- **v0.2 확장의 성격**: 능력을 "푼" 것이 아니라 열거에 항목을 더한 것이다. 세 조회는
  전부 읽기 전용이고, 채널이 이미 결정론 verb로 보여주던 것과 **같은 렌더러**를 쓴다.
  프론트데스크가 못 보던 것을 물으면 잡담으로 떨어져 "확인할게요" 같은 지킬 수 없는
  약속을 하게 되는데, 그 정직성 결함의 해법은 열거를 넓히는 것이지 접근을 여는 것이 아니다.
  경계 밖은 항상 남으므로, 못 하는 일은 못 한다고 답하는 규칙을 프롬프트가 함께 못박는다.

## 4. 세션 메모리

- 대화 상태는 R5 working memory의 **`frontdesk_session`** scope 후보로 저장한다. 이
  scope는 이 역할만 읽는다 — specialist/validator의 `readable_scopes`에 없으므로 대화
  맥락이 작업 컨텍스트를 오염시키지 않는다.
- 역방향도 닫힌다: 프론트데스크는 `task_working_memory`를 읽지 못한다. 작업 결과의
  전달은 레지스트리/원장 조회(`QUERY_RESULT`)로만 한다.
- 기존 retention 의미론 그대로: `expires_at` 필수, prune 대상. VALIDATED 승격은 별도
  R9 승인 (프론트데스크는 승격 불가).

## 5. Provider와 강등

- 자체 게이트 provider: `MVP_FRONTDESK_PROVIDER` — `MVP_VALIDATOR_PROVIDER`와 동일한
  체인 의미론(멤버별 per-machine 그랜트, 미인가 멤버 포함 체인은 전체 fail-closed,
  env var 단독으로는 아무것도 안 켜짐).
- 턴당 예산: `max_model_calls: 1`, 자체 토큰 allowance (R7.2 `TRIAGE_TOKEN_ALLOWANCE`
  선례). 대화는 호출 빈도가 높으므로 spend는 턴 단위로 기록된다.
- **강등은 차단이 아니다**: provider 장애/미인가 시 대화만 죽고 런타임은 산다.
  `FRONTDESK_DEGRADED` 감사 후 결정론 verb(`/tasks` `/history` `/result` `/cancel` +
  일반 텍스트 제출)로 안내한다 (R3 `SEARCH_DEGRADED` 선례).

## 6. Kill switch

- 프론트데스크의 모델 호출은 kill-switch 대상이다 (PAUSED/KILLED에서 대화 LLM 정지 —
  `INTERNAL_ANALYSIS`도 새 실행이다).
- 읽기 verb와 비상 콘솔은 기존 `kill_allows` 의미론대로 계속 응답한다 — 죽은 런타임의
  상태를 물어볼 수단은 항상 남는다.

## 7. 활성화

- `status: active` (D2 결정, 2026-07-25, F2 런타임 구현 PR에서 명시적으로 플립).
  활성화가 곧 실행은 아니다: 머신별 provider 그랜트(D3,
  `scripts/activate_safety_flag.py` + `MVP_FRONTDESK_PROVIDER`)가 없으면 런타임
  selection이 fail-closed로 거부하며, 레지스트리 활성 + 해시 일치를 selection이
  직접 재검증한다 (`frontdesk.select_frontdesk_provider`).
