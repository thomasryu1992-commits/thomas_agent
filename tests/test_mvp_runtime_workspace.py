"""R8 controlled-write tests.

The write is the runtime's first EXECUTE_AND_REPORT action and its first effect outside
its own private state, so the tests concentrate on what must fail closed: path escape,
overwrite, a killed runtime, and an unauthorized writer. Every test writes into a tmp_path
workspace — none touches the real ``workspace/``.
"""

from __future__ import annotations

import json

import pytest

from runtime.mvp_runtime import control, workspace
from runtime.mvp_runtime.control import ControlState, ControlStore
from runtime.mvp_runtime.errors import SafetyGateBlocked, ToolBlocked, ToolError
from runtime.mvp_runtime.safety_gate import FILESYSTEM_WRITE, Authorization
from runtime.mvp_runtime.workspace import (
    WRITER_ENV,
    DryRunWriter,
    RealWorkspaceWriter,
    resolve_target,
    run_write,
    select_writer,
    workspace_root,
)

NOW = "2026-07-16T09:00:00Z"

# A granted write authorization (as select_writer would produce after the gate passes).
_AUTH = Authorization(
    flags=(FILESYSTEM_WRITE,),
    provider_id="workspace.writer",
    activation_sha256="sha256:test",
    expires_at="2999-01-01T00:00:00Z",
    evidence_ref=".runtime_governance_state/evidence.md",
)


@pytest.fixture
def root(tmp_path):
    """A repo root whose workspace/ is empty and whose control state is ACTIVE."""
    (tmp_path / "workspace").mkdir()
    return tmp_path


def _active_store(root) -> ControlStore:
    return ControlStore(root)


# --- path confinement -------------------------------------------------------------


@pytest.mark.parametrize(
    "bad,reason",
    [
        ("../escape.txt", "PATH_ESCAPE"),
        ("a/../../../b.txt", "PATH_ESCAPE"),
        ("..\\..\\x.txt", "PATH_ESCAPE"),
        ("/etc/passwd", "ABSOLUTE_PATH"),
        ("C:/Windows/x.txt", "ABSOLUTE_PATH"),
        ("\\\\server\\share\\x", "ABSOLUTE_PATH"),
        ("", "EMPTY_PATH"),
        ("   ", "EMPTY_PATH"),
        ("sub/\x00evil.txt", "INVALID_PATH"),
        ("x" * 201, "PATH_TOO_LONG"),
    ],
)
def test_resolve_target_rejects_escaping_paths(bad, reason, root):
    with pytest.raises(ToolBlocked) as exc:
        resolve_target(bad, root=root)
    assert exc.value.reason_code == reason


def test_resolve_target_rejects_non_string(root):
    with pytest.raises(ToolBlocked) as exc:
        resolve_target(None, root=root)
    assert exc.value.reason_code == "EMPTY_PATH"


def test_resolve_target_rejects_the_workspace_root_itself(root):
    with pytest.raises(ToolBlocked) as exc:
        resolve_target(".", root=root)
    assert exc.value.reason_code == "INVALID_PATH"


def test_resolve_target_accepts_a_nested_relative_path(root):
    target = resolve_target("reports/2026/analysis.md", root=root)
    assert target.relative_to(workspace_root(root).resolve()).as_posix() == "reports/2026/analysis.md"


def test_resolve_target_rejects_symlink_escape(root, tmp_path):
    """Containment is checked on the RESOLVED path, so a symlinked parent cannot smuggle
    a write out of the workspace."""
    outside = tmp_path / "outside"
    outside.mkdir()
    link = workspace_root(root) / "escape_link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform/account")
    with pytest.raises(ToolBlocked) as exc:
        resolve_target("escape_link/pwned.txt", root=root)
    assert exc.value.reason_code == "PATH_ESCAPE"


# --- content validation -----------------------------------------------------------


@pytest.mark.parametrize(
    "content,reason",
    [
        ("", "EMPTY_CONTENT"),
        (None, "INVALID_CONTENT"),
        (123, "INVALID_CONTENT"),
    ],
)
def test_run_write_rejects_invalid_content(content, reason, root):
    with pytest.raises(ToolBlocked) as exc:
        run_write("a.md", content, writer=DryRunWriter(), now=NOW, root=root,
                  control_store=_active_store(root))
    assert exc.value.reason_code == reason


def test_run_write_rejects_oversized_content(root):
    # Built here, not in a parametrize id: a megabyte-long test id overflows the
    # PYTEST_CURRENT_TEST environment variable on Windows.
    oversized = "x" * (workspace.MAX_CONTENT_BYTES + 1)
    with pytest.raises(ToolBlocked) as exc:
        run_write("a.md", oversized, writer=DryRunWriter(), now=NOW, root=root,
                  control_store=_active_store(root))
    assert exc.value.reason_code == "CONTENT_TOO_LARGE"


# --- create-only ------------------------------------------------------------------


def test_run_write_refuses_to_overwrite_an_existing_file(root):
    existing = workspace_root(root) / "a.md"
    existing.write_text("original", encoding="utf-8")
    with pytest.raises(ToolBlocked) as exc:
        run_write("a.md", "replacement", writer=RealWorkspaceWriter(authorization=_AUTH),
                  now=NOW, root=root, control_store=_active_store(root))
    assert exc.value.reason_code == "TARGET_EXISTS"
    # The original bytes must survive untouched — that is the reversibility guarantee.
    assert existing.read_text(encoding="utf-8") == "original"


def test_real_writer_refuses_an_existing_target_even_if_it_appears_late(root):
    """The create-only guarantee is enforced at the syscall ("x" mode), not just by the
    caller's exists() check, so a file appearing in between cannot be clobbered."""
    target = workspace_root(root) / "late.md"
    target.write_text("original", encoding="utf-8")
    with pytest.raises(ToolBlocked) as exc:
        RealWorkspaceWriter(authorization=_AUTH).write(target, "replacement")
    assert exc.value.reason_code == "TARGET_EXISTS"
    assert target.read_text(encoding="utf-8") == "original"


# --- kill switch ------------------------------------------------------------------


@pytest.mark.parametrize("mode", [control.PAUSED, control.KILLED])
def test_run_write_is_blocked_while_not_active(mode, root):
    """kill_blocks: tool_write — a paused or killed runtime must not leave an artifact."""
    store = ControlStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(ControlState(mode=mode, updated_by="op", updated_at=NOW, reason="test").as_record()),
        encoding="utf-8",
    )
    with pytest.raises(ToolBlocked) as exc:
        run_write("a.md", "content", writer=RealWorkspaceWriter(authorization=_AUTH),
                  now=NOW, root=root, control_store=store)
    assert exc.value.reason_code == "KILL_SWITCH_ACTIVE"
    assert not (workspace_root(root) / "a.md").exists()


def test_run_write_is_blocked_when_control_state_is_corrupt(root):
    """A corrupt safety state reads as KILLED (fail-closed), so the write is refused."""
    store = ControlStore(root)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ToolBlocked) as exc:
        run_write("a.md", "content", writer=DryRunWriter(), now=NOW, root=root, control_store=store)
    assert exc.value.reason_code == "KILL_SWITCH_ACTIVE"


# --- the gate ---------------------------------------------------------------------


def test_select_writer_defaults_to_dry_run(monkeypatch, root):
    monkeypatch.delenv(WRITER_ENV, raising=False)
    writer = select_writer(now=NOW, root=root)
    assert isinstance(writer, DryRunWriter)
    assert writer.filesystem_write is False


def test_select_writer_fails_closed_without_an_activation_record(monkeypatch, root):
    """The env var alone must never open a write path."""
    monkeypatch.setenv(WRITER_ENV, "real")
    with pytest.raises(SafetyGateBlocked):
        select_writer(now=NOW, root=root)


def test_real_writer_refuses_to_touch_disk_without_authorization(root):
    """A directly-constructed writer cannot bypass the gate: it re-checks at egress."""
    target = workspace_root(root) / "a.md"
    with pytest.raises(SafetyGateBlocked):
        RealWorkspaceWriter(authorization=None).write(target, "content")
    assert not target.exists()


def test_real_writer_refuses_an_authorization_for_another_provider(root):
    wrong = Authorization(
        flags=(FILESYSTEM_WRITE,), provider_id="brave_search",
        activation_sha256="sha256:test", expires_at="2999-01-01T00:00:00Z",
        evidence_ref=".runtime_governance_state/evidence.md",
    )
    target = workspace_root(root) / "a.md"
    with pytest.raises(SafetyGateBlocked):
        RealWorkspaceWriter(authorization=wrong).write(target, "content")
    assert not target.exists()


# --- the happy paths --------------------------------------------------------------


def test_dry_run_write_records_the_write_without_touching_disk(root):
    result, record = run_write("reports/a.md", "hello", writer=DryRunWriter(), now=NOW,
                               root=root, control_store=_active_store(root))
    assert not (workspace_root(root) / "reports/a.md").exists()
    assert result.created is False
    assert result.bytes_written == 5
    assert record["filesystem_write"] is False
    assert record["relative_path"] == "reports/a.md"
    assert record["target_ref"] == "workspace:reports/a.md"
    assert record["create_only"] is True
    assert record["overwrote_existing"] is False
    assert record["reversible"] is True
    assert record["external_action"] is False


def test_real_write_creates_the_file_and_records_it(root):
    result, record = run_write("reports/a.md", "hello", writer=RealWorkspaceWriter(authorization=_AUTH),
                               now=NOW, root=root, control_store=_active_store(root))
    target = workspace_root(root) / "reports/a.md"
    assert target.read_text(encoding="utf-8") == "hello"
    assert result.created is True
    assert record["filesystem_write"] is True
    assert record["bytes_written"] == 5


def test_write_record_carries_a_content_hash_but_never_the_content(root):
    """Secrets are metadata-only: the record must identify the bytes without embedding them."""
    secret_ish = "the quick brown fox jumped over the lazy dog"
    _, record = run_write("a.md", secret_ish, writer=DryRunWriter(), now=NOW, root=root,
                          control_store=_active_store(root))
    serialized = json.dumps(record)
    assert secret_ish not in serialized
    assert record["content_sha256"].startswith("sha256:")


def test_write_creates_missing_parent_directories(root):
    run_write("deep/nested/dir/a.md", "hello", writer=RealWorkspaceWriter(authorization=_AUTH),
              now=NOW, root=root, control_store=_active_store(root))
    assert (workspace_root(root) / "deep/nested/dir/a.md").is_file()


def test_dry_run_and_real_writers_agree_on_everything_but_the_effect(root):
    """The dry run must be a faithful preview: same path, same size, same hash."""
    dry_result, dry_record = run_write("a.md", "hello", writer=DryRunWriter(), now=NOW,
                                       root=root, control_store=_active_store(root))
    real_result, real_record = run_write("a.md", "hello", writer=RealWorkspaceWriter(authorization=_AUTH),
                                         now=NOW, root=root, control_store=_active_store(root))
    assert dry_result.relative_path == real_result.relative_path
    assert dry_result.bytes_written == real_result.bytes_written
    assert dry_result.content_sha256 == real_result.content_sha256
    assert dry_record["filesystem_write"] != real_record["filesystem_write"]


def test_write_failure_surfaces_as_a_tool_error(root, monkeypatch):
    """An OS-level failure must not masquerade as success."""
    class _Boom(RealWorkspaceWriter):
        def write(self, target, content):
            raise ToolError("WRITE_FAILED", "disk on fire")

    with pytest.raises(ToolError) as exc:
        run_write("a.md", "hello", writer=_Boom(authorization=_AUTH), now=NOW, root=root,
                  control_store=_active_store(root))
    assert exc.value.reason_code == "WRITE_FAILED"
