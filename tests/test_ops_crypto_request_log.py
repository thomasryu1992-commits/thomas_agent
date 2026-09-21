"""The record capture's request log (`scripts/ops/crypto_request_log.py`, crypto PR7e-3).

It is evidence only if it cannot change what it observes and cannot silently switch off. So a seam's
wrapper returns the function's own result or re-raises its exception, a value it cannot spell becomes a
marker line instead of an error, every binding of a seam holds one shared wrapper while a test runs and
is bound back afterwards, and `capture` loads it: a capture without it would compare clean with the
request evidence gone.
"""

from __future__ import annotations

import datetime
import decimal
import enum
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from scripts.ops import crypto_record_capture as capture
from scripts.ops import crypto_request_log as request_log
from runtime.mvp_runtime.errors import ToolError

ROOT = Path(__file__).resolve().parents[1]


def _module(name: str, **attrs):
    module = types.ModuleType(name)
    module.__file__ = str(request_log._ROOT / "runtime" / f"{name.rsplit('.', 1)[-1]}.py")
    for key, value in attrs.items():
        setattr(module, key, value)
    return module


def test_every_binding_of_a_seam_is_logged_and_bound_back(monkeypatch):
    def seam(quantity, *, side):
        return {"quantity": quantity, "side": side}

    source = _module("runtime._request_log_source", seam=seam)
    importer = _module("runtime._request_log_importer", seam=seam, renamed=seam)
    for module in (source, importer):
        monkeypatch.setitem(sys.modules, module.__name__, module)

    request_log.install(((source.__name__, "seam"),))
    try:
        wrapper = source.seam
        assert wrapper is not seam and importer.seam is wrapper and importer.renamed is wrapper
        late = _module("runtime._request_log_late", seam=source.seam)     # imported during the test
        monkeypatch.setitem(sys.modules, late.__name__, late)
        assert importer.seam(0.01, side="SELL") == {"quantity": 0.01, "side": "SELL"}
        assert late.seam(quantity=0.01, side="SELL") == {"quantity": 0.01, "side": "SELL"}
    finally:
        logged = request_log.uninstall()

    assert source.seam is seam and importer.seam is seam and importer.renamed is seam and late.seam is seam
    line = {"seam": "seam", "call": {"quantity": 0.01, "side": "SELL"}, "result": {"quantity": 0.01, "side": "SELL"}}
    assert [json.loads(entry) for entry in logged] == [line, line]     # positional and keyword log alike
    assert seam(1, side="BUY") == {"quantity": 1, "side": "BUY"}     # outside a test: nothing is logged
    assert request_log.uninstall() == []


def test_a_call_that_raises_is_logged_and_still_raises(monkeypatch):
    def seam(intent):
        raise ToolError("MALFORMED_LIVE_ORDER_INTENT", "no symbol")

    source = _module("runtime._request_log_raiser", seam=seam)
    monkeypatch.setitem(sys.modules, source.__name__, source)
    request_log.install(((source.__name__, "seam"),))
    try:
        with pytest.raises(ToolError) as refused:
            source.seam({})
    finally:
        logged = request_log.uninstall()
    assert refused.value.reason_code == "MALFORMED_LIVE_ORDER_INTENT"
    assert [json.loads(entry) for entry in logged] == [
        {"seam": "seam", "call": {"intent": {}}, "raised": "ToolError:MALFORMED_LIVE_ORDER_INTENT"},
    ]


def test_a_value_json_cannot_spell_is_a_marker_line_not_an_error(monkeypatch):
    def seam(value):
        return {("tuple", "key"): value}          # a key JSON cannot spell

    source = _module("runtime._request_log_unspellable", seam=seam)
    monkeypatch.setitem(sys.modules, source.__name__, source)
    request_log.install(((source.__name__, "seam"),))
    try:
        assert source.seam(1) == {("tuple", "key"): 1}
    finally:
        logged = request_log.uninstall()
    assert [json.loads(entry) for entry in logged] == [{"seam": "seam", "unloggable": "TypeError"}]


def test_values_are_spelled_by_what_they_are_and_the_same_every_run():
    class Side(enum.Enum):
        BUY = "BUY"

    class Opaque:
        pass

    assert request_log._stable({2, 1}) == [1, 2]
    assert request_log._stable(decimal.Decimal("0.07054")) == {"<decimal>": "0.07054"}
    assert request_log._stable(decimal.Decimal("0.07054")) != request_log._stable(decimal.Decimal("0.07055"))
    assert request_log._stable(datetime.datetime(2026, 9, 21, 12, 0)) == {"<datetime>": "2026-09-21T12:00:00"}
    assert request_log._stable(Side.BUY).endswith("Side.BUY")
    spelled = request_log._stable(Opaque())
    assert spelled == request_log._stable(Opaque()) and "0x" not in spelled


def test_the_log_is_named_after_the_test_and_kept_out_of_its_temp_dir(tmp_path):
    long_id = "tests/test_x.py::test_" + "a" * 400 + "[param/with:odd chars]"
    path = request_log.log_path(tmp_path, long_id)
    assert path.parent == tmp_path / request_log.LOG_DIR
    assert len(path.name) < 200 and path.name.endswith(".jsonl")
    assert path != request_log.log_path(tmp_path, long_id + "x")      # unique per test id
    assert path == request_log.log_path(tmp_path, long_id)            # and the same in every run


def test_the_seams_are_the_order_paths_pure_functions():
    """What the log watches, resolved by what each seam is, so a move that re-exports it keeps it."""
    import importlib

    for module_name, name in request_log.SEAMS:
        assert callable(getattr(importlib.import_module(module_name), name)), (module_name, name)


def test_a_pytest_run_with_the_plugin_writes_the_log_and_binds_everything_back(tmp_path):
    """End to end, through the hook: a session loads the plugin as `capture` does, one test calls a
    seam through its module attribute, and when the session ends the attribute holds the function
    again."""
    tests_dir = tmp_path / "inner"
    tests_dir.mkdir()
    (tests_dir / "conftest.py").write_text(textwrap.dedent('''
        from pathlib import Path

        def pytest_sessionfinish(session):
            from runtime.mvp_runtime.crypto import venue_contract
            wrapped = hasattr(venue_contract.legacy_conditional_probe, "__wrapped__")
            (Path(__file__).parent / "restored.txt").write_text("wrapped" if wrapped else "restored")
    '''), encoding="utf-8")
    (tests_dir / "test_inner.py").write_text(textwrap.dedent('''
        from runtime.mvp_runtime.crypto import venue_contract

        def test_calls_a_seam():
            assert hasattr(venue_contract.legacy_conditional_probe, "__wrapped__")
            venue_contract.legacy_conditional_probe("BTCUSDT", stop_price=1.5, client_id="TAI_x")
    '''), encoding="utf-8")
    basetemp = tmp_path / "bt"
    # `--rootdir` keeps the inner node id relative, so the log this writes under the outer test's
    # `tmp_path` has the same name in every capture instead of one carrying the capture's own path.
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          "-p", capture.REQUEST_LOG_PLUGIN, f"--basetemp={basetemp}",
                          f"--rootdir={tests_dir}", str(tests_dir)],
                         cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-2000:]
    logs = list((basetemp / request_log.LOG_DIR).glob("*.jsonl"))
    assert [log.name.split("-")[0] for log in logs] == ["test_inner.py_test_calls_a_seam"]
    entry = json.loads(logs[0].read_text(encoding="utf-8").splitlines()[0])
    assert entry["seam"] == "legacy_conditional_probe"
    assert entry["call"] == {"symbol": "BTCUSDT", "stop_price": 1.5, "client_id": "TAI_x"}
    assert entry["result"]["type"] == "STOP_MARKET"
    assert (tests_dir / "restored.txt").read_text() == "restored"


def test_capture_loads_the_plugin_on_every_run(tmp_path, monkeypatch):
    """A capture without the plugin has no request logs on either side, and a compare of two such
    captures is clean: the evidence would be gone without anything failing."""
    calls: list[list[str]] = []

    def fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    base = tmp_path / "base"
    base.mkdir()
    (base / capture.IDS_FILE).write_text("tests/test_x.py::test_a\n", encoding="utf-8")
    assert capture.capture(tmp_path / "head", [], same_as=base) == 0
    assert len(calls) == 2
    for argv in calls:
        assert argv[argv.index("-p", argv.index("no:cacheprovider")) + 1] == capture.REQUEST_LOG_PLUGIN
