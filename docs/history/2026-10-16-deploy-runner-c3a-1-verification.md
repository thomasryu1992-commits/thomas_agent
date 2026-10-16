# Option C deploy runner C3a-1: approval verification, closed reason codes, trust files and time rules

**What changed** (`runtime/mvp_runtime/deploy_runner/`, three schemas, one test file).

This is the first code of the Option C deploy runner (host privilege separation). The C2 design was
confirmed complete by Thomas on 2026-10-16 (C2-FINAL), with the C3 prerequisite decisions D-10a,
CR-12 (T3, conditional), TYPE-B-KEY K1, D-3 (C3a scope), CR-8 (8a/8b) and the CR-10/11/19
invariant. This increment is C3a-1: the pieces that decide whether a signed approval counts. Nothing
is wired into a service, installed, or able to reach Docker or `.env`.

- **`reasons.py`** — the closed reason-code table.
  - Every code that may leave in a reply has exactly one fixed sentence, and the reply says nothing
    else about the failure.
  - Codes that describe states visible only in the audit (store genesis, chain breaks, recovery-inbox
    outcomes) are a separate set. If one reaches the reply builder, it answers as `RUNNER_ERROR`.
  - `request_id` is echoed only when it is a plain token.
  - The reply schema's enum is pinned to the table by a test.
- **`canonical.py`** — a signed document is verified on its arrived bytes and only then parsed.
  - Parsing refuses anything two parsers could read differently: non-ASCII, duplicate keys, floats,
    non-finite constants.
  - Re-encoding with the kernel's canonical encoder must reproduce the signed bytes exactly
    (`APPROVAL_NOT_CANONICAL`).
- **`trust.py`** — the three root-owned trust files are opened with `O_NOFOLLOW` and checked on the
  descriptor: regular file, one link, expected owner, nobody else can write.
  - `allowed_signers` is held to a closed format: one principal, `ssh-ed25519` only,
    `namespaces=` required and limited to the three document namespaces, no `cert-authority`.
  - Its key set must equal the anchor shipped in the reviewed install (CR-12 T3). A key added to the
    file but not the anchor refuses the whole file (`TRUST_ANCHOR_MISMATCH`).
  - A revocation list that cannot be read with certainty is `REVOCATION_LIST_UNAVAILABLE`, never
    "nothing revoked".
- **`clock.py`** — an action starts only when the approval stays valid for the whole deadline, so
  an expiry never cuts an effect in half.
  - A policy whose maximum lifetime is shorter than its longest deadline refuses to start.
  - A clock that is unsynchronised, stepped back past tolerance, or jumping against the monotonic
    clock is `CLOCK_UNTRUSTED`.
  - The numbers stay configuration until C4 measures them.
- **`approval_verify.py`** — the C2 order:
  1. closed request arguments (no paths, no free text);
  2. `ssh-keygen -Y verify` by argv, in the deploy namespace, on a private copy of the trust
     material, with output discarded and only the exit status read;
  3. then schema, canonical form, action, environment, time window, revocation, and the signed
     target against the requested one.
  - `check_measured_binding` compares the signed target with what the actor measured; a missing
    measurement is a mismatch, never a pass.
- **Schemas:** `deploy_approval.v0.1`, `deploy_runner_reply.v0.1`, `deploy_audit.v0.1`, all closed.
  - The approval's in-document `schema` value stays `thomas.deploy_approval.v1`, as closed in C2.
  - File names follow the repository's `v0.N` convention.

**Why the CR-7 gate is not an env opt-in.** The operational verbs (validate, promote, rollback) are
not implemented in C3a at all. When they are, their gate is a code constant by decision (CR-7).
The env-only gate rule in `CLAUDE.md` names model and network capabilities; whether a deploy
capability belongs under it is a governance question to record when those verbs are built.

**What this does not protect.**
- The actor that will hold Docker access is root-equivalent if it is compromised; nothing here
  changes that.
- T3 stops writers of the trust files that cannot write the install. It does not stop root or a
  compromised actor.
- Synthetic tests prove code paths, not permission separation (that is C4 and C6).

**Numbers.**
- **Tests:** `tests/test_mvp_runtime_deploy_runner_verify.py`, 217 cases. Signing tests make a
  throwaway ed25519 key in `tmp_path` per test; no real key or trust file is touched.
- **Fault injection** (scratch, outside the suite): each of 24 injected faults made a test fail.
  - verify-after-parse, no canonical check, wrong namespace, no revocation, no requested binding;
  - extra arguments, no action check, writable verifier, verifier output kept, missing measurement
    passing, exit 255 passing;
  - no anchor, links followed, hard links, any principal, optional namespaces, bad revocation lines;
  - no deadline room, no cutoff, no lifetime invariant, unsynchronised clock, duplicate keys;
  - any `request_id` echoed, audit-only codes leaking.
- **Windows skips:** +26, the descriptor-open and `ssh-keygen` tests. A simulated run without
  `O_NOFOLLOW` and `ssh-keygen` gives 191 passed and 26 skipped. The win32 ceiling moves 306 → 332,
  predicted; this PR's Windows run is the measurement.
- **Observed on this host while building:** `ssh-keygen -Y verify -r` accepts an empty revoked-keys
  file, and a key limited by `namespaces=` does not verify a signature in another namespace. These
  were NOT_VERIFIED in the C2 draft.
