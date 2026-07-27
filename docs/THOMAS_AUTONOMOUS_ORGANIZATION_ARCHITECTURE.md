# Thomas Autonomous Organization Architecture

**Document Version:** `0.2.0`
**Status:** ACTIVE — top-level design-direction (Goal) document
**Owner:** Thomas
**Applies To:** Thomas Prime, departments, Agents, Programs, Tools, future business organizations
**Promoted:** 2026-07-24, by explicit Thomas decision. Content is carried forward unchanged
from the frozen historical record
[`historical/architecture/thomas-autonomous-organization-architecture-v0.1.md`](../historical/architecture/thomas-autonomous-organization-architecture-v0.1.md)
(v0.1); only this promotion header, the document-position section, and repository links were
updated. The historical copy stays frozen evidence and is never edited.

MVP Operating Rules: [MVP Operating Policy](./MVP_OPERATING_POLICY.md)
Prime Role and Authority: [Thomas Prime Charter](../03_ROLE_CONTRACTS/THOMAS_PRIME_CHARTER.md)
Dynamic Role Contracts: [Role Contracts](../03_ROLE_CONTRACTS/README.md)
Current implementation reality: [Active Architecture](./ACTIVE_ARCHITECTURE.md)

## 0. Document Position and Authority

이 문서는 활성 문서 체계의 **최상위 설계 방향(Goal) 문서**다. 새로운 기능, 마일스톤,
로드맵, 조직 확장 설계는 이 문서의 세 레이어(Target / MVP / Dynamic Task Team) 중 어디에
속하는지, 그리고 §12–§16의 확장 기준과 가드레일을 만족하는지를 기준으로 설계한다.

문서 계층 안에서의 위치는 [MVP Operating Policy](./MVP_OPERATING_POLICY.md)의 Document
Position 다이어그램을 따른다: Thomas → Thomas Core → (Operating Constitution — 비활성
후보) → **본 문서 (Organization Architecture)** → Canonical Governance Policy → MVP
Operating Policy → Contracts / Definitions / Records.

권한 경계 (One Concept = One Authority):

- 이 문서가 소유하는 것: **장기 조직 방향, MVP 조직 범위, 동적 팀 구성 원칙, 확장/분리
  기준, 아키텍처 가드레일** — 즉 "무엇을 다음에 설계·구축할 것인가"의 기준.
- 이 문서가 소유하지 않는 것: Permission / Approval / Authority / effect / Kill Switch는
  [`governance/GOVERNANCE_POLICY.yaml`](../governance/GOVERNANCE_POLICY.yaml)이,
  현재 구현된 아키텍처의 사실 관계는 [Active Architecture](./ACTIVE_ARCHITECTURE.md)가,
  Identity / Values / Goals는 [`THOMAS_CORE/`](../THOMAS_CORE/)가 소유한다.
- 이 문서는 어떤 Runtime capability도 활성화하지 않는다. 설계 방향과 실행 권한은
  별개이며, capability 활성화는 언제나 Safety-Flag Gate와 명시적 Thomas 승인 경로를
  따른다.

"무엇을 만들 것인가"가 충돌하면 이 문서를 우선하고, "무엇이 허용되는가"가 충돌하면
Governance Policy를 우선한다.

## 1. Document Purpose

This document defines the overall organization architecture for Thomas Autonomous Organization.

It separates the architecture into three different layers:

1. Target Organization Architecture
2. MVP Organization Architecture
3. Dynamic Task Team Architecture

These layers are not competing designs. They solve different problems.

The Target Architecture defines the long-term direction. The MVP Architecture defines what should actually be built and validated now. The Dynamic Task Team Architecture defines how the system should activate only the minimum required roles for each task.

## 2. Architecture Principle

```text
Design for long-term expansion.
Build only the minimum required system.
Execute with the smallest effective team.
```

Core principles:

1. 조직은 크게 설계하되, 현재 구현은 최소화한다.
2. 모든 조직과 역할을 항상 실행하지 않는다.
3. 각 업무에는 필요한 최소 Agent와 Program만 동적으로 활성화한다.
4. 미래 확장을 위해 현재 시스템을 과도하게 복잡하게 만들지 않는다.
5. 현재 단순성을 위해 미래 확장 경로를 차단하지 않는다.
6. Agent 수를 늘리는 것이 조직 성장의 목표가 아니다.
7. 검증된 필요와 성과가 있을 때만 새로운 역할, 부서, 사업 조직을 추가한다.

## 3. Architecture Layers

### 3.1 Target Organization Architecture

Target Architecture is the long-term organization direction.

Purpose:

- Define the long-term organization shape.
- Separate common capability organization from future business organizations.
- Preserve Thomas Core, governance, memory, and permission structure across future expansion.
- Allow new departments, capabilities, and business groups without redesigning the whole system.

Important: Target Architecture does not mean every component should be implemented now.

### 3.2 MVP Organization Architecture

MVP Architecture is the minimum organization to build now.

Purpose:

- Validate Thomas Core.
- Validate Thomas Prime operation.
- Validate Agent and Program collaboration.
- Validate permission, memory, validation, and audit structure.
- Confirm early value with low complexity and low cost.

### 3.3 Dynamic Task Team Architecture

Dynamic Task Team is the temporary execution structure assembled per task.

Purpose:

- Activate only the minimum roles required for the task.
- Avoid unnecessary Agent calls.
- Reduce cost and latency.
- Match team size to task complexity and risk.
- Release execution resources after task completion.

## 4. Target Organization Architecture

The long-term architecture is:

```text
Thomas
Human Owner and Sovereign Authority
  |
  v
Thomas Core
Identity, Values, Goals, Decision Model, Preference Profile
  |
  v
Operating Constitution
Organization Principles, Autonomy Principles, Responsibility Principles
  |
  v
Governance Layer
Authority, Permission, Risk, Approval, Audit
  |
  v
Thomas Prime
Chief Autonomous Coordinator
  |
  +-------------------------------+
  |                               |
  v                               v
Dynamic Strategic Board           Common Capability Organization
Important decisions only          Shared expert capability
                                  - Research & Intelligence
                                  - Knowledge & Memory
                                  - Communication
                                  - Planning & Operations
                                  - Business Analysis
                                  - Risk & Validation
                                  - Technology & Development
                                  - Performance & Audit
  |
  v
Opportunity & Business Creation System
  - Opportunity Discovery
  - Opportunity Research
  - Revenue Validation
  - Risk-Adjusted Evaluation
  - MVP Experiment
  - Business Validation
  - Expansion Decision
  |
  v
Future Business Portfolio
  - Business Group A
  - Business Group B
  - Business Group C
  - Future Ventures
  |
  v
Shared Autonomous Infrastructure
  - Agent Registry
  - Program Registry
  - Tool Registry
  - Memory Infrastructure
  - Data Infrastructure
  - Workflow Infrastructure
  - Policy Engine
  - Monitoring
  - Audit System
```

## 5. Long-Term Organization Layers

### 5.1 Thomas

Thomas is the final owner and sovereign authority.

Only Thomas can finally change:

- Core identity
- Core values
- Long-term goals
- Risk tolerance
- Top-level authority
- Operating Constitution
- Thomas Prime authority scope
- New high-risk business domains

Thomas does not manage every daily task. Thomas manages direction, boundaries, and important exceptions.

### 5.2 Thomas Core

Thomas Core is the common identity and judgment foundation inherited by all organizations and Agents.

Core includes:

- Thomas Identity
- Thomas Values
- Thomas Goals
- Thomas Decision Model
- Thomas Preference Profile

Thomas Core is not tied to a specific business. Every specialist organization and future business group inherits Thomas Core.

### 5.3 Operating Constitution

Operating Constitution is the highest operational rule set for the organization.

It should define:

- Purpose of the organization
- Thomas sovereignty
- Autonomy principles
- Responsibility separation
- Agent and Program roles
- Execution and validation principles
- Learning and permission boundaries
- Organization expansion principles

For MVP, this should stay lightweight.

### 5.4 Governance Layer

Governance Layer controls authority, risk, approval, and audit.

Main functions:

- Agent permission management
- Tool access management
- Risk classification
- Approval decision
- Prohibited action blocking
- Audit records
- Permission escalation prevention

Governance Layer has higher priority than normal Agents.

### 5.5 Thomas Prime

Thomas Prime is the central autonomous coordinator.

Responsibilities:

- Interpret Thomas goals.
- Analyze the current situation.
- Set task priority.
- Select required specialist capability.
- Assemble Dynamic Task Teams.
- Coordinate across roles or departments.
- Integrate results.
- Report important exceptions.

Thomas Prime does not directly perform every specialist task.

### 5.6 Dynamic Strategic Board

Strategic Board is not an always-on organization.

It is assembled only for important decisions such as:

- New business entry
- Large capital or long-term resource commitment
- Major organization expansion
- New high-risk permission
- Long-term strategy change
- Important business shutdown

Only required perspectives are selected.

Example for new business evaluation:

```text
Research Perspective
+ Revenue Perspective
+ Risk Perspective
+ Operations Perspective
+ Technology Perspective
```

Do not call every Board member by default.

### 5.7 Common Capability Organization

Common Capability Organization provides reusable capabilities across multiple businesses.

Examples:

- Research can be shared by many businesses.
- Memory is shared across the organization.
- Risk capability can support all high-risk actions.

Common capability is not owned by one business group.

### 5.8 Future Business Portfolio

Validated businesses may become separate Business Groups.

A Business Group may have:

- Independent business goals
- Business leadership
- Business-specific Agents
- Business-specific Programs
- Business-specific data
- Business performance metrics
- Business-specific risk policy

Every Business Group still inherits Thomas Core and Operating Constitution.

## 6. MVP Organization Architecture

MVP does not implement the whole long-term organization.

MVP validates these hypotheses:

- Does Thomas Core help real judgment?
- Can Thomas Prime classify work properly?
- Can Agents use required Programs?
- Does independent validation improve output quality?
- Does Memory help later tasks?
- Can low-risk tasks be performed autonomously?

## 7. MVP Organization Diagram

```text
Thomas
  |
  v
Thomas Core v0.x
  |
  v
MVP Operating Constitution
  |
  v
Thomas Prime
  |
  v
Task Classifier & Router
  |
  +-----------------------+
  |                       |
  v                       v
General Specialist Agent  Validation Agent
  |
  v
Program & Tool Registry
  |
  v
Working Memory / Validated Memory
  |
  v
Activity Log / Audit Record
```

## 8. MVP Components

### 8.1 Thomas Core v0.x

Initial Core includes:

- Identity
- Values
- Goals
- Decision Model
- Preference Profile

MVP goal is not perfect Thomas replication. MVP only needs a minimum Core that can support real judgment.

Active MVP Core is defined in [MVP_ACTIVE_CORE.yaml](../THOMAS_CORE/MVP_ACTIVE_CORE.yaml).

### 8.2 MVP Operating Constitution

MVP Operating Constitution should define only the first operational principles.

It should include:

- Operating scope
- Autonomous execution principles
- Role separation
- Basic permission principles
- Basic memory principles
- Failure handling
- High-risk action limits

It should not include detailed department policy yet.

### 8.3 Thomas Prime

Initial responsibilities:

- Understand user requests.
- Connect requests to goals.
- Evaluate task complexity.
- Choose Agent or Program.
- Request validation when needed.
- Integrate results.
- Report results.

In MVP, Thomas Prime does not autonomously start a new long-term business.

### 8.4 Task Classifier & Router

All tasks are classified into one of these paths:

| Task Type | Route |
| --- | --- |
| Rule-based Task | Program |
| Simple Judgment | General Specialist Agent |
| Complex Judgment | Specialist Agent + Validation |
| High-risk Decision | Analysis + Thomas Approval |

### 8.5 General Specialist Agent

MVP should not implement every role as a separate Agent.

One General Specialist Agent can dynamically apply role contracts.

Example roles:

- Translation Role
- Research Role
- Planning Role
- Content Role
- Business Analysis Role

Separate Agents should be created only when task volume, independence, permission, or evaluation criteria justify separation.

### 8.6 Validation Agent

Validation Agent independently reviews generated results.

Review areas:

- Goal fit
- Logic
- Accuracy
- Missing information
- Evidence
- Risk
- Output quality

Low-risk simple tasks may use automatic Validator Programs instead.

### 8.7 Program & Tool Registry

Initial Programs:

- Text format conversion
- Data validation
- File saving
- Duplicate checking
- Schedule calculation
- Basic quality check

Initial Tools:

- LLM
- Memory
- File System
- Search
- Telegram

Agents cannot arbitrarily use unregistered Programs or Tools.

### 8.8 MVP Memory

MVP Memory has four levels:

```text
Session Memory
  -> Working Memory
  -> Validated Memory
  -> Core Candidate
```

Do not automatically store every conversation into Core.

## 9. Not Implemented In MVP

The MVP does not implement these as always-on organizations:

- Permanent Business Board
- Every specialist Department
- Every role-specific independent Agent
- Independent Business Groups
- Automatic business creation
- Automatic organization expansion
- Large Agent hierarchy
- Complex multi-step approval organization

These structures may exist as future expansion locations in documents, but should not be built now.

## 10. Dynamic Task Team Architecture

The whole MVP organization should not run for every task.

Each task activates only the minimum required team.

### 10.1 Simple Repetitive Task

Examples: format conversion, fixed calculation, basic validation.

```text
Thomas Prime
  -> Program
  -> Result
```

### 10.2 Simple Specialist Task

Example: normal translation.

```text
Thomas Prime
  -> Specialist Agent using Translation Role
  -> Automatic Check
  -> Result
```

### 10.3 Normal Complex Task

Example: market research.

```text
Thomas Prime
  -> Research Role
  -> Validation Agent
  -> Result
```

### 10.4 Complex Strategy Task

Example: new business strategy.

```text
Thomas Prime
  -> Research Perspective
  -> Revenue Perspective
  -> Risk Perspective
  -> Integrated Decision
  -> Result
```

In early MVP, one Agent may separate these perspectives internally. If independence or risk increases, perspectives can become separate Agents.

### 10.5 High-Risk Decision

```text
Thomas Prime
  -> Relevant Specialist Agents
  -> Independent Risk Review
  -> Thomas Approval
  -> Restricted Execution
```

## 11. Relationship Between Target And MVP

```text
Target Architecture
Future organization direction
  ^
  |
Gradual expansion
  ^
  |
MVP Architecture
Current minimum validation organization
```

MVP is not a smaller copy of Target Architecture.

MVP is the minimum foundation that can later expand into Target Architecture.

## 12. Expansion Principles

New Agents, departments, or business organizations are added only when:

- Repeated specialist work exists.
- Existing Agents cannot handle it well.
- Independent specialist judgment is needed.
- Separate permission is needed.
- Independent validation is needed.
- Clear performance criteria exist.

## 13. Agent Separation Criteria

Consider a separate Agent when three or more answers are YES:

1. Does it require different specialist knowledge?
2. Does it have a different goal from the existing Agent?
3. Does it require independent review?
4. Does it require different data permission?
5. Does it need separate performance metrics?
6. Would conflict of interest occur if the existing Agent handled it?

## 14. Department Creation Criteria

Create a new Department only when:

- Persistent specialist work exists.
- Multiple Agents or Programs are required.
- The work has an independent goal.
- Independent operating responsibility is needed.
- Long-term value is expected.
- Separation is more efficient than including it in an existing department.

## 15. Business Group Creation Criteria

A new Business Group follows this path:

```text
Opportunity
  -> Research Candidate
  -> Business Hypothesis
  -> Small Validation
  -> Revenue Evidence
  -> Risk Review
  -> Expansion Proposal
  -> Thomas Approval
  -> Business Group
```

A good idea alone does not create a Business Group.

## 16. Architecture Guardrails

Avoid:

- Building many Agents now for future possibilities.
- Using multiple Agents for every task.
- Handling simple repetitive work with Agents.
- Treating the organization chart as the execution process.
- Expanding unvalidated businesses into independent organizations.
- Using Agent count as a maturity metric.
- Mistaking complexity for scalability.

## 17. Official Architecture Summary

### Long-Term Target

```text
Thomas
  -> Thomas Core
  -> Operating Constitution
  -> Governance
  -> Thomas Prime
  -> Common Capability Organization
  -> Opportunity & Business Creation
  -> Validated Business Portfolio
  -> Shared Autonomous Infrastructure
```

### Current MVP

```text
Thomas
  -> Thomas Core v0.x
  -> MVP Operating Constitution
  -> Thomas Prime
  -> Task Router
  -> General Specialist Agent + Validation Agent
  -> Programs, Tools, Memory
```

### Runtime

```text
Task
  -> Complexity and Risk Classification
  -> Minimum Required Dynamic Team
  -> Execution
  -> Validation
  -> Memory
  -> Close
```

## 18. Implementation Status Note (non-normative)

이 절은 설계 기준이 아니라 현재 위치를 가리키는 포인터다. §6–§8의 MVP 레이어는 대부분
구현되어 있다 (intake → Prime planning → routing → gated model call → independent
validation → audit → memory; R2–R10 및 이후 증분 — 구조와 소유권은
[Active Architecture](./ACTIVE_ARCHITECTURE.md), 전달 이력은 [Build History](./BUILD_HISTORY.md),
남은 작업은 [Remaining Work](./REMAINING_WORK.md)가 소유한다). 새로운 설계 작업은
이 문서의 레이어와 §12–§16 기준에 근거를 두어야 하며, 근거 없는 확장은 §16 가드레일
위반으로 본다.
