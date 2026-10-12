"""IMAP4 modified UTF-7 (RFC 3501 §5.1.3) encode/decode.

Folder names on the wire look like "&AOk-t&AOk-" rather than "été". Python
ships no codec for this, so here it is in thirty lines.
"""

from __future__ import annotations

import binascii


def decode(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("ascii", "replace")
    out: list[str] = []
    buffer: list[bytes] = []
    in_shift = False
    for byte in value:
        char = bytes([byte])
        if in_shift:
            if char == b"-":
                if not buffer:
                    out.append("&")  # "&-" is a literal ampersand
                else:
                    out.append(_b64_decode(b"".join(buffer)))
                buffer = []
                in_shift = False
            else:
                buffer.append(char)
        elif char == b"&":
            in_shift = True
        else:
            out.append(char.decode("latin-1"))
    if buffer:  # unterminated shift; salvage what we can
        out.append(_b64_decode(b"".join(buffer)))
    return "".join(out)


def encode(value: str) -> bytes:
    out: list[bytes] = []
    shift: list[str] = []
    for char in value:
        if 0x20 <= ord(char) <= 0x7E:
            if shift:
                out.append(_b64_encode("".join(shift)))
                shift = []
            out.append(b"&-" if char == "&" else char.encode("ascii"))
        else:
            shift.append(char)
    if shift:
        out.append(_b64_encode("".join(shift)))
    return b"".join(out)


def _b64_decode(chunk: bytes) -> str:
    padded = chunk.replace(b",", b"/")
    padded += b"=" * ((4 - len(padded) % 4) % 4)
    try:
        return binascii.a2b_base64(padded).decode("utf-16-be")
    except Exception:
        return chunk.decode("latin-1", "replace")


def _b64_encode(chunk: str) -> bytes:
    raw = binascii.b2a_base64(chunk.encode("utf-16-be")).rstrip(b"\n=")
    return b"&" + raw.replace(b"/", b",") + b"-"
