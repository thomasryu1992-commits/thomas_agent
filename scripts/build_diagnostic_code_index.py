#!/usr/bin/env python3
"""Generate ``docs/DIAGNOSTIC_CODE_INDEX.md`` — every reason code, where it is raised, and why.

``REMAINING_WORK.md`` §G3: the diagnostics outgrew their index. A large ``reason_code``
vocabulary is correct for a fail-closed system; what was missing is that **reading a code back to
its cause is the operator's main diagnostic path and nothing mapped one to the other.**

Extraction is AST, not grep, and the difference is the first thing this found. §G3's own
`grep -rhoE '"[A-Z][A-Z0-9_]{6,}"'` counted every upper-case string literal in the package —
record types, status values, provenance labels — and reported ~1,188. The codes that are actually
*raised* number **far fewer**: this walks every call to a class whose name ends in ``Error``,
``Blocked`` or ``Refused`` and takes the literal first positional argument or ``reason_code=``.

For each site it records the module, the line, the exception class, the enclosing function and —
where the raise sits inside one — the guarding ``if`` condition, unparsed. That last column is
the "why" §G3 asked for: not a description someone wrote and let rot, but the test the code is
actually behind.

Deliberately not in ``generated/``: that tree is governed by ``GENERATED_ARTIFACT_INDEX.yaml``
and registering there is a governance surface this does not need. It is a reading aid, and the
freshness test in ``tests/test_diagnostic_code_index.py`` is what keeps it true.

Run:  python scripts/build_diagnostic_code_index.py          # writes the doc
      python scripts/build_diagnostic_code_index.py --check  # exits 1 if stale
"""

from __future__ import annotations

import argparse
import ast
import pathlib
import sys
from collections import Counter, defaultdict
from typing import Any, Iterator, Mapping, NamedTuple

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "runtime"
OUTPUT_REL = "docs/DIAGNOSTIC_CODE_INDEX.md"


def _is_error_class(name: str) -> bool:
    return name.endswith(("Error", "Blocked", "Refused"))


class Site(NamedTuple):
    code: str
    module: str
    line: int
    error_class: str
    function: str | None
    condition: str | None


def module_string_constants(tree: ast.Module) -> dict[str, str]:
    """Module-scope ``NAME = "LITERAL"`` bindings that a raise site may name instead of inlining.

    Resolving these is not the guessing this file refuses to do. ``LIVE_HISTORY_TAMPERED =
    "LIVE_HISTORY_TAMPERED"`` at module scope has exactly one value, readable without executing
    anything — and 79 raise sites were being reported as *"built at runtime"* on that basis alone,
    including the live P&L ledger's and the canary registry's own tamper codes, which are the
    codes an operator is most likely to be holding when they come here.

    Two bindings are dropped rather than resolved, because either could make the index lie:

    * a name assigned twice at module scope with different values — its value at the raise depends
      on execution order, which is the thing this file does not model;
    * a name also bound inside any function in the module (parameter or assignment) — the raise
      may be reading the local one, and an index that silently picks the module value would name
      the wrong code with full confidence.
    """
    constants: dict[str, str] = {}
    ambiguous: set[str] = set()
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            name = node.targets[0].id
            if name in constants and constants[name] != node.value.value:
                ambiguous.add(name)
            constants[name] = node.value.value

    shadowed: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                    args.vararg, args.kwarg):
            if arg is not None:
                shadowed.add(arg.arg)
        for inner in ast.walk(node):
            if isinstance(inner, ast.Assign):
                for target in inner.targets:
                    if isinstance(target, ast.Name):
                        shadowed.add(target.id)
            elif isinstance(inner, (ast.AugAssign, ast.AnnAssign)):
                if isinstance(inner.target, ast.Name):
                    shadowed.add(inner.target.id)

    return {n: v for n, v in constants.items() if n not in ambiguous and n not in shadowed}


def _string_value(node: ast.AST | None, constants: Mapping[str, str]) -> str | None:
    """A string this node certainly evaluates to — a literal, or a resolved module constant."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _literal_code(call: ast.Call, constants: Mapping[str, str]) -> str | None:
    """The reason code, from the first positional argument or ``reason_code=``.

    A non-literal that is not a resolvable module constant (an f-string, a local, an attribute) is
    skipped rather than guessed at: an index that invented a code would be worse than one that
    admits a gap, and the gap is countable.
    """
    for keyword in call.keywords:
        if keyword.arg == "reason_code":
            return _string_value(keyword.value, constants)
    if call.args:
        return _string_value(call.args[0], constants)
    return None


# A shared primitive that raises on its caller's behalf takes the code as a parameter, so the
# raise itself carries a *variable* and the literal sits at the call site one frame up. Walking
# only error-class calls therefore loses the code entirely the moment a reader delegates its I/O
# — measured: folding `counterfactual`'s reader into `jsonl` dropped
# `COUNTERFACTUAL_HISTORY_UNREADABLE` out of this index while it was still being raised, with the
# literal plainly visible at the call. These are the parameter names those primitives use.
DELEGATED_CODE_KEYWORDS = ("read_code", "write_code")
# What they raise when the caller does not choose (``jsonl``'s documented default).
DELEGATED_DEFAULT_CLASS = "PersistenceError"


def _delegated_code(call: ast.Call, constants: Mapping[str, str]) -> tuple[str, str] | None:
    """``(code, error class)`` for a call that hands a literal code to a primitive that raises.

    The class comes from ``exc_type=`` when the caller names one and from the primitive's default
    otherwise — so the row says which except-clause actually sees it, not merely that it exists.
    """
    code: str | None = None
    error_class = DELEGATED_DEFAULT_CLASS
    for keyword in call.keywords:
        if keyword.arg in DELEGATED_CODE_KEYWORDS:
            resolved = _string_value(keyword.value, constants)
            if resolved is not None:
                code = resolved
        elif keyword.arg == "exc_type":
            named = keyword.value
            if isinstance(named, ast.Name):
                error_class = named.id
            elif isinstance(named, ast.Attribute):
                error_class = named.attr
    return (code, error_class) if code is not None else None


def collect_sites(source_dir: pathlib.Path = SOURCE_DIR) -> tuple[list[Site], int]:
    """Every raise site carrying a literal code, plus how many were skipped as non-literal."""
    sites: list[Site] = []
    skipped = 0
    for path in sorted(source_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - a file that does not parse is not indexable
            continue
        constants = module_string_constants(tree)
        parents: dict[ast.AST, ast.AST] = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None)
            if not name:
                continue
            if _is_error_class(name):
                code = _literal_code(node, constants)
                if code is None:
                    skipped += 1
                    continue
            else:
                delegated = _delegated_code(node, constants)
                if delegated is None:
                    continue
                code, name = delegated
            enclosing_function: str | None = None
            condition: str | None = None
            walker: ast.AST = node
            while walker in parents:
                walker = parents[walker]
                if enclosing_function is None and isinstance(
                        walker, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    enclosing_function = walker.name
                if condition is None and isinstance(walker, ast.If):
                    try:
                        condition = ast.unparse(walker.test)
                    except Exception:  # pragma: no cover - unparse is total in practice
                        condition = None
            # `as_posix()`, not `str()`: the committed index is generated on one platform and
            # checked on both, and `str()` emits `runtime\mvp_runtime\...` on Windows — so every
            # row differs and the freshness test fails for a reason that has nothing to do with
            # the code having changed. Found by CI 2026-08-06, which is also the reason the
            # module path is normalised here rather than at each of the places that read it.
            sites.append(Site(code, path.relative_to(ROOT).as_posix(), node.lineno, name,
                              enclosing_function, condition))
    return sites, skipped


def _cell(value: Any, limit: int = 96) -> str:
    """Table-safe: pipes would split a column and a long condition would make the row unreadable."""
    if value is None:
        return "—"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render(sites: list[Site], skipped: int) -> str:
    by_code: dict[str, list[Site]] = defaultdict(list)
    for site in sites:
        by_code[site.code].append(site)
    modules_per_code = {code: sorted({s.module for s in group}) for code, group in by_code.items()}
    shared = {code: mods for code, mods in modules_per_code.items() if len(mods) > 1}
    classes = Counter(site.error_class for site in sites)

    out: list[str] = []
    out.append("# Diagnostic code index — GENERATED, do not edit by hand")
    out.append("")
    out.append("Regenerate with `python scripts/build_diagnostic_code_index.py`. "
               "`tests/test_diagnostic_code_index.py` fails when this file and the code disagree, "
               "so a new reason code lands here or the suite goes red.")
    out.append("")
    out.append("Answers the question `REMAINING_WORK.md` §G3 says an operator actually asks: "
               "**a code came out of the runtime — where is it raised, and what test is it "
               "behind?** The `condition` column is the guarding `if`, unparsed from the source, "
               "so it cannot drift from what the code does the way a written description would.")
    out.append("")
    out.append(f"- **{len(by_code)}** distinct codes across **{len(sites)}** raise sites")
    out.append(f"- **{len(classes)}** exception classes carry them")
    out.append(f"- **{len(shared)}** codes are raised from more than one module (see below)")
    out.append(f"- **{skipped}** raise sites build their code at runtime rather than from a "
               "literal and are not indexable; they are counted rather than guessed at")
    out.append("")
    out.append("## Codes raised from more than one module")
    out.append("")
    out.append("Not automatically a defect — `APPROVAL_EXPIRED` meaning one thing in seven "
               "modules is shared vocabulary working correctly. It is here because the opposite "
               "case looks identical from the outside: one code, two meanings, and an operator "
               "reading it back gets the wrong module. The test pins this set, so the next one "
               "is a decision rather than an accident.")
    out.append("")
    out.append("| code | modules |")
    out.append("|---|---|")
    for code in sorted(shared):
        names = ", ".join(f"`{pathlib.Path(m).name}`" for m in shared[code])
        out.append(f"| `{code}` | {names} |")
    out.append("")
    out.append("## Every code")
    out.append("")
    out.append("| code | class | module | line | function | condition |")
    out.append("|---|---|---|---|---|---|")
    for code in sorted(by_code):
        for site in sorted(by_code[code], key=lambda s: (s.module, s.line)):
            out.append(
                f"| `{code}` | `{site.error_class}` | `{site.module}` | {site.line} "
                f"| `{_cell(site.function)}` | `{_cell(site.condition)}` |"
            )
    out.append("")
    return "\n".join(out)


def build() -> str:
    sites, skipped = collect_sites()
    return render(sites, skipped)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed index is stale, writing nothing")
    args = parser.parse_args(argv)
    rendered = build()
    target = ROOT / OUTPUT_REL
    if args.check:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != rendered:
            print(f"STALE: {OUTPUT_REL} does not match the code; "
                  "run scripts/build_diagnostic_code_index.py", file=sys.stderr)
            return 1
        print(f"OK: {OUTPUT_REL} is current")
        return 0
    target.write_text(rendered, encoding="utf-8")
    print(f"wrote {OUTPUT_REL}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
