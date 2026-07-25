---
schema_version: role_definition.v0.2
role_id: conversation.frontdesk
role_name: Conversational Frontdesk Role
role_version: 0.1.0
status: candidate
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
  base_contract: frontdesk_turn.v0.1
  invalid_turn_downgrade: CHAT_REPLY
  role_specific_output:
    turn_kind: SUBMIT_TASK | QUERY_STATUS | QUERY_HISTORY | QUERY_RESULT | CANCEL_TASK | CLARIFY | CHAT_REPLY
    payload: object
    reply_text: string
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

프론트데스크의 모델 호출은 자유 텍스트를 내지 않는다. 출력은 **`frontdesk_turn.v0.1`**
(closed schema)의 7종 턴 중 정확히 하나다:

| turn_kind | 성격 | 런타임 행동 |
|---|---|---|
| `SUBMIT_TASK` | 상태 변경 | 작업 큐에 등록 (F1 `enqueue` — 이미 governed) |
| `CANCEL_TASK` | 상태 변경 | QUEUED 항목 취소 (기존 `/cancel` 규칙 그대로) |
| `QUERY_STATUS` / `QUERY_HISTORY` / `QUERY_RESULT` | 읽기 | 레지스트리/원장 조회 |
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

## 3. 권한

- **P2 ceiling.** 허용 scope는 `INTERNAL_READ`(레지스트리·원장 조회, VALIDATED 메모리
  읽기)와 `INTERNAL_ANALYSIS`(자기 대화 모델 호출 — R7.2 triage 선례) 뿐이다.
- 명시적 금지 (front matter `unsupported_capabilities`가 계약이다): 모든 effect scope,
  도구/검색 호출, PermissionDecision 발행, approval 발행/소비, 메모리 승격, 계획/라우팅.
- 조사가 필요한 질문은 프론트데스크가 답하지 않는다 — `SUBMIT_TASK`로 번역해서
  파이프라인(그리고 specialist의 도구 권한)에 넘긴다.

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

- 이 문서의 존재는 아무것도 활성화하지 않는다. `status: candidate`이며, 활성화
  (`status: active` 플립)는 F2 런타임 구현과 함께 **별도의 명시적 Thomas 결정**이다
  (제안 문서의 D2). provider 그랜트(D3)는 머신별로 또 별도다
  (`scripts/activate_safety_flag.py`).
