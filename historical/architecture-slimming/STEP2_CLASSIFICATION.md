# Thomas Agent Step 2 — Repository Artifact Classification

- Document ID: `THOMAS_AGENT_STEP2_ARTIFACT_CLASSIFICATION`
- Version: `0.1.0-draft`
- Status: `STEP_2_FIRST_PASS`
- Repository: `thomasryu1992-commits/thomas_agent`
- Baseline commit: `f638a3aba522d112e9985e5189edd4e013f44835`
- Baseline phase: `I0.5.5`
- Purpose: 현재 Repository의 주요 Artifact를 Active / Generated / Deferred / Historical / Duplicate Candidate로 분류하고 Step 3의 Single Source of Truth 확정 입력을 만든다.

---

## 1. Step 2 원칙

Step 2에서는 대규모 삭제, 이동, 이름 변경, Runtime Behavior 변경을 하지 않는다.

분류 목적은 파일 수를 줄이는 것이 아니라 다음을 식별하는 것이다.

1. 현재 실제 규칙의 Source of Truth
2. 현재 Runtime을 구현하는 코드
3. 재생성 가능한 산출물
4. 미래 단계에서만 필요한 설계
5. 과거 또는 참고용 기록
6. 의미가 중복되어 Reference로 축소해야 할 후보

분류 값:

```text
ACTIVE_NORMATIVE
ACTIVE_IMPLEMENTATION
GENERATED
DEFERRED
HISTORICAL
DUPLICATE_CANDIDATE
```

---

## 2. Repository Level First-Pass Classification

| Repository area | Primary classification | Secondary flag | Current decision |
|---|---|---|---|
| `THOMAS_CORE/MVP_ACTIVE_CORE.yaml` | ACTIVE_NORMATIVE | KEEP | Thomas Identity / Values / Goals의 현재 기준으로 유지 |
| `THOMAS_CORE/CURRENT_CORE_RELEASE.yaml` | ACTIVE_NORMATIVE | KEEP | 활성 Core Release pointer로 유지 |
| `THOMAS_CORE/releases/*/manifest.yaml` | GENERATED | KEEP_AS_EVIDENCE | Immutable release evidence로 보존하되 Active rule source로 사용하지 않음 |
| `THOMAS_CORE/releases/*` copied source/toolchain | GENERATED | REDUCE_LATER | Git tag/commit/hash + Release artifact 전환 후보 |
| `docs/thomas-autonomous-organization-architecture-v0.1.md` | ACTIVE_NORMATIVE | CONSOLIDATE_LATER | 장기 구조 원칙은 유지하되 System Constitution과 역할 경계를 재정리 |
| `docs/MVP_OPERATING_POLICY.md` | ACTIVE_NORMATIVE | SOURCE_REVIEW | Governance Policy의 핵심 입력. Permission/Approval 중복 정리 필요 |
| `docs/runtime-contracts/RUNTIME_CONTRACT_PRECEDENCE_ADDENDUM_v0.4.md` | ACTIVE_NORMATIVE | DUPLICATE_CANDIDATE | 현재 충돌 해결용이지만 Slim 구조에서는 Authority Map으로 축소 후보 |
| `docs/runtime-contracts/CORE_RELEASE_LIFECYCLE_V0.3.md` | ACTIVE_NORMATIVE | KEEP | Core 변경/승인/활성/롤백 경계 유지 |
| `docs/runtime-contracts/CORE_CONTEXT_BINDING_V0.3.md` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Task와 Core lineage 경계 유지, 중복 필드 검토 |
| `docs/runtime-contracts/TASK_CONTRACT_V0.3.md` | ACTIVE_NORMATIVE | KEEP | MVP Runtime의 핵심 Record |
| `docs/runtime-contracts/TASK_STATE_MACHINE_V0.1.yaml` | ACTIVE_NORMATIVE | KEEP | Task lifecycle source로 유지 |
| `docs/runtime-contracts/AUTHORITY_AND_PERMISSION_MODEL.md` | ACTIVE_NORMATIVE | MERGE_TARGET | 중앙 Governance Policy로 통합할 핵심 입력 |
| `docs/runtime-contracts/PERMISSION_DECISION_CONTRACT_V0.3.md` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | 독립 Decision Record 필요성 유지, Rule 정의는 중앙 Policy로 이동 |
| `docs/runtime-contracts/APPROVAL_CONTRACT_V0.1.md` | ACTIVE_NORMATIVE | KEEP_CONDITIONAL | 외부 효과/고위험 행동 도입 전까지 최소 구조만 유지 |
| `docs/runtime-contracts/ACTION_FINGERPRINT_POLICY_V0.1.md` | DUPLICATE_CANDIDATE | MERGE_INTO_GOVERNANCE | 독립 최상위 정책보다 Governance 내부 Action Identity Rule 후보 |
| `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md` | DUPLICATE_CANDIDATE | MERGE | Human-readable Governance 문서로 흡수 후보 |
| `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml` | ACTIVE_NORMATIVE | MERGE_TARGET | 미래 `GOVERNANCE_POLICY.yaml`의 직접 기반 후보 |
| `AGENT_OUTPUT_CONTRACT_V0.2.md` | ACTIVE_NORMATIVE | KEEP | 핵심 Runtime Record |
| `EXECUTION_BUDGET_SCHEMA.yaml` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Budget 공통 규칙으로 유지 |
| `03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md` | ACTIVE_NORMATIVE | KEEP | Prime 책임과 금지 경계 유지 |
| `03_ROLE_CONTRACTS/MVP_DYNAMIC_ROLE_CONTRACT.md` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | 공통 Role 규칙 유지, Governance Rule 복제 제거 |
| `03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Role Definition schema/template 유지 |
| `03_ROLE_CONTRACTS/ROLE_ASSIGNMENT_CONTRACT.md` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Task-specific scope 유지, Permission Rule 복제 제거 |
| `03_ROLE_CONTRACTS/ROLES/ACTIVE/*` | ACTIVE_NORMATIVE | KEEP | Active Role capability source |
| `03_ROLE_CONTRACTS/ROLES/CANDIDATES/*` | DEFERRED | KEEP_AS_CANDIDATES | 실제 반복 검증 전까지 Runtime active 구조에서 분리 |
| `03_ROLE_CONTRACTS/ROLE_REGISTRY.yaml` | ACTIVE_NORMATIVE | HIGH_DUPLICATE_CANDIDATE | Index 역할만 남기고 Capability/Permission/Restriction/Promotion 중복 제거 |
| `05_REGISTRIES/PROGRAM_REGISTRY.yaml` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Program index 유지. Governance 세부 규칙은 중앙 Policy 참조로 전환 |
| `05_REGISTRIES/TOOL_REGISTRY.yaml` | ACTIVE_NORMATIVE | KEEP_SIMPLIFY | Tool index 유지. Governance 세부 규칙은 중앙 Policy 참조로 전환 |
| `05_REGISTRIES/I0_4_RUNTIME_CONTRACT_SET_INDEX.yaml` | HISTORICAL | MOVE_TO_HISTORY | I0.4 frozen review foundation 기록 |
| `runtime/read_only_kernel/*` | ACTIVE_IMPLEMENTATION | KEEP_REFACTOR | 현재 유일한 실제 Kernel 구현. Loader/Policy/Router/Worker/Validation/Audit 분리 후보 |
| `runtime/read_only_entry/*` | DEFERRED | FREEZE | I0.5.2~I0.5.5 미래 Runtime-authoritative entry 설계. 현재 Active Runtime에서 분리 |
| `runtime/protected_governance_state/*` | DEFERRED | FREEZE | Synthetic-only SQLite/CAS/Recovery 후보. 실제 필요 시점까지 동결 |
| `schemas/task*`, `core_context_binding*`, `role_assignment*`, `agent_output*`, `validation_result*`, `audit_event*` | ACTIVE_NORMATIVE | KEEP | 핵심 Runtime Data Boundary schema |
| Permission/Approval 관련 schema | ACTIVE_NORMATIVE | CONSOLIDATE | 중앙 Governance Decision/Approval Record 기준으로 축소 |
| Tool/Program request schema | DEFERRED | ACTIVATE_WITH_CAPABILITY | 실제 Tool/Program Runtime 도입 시 Active 전환 |
| Executor/Readiness/Monitoring/Alert/Health/Clock/Kill Switch/Supervisor/Scheduler/Sandbox schema | DEFERRED | MOVE_AS_FAMILY | 미래 운영 Requirement 계열로 묶음 |
| I0.5.1~I0.5.5 Entry Authorization/CAS/Recovery/Integration schema | DEFERRED | MOVE_AS_FAMILY | Runtime-authoritative entry 시점까지 동결 |
| `examples/` core Task/Role/Output examples | GENERATED | KEEP_MINIMAL | Canonical happy path와 필수 negative example만 유지 |
| `examples/` review-only executor/operations/entry examples | DEFERRED | REDUCE_LATER | 해당 미래 설계와 함께 이동 |
| `tests/fixtures/` core Runtime fixtures | GENERATED | KEEP | Active contract tests에 필요한 fixtures |
| `tests/fixtures/` phase-specific I0.4/I0.5.x mutation fixtures | DEFERRED | CONSOLIDATE_LATER | 공통 mutation harness로 통합 후보 |
| `scripts/validate_thomas_core.py` | ACTIVE_IMPLEMENTATION | KEEP | Core 검증 도구 |
| Task/Role/Core/Runtime 핵심 validators | ACTIVE_IMPLEMENTATION | KEEP_CONSOLIDATE | Domain 기반 공통 harness로 통합 |
| Executor/Operations/I0.4/I0.5.1~.5 phase validators | DEFERRED | REMOVE_FROM_ACTIVE_GATE | 미래 설계 검증 suite로 분리 |
| `scripts/run_repository_release_gate.py` | ACTIVE_IMPLEMENTATION | HIGH_REFACTOR_CANDIDATE | Phase 기반 직렬 Gate를 Domain 기반 Active Gate + Deferred Gate로 분리 |
| `build/release_gate/*` | GENERATED | DO_NOT_TREAT_AS_SOURCE | 재생성 가능한 evidence. 로컬 절대 경로 제거 필요 |
| `docs/README.md` | GENERATED | HIGH_DUPLICATE_CANDIDATE | Document map/index로만 유지. 상태/로드맵/권위 규칙 Source 역할 제거 |
| `docs/thomas-twin-core-architecture-v0.1.md` | HISTORICAL | KEEP_REFERENCE | Legacy/historical reference로 명확히 격리 |
| `.github/workflows/*` | ACTIVE_IMPLEMENTATION | KEEP_SIMPLIFY | Active Gate 중심 CI 유지, Deferred suite는 별도 optional job 후보 |

---

## 3. High-Priority Source-of-Truth Conflicts

### Conflict 1. Role Definition vs Role Registry

현재 Role Registry는 `router_source_of_truth: true`와 `role_definition_is_capability_source_of_truth: true`를 함께 선언한다.

그러나 Registry 안에 다시 다음 정보를 복제한다.

- capabilities
- capability hash
- permission ceiling
- restrictions
- validation default
- promotion requirements

판정:

```yaml
conflict_id: SOT_ROLE_001
severity: HIGH
selected_owner:
  role_capability: Role Definition
  role_runtime_status: Role Registry
action:
  - remove duplicated capability and permission content from registry
  - keep role_id/version/status/routable/path/hash only
```

### Conflict 2. Permission and Approval Rules Are Distributed

다음 Artifact에 동일 또는 유사한 권한 규칙이 분산된다.

- `MVP_OPERATING_POLICY.md`
- `AUTHORITY_AND_PERMISSION_MODEL.md`
- `PERMISSION_DECISION_CONTRACT_V0.3.md`
- `APPROVAL_CONTRACT_V0.1.md`
- `ACTION_FINGERPRINT_POLICY_V0.1.md`
- `THOMAS_PERMISSION_APPROVAL_OPERATING_PRINCIPLES_V0.1.md`
- `THOMAS_PERMISSION_APPROVAL_OPERATING_POLICY_V0.1.yaml`
- Role Registry / Role Assignment / Runtime Kernel checks

판정:

```yaml
conflict_id: SOT_GOV_001
severity: CRITICAL_ARCHITECTURE
selected_owner: future governance/GOVERNANCE_POLICY.yaml
record_contracts:
  - PermissionDecision
  - Approval
action:
  - move rule definitions into central policy
  - keep record schemas separate only where lifecycle requires
  - replace copied rule text with rule references
```

### Conflict 3. Document Map Has Become a Status Authority

`docs/README.md`는 문서 Index 역할을 넘어 Active status, phase roadmap, implementation state, deferred list, change rules까지 소유한다.

일부 구간은 아직 “Before I0.5 / Next I0.5”를 표시하면서 같은 문서 아래에 I0.5.5까지 기록되어 있다.

판정:

```yaml
conflict_id: SOT_STATUS_001
severity: MEDIUM
selected_owner:
  document_index: docs/README.md
  architecture_status: dedicated architecture status file or generated manifest
action:
  - remove normative policy from document map
  - generate current phase/status from one status source
```

### Conflict 4. Runtime Component Identity Is Repeated in Code

Planner, Authorization, Integration Candidate가 Kernel ID/version, component IDs, expected output schemas, SHA validation logic을 반복한다.

판정:

```yaml
conflict_id: SOT_COMPONENT_001
severity: MEDIUM
selected_owner: runtime/component_catalog.py or machine-readable component registry
action:
  - centralize immutable component metadata
  - move generic hash validation to shared integrity module
```

### Conflict 5. Every Phase Owns Its Own Boundary, Schema, Validator, Fixture, Gate

I0.5.1~I0.5.5는 각각 Contract, Boundary, Schema, Builder, Validator, Mutation Fixture, Release Gate entry를 생성한다.

판정:

```yaml
conflict_id: SOT_PHASE_001
severity: HIGH
selected_owner:
  active_runtime_rules: domain contracts and governance policy
  future_entry_design: deferred runtime-entry design package
action:
  - stop phase-specific artifact fan-out
  - consolidate common validator harness
  - keep phase history as review evidence, not active architecture
```

---

## 4. Active → Deferred Candidate Families

### Family D1. Executor and Pre-execution

- Executor Registry Design
- Executor Readiness Review
- Disabled Restricted Execution Service
- Hot-Path Revalidation
- Approval Consumption Preview
- Executor Candidate Intake
- Executor Candidate Intake Review

Decision: `DEFERRED_EXECUTOR_REQUIREMENTS`

Reason: 실제 Executor 또는 external effect가 아직 Active MVP 책임이 아니다.

### Family D2. Operations and Control

- Monitoring Snapshot
- Alert Event
- Health Snapshot
- Clock Sync Evidence
- Kill Switch State
- Kill Switch Command Review
- Control Channel Identity Binding
- Control Channel Command Envelope Review
- Disabled Process Supervisor
- Disabled Scheduler
- Monitoring/Alert Threshold Policy
- Threshold Evaluation
- Local Reversible Sandbox Test Plan/Review

Decision: `DEFERRED_OPERATIONS_REQUIREMENTS`

Reason: 실제 장기 실행 Runtime, 서버 운영, 외부 제어 채널 활성 전까지 요구사항으로 보존 가능하다.

### Family D3. Runtime-authoritative Entry

- I0.5.1 Runtime Promotion Readiness
- I0.5.2 Entry Plan / Disabled Adapter
- I0.5.3 Exact Authorization / At-most-once Transition
- I0.5.4 Protected Governance State / Durable CAS / Recovery
- I0.5.5 Disabled Integration Candidate
- associated runtime code, schemas, examples, fixtures, validators

Decision: `DEFERRED_RUNTIME_ENTRY_DESIGN`

Reason: 현재 구현은 항상 실제 Kernel invocation 전에 차단되며, Active Runtime value loop에 직접 기여하지 않는다.

---

## 5. Active → Historical Candidate Families

### Family H1. Legacy Integrated Architecture

- `docs/thomas-twin-core-architecture-v0.1.md`

Decision: `HISTORICAL_REFERENCE`

### Family H2. I0.4 Frozen Consolidation Evidence

- I0.4 Contract Set Index
- I0.4 Consolidation Checkpoint
- I0.4 Consolidation Boundary
- associated phase-specific review evidence

Decision: `HISTORICAL_PHASE_EVIDENCE`

### Family H3. Old Release Copies

Immutable release manifests and hashes remain valuable. Full copied source/toolchain snapshots should be reviewed for migration to GitHub tag/commit + Release artifact storage.

Decision: `GENERATED_RELEASE_EVIDENCE`, not Active Normative.

---

## 6. Must-Preserve Safety Boundaries

Slimming 과정에서 다음은 제거하면 안 된다.

1. Thomas is the final approval authority.
2. Thomas Core cannot be silently modified or activated.
3. Runtime cannot expand its own authority.
4. Role, Program, and Tool cannot grant permission to themselves.
5. External, financial, public, destructive, or irreversible effects require explicit policy and approval.
6. Documentation, readiness evidence, candidate status, or passing tests never grant Runtime permission by themselves.
7. Validation remains separate from execution and cannot mutate the subject it validates.
8. Audit remains append-only or correction-by-new-event.
9. Secrets must not be embedded in public records, hashes, logs, examples, or generated evidence.
10. Missing, stale, ambiguous, or inconsistent authority data fails closed.
11. Deferred components remain disabled until their explicit activation criteria are satisfied.
12. Architecture Slimming must not silently enable model, Tool, Program, network, filesystem write, external action, or Executor handoff.

---

## 7. Step 2 First-Pass Conclusion

현재 Repository의 가장 큰 두께는 기능 구현 자체보다 다음 세 계열에서 발생한다.

```text
1. Distributed Governance Rules
2. Review-only / Disabled Future Runtime Families
3. Phase-specific Validation and Evidence Fan-out
```

가장 먼저 정리할 대상은 파일 삭제가 아니라 Source of Truth 충돌이다.

우선순위:

```text
P1. Permission / Approval / Effect rules → one Governance Policy
P2. Role Registry → index-only structure
P3. I0.5.1~I0.5.5 → Deferred Runtime Entry family
P4. Executor / Operations / Control → Deferred requirements families
P5. Phase validators → shared domain contract harness
P6. docs/README → generated document map only
P7. Release snapshots/evidence → generated artifact boundary
```

---

## 8. Step 2 Acceptance Status

```yaml
step_2_status:
  repository_family_inventory_created: true
  active_normative_candidates_identified: true
  active_implementation_candidates_identified: true
  generated_candidates_identified: true
  deferred_candidates_identified: true
  historical_candidates_identified: true
  duplicate_candidates_identified: true
  source_of_truth_conflicts_identified: true
  safety_boundaries_to_preserve_identified: true
  physical_moves_performed: false
  runtime_behavior_changed: false
  next_step_ready: true
```

---

## 9. Step 3 Input

Step 3에서는 다음 Source of Truth를 최종 확정한다.

```text
Identity
→ THOMAS_CORE/MVP_ACTIVE_CORE.yaml

System Constitution
→ one normative constitution document

Governance
→ one GOVERNANCE_POLICY.yaml

Task
→ one canonical Task Contract + State Machine

Role Capability
→ Role Definition

Role Status
→ slim Role Registry

Program Status
→ slim Program Registry

Tool Status
→ slim Tool Registry

Runtime Behavior
→ Runtime Kernel modules

Validation
→ one shared validation framework

Memory
→ one memory policy and runtime boundary

Audit
→ one canonical Audit Contract
```

Step 3의 핵심 질문은 다음 하나다.

> 각 개념의 최종 소유자는 정확히 어느 파일인가?
