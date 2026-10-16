"""Strict decoding of a signed document, and the check that its bytes are the canonical bytes.

A signed document is verified on the bytes it arrived as, and only then parsed. Parsing must not
accept anything two parsers could read differently, or a document could carry a signature over
one meaning and be acted on with another:

- ASCII only, so there is one byte form for every value;
- no duplicate keys (``json`` would keep the last one silently);
- no floats, ``NaN`` or ``Infinity``;
- and after parsing, re-encoding with the kernel's canonical encoder must give back the exact
  bytes that were signed. Whitespace, key order, escapes and number spellings are all pinned by
  that single comparison.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from typing import Any

from runtime.read_only_kernel import integrity
from runtime.read_only_kernel.integrity import IntegrityError

from .reasons import RunnerRefused

MAX_DOCUMENT_BYTES = 2048
MAX_SIGNATURE_BYTES = 4096

_SIGNATURE = re.compile(
    r"^-----BEGIN SSH SIGNATURE-----\n(?:[A-Za-z0-9+/=]{1,76}\n)+-----END SSH SIGNATURE-----\n?\Z"
)
_B64 = re.compile(r"^[A-Za-z0-9+/]+={0,2}\Z")


class _Rejected(ValueError):
    pass


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise _Rejected("duplicate key")
        out[key] = value
    return out


def _no_float(_: str) -> Any:
    raise _Rejected("float")


def _no_constant(_: str) -> Any:
    raise _Rejected("non-finite constant")


def decode_document_b64(text: Any) -> bytes:
    """The document bytes from their base64 carrier, or ``APPROVAL_MALFORMED``."""
    if not isinstance(text, str) or not text or len(text) > (MAX_DOCUMENT_BYTES * 4) // 3 + 4:
        raise RunnerRefused("APPROVAL_MALFORMED")
    if not _B64.match(text):
        raise RunnerRefused("APPROVAL_MALFORMED")
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise RunnerRefused("APPROVAL_MALFORMED") from None
    if not raw or len(raw) > MAX_DOCUMENT_BYTES:
        raise RunnerRefused("APPROVAL_MALFORMED")
    return raw


def check_signature_text(text: Any) -> str:
    """An armored SSH signature in the accepted shape, or ``APPROVAL_MALFORMED``."""
    if not isinstance(text, str) or len(text) > MAX_SIGNATURE_BYTES or not text.isascii():
        raise RunnerRefused("APPROVAL_MALFORMED")
    if not _SIGNATURE.match(text):
        raise RunnerRefused("APPROVAL_MALFORMED")
    return text


def parse_strict(raw: bytes) -> dict[str, Any]:
    """Parse signed bytes into an object under the rules above, or ``APPROVAL_MALFORMED``."""
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_DOCUMENT_BYTES or not raw.isascii():
        raise RunnerRefused("APPROVAL_MALFORMED")
    try:
        value = json.loads(
            raw.decode("ascii"),
            object_pairs_hook=_no_duplicate_keys,
            parse_float=_no_float,
            parse_constant=_no_constant,
        )
    except (_Rejected, ValueError):
        raise RunnerRefused("APPROVAL_MALFORMED") from None
    if not isinstance(value, dict):
        raise RunnerRefused("APPROVAL_MALFORMED")
    return value


def require_canonical(raw: bytes, value: dict[str, Any]) -> None:
    """``APPROVAL_NOT_CANONICAL`` unless ``value`` re-encodes to exactly ``raw``."""
    try:
        encoded = integrity.canonical_bytes(value)
    except IntegrityError:
        raise RunnerRefused("APPROVAL_MALFORMED") from None
    if encoded != raw:
        raise RunnerRefused("APPROVAL_NOT_CANONICAL")
