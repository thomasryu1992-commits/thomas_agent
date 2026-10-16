"""Reading the signer trust files, and checking them against the installed anchor (CR-12 T3).

Three root-owned files decide whose signature counts:

- ``allowed_signers`` — the keys ``ssh-keygen -Y verify`` accepts, each limited by ``namespaces=``
  to the document types that key may sign (TYPE-B-KEY K1);
- ``revoked_keys`` — keys that no longer count; it must exist, and may be empty;
- ``revoked_approvals`` — approval ids withdrawn before use.

Each is opened through a descriptor that refuses to follow a link and is then checked on what was
actually opened: a regular file, one link, owned by the expected owner, writable by nobody else.
The caller passes the expected owner (root in production) so the checks are testable as any user.

``allowed_signers`` is then held to a closed format — one principal, ``ssh-ed25519`` keys only,
only the ``namespaces``/``valid-after``/``valid-before`` options, no ``cert-authority`` — and its
key set must equal the anchor shipped inside the reviewed runner install. A key added to the file
but not to the anchor refuses the whole file: changing who may sign means a new install.

**What this does not protect (stated in the design):** an actor that can rewrite the install
itself — root, or the runner's Docker-holding actor if it is compromised — can change the anchor
and the files together. The anchor stops writers of the trust files that cannot write the install.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from runtime.read_only_kernel import integrity

from .reasons import RunnerRefused

MAX_TRUST_FILE_BYTES = 64 * 1024

DEPLOY_NAMESPACE = "deploy-approval.v1@thomas-agent"
LATCH_RESOLUTION_NAMESPACE = "latch-resolution.v1@thomas-agent"
STORE_REINIT_NAMESPACE = "store-reinit.v1@thomas-agent"
KNOWN_NAMESPACES = frozenset({DEPLOY_NAMESPACE, LATCH_RESOLUTION_NAMESPACE, STORE_REINIT_NAMESPACE})

_KEY_TYPE = "ssh-ed25519"
_BLOB = r"[A-Za-z0-9+/]{20,700}={0,2}"
_SIGNER_LINE = re.compile(
    rf"^(?P<principal>[A-Za-z0-9._@-]{{1,64}}) (?P<opts>[^ ]+) (?P<type>{_KEY_TYPE}) (?P<blob>{_BLOB})"
    r"(?: [\x21-\x7e][\x20-\x7e]{0,200})?\Z"
)
_OPTION = re.compile(r'(namespaces|valid-after|valid-before)="([^"\s]*)"')
_STAMP = re.compile(r"^\d{8}(?:\d{4}(?:\d{2})?)?Z?\Z")
_REVOKED_KEY_LINE = re.compile(rf"^(?P<type>{_KEY_TYPE}) (?P<blob>{_BLOB})(?: [\x21-\x7e][\x20-\x7e]{{0,200}})?\Z")
_APPROVAL_ID = re.compile(r"^dap_[a-z2-7]{26}\Z")

SignerKey = tuple[str, str]


@dataclass(frozen=True)
class TrustMaterial:
    """The bytes the verifier is handed, the keys they name, and one hash over both files."""

    allowed_signers: bytes
    revoked_keys: bytes
    keys: frozenset[SignerKey]
    sha256: str


def protected_files_supported() -> bool:
    return hasattr(os, "O_NOFOLLOW") and hasattr(os, "getuid")


def read_protected_file(path: Path, *, owner_uid: int, unavailable: str, insecure: str) -> bytes:
    """The bytes of ``path``, opened without following a link and checked on its descriptor.

    ``unavailable`` is raised when the file cannot be opened or read (or the platform cannot open
    it safely); ``insecure`` when it opens but is not a private regular file of ``owner_uid``."""
    if not protected_files_supported():
        raise RunnerRefused(unavailable)
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        raise RunnerRefused(unavailable) from None
    try:
        st = os.fstat(fd)
        if (not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or st.st_uid != owner_uid
                or st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)):
            raise RunnerRefused(insecure)
        if st.st_size > MAX_TRUST_FILE_BYTES:
            raise RunnerRefused(insecure)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_TRUST_FILE_BYTES:
                raise RunnerRefused(insecure)
            chunks.append(chunk)
        return b"".join(chunks)
    except OSError:
        raise RunnerRefused(unavailable) from None
    finally:
        os.close(fd)


def _content_lines(raw: bytes, *, insecure: str) -> list[str]:
    if not raw.isascii():
        raise RunnerRefused(insecure)
    text = raw.decode("ascii")
    if "\r" in text:
        raise RunnerRefused(insecure)
    return [line for line in text.split("\n") if line.strip() and not line.lstrip().startswith("#")]


def parse_allowed_signers(raw: bytes, *, principal: str, permitted_namespaces: frozenset[str]) -> frozenset[SignerKey]:
    """The keys in a closed-format ``allowed_signers``, or ``TRUST_CONFIG_INSECURE``."""
    keys: set[SignerKey] = set()
    for line in _content_lines(raw, insecure="TRUST_CONFIG_INSECURE"):
        m = _SIGNER_LINE.match(line)
        if m is None or m.group("principal") != principal:
            raise RunnerRefused("TRUST_CONFIG_INSECURE")
        opts = m.group("opts")
        found = _OPTION.findall(opts)
        if ",".join(f'{name}="{value}"' for name, value in found) != opts:
            raise RunnerRefused("TRUST_CONFIG_INSECURE")
        names = [name for name, _ in found]
        if len(names) != len(set(names)) or "namespaces" not in names:
            raise RunnerRefused("TRUST_CONFIG_INSECURE")
        for name, value in found:
            if name == "namespaces":
                listed = value.split(",")
                if not value or len(listed) != len(set(listed)) or not set(listed) <= permitted_namespaces:
                    raise RunnerRefused("TRUST_CONFIG_INSECURE")
            elif not _STAMP.match(value):
                raise RunnerRefused("TRUST_CONFIG_INSECURE")
        keys.add((m.group("type"), m.group("blob")))
    if not keys:
        raise RunnerRefused("TRUST_CONFIG_INSECURE")
    return frozenset(keys)


def parse_revoked_keys(raw: bytes) -> frozenset[SignerKey]:
    """The keys in a one-per-line ``revoked_keys`` (empty is allowed), or ``TRUST_CONFIG_INSECURE``."""
    keys: set[SignerKey] = set()
    for line in _content_lines(raw, insecure="TRUST_CONFIG_INSECURE"):
        m = _REVOKED_KEY_LINE.match(line)
        if m is None:
            raise RunnerRefused("TRUST_CONFIG_INSECURE")
        keys.add((m.group("type"), m.group("blob")))
    return frozenset(keys)


def load_trust(allowed_signers_path: Path, revoked_keys_path: Path, *, owner_uid: int, principal: str,
               anchor: frozenset[SignerKey], permitted_namespaces: frozenset[str] = KNOWN_NAMESPACES) -> TrustMaterial:
    """Read, check and hash both trust files, and hold the signer keys to the installed anchor."""
    if not anchor or not permitted_namespaces <= KNOWN_NAMESPACES:
        raise RunnerRefused("TRUST_CONFIG_UNAVAILABLE")
    allowed = read_protected_file(allowed_signers_path, owner_uid=owner_uid,
                                  unavailable="TRUST_CONFIG_UNAVAILABLE", insecure="TRUST_CONFIG_INSECURE")
    revoked = read_protected_file(revoked_keys_path, owner_uid=owner_uid,
                                  unavailable="TRUST_CONFIG_UNAVAILABLE", insecure="TRUST_CONFIG_INSECURE")
    keys = parse_allowed_signers(allowed, principal=principal, permitted_namespaces=permitted_namespaces)
    parse_revoked_keys(revoked)
    if keys != anchor:
        raise RunnerRefused("TRUST_ANCHOR_MISMATCH")
    digest = integrity.sha256_value({
        "allowed_signers": integrity.sha256_bytes(allowed),
        "revoked_keys": integrity.sha256_bytes(revoked),
    })
    return TrustMaterial(allowed_signers=allowed, revoked_keys=revoked, keys=keys, sha256=digest)


def load_revoked_approvals(path: Path, *, owner_uid: int) -> frozenset[str]:
    """The withdrawn approval ids, one per line. Any problem at all is ``REVOCATION_LIST_UNAVAILABLE``:
    a revocation list that cannot be read with certainty must not read as "nothing revoked"."""
    raw = read_protected_file(path, owner_uid=owner_uid, unavailable="REVOCATION_LIST_UNAVAILABLE",
                              insecure="REVOCATION_LIST_UNAVAILABLE")
    lines = _content_lines(raw, insecure="REVOCATION_LIST_UNAVAILABLE")
    ids = [line.strip() for line in lines]
    if any(not _APPROVAL_ID.match(i) for i in ids):
        raise RunnerRefused("REVOCATION_LIST_UNAVAILABLE")
    return frozenset(ids)
