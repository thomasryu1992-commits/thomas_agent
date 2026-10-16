# Option C: the C2 specification enters the repository (CR-16)

**What changed** (documents only).

- `docs/proposals/HOST_PRIVILEGE_SEPARATION_C2_SPEC_V0.5.md` is new. Its body is the C2
  specification v0.5 exactly as Thomas fixed it with C2-FINAL (md5 `5d096745`). A short header
  gives its status and points to the main proposal for everything decided later.
- `HOST_PRIVILEGE_SEPARATION_V0.1.md` changes:
  - a new §4.25 lists the C2 decisions in their approval wording, the C3a progress (#1227 and
    #1228 merged, #1229 open), and where the implementation differs from the specification and why;
  - §4.3: the `prepare_candidate` row now says what C2 §6.3 settled. The actor measures its own tree
    and the running images, checks the compose project label and that the tags are free, tags the
    measured image, and builds. compose config moves to `validate_candidate`;
  - §4.6, §6 and §7 named N-1–N-12 and dropped N-13–N-15, which were added in C1. The numbering is
    corrected;
  - §8: D-1, D-2, D-3, D-4 and D-10 carry their 2026-10-16 DESIGN_ONLY decisions.
- `docs/proposals/STATUS.md` was regenerated from the status lines.

**Why the specification is copied verbatim rather than edited into the proposal.** C2-FINAL fixed
one document by its hash, and the closed contract (binding fields and reason codes) is defined
against it. Rewording it while moving it would leave two texts and a question about which one was
approved. Later status lives in the header and in §4.25, never in the body.

**What this does not do.** It grants nothing: no install, no account change, no operation. It
settles none of the open decisions in §4.25.1.
