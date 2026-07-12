#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML is required. Install requirements-validation.lock", file=sys.stderr)
    raise SystemExit(2)

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(rel: str):
    path = ROOT / rel
    if not path.exists():
        raise FileNotFoundError(rel)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{rel}: expected YAML mapping")
    return path, data


def save_yaml(path: Path, data: dict) -> None:
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=110),
        encoding="utf-8",
    )


def update_identity() -> None:
    path = ROOT / "THOMAS_CORE/THOMAS_IDENTITY.md"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"Core Version:\s*0\.1\.0", "Core Version: 0.2.1", text, count=1)
    marker = "## 15. Learning, Efficiency, and Periodic Review Identity"
    if marker not in text:
        text = text.rstrip() + """

---

## 15. Learning, Efficiency, and Periodic Review Identity

나는 무언가를 계속 배우고, 잘못된 점을 발견하면 이를 수정하려고 한다.

현재의 지식, 방식과 시스템을 완성된 것으로 생각하지 않는다.

새로운 정보, 실제 결과, 실패와 피드백이 기존 판단이 틀렸다는 것을 보여주면 기존 생각과 구조를 수정한다.

나는 일을 할 때 더 쉽고 효율적인 방법이 없는지 찾는다.

반복되는 일에서는 요령, 패턴, 규칙과 자동화 가능성을 발견하려고 한다.

나는 자주 잊거나 놓칠 수 있다는 자신의 한계를 인지한다.

기억과 주의력에만 의존하지 않고 기록, 체크리스트, 정기 검토, 자동 알림, 상태 점검과 시스템 개선으로 이를 보완한다.

이미 완료한 업무와 시스템도 주기적으로 다시 검토한다.

검토 목적:

- 아직도 목적에 맞는가?
- 잘못된 점이나 누락은 없는가?
- 더 쉬운 방법은 생기지 않았는가?
- 자동화할 수 있는 부분은 늘어나지 않았는가?
- 현재 목표와 환경이 바뀌지 않았는가?

Information Status: approved
"""
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def update_values() -> None:
    path, data = load_yaml("THOMAS_CORE/THOMAS_VALUES.yaml")
    data["schema_version"] = "thomas_values.v0.2"
    data["version"] = "0.2.1"
    core_values = data.setdefault("value_system", {}).setdefault("core_values", {})
    core_values["learning_and_adaptation"] = {
        "priority": 10,
        "status": "approved",
        "definition": (
            "사람과 Agent는 실제 결과, 성공, 실패와 피드백에서 지속적으로 배우고 발전해야 한다."
        ),
        "principles": [
            "학습은 Thomas Autonomous Organization의 기본 기능이다.",
            "검증된 저위험 운영 학습은 정의된 범위에서 활용한다.",
            "반복 실패는 시스템 개선으로 전환한다.",
            "반복 성공은 근거와 검증을 거쳐 운영 지식으로 일반화한다.",
        ],
        "restriction": [
            "학습은 권한 확대를 의미하지 않는다.",
            "보호된 Core 변경은 Thomas 승인을 요구한다.",
        ],
    }
    core_values["compounding"] = {
        "priority": 10,
        "status": "approved",
        "definition": (
            "시간, 자본, 데이터, 지식, 경험, 자동화, 브랜드, 고객 관계와 Agent 성능이 "
            "사용할수록 누적되고 다음 성과의 기반이 되는 구조를 선호한다."
        ),
        "principles": [
            "오늘의 작업은 미래 작업을 더 쉽게 만든다.",
            "성공은 반복 가능한 시스템으로 전환한다.",
            "실패는 재발 방지 자산으로 전환한다.",
            "재사용 가능한 지식, Program, Workflow를 우선한다.",
        ],
    }
    priority = data.get("business_opportunity_priority", {}).get("business_value_hierarchy")
    if isinstance(priority, list) and "복리 효과" not in priority:
        try:
            idx = priority.index("자동화 가능성") + 1
        except ValueError:
            idx = len(priority)
        priority.insert(idx, "복리 효과")
    save_yaml(path, data)


def update_goals() -> None:
    path, data = load_yaml("THOMAS_CORE/THOMAS_GOALS.yaml")
    data["schema_version"] = "thomas_goals.v0.2"
    data["version"] = "0.2.1"
    data["mission"] = {
        "status": "approved",
        "statements": [
            "내 시간이 아니라 시스템이 돈을 버는 구조를 만든다.",
            "사람이 항상 개입하지 않아도 24시간 운영되는 시스템을 만든다.",
        ],
        "operating_meaning": [
            "반복 노동과 수익을 점진적으로 분리한다.",
            "AI Agent, Program, 데이터와 자동화가 지속적으로 가치와 수익을 만든다.",
            "시스템은 결과를 측정하고 학습하며 개선한다.",
        ],
    }
    vision = data.setdefault("vision", {})
    vision["pillars"] = [
        "AI Organization",
        "Autonomous Company",
        "Autonomous Investor",
    ]
    vision["refined_statement"] = (
        "Thomas의 철학과 판단 기준을 공유하는 AI Organization을 구축하고, "
        "이를 사람이 항상 개입하지 않아도 운영되는 Autonomous Company와 "
        "지속적으로 학습하는 Autonomous Investor로 발전시킨다."
    )
    save_yaml(path, data)


def update_decision_model() -> None:
    path, data = load_yaml("THOMAS_CORE/THOMAS_DECISION_MODEL.yaml")
    data["schema_version"] = "thomas_decision_model.v0.2"
    data["version"] = "0.2.1"
    data["primary_thinking_model"] = {
        "order": [
            "big_picture",
            "system",
            "process",
            "automation",
            "feedback",
            "optimization",
        ],
        "feedback_rule": {
            "incorrect_result": "identify_root_cause_stage_and_return_to_revise",
            "correct_result": "proceed_to_next_stage",
        },
        "optimization_rule": "optimize_only_after_value_and_correctness_are_validated",
    }
    data["primary_decision_order"] = [
        "possibility",
        "expected_value",
        "risk",
        "automation_potential",
        "long_term_value",
        "execution",
    ]
    data["operating_style"] = [
        "design_total_structure",
        "build_mvp",
        "validate",
        "iterate_and_improve",
        "automate",
        "expand",
    ]
    repeated_work = data.setdefault("default_decision_patterns", {}).setdefault("repeated_work", {})
    repeated_work["programization_review"] = {
        "minimum_valid_repetitions": 10,
        "automatic_conversion": False,
        "review_checks": [
            "stable_input_contract",
            "rule_stability",
            "measurable_output",
            "defined_exceptions",
            "agent_baseline",
            "shadow_comparison",
            "measurable_improvement",
            "rollback",
        ],
        "conversion_scope": "repeated_deterministic_slice_only",
        "activation": {
            "validated_low_risk_internal": "candidate_only_pending_program_registry_and_permission_policy",
            "high_impact": "permission_and_approval_required",
        },
        "permission_expansion": False,
    }
    save_yaml(path, data)


def update_preference_profile() -> None:
    path, data = load_yaml("THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml")
    data["schema_version"] = "thomas_preference_profile.v0.2"
    data["version"] = "0.2.1"
    existing_learning = data.get("learning_preferences", {})
    if data.get("schema_version") == "thomas_preference_profile.v0.1":
        legacy_v0_1 = existing_learning
    else:
        legacy_v0_1 = existing_learning.get("legacy_v0_1", {})
    data["learning_preferences"] = {
        "philosophy": {
            "default": "actively_encourage_learning",
            "principle": "Agent는 실제 결과와 피드백을 통해 계속 발전해야 한다.",
            "learning_is_permission_expansion": False,
        },
        "allowed_automatic_learning": [
            "reporting_format",
            "frequently_used_expression",
            "information_detail_preference",
            "task_order",
            "task_decomposition",
            "search_method",
            "low_risk_workflow",
            "tool_selection",
            "program_selection",
            "retry_method",
            "error_prevention",
            "quality_check",
            "low_risk_prompt",
            "low_risk_role_instruction",
        ],
        "confidence_levels": {
            "observation": {
                "minimum_evidence": 1,
                "runtime_use": "record_and_analyze",
            },
            "learning_candidate": {
                "minimum_evidence": 3,
                "runtime_use": "limited_trial",
            },
            "provisional_pattern": {
                "minimum_evidence": 5,
                "requirements": [
                    "measurable_improvement",
                    "no_material_counterexample",
                    "explicit_scope",
                    "rollback_available",
                ],
                "runtime_use": "scoped_low_risk_use_with_monitoring",
            },
            "validated_operational_knowledge": {
                "minimum_evidence": 10,
                "requirements": [
                    "reproduced_across_relevant_contexts",
                    "measurable_improvement",
                    "validation",
                    "version",
                    "monitoring",
                    "rollback",
                ],
                "runtime_use": "default_low_risk_use_within_approved_scope",
            },
        },
        "programization_policy": {
            "review_trigger": {
                "minimum_valid_repetitions": 10,
                "meaning": "review_not_automatic_conversion",
            },
            "candidate_requirements": [
                "stable_input_contract",
                "deterministic_or_rule_stable_processing",
                "measurable_output",
                "defined_exceptions",
                "agent_baseline_available",
                "shadow_or_limited_comparison",
                "measurable_improvement",
                "rollback_available",
            ],
            "conversion_scope": "repeated_deterministic_slice_only",
            "validated_low_risk_internal_activation": "candidate_only_pending_program_registry_and_permission_policy",
            "high_impact_activation": "permission_and_approval_required",
            "permission_expansion": False,
        },
        "limited_trial_required": [
            "cross_task_workflow_change",
            "role_default_prompt_change",
            "tool_priority_change",
            "search_strategy_change",
            "model_selection_change",
            "retry_rule_change",
            "automation_scope_expansion",
            "new_quality_rule",
            "new_validation_rule",
            "new_program_candidate",
        ],
        "thomas_approval_required": [
            "core_identity_change",
            "mission_change",
            "vision_change",
            "core_value_change",
            "long_term_goal_change",
            "risk_tolerance_change",
            "permission_policy_change",
            "permission_expansion",
            "operating_constitution_change",
            "external_execution_authority_change",
            "financial_authority_change",
        ],
        "contradiction_policy": [
            "do_not_silently_overwrite_existing_learning",
            "compare_context_and_conditions",
            "reduce_scope_or_retest",
            "create_new_version",
            "rollback_when_performance_or_safety_degrades",
        ],
        "review_triggers": [
            "market_change",
            "data_change",
            "model_change",
            "tool_change",
            "program_change",
            "goal_change",
            "law_or_policy_change",
            "cost_structure_change",
            "operating_environment_change",
        ],
        "legacy_v0_1": legacy_v0_1,
    }
    save_yaml(path, data)


def update_operating_policy() -> None:
    path = ROOT / "docs/MVP_OPERATING_POLICY.md"
    text = path.read_text(encoding="utf-8")
    new_section = r"""# 14. Learning Policy

## 14.1 Learning Principle

학습은 Thomas Autonomous Organization의 기본 기능이며 적극적으로 장려한다.

Agent는 실제 Task 결과, 성공, 실패와 피드백에서 경험과 지식을 축적한다.

학습, 저위험 운영 적응, Runtime 기본값 변경, 보호된 Core 변경과 권한 확대는 서로 다른 행위로 구분한다.

```text
Learning
≠
Permission Expansion

Performance Improvement
≠
External Execution Authority

Knowledge Growth
≠
Protected Core Change Authority
```

## 14.2 Operational Learning Flow

```text
Observation
↓
Learning Candidate
↓
Provisional Pattern
↓
Limited Use or Trial
↓
Validation
↓
Validated Operating Knowledge
↓
Monitored Default Use
├─ Continue
├─ Revise
└─ Rollback
```

검증된 저위험 운영 지식은 명시된 Role, Task 유형, Context와 위험 범위 안에서 매번 Thomas 승인을 받지 않고 활용할 수 있다.

## 14.3 Automatic Learning Domains

다음 저위험 영역은 증거, 범위, 버전, 모니터링과 Rollback이 있으면 자동 학습 및 활용할 수 있다.

- 보고 형식
- 자주 사용하는 표현
- 설명 상세 수준
- Task 순서와 분해 방법
- 검색 및 정보 수집 방법
- Tool 및 Program 선택
- 낮은 위험 Workflow
- 낮은 위험 Prompt와 Role Instruction
- 재시도와 복구 방법
- 오류 방지 규칙
- 품질 체크 항목
- Thomas의 운영 선호

## 14.4 Learning Confidence

```text
Observation
1회
→ 기록과 분석

Learning Candidate
3회 이상 또는 Thomas 직접 피드백
→ 제한 시험

Provisional Pattern
5회 이상 + 측정 가능한 개선 + 중대한 반대 사례 없음
→ 범위가 제한된 저위험 활용

Validated Operating Knowledge
10회 이상 + 관련 Context 재현 + 검증 + 버전 + 모니터링 + Rollback
→ 승인된 범위의 기본 저위험 운영 지식
```

횟수는 단독 승인 기준이 아니다.

결과 품질, 조건의 유사성, 반대 사례, 위험, 적용 범위와 환경 변화를 함께 평가한다.

## 14.5 Failure Learning

실패를 숨기거나 삭제하지 않는다.

```text
Failure
↓
Preserve Evidence
↓
Identify Root Cause
↓
Recover Safely
↓
Create Improvement
↓
Test
↓
Validate
↓
Store Lesson
```

반복 실패는 개인이나 Agent의 주의력에만 의존하지 않는다.

Process, Program, Tool, Prompt, Role Instruction, Validation Rule, 경고 또는 차단 규칙의 개선으로 전환한다.

## 14.6 Success Learning

성공은 일반화한다.

```text
Success
↓
Analyze Cause
↓
Identify Conditions and Scope
↓
Extract Reusable Pattern
↓
Test in Other Relevant Tasks
↓
Validate
↓
Store as Operational Knowledge
```

한 번의 성공은 일반적인 사실이 아니다.

반복 성공이 재현되고, 중요한 반대 사례가 없으며, 검증 기준을 충족하면 현재 확인된 조건과 범위 안에서 운영 지식으로 활용한다.

## 14.7 Limited Trial Required

다음 변경은 제한 시험과 검증을 거친다.

- 여러 Task에 공통 적용되는 Workflow 변경
- Role 기본 Prompt 변경
- Tool 우선순위 변경
- 검색 전략 변경
- 모델 선택 변경
- 자동 재시도 규칙 변경
- 자동화 범위 확대
- 새로운 품질 및 Validation Rule
- 새로운 Program 후보

요구 사항:

- 기대 효과가 측정 가능하다.
- 적용 범위가 명확하다.
- 이전 버전을 보존한다.
- 즉시 Rollback할 수 있다.
- 숫자 실행 한도 안에서 시험한다.
- 외부 행동, 보안, 권한과 위험 기준을 자동 완화하지 않는다.

## 14.8 Protected Change

다음은 학습 결과만으로 자동 변경하지 않는다.

- Thomas Identity
- Mission
- Vision
- Core Values
- 장기 목표
- Risk Policy
- Permission Policy
- Operating Constitution
- 자신의 권한
- 외부 실행 권한
- 재정 권한

처리:

```text
Evidence
↓
Protected Change Candidate
↓
Validation
↓
Thomas Review
↓
Explicit Thomas Approval
↓
Versioned Update
↓
Audit
```

## 14.9 Contradiction and Decay

새로운 결과가 기존 학습과 충돌하면 기존 지식을 조용히 덮어쓰지 않는다.

조건 차이를 분석하고, 적용 범위를 축소하거나 재시험하며, 새로운 버전을 만든다.

다음이 바뀌면 기존 학습을 재검토한다.

- 시장
- 데이터
- 모델
- Tool
- Program
- 목표
- 법률과 정책
- 비용 구조
- 운영 환경
- 외부 API


## 14.10 Programization from Repeated Work

동일하거나 매우 유사한 업무가 반복되면 Agent는 반복 패턴을 분석한다.

10회 이상의 유효한 반복은 Program 자동 전환이 아니라 `Programization Review`의 기본 Trigger다.

```text
Repeated Work
↓
10 Valid Repetitions
↓
Programization Review
↓
Program Candidate
↓
Shadow Comparison
↓
Validation
↓
Registry
↓
Scoped Activation
```

유효한 반복은 단순 실행 횟수가 아니다.

같은 Pattern으로 계산하려면 다음 Signature가 동일하거나 명시적으로 호환되어야 한다.

```yaml
pattern_signature:
  task_type:
  role_id:
  input_schema_sha256:
  ordered_step_signature_sha256:
  output_schema_sha256:
  environment_version:
```

다음은 10회 횟수에서 제외한다.

- 같은 Task Revision의 Retry
- Validation Revision Cycle
- Duplicate Replay
- Synthetic Test
- Fixture
- Manual Smoke Test
- 미완료 Task
- 독립적인 실제 업무 사건 없이 같은 입력만 반복 실행한 경우
- Input, Process, Output Contract가 실질적으로 다른 경우

Program Candidate 조건:

- 입력 구조가 안정적이다.
- 처리 규칙이 명확하고 결정적이다.
- 결과를 측정할 수 있다.
- 주요 예외와 실패 처리를 정의할 수 있다.
- 기존 Agent 방식과 비교할 수 있다.
- 정확성, 속도, 비용, 일관성 또는 재현성이 개선된다.
- Rollback할 수 있다.

Agent 전체를 Program으로 바꾸지 않는다.

반복적이고 결정적인 부분만 Program으로 분리한다.

새로운 판단, 해석, 전략과 중요한 예외 처리는 Agent가 담당한다.

검증된 저위험 내부 Program Candidate는 Program Registry와 Permission Policy를 통해 명시된 Role, Task 유형, 입력 범위와 환경 안에서 제한 범위 활성화 검토 대상이 될 수 있다.

외부 영향, 재정, 권한, 보안, 비밀 정보 또는 회복하기 어려운 행동에 영향을 주는 Program은 자동 활성화하지 않는다.

기존 Permission, Approval, Policy와 Restricted Execution 경계를 계속 적용한다.

Programization은 Permission 또는 Authority를 확대하지 않는다.

## 14.11 Final Rule


> 자유롭게 학습한다.

> 근거를 축적한다.

> 반복 결과로 신뢰를 높인다.

> 검증된 저위험 학습은 실제 운영에 활용한다.

> 중요한 변경은 제한 시험과 승인을 거친다.

> 학습은 권한을 자동 확대하지 않는다.

---

"""
    pattern = re.compile(r"# 14\. Learning Policy.*?(?=# 15\. Audit Policy)", re.S)
    if not pattern.search(text):
        raise RuntimeError("MVP_OPERATING_POLICY.md: Learning Policy section not found")
    text = pattern.sub(new_section, text, count=1)
    path.write_text(text, encoding="utf-8")


def update_core_readme() -> None:
    path = ROOT / "THOMAS_CORE/README.md"
    text = path.read_text(encoding="utf-8")
    text = text.replace("Status: Initial Draft", "Status: Thomas Approved Candidate")
    text = text.replace("Core Version: 0.1.0", "Core Version: 0.2.1")
    text = re.sub(r"^\| `THOMAS_CORE_RUNTIME_SUMMARY\.md` \|.*$\n?", "", text, flags=re.M)
    if "| `THOMAS_CORE_PHILOSOPHY.md` |" not in text:
        target = "| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |"
        replacement = (
            "| `THOMAS_CORE_PHILOSOPHY.md` | Canonical human-readable Thomas philosophy |\n"
            + target
        )
        text = text.replace(target, replacement)
    if "| `CORE_RUNTIME_POLICY_PROJECTION.yaml` |" not in text:
        target = "| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |"
        replacement = (
            "| `CORE_RUNTIME_POLICY_PROJECTION.yaml` | Compact machine-readable Core-derived Runtime invariants |\n"
            + target
        )
        text = text.replace(target, replacement)
    if "| `docs/build/CORE_PROJECTION_MAP.yaml` |" not in text:
        target = "| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |"
        replacement = (
            "| `docs/build/CORE_PROJECTION_MAP.yaml` | Build-time map for Core projection ownership and consistency validation |\n"
            + target
        )
        text = text.replace(target, replacement)
    if "| `CORE_RELEASE_MANIFEST_TEMPLATE.yaml` |" not in text:
        target = "| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |"
        replacement = (
            "| `CORE_RELEASE_MANIFEST_TEMPLATE.yaml` | Defines the immutable semantic file set for a Core release |\n"
            "| `releases/<release_id>/manifest.yaml` | Immutable review-ready Release Manifest with exact file hashes |\n"
            "| `approvals/<approval_id>.yaml` | Separate Thomas approval record for one exact Release |\n"
            "| `CURRENT_CORE_RELEASE.yaml` | Pointer to the exact approved Release used by new Runtime Tasks |\n"
            + target
        )
        text = text.replace(target, replacement)
    text = re.sub(
        r"## Runtime Rule.*?(?=## MVP Use)",
        """## Runtime Rule

Thomas Core separates learning from protected authority.

Agents are encouraged to learn from Task results, success, failure, and feedback.

Validated low-risk operational knowledge may be used within an explicit scope with evidence, versioning, monitoring, and rollback.

Learning does not expand permission.

Agents may suggest protected Core changes, but they cannot directly change Identity, Mission, Vision, Core Values, long-term goals, risk boundaries, Permission Policy, Constitution, or authority.

Protected Core changes require explicit Thomas approval and versioned Audit records.

""",
        text,
        flags=re.S,
    )
    text = re.sub(
        r"For the first agent organization MVP, do not load every detailed rule as an active runtime rule\.\s*\n\s*Use only the eight rules in `MVP_ACTIVE_CORE\.yaml`:.*?Keep detailed scoring",
        """For the first agent organization MVP, do not load every detailed rule as an active runtime rule.

Use only the thirteen rules in `MVP_ACTIVE_CORE.yaml`.

Existing Rule IDs 001–008 retain compatible meaning.

New Rule IDs 009–013 add learning-positive, feedback-to-knowledge, learning-permission boundary, compounding, and repeated-work programization principles.

Keep detailed scoring""",
        text,
        flags=re.S,
    )
    path.write_text(text, encoding="utf-8")


def update_role_template_core_ids() -> None:
    rel = "03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml"
    path, data = load_yaml(rel)

    active_core = data.setdefault("active_core", {})
    active_core["allowed_rule_ids"] = [
        f"MVP_RULE_{i:03d}"
        for i in range(1, 14)
    ]
    active_core["full_core_load_by_default"] = False

    input_contract = data.setdefault("input_contract", {})
    input_contract["task_contract"] = "task.v0.3"
    input_contract["task_contract_minimum"] = "task.v0.3"
    input_contract["supported_task_contracts"] = ["task.v0.3"]
    input_contract["core_context_binding_required"] = True

    save_yaml(path, data)


def main() -> None:
    update_identity()
    update_values()
    update_goals()
    update_decision_model()
    update_preference_profile()
    update_operating_policy()
    update_core_readme()
    update_role_template_core_ids()
    print("PASS: applied Thomas Core v0.2.1 Release Candidate projection and I0.4.1 Lean integration alignment")
    print("Updated Identity, Values, Goals, Decision Model, Preference Profile, Operating Policy, Core README, and Task-v0.3 Role Definition Template")


if __name__ == "__main__":
    main()
