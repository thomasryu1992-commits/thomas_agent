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
import hashlib
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


# --- P4a: the keyword pipeline, generated for offline replay (Thomas 2026-10-27 "재생 그림자 먼저") ------------------
#
# The vault's ``tools/kw_pipeline.py`` (with ``affiliate_fetch.py``'s note parser) is generated into ``blog/`` so the
# system can re-run a recorded run (``KW_REPLAY_DIR`` bundle) and compare outputs. The edits differ from the checkers':
#
# - **No network, by substitution.** ``http`` is swapped for a call into the replay's transport, which answers only
#   with the bodies the recorded run received (a URL it never fetched raises ``ReplayMiss``). ``urllib.request`` goes.
# - **The recorded run's clock and branches.** ``now``, ``time.time`` and ``time.sleep`` read the recording (sleep is a
#   no-op); ``load_keys`` answers "present" exactly when the recorded run had a Search Ad key (no key is ever read), and
#   ``decision_mode`` the recorded mode.
# - **Writes are redirected, not removed.** Every path derives from ``VAULT``/``CACHE_DIR``; ``configure`` points them
#   at the replay's working copy of the bundle, so the run writes there and nowhere else. Removing writes would change
#   the control flow the comparison is meant to test.
# - **Nothing at import time.** The module-level caches and ``POSTS_PER_DAY`` are built by ``configure``; every
#   module-level assignment that depends on a path is re-run there (found by name, below), so none is listed by hand.
REPLAY_SOURCES = {"kw_pipeline": "kw_pipeline.py", "affiliate": "affiliate_fetch.py", "kw_taxonomy": "blog_taxonomy.py"}

REPLAY_REPLACE = {
    "kw_pipeline": [
        ("import urllib.parse, urllib.request\n", "import urllib.parse\n"),
        # Nothing is signed: the replay sends no request and reads no key, so the Search Ad signature goes with hmac.
        ("import base64, collections, datetime, glob, hashlib, hmac, html,", "import base64, collections, datetime, glob, hashlib, html,"),
        ('    sig = base64.b64encode(hmac.new(keys["SECRET_KEY"].encode(), f"{ts}.GET.{uri}".encode(), hashlib.sha256).digest()).decode()\n',
         '    sig = ""   # generated: nothing is signed (the replay sends no request)\n'),
        ('VAULT = os.environ.get("BLOG_VAULT") or ("/root/obsidian-thomas" if os.path.isdir("/root/obsidian-thomas") '
         'else os.path.dirname(os.path.dirname(os.path.realpath(__file__))))\n', 'VAULT = ""   # generated: set by configure()\n'),
        ('CACHE_DIR = os.path.expanduser("~/.cache/blog-kw")\n', 'CACHE_DIR = ""   # generated: set by configure()\n'),
        ('C_AC, C_SERP, C_PROF, C_CHAN = (Cache(n) for n in ("ac", "serp", "prof", "chan"))\n',
         "C_AC = C_SERP = C_PROF = C_CHAN = None   # generated: built by configure()\n"),
        ('REPLAY_DIR = os.environ.get("KW_REPLAY_DIR", "")\n', 'REPLAY_DIR = ""   # generated: a replay never records\n'),
    ],
    "affiliate": [
        ("VAULT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))\n", 'VAULT = ""   # generated: set by configure()\n'),
    ],
    # The pipeline's own copy of the taxonomy: the checkers' edits, but its writers kept (the pipeline appends the
    # update hand-over and the discrepancy rows, into the replay's working copy).
    "kw_taxonomy": REPLACE["taxonomy"],
}
# Whole lines replaced by their start (the vault keeps a comment after the code); exactly one line must match.
REPLAY_LINES = {
    "kw_pipeline": [
        ('C_VOL = Cache("vol", flush_every=5000)', "C_VOL = None   # generated: built by configure()\n"),
        ("POSTS_PER_DAY = _naver_day_max()", "POSTS_PER_DAY = 3   # generated: read from the bundle's naver_reserve.py by configure()\n"),
    ],
    "affiliate": [],
    "kw_taxonomy": [],
}
# Every occurrence (at least one must exist).
REPLAY_ALL = {
    "kw_pipeline": [
        ("sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))", "pass"),
        ("import blog_taxonomy as bt", "from . import kw_taxonomy as bt"),
        ("import affiliate_fetch as af", "from . import affiliate as af"),
        ("os.path.dirname(os.path.realpath(__file__))", "_TOOLS_DIR"),
        ("time.time()", "_epoch()"),
        ("time.sleep(", "_sleep("),
    ],
    "affiliate": [],
    "kw_taxonomy": [],
}
REPLAY_SWAP = {
    "kw_pipeline": {
        "http": '''def http(url, headers=None, timeout=20, pause=0.3):
    """Generated: the runtime opens no URL. The replay answers with the body the recorded run received for it."""
    if _TRANSPORT is None:
        raise ReplayMiss(url)
    return _TRANSPORT(url)
''',
        "now": '''def now():
    """Generated: the recorded run's clock — its start, then the time each recorded response arrived."""
    return getattr(_TRANSPORT, "clock", None) or _CLOCK
''',
        "load_keys": '''def load_keys():
    """Generated: no key is read. "Present" exactly when the recorded run had one, so the same branch runs; the Search
    Ad responses come from the recording."""
    return {"CUSTOMER_ID": "replay", "ACCESS_LICENSE": "replay", "SECRET_KEY": "replay", "_src": "replay"} if _HAS_VOL else None
''',
        "decision_mode": '''def decision_mode():
    """Generated: the recorded run's mode."""
    return _DECISION_MODE
''',
    },
    "affiliate": {},
    "kw_taxonomy": {},
}
REPLAY_DROP = {"kw_pipeline": {"main"},
               "kw_taxonomy": {"live_category", "cmd_classify", "cmd_validate", "cmd_discrepancy", "cmd_decide", "main"}}
# affiliate_fetch is a Mac tool (it drives Chrome); only its note parser is used by the pipeline.
REPLAY_KEEP_FUNCTIONS = {"affiliate": {"norm", "_is_text_line", "_strip_label", "targets", "clean_url"}}

REPLAY_TAIL = {
    "kw_pipeline": '''

# --- generated: replay configuration (BLOG_DEPARTMENT v0.2 P4a) --------------------------------------------------
_TOOLS_DIR = ""
_CLOCK = None
_EPOCH = 0.0
_HAS_VOL = False
_DECISION_MODE = "shadow"
_TRANSPORT = None


class ReplayMiss(Exception):
    """The replay asked for a URL the recorded run never fetched."""


def _epoch():
    return getattr(_TRANSPORT, "epoch", None) or _EPOCH


def _sleep(_seconds):
    return None


def configure(vault_root, *, cache_dir, clock, epoch, has_vol, decision_mode, transport):
    """Point every path at a replay's working copy and pin the recorded run's clock, key presence and mode."""
    global VAULT, CACHE_DIR, _TOOLS_DIR, _CLOCK, _EPOCH, _HAS_VOL, _DECISION_MODE, _TRANSPORT
    global C_AC, C_SERP, C_PROF, C_CHAN, C_VOL, POSTS_PER_DAY
    {path_globals}
    VAULT, CACHE_DIR = str(vault_root), str(cache_dir)
    _TOOLS_DIR = os.path.join(VAULT, "tools")
    _CLOCK, _EPOCH, _HAS_VOL, _DECISION_MODE, _TRANSPORT = clock, float(epoch), bool(has_vol), decision_mode, transport
{path_assignments}
    C_AC, C_SERP, C_PROF, C_CHAN = (Cache(n) for n in ("ac", "serp", "prof", "chan"))
    C_VOL = Cache("vol", flush_every=5000)
    POSTS_PER_DAY = _naver_day_max()
    from . import affiliate, kw_taxonomy
    kw_taxonomy.configure(VAULT)
    affiliate.configure(VAULT)
''',
    "affiliate": '''

def configure(vault_root):
    """Point the note parser at a vault (generated: replaces the import-time __file__ lookup)."""
    global VAULT, KW_DIR, CLIP_DIR, DEBUG_DIR
    VAULT = str(vault_root)
    KW_DIR = os.path.join(VAULT, "제휴", "키워드")
    CLIP_DIR = os.path.join(VAULT, "제휴", "클립")
    DEBUG_DIR = os.path.join(CLIP_DIR, "_debug")
''',
    "kw_taxonomy": CONFIGURE["taxonomy"],
}
_PATH_ROOTS = {"VAULT", "CACHE_DIR", "_TOOLS_DIR"}


def _path_assignments(text: str) -> tuple[list[str], list[str]]:
    """The module-level assignments that depend (transitively) on a path root, in source order, and their names."""
    tree = ast.parse(text)
    lines = text.splitlines(keepends=True)
    roots, names, out = set(_PATH_ROOTS), [], []
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        target_names = [t.id for t in targets if isinstance(t, ast.Name)]
        if not target_names or set(target_names) & _PATH_ROOTS:
            continue
        used = {n.id for n in ast.walk(node.value) if isinstance(n, ast.Name)} if node.value is not None else set()
        if used & roots and not set(target_names) & {"C_AC", "C_VOL", "POSTS_PER_DAY"}:
            out.append("".join(lines[node.lineno - 1:node.end_lineno]))
            names += target_names
            roots |= set(target_names)
    return out, names


def generate_replay(key: str, vault: Path, commit: str) -> str:
    source = REPLAY_SOURCES[key]
    text = (vault / "tools" / source).read_text(encoding="utf-8")
    for old, new in REPLAY_REPLACE[key]:
        if text.count(old) != 1:
            raise SystemExit(f"{source}: expected exactly one occurrence of an edited line, found {text.count(old)}:\n{old}")
        text = text.replace(old, new)
    for start, new in REPLAY_LINES[key]:
        hits = [i for i, line in enumerate(text.splitlines(keepends=True)) if line.startswith(start)]
        if len(hits) != 1:
            raise SystemExit(f"{source}: expected exactly one line starting with {start!r}, found {len(hits)}")
        lines = text.splitlines(keepends=True)
        lines[hits[0]] = new
        text = "".join(lines)
    for old, new in REPLAY_ALL[key]:
        if old not in text:
            raise SystemExit(f"{source}: expected at least one occurrence of {old!r}")
        text = text.replace(old, new)
    if key in REPLAY_KEEP_FUNCTIONS:
        tree = ast.parse(text)
        names = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        missing = REPLAY_KEEP_FUNCTIONS[key] - names
        if missing:
            raise SystemExit(f"{source}: functions the replay keeps are gone: {sorted(missing)}")
        drop = names - REPLAY_KEEP_FUNCTIONS[key]
    else:
        drop = REPLAY_DROP.get(key, set())
    text = _cut(text, source, drop, REPLAY_SWAP[key])
    if text.startswith("#!"):
        text = text.split("\n", 1)[1]
    tail = REPLAY_TAIL[key]
    if key == "kw_pipeline":
        assignments, names = _path_assignments(text)
        tail = tail.replace("{path_globals}", "global " + ", ".join(names) if names else "pass")
        tail = tail.replace("{path_assignments}", "".join("    " + line if line.strip() else line
                                                          for block in assignments for line in block.splitlines(keepends=True)))
    if key == "kw_pipeline":
        # What a bundle is replayed against: the recording names the sha of each file it ran; a different one means the
        # generated module is stale for that bundle, and the replay says so instead of comparing.
        shas = {name: hashlib.sha256((vault / "tools" / name).read_bytes()).hexdigest()
                for name in ("kw_pipeline.py", "blog_taxonomy.py", "affiliate_fetch.py")}
        tail += f"\n\nGENERATED_FROM_SHA256 = {shas!r}\n"
    header = (f"# GENERATED by scripts/blog_vendor.py from the vault's tools/{source} @ {commit}. Do not edit by hand:\n"
              f"# change the vault file and regenerate (BLOG_DEPARTMENT v0.2 P4a). The edits are listed in that script.\n")
    return header + text.rstrip("\n") + "\n" + tail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--vault", required=True, type=Path)
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed modules are stale")
    args = parser.parse_args(argv)
    commit = subprocess.run(["git", "-C", str(args.vault), "log", "-1", "--format=%h", "--",
                             *(f"tools/{s}" for s in SOURCES.values())],
                            capture_output=True, text=True, check=True).stdout.strip() or "unknown"
    replay_commit = subprocess.run(["git", "-C", str(args.vault), "log", "-1", "--format=%h", "--",
                                    *(f"tools/{s}" for s in REPLAY_SOURCES.values())],
                                   capture_output=True, text=True, check=True).stdout.strip() or "unknown"
    stale = []
    jobs = [(key, lambda key=key: generate(key, args.vault, commit)) for key in SOURCES]
    jobs += [(key, lambda key=key: generate_replay(key, args.vault, replay_commit)) for key in REPLAY_SOURCES]
    for key, make in jobs:
        path = OUT_DIR / f"{key}.py"
        text = make()
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
