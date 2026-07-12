#!/usr/bin/env python3
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FILES = [
    "THOMAS_CORE/THOMAS_IDENTITY.md",
    "THOMAS_CORE/THOMAS_VALUES.yaml",
    "THOMAS_CORE/THOMAS_GOALS.yaml",
    "THOMAS_CORE/THOMAS_DECISION_MODEL.yaml",
    "THOMAS_CORE/THOMAS_PREFERENCE_PROFILE.yaml",
    "THOMAS_CORE/README.md",
    "docs/MVP_OPERATING_POLICY.md",
    "03_ROLE_CONTRACTS/ROLE_DEFINITION_TEMPLATE.yaml",
]


def digest(root: Path) -> dict[str, str]:
    return {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in FILES
    }


def load_apply_module(target: Path):
    script = target / "scripts/apply_thomas_core_release_candidate.py"
    spec = importlib.util.spec_from_file_location(
        "thomas_core_apply_idempotency_target",
        script,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load Core apply module")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.ROOT = target
    return module


def main() -> int:
    missing = [rel for rel in FILES if not (ROOT / rel).exists()]
    if missing:
        print("FAIL: Apply idempotency test cannot run; files missing")
        for rel in missing:
            print(f" - {rel}")
        return 1

    with tempfile.TemporaryDirectory() as temp:
        target = Path(temp) / "repo"

        for rel in FILES + ["scripts/apply_thomas_core_release_candidate.py"]:
            src = ROOT / rel
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

        module = load_apply_module(target)
        captured = io.StringIO()

        try:
            with contextlib.redirect_stdout(captured):
                module.main()
            first_hashes = digest(target)

            with contextlib.redirect_stdout(captured):
                module.main()
            second_hashes = digest(target)
        except Exception as exc:
            print(captured.getvalue())
            print(f"FAIL: Core apply execution failed: {exc}")
            return 1

        changed = [
            rel
            for rel in FILES
            if first_hashes[rel] != second_hashes[rel]
        ]

        if changed:
            print("FAIL: Core apply is not idempotent")
            for rel in changed:
                print(f" - {rel}")
            return 1

    print("PASS: Core apply idempotency test completed")
    print("Applying the Core projection twice produces identical output hashes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
