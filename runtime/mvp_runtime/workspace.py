"""R8 Controlled Write — create a file in the approved workspace.

The first capability that leaves a durable artifact of the agent's own choosing outside
the runtime's private state, and the first **EXECUTE_AND_REPORT** action in the runtime
(everything before it is ALLOW-tier). Governance already models it end to end — this
module adds no contract, schema, registry, or gate:

- Scope ``WORKSPACE_REVERSIBLE_WRITE`` is already in the ``permission_decision.v0.3``
  enum and already priced at EXECUTE_AND_REPORT by ``governance/GOVERNANCE_POLICY.yaml``.
- The kill switch already lists ``tool_write`` in ``kill_blocks``.

Safety model:

- **Create-only.** A write that would replace existing bytes fails closed
  (``TARGET_EXISTS``). Nothing is ever destroyed, so the write is reversible by deleting
  what it created — the reversibility ``WORKSPACE_REVERSIBLE_WRITE`` is named for is
  structural here, not a promise backed by a backup mechanism. Modifying an existing file
  is P4 INTERNAL_MODIFY, above the specialist's P3 ceiling, and is deliberately not built.
- **Confined.** The target must resolve inside ``workspace/``. Absolute paths, ``..``
  traversal, and symlink escape are each rejected with their own reason code; containment
  is verified **after** resolution, so a symlinked parent cannot smuggle a write out.
- **Kill-switch bound** (``kill_blocks: tool_write``): a PAUSED or KILLED runtime refuses
  to write. Checked in :func:`run_write`, the chokepoint both writers pass through.
- **Gated + off by default.** :func:`select_writer` returns the :class:`DryRunWriter`
  unless the caller opts in AND the Safety-Flag Gate authorizes ``filesystem_write``
  against a local activation record. The env var alone fails closed. The real writer
  re-asserts its authorization at the moment it touches disk.
- **Content is metadata-only in the record.** The write record carries the target,
  byte count, and ``content_sha256`` — never the content, so nothing a model wrote can
  leak into the audit trail.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Protocol, runtime_checkable

from runtime.read_only_kernel import integrity

from . import safety_gate, timeutil
from .control import ControlStore
from .errors import ToolBlocked, ToolError
from .paths import repo_root as _repo_root
from .safety_gate import FILESYSTEM_WRITE, Authorization

# The approved internal workspace: repo-root `workspace/`, gitignored and per-machine.
# Everything the agent may write lives under here; nothing else is reachable.
WORKSPACE_REL = "workspace"

WRITE_TOOL_ID = "workspace.writer"
WRITE_TOOL_VERSION = "0.1.0"
WRITE_TOOL_CLASS = "write"

MAX_CONTENT_BYTES = 1_000_000  # 1 MB — an analysis artifact, not a data dump
MAX_PATH_CHARS = 200

# Opting into the real disk-writing writer. As with the model provider and the search
# tool, the env var alone is NOT sufficient: the Safety-Flag Gate must authorize
# filesystem_write before a disk-capable writer is ever built (see select_writer).
WRITER_ENV = "MVP_WORKSPACE_WRITER"
REAL_WRITER = "real"
# Writing a local file touches no network and invokes no model.
_WRITE_FLAGS = (FILESYSTEM_WRITE,)


@dataclass
class WriteResult:
    relative_path: str
    bytes_written: int
    content_sha256: str
    created: bool


@runtime_checkable
class WorkspaceWriter(Protocol):
    tool_id: str
    tool_version: str

    def write(self, target: Path, content: str) -> int: ...


class DryRunWriter:
    """Default writer: computes the write and touches nothing.

    The analog of ``MockSearchTool`` — it lets the whole controlled-write path (permission
    decision, confinement, audit) be exercised on the default path without the runtime
    gaining the ability to leave a file behind.
    """

    tool_id = WRITE_TOOL_ID
    tool_version = f"{WRITE_TOOL_VERSION}-dryrun"
    filesystem_write = False  # never opens a file for writing

    def write(self, target: Path, content: str) -> int:
        return len(content.encode("utf-8"))


class RealWorkspaceWriter:
    """Real writer — creates the file on disk.

    Requires the Safety-Flag Gate to be open (an integrity-checked local activation record
    enabling ``filesystem_write``). Inert unless explicitly selected; nothing here runs on
    the default MVP path.
    """

    tool_id = WRITE_TOOL_ID
    tool_version = WRITE_TOOL_VERSION
    provider_id = WRITE_TOOL_ID
    filesystem_write = True

    def __init__(self, *, authorization: Authorization | None = None):
        # Write authorization from the Safety-Flag Gate. Without it, write() refuses to
        # touch disk — so a directly-constructed writer cannot bypass the gate.
        self._authorization = authorization

    def write(self, target: Path, content: str) -> int:
        # Chokepoint: re-verify authorization at the moment of the write (defense in depth).
        safety_gate.assert_authorization(
            self._authorization,
            required_flags=_WRITE_FLAGS,
            provider_id=self.provider_id,
            now=timeutil.utc_now_iso(),
        )
        data = content.encode("utf-8")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # "x" is the create-only guarantee at the syscall level: if the target appeared
            # between the caller's check and here, this raises rather than replacing it.
            with open(target, "x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError as exc:
            raise ToolBlocked("TARGET_EXISTS", "target file already exists; create-only write refused") from exc
        except OSError as exc:
            raise ToolError("WRITE_FAILED", f"could not write workspace file: {exc.strerror}") from exc
        return len(data)


def workspace_root(root: Path | None = None) -> Path:
    return (root if root is not None else _repo_root()) / WORKSPACE_REL


def resolve_target(relative_path: Any, *, root: Path | None = None) -> Path:
    """Resolve a caller-supplied path against the workspace, or fail closed.

    Rejects, each with its own reason code: a non-string/empty path, an over-long path, an
    absolute path or drive letter, ``..`` traversal, NUL/control characters, and any path
    that resolves outside the workspace (which is what catches symlink escape, since
    containment is checked on the *resolved* path).
    """
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ToolBlocked("EMPTY_PATH", "workspace path must be a non-empty string")
    if len(relative_path) > MAX_PATH_CHARS:
        raise ToolBlocked("PATH_TOO_LONG", f"workspace path exceeds {MAX_PATH_CHARS} characters")
    if any(ord(ch) < 32 for ch in relative_path):
        raise ToolBlocked("INVALID_PATH", "workspace path contains control characters")

    # PureWindowsPath deliberately, on every platform: it treats both separators and drive
    # letters as absolute, so a POSIX-looking "/etc/x" is rejected on Windows too (where
    # PurePosixPath-style checks would miss it). Strictest reading wins.
    candidate = PureWindowsPath(relative_path)
    if candidate.is_absolute() or candidate.drive or relative_path.startswith(("/", "\\")):
        raise ToolBlocked("ABSOLUTE_PATH", "workspace path must be relative to the workspace root")
    if ".." in candidate.parts:
        raise ToolBlocked("PATH_ESCAPE", "workspace path must not traverse outside the workspace")

    base = workspace_root(root)
    # Resolve the base to its real location first: comparing a resolved target against an
    # unresolved base would false-negative when the workspace itself sits behind a symlink.
    base_real = base.resolve()
    target = (base_real / candidate).resolve()
    if target != base_real and base_real not in target.parents:
        # Post-resolution containment: catches symlinked parents pointing out of the tree.
        raise ToolBlocked("PATH_ESCAPE", "workspace path resolves outside the workspace root")
    if target == base_real:
        raise ToolBlocked("INVALID_PATH", "workspace path must name a file, not the workspace root")
    return target


def _require_content(content: Any) -> str:
    if not isinstance(content, str):
        raise ToolBlocked("INVALID_CONTENT", "workspace content must be a string")
    if not content:
        raise ToolBlocked("EMPTY_CONTENT", "workspace content must not be empty")
    size = len(content.encode("utf-8"))
    if size > MAX_CONTENT_BYTES:
        raise ToolBlocked("CONTENT_TOO_LARGE", f"workspace content exceeds {MAX_CONTENT_BYTES} bytes")
    return content


def run_write(
    relative_path: str,
    content: str,
    *,
    writer: WorkspaceWriter,
    now: str,
    root: Path | None = None,
    control_store: ControlStore | None = None,
) -> tuple[WriteResult, dict[str, Any]]:
    """Perform one controlled workspace write. Returns ``(result, write_record)``.

    Fails closed (``ToolBlocked``) on an invalid/escaping path, invalid or oversized
    content, an existing target (create-only), or a runtime that is not ACTIVE
    (``kill_blocks: tool_write``). The record carries the target, byte count, and content
    hash — never the content itself.
    """
    target = resolve_target(relative_path, root=root)
    content = _require_content(content)

    # kill_blocks: tool_write — a paused or killed runtime must not leave an artifact.
    store = control_store if control_store is not None else ControlStore(root or _repo_root())
    state = store.load()
    if not state.execution_allowed:
        raise ToolBlocked(
            "KILL_SWITCH_ACTIVE",
            f"runtime is {state.mode}; kill_blocks tool_write forbids the write",
        )

    if target.exists():
        raise ToolBlocked("TARGET_EXISTS", "target file already exists; create-only write refused")

    bytes_written = writer.write(target, content)

    normalized = target.relative_to(workspace_root(root).resolve()).as_posix()
    # Hash the content itself (not a record wrapper) so the digest identifies the bytes on
    # disk, and the content never has to appear in a record to be verifiable.
    content_sha256 = integrity.sha256_record({"content": content})
    record = {
        "tool_id": writer.tool_id,
        "tool_version": writer.tool_version,
        "tool_class": WRITE_TOOL_CLASS,
        "operation": "create",
        "workspace_root": WORKSPACE_REL,
        "relative_path": normalized,
        "target_ref": f"workspace:{normalized}",
        "bytes_written": int(bytes_written),
        "content_sha256": content_sha256,
        "create_only": True,
        "overwrote_existing": False,  # structurally impossible: create-only
        "reversible": True,           # reversible by deleting what was created
        "external_action": False,
        # Whether this write actually touched disk (dry-run=False, real writer=True).
        "filesystem_write": bool(getattr(writer, "filesystem_write", False)),
        "created_at": now,
    }
    result = WriteResult(
        relative_path=normalized,
        bytes_written=int(bytes_written),
        content_sha256=content_sha256,
        created=bool(getattr(writer, "filesystem_write", False)),
    )
    return result, record


def select_writer(*, now: str | None = None, root: Path | None = None) -> WorkspaceWriter:
    """Choose the workspace writer — the enforced Safety-Flag Gate chokepoint.

    Defaults to :class:`DryRunWriter` (no gate needed; it touches nothing). The real
    disk-writing writer is returned ONLY when both (a) the caller opts in via
    ``MVP_WORKSPACE_WRITER=real`` AND (b) the Safety-Flag Gate authorizes
    ``filesystem_write`` against a local, integrity-checked activation record. The env var
    alone is NOT sufficient: with no valid activation this fails closed
    (:class:`SafetyGateBlocked`) rather than silently opening a write path.

    The writer analog of ``providers.select_provider`` and ``tools.select_search_tool``;
    all four share ``safety_gate.select_gated``, which is what makes "authorize before the
    capable thing is constructed" structural rather than remembered.
    """
    return safety_gate.select_gated(
        env_var=WRITER_ENV,
        opt_in_value=REAL_WRITER,
        flags=_WRITE_FLAGS,
        provider_id=WRITE_TOOL_ID,
        default_factory=DryRunWriter,
        gated_factory=lambda authorization: RealWorkspaceWriter(authorization=authorization),
        now=now,
        root=root,
    )
