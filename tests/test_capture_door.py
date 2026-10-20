"""The capture door (``scripts/ops/capture_door.py``): synthetic only — no Docker, no root, no network.

What these pin:
- the door refuses a layout that is not the installed one;
- which requests it rejects (link, wrong owner, oversize, bad id, IP literal, blocked host, private address);
- that no rejection carries the request's contents back out;
- that a good request runs the hardened container command and hands back only the screenshot and the result;
- that every request file is removed, whatever happened to it.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the door runs on the Linux host (O_NOFOLLOW, fcntl, pwd)")

if os.name != "nt":
    from scripts.ops import capture_door as cd

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
GOOD_ID = "0123456789abcdef0123456789abcdef"
SECRET = "SECRET-CONTENT-THAT-MUST-NOT-LEAK"


def _layout(tmp_path: Path) -> "cd.Paths":
    p = cd.Paths(tmp_path / "door")
    p.root.mkdir()
    p.req.mkdir()
    p.out.mkdir()
    p.work.mkdir()
    os.chmod(p.req, stat.S_ISVTX | 0o730)
    os.chmod(p.out, 0o750)
    os.chmod(p.work, 0o700)
    return p


def _public(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("142.250.76.142", port))]


def _private(host, port, proto=None):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


def _docker(ok=True, final_url="https://support.google.com/gemini/answer/1", calls=None):
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        if argv[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[:2] == ["docker", "run"]:
            work = Path(argv[argv.index("-v") + 1].split(":")[0])
            res = {"ok": ok, "status": 200, "final_url": final_url, "title": "Gemini 도움말"}
            if ok:
                (work / "shot.png").write_bytes(PNG)
            else:
                res["reason"] = "문구 없음"
            (work / "result.json").write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(argv)
    return run


def _request(p: "cd.Paths", rid=GOOD_ID, **over) -> Path:
    body = {"id": rid, "url": "https://support.google.com/gemini/answer/1", "text": "Gemini 앱 한도", "click": ""}
    body.update(over)
    f = p.req / f"{rid}.json"
    f.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return f


def _pass(p, monkeypatch, run=None, resolve=_public):
    monkeypatch.setattr(cd.pwd, "getpwnam", lambda name: type("P", (), {"pw_uid": os.getuid(), "pw_gid": os.getgid()})())
    return cd.run_pass(p, "claude", run=run or _docker(), resolve=resolve, root_uid=os.getuid())


def _result(p, rid=GOOD_ID):
    return json.loads((p.out / f"{rid}.json").read_text(encoding="utf-8"))


def test_a_good_request_hands_back_the_screenshot_and_a_result(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    _request(p)
    calls = []
    assert _pass(p, monkeypatch, run=_docker(calls=calls)) == ["support.google.com ok"]
    res = _result(p)
    assert res["ok"] is True and res["status"] == 200 and res["final_url"].startswith("https://support.google.com/")
    assert (p.out / f"{GOOD_ID}.png").read_bytes() == PNG
    assert stat.S_IMODE(os.stat(p.out / f"{GOOD_ID}.png").st_mode) == 0o640
    assert not list(p.req.iterdir()) and not (p.work / GOOD_ID).exists()
    run = next(c for c in calls if c[:2] == ["docker", "run"])
    for flag in ("--cap-drop", "ALL", "no-new-privileges", "--pids-limit", "--memory", "--shm-size=1g"):
        assert flag in run
    assert "--ipc=host" not in run


@pytest.mark.parametrize("over,code", [
    ({"url": "http://support.google.com/x"}, "bad_url"),
    ({"url": "https://142.250.76.142/x"}, "ip_literal"),
    ({"url": "https://[::1]/x"}, "ip_literal"),
    ({"url": "https://blog.naver.com/x"}, "blocked_host"),
    ({"url": "https://www.coupang.com/x"}, "blocked_host"),
    ({"url": "https://user:pw@support.google.com/x"}, "bad_url"),
    ({"url": "https://support.google.com:8443/x"}, "bad_url"),
    ({"text": ""}, "bad_text"),
    ({"text": "x" * 201}, "bad_text"),
    ({"text": "줄\n바꿈"}, "bad_text"),
    ({"click": "x" * 61}, "bad_text"),
    ({"id": "f" * 32}, "id_mismatch"),
    ({"url": 5}, "bad_fields"),
])
def test_rejections_are_fixed_codes(tmp_path, monkeypatch, over, code):
    p = _layout(tmp_path)
    _request(p, **over)
    calls = []
    assert _pass(p, monkeypatch, run=_docker(calls=calls)) == [f"rejected:{code}"]
    assert _result(p) == {"ok": False, "reason": f"문지기 거절: {code}"}
    assert not any(c[:2] == ["docker", "run"] for c in calls)
    assert not list(p.req.iterdir())


def test_a_name_resolving_to_a_private_address_is_refused(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    _request(p)
    assert _pass(p, monkeypatch, resolve=_private) == ["rejected:non_global_address"]


def test_a_symlinked_request_is_refused_and_its_target_never_echoed(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    target = tmp_path / "secret.json"
    target.write_text(json.dumps({"id": GOOD_ID, "url": "https://support.google.com/x", "text": SECRET}), encoding="utf-8")
    (p.req / f"{GOOD_ID}.json").symlink_to(target)
    assert _pass(p, monkeypatch) == ["rejected:unreadable"]
    assert SECRET not in (p.out / f"{GOOD_ID}.json").read_text(encoding="utf-8")
    assert SECRET not in p.log.read_text(encoding="utf-8")
    assert target.exists() and not list(p.req.iterdir())


def test_no_rejection_echoes_the_request_text(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    _request(p, url="https://blog.naver.com/" + SECRET, text=SECRET)
    _pass(p, monkeypatch)
    assert SECRET not in (p.out / f"{GOOD_ID}.json").read_text(encoding="utf-8")
    assert SECRET not in p.log.read_text(encoding="utf-8")


def test_wrong_owner_oversize_and_bad_json(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    _request(p)
    monkeypatch.setattr(cd.pwd, "getpwnam", lambda name: type("P", (), {"pw_uid": os.getuid() + 1, "pw_gid": os.getgid()})())
    assert cd.run_pass(p, "claude", run=_docker(), resolve=_public, root_uid=os.getuid()) == ["rejected:wrong_owner"]
    (p.req / f"{GOOD_ID}.json").write_text("{" + " " * 5000 + "}", encoding="utf-8")
    assert _pass(p, monkeypatch) == ["rejected:too_large"]
    (p.req / f"{GOOD_ID}.json").write_text("not json", encoding="utf-8")
    assert _pass(p, monkeypatch) == ["rejected:bad_json"]


def test_a_badly_named_request_is_removed_without_a_result(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    (p.req / "escape.json").write_text("{}", encoding="utf-8")
    (p.req / "short.json").write_text("{}", encoding="utf-8")
    out = _pass(p, monkeypatch)
    assert out == ["rejected:bad_name", "rejected:bad_name"]
    assert not list(p.req.iterdir()) and not list(p.out.iterdir())


def test_a_failed_capture_is_a_result_and_a_redirect_to_a_blocked_host_is_refused(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    _request(p)
    assert _pass(p, monkeypatch, run=_docker(ok=False)) == ["support.google.com fail"]
    assert _result(p)["reason"] == "문구 없음" and not (p.out / f"{GOOD_ID}.png").exists()
    _request(p)
    _pass(p, monkeypatch, run=_docker(final_url="https://m.blog.naver.com/x"))
    assert _result(p)["ok"] is False and not (p.out / f"{GOOD_ID}.png").exists()


def test_a_temporary_name_is_skipped_until_stale(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    tmp = p.req / f".{GOOD_ID}.tmp"
    tmp.write_text("{", encoding="utf-8")
    assert _pass(p, monkeypatch) == [] and tmp.exists()
    old = tmp.stat().st_mtime - cd.STALE_TEMP_SECONDS - 5
    os.utime(tmp, (old, old))
    assert _pass(p, monkeypatch) == [] and not tmp.exists()


def test_rate_limit(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    for i in range(cd.MAX_PER_HOUR):
        (p.out / f"{i:032x}.json").write_text("{}", encoding="utf-8")
    _request(p)
    assert _pass(p, monkeypatch) == ["rejected:rate_limited"]


def test_the_door_refuses_a_layout_that_is_not_the_installed_one(tmp_path, monkeypatch):
    p = _layout(tmp_path)
    os.chmod(p.req, 0o777)
    _request(p)
    with pytest.raises(cd.LayoutError):
        _pass(p, monkeypatch)
    assert list(p.req.iterdir())   # nothing processed


def test_main_exits_2_on_a_bad_layout(tmp_path, capsys):
    assert cd.main(["--root", str(tmp_path / "missing"), "--owner", "root"]) == 2
    assert "refused" in capsys.readouterr().err


def test_the_units_keep_the_door_out_of_home_and_off_tmp():
    units = Path(__file__).resolve().parents[1] / "scripts" / "ops" / "capture_door_units"
    svc = (units / "thomas-capture-door.service").read_text(encoding="utf-8")
    assert "ProtectHome=yes" in svc and "ProtectSystem=strict" in svc
    assert not any(line.strip().startswith("PrivateTmp=") for line in svc.splitlines())
    assert "ExecStart=/usr/bin/python3 -I /usr/local/sbin/thomas-capture-door" in svc
    path = (units / "thomas-capture-door.path").read_text(encoding="utf-8")
    assert "DirectoryNotEmpty=/var/lib/thomas-capture/req" in path
