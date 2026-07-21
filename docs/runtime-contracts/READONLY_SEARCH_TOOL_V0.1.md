# Read-only Search Tool (R3) — v0.1

**Status:** Active (MVP runtime). **Normative authority:** None — this document describes
the runtime behavior; the Governance Policy, closed schemas, and `runtime/mvp_runtime/`
code remain authoritative.

The R3 read-only web-search tool lets the specialist gather public web results as
**evidence** for its analysis. It is the tool analog of the model provider: deterministic
and network-free by default (`MockSearchTool`), with a real network backend reachable only
behind the enforced Safety-Flag Gate.

## What it is — and is not

- It **is** a read-only information lookup, modelled as an **`INTERNAL_READ` ALLOW action
  at P1 (READ)** with its own `permission_decision.v0.3`. Its hits become source-attributed
  `web_search` evidence on the `agent_output`, and the use is audited as a `TOOL_USED`
  event.
- It is **not** a runtime tool-enablement. `role_assignment.allowed_tool_ids` stays empty,
  every `runtime_effect` stays REVIEW_ONLY / EVIDENCE_ONLY, and `tool_enablement_allowed`
  stays false. The search executes a read; it enables nothing. A read-only search is
  therefore **not** modelled as a `tool_request.v0.1` (that contract is an executor-handoff
  review packet — the wrong shape for an internal read).

## Governance & safety

- **Permission:** `runtime/mvp_runtime/permission.py::build_search_permission_decision`
  mints the `INTERNAL_READ` / P1 decision, validated against the closed schema and the
  canonical Governance Policy (`scope INTERNAL_READ → ALLOW`). Fails closed identically to
  any other action.
- **Safety-Flag Gate:** a real network search requires `network_access` to be activated
  (`runtime/mvp_runtime/safety_gate.py`). `select_search_tool()` returns `MockSearchTool`
  by default; it returns the real `WebSearchTool` only when `MVP_SEARCH_TOOL=<backend>` AND
  a valid local activation record authorizes `network_access` for that backend. The env var
  alone fails closed. `WebSearchTool` re-verifies the authorization at socket-open time
  (defense in depth) and reads its API key from an env var **by name** (never stored,
  logged, or echoed).
- **Audit:** the pipeline records a `search_permission_decision` + a `tool_use` record and a
  `TOOL_USED` audit event (tool id/class, query/result hashes, sources, result count,
  network egress), chained into the run's tamper-evident trail.

## Running the real search locally (operator step)

Real network search is a deliberate, per-machine, gitignored activation — never committed.

```bash
# 1) Activate the network_access flag for the search backend (writes a local record):
python scripts/activate_safety_flag.py \
    --provider-id brave_search --flags network_access \
    --authority-level P1 --ttl-minutes 120 \
    --reason "Operator decision: enable read-only Brave search."

# 2) Provide the backend API key (its own env var; the activation record never holds it):
export BRAVE_SEARCH_API_KEY=...            # Windows: setx BRAVE_SEARCH_API_KEY ...

# 3) Run with the real tool selected:
MVP_SEARCH_TOOL=brave_search python -m runtime.mvp_runtime.cli "이 사업 아이디어를 분석해줘: ..."
```

Without step 1 the run fails closed (`ACTIVATION_MISSING`); without step 2 it fails closed
(`NO_API_KEY`); the default (no `MVP_SEARCH_TOOL`) uses the network-free mock. The backend
is swappable — only the endpoint/parse and provider id in `WebSearchTool` are Brave-specific.

### Tavily backend (2026-07-21)

After Brave dropped its free tier for new users, **Tavily** became the recommended free
backend (`TavilySearchTool`): the Researcher plan is recurring free — 1,000 credits/month,
no payment method — which keeps the "free hosted APIs only" locked decision intact. Same
gate posture, own provider id and grant:

```bash
python scripts/activate_safety_flag.py --provider-id tavily_search --flags network_access \
    --authority-level P1 --reason "Operator decision: enable read-only Tavily search."
export TAVILY_API_KEY=...
MVP_SEARCH_TOOL=tavily_search python -m runtime.mvp_runtime.cli "..."
```

One backend at a time — a search failover chain was considered and deliberately not built.

### Degradation (search is enrichment, not the task)

A backend failure at run time — quota exhausted, transport error, malformed response —
**degrades the run instead of blocking it** (explicit Thomas decision with the Tavily
rollout): the analysis proceeds with no live evidence, the `tool_use` record carries
`degraded: true` + the failure's reason code, and the `TOOL_USED` audit event adds
`SEARCH_DEGRADED` + that code to the chain. The R7.2 triage-degradation precedent:
recorded and audited, never silent, and exhausting a free tier can never turn into a paid
call or a dead agent. Selection-time failures are unchanged and still fail closed
(`ACTIVATION_MISSING` etc.) — a misconfigured gate is not a degraded search.

## Key modules

- `runtime/mvp_runtime/tools.py` — `SearchTool` protocol, `MockSearchTool`, `WebSearchTool`,
  `select_search_tool`, `run_search` (evidence record).
- `runtime/mvp_runtime/permission.py` — `build_search_permission_decision`.
- `runtime/mvp_runtime/pipeline.py` / `worker.py` / `audit.py` — execution, evidence, audit.
- `scripts/activate_safety_flag.py` — operator activation helper.
