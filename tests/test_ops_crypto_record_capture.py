"""The record-capture harness PR7 compares commits with (`scripts/ops/crypto_record_capture.py`).

It is the evidence that a refactor changed no record, so what it must not do is pass a real change:
a field that is stable in both commits and differs between them is a difference, and so is one that
varies in one commit's runs and not the other's (a refactor that starts reading the wall clock). Only
what both commits vary alike is set aside.
"""

from __future__ import annotations

import json

import pytest

from scripts.ops import crypto_record_capture as capture


def _write(root, rel, data):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):      # written as it stands (a record too deep to build as Python data)
        path.write_text(data, encoding="utf-8")
    elif rel.endswith(".jsonl"):
        path.write_text("\n".join(json.dumps(line) for line in data) + "\n", encoding="utf-8")
    else:
        path.write_text(json.dumps(data), encoding="utf-8")


def _capture(out, files, ids=("tests/test_x.py::test_a",)):
    """``files``: {rel: (run1 data, run2 data)}, None where a run did not write the file."""
    for run, index in (("run1", 0), ("run2", 1)):
        (out / run).mkdir(parents=True, exist_ok=True)
        for rel, datas in files.items():
            if datas[index] is not None:
                _write(out / run, rel, datas[index])
    (out / capture.IDS_FILE).write_text("\n".join(ids) + "\n", encoding="utf-8")
    return out


def _outcome(at, r):
    return {"recorded_at": at, "net_r": r, "close_reason": "EMERGENCY"}


def test_key_order_and_the_captures_own_temp_root_are_not_differences(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for root, text in ((a, '{"x": 1, "path": "%s/t0/book.json"}'), (b, '{"path": "%s/t0/book.json", "x": 1}')):
        root.mkdir()
        (root / "r.json").write_text(text % root, encoding="utf-8")
    assert capture.normalise(a / "r.json", a) == capture.normalise(b / "r.json", b)


def test_only_what_both_commits_vary_alike_is_set_aside(tmp_path):
    base = _capture(tmp_path / "base", {
        "t0/outcomes.jsonl": ([_outcome("09:00", 1.25)], [_outcome("09:01", 1.25)]),
        "t0/same.json": ({"a": 1}, {"a": 1}),
        "t0/wobbles.json": ({"a": 1}, None),
    })
    head = _capture(tmp_path / "head", {
        "t0/outcomes.jsonl": ([_outcome("10:00", 1.5)], [_outcome("10:02", 1.5)]),
        "t0/same.json": ({"a": 1}, {"a": 1}),
        "t0/wobbles.json": ({"a": 1}, None),
        "t0/new.json": ({"b": 2}, {"b": 2}),
    })
    result = capture.diff(base, head)
    # recorded_at varies in both commits and is set aside; net_r is stable in each and differs.
    assert result["changed"] == ["t0/outcomes.jsonl"]
    assert result["only_in_head"] == ["t0/new.json"] and result["only_in_base"] == []
    assert result["skipped_unstable"] == ["t0/wobbles.json"] and result["masked_fields"] == 1
    assert result["unstable_in_one"] == []


def test_a_record_that_differs_only_in_its_wall_clock_is_the_same_record(tmp_path):
    rows = lambda at: [{"recorded_at": at, "net_r": 1.25}]  # noqa: E731
    base = _capture(tmp_path / "base", {"t0/outcomes.jsonl": (rows("09:00"), rows("09:01"))})
    head = _capture(tmp_path / "head", {"t0/outcomes.jsonl": (rows("10:00"), rows("10:02"))})
    assert capture.compare(base, head) == 0


def test_a_field_that_starts_reading_the_wall_clock_is_a_difference(tmp_path, capsys):
    """The review of #917 moved `created_at_utc` from the injected `now` to the wall clock: every test
    passed and the field, varying only in the head, was set aside on both sides."""
    base = _capture(tmp_path / "base", {"t0/outcomes.jsonl": ([_outcome("09:00", 1.25)], [_outcome("09:00", 1.25)])})
    head = _capture(tmp_path / "head", {"t0/outcomes.jsonl": ([_outcome("10:00", 1.25)], [_outcome("10:02", 1.25)])})
    assert capture.diff(base, head)["unstable_in_one"] == ["t0/outcomes.jsonl"]
    assert capture.compare(base, head) == 1
    assert "unstable_in_one: t0/outcomes.jsonl  [0.recorded_at]" in capsys.readouterr().out


def test_a_file_written_in_only_one_of_the_heads_runs_is_a_difference(tmp_path):
    base = _capture(tmp_path / "base", {"t0/k.json": ({"a": 1}, {"a": 1})})
    head = _capture(tmp_path / "head", {"t0/k.json": ({"a": 1}, None)})
    assert capture.diff(base, head)["unstable_in_one"] == ["t0/k.json"]
    assert capture.compare(base, head) == 1


@pytest.mark.parametrize("before,after", [(True, 1), (1, 1.0), (-0.0, 0.0), (None, False)])
def test_values_compare_as_written_not_as_python_equality(tmp_path, before, after):
    """The lane's readers check `is True`; a `true` that becomes `1` flips a gate."""
    base = _capture(tmp_path / "base", {"t0/k.json": ({"reduceOnly": before}, {"reduceOnly": before})})
    head = _capture(tmp_path / "head", {"t0/k.json": ({"reduceOnly": after}, {"reduceOnly": after})})
    assert capture.diff(base, head)["changed"] == ["t0/k.json"]


def test_a_nan_that_stays_nan_is_neither_unstable_nor_changed(tmp_path):
    nan = float("nan")
    base = _capture(tmp_path / "base", {"t0/caps.json": ({"cap": nan}, {"cap": nan})})
    head = _capture(tmp_path / "head", {"t0/caps.json": ({"cap": nan}, {"cap": nan})})
    result = capture.diff(base, head)
    assert result["changed"] == [] and result["masked_fields"] == 0 and capture.compare(base, head) == 0


def test_a_record_nested_too_deep_to_walk_is_compared_whole(tmp_path):
    """The lane's tests write 5,000-deep records on purpose (they must be refused); the field walk
    cannot descend that far, so such a file is compared as normalised bytes."""
    deep = lambda leaf: "[" * 5000 + json.dumps(leaf) + "]" * 5000  # noqa: E731  (as text: no parse)
    base = _capture(tmp_path / "base", {"t0/deep.json": (deep(1), deep(1)), "t0/same.json": (deep(2), deep(2))})
    head = _capture(tmp_path / "head", {"t0/deep.json": (deep(3), deep(3)), "t0/same.json": (deep(2), deep(2))})
    assert capture.diff(base, head)["changed"] == ["t0/deep.json"]


def test_captures_of_different_tests_are_not_compared(tmp_path):
    """Temp directories are numbered by run order: one added test renumbers every one after it."""
    base = _capture(tmp_path / "base", {"t0/k.json": ({"a": 1}, {"a": 1})}, ids=("tests/test_a.py::test_x",))
    head = _capture(tmp_path / "head", {"t0/k.json": ({"a": 1}, {"a": 1})},
                    ids=("tests/test_a.py::test_new", "tests/test_a.py::test_x"))
    assert capture.compare(base, head) == 2


def test_an_empty_or_unfinished_capture_does_not_pass(tmp_path):
    base = _capture(tmp_path / "base", {})
    head = _capture(tmp_path / "head", {})
    assert capture.compare(base, head) == 1, "nothing captured is not evidence"
    (head / capture.IDS_FILE).unlink()
    assert capture.compare(base, head) == 2, "a capture whose runs did not both pass is not one"


def test_a_decoder_that_runs_out_of_stack_still_compares_the_file(tmp_path, monkeypatch):
    """How deep `json.loads` may go depends on the C stack: the Windows runner's raises RecursionError
    on the 5,000-deep records this host parses. Such a file is compared as its text, on any host."""
    parse = json.loads

    def shallow(data, *args, **kwargs):
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        if text.count("[") > 900:
            raise RecursionError("maximum recursion depth exceeded while decoding a JSON array")
        return parse(data, *args, **kwargs)

    monkeypatch.setattr(capture.json, "loads", shallow)
    deep = lambda leaf: "[" * 5000 + json.dumps(leaf) + "]" * 5000  # noqa: E731
    base = _capture(tmp_path / "base", {"t0/deep.jsonl": (deep(1), deep(1)), "t0/same.json": (deep(2), deep(2))})
    head = _capture(tmp_path / "head", {"t0/deep.jsonl": (deep(3), deep(3)), "t0/same.json": (deep(2), deep(2))})
    assert capture.diff(base, head)["changed"] == ["t0/deep.jsonl"]
