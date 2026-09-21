"""The record-capture harness PR7 compares trees with (`scripts/ops/crypto_record_capture.py`).

It is the evidence that a refactor changed no record, so what it must not do is pass a real change:
a field that is stable within a tree and differs between trees is a difference, and only what a
tree's own two runs disagree on is set aside.
"""

from __future__ import annotations

import json

from scripts.ops import crypto_record_capture as capture


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if rel.endswith(".jsonl"):
        path.write_text("\n".join(json.dumps(line) for line in data) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def _capture(out, files, tests=("tests/test_x.py",)):
    """``files``: {rel: (run1 data, run2 data)}, None where a run did not write the file."""
    for run, index in (("run1", 0), ("run2", 1)):
        (out / run).mkdir(parents=True, exist_ok=True)
        for rel, datas in files.items():
            if datas[index] is not None:
                _write(out / run, rel, datas[index])
    (out / capture.TESTS_FILE).write_text(json.dumps(list(tests)), encoding="utf-8")
    return out


def test_key_order_and_the_captures_own_temp_root_are_not_differences(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, text in ((a, '{"x": 1, "path": "%s/t0/book.json"}'), (b, '{"path": "%s/t0/book.json", "x": 1}')):
        root.mkdir()
        (root / "r.json").write_text(text % root, encoding="utf-8")
    assert capture.normalise(a / "r.json", a) == capture.normalise(b / "r.json", b)


def test_only_what_a_trees_own_runs_disagree_on_is_set_aside(tmp_path):
    outcome = lambda at, r: {"recorded_at": at, "net_r": r, "close_reason": "EMERGENCY"}  # noqa: E731
    base = _capture(tmp_path / "base", {
        "t0/outcomes.jsonl": ([outcome("09:00", 1.25)], [outcome("09:01", 1.25)]),
        "t0/same.json": ({"a": 1}, {"a": 1}),
        "t0/wobbles.json": ({"a": 1}, None),
    })
    head = _capture(tmp_path / "head", {
        "t0/outcomes.jsonl": ([outcome("10:00", 1.5)], [outcome("10:02", 1.5)]),
        "t0/same.json": ({"a": 1}, {"a": 1}),
        "t0/new.json": ({"b": 2}, {"b": 2}),
    })
    result = capture.diff(base, head)
    # recorded_at wobbles in both trees and is masked; net_r is stable in each and differs: a change.
    assert result["changed"] == ["t0/outcomes.jsonl"]
    assert result["only_in_head"] == ["t0/new.json"] and result["only_in_base"] == []
    assert result["skipped_unstable"] == ["t0/wobbles.json"] and result["masked_fields"] == 1


def test_a_record_that_differs_only_in_its_wall_clock_is_the_same_record(tmp_path):
    rows = lambda at: [{"recorded_at": at, "net_r": 1.25}]  # noqa: E731
    base = _capture(tmp_path / "base", {"t0/outcomes.jsonl": (rows("09:00"), rows("09:01"))})
    head = _capture(tmp_path / "head", {"t0/outcomes.jsonl": (rows("10:00"), rows("10:02"))})
    assert capture.compare(base, head) == 0
    assert capture.diff(base, head)["changed"] == []


def test_a_shape_that_changes_between_runs_skips_the_file_and_compare_fails_on_a_change(tmp_path, capsys):
    base = _capture(tmp_path / "base", {"t0/r.jsonl": ([{"a": 1}], [{"a": 1}, {"a": 2}]),
                                        "t0/k.json": ({"a": 1}, {"a": 1})})
    head = _capture(tmp_path / "head", {"t0/r.jsonl": ([{"a": 1}], [{"a": 1}]), "t0/k.json": ({"a": 2}, {"a": 2})})
    assert capture.diff(base, head)["skipped_unstable"] == ["t0/r.jsonl"]
    assert capture.compare(base, head) == 1
    assert "changed: t0/k.json" in capsys.readouterr().out


def test_two_captures_of_different_test_lists_are_not_compared(tmp_path):
    base = _capture(tmp_path / "base", {"t0/k.json": ({"a": 1}, {"a": 1})}, tests=("tests/test_a.py",))
    head = _capture(tmp_path / "head", {"t0/k.json": ({"a": 1}, {"a": 1})}, tests=("tests/test_b.py",))
    assert capture.compare(base, head) == 2


def test_a_record_nested_too_deep_to_walk_is_compared_whole(tmp_path):
    """The lane's tests write 5,000-deep records on purpose (they must be refused); the field walk
    cannot descend that far, so such a file is compared as normalised bytes."""
    deep = lambda leaf: json.loads("[" * 5000 + json.dumps(leaf) + "]" * 5000)  # noqa: E731
    base = _capture(tmp_path / "base", {"t0/deep.json": (deep(1), deep(1)), "t0/same.json": (deep(2), deep(2))})
    head = _capture(tmp_path / "head", {"t0/deep.json": (deep(3), deep(3)), "t0/same.json": (deep(2), deep(2))})
    assert capture.diff(base, head)["changed"] == ["t0/deep.json"]
