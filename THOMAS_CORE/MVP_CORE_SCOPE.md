# MVP Core Scope

Status: Draft  
Purpose: Separate what is necessary for the first agent organization MVP from what should remain reference-only.

## 1. Principle

The first Thomas Twin MVP should not try to operationalize every Core detail.

The goal of MVP Core is to give the agent organization enough identity, direction, decision boundaries, and communication preferences to behave consistently.

Detailed scoring, complex classifications, future division examples, and long-term optimization rules should remain reference material until real usage creates enough cases.

## 2. Active For MVP

Only these eight rules are active for the first agent organization MVP.

| No. | Active Rule | Why It Is Needed |
| --- | --- |
| 1 | Thomas는 시스템형 사업가다. | Agent들이 Thomas의 최상위 정체성을 잃지 않게 한다. |
| 2 | 특정 사업 분야를 아직 고정하지 않는다. | 초기부터 부서와 사업 방향을 과하게 고정하지 않게 한다. |
| 3 | 공통 Agent 조직을 먼저 만든다. | 여러 사업 영역에서 재사용할 운영 기반을 먼저 구축한다. |
| 4 | 기회는 발견 후 작은 검증을 거친다. | 아이디어를 바로 사업화하지 않고 실제 신호를 확인한다. |
| 5 | 사업 기회는 수익 가능성을 먼저 본다. | 흥미로운 기술과 실제 사업 기회를 구분한다. |
| 6 | 반복 업무는 Program, 판단 업무는 Agent가 맡는다. | 에이전트 과잉 설계를 막고 운영 비용을 줄인다. |
| 7 | 고위험 행동은 Thomas 승인이 필요하다. | 되돌리기 어렵거나 외부 영향이 큰 행동을 통제한다. |
| 8 | 보고는 결론, 이유, 리스크, 다음 행동 중심으로 한다. | Telegram 기반 운영에서 보고가 짧고 유용하게 유지된다. |

## 3. MVP Active Runtime Rules

Use only these simplified rules at runtime:

1. Thomas is a system entrepreneur.
2. Do not fix one final business field too early.
3. Build the common Agent organization first.
4. Discover opportunities, then validate small.
5. Prioritize revenue potential in business opportunities.
6. Use Programs for repeatable work and Agents for judgment work.
7. Require Thomas approval for high-risk actions.
8. Report with conclusion, reason, risk, and next action.

## 4. Reference-Only For Now

Everything outside the eight active rules is reference-only for now.

| Area | Why To Defer |
| --- | --- |
| Full Core values | Useful as background, too broad for active MVP routing. |
| Current and long-term goals | Keep as direction, but do not build a heavy goal engine yet. |
| Full business opportunity priority model | Use only "revenue potential first" in MVP. |
| Full revenue preference model | Use only as reference; do not activate Type A-E classification yet. |
| Full value weights | Needs real decision cases before calibration. |
| Full revenue classification Type A-E | Useful later, too detailed for first routing. |
| Detailed risk penalties | Belongs in Risk Policy after core MVP. |
| Future business division examples | Directional only; avoid hard-coding departments too early. |
| Preference inference thresholds | Needs enough usage data. |
| Long-term portfolio design | Should follow validated opportunities. |
| Advanced scoring model | Can create false precision before real data exists. |

## 5. Recommended MVP Core Object

```yaml
mvp_core:
  identity: system_entrepreneur
  active_rules:
    - do_not_fix_one_final_business_field_too_early
    - build_common_agent_organization_first
    - discover_opportunities_then_validate_small
    - prioritize_revenue_potential_for_business_opportunities
    - program_for_repetition_agent_for_judgment
    - require_thomas_approval_for_high_risk_actions
    - report_with_conclusion_reason_risk_next_action
```

## 6. What This Means

For the first agent group, the Core should be used as a compass, not a heavy rule engine.

The system should start with simple routing and judgment:

```text
What is the request?
Is this judgment work or repeatable work?
Is this related to a business opportunity?
If yes, is there revenue potential?
Is there high risk?
Can it be handled autonomously?
What should be reported to Thomas?
```

The detailed models can become active only after actual decisions, corrections, and results are stored.
