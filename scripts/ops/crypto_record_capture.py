"""Capture what the crypto lane's tests write, so two trees can be compared record for record.

Crypto PR7a; the directive's CI-3 (deterministic integration) and CI-4 (historical replay). PR7 moves
code between modules and must not change trading behaviour (directive §9). A green suite says every
assertion still holds. It does not say that a record nobody asserts on is still the same, and this
does: it runs the lane's tests with pytest's ``--basetemp`` inside OUT, so every file a test writes
under ``tmp_path`` is kept, and hashes each file after normalising it:

- JSON is parsed and re-dumped with sorted keys, and JSONL line by line;
- the capture's own temp root, which records embed as absolute paths, becomes ``<BASETEMP>``.

``capture`` runs the tests twice. Whatever differs between the two runs on one tree carries a wall
clock or a random id, and ``compare`` masks exactly that: the JSON fields that differed between a
tree's own two runs (a ``recorded_at``, an approval id) are set aside on both sides, and every other
field must match. A file whose shape differs between runs is skipped whole. ``compare`` says how many
fields and files it set aside.

    python scripts/ops/crypto_record_capture.py capture OUT [TEST ...]
    python scripts/ops/crypto_record_capture.py compare BASE HEAD

Run ``capture`` once on the base tree and once on the head, then ``compare``. A test that writes
outside ``tmp_path`` is not seen. Nothing here reads or writes runtime state: the tests run on temp
roots, as they do in the suite.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

DEFAULT_TESTS = ("tests/test_mvp_runtime_crypto_*.py", "tests/test_crypto_*.py")
TESTS_FILE = "tests.json"
PLACEHOLDER = "<BASETEMP>"


def normalise(path: Path, basetemp: Path) -> bytes:
    """The bytes to hash: key order and the capture's own temp root do not count as a difference."""
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw
    text = text.replace(str(basetemp), PLACEHOLDER)
    if path.suffix == ".json":
        try:
            return json.dumps(json.loads(text), sort_keys=True, ensure_ascii=False).encode("utf-8")
        except ValueError:
            return text.encode("utf-8")
    if path.suffix == ".jsonl":
        lines = []
        for line in text.splitlines():
            try:
                lines.append(json.dumps(json.loads(line), sort_keys=True, ensure_ascii=False))
            except ValueError:
                lines.append(line)
        return "\n".join(lines).encode("utf-8")
    return text.encode("utf-8")


_MASK = "<UNSTABLE>"


def value(path: Path, basetemp: Path) -> Any:
    """A file as data: JSON parsed, JSONL a list of lines (each parsed where it parses), anything else
    its normalised text."""
    data = normalise(path, basetemp)
    if path.suffix == ".json":
        try:
            return json.loads(data)
        except ValueError:
            return data
    if path.suffix == ".jsonl":
        lines: list[Any] = []
        for line in data.decode("utf-8").splitlines():
            try:
                lines.append(json.loads(line))
            except ValueError:
                lines.append(line)
        return lines
    return data


def unstable_paths(a: Any, b: Any, at: tuple = ()) -> set[tuple]:
    """Where two runs of one tree differ: the leaf paths, or the subtree whose shape changed."""
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return set().union(*(unstable_paths(a[k], b[k], at + (k,)) for k in a)) if a else set()
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return set().union(*(unstable_paths(x, y, at + (i,)) for i, (x, y) in enumerate(zip(a, b)))) if a else set()
    return set() if a == b else {at}


def masked(data: Any, paths: set[tuple], at: tuple = ()) -> Any:
    if at in paths:
        return _MASK
    if isinstance(data, dict):
        return {k: masked(v, paths, at + (k,)) for k, v in data.items()}
    if isinstance(data, list):
        return [masked(v, paths, at + (i,)) for i, v in enumerate(data)]
    return data


def _files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def diff(base: Path, head: Path) -> dict[str, Any]:
    """Compare two captures (each OUT with run1 and run2) file by file, masking what either tree's own
    two runs disagree on."""
    runs = {name: (out / "run1", out / "run2") for name, out in (("base", base), ("head", head))}
    present = {name: (_files(r1), _files(r2)) for name, (r1, r2) in runs.items()}
    stable = {name: f1 & f2 for name, (f1, f2) in present.items()}
    wobbling = {p for f1, f2 in present.values() for p in f1 ^ f2}
    result: dict[str, Any] = {"only_in_base": sorted(stable["base"] - _files(runs["head"][0]) - wobbling),
                              "only_in_head": sorted(stable["head"] - _files(runs["base"][0]) - wobbling),
                              "changed": [], "skipped_unstable": sorted(wobbling), "masked_fields": 0}
    for rel in sorted(stable["base"] & stable["head"]):
        (b1, b2), (h1, h2) = ([value(r / rel, r) for r in runs[name]] for name in ("base", "head"))
        mask = unstable_paths(b1, b2) | unstable_paths(h1, h2)
        if () in mask:
            result["skipped_unstable"].append(rel)
            continue
        result["masked_fields"] += len(mask)
        if masked(b1, mask) != masked(h1, mask):
            result["changed"].append(rel)
    result["skipped_unstable"].sort()
    return result


def _expand(patterns: Sequence[str]) -> list[str]:
    found: list[str] = []
    for pattern in patterns:
        matches = sorted(str(p) for p in Path(".").glob(pattern)) if any(c in pattern for c in "*?[") else [pattern]
        found.extend(m for m in matches if m not in found)
    return found


def capture(out: Path, patterns: Sequence[str]) -> int:
    tests = _expand(patterns)
    if not tests:
        print("no tests matched", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)      # pytest makes --basetemp itself, but not its parent
    for run in ("run1", "run2"):
        basetemp = (out / run).resolve()
        code = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               f"--basetemp={basetemp}", *tests]).returncode
        if code != 0:
            print(f"the tests failed on {run} (exit {code}); a failing tree has no record to compare",
                  file=sys.stderr)
            return code
    (out / TESTS_FILE).write_text(json.dumps(tests, indent=1), encoding="utf-8")
    print(f"captured {len(_files((out / 'run1').resolve()))} files from {len(tests)} test files, twice")
    return 0


def compare(base: Path, head: Path) -> int:
    tests = [json.loads((out / TESTS_FILE).read_text(encoding="utf-8")) for out in (base, head)]
    if tests[0] != tests[1]:
        print("the two captures ran different test files; capture both with the same list", file=sys.stderr)
        return 2
    result = diff(base.resolve(), head.resolve())
    for key in ("only_in_base", "only_in_head", "changed"):
        for path in result[key]:
            print(f"{key}: {path}")
    differing = sum(len(result[k]) for k in ("only_in_base", "only_in_head", "changed"))
    print(f"{differing} differing; set aside: {len(result['skipped_unstable'])} unstable files, "
          f"{result['masked_fields']} unstable fields")
    return 1 if differing else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="run the tests twice, into OUT/run1 and OUT/run2")
    cap.add_argument("out", type=Path)
    cap.add_argument("tests", nargs="*", default=list(DEFAULT_TESTS))
    cmp_ = sub.add_parser("compare", help="compare two captures; exit 1 on any stable difference")
    cmp_.add_argument("base", type=Path)
    cmp_.add_argument("head", type=Path)
    args = parser.parse_args(argv)
    if args.command == "capture":
        return capture(args.out, args.tests)
    return compare(args.base, args.head)


if __name__ == "__main__":
    sys.exit(main())
