"""Startup guard: refuse to run when the per-machine state directory is not writable.

The failure this exists for happened in production. The deployed services run as a
non-root user (uid 10001); an operator ran a CLI directly on the host as root; root-owned
files were left behind in the mounted `.runtime_governance_state/`. The container could
then no longer write them — and every capability that touches one failed *individually*,
at its own door, with its own reason code, on every request. The task queue refused each
submission (`REGISTRY_WRITE_FAILED`), the feedback pointer silently stopped binding, and
the only startup signal was one best-effort warning line scrolling past in a log.

Each of those refusals was individually correct — fail-closed did its job. What was missing
was a single place that says the real thing **once, at startup, loudly**: *this process
cannot write its own state, so it must not pretend to run.* A runtime whose evidence cannot
be persisted does not deliver (`PersistenceError`'s whole premise); one that cannot persist
*anything* should not start.

Why writability, not ownership: ownership is a proxy. `os.access(W_OK)` asks the question
that actually matters and stays correct for group permissions, ACLs, and the root case
(root genuinely can write root-owned files, so a host-side run is not falsely accused).

Deliberately not self-healing: this refuses and prints the exact `chown` to run. Silently
fixing permissions would mean a process quietly widening its own access to governed state
— the wrong direction in a repo where nothing grants itself anything.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Iterable

from .errors import PersistenceError
from .paths import repo_root as _repo_root

STATE_DIR_REL = ".runtime_governance_state"

# Enough offenders to recognize the pattern (one directory? one stale file? everything?)
# without a wall of text burying the fix line underneath it.
MAX_REPORTED = 10


def _default_writable(path: Path) -> bool:
    return os.access(path, os.W_OK)


def unwritable_paths(
    root: Path | None = None,
    *,
    writable: Callable[[Path], bool] = _default_writable,
) -> list[Path]:
    """Every existing path under the state directory this process cannot write.

    The directories are checked too, not only the files: a read-only directory blocks the
    *creation* of the next ledger segment, which is exactly as fatal as an unwritable
    existing one and is invisible until the first append.

    An absent state directory is not a finding — a fresh machine creates it on first write.
    ``writable`` is injectable so the behavior can be tested without depending on the uid
    the test suite happens to run as (as root, every real permission check passes).
    """
    base = (root if root is not None else _repo_root()) / STATE_DIR_REL
    if not base.exists():
        return []
    offenders: list[Path] = []
    if not writable(base):
        offenders.append(base)
    for parent, dirnames, filenames in os.walk(base):
        parent_path = Path(parent)
        for name in sorted(dirnames) + sorted(filenames):
            candidate = parent_path / name
            if not writable(candidate):
                offenders.append(candidate)
    return offenders


def describe(offenders: Iterable[Path], *, root: Path) -> str:
    """The refusal message: what is wrong, who this process is, and the one command."""
    listed = list(offenders)
    shown = listed[:MAX_REPORTED]
    base = root / STATE_DIR_REL
    # POSIX-only identity; on Windows the uid/gid framing does not apply and the message
    # stays accurate by simply omitting it rather than inventing one.
    identity = f" (uid={os.getuid()}, gid={os.getgid()})" if hasattr(os, "getuid") else ""
    lines = [
        f"{len(listed)} path(s) under {STATE_DIR_REL}/ are not writable by this "
        f"process{identity}"
    ]
    lines += [f"  - {'.' if path == base else path.relative_to(base)}" for path in shown]
    if len(listed) > len(shown):
        lines.append(f"  … and {len(listed) - len(shown)} more")
    lines.append(
        "The runtime persists every record, audit event and control state here; it will "
        "not start while it cannot. This usually means a CLI was run directly on the host "
        "as another user (commonly root) against the same state the service mounts."
    )
    # A refusal without a remedy just relocates the investigation. Every platform gets a
    # fix line — the POSIX one can be pasted, and elsewhere it names the account and the
    # path, which is the same instruction in the form that platform's tooling takes.
    if hasattr(os, "getuid"):
        lines.append(f"Fix: chown -R {os.getuid()}:{os.getgid()} {base}")
    else:
        lines.append(f"Fix: grant the account running this process write access to {base}")
    return "\n".join(lines)


def assert_state_writable(
    root: Path | None = None,
    *,
    writable: Callable[[Path], bool] = _default_writable,
) -> None:
    """Raise ``PersistenceError('STATE_NOT_WRITABLE')`` if any state path is unwritable.

    Called once at the start of each long-lived service. Fail-closed and *early*: the
    alternative is a service that starts healthy, answers `/status`, and refuses every
    piece of real work it is asked to do.
    """
    base = root if root is not None else _repo_root()
    offenders = unwritable_paths(base, writable=writable)
    if offenders:
        raise PersistenceError("STATE_NOT_WRITABLE", describe(offenders, root=base))
