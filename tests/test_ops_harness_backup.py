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
# -R <file> -o <out>: behave per $STUB_AGE_MODE
out=""; while [ $# -gt 0 ]; do case "$1" in -o) out="$2"; shift 2;; *) shift;; esac; done
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
                                         ".claude/prompt-collector/state.json"]
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
    (inc,) = dest.glob("govstate-transcripts-inc-*.tar.gz.age")
    assert _members(inc) == [".claude/projects/-root-thomas-agent/new-session.jsonl"]
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
