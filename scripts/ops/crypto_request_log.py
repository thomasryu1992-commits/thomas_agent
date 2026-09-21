"""A pytest plugin for the record capture: what the lane asks the venue for, logged beside the files
the tests write (crypto PR7e-3).

``crypto_record_capture.py`` compares what the tests write under ``tmp_path``. The order requests the
tests' scripted adapters receive live only in memory, so a refactor of the order path could change a
request and still pass every compare. This plugin wraps the pure functions that every order request and
every reconciliation passes through, and appends each call's arguments and result to
``<basetemp>/_requests/<test>.jsonl``. That directory is a sibling of the tests' own temp directories,
never inside one, so no test that lists its ``tmp_path`` sees it. The seams are:

- ``build_order_request``: the request, built from the intent;
- ``normalize_algo_order``: a conditional order's venue answer, normalised;
- ``reconcile_order``: the verdict on a venue order.

The wrapper returns exactly what the function returned, so no test sees a different value. For the
length of each test, every module attribute bound to one of these functions is rebound to one shared
wrapper, and it is bound back afterwards. That covers module-level imports (``live_leg``'s) and modules
first imported during the test, and ``a.f is b.f`` stays true between modules. A test that patches one
of these names itself replaces the wrapper, and its calls are then not logged. That test behaves the
same way in both commits of a compare, so it hides nothing that differs between them.

``crypto_record_capture.py capture`` loads this with ``-p scripts.ops.crypto_request_log``. It is not
loaded in a normal test run, and it writes nothing when no ``--basetemp`` is given.
"""

from __future__ import annotations

import dataclasses
import functools
import hashlib
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

# (module, name) of each seam: resolved to the function object at install time, so a seam that moves to
# another module and is re-exported from this one is still found by what it is.
SEAMS: tuple[tuple[str, str], ...] = (
    ("runtime.mvp_runtime.crypto.live_execution", "build_order_request"),
    ("runtime.mvp_runtime.crypto.live_execution", "normalize_algo_order"),
    ("runtime.mvp_runtime.crypto.live_execution", "reconcile_order"),
)
LOG_DIR = "_requests"
_ROOT = Path(__file__).resolve().parents[2]

_current: list[str] | None = None                 # the running test's log lines; None outside a test
_wrappers: dict[int, Callable[..., Any]] = {}      # id(original) -> its one wrapper
_originals: dict[int, Callable[..., Any]] = {}     # id(wrapper) -> the original
_ours: dict[str, bool] = {}                        # module name -> whether its file is in this tree


def _stable(value: Any) -> Any:
    """A JSON-safe spelling of a value that is the same in every run: no memory addresses."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"<dataclass>": type(value).__qualname__, **dataclasses.asdict(value)}
    if isinstance(value, (set, frozenset)):
        return sorted((_stable(v) for v in value), key=repr)
    if isinstance(value, Path):
        return value.as_posix()
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _wrapper_for(original: Callable[..., Any], name: str) -> Callable[..., Any]:
    wrapper = _wrappers.get(id(original))
    if wrapper is not None:
        return wrapper

    @functools.wraps(original)
    def logged(*args: Any, **kwargs: Any) -> Any:
        result = original(*args, **kwargs)
        if _current is not None:
            _current.append(json.dumps({"seam": name, "args": list(args), "kwargs": kwargs, "result": result},
                                       sort_keys=True, ensure_ascii=False, default=_stable))
        return result

    _wrappers[id(original)] = logged
    _originals[id(logged)] = original
    return logged


def _scanned_modules() -> list[Any]:
    """Every loaded module whose file is in this tree: the runtime, the scripts and the tests, however
    each was named on import. Decided once per module name."""
    found = []
    for name, module in list(sys.modules.items()):
        if name not in _ours:
            path = getattr(module, "__file__", None)
            try:
                _ours[name] = bool(path) and "site-packages" not in path and Path(path).resolve().is_relative_to(_ROOT)
            except (OSError, ValueError):
                _ours[name] = False
        if _ours[name]:
            found.append(module)
    return found


def _rebind(mapping: dict[int, Callable[..., Any]]) -> None:
    """Rebind every scanned module attribute whose value is a key of ``mapping`` (by identity)."""
    for module in _scanned_modules():
        namespace = getattr(module, "__dict__", None)
        if not isinstance(namespace, dict):
            continue
        for attr, value in list(namespace.items()):
            if callable(value) and id(value) in mapping:
                namespace[attr] = mapping[id(value)]


def install(seams: tuple[tuple[str, str], ...] = SEAMS) -> None:
    """Start logging: every scanned module attribute bound to a seam now holds its wrapper."""
    global _current
    targets: dict[int, Callable[..., Any]] = {}
    for module_name, name in seams:
        original = getattr(importlib.import_module(module_name), name)
        original = _originals.get(id(original), original)   # already wrapped: find the function
        targets[id(original)] = _wrapper_for(original, name)
    _rebind(targets)
    _current = []


def uninstall() -> list[str]:
    """Stop logging, bind every wrapper back to its function, and return what was logged."""
    global _current
    _rebind(dict(_originals))
    logged, _current = _current or [], None
    return logged


def log_path(basetemp: Path, nodeid: str) -> Path:
    """One file per test, named after the test id: readable, bounded, and unique."""
    readable = re.sub(r"[^A-Za-z0-9_.-]+", "_", nodeid)[:150]
    digest = hashlib.sha256(nodeid.encode("utf-8")).hexdigest()[:12]
    return basetemp / LOG_DIR / f"{readable}-{digest}.jsonl"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    install()
    try:
        yield
    finally:
        logged = uninstall()
        basetemp = item.config.getoption("basetemp")
        if logged and basetemp:
            path = log_path(Path(basetemp).resolve(), item.nodeid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(logged) + "\n", encoding="utf-8")
