"""Option C deploy runner — the gatekeeper (G) and privileged actor (X) of the C2 design.

This package is the C3a implementation of the design recorded for Option C (host privilege
separation). It is a lane in the sense of ``tests/test_mvp_runtime_domain_isolation.py``: the
core never imports it at module level, and nothing here is wired into any running service.

C3a-1 (this increment) holds only pure, synthetic-testable pieces:

- ``reasons`` — the closed reason-code table and the one fixed sentence each code answers with;
- ``canonical`` — strict decoding of a signed document's bytes and the canonical-bytes check;
- ``trust`` — reading the root-owned trust files through a descriptor and checking them against
  the installed anchor (CR-12 T3);
- ``clock`` — approval time windows, the lifetime-versus-deadline invariant, clock-trust checks;
- ``approval_verify`` — verifying a deploy approval end to end against those pieces.

Nothing here reads ``.env``, calls Docker, opens a socket, or touches the runtime state root.
Every path is passed in by the caller. Authority: the C2 specification (scratchpad
``C2_SPEC_V0.5``) as approved by Thomas on 2026-10-16; the increment's record is
``docs/history/2026-10-16-deploy-runner-c3a-1-verification.md``.
"""

from __future__ import annotations
