"""Option C deploy runner — the gatekeeper (G) and privileged actor (X) of the C2 design.

This package is the C3a implementation of the design recorded for Option C (host privilege
separation). It is a lane in the sense of ``tests/test_mvp_runtime_domain_isolation.py``: the
core never imports it at module level, and nothing here is wired into any running service or
installed.

- C3a-1 — deciding whether a signed approval counts: ``reasons`` (the closed code table and its
  fixed sentences), ``canonical``, ``trust`` (CR-12 T3), ``clock``, ``approval_verify``.
- C3a-2 — the actor's records: ``private_files``, ``consumption``, ``request_state``,
  ``audit_chain``, ``latch``, ``startup``.
- C3a-3 — the two processes and what X may run: ``gate`` (G), ``actor`` (X), ``door`` (their unix
  sockets on the shared door transport, with a literal uid list), ``docker_argv`` (the closed argv
  templates) and ``commands`` (the one place a process is started, by argv, with a fixed
  environment).

What the package does not do: read ``.env`` or any secret, fetch from a network, run a container,
or touch the runtime state root. ``validate_candidate``, ``promote`` and ``rollback`` are refused by
a code constant (CR-7). Every path is passed in by the caller. Authority: the C2 specification
(scratchpad ``C2_SPEC_V0.5``) as approved by Thomas on 2026-10-16; each increment's record is under
``docs/history/2026-10-16-deploy-runner-c3a-*``.
"""

from __future__ import annotations
