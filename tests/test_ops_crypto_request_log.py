"""The record capture's request log (`scripts/ops/crypto_request_log.py`, crypto PR7e-3).

It is evidence only if it cannot change what it observes. So a seam's wrapper returns the function's
own result, every binding of a seam holds one shared wrapper while a test runs, and every binding is
bound back afterwards. That includes a module first imported during the test, which picked up the
wrapper and would otherwise keep it.
"""

from __future__ import annotations

import json
import sys
import types

from scripts.ops import crypto_request_log as request_log


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
        result = importer.seam(0.01, side="SELL")
        assert result == {"quantity": 0.01, "side": "SELL"}
    finally:
        logged = request_log.uninstall()

    assert source.seam is seam and importer.seam is seam and importer.renamed is seam and late.seam is seam
    assert [json.loads(line) for line in logged] == [
        {"seam": "seam", "args": [0.01], "kwargs": {"side": "SELL"}, "result": {"quantity": 0.01, "side": "SELL"}},
    ]
    assert seam(1, side="BUY") == {"quantity": 1, "side": "BUY"}     # outside a test: nothing is logged
    assert request_log.uninstall() == []


def test_a_value_json_cannot_spell_is_logged_the_same_way_every_run():
    class Opaque:
        pass

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
    from runtime.mvp_runtime.crypto import live_execution

    for module_name, name in request_log.SEAMS:
        assert module_name == live_execution.__name__
        assert callable(getattr(live_execution, name))
