"""Capture what the crypto lane's tests write, so two commits can be compared record by record.

Crypto PR7a. PR7 moves code between modules and must not change trading behaviour (directive §9); this
is the evidence each step carries, beside the suite. A green suite says every assertion still holds.
This says that every record the tests write to disk is still the same, including the fields no
assertion reads.

``capture`` runs the tests twice with pytest's ``--basetemp`` inside OUT, keeping every file a test
writes under ``tmp_path``. ``compare`` reads two captures file by file:

- JSON and JSONL are compared value by value after sorting keys and replacing the capture's own temp
  root with ``<BASETEMP>``. A value is its JSON spelling, so ``true``, ``1`` and ``1.0`` differ, and so
  do ``-0.0`` and ``0.0``, while ``NaN`` equals itself. Any other file is compared as text or bytes.
- What differs between one commit's own two runs is a wall clock or a random id, and is set aside, but
  only where both commits vary the same way. A field, or a file, that varies in one commit's runs and
  not the other's is a difference: that is how a refactor that starts reading the wall clock shows up.
  ``compare`` names the fields. A measured duration (the scheduler's ``duration_ms``, and the event
  hash over it) varies between some runs and not others, so it can show up here by chance. Read those
  lines rather than trusting the exit code.
- A record nested too deep to walk (the lane writes 5,000-deep records on purpose, to prove they are
  refused) is compared whole.

    python scripts/ops/crypto_record_capture.py capture BASE_OUT [TEST ...]
    python scripts/ops/crypto_record_capture.py capture HEAD_OUT --same-tests-as BASE_OUT
    python scripts/ops/crypto_record_capture.py compare BASE_OUT HEAD_OUT

Two rules make the comparison mean something:

- **One worktree, two commits.** Check out the base, capture it, check out the head, capture it. The
  Core activation is per worktree, and every id bound to it (a ``core_context_binding_id``, and the
  approval ids and fingerprints derived from it) differs between worktrees. Measured on PR7a: two
  worktrees at the same code differed in 121 files, one worktree in none.
- **The same tests, in the same order.** A capture records the test ids it ran, and the head runs
  exactly those (``--same-tests-as``); ``compare`` refuses two captures of different tests. A test's
  temp directories are numbered by the order tests run in, so one added test renumbers every
  directory after it.

What it does not see: anything a test keeps in memory (the scripted adapters' order requests among
them), anything written outside ``tmp_path``, tests outside the list, and byte-level layout (key order,
whitespace, blank lines). The default list is every test file that imports the crypto lane. Nothing
here reads or writes runtime state: the tests run on temp roots, as they do in the suite.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

IDS_FILE = "test_ids.txt"
PLACEHOLDER = "<BASETEMP>"
_MASK = "<UNSTABLE>"
_LANE_MARKERS = ("mvp_runtime.crypto", "mvp_runtime import crypto")


def normalise(path: Path, basetemp: Path) -> bytes:
    """A file's bytes, with the capture's own temp root replaced and JSON keys sorted."""
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


def value(path: Path, basetemp: Path) -> Any:
    """A file as data: JSON parsed, JSONL a list of lines (each parsed where it parses), anything else
    its normalised bytes."""
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


def same(a: Any, b: Any) -> bool:
    """Equal as written: type-strict, where Python's ``True == 1 == 1.0`` and ``nan != nan`` are not."""
    if isinstance(a, bytes) or isinstance(b, bytes):
        return a == b
    return json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False)


def unstable_paths(a: Any, b: Any, at: tuple = ()) -> set[tuple]:
    """Where two runs of one commit differ: the leaf paths, or the subtree whose shape changed."""
    if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
        return set().union(*(unstable_paths(a[k], b[k], at + (k,)) for k in a)) if a else set()
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return set().union(*(unstable_paths(x, y, at + (i,)) for i, (x, y) in enumerate(zip(a, b)))) if a else set()
    return set() if same(a, b) else {at}


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


def _masks(runs: tuple[Path, Path], rel: str) -> tuple[Any, set[tuple]]:
    """One commit's first run of a file, and what its two runs disagree on."""
    first, second = runs
    try:
        a, b = value(first / rel, first), value(second / rel, second)
        return a, unstable_paths(a, b)
    except RecursionError:
        a, b = normalise(first / rel, first), normalise(second / rel, second)
        return a, (set() if a == b else {()})


def diff(base: Path, head: Path) -> dict[str, Any]:
    """Compare two captures (each OUT with run1 and run2) file by file."""
    runs = {name: (out / "run1", out / "run2") for name, out in (("base", base), ("head", head))}
    present = {name: (_files(r1), _files(r2)) for name, (r1, r2) in runs.items()}
    stable = {name: f1 & f2 for name, (f1, f2) in present.items()}
    wobbling = {name: f1 ^ f2 for name, (f1, f2) in present.items()}
    either = {name: f1 | f2 for name, (f1, f2) in present.items()}
    result: dict[str, Any] = {
        "only_in_base": sorted(stable["base"] - either["head"]),
        "only_in_head": sorted(stable["head"] - either["base"]),
        "changed": [],
        # Varies between one commit's own runs and not the other's: new (or lost) nondeterminism.
        "unstable_in_one": sorted(wobbling["base"] ^ wobbling["head"]),
        "skipped_unstable": sorted(wobbling["base"] & wobbling["head"]),
        "unstable_fields": {},   # file -> the paths that vary in one commit's runs only
        "masked_fields": 0,
        "files": len(either["base"]),
    }
    for rel in sorted(stable["base"] & stable["head"]):
        (b1, base_mask), (h1, head_mask) = _masks(runs["base"], rel), _masks(runs["head"], rel)
        if base_mask != head_mask:
            result["unstable_in_one"].append(rel)
            result["unstable_fields"][rel] = sorted(".".join(map(str, p)) or "<file>" for p in base_mask ^ head_mask)
        elif () in base_mask:
            result["skipped_unstable"].append(rel)
        else:
            result["masked_fields"] += len(base_mask)
            if not same(masked(b1, base_mask), masked(h1, base_mask)):
                result["changed"].append(rel)
    result["unstable_in_one"].sort()
    return result


def lane_tests() -> list[str]:
    """Every test file that imports the crypto lane."""
    return sorted(
        p.as_posix() for p in Path("tests").rglob("test_*.py")
        if any(marker in p.read_text(encoding="utf-8", errors="replace") for marker in _LANE_MARKERS)
    )


def _collect(tests: Sequence[str]) -> list[str]:
    listing = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                              *tests], capture_output=True, text=True)
    if listing.returncode != 0:
        print(listing.stdout[-2000:] + listing.stderr[-2000:], file=sys.stderr)
        raise SystemExit(listing.returncode)
    return [line for line in listing.stdout.splitlines() if "::" in line]


def capture(out: Path, tests: Sequence[str], *, same_as: Path | None = None) -> int:
    if same_as is not None:
        ids = (same_as / IDS_FILE).read_text(encoding="utf-8").splitlines()
    else:
        ids = _collect(list(tests) or lane_tests())
    if not ids:
        print("no tests collected", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)      # pytest makes --basetemp itself, but not its parent
    (out / IDS_FILE).unlink(missing_ok=True)    # written only when both runs pass
    argfile = out / "pytest_args.txt"
    argfile.write_text("\n".join(ids) + "\n", encoding="utf-8")
    for run in ("run1", "run2"):
        code = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                               f"--basetemp={(out / run).resolve()}", f"@{argfile}"]).returncode
        if code != 0:
            print(f"the tests failed on {run} (exit {code}); a failing tree has no record to compare",
                  file=sys.stderr)
            return code
    (out / IDS_FILE).write_text("\n".join(ids) + "\n", encoding="utf-8")
    print(f"captured {len(_files((out / 'run1').resolve()))} files from {len(ids)} tests, twice")
    return 0


def compare(base: Path, head: Path) -> int:
    for out in (base, head):
        if not (out / IDS_FILE).is_file():
            print(f"{out} holds no finished capture", file=sys.stderr)
            return 2
    ids = [(out / IDS_FILE).read_text(encoding="utf-8") for out in (base, head)]
    if ids[0] != ids[1]:
        print("the two captures ran different tests; capture the head with --same-tests-as BASE", file=sys.stderr)
        return 2
    result = diff(base.resolve(), head.resolve())
    if not result["files"]:
        print("nothing was captured: no test wrote a file under tmp_path", file=sys.stderr)
        return 1
    keys = ("only_in_base", "only_in_head", "changed", "unstable_in_one")
    for key in keys:
        for path in result[key]:
            fields = result["unstable_fields"].get(path) if key == "unstable_in_one" else None
            print(f"{key}: {path}" + (f"  [{', '.join(fields)}]" if fields else ""))
    differing = sum(len(result[k]) for k in keys)
    print(f"{differing} differing of {result['files']} files; set aside where both commits vary alike: "
          f"{len(result['skipped_unstable'])} files, {result['masked_fields']} fields")
    return 1 if differing else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    cap = sub.add_parser("capture", help="run the tests twice, into OUT/run1 and OUT/run2")
    cap.add_argument("out", type=Path)
    cap.add_argument("tests", nargs="*", help="test files (default: every test file that imports the lane)")
    cap.add_argument("--same-tests-as", type=Path, default=None, help="run exactly the tests another capture ran")
    cmp_ = sub.add_parser("compare", help="compare two captures; exit 1 on any difference")
    cmp_.add_argument("base", type=Path)
    cmp_.add_argument("head", type=Path)
    args = parser.parse_args(argv)
    if args.command == "capture":
        return capture(args.out, args.tests, same_as=args.same_tests_as)
    return compare(args.base, args.head)


if __name__ == "__main__":
    sys.exit(main())
