"""P4a of BLOG_DEPARTMENT v0.2: the keyword pipeline, replayed offline from a recorded run and compared.

The vault's keyword pipeline (``tools/kw_pipeline.py``) still runs outside the system, from root's daily cron, because
it reads Naver and Google and the Search Ad API. Thomas chose (2026-10-27, "재생 그림자 먼저") to prove the port before
any of that network moves in: a full run started with ``KW_REPLAY_DIR`` leaves a **bundle** —

* ``meta.json`` — when it started, its arguments, whether it had a Search Ad key (never the key), its decision mode,
  the click share it scored with, and the sha256 of each tool file it ran;
* ``inputs.tar.gz`` — the text files it could read at the start (posts, the keyword folder, the weekly browser
  collection, the affiliate notes, the tools);
* ``cache/*.json`` — its caches at the start (a cache hit never reaches the network, so the response log alone would
  not be enough);
* ``http.jsonl.gz`` — every response it received, by URL (a fetch that failed is logged as a failure; request
  headers, where the Search Ad signature lives, are never logged);
* ``outputs/`` — the ``history/<date>.json`` and ``queue.md`` it wrote.

Here the generated port (``blog/kw_pipeline.py``, cut from the same vault file by ``scripts/blog_vendor.py``) runs the
same ``run`` on a working copy of the bundle, with the recorded clock and only the recorded responses, and the two
history files are compared field by field. The states:

* ``compared`` — the replay ran; ``agree`` says whether every compared field is equal;
* ``generator_stale`` — the bundle ran a tool file whose sha differs from the one the port was generated from (the
  P2 ``--check`` rule, applied per bundle). Regenerate in a PR; nothing is compared;
* ``replay_miss`` — the replay asked for a URL the recording does not hold. The bundle is incomplete, so a difference
  could not be told from a port error; the comparison is kept for reading but ``agree`` is None;
* ``unreadable`` / ``error:<Type>`` — the bundle or the replay broke; recorded, never raised past one bundle.

Nothing here opens a URL or writes outside a temporary directory, and the bundle folder is mounted read-only.
"""

from __future__ import annotations

import contextlib
import gzip
import importlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
import zlib
from collections import defaultdict, deque
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

REPLAY_DIR_ENV = "MVP_KW_REPLAY_DIR"
BUNDLE_NAME = re.compile(r"^\d{8}-\d{6}$")
BUNDLE_SCHEMA = "kw_replay_bundle.v1"
TOP_PER_LANE = 10
DIFFER_MAX = 20
PER_FIRE = 2

RECORD_KIND = "blog_kw_replay"
RECORD_SCHEMA = "schemas/blog_kw_replay.v0.1.schema.json"
RECORD_SCHEMA_VERSION = "blog_kw_replay.v0.1"


class RecordedFailure(Exception):
    """The recorded run's fetch of this URL failed; the replay fails the same way."""


class Transport:
    """Answers each URL with the recorded bodies in the order they were received, and moves the replay's clock to the
    time the recorded run received each one (the pipeline reads the clock right after some fetches). Counts what it
    could not answer."""

    def __init__(self, rows: Iterable[Mapping[str, Any]], miss: type[Exception]):
        # Bodies are held compressed (a day's result pages are a few hundred MB as text) and inflated when served.
        self.bodies: dict[str, deque] = defaultdict(deque)
        self.recorded = 0
        for row in rows:
            body = row.get("body")
            self.bodies[str(row.get("url"))].append(
                {"at": row.get("at"), "epoch": row.get("epoch"),
                 "z": None if body is None else zlib.compress(str(body).encode("utf-8"), 1)})
            self.recorded += 1
        self.miss = miss
        self.misses = 0
        self.served = 0
        self.clock: datetime | None = None
        self.epoch: float | None = None

    def __call__(self, url: str) -> str:
        queue = self.bodies.get(url)
        if not queue:
            self.misses += 1
            raise self.miss(url)
        row = queue.popleft() if len(queue) > 1 else queue[0]
        if row.get("at"):
            self.clock = datetime.fromisoformat(str(row["at"]))
        if isinstance(row.get("epoch"), (int, float)):
            self.epoch = float(row["epoch"])
        if row["z"] is None:
            raise RecordedFailure(url)
        self.served += 1
        return zlib.decompress(row["z"]).decode("utf-8")


def replay_dir() -> Path | None:
    value = os.environ.get(REPLAY_DIR_ENV)
    return Path(value) if value else None


def bundles(root: Path) -> list[Path]:
    """Finished bundles, oldest first."""
    out = []
    for path in sorted(root.iterdir()) if root.is_dir() else []:
        if not BUNDLE_NAME.match(path.name) or not path.is_dir():
            continue
        meta = _meta(path)
        if meta is not None and meta.get("finished") is True:
            out.append(path)
    return out


def _meta(bundle: Path) -> Mapping[str, Any] | None:
    try:
        meta = json.loads((bundle / "meta.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return meta if isinstance(meta, Mapping) and meta.get("schema") == BUNDLE_SCHEMA else None


def _norm(text: Any) -> str:
    return re.sub(r"\s+", "", str(text)).casefold()


def _http_rows(bundle: Path) -> Iterator[Mapping[str, Any]]:
    with gzip.open(bundle / "http.jsonl.gz", "rt", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _stale(meta: Mapping[str, Any], generated: Mapping[str, str]) -> list[str]:
    ran = meta.get("tools_sha256") if isinstance(meta.get("tools_sha256"), Mapping) else {}
    return sorted(name for name, sha in generated.items() if ran.get(name) != sha)


def _args(meta: Mapping[str, Any]) -> list[str]:
    """The recorded arguments without ``--max-age N``: a bundle exists only for a run that went the full way."""
    args = [str(a) for a in meta.get("args") or []]
    if "--max-age" in args:
        i = args.index("--max-age")
        del args[i:i + 2]
    return args


def _decision(c: Mapping[str, Any]) -> tuple:
    d = c.get("decision") if isinstance(c.get("decision"), Mapping) else {}
    return (d.get("decision"), d.get("rule"), d.get("matched"))


def compare(recorded: Mapping[str, Any], replayed: Mapping[str, Any]) -> dict[str, Any]:
    """The two history files, field by field: the candidate set with each one's lane, score and decision, the top
    ``TOP_PER_LANE`` per lane, the Tistory candidates, the out-of-league list and the rank table."""
    def index(h):
        return {_norm(c.get("kw")): c for c in h.get("candidates") or [] if isinstance(c, Mapping)}

    rec, rep = index(recorded), index(replayed)
    differ: list[dict[str, str]] = []
    for kw in sorted(set(rec) | set(rep)):
        if kw not in rep:
            differ.append({"kw": rec[kw].get("kw", kw), "field": "missing"})
            continue
        if kw not in rec:
            differ.append({"kw": rep[kw].get("kw", kw), "field": "extra"})
            continue
        for field, get in (("lane", lambda c: c.get("lane")), ("score", lambda c: c.get("score")),
                           ("decision", _decision)):
            if get(rec[kw]) != get(rep[kw]):
                differ.append({"kw": rec[kw].get("kw", kw), "field": field})

    def tops(h):
        lanes: dict[str, list] = defaultdict(list)
        for c in h.get("candidates") or []:
            if isinstance(c, Mapping):
                lanes[str(c.get("lane"))].append(c)
        return {lane: {_norm(c.get("kw")) for c in sorted(cs, key=lambda c: -(c.get("score") or 0))[:TOP_PER_LANE]}
                for lane, cs in lanes.items()}

    def kws(h, key):
        return {_norm(c.get("kw")) for c in h.get(key) or [] if isinstance(c, Mapping)}

    def ranks(h):
        return sorted((str(r.get("file")), repr(r.get("rank"))) for r in h.get("ranks") or [] if isinstance(r, Mapping))

    fields = {
        "candidates_recorded": len(rec), "candidates_replayed": len(rep),
        "top_sets_equal": tops(recorded) == tops(replayed),
        "tistory_recorded": len(kws(recorded, "tistory")), "tistory_replayed": len(kws(replayed, "tistory")),
        "tistory_equal": kws(recorded, "tistory") == kws(replayed, "tistory"),
        "out_of_league_equal": kws(recorded, "out_of_league") == kws(replayed, "out_of_league"),
        "ranks_equal": ranks(recorded) == ranks(replayed),
        "fields_differing": len(differ),
    }
    agree = (not differ and fields["top_sets_equal"] and fields["tistory_equal"] and fields["out_of_league_equal"]
             and fields["ranks_equal"])
    return {"agree": agree, "comparison": fields, "differ": [{**d, "kw": str(d["kw"])[:80]} for d in differ[:DIFFER_MAX]]}


def _extract(bundle: Path, work: Path) -> None:
    with tarfile.open(bundle / "inputs.tar.gz", "r:gz") as tar:
        tar.extractall(work, filter="data")       # refuses absolute paths, '..' and links that leave ``work``
    (work / "cache").mkdir()
    for path in sorted((bundle / "cache").glob("*.json")):
        shutil.copyfile(path, work / "cache" / path.name)


def replay(bundle: Path) -> dict[str, Any]:
    """Re-run one bundle and compare. Never raises for a broken bundle; the state says what happened."""
    from . import affiliate, kw_taxonomy
    from . import kw_pipeline as kp

    out: dict[str, Any] = {"bundle": bundle.name, "run_started": None, "state": "unreadable", "agree": None,
                           "responses": 0, "misses": 0, "stale": [], "comparison": None, "differ": []}
    meta = _meta(bundle)
    if meta is None or meta.get("finished") is not True:
        return out
    out["run_started"] = str(meta.get("started"))[:25]
    stale = _stale(meta, kp.GENERATED_FROM_SHA256)
    if stale:
        out.update(state="generator_stale", stale=stale)
        return out
    try:
        recorded = json.loads((bundle / "outputs" / str(meta["history"])).read_text(encoding="utf-8"))
        clock = datetime.fromisoformat(str(meta["started"]))
    except (OSError, KeyError, UnicodeDecodeError, ValueError, EOFError):
        return out
    with tempfile.TemporaryDirectory(prefix="kw-replay-") as tmp:
        work = Path(tmp)
        try:
            _extract(bundle, work)
            # A fresh module state per bundle: the pipeline memoises registries and lookups in module globals.
            for module in (kw_taxonomy, affiliate, kp):
                importlib.reload(module)
            transport = Transport(_http_rows(bundle), kp.ReplayMiss)
            out["responses"] = transport.recorded
            kp.configure(work, cache_dir=work / "cache", clock=clock, epoch=float(meta["started_epoch"]),
                         has_vol=bool(meta.get("has_vol")), decision_mode=str(meta.get("decision_mode")),
                         transport=transport)
            kp.CLICK_SHARE, kp.CLICK_SHARE_WHY = kp.measured_click_share()
            with contextlib.redirect_stdout(io.StringIO()):
                kp.run(_args(meta))
            replayed = json.loads((work / "analytics" / "keywords" / "history" / str(meta["history"]))
                                  .read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001 — one broken bundle is a recorded state, never the fire's failure
            out["state"] = f"error:{type(exc).__name__}"
            return out
    out["misses"] = transport.misses
    result = compare(recorded, replayed)
    out.update(comparison=result["comparison"], differ=result["differ"])
    if transport.misses:
        out["state"] = "replay_miss"
    else:
        out.update(state="compared", agree=result["agree"])
    return out


# --- the daily fire (scheduler kind ``blog_kw_replay``) ----------------------------------------------------------

def build_record(result: Mapping[str, Any], *, now: str) -> dict[str, Any]:
    from runtime.read_only_kernel import integrity

    from .. import schema_cache
    from ..paths import repo_root

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "replay_id": integrity.short_id("blogkwreplay", {"bundle": result["bundle"], "replayed_at": now}),
        "replayed_at": now,
        **{key: result[key] for key in ("bundle", "run_started", "state", "agree", "responses", "misses", "stale",
                                        "comparison", "differ")},
    }
    schema_cache.validate_against_schema(record, repo_root() / RECORD_SCHEMA, RECORD_KIND)
    return record


def fire(ledger: Any, *, now: str, root: Path | None = None) -> str:
    """Replay up to ``PER_FIRE`` finished bundles that have no record yet, oldest first, one record each. An unset or
    absent bundle folder is ``blog_kw_replay_no_bundles`` (the root cron may not record yet), not a failure."""
    folder = root if root is not None else replay_dir()
    if folder is None or not folder.is_dir():
        return "blog_kw_replay_no_bundles"
    done = {str(row["record"].get("bundle")) for row in ledger.iter_records(kinds=[RECORD_KIND])
            if row.get("kind") == RECORD_KIND}
    todo = [b for b in bundles(folder) if b.name not in done][:PER_FIRE]
    if not todo:
        return "blog_kw_replay_nothing_new"
    states = []
    for bundle in todo:
        record = build_record(replay(bundle), now=now)
        ledger.append_records(record["replay_id"], {RECORD_KIND: record})
        states.append(f"{bundle.name}:{record['state']}" + ("" if record["agree"] is None else f"/{'agree' if record['agree'] else 'differ'}"))
    return "blog_kw_replayed=" + ",".join(states)
