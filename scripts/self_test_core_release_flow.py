#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from lib.core_release_verifier import (
    CoreReleaseVerificationError,
    verify_activation_record,
    verify_current_pointer,
    verify_manifest,
)
from lib.git_provenance import require_file_tracked_at_head
from lib.safe_io import SafeIOError, safe_repo_path

ROOT = Path(__file__).resolve().parents[1]
GIT_EXECUTABLE = os.environ.get("THOMAS_GIT") or shutil.which("git") or "git"


def run(command: list[str], cwd: Path, *, expect_success: bool = True, timeout: int = 600) -> str:
    print("$ " + " ".join(command), flush=True)
    proc = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    output = (proc.stdout + proc.stderr).strip()
    if output:
        print(output, flush=True)
    if expect_success and proc.returncode != 0:
        print(output)
        raise RuntimeError("Command failed: " + " ".join(command))
    if not expect_success and proc.returncode == 0:
        print(output)
        raise RuntimeError("Negative command unexpectedly passed: " + " ".join(command))
    return output




def run_stream(command: list[str], cwd: Path, *, timeout: int = 600) -> None:
    print("$ " + " ".join(command), flush=True)
    proc = subprocess.run(
        command,
        cwd=cwd,
        timeout=timeout,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    if proc.returncode != 0:
        raise RuntimeError("Command failed: " + " ".join(command))


def copy_repository(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    owned = [
        "THOMAS_CORE", "03_ROLE_CONTRACTS", "05_REGISTRIES", "docs", "schemas",
        "scripts", "runtime", "governance", "programs", "tools", "examples", "tests",
        "deferred", "generated", "historical",
        "requirements-validation.in", "requirements-validation.lock",
        ".gitattributes", ".github",
    ]

    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name for name in names
            if name in {
                ".git", "__pycache__", ".pytest_cache", "releases", "approvals",
                "activations", "revocations", "deactivations", ".runtime_locks",
            }
        }

    for rel in owned:
        src = source / rel
        if not src.exists():
            continue
        dst = target / rel
        if src.is_dir():
            shutil.copytree(src, dst, ignore=ignore)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    for rel in ["THOMAS_CORE/REVIEW_CORE_RELEASE.yaml", "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"]:
        path = target / rel
        if path.exists():
            path.unlink()


def write_yaml(root: Path, rel: str, data) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=120),
        encoding="utf-8",
    )


def ensure_detailed_core(root: Path) -> None:
    identity = root / "THOMAS_CORE/THOMAS_IDENTITY.md"
    if identity.exists():
        return

    identity.write_text(
        "# Thomas Identity\n\nStatus: Initial Draft\nCore Version: 0.1.0\n\n"
        "## 1. Identity\n\nThomas는 시스템형 사업가다.\n",
        encoding="utf-8",
    )
    write_yaml(root, "THOMAS_CORE/THOMAS_VALUES.yaml", {
        "schema_version": "thomas_values.v0.1",
        "version": "0.1.0",
        "owner": {"name": "Thomas"},
        "value_system": {"core_values": {"continuous_improvement": {
            "priority": 9, "status": "approved", "definition": "지속적으로 개선한다."
        }}},
        "business_opportunity_priority": {"business_value_hierarchy": [
            "수익 가능성", "위험 대비 기대 가치", "확장 가능성", "자동화 가능성", "장기 성장 가능성"
        ]},
    })
    write_yaml(root, "THOMAS_CORE/THOMAS_GOALS.yaml", {
        "schema_version": "thomas_goals.v0.1", "version": "0.1.0",
        "goal_owner": {"name": "Thomas"},
        "vision": {"status": "approved", "statement": "AI와 자동화 시스템으로 지속적인 가치를 만든다."},
    })
    write_yaml(root, "THOMAS_CORE/THOMAS_DECISION_MODEL.yaml", {
        "schema_version": "thomas_decision_model.v0.1", "version": "0.1.0",
        "default_decision_patterns": {},
    })
    write_yaml(root, "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml", {
        "schema_version": "thomas_preference_profile.v0.1", "version": "0.1.0",
        "learning_preferences": {
            "allowed_automatic_learning": ["reporting_format", "tool_selection"],
            "approval_required": ["core_identity_change", "permission_expansion"],
        },
    })
    (root / "THOMAS_CORE/THOMAS_REVENUE_PREFERENCE_MODEL.yaml").write_text(
        "schema_version: thomas_revenue_preference_model.v0.1\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (root / "THOMAS_CORE/MVP_CORE_SCOPE.md").write_text("# MVP Core Scope\n", encoding="utf-8")
    (root / "THOMAS_CORE/README.md").write_text(
        """# THOMAS_CORE

Status: Initial Draft
Core Version: 0.1.0

## Files

| File | Purpose |
| --- | --- |
| `CORE_METADATA.yaml` | Core metadata |
| `THOMAS_IDENTITY.md` | Thomas identity |
| `MVP_ACTIVE_CORE.yaml` | Active Core |

## Runtime Rule

Thomas Core is protected.

## MVP Use

For the first agent organization MVP, do not load every detailed rule as an active runtime rule.

Use only the eight rules in `MVP_ACTIVE_CORE.yaml`:

1. Thomas는 시스템형 사업가다.
2. 특정 사업 분야를 아직 고정하지 않는다.
3. 공통 Agent 조직을 먼저 만든다.
4. 기회는 발견 후 작은 검증을 거친다.
5. 사업 기회는 수익 가능성을 먼저 본다.
6. 반복 업무는 Program, 판단 업무는 Agent가 맡는다.
7. 고위험 행동은 Thomas 승인이 필요하다.
8. 보고는 결론, 이유, 리스크, 다음 행동 중심으로 한다.

Keep detailed scoring as reference material.
""",
        encoding="utf-8",
    )
    (root / "docs/MVP_OPERATING_POLICY.md").write_text(
        "# MVP Operating Policy\n\n# 14. Learning Policy\n\n기존 학습 정책.\n\n# 15. Audit Policy\n\n기존 감사 정책.\n",
        encoding="utf-8",
    )


def git_commit(root: Path, message: str) -> None:
    run([GIT_EXECUTABLE, "add", "."], root, timeout=120)
    run([GIT_EXECUTABLE, "commit", "-m", message], root, timeout=120)


def parse_value(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip()
    raise RuntimeError(f"Output missing prefix: {prefix}")


def sha_source(value: str) -> str:
    return "sha256:" + hashlib.sha256(value.encode("utf-8")).hexdigest()


def main() -> int:
    with tempfile.TemporaryDirectory() as temp:
        test_root = Path(temp) / "repo"
        copy_repository(ROOT, test_root)
        ensure_detailed_core(test_root)
        python = sys.executable

        run([python, "scripts/apply_thomas_core_release_candidate.py"], test_root)
        run_stream([python, "scripts/run_repository_release_gate.py"], test_root)

        run_stream(
            [python, "scripts/build_core_release_manifest.py", "--built-by", "I0.4.1 Lean self-test"],
            test_root,
        )
        review = yaml.safe_load(
            (test_root / "THOMAS_CORE/REVIEW_CORE_RELEASE.yaml").read_text(encoding="utf-8")
        )
        manifest_rel = review["manifest_path"]

        manifest_path = test_root / manifest_rel
        verify_manifest(test_root, manifest_path)

        # Historical Release must remain valid after working-tree Core and validator changes.
        mutable_core = test_root / "THOMAS_CORE/MVP_ACTIVE_CORE.yaml"
        mutable_validator = test_root / "scripts/validate_thomas_core.py"
        original_core = mutable_core.read_text(encoding="utf-8")
        original_validator = mutable_validator.read_text(encoding="utf-8")
        mutable_core.write_text(original_core + "\n# future-core-development\n", encoding="utf-8")
        mutable_validator.write_text(original_validator + "\n# future-validator-development\n", encoding="utf-8")
        verify_manifest(test_root, manifest_path)
        mutable_core.write_text(original_core, encoding="utf-8")
        mutable_validator.write_text(original_validator, encoding="utf-8")

        try:
            safe_repo_path(test_root, "../outside.yaml", must_exist=True)
        except (SafeIOError, FileNotFoundError):
            pass
        else:
            raise RuntimeError("Path traversal unexpectedly passed")

        run([GIT_EXECUTABLE, "init"], test_root, timeout=60)
        run([GIT_EXECUTABLE, "config", "user.email", "self-test@example.com"], test_root, timeout=60)
        run([GIT_EXECUTABLE, "config", "user.name", "Thomas Agent Self-Test"], test_root, timeout=60)
        git_commit(test_root, "Build immutable Core Release snapshot")

        approval_output = run(
            [
                python, "scripts/approve_core_release.py",
                "--manifest", manifest_rel,
                "--approved-by", "Thomas",
                "--approval-ref", "i0.4.1-runtime-approval",
                "--reason", "Approve exact self-test Release for Runtime Core reference.",
                "--approval-source-type", "signed_git_commit",
                "--approval-source-id", "i0.4.1-runtime-approval",
                "--approval-source-hash", sha_source("runtime-approval"),
                "--identity-verification-method", "isolated_test_signature",
                "--verification-status", "verified_by_signature",
            ],
            test_root,
        )
        approval_rel = parse_value(approval_output, "Approval path:")
        git_commit(test_root, "Record verified Core Approval")

        activation_output = run(
            [
                python, "scripts/activate_core_release.py",
                "--activation-type", "activate",
                "--manifest", manifest_rel,
                "--approval", approval_rel,
                "--activated-by", "I0.4.1 self-test",
                "--activation-ref", "i0.4.1-activate",
                "--reason", "Activate exact approved Release for new Task Bindings.",
                "--source-type", "signed_git_commit",
                "--source-id", "i0.4.1-activate",
                "--source-hash", sha_source("activate"),
                "--identity-verification-method", "isolated_test_signature",
                "--verification-status", "verified_by_signature",
            ],
            test_root,
        )
        activation_id = parse_value(activation_output, "Activation ID:")
        activation_rel = f"THOMAS_CORE/activations/{activation_id}.yaml"
        current_rel = "THOMAS_CORE/CURRENT_CORE_RELEASE.yaml"
        git_commit(test_root, "Activate Core Release and update Current pointer")

        activation_path = test_root / activation_rel
        current_path = test_root / current_rel
        verify_activation_record(test_root, activation_path)
        verify_current_pointer(test_root, current_path)
        require_file_tracked_at_head(test_root, current_path)

        # Bind an actual RECEIVED Task and reject unknown Rule membership.
        task = yaml.safe_load(
            (test_root / "examples/tasks/task_v0.3_received_unbound.yaml").read_text(encoding="utf-8")
        )
        task["identity"]["task_id"] = "task-i0-4-1-self-test"
        task["identity"]["trace_id"] = "trace-i0-4-1-self-test"
        task["identity"]["root_task_id"] = "task-i0-4-1-self-test"
        task["context"]["active_core_rule_ids"] = ["MVP_RULE_001", "MVP_RULE_009", "MVP_RULE_013"]
        input_rel = "runtime/tasks/task-i0-4-1-self-test.received.yaml"
        bound_rel = "runtime/tasks/task-i0-4-1-self-test.bound.yaml"
        binding_rel = "runtime/core_context/task-i0-4-1-self-test.binding.yaml"
        write_yaml(test_root, input_rel, task)
        run(
            [python, "scripts/create_core_context_binding.py", "--task-file", input_rel,
             "--binding-output", binding_rel, "--updated-task-output", bound_rel],
            test_root,
        )
        bound_task = yaml.safe_load((test_root / bound_rel).read_text(encoding="utf-8"))
        binding = yaml.safe_load((test_root / binding_rel).read_text(encoding="utf-8"))
        if bound_task["context"]["core_context_binding_id"] != binding["identity"]["core_context_binding_id"]:
            raise RuntimeError("Task and Binding IDs differ")
        if "artifacts" in binding or "active_rule_ids" in binding.get("rules", {}):
            raise RuntimeError("Lean Binding duplicated Release Manifest content")

        bad_task = yaml.safe_load(yaml.safe_dump(task))
        bad_task["identity"]["task_id"] = "task-unknown-rule"
        bad_task["identity"]["trace_id"] = "trace-unknown-rule"
        bad_task["identity"]["root_task_id"] = "task-unknown-rule"
        bad_task["context"]["active_core_rule_ids"] = ["MVP_RULE_" + "999"]
        bad_rel = "runtime/tasks/task-unknown-rule.yaml"
        write_yaml(test_root, bad_rel, bad_task)
        run(
            [python, "scripts/create_core_context_binding.py", "--task-file", bad_rel,
             "--binding-output", "runtime/core_context/invalid.yaml",
             "--updated-task-output", "runtime/tasks/invalid.yaml"],
            test_root,
            expect_success=False,
        )

        # Runtime Task/Binding artifacts above are isolated test data. Remove them
        # before governance lifecycle operations, which correctly require a clean
        # committed Repository state.
        shutil.rmtree(test_root / "runtime/tasks", ignore_errors=True)
        shutil.rmtree(test_root / "runtime/core_context", ignore_errors=True)

        # Deactivate fail closed in one operation and one commit.
        deactivation_output = run(
            [
                python, "scripts/deactivate_core_release.py",
                "--deactivated-by", "I0.4.1 self-test",
                "--deactivation-ref", "i0.4.1-deactivate",
                "--reason", "Test fail-closed deactivation.",
                "--source-type", "signed_git_commit",
                "--source-id", "i0.4.1-deactivate",
                "--source-hash", sha_source("deactivate"),
                "--identity-verification-method", "isolated_test_signature",
                "--verification-status", "verified_by_signature",
            ],
            test_root,
        )
        deactivation_id = parse_value(deactivation_output, "Deactivation ID:")
        git_commit(test_root, "Deactivate Core Release fail closed")
        verify_current_pointer(test_root, current_path)
        require_file_tracked_at_head(test_root, current_path)

        deactivated_task = yaml.safe_load(yaml.safe_dump(task))
        deactivated_task["identity"]["task_id"] = "task-deactivated-must-fail"
        deactivated_task["identity"]["trace_id"] = "trace-deactivated-must-fail"
        deactivated_task["identity"]["root_task_id"] = "task-deactivated-must-fail"
        deactivated_task["context"]["active_core_rule_ids"] = ["MVP_RULE_001"]
        deactivated_task_rel = "runtime/tasks/task-deactivated-must-fail.yaml"
        write_yaml(test_root, deactivated_task_rel, deactivated_task)

        run(
            [python, "scripts/create_core_context_binding.py", "--task-file", deactivated_task_rel,
             "--binding-output", "runtime/core_context/deactivated.yaml",
             "--updated-task-output", "runtime/tasks/deactivated.yaml"],
            test_root,
            expect_success=False,
        )
        shutil.rmtree(test_root / "runtime/tasks", ignore_errors=True)
        shutil.rmtree(test_root / "runtime/core_context", ignore_errors=True)

        # Roll back through the same Activation command.
        rollback_output = run(
            [
                python, "scripts/activate_core_release.py",
                "--activation-type", "rollback",
                "--manifest", manifest_rel,
                "--approval", approval_rel,
                "--activated-by", "I0.4.1 self-test",
                "--activation-ref", "i0.4.1-rollback",
                "--reason", "Test verified rollback.",
                "--source-type", "signed_git_commit",
                "--source-id", "i0.4.1-rollback",
                "--source-hash", sha_source("rollback"),
                "--identity-verification-method", "isolated_test_signature",
                "--verification-status", "verified_by_signature",
            ],
            test_root,
        )
        rollback_id = parse_value(rollback_output, "Activation ID:")
        rollback_rel = f"THOMAS_CORE/activations/{rollback_id}.yaml"
        git_commit(test_root, "Rollback to approved Core Release")
        rollback_path = test_root / rollback_rel
        verify_activation_record(test_root, rollback_path)
        verify_current_pointer(test_root, current_path)
        require_file_tracked_at_head(test_root, current_path)

        # Effective Revocation invalidates the Current chain.
        run(
            [
                python, "scripts/revoke_core_approval.py",
                "--approval", approval_rel,
                "--activation", rollback_rel,
                "--revoked-by", "I0.4.1 self-test",
                "--revocation-ref", "i0.4.1-revoke",
                "--reason", "Test effective Approval revocation.",
                "--source-type", "signed_git_commit",
                "--source-id", "i0.4.1-revoke",
                "--source-hash", sha_source("revoke"),
                "--identity-verification-method", "isolated_test_signature",
                "--verification-status", "verified_by_signature",
            ],
            test_root,
        )
        git_commit(test_root, "Record effective Core Approval Revocation")
        try:
            verify_current_pointer(test_root, current_path)
        except CoreReleaseVerificationError:
            pass
        else:
            raise RuntimeError("Revoked Current Core unexpectedly verified")

    print("PASS: I0.4.1 Lean isolated end-to-end Core lifecycle self-test completed")
    print(
        "Validated self-contained historical Release, externally verified Approval evidence, one-step Activation/Current update, "
        "minimal Task-file Binding, unknown Rule block, one-step fail-closed Deactivation, rollback, Revocation, and path safety"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
