# Runtime Component Attestation Contract v0.1

**Schema Version:** `runtime_component_attestation.v0.1`
**Document Version:** `0.1.0`
**Status:** `I0.5.1 Review-Only Contract`
**Owner:** `Thomas`

## Purpose

Bind each registered I0.5 component to the literal identity and version declared by its implementation source without executing the component.

## Required Evidence

| Field | Meaning |
| --- | --- |
| `component_id` | Registry component identity |
| `registry_version` | Version declared by the Registry |
| `implementation_ref` | Repository-relative implementation file |
| `implementation_sha256` | Exact implementation file hash |
| `implementation_id_constant` | Literal source constant holding the implementation ID |
| `implementation_version_constant` | Literal source constant holding the implementation version |
| `implementation_id` | Extracted literal ID |
| `implementation_version` | Extracted literal version |
| `id_match` | Registry and implementation ID parity |
| `version_match` | Registry and implementation version parity |
| `boundary_match` | Candidate status and no-effect Runtime boundary parity |
| `result` | `PASS` or `BLOCK` |

## Rules

- Attestation uses AST inspection of literal constants and does not import or execute Runtime implementation modules.
- Registry ID and version must exactly match implementation constants.
- Registry and component boundaries must remain candidate-only, non-authoritative, and no-effect.
- Kernel mode remains `DEVELOPMENT_REPLAY`; model, Tool, Program, network, filesystem-write, external-action, and Runtime-mutation capabilities remain disabled.
- Implementation paths must stay inside the Repository and must exist.
- Attestation is evidence only. It does not register, activate, execute, or promote a component.
- A `PASS` attestation does not create Runtime permission or Runtime authority.
