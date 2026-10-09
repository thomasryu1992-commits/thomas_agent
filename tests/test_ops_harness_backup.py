"""The harness backup's core archive is encrypted to an age public key (Thomas decision, 2026-09-29).

Every case runs the real script against a scratch host root, with stub `docker` and `age` on PATH,
so nothing here reads the host's state, its .env or its backups. The stub age writes the age
version line and then its stdin, which lets a test see what tar streamed into it without a real
key; the one case that uses the real binary skips where it is not installed.
"""
from __future__ import annotations

import gzip
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ops" / "harness_backup.sh"
RECIPIENT = "age1" + "q" * 58
HEADER = b"age-encryption.org/v1\n"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="harness_backup.sh is a bash script")

STUB_AGE = """#!/bin/bash
# -R <file> -o <out>: behave per $STUB_AGE_MODE.  -d -i <key> <file>: strip the version line (restore tests)
out=""; dec=0; in=""
while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; -d) dec=1; shift;; -i|-R) shift 2;; *) in="$1"; shift;; esac; done
if [ "$dec" = 1 ]; then tail -c +23 "$in"; exit 0; fi
case "${STUB_AGE_MODE:-ok}" in
  ok)        { printf 'age-encryption.org/v1\\n'; cat; } > "$out" ;;
  fail)      cat > /dev/null; printf 'partial' > "$out"; exit 1 ;;
  truncated) { printf 'age-encryption.org/v1\n'; head -c 100; } > "$out"; cat > /dev/null; exit 1 ;;
  plaintext) cat > "$out" ;;
esac
"""


def _host(tmp_path: Path) -> Path:
    host = tmp_path / "host"
    for d in ("thomas_agent/.runtime_governance_state", "thomas_agent/THOMAS_CORE/activations",
              "thomas_agent/THOMAS_CORE/approvals", "thomas_agent/workspace", "hermes-trial/data"):
        (host / d).mkdir(parents=True)
    (host / "thomas_agent/.runtime_governance_state/records.jsonl").write_text("{}\n", encoding="utf-8")
    (host / "thomas_agent/.env").write_text("TOKEN=not-a-real-secret\n", encoding="utf-8")
    return host


def _bin(tmp_path: Path) -> Path:
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    (stub / "docker").write_text(f'#!/bin/bash\necho "$*" >> {tmp_path}/docker.calls\nexit 0\n', encoding="utf-8")
    (stub / "age").write_text(STUB_AGE, encoding="utf-8")
    for f in stub.iterdir():
        f.chmod(0o755)
    return stub


RESTORE = REPO_ROOT / "scripts" / "ops" / "restore_transcripts.sh"


def _run(tmp_path: Path, *, recipients: str | None = RECIPIENT + "\n", age_mode: str = "ok",
         age_bin: str | None = None, host: Path | None = None, script: Path = SCRIPT):
    host = host or _host(tmp_path)
    dest = tmp_path / "dest"
    rfile = tmp_path / "age-recipients.txt"
    if recipients is not None:
        rfile.write_text(recipients, encoding="utf-8")
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}",
           "HARNESS_BACKUP_HOST_ROOT": str(host), "HARNESS_BACKUP_DEST": str(dest),
           "HARNESS_BACKUP_AGE_RECIPIENTS": str(rfile), "STUB_AGE_MODE": age_mode}
    if age_bin is not None:
        env["AGE_BIN"] = age_bin
    out = subprocess.run(["bash", str(script), "core"], capture_output=True, text=True, timeout=60, env=env)
    log = (dest / "backup.log").read_text(encoding="utf-8") if (dest / "backup.log").exists() else ""
    return out, dest, log


def _archives(dest: Path) -> list[str]:
    return sorted(p.name for p in dest.glob("govstate-*")) if dest.exists() else []


@pytest.mark.parametrize("recipients, reason", [
    (None, "reason=no-recipients-file"),
    ("# only a comment\n\n", "reason=no-recipient"),
    ("age1short\n", "reason=malformed-recipient"),
    (RECIPIENT + "\nAGE-SECRET-KEY-1" + "Q" * 58 + "\n", "reason=private-key-in-recipients-file"),
])
def test_a_bad_recipients_file_refuses_before_any_snapshot_or_archive(tmp_path, recipients, reason):
    out, dest, log = _run(tmp_path, recipients=recipients)
    assert out.returncode == 3
    assert f"FAILED mode=core stage=encrypt {reason}" in log
    assert _archives(dest) == []
    assert not (tmp_path / "docker.calls").exists()          # refused before the snapshots too


def test_no_age_binary_refuses(tmp_path):
    out, dest, log = _run(tmp_path, age_bin=str(tmp_path / "no-such-age"))
    assert out.returncode == 3 and "stage=encrypt reason=no-age-binary" in log
    assert _archives(dest) == []


@pytest.mark.parametrize("mode", ["fail", "truncated", "plaintext"])
def test_a_failed_or_non_age_encryption_leaves_no_file_and_no_ok(tmp_path, mode):
    # `truncated`: age wrote its header and then failed — ciphertext-shaped, but incomplete.
    # `plaintext`: age exits 0 but what it wrote is not an age file — it must not be kept as .age.
    out, dest, log = _run(tmp_path, age_mode=mode)
    assert out.returncode == 3
    assert "FAILED mode=core stage=encrypt" in log and " OK " not in log
    assert _archives(dest) == []                              # no .age, no .part, no plaintext .tar.gz


def test_a_missing_member_is_an_archive_failure(tmp_path):
    host = _host(tmp_path)
    (host / "thomas_agent/.env").unlink()
    out, dest, log = _run(tmp_path, host=host)
    assert out.returncode == 2
    assert "FAILED mode=core stage=archive rc=2 missing=thomas_agent/.env" in log
    assert _archives(dest) == []


ANCHOR = "thomas_agent/.runtime_governance_state/crypto/execution_stage_anchor.json"


def _host_with_anchor(tmp_path: Path) -> Path:
    host = _host(tmp_path)
    (host / ANCHOR).parent.mkdir(parents=True)
    (host / ANCHOR).write_text("{}", encoding="utf-8")
    (host / ANCHOR).with_name("execution_stage_ledger.jsonl").write_text("{}\n", encoding="utf-8")
    return host


def test_the_anchor_stays_out_of_the_archive_and_the_log_says_it_was_checked(tmp_path):
    """EXECUTION_STAGE_ANTI_ROLLBACK D1 a. The archive is encrypted and cannot be listed on the host,
    so the script's own check of tar's member list is the only evidence; the OK line carries it."""
    out, dest, log = _run(tmp_path, host=_host_with_anchor(tmp_path))
    assert out.returncode == 0, out.stderr
    assert " anchor=excluded " in log
    (archive,) = dest.glob("govstate-*.tar.gz.age")
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.read_bytes()[len(HEADER):]))) as tar:
        members = tar.getnames()
    assert ANCHOR not in members and ANCHOR.replace("anchor.json", "ledger.jsonl") in members
    assert not list(dest.glob("*.index"))                     # the member list does not outlive the run


def test_an_archive_that_would_carry_the_anchor_is_refused_and_removed(tmp_path):
    # The --exclude lost in an edit: the member-list check is what still keeps the anchor out.
    lost = tmp_path / "harness_backup.sh"
    lost.write_text("".join(line for line in SCRIPT.read_text(encoding="utf-8").splitlines(keepends=True)
                            if "execution_stage_anchor.json\"" not in line), encoding="utf-8")
    assert lost.read_text(encoding="utf-8") != SCRIPT.read_text(encoding="utf-8")
    out, dest, log = _run(tmp_path, host=_host_with_anchor(tmp_path), script=lost)
    assert out.returncode == 2
    assert "FAILED mode=core stage=archive reason=anchor-in-archive" in log and " OK " not in log
    assert sorted(p.name for p in dest.iterdir()) == ["backup.log"]   # no .age, no .part, no .index


def test_success_writes_only_the_encrypted_archive_and_names_the_key(tmp_path):
    out, dest, log = _run(tmp_path)
    assert out.returncode == 0, out.stderr
    names = _archives(dest)
    assert len(names) == 1 and names[0].endswith(".tar.gz.age"), names
    archive = dest / names[0]
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    assert f"OK mode=core {names[0]}" in log and f"enc=age recipient={RECIPIENT[:12]}" in log
    # What reached age was the whole archive, .env included — streamed, never written as plaintext.
    body = archive.read_bytes()
    assert body.startswith(HEADER)
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(body[len(HEADER):]))) as tar:
        members = tar.getnames()
    assert "thomas_agent/.env" in members and "hermes-trial/data" in members


def test_legacy_plaintext_archives_go_only_once_seven_encrypted_ones_exist(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    legacy = [dest / f"govstate-2026092{d}-0745.tar.gz" for d in (1, 2)]
    for f in legacy:
        f.write_bytes(b"plaintext")
    for d in range(5):                                        # 5 + today's = 6 < 7: keep the legacy
        (dest / f"govstate-2026093{d}-0745.tar.gz.age").write_bytes(HEADER)
    host = _host(tmp_path)
    out, _, log = _run(tmp_path, host=host)
    assert out.returncode == 0 and all(f.exists() for f in legacy)
    assert "legacy-plaintext-removed" not in log
    (dest / "govstate-20260939-0745.tar.gz.age").write_bytes(HEADER)   # 7 after the next run
    out, _, log = _run(tmp_path, host=host)
    assert out.returncode == 0 and not any(f.exists() for f in legacy)
    assert "legacy-plaintext-removed=2" in log.splitlines()[-1]


def test_the_candle_archive_is_unchanged_and_not_encrypted(tmp_path):
    host = _host(tmp_path)
    (host / "thomas_agent/.runtime_governance_state/crypto/candle_archive").mkdir(parents=True)
    dest = tmp_path / "dest"
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}",
           "HARNESS_BACKUP_HOST_ROOT": str(host), "HARNESS_BACKUP_DEST": str(dest),
           "HARNESS_BACKUP_AGE_RECIPIENTS": str(tmp_path / "absent")}
    out = subprocess.run(["bash", str(SCRIPT), "candles"], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    assert [p.name.endswith(".tar.gz") for p in dest.glob("govstate-candles-*")] == [True]


@pytest.mark.skipif(not (shutil.which("age") and shutil.which("age-keygen")), reason="age is not installed")
def test_a_real_age_round_trip_decrypts_with_the_private_key_only(tmp_path):
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    public = subprocess.run(["age-keygen", "-y", str(key)], check=True, capture_output=True, text=True).stdout
    host = _host(tmp_path)
    dest = tmp_path / "dest"
    (tmp_path / "age-recipients.txt").write_text(public, encoding="utf-8")
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}", "AGE_BIN": shutil.which("age"),
           "HARNESS_BACKUP_HOST_ROOT": str(host), "HARNESS_BACKUP_DEST": str(dest),
           "HARNESS_BACKUP_AGE_RECIPIENTS": str(tmp_path / "age-recipients.txt")}
    out = subprocess.run(["bash", str(SCRIPT), "core"], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    archive = next(dest.glob("govstate-*.tar.gz.age"))
    assert b".env" not in archive.read_bytes()                # ciphertext, not a tar
    plain = subprocess.run(["age", "-d", "-i", str(key), str(archive)], check=True, capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(plain))) as tar:
        assert "thomas_agent/.env" in tar.getnames()


# The transcripts mode (Thomas 2026-10-09): Claude Code conversations, encrypted like core.

def _transcripts_host(tmp_path: Path, *, collector: bool = True) -> Path:
    host = _host(tmp_path)
    proj = host / ".claude/projects/-root-thomas-agent"
    proj.mkdir(parents=True)
    (proj / "old-session.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    (proj / "new-session.jsonl").write_text('{"type":"user"}\n', encoding="utf-8")
    if collector:
        (host / ".claude/prompt-collector").mkdir(parents=True)
        (host / ".claude/prompt-collector/state.json").write_text("{}", encoding="utf-8")
    return host


def _run_transcripts(tmp_path: Path, host: Path, *, recipients: str = RECIPIENT + "\n"):
    dest = tmp_path / "dest"
    rfile = tmp_path / "age-recipients.txt"
    rfile.write_text(recipients, encoding="utf-8")
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}",
           "HARNESS_BACKUP_HOST_ROOT": str(host), "HARNESS_BACKUP_DEST": str(dest),
           "HARNESS_BACKUP_AGE_RECIPIENTS": str(rfile), "STUB_AGE_MODE": "ok"}
    out = subprocess.run(["bash", str(SCRIPT), "transcripts"], capture_output=True, text=True, timeout=60, env=env)
    log = (dest / "backup.log").read_text(encoding="utf-8") if (dest / "backup.log").exists() else ""
    return out, dest, log


def _members(archive: Path) -> list[str]:
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.read_bytes()[len(HEADER):]))) as tar:
        return [m.name for m in tar.getmembers() if m.isfile()]


def test_the_first_transcripts_run_is_a_full_encrypted_archive_of_both_roots(tmp_path):
    out, dest, log = _run_transcripts(tmp_path, _transcripts_host(tmp_path))
    assert out.returncode == 0, out.stderr
    (archive,) = dest.glob("govstate-transcripts-full-*.tar.gz.age")
    assert oct(archive.stat().st_mode & 0o777) == "0o600"
    assert archive.read_bytes().startswith(HEADER)
    assert sorted(_members(archive)) == [".claude/projects/-root-thomas-agent/new-session.jsonl",
                                         ".claude/projects/-root-thomas-agent/old-session.jsonl",
                                         ".claude/prompt-collector/state.json",
                                         "TRANSCRIPTS_MANIFEST.txt"]
    line = log.splitlines()[-1]
    assert " OK mode=transcripts kind=full " in line and " files=3 " in line
    assert f"enc=age recipient={RECIPIENT[:12]}" in line and "collector-state=included" in line
    assert not list(dest.glob("*.part*")) and not list(dest.glob("*.index"))


def test_a_later_run_carries_only_what_changed_since_the_last_archive(tmp_path):
    host = _transcripts_host(tmp_path, collector=False)
    out, dest, _ = _run_transcripts(tmp_path, host)
    assert out.returncode == 0, out.stderr
    full = next(dest.glob("govstate-transcripts-full-*"))
    past = full.stat().st_mtime - 3 * 3600                  # the full was written three hours ago
    os.utime(full, (past, past))
    old = host / ".claude/projects/-root-thomas-agent/old-session.jsonl"
    os.utime(old, (past - 7200, past - 7200))               # untouched since before that
    os.utime(host / ".claude/projects/-root-thomas-agent/new-session.jsonl", (past - 7200, past - 7200))
    (host / ".claude/projects/-root-thomas-agent/new-session.jsonl").write_text('{"type":"user"}\n{}\n', encoding="utf-8")
    out, dest, log = _run_transcripts(tmp_path, host)
    assert out.returncode == 0, out.stderr
    assert "not dumped" not in out.stderr                      # one line per unchanged file would flood cron
    (inc,) = dest.glob("govstate-transcripts-inc-*.tar.gz.age")
    assert _members(inc) == [".claude/projects/-root-thomas-agent/new-session.jsonl", "TRANSCRIPTS_MANIFEST.txt"]
    assert " OK mode=transcripts kind=inc " in log.splitlines()[-1] and "collector-state=absent" in log


def test_a_week_old_full_makes_the_next_run_full_and_retention_keeps_two(tmp_path):
    host = _transcripts_host(tmp_path)
    dest = tmp_path / "dest"
    dest.mkdir()
    for i, name in enumerate(("full-20260901-0755", "inc-20260902-0755", "full-20260908-0755", "inc-20260909-0755")):
        f = dest / f"govstate-transcripts-{name}.tar.gz.age"
        f.write_bytes(HEADER)
        t = 1_000_000_000 + i * 86400
        os.utime(f, (t, t))
    out, dest, log = _run_transcripts(tmp_path, host)
    assert out.returncode == 0, out.stderr
    assert " kind=full " in log.splitlines()[-1]
    names = sorted(p.name for p in dest.glob("govstate-transcripts-*"))
    # three fulls exist → the oldest goes, and so does the inc that sat on it
    assert "govstate-transcripts-full-20260901-0755.tar.gz.age" not in names
    assert "govstate-transcripts-inc-20260902-0755.tar.gz.age" not in names
    assert "govstate-transcripts-full-20260908-0755.tar.gz.age" in names
    assert "govstate-transcripts-inc-20260909-0755.tar.gz.age" in names
    assert len([n for n in names if "-full-" in n]) == 2


def test_transcripts_refuse_without_a_valid_recipient_and_without_the_projects_root(tmp_path):
    out, dest, log = _run_transcripts(tmp_path, _transcripts_host(tmp_path), recipients="age1short\n")
    assert out.returncode == 3 and "FAILED mode=transcripts stage=encrypt reason=malformed-recipient" in log
    assert not list(dest.glob("govstate-transcripts-*"))
    bare = tmp_path / "bare"
    bare.mkdir()
    out, dest, log = _run_transcripts(tmp_path, _host(bare))
    assert out.returncode == 2 and "FAILED mode=transcripts stage=archive rc=2 missing=.claude/projects" in log


def test_core_does_not_carry_the_transcripts(tmp_path):
    out, dest, _ = _run(tmp_path, host=_transcripts_host(tmp_path))
    assert out.returncode == 0, out.stderr
    (archive,) = dest.glob("govstate-2*.tar.gz.age")
    assert not any(m.startswith(".claude") for m in _members(archive))



# Restore order and deletions (2026-10-09 review): newest full at or before the point, then only the
# incs after it; files deleted since the full stay deleted; the final manifest is the integrity check.

def _backup(tmp_path: Path, host: Path, stamp: str, *, full: bool = False) -> str:
    dest = tmp_path / "dest"
    (tmp_path / "age-recipients.txt").write_text(RECIPIENT + "\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}",
           "HARNESS_BACKUP_HOST_ROOT": str(host), "HARNESS_BACKUP_DEST": str(dest),
           "HARNESS_BACKUP_AGE_RECIPIENTS": str(tmp_path / "age-recipients.txt"), "STUB_AGE_MODE": "ok",
           "HARNESS_BACKUP_STAMP": stamp, "HARNESS_TRANSCRIPTS_FULL_DAYS": "0" if full else "7"}
    out = subprocess.run(["bash", str(SCRIPT), "transcripts"], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    return (dest / "backup.log").read_text(encoding="utf-8").splitlines()[-1]


def _restore(tmp_path: Path, target: Path, *extra: str):
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(RESTORE), str(tmp_path / "dest"), str(target), "--identity", "unused", *extra],
                          capture_output=True, text=True, timeout=60, env=env)


def _session(host: Path, name: str) -> Path:
    return host / ".claude/projects/-root-thomas-agent" / name


def test_every_transcripts_archive_carries_a_manifest_with_its_t0(tmp_path):
    line = _backup(tmp_path, _transcripts_host(tmp_path), "20261009-0100", full=True)
    assert " files=3 " in line                                   # the manifest is not counted as a file
    (archive,) = (tmp_path / "dest").glob("govstate-transcripts-full-*.age")
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.read_bytes()[len(HEADER):]))) as tar:
        body = tar.extractfile("TRANSCRIPTS_MANIFEST.txt").read().decode()
    lines = body.splitlines()
    assert lines[0].startswith("# t0=") and len(lines[0]) == len("# t0=202610090100.00")
    assert lines[1:6] == ["# set=transcripts", "# kind=full", "# stamp=20261009-0100", "# chain=20261009-0100", "# prev=none"]
    row = next(l for l in lines if l.startswith(".claude/projects/-root-thomas-agent/old-session.jsonl\t"))
    path, size, mtime = row.split("\t")
    assert int(size) == len('{"type":"user"}\n') and int(mtime) > 1_700_000_000
    assert not list((tmp_path / "dest").glob(".manifest-*"))     # the staging directory is gone


def test_restore_applies_the_inc_and_keeps_deletions_deleted(tmp_path):
    host = _transcripts_host(tmp_path)
    _backup(tmp_path, host, "20261001-0755", full=True)
    _session(host, "old-session.jsonl").write_text('{"v":2}\n', encoding="utf-8")       # changed
    _session(host, "new-session.jsonl").unlink()                                          # deleted
    _session(host, "third-session.jsonl").write_text('{"v":1}\n', encoding="utf-8")     # added
    _backup(tmp_path, host, "20261002-0755")
    target = tmp_path / "restored"
    out = _restore(tmp_path, target)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _session(target, "old-session.jsonl").read_text() == '{"v":2}\n'
    assert not _session(target, "new-session.jsonl").exists()
    assert _session(target, "third-session.jsonl").exists()
    assert "deleted=1 missing=0" in out.stdout and "archives=2" in out.stdout


def test_restore_never_applies_an_inc_older_than_the_chosen_full(tmp_path):
    host = _transcripts_host(tmp_path)
    s = _session(host, "old-session.jsonl")
    s.write_text("v1\n", encoding="utf-8")
    _backup(tmp_path, host, "20261001-0755", full=True)
    s.write_text("v2\n", encoding="utf-8")
    _backup(tmp_path, host, "20261002-0755")                       # inc: v2
    s.write_text("v3\n", encoding="utf-8")
    _backup(tmp_path, host, "20261008-0755", full=True)            # newer full: v3
    _session(host, "late.jsonl").write_text("x\n", encoding="utf-8")
    _backup(tmp_path, host, "20261009-0755")                       # inc after it
    target = tmp_path / "latest"
    out = _restore(tmp_path, target)
    assert out.returncode == 0, out.stdout + out.stderr
    assert _session(target, "old-session.jsonl").read_text() == "v3\n"   # not v2 from the older inc
    assert _session(target, "late.jsonl").exists()
    assert "full=20261008-0755 archives=2 skipped-older-incs=1" in out.stdout
    earlier = tmp_path / "earlier"
    out = _restore(tmp_path, earlier, "--until", "20261002-0755")
    assert out.returncode == 0, out.stdout + out.stderr
    assert _session(earlier, "old-session.jsonl").read_text() == "v2\n"
    assert not _session(earlier, "late.jsonl").exists()
    assert "full=20261001-0755 archives=2" in out.stdout


def test_restore_reports_a_missing_full_and_files_the_archives_did_not_carry(tmp_path):
    host = _transcripts_host(tmp_path)
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 2
    _backup(tmp_path, host, "20261001-0755", full=True)
    out = _restore(tmp_path, tmp_path / "t", "--until", "20260901-0000")
    assert out.returncode == 2 and "no full archive at or before" in out.stderr
    # an archive whose manifest lists a file it did not carry: the integrity check says so
    archive = next((tmp_path / "dest").glob("govstate-transcripts-full-*.age"))
    raw = gzip.decompress(archive.read_bytes()[len(HEADER):])
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(fileobj=buf, mode="w") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read() if m.isfile() else None
            if m.name == "TRANSCRIPTS_MANIFEST.txt":
                data += b".claude/projects/-root-thomas-agent/never-archived.jsonl\n"
                m.size = len(data)
            dst.addfile(m, io.BytesIO(data) if data is not None else None)
    archive.write_bytes(HEADER + gzip.compress(buf.getvalue()))
    out = _restore(tmp_path, tmp_path / "t2")
    assert out.returncode == 4 and "missing=1" in out.stdout


@pytest.mark.skipif(not (shutil.which("age") and shutil.which("age-keygen")), reason="age is not installed")
def test_a_real_age_transcripts_backup_restores_with_the_script(tmp_path):
    key = tmp_path / "key.txt"
    subprocess.run(["age-keygen", "-o", str(key)], check=True, capture_output=True)
    public = subprocess.run(["age-keygen", "-y", str(key)], check=True, capture_output=True, text=True).stdout
    host = _transcripts_host(tmp_path)
    dest = tmp_path / "dest"
    (tmp_path / "age-recipients.txt").write_text(public, encoding="utf-8")
    env = {**os.environ, "AGE_BIN": shutil.which("age"), "HARNESS_BACKUP_HOST_ROOT": str(host),
           "HARNESS_BACKUP_DEST": str(dest), "HARNESS_BACKUP_AGE_RECIPIENTS": str(tmp_path / "age-recipients.txt"),
           "HARNESS_BACKUP_STAMP": "20261009-0100"}
    out = subprocess.run(["bash", str(SCRIPT), "transcripts"], capture_output=True, text=True, timeout=60, env=env)
    assert out.returncode == 0, out.stderr
    target = tmp_path / "restored"
    out = subprocess.run(["bash", str(RESTORE), str(dest), str(target), "--identity", str(key)],
                         capture_output=True, text=True, timeout=60, env={**os.environ, "AGE_BIN": shutil.which("age")})
    assert out.returncode == 0, out.stdout + out.stderr
    assert "missing=0" in out.stdout
    assert _session(target, "old-session.jsonl").read_text() == _session(host, "old-session.jsonl").read_text()



# Chain and file checks (2026-10-09, second review): a gap, a missing tail, an out-of-order archive, a
# stale copy and a legacy archive are each told apart from a verified restore.

def _edit_manifest(archive: Path, edit) -> None:
    raw = gzip.decompress(archive.read_bytes()[len(HEADER):])
    buf = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(fileobj=buf, mode="w") as dst:
        for m in src.getmembers():
            data = src.extractfile(m).read() if m.isfile() else None
            if m.name == "TRANSCRIPTS_MANIFEST.txt":
                data = edit(data.decode()).encode()
                m.size = len(data)
            dst.addfile(m, io.BytesIO(data) if data is not None else None)
    archive.write_bytes(HEADER + gzip.compress(buf.getvalue()))


def _chain_of_three(tmp_path: Path) -> Path:
    host = _transcripts_host(tmp_path)
    _backup(tmp_path, host, "20261001-0755", full=True)
    _session(host, "a.jsonl").write_text("a\n", encoding="utf-8")
    _backup(tmp_path, host, "20261002-0755")
    _session(host, "b.jsonl").write_text("b\n", encoding="utf-8")
    _backup(tmp_path, host, "20261003-0755")
    _session(host, "c.jsonl").write_text("c\n", encoding="utf-8")
    _backup(tmp_path, host, "20261004-0755")
    return host


def test_each_inc_names_its_full_and_the_archive_before_it(tmp_path):
    _chain_of_three(tmp_path)
    log = (tmp_path / "dest" / "backup.log").read_text().splitlines()
    assert "chain=20261001-0755 prev=none" in log[0]
    assert "chain=20261001-0755 prev=20261001-0755" in log[1]
    assert "chain=20261001-0755 prev=20261003-0755" in log[3]


def test_a_complete_chain_with_the_logged_head_is_verified(tmp_path):
    _chain_of_three(tmp_path)
    out = _restore(tmp_path, tmp_path / "t", "--expect-head", "20261004-0755")
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.startswith("CONTENT_VERIFIED") and "tail=ok" in out.stdout and "missing=0 stale=0" in out.stdout
    assert "core-hash=ok:1,bad:0" in out.stdout                     # the collector's state.json


def test_a_missing_middle_inc_is_refused_even_though_every_file_is_present(tmp_path):
    """FULL + INC-A + INC-C without INC-B: INC-C carries everything INC-B did (one-hour overlap aside, its
    window starts at INC-B), so the files could all be there — the chain still says B is missing."""
    _chain_of_three(tmp_path)
    (tmp_path / "dest" / "govstate-transcripts-inc-20261003-0755.tar.gz.age").unlink()
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 5 and out.stdout.startswith("BROKEN")
    assert "follows 20261003-0755, which is missing" in out.stdout


def test_a_missing_last_inc_is_caught_only_against_the_logged_head(tmp_path):
    _chain_of_three(tmp_path)
    (tmp_path / "dest" / "govstate-transcripts-inc-20261004-0755.tar.gz.age").unlink()
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 0 and "tail=unchecked" in out.stdout      # the archives alone cannot tell
    out = _restore(tmp_path, tmp_path / "t2", "--expect-head", "20261004-0755")
    assert out.returncode == 5 and "tail=MISSING" in out.stdout


def test_an_archive_renamed_out_of_order_breaks_the_chain(tmp_path):
    _chain_of_three(tmp_path)
    d = tmp_path / "dest"
    (d / "govstate-transcripts-inc-20261002-0755.tar.gz.age").rename(d / "govstate-transcripts-inc-20261005-0755.tar.gz.age")
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 5 and out.stdout.startswith("BROKEN")       # the gap where it used to sit is found first


def test_a_restored_copy_older_than_the_manifest_says_is_stale(tmp_path):
    _chain_of_three(tmp_path)
    last = tmp_path / "dest" / "govstate-transcripts-inc-20261004-0755.tar.gz.age"
    def newer(body):                     # the manifest says a.jsonl was written later than any copy carried
        return "\n".join(l.rsplit("\t", 1)[0] + "\t9999999999" if l.startswith(".claude/projects/-root-thomas-agent/a.jsonl\t") else l
                         for l in body.splitlines()) + "\n"
    _edit_manifest(last, newer)
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 6 and out.stdout.startswith("STALE") and "stale=1" in out.stdout


def test_a_file_that_changed_while_the_backup_ran_is_counted_not_failed(tmp_path):
    _chain_of_three(tmp_path)
    last = tmp_path / "dest" / "govstate-transcripts-inc-20261004-0755.tar.gz.age"
    def older(body):                     # the manifest line predates the copy tar carried
        return "\n".join(l.rsplit("\t", 1)[0] + "\t1000000000" if l.startswith(".claude/projects/-root-thomas-agent/c.jsonl\t") else l
                         for l in body.splitlines()) + "\n"
    _edit_manifest(last, older)
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 0 and "changed-during-backup=1" in out.stdout


def test_a_legacy_archive_without_chain_headers_restores_but_is_not_verified(tmp_path):
    host = _transcripts_host(tmp_path)
    _backup(tmp_path, host, "20261001-0755", full=True)
    full = next((tmp_path / "dest").glob("govstate-transcripts-full-*"))
    _edit_manifest(full, lambda body: "\n".join(l.split("\t")[0] for l in body.splitlines()
                                                if not l.startswith(("# set=", "# kind=", "# stamp=", "# chain=", "# prev="))) + "\n")
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 7 and out.stdout.startswith("UNVERIFIED")
    assert _session(tmp_path / "t", "old-session.jsonl").exists()


# The parallel trial: human sessions only, zstd -3, separate names, skipped on a full disk.

def _trial(tmp_path: Path, host: Path, stamp: str, **env_extra):
    dest = tmp_path / "dest"
    (tmp_path / "age-recipients.txt").write_text(RECIPIENT + "\n", encoding="utf-8")
    env = {**os.environ, "PATH": f"{_bin(tmp_path)}:{os.environ['PATH']}", "HARNESS_BACKUP_HOST_ROOT": str(host),
           "HARNESS_BACKUP_DEST": str(dest), "HARNESS_BACKUP_AGE_RECIPIENTS": str(tmp_path / "age-recipients.txt"),
           "STUB_AGE_MODE": "ok", "HARNESS_BACKUP_STAMP": stamp, "HARNESS_TRIAL_MIN_FREE_GB": "0", **env_extra}
    out = subprocess.run(["bash", str(SCRIPT), "transcripts-trial"], capture_output=True, text=True, timeout=60, env=env)
    return out, dest, (dest / "backup.log").read_text() if (dest / "backup.log").exists() else ""


def _human_and_headless(tmp_path: Path) -> Path:
    host = _transcripts_host(tmp_path)
    proj = host / ".claude/projects/-root-thomas-agent"
    (proj / "human.jsonl").write_text('{"type":"user","origin":{"kind":"human"}}\n', encoding="utf-8")
    (proj / "human" / "subagents").mkdir(parents=True)
    (proj / "human" / "subagents" / "agent-1.jsonl").write_text("{}\n", encoding="utf-8")
    (proj / "headless.jsonl").write_text('{"type":"user","promptSource":"sdk"}\n', encoding="utf-8")
    (proj / "memory").mkdir()
    (proj / "memory" / "MEMORY.md").write_text("m\n", encoding="utf-8")
    return host


@pytest.mark.skipif(not shutil.which("zstd"), reason="zstd is not installed")
def test_the_trial_keeps_only_human_sessions_compressed_with_zstd_and_restores(tmp_path):
    out, dest, log = _trial(tmp_path, _human_and_headless(tmp_path), "20261009-0805")
    assert out.returncode == 0, out.stderr
    (archive,) = dest.glob("govstate-trialzst-full-20261009-0805.tar.zst.age")
    plain = subprocess.run(["zstd", "-dc"], input=archive.read_bytes()[len(HEADER):], capture_output=True, check=True).stdout
    with tarfile.open(fileobj=io.BytesIO(plain)) as tar:
        names = {m.name for m in tar.getmembers() if m.isfile()}
    assert ".claude/projects/-root-thomas-agent/human.jsonl" in names
    assert ".claude/projects/-root-thomas-agent/human/subagents/agent-1.jsonl" in names
    assert ".claude/projects/-root-thomas-agent/memory/MEMORY.md" in names
    assert ".claude/prompt-collector/state.json" in names
    assert ".claude/projects/-root-thomas-agent/headless.jsonl" not in names
    assert ".claude/projects/-root-thomas-agent/old-session.jsonl" not in names    # no human marker
    assert " OK mode=transcripts-trial kind=full " in log and " sel=human " in log
    assert not list(dest.glob("govstate-transcripts-*"))                             # the real set is untouched
    r = _restore(tmp_path, tmp_path / "t", "--set", "trialzst", "--expect-head", "20261009-0805")
    assert r.returncode == 0 and r.stdout.startswith("CONTENT_VERIFIED set=trialzst"), r.stdout + r.stderr


def test_the_trial_is_skipped_not_failed_when_the_disk_is_short(tmp_path):
    out, dest, log = _trial(tmp_path, _human_and_headless(tmp_path), "20261009-0805", HARNESS_TRIAL_MIN_FREE_GB="999999")
    assert out.returncode == 0
    assert "SKIPPED mode=transcripts-trial reason=disk" in log
    assert not list(dest.glob("govstate-trialzst-*"))



# Core-asset content (2026-10-09 final review): the collector state and memory carry a sha256.

def test_only_core_assets_carry_a_sha256_in_the_manifest(tmp_path):
    host = _human_and_headless(tmp_path)
    _backup(tmp_path, host, "20261009-0100", full=True)
    (archive,) = (tmp_path / "dest").glob("govstate-transcripts-full-*")
    with tarfile.open(fileobj=io.BytesIO(gzip.decompress(archive.read_bytes()[len(HEADER):]))) as tar:
        body = tar.extractfile("TRANSCRIPTS_MANIFEST.txt").read().decode()
    rows = {l.split("\t")[0]: l.split("\t") for l in body.splitlines() if not l.startswith("#")}
    assert "# hashed=collector-state,memory" in body
    assert len(rows[".claude/prompt-collector/state.json"]) == 4 and len(rows[".claude/prompt-collector/state.json"][3]) == 64
    assert len(rows[".claude/projects/-root-thomas-agent/memory/MEMORY.md"]) == 4
    assert len(rows[".claude/projects/-root-thomas-agent/human.jsonl"]) == 3          # conversation logs: no hash


def test_a_core_asset_whose_content_does_not_match_is_broken_not_verified(tmp_path):
    host = _transcripts_host(tmp_path)
    _backup(tmp_path, host, "20261001-0755", full=True)
    full = next((tmp_path / "dest").glob("govstate-transcripts-full-*"))
    _edit_manifest(full, lambda body: "\n".join(
        (l.rsplit("\t", 1)[0] + "\t" + "0" * 64) if l.startswith(".claude/prompt-collector/state.json\t") else l
        for l in body.splitlines()) + "\n")
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 8 and out.stdout.startswith("BROKEN") and "core-hash=ok:0,bad:1" in out.stdout


def test_without_core_assets_the_verdict_is_chain_verified(tmp_path):
    host = _transcripts_host(tmp_path, collector=False)
    _backup(tmp_path, host, "20261001-0755", full=True)
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 0 and out.stdout.startswith("CHAIN_VERIFIED") and "core-hash=ok:0,bad:0" in out.stdout


def test_a_core_asset_that_changed_during_the_backup_is_skipped_not_failed(tmp_path):
    host = _transcripts_host(tmp_path)
    _backup(tmp_path, host, "20261001-0755", full=True)
    full = next((tmp_path / "dest").glob("govstate-transcripts-full-*"))
    _edit_manifest(full, lambda body: "\n".join(       # the line predates the copy, and its hash is of an older content
        (".claude/prompt-collector/state.json\t2\t1000000000\t" + "0" * 64) if l.startswith(".claude/prompt-collector/state.json\t") else l
        for l in body.splitlines()) + "\n")
    out = _restore(tmp_path, tmp_path / "t")
    assert out.returncode == 0 and "core-hash=ok:0,bad:0,skipped:1" in out.stdout and "changed-during-backup=1" in out.stdout
