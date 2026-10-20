"""Capture door: root takes the official-page screenshots that the ``claude`` account can no longer take itself.

    python3 -I /usr/local/sbin/thomas-capture-door --root /var/lib/thomas-capture --owner claude

Why (Thomas 2026-10-20, Y3 (가)): ``blog-capture.py`` drives a headless Chromium in Docker. C6b moved Claude to the
``claude`` account, which is outside the docker group by design: the group reaches every mount, so it is root.
Without a capture, every new Naver draft lands as ``hold`` (blog-preflight Y3). The door keeps that boundary.
``claude`` drops a small request file, and root (this script, started by a systemd path unit) validates it and
takes the screenshot with the same image and in-container script as before. It hands back only the raw
screenshot and a result record. ``claude`` composes the PNG and its metadata exactly as it does today.

The protocol, under ``--root`` (default ``/var/lib/thomas-capture``, made by the install):
- ``req/``  root:claude 1730. ``claude`` creates ``<id>.json`` and can neither list nor delete other files.
  It writes ``.<id>.tmp`` first and renames it, so a pass never reads a half-written request. Dot names are
  skipped, and removed once older than a minute.
  Request fields: ``{"id", "url", "text", "click"}``, where ``<id>`` is 32 hex characters and the file stem.
- ``out/``  root:claude 0750. The door writes ``<id>.json`` (the result) and, on success, ``<id>.png`` (the raw
  1200x820 screenshot), each 0640 root:claude, through a temporary name and a rename.
- ``work/`` root 0700. Holds the per-request directory bind-mounted into the container. It is never under
  ``/tmp``: dockerd resolves ``-v`` on the host's mount namespace, so a private tmp would mount the wrong
  directory.
- ``door.log`` root 0600. One line per request: id, then the host or the rejection code.

What root trusts from ``claude``, and nothing more:
- A request is read with ``O_NOFOLLOW`` and must be a regular file owned by the owner account, at most 4 KiB.
- The URL must be ``https``. Its host must be a name, not an IP literal, and outside the blocked list (the
  same list as blog-capture: Naver, Tistory, Daum, Coupang). Every address it resolves to must be global.
- The quoted text is 1-200 characters with no control characters. The click text is at most 60.
- At most ``MAX_PER_HOUR`` captures an hour.
- A rejection is written as a fixed code. **Request contents are never echoed** into a result or the log, so a
  request that points somewhere else (a link raced in after the checks) cannot carry what root read back out.

Container hardening, tighter than the old tool's ``--ipc=host``: ``--shm-size``, ``--cap-drop ALL``,
``no-new-privileges``, pids, memory and CPU limits. The residual risk is unchanged from before C6b, when root
Claude ran this same tool: the container can reach the network, so DNS rebinding or a host-local service
stays reachable.

Exit status: 0 always after a pass (per-request failures are results, not errors), 2 if the layout is not the
installed one (wrong owner or mode on ``req/`` or ``out/``). Then it refuses to run at all.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import ipaddress
import json
import os
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

BASE = "mcr.microsoft.com/playwright/python:v1.47.0-jammy"
IMAGE = "blog-capture:1"
BLOCKED = ("naver.com", "naver.me", "naver.net", "tistory.com", "daum.net", "coupang.com", "coupa.ng")
ID_RE = re.compile(r"^[0-9a-f]{32}$")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
MAX_REQUEST_BYTES = 4096
MAX_TEXT, MAX_CLICK = 200, 60
MAX_PER_HOUR = 30
MAX_PER_PASS = 5
OUT_TTL_SECONDS = 24 * 3600
MAX_SHOT_BYTES = 10 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024
RUN_TIMEOUT = 150
STALE_TEMP_SECONDS = 60   # the client writes ".<id>.tmp" and renames it to "<id>.json"; a pass skips dot names this young

# The in-container script: the same one blog-capture.py runs (2026-10-16), writing /out/shot.png and /out/result.json.
INNER = r'''
import json, sys
from playwright.sync_api import sync_playwright
url, text = sys.argv[1], sys.argv[2]
click = sys.argv[3] if len(sys.argv) > 3 else ""
res = {"ok": False}
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 820}, locale="ko-KR", timezone_id="Asia/Seoul",
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36")
    try:
        r = pg.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            pg.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        res["status"] = r.status if r else 0
        res["final_url"] = pg.url
        res["title"] = pg.title()
        if not r or r.status >= 400:
            res["reason"] = f"HTTP {res['status']}"
        else:
            if click:
                pg.get_by_text(click, exact=False).first.click(timeout=10000)
                pg.wait_for_timeout(1500)
            loc = pg.get_by_text(text, exact=False)
            el = next((c for c in loc.all() if c.is_visible()), None) if loc.count() else None
            if loc.count() == 0:
                res["reason"] = "문구 없음"
            elif el is None:
                res["reason"] = "문구 없음(접힌 곳·숨은 요소에만 있음)"
            else:
                el.scroll_into_view_if_needed(timeout=10000)
                el.evaluate("e => { e.style.outline = '4px solid #E8590C'; e.style.outlineOffset = '3px'; }")
                bb = el.bounding_box()
                if not bb or bb["width"] < 3 or bb["height"] < 3:
                    res["reason"] = "문구 없음(화면에 그려지지 않음 — 접힌 목록이면 --click)"
                    raise SystemExit
                y0 = pg.evaluate("window.scrollY")
                total = pg.evaluate("document.documentElement.scrollHeight")
                cy = y0 + bb["y"] + bb["height"] / 2
                top = max(0, min(cy - 410, max(0, total - 820)))
                if not (top <= y0 + bb["y"] and y0 + bb["y"] + bb["height"] <= top + 820):
                    res["reason"] = "문구 없음(문구 상자가 찍을 영역 밖)"
                    raise SystemExit
                pg.screenshot(path="/out/shot.png", full_page=True, clip={"x": 0, "y": top, "width": 1200, "height": 820})
                res.update(ok=True, box=bb)
    except SystemExit:
        pass
    except Exception as e:
        res["reason"] = (type(e).__name__ + ": " + str(e))[:160]
    b.close()
json.dump(res, open("/out/result.json", "w"), ensure_ascii=False)
'''

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Resolver = Callable[..., list]


class Rejected(Exception):
    """A request the door will not act on. ``code`` is fixed text — never the request's contents."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LayoutError(Exception):
    pass


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def req(self) -> Path:
        return self.root / "req"

    @property
    def out(self) -> Path:
        return self.root / "out"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def log(self) -> Path:
        return self.root / "door.log"

    @property
    def lock(self) -> Path:
        return self.work / ".lock"


def check_layout(paths: Paths, owner_gid: int, root_uid: int = 0) -> None:
    """The install's modes (see the module docstring). Anything else and the door does not run."""
    want = {paths.req: (stat.S_ISVTX | 0o730, owner_gid), paths.out: (0o750, owner_gid), paths.work: (0o700, None)}
    for d, (mode, gid) in want.items():
        try:
            st = os.lstat(d)
        except FileNotFoundError:
            raise LayoutError(f"{d} missing") from None
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != root_uid or stat.S_IMODE(st.st_mode) != mode or (gid is not None and st.st_gid != gid):
            raise LayoutError(f"{d} is not the installed layout (want {oct(mode)} root" + (f":{gid}" if gid is not None else "") + ")")


def read_request(path: Path, owner_uid: int) -> dict:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        raise Rejected("unreadable") from None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise Rejected("not_regular")
        if st.st_uid != owner_uid:
            raise Rejected("wrong_owner")
        if st.st_size > MAX_REQUEST_BYTES:
            raise Rejected("too_large")
        raw = os.read(fd, MAX_REQUEST_BYTES + 1)
    finally:
        os.close(fd)
    if len(raw) > MAX_REQUEST_BYTES:
        raise Rejected("too_large")
    try:
        req = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise Rejected("bad_json") from None
    if not isinstance(req, dict):
        raise Rejected("bad_json")
    return req


def host_blocked(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in BLOCKED)


def validate(req: dict, stem: str, resolve: Resolver = socket.getaddrinfo) -> tuple[str, str, str, str]:
    """→ (url, host, text, click), or Rejected with a fixed code."""
    if req.get("id") != stem:
        raise Rejected("id_mismatch")
    url, text, click = req.get("url"), req.get("text"), req.get("click", "")
    if not isinstance(url, str) or not isinstance(text, str) or not isinstance(click, str):
        raise Rejected("bad_fields")
    if len(url) > 2000 or not url.startswith("https://") or CONTROL_RE.search(url):
        raise Rejected("bad_url")
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        raise Rejected("bad_url") from None
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or parts.username or parts.password or port not in (None, 443):
        raise Rejected("bad_url")
    try:
        ipaddress.ip_address(host)
        raise Rejected("ip_literal")
    except ValueError:
        pass
    if host_blocked(host):
        raise Rejected("blocked_host")
    if not 1 <= len(text) <= MAX_TEXT or CONTROL_RE.search(text) or len(click) > MAX_CLICK or CONTROL_RE.search(click):
        raise Rejected("bad_text")
    try:
        infos = resolve(host, 443, proto=socket.IPPROTO_TCP)
    except OSError:
        raise Rejected("unresolved") from None
    addrs = {info[4][0] for info in infos}
    if not addrs:
        raise Rejected("unresolved")
    for a in addrs:
        if not ipaddress.ip_address(a.split("%")[0]).is_global:
            raise Rejected("non_global_address")
    return url, host, text, click


def docker_command(work_dir: Path, url: str, text: str, click: str) -> list[str]:
    return ["docker", "run", "--rm", "--shm-size=1g", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--pids-limit", "512", "--memory", "1g", "--cpus", "1", "-v", f"{work_dir}:/out",
            IMAGE, "python3", "/out/cap.py", url, text, click]


def ensure_image(run: Runner) -> None:
    if run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True).returncode == 0:
        return
    with tempfile.TemporaryDirectory(prefix="capture-door-image-") as d:
        Path(d, "Dockerfile").write_text(f"FROM {BASE}\nRUN pip install --no-cache-dir playwright==1.47.0\n", encoding="utf-8")
        run(["docker", "build", "-q", "-t", IMAGE, d], capture_output=True, text=True, timeout=900, check=True)


def _write_private(dirpath: Path, name: str, data: bytes, gid: int) -> None:
    """Write through a fresh temporary name (O_EXCL, no-follow) and rename into place, 0640 root:<gid>."""
    tmp = dirpath / f".{name}.{os.getpid()}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, data)
        os.fchown(fd, os.geteuid(), gid)
        os.fchmod(fd, 0o640)
    finally:
        os.close(fd)
    os.replace(tmp, dirpath / name)


def _read_bounded(path: Path, limit: int) -> bytes | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        data = os.read(fd, limit + 1)
    finally:
        os.close(fd)
    return None if len(data) > limit else data


def _clean_result(raw: bytes | None) -> dict:
    """Only the fields the client uses, with fixed types and lengths. The page is untrusted too."""
    try:
        res = json.loads(raw.decode("utf-8")) if raw else None
    except (UnicodeDecodeError, ValueError):
        res = None
    if not isinstance(res, dict):
        return {"ok": False, "reason": "브라우저 결과 없음"}
    out: dict = {"ok": res.get("ok") is True}
    for key, limit in (("reason", 200), ("final_url", 2000), ("title", 300)):
        if isinstance(res.get(key), str):
            out[key] = res[key][:limit]
    if isinstance(res.get("status"), int):
        out["status"] = res["status"]
    return out


def captures_last_hour(paths: Paths, now: float) -> int:
    n = 0
    for e in os.scandir(paths.out):
        if e.name.endswith(".json") and not e.name.startswith(".") and now - e.stat(follow_symlinks=False).st_mtime < 3600:
            n += 1
    return n


def purge(paths: Paths, now: float) -> None:
    for e in os.scandir(paths.out):
        if now - e.stat(follow_symlinks=False).st_mtime > OUT_TTL_SECONDS:
            os.unlink(e.path)


def log_line(paths: Paths, rid: str, what: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    fd = os.open(paths.log, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    try:
        os.write(fd, f"{stamp} {rid} {what}\n".encode("utf-8"))
    finally:
        os.close(fd)


def process_one(paths: Paths, name: str, owner_uid: int, owner_gid: int, run: Runner, resolve: Resolver, now: float) -> str:
    """One request file → one result. Returns what was logged. The request is always removed."""
    stem = name[:-5] if name.endswith(".json") else ""
    req_path = paths.req / name
    try:
        if not ID_RE.match(stem):
            log_line(paths, "-", "rejected:bad_name")
            return "rejected:bad_name"
        try:
            req = read_request(req_path, owner_uid)
            url, host, text, click = validate(req, stem, resolve)
            if captures_last_hour(paths, now) >= MAX_PER_HOUR:
                raise Rejected("rate_limited")
        except Rejected as r:
            _write_private(paths.out, f"{stem}.json", json.dumps({"ok": False, "reason": f"문지기 거절: {r.code}"}, ensure_ascii=False).encode(), owner_gid)
            log_line(paths, stem, f"rejected:{r.code}")
            return f"rejected:{r.code}"
        work = paths.work / stem
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(mode=0o700)
        try:
            (work / "cap.py").write_text(INNER, encoding="utf-8")
            try:
                ensure_image(run)
                run(docker_command(work, url, text, click), capture_output=True, text=True, timeout=RUN_TIMEOUT)
            except subprocess.TimeoutExpired:
                pass
            except (OSError, subprocess.CalledProcessError):
                pass
            res = _clean_result(_read_bounded(work / "result.json", MAX_RESULT_BYTES))
            if res["ok"]:
                if host_blocked((urllib.parse.urlsplit(res.get("final_url", url)).hostname or "").lower()):
                    res = {"ok": False, "reason": "막힌 도메인으로 이동함", "final_url": res.get("final_url", "")}
                else:
                    shot = _read_bounded(work / "shot.png", MAX_SHOT_BYTES)
                    if not shot or not shot.startswith(b"\x89PNG\r\n\x1a\n"):
                        res = {"ok": False, "reason": "브라우저 결과 없음(화면 파일)"}
                    else:
                        _write_private(paths.out, f"{stem}.png", shot, owner_gid)
            elif "reason" not in res:
                res["reason"] = "알 수 없음"
            _write_private(paths.out, f"{stem}.json", json.dumps(res, ensure_ascii=False).encode("utf-8"), owner_gid)
            what = f"{host} " + ("ok" if res["ok"] else "fail")
            log_line(paths, stem, what)
            return what
        finally:
            shutil.rmtree(work, ignore_errors=True)
    finally:
        try:
            os.unlink(req_path)
        except FileNotFoundError:
            pass


def run_pass(paths: Paths, owner: str, run: Runner = subprocess.run, resolve: Resolver = socket.getaddrinfo,
             now: Callable[[], float] = time.time, root_uid: int = 0) -> list[str]:
    pw = pwd.getpwnam(owner)
    check_layout(paths, pw.pw_gid, root_uid)
    with open(os.open(paths.lock, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        purge(paths, now())
        names = []
        for e in os.scandir(paths.req):
            if not e.name.startswith("."):
                names.append(e.name)
            elif now() - e.stat(follow_symlinks=False).st_mtime > STALE_TEMP_SECONDS:
                os.unlink(e.path)   # a temporary name the client never renamed into place
        names = sorted(names)[:MAX_PER_PASS]
        return [process_one(paths, n, pw.pw_uid, pw.pw_gid, run, resolve, now()) for n in names]


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Capture door: one pass over the capture requests (root).")
    ap.add_argument("--root", default="/var/lib/thomas-capture")
    ap.add_argument("--owner", default="claude")
    a = ap.parse_args(argv)
    try:
        for line in run_pass(Paths(Path(a.root)), a.owner):
            print(line)
    except LayoutError as e:
        print(f"capture-door: refused — {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
