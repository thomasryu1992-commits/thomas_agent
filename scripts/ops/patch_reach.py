"""A pytest plugin that answers, before and after a module split, whether a test's patch reaches the
function it was meant for (crypto PR7).

When a function moves from module ``old`` to module ``new``, it reads ``new``'s globals from then on. A
test that patches ``old.<name>`` still passes if the moved function no longer sees the patch but the
test's assertions do not depend on it, and a static count of the patches cannot tell whether that is
the case. The count also overstates the risk: in PR7e-7, ``pool.load_active_pool`` was patched 83 times,
yet no function in ``pool`` that reads it ever ran under that patch. This plugin measures what happens
while the tests run.

For every call of a top-level function defined in a watched module, it records which of the names the
function reads are patched at that moment. A name is patched when the module's binding is no longer the
object it held when the session started. It records two kinds of hits:

- ``own``: the name is patched in the module the function is defined in, so the patch reaches it;
- ``missed``: the function is defined in one of the ``--patch-reach-new`` modules, the name is patched on
  ``--patch-reach-old``, and the function's own module holds a different object. The patch on ``old``
  would have reached the function before the move, and it does not reach it now. A test that patches
  both bindings with one object is not a miss.

Run it on the base commit with ``--patch-reach-old`` alone to see which of the functions you plan to
move ever run under a patch. Run it on the head commit with the new modules as well to count what the
move cut off::

    python -m pytest tests/ -q -p no:cacheprovider -p scripts.ops.patch_reach \\
        --patch-reach-old=runtime.mvp_runtime.crypto.pool \\
        --patch-reach-new=runtime.mvp_runtime.crypto.pool_state \\
        --patch-reach-out=/tmp/reach.jsonl --basetemp=/tmp/reach

The output is JSON lines: one line per distinct ``(test, function, kind, patched names)`` hit, followed
by one ``calls`` line per watched function that says how many tests called it. A function called by no
test is listed with 0.

**It cannot change what it observes.** It listens to ``sys.monitoring`` ``PY_START`` events on the
watched functions' code objects only, and it replaces no object, so every test passes or fails exactly
as it does without it.

**What it does not see.**
- It watches module-level functions only: methods, nested functions and lambdas are not watched. The
  names a nested function reads count as read by the enclosing function, when that function starts.
- The names a function reads come from its AST. A parameter or local with the same name as a patched
  global is counted as a read of that global.
- A name the module did not hold when the session started is never counted as patched.
- A patch applied after a function has started, and read later in the same call, is not seen.

It uses ``sys.monitoring`` tool id 4. If another tool already holds that id, the run stops with a usage
error instead of measuring nothing. The plugin is not loaded in a normal test run.
"""

from __future__ import annotations

import ast
import collections
import importlib
import json
import sys
from pathlib import Path
from types import CodeType, ModuleType
from typing import Any

import pytest

TOOL_ID = 4
TOOL_NAME = "patch-reach"


class _Census:
    def __init__(self, old: ModuleType, new: list[ModuleType], out: Path) -> None:
        self.old = old
        self.new = new
        self.out = out
        self.test: str | None = None
        self.originals = {module.__name__: dict(vars(module)) for module in (old, *new)}
        self.watched: dict[CodeType, tuple[ModuleType, str, tuple[str, ...]]] = {}
        for module in (old, *new):
            self.watched.update(_top_level_functions(module))
        self.hits: set[tuple[Any, ...]] = set()
        self.calls: dict[str, set[str]] = collections.defaultdict(set)
        self.counts = collections.Counter()
        self.lines: list[str] = []

    def patched(self, module: ModuleType, names: tuple[str, ...]) -> tuple[str, ...]:
        namespace, original = vars(module), self.originals[module.__name__]
        return tuple(name for name in names if name in original and namespace.get(name) is not original[name])

    def on_start(self, code: CodeType, offset: int) -> None:
        entry = self.watched.get(code)
        if entry is None:
            return
        module, name, reads = entry
        function = f"{module.__name__}.{name}"
        self.calls.setdefault(function, set())
        if self.test is not None:
            self.calls[function].add(self.test)
        own = self.patched(module, reads)
        missed: tuple[str, ...] = ()
        if module is not self.old:
            old_namespace, own_namespace = vars(self.old), vars(module)
            missed = tuple(read for read in self.patched(self.old, reads)
                           if own_namespace.get(read) is not old_namespace.get(read))
        for kind, names in (("own", own), ("missed", missed)):
            key = (self.test, function, kind, names)
            if names and key not in self.hits:
                self.hits.add(key)
                self.counts[kind] += 1
                self.lines.append(json.dumps({"test": self.test, "fn": function, "kind": kind,
                                              "patched": list(names)}, ensure_ascii=False))

    def write(self) -> None:
        lines = list(self.lines)
        functions = sorted({f"{module.__name__}.{name}" for module, name, _ in self.watched.values()})
        for function in functions:
            lines.append(json.dumps({"calls": function, "tests": len(self.calls.get(function, ()))}))
        self.out.parent.mkdir(parents=True, exist_ok=True)
        self.out.write_text("".join(line + "\n" for line in lines), encoding="utf-8")


def _top_level_functions(module: ModuleType) -> dict[CodeType, tuple[ModuleType, str, tuple[str, ...]]]:
    """Each function defined at the top of ``module``'s source and still bound there under its name, with
    the names its body reads."""
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    found = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        function = vars(module).get(node.name)
        code = getattr(function, "__code__", None)
        if code is None or getattr(function, "__module__", None) != module.__name__:
            continue
        reads = {child.id for child in ast.walk(node)
                 if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)}
        found[code] = (module, node.name, tuple(sorted(reads - {node.name})))
    return found


_census: _Census | None = None


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("patch-reach")
    group.addoption("--patch-reach-old", metavar="MODULE",
                    help="the module functions move out of (required when the plugin is loaded)")
    group.addoption("--patch-reach-new", metavar="MODULE", action="append", default=[],
                    help="a module functions moved to; repeat for several")
    group.addoption("--patch-reach-out", metavar="PATH", help="where to write the JSON lines (required)")


def pytest_configure(config: pytest.Config) -> None:
    global _census
    old, out = config.getoption("patch_reach_old"), config.getoption("patch_reach_out")
    if not old or not out:
        raise pytest.UsageError("patch_reach needs --patch-reach-old and --patch-reach-out")
    try:
        sys.monitoring.use_tool_id(TOOL_ID, TOOL_NAME)
    except ValueError as exc:
        raise pytest.UsageError(f"patch_reach: sys.monitoring tool id {TOOL_ID} is taken "
                                f"({sys.monitoring.get_tool(TOOL_ID)})") from exc
    try:
        census = _Census(importlib.import_module(old),
                         [importlib.import_module(name) for name in config.getoption("patch_reach_new")],
                         Path(out))
    except BaseException:
        sys.monitoring.free_tool_id(TOOL_ID)
        raise
    sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.PY_START, census.on_start)
    for code in census.watched:
        sys.monitoring.set_local_events(TOOL_ID, code, sys.monitoring.events.PY_START)
    _census = census


def pytest_unconfigure(config: pytest.Config) -> None:
    global _census
    if _census is None:
        return
    for code in _census.watched:
        sys.monitoring.set_local_events(TOOL_ID, code, 0)
    sys.monitoring.register_callback(TOOL_ID, sys.monitoring.events.PY_START, None)
    sys.monitoring.free_tool_id(TOOL_ID)
    _census = None


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    if _census is not None:
        _census.test = item.nodeid
    try:
        return (yield)
    finally:
        if _census is not None:
            _census.test = None


def pytest_sessionfinish(session: pytest.Session) -> None:
    if _census is not None:
        _census.write()


def pytest_terminal_summary(terminalreporter: Any) -> None:
    if _census is not None:
        terminalreporter.write_line(
            f"patch reach: {len(_census.watched)} functions watched, {_census.counts['own']} own and "
            f"{_census.counts['missed']} missed hits -> {_census.out}")
