"""A pytest plugin for the record capture: what the order path's pure seams produced, logged beside the
files the tests write (crypto PR7e-3).

``crypto_record_capture.py`` compares what the tests write under ``tmp_path``. Much of the order path
lives only in memory, in the requests and verdicts the tests' scripted adapters exchange, so a refactor
could change them and still pass every compare. This plugin wraps the pure functions the order path
builds and judges orders with, and appends each call to ``<basetemp>/_requests/<test>.jsonl``. That
directory is a sibling of the tests' own temp directories, never inside one, so no test that lists its
``tmp_path`` sees it. The seams are:

- ``build_order_request``: the request, built from the intent;
- ``normalize_algo_order``: a conditional order's venue answer, normalised;
- ``reconcile_order``: the verdict on a venue order;
- ``venue_contract.legacy_conditional_probe``: the one request built by hand, for the venue contract.

Each line holds the call's arguments, bound to the function's parameter names so a positional call and
a keyword call log alike, and either its result or the exception it raised.

**What it does not see.** It sees what these functions returned, not what an adapter then received. A
call site that changes a request between building and sending it (``submit_and_reconcile``,
``live_leg.place_bracket_leg``, the venue contract's validations) is outside it, as are the arguments of
the reads and cancels (``fetch_order``, ``cancel_order``, the open-orders reads). Code on those paths is
held to its AST instead.

**It cannot change what it observes, within this tree.**
- The wrapper returns the function's own result, or re-raises its exception.
- A value it cannot spell in JSON is logged as a marker line rather than raised.
- For the length of each test, every module attribute in this tree that is bound to a seam holds one
  shared wrapper, and it is bound back afterwards, including in modules first imported during the test.
  So ``a.f is b.f`` still holds between those modules, and ``inspect.getsource`` and
  ``inspect.signature`` see the function through the wrapper.

Nothing outside module attributes is rebound: a reference taken into a dict, a tuple or a partial keeps
whatever it held. The plugin is not reentrant, so a nested in-process pytest run that loads it too takes
over the log. A test that patches a seam itself replaces the wrapper, and its calls are not logged. That
test is blind to a change in what its path passes to that seam, in both commits alike.

``crypto_record_capture.py capture`` loads this with ``-p scripts.ops.crypto_request_log``. It is not
loaded in a normal test run, and it writes nothing when no ``--basetemp`` is given.
"""

from __future__ import annotations

import dataclasses
import datetime
import decimal
import enum
import functools
import hashlib
import importlib
import inspect
import json
import re
import sys
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable

import pytest

# (module, name) of each seam: resolved to the function object at install time, so a seam that moves to
# another module and is re-exported from this one is still found by what it is.
SEAMS: tuple[tuple[str, str], ...] = (
    ("runtime.mvp_runtime.crypto.live_execution", "build_order_request"),
    ("runtime.mvp_runtime.crypto.live_execution", "normalize_algo_order"),
    ("runtime.mvp_runtime.crypto.live_execution", "reconcile_order"),
    ("runtime.mvp_runtime.crypto.venue_contract", "legacy_conditional_probe"),
)
LOG_DIR = "_requests"
_ROOT = Path(__file__).resolve().parents[2]

_current: list[str] | None = None                 # the running test's log lines; None outside a test
_wrappers: dict[int, Callable[..., Any]] = {}      # id(original) -> its one wrapper
_originals: dict[int, Callable[..., Any]] = {}     # id(wrapper) -> the original
_ours: dict[str, bool] = {}                        # module name -> whether its file is in this tree


def _json_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=_stable)


def _stable(value: Any) -> Any:
    """A JSON-safe spelling of a value that says what it is and is the same in every run."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {"<dataclass>": type(value).__qualname__,
                **{field.name: getattr(value, field.name) for field in dataclasses.fields(value)}}
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=_json_key)
    if isinstance(value, MappingProxyType):
        return dict(value)
    if isinstance(value, enum.Enum):
        return f"{type(value).__qualname__}.{value.name}"
    if isinstance(value, decimal.Decimal):
        return {"<decimal>": str(value)}
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return {f"<{type(value).__name__}>": value.isoformat()}
    if isinstance(value, Path):
        return value.as_posix()
    return f"<{type(value).__module__}.{type(value).__qualname__}>"


def _line(entry: dict[str, Any]) -> str:
    try:
        return json.dumps(entry, sort_keys=True, ensure_ascii=False, default=_stable)
    except (TypeError, ValueError, RecursionError) as exc:
        return json.dumps({"seam": entry["seam"], "unloggable": type(exc).__name__}, sort_keys=True)


def _wrapper_for(original: Callable[..., Any], name: str) -> Callable[..., Any]:
    wrapper = _wrappers.get(id(original))
    if wrapper is not None:
        return wrapper
    signature = inspect.signature(original)

    @functools.wraps(original)
    def logged(*args: Any, **kwargs: Any) -> Any:
        try:
            result = original(*args, **kwargs)
        except BaseException as exc:
            if _current is not None:
                _current.append(_line({"seam": name, "call": _call(signature, args, kwargs),
                                       "raised": f"{type(exc).__name__}:{getattr(exc, 'reason_code', '')}"}))
            raise
        if _current is not None:
            _current.append(_line({"seam": name, "call": _call(signature, args, kwargs), "result": result}))
        return result

    _wrappers[id(original)] = logged
    _originals[id(logged)] = original
    return logged


def _call(signature: inspect.Signature, args: tuple, kwargs: dict[str, Any]) -> Any:
    """The arguments by parameter name, so ``f(x)`` and ``f(intent=x)`` log alike."""
    try:
        return dict(signature.bind(*args, **kwargs).arguments)
    except TypeError:
        return {"<args>": list(args), "<kwargs>": kwargs}


def _scanned_modules() -> list[Any]:
    """Every loaded module whose file is in this tree: the runtime, the scripts and the tests, however
    each was named on import. Decided once per module name."""
    found = []
    for name, module in list(sys.modules.items()):
        if name not in _ours:
            path = getattr(module, "__file__", None)
            try:
                _ours[name] = (isinstance(path, str) and "site-packages" not in path
                               and Path(path).resolve().is_relative_to(_ROOT))
            except (OSError, ValueError, TypeError):
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


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    install()
    try:
        return (yield)
    finally:
        logged = uninstall()
        basetemp = item.config.getoption("basetemp")
        if logged and basetemp:
            path = log_path(Path(basetemp).resolve(), item.nodeid)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(logged) + "\n", encoding="utf-8")
