"""Thomas Agent I0.5 read-only development replay kernel.

This package is intentionally non-authoritative. It performs local, in-memory,
read-only orchestration over explicit contract snapshots and never enables
Tool, Program, Executor, network, model, scheduler, control-channel, or
filesystem mutation paths.
"""

from .kernel import KernelBlocked, ReadOnlyRuntimeKernel, run_bundle

__all__ = ["KernelBlocked", "ReadOnlyRuntimeKernel", "run_bundle"]
__version__ = "0.1.0"
