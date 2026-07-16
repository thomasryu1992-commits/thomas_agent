"""Thomas Agent MVP Runtime (Phase R2).

A thin, live single-agent runtime built *beside* the deterministic read-only
replay kernel. It reuses kernel components (integrity, schema validation, audit,
registry resolution, policy) as libraries and never mutates them, so the replay
kernel keeps its ``model_calls == 0`` read-only contract.

This module tree is inert until each capability is explicitly enabled through
its own governance gate. R2.1 (Task Intake) performs no external writes, no
network I/O, and no model invocation (it does read schema files read-only).
"""

from __future__ import annotations
