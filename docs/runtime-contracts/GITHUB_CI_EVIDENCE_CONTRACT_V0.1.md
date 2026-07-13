# GitHub CI Evidence Contract v0.1

**Schema Version:** `github_ci_evidence.v0.1`
**Document Version:** `0.1.0`
**Status:** `I0.5.1 Rev2 Verified-Evidence Contract`
**Owner:** `Thomas`

## Purpose

Bind one successful Thomas Agent GitHub Actions run to the exact local Repository state used for Runtime Promotion Readiness review.

This contract replaces free-form `PASS` strings and manual CI-status booleans. It is evidence for review only and never grants Runtime activation.

## Required Evidence

The record must include:

- Repository `owner/name`;
- exact workflow name and path;
- SHA-256 of the local workflow file;
- GitHub Actions run ID and attempt;
- trigger event;
- exact 40-character Head Commit SHA;
- completed/success run status;
- GitHub run URL;
- one successful Ubuntu job;
- one successful Windows job;
- positive and unique GitHub job IDs;
- collection timestamp;
- `GITHUB_API_VIA_GH` collection source;
- deterministic evidence fingerprint and SHA-256.

## Collection Boundary

The canonical collector is:

```text
scripts/collect_github_ci_evidence.py
```

Prefer the successful `push` run for the exact branch HEAD. A `pull_request` run is accepted only when the GitHub API `head_sha` exactly matches local HEAD.

It invokes the authenticated GitHub CLI only to read the public/private Repository run and job metadata available to the current user. It must not read, print, store, or create API tokens, application secrets, private keys, passwords, or credential values.

## Verification Boundary

Runtime Promotion Readiness verifies all of the following:

```text
Evidence semantics and fingerprint
+
Local Git origin Repository
+
Local Git HEAD Commit SHA
+
Local workflow path and SHA-256
+
GitHub run completed/success
+
Ubuntu job completed/success
+
Windows job completed/success
```

A workflow file by itself is not CI evidence. A free-form run reference is not CI evidence. A manually supplied `PASS` value is not CI evidence.

## Trust Limitation

The evidence is hash-bound and collected from the live GitHub API at collection time, but it is not a cryptographic signature from GitHub. It protects against accidental or unstructured promotion claims within the local review workflow; it does not protect against a fully privileged local operator intentionally rewriting source and evidence together.

## Runtime Effect

Always false:

```text
grants_runtime_permission=false
grants_runtime_activation=false
grants_core_activation=false
grants_tool_enablement=false
grants_program_enablement=false
grants_executor_enablement=false
grants_external_execution=false
grants_financial_execution=false
```
