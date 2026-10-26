"""Generate ``runtime/mvp_runtime/blog/{preflight,variety,taxonomy}.py`` from the vault's own checkers.

BLOG_DEPARTMENT v0.2 P2 (Thomas 2026-10-26, "승인: BLOG-DEPT v0.2 §7 권고대로"): the system re-checks each new draft
with the same rules the drafting cron uses. Those rules live in the vault (``tools/blog-preflight.py``,
``tools/blog_variety.py``, ``tools/blog_taxonomy.py``) and change there, so the port is **generated, not
hand-copied**: this script reads the three files and applies a fixed list of mechanical edits, so the generated
modules are the vault's code minus exactly what the runtime may not do.

- **No network.** ``urllib.request`` and every function that opens a URL are removed. ``_resolves`` (link
  liveness, codes ``X2``) becomes the vault's own offline answer (``"unknown", "오프라인"``), which the vault
  checker already treats as "say nothing". Link liveness stays with the outsourced side (v0.2 §7 N1).
- **No writes.** The taxonomy's writers (``write_discrepancies``, ``append_updates``) and every CLI entry point
  are removed. The live-mode checker (``check_live``) is not ported.
- **Paths come from the caller.** Module-level ``VAULT``/``CORPORA`` derived from ``__file__`` or the environment
  become a ``configure(vault_root)`` call; nothing is read at import time.
- **Imports between the three are relative.** The ``sys.path.insert`` lines go.

Everything else is byte-for-byte the vault's. Each generated file names its source file and the vault commit it was
cut from. ``--check`` exits 1 when the committed modules differ from what the current vault would generate, which is
the signal to regenerate in a PR (the equality the P2 switch condition measures).

    python scripts/blog_vendor.py --vault /home/claude/obsidian-thomas [--check]
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT_DIR = REPO / "runtime" / "mvp_runtime" / "blog"
SOURCES = {"preflight": "blog-preflight.py", "variety": "blog_variety.py", "taxonomy": "blog_taxonomy.py"}

CONFIGURE = {
    "variety": '''

def configure(vault_root):
    """Point the corpus at a vault (generated: replaces the vault file's import-time BLOG_VAULT/__file__ lookup)."""
    global _VAULT, _CORPUS, CORPORA
    _VAULT = str(vault_root)
    _CORPUS = os.path.join(_VAULT, "content")
    CORPORA = {"naver": os.path.join(_CORPUS, "naver"), "tistory": os.path.join(_CORPUS, "tistory")}
''',
    "taxonomy": '''

def configure(vault_root):
    """Point the registries and corpus at a vault (generated: replaces the vault file's import-time lookup)."""
    global VAULT, KW_DIR, NAVER_DIR, CLUSTERS_F, ENTITIES_F, DISCREPANCY_F, UPDATE_F
    VAULT = str(vault_root)
    KW_DIR = os.path.join(VAULT, "analytics", "keywords")
    NAVER_DIR = os.path.join(VAULT, "content", "naver")
    CLUSTERS_F = os.path.join(KW_DIR, "clusters.tsv")
    ENTITIES_F = os.path.join(KW_DIR, "entities.tsv")
    DISCREPANCY_F = os.path.join(KW_DIR, "category_discrepancy.tsv")
    UPDATE_F = os.path.join(KW_DIR, "update_candidates.tsv")
''',
}

OFFLINE_RESOLVES = '''def _resolves(url):
    """Generated: the runtime opens no URL. The vault's offline answer — check_links/check_sources say nothing for it."""
    return "unknown", "오프라인"
'''

# (old, new) pairs applied in order; each old must occur exactly once in its source.
REPLACE = {
    "preflight": [
        ("import urllib.error\nimport urllib.parse\nimport urllib.request\n", "import urllib.parse\n"),
        ("    sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))\n    try:\n        import blog_taxonomy as bt\n",
         "    try:\n        from . import taxonomy as bt\n"),
    ],
    "variety": [
        ('_VAULT = os.environ.get("BLOG_VAULT") or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))\n'
         '_CORPUS = os.environ.get("BLOG_VARIETY_CORPUS") or os.path.join(_VAULT, "content")\n'
         'CORPORA = {"naver": os.path.join(_CORPUS, "naver"), "tistory": os.path.join(_CORPUS, "tistory")}\n',
         '_VAULT = _CORPUS = ""   # generated: set by configure(vault_root)\nCORPORA = {"naver": "", "tistory": ""}\n'),
        ("            sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))   # blog_taxonomy는 이 폴더의 링크(볼트 tools)\n"
         "            import blog_taxonomy as bt\n",
         "            from . import taxonomy as bt\n"),
    ],
    "taxonomy": [
        ("import datetime, html, io, json, os, re, sys, time, urllib.request\n", "import datetime, html, io, json, os, re, sys, time\n"),
        ('VAULT = os.environ.get("BLOG_VAULT") or os.path.dirname(os.path.dirname(os.path.realpath(__file__)))\n',
         'VAULT = ""   # generated: set by configure(vault_root)\n'),
    ],
}

# Top-level functions removed whole (network, writers, CLI, live mode).
DROP = {
    "preflight": {"fetch", "div_close", "check_live"},
    "variety": set(),
    "taxonomy": {"live_category", "write_discrepancies", "append_updates", "cmd_classify", "cmd_validate",
                 "cmd_discrepancy", "cmd_decide", "main"},
}
SWAP = {"preflight": {"_resolves": OFFLINE_RESOLVES}}


def _cut(text: str, name: str, drop: set[str], swap: dict[str, str]) -> str:
    """Remove the ``if __name__`` block and the named top-level functions; swap others for a replacement."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    spans: list[tuple[int, int, str]] = []
    for node in tree.body:
        start, end = node.lineno - 1, node.end_lineno
        if isinstance(node, ast.FunctionDef) and (node.name in drop or node.name in swap):
            spans.append((start, end, swap.get(node.name, "")))
        elif (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
              and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__"):
            spans.append((start, end, ""))
    found = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    missing = (drop | set(swap)) - found
    if missing:
        raise SystemExit(f"{name}: functions to remove/replace are gone from the source: {sorted(missing)}")
    for start, end, new in sorted(spans, reverse=True):
        lines[start:end] = [new] if new else []
    return "".join(lines)


def generate(key: str, vault: Path, commit: str) -> str:
    source = SOURCES[key]
    text = (vault / "tools" / source).read_text(encoding="utf-8")
    for old, new in REPLACE[key]:
        if text.count(old) != 1:
            raise SystemExit(f"{source}: expected exactly one occurrence of an edited line, found {text.count(old)}:\n{old}")
        text = text.replace(old, new)
    text = _cut(text, source, DROP[key], SWAP.get(key, {}))
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    header = (f"# GENERATED by scripts/blog_vendor.py from the vault's tools/{source} @ {commit}. Do not edit by hand:\n"
              f"# change the vault file and regenerate (BLOG_DEPARTMENT v0.2 P2). The edits are listed in that script.\n")
    return header + text.rstrip("\n") + "\n" + CONFIGURE.get(key, "")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed modules are stale")
    args = parser.parse_args(argv)
    commit = subprocess.run(["git", "-C", str(args.vault), "log", "-1", "--format=%h", "--",
                             *(f"tools/{s}" for s in SOURCES.values())],
                            capture_output=True, text=True, check=True).stdout.strip() or "unknown"
    stale = []
    for key in SOURCES:
        path = OUT_DIR / f"{key}.py"
        text = generate(key, args.vault, commit)
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        # The header's commit moves whenever any of the three files changes; compare the code below it.
        if current.split("\n", 2)[-1] != text.split("\n", 2)[-1]:
            stale.append(path.name)
            if not args.check:
                path.write_text(text, encoding="utf-8")
    if args.check:
        print("STALE: " + ", ".join(stale) if stale else "OK: the generated blog checkers match the vault")
        return 1 if stale else 0
    print("wrote " + ", ".join(stale) if stale else "unchanged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
