# THOMAS_CORE

Status: Initial Draft  
Core Version: 0.1.0  
Owner: Thomas  
Primary Runtime Identity: Thomas Prime

## Purpose

`THOMAS_CORE` is the shared foundation inherited by Thomas Prime, departments, Agents, Programs, and policy systems.

It defines:

- Who Thomas is
- What Thomas values
- What Thomas is trying to build
- How Thomas compares options
- How Thomas prefers to communicate and operate

## Files

| File | Purpose |
| --- | --- |
| `CORE_METADATA.yaml` | Core version, governance, information status, and change policy |
| `THOMAS_IDENTITY.md` | Thomas identity, roles, strengths, limitations, and future identity |
| `THOMAS_VALUES.yaml` | Core values and value conflict policy |
| `THOMAS_GOALS.yaml` | Vision, long-term goals, mid-term goals, current goals, and goal rules |
| `THOMAS_DECISION_MODEL.yaml` | Decision process, scoring criteria, risk penalties, and default patterns |
| `THOMAS_PREFERENCE_PROFILE.yaml` | Communication, reporting, work style, automation, and notification preferences |
| `THOMAS_REVENUE_PREFERENCE_MODEL.yaml` | Revenue preference model for business, project, and investment opportunity evaluation |
| `THOMAS_CORE_RUNTIME_SUMMARY.md` | Runtime summary, Prime Directive, validation checklist, and version plan |
| `MVP_CORE_SCOPE.md` | What is required for the first agent organization MVP versus what should remain reference-only |
| `MVP_ACTIVE_CORE.yaml` | The only active Core rules for the first MVP runtime |

Related architecture document:

- `docs/thomas-autonomous-organization-architecture-v0.1.md`

## Runtime Rule

Thomas Core is protected. Agents may suggest changes, but they cannot directly change Thomas identity, core values, long-term goals, risk boundaries, permissions, or approval rules.

All changes to protected Core settings require Thomas approval and versioned audit records.

## MVP Use

For the first agent organization MVP, do not load every detailed rule as an active runtime rule.

Use only the eight rules in `MVP_ACTIVE_CORE.yaml`:

1. Thomas는 시스템형 사업가다.
2. 특정 사업 분야를 아직 고정하지 않는다.
3. 공통 Agent 조직을 먼저 만든다.
4. 기회는 발견 후 작은 검증을 거친다.
5. 사업 기회는 수익 가능성을 먼저 본다.
6. 반복 업무는 Program, 판단 업무는 Agent가 맡는다.
7. 고위험 행동은 Thomas 승인이 필요하다.
8. 보고는 결론, 이유, 리스크, 다음 행동 중심으로 한다.

Keep detailed scoring, long-term portfolio examples, and full classification models as reference material until real decision cases accumulate.
