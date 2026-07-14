# PR #11 — Generated / Historical / Final Reference Cleanup

**Status:** Prepared for ordered batch application
**Runtime behavior change:** None
**Authority expansion:** None

## Result

- moves reproducible `build/` and `generated/docs/` outputs behind `generated/`;
- establishes one Generated Artifact index;
- moves superseded architecture, frozen I0.4 evidence, and migration review records behind `historical/`;
- preserves Core release manifests and copied release/toolchain snapshots in place to avoid invalidating immutable hashes, while classifying them separately;
- replaces the legacy Registry projection with `runtime/registry_resolution.py`;
- archives the parallel Kernel candidate, slim adapter candidate, legacy Registry projection, and superseded Registry candidate references only after active import consumers are removed;
- publishes the final one-screen Active Architecture reference;
- keeps one canonical Gate CLI and existing Domain Gates.

## Safety

Generated, Historical, Deferred, Compatibility, and validation evidence do not grant Permission, Approval, Authority, Runtime activation, Tool/Program enablement, Executor handoff, external action, financial action, or Core activation.
