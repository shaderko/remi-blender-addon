"""Bounded, lossless artifact transport shared by CLI and embedded clients."""
import struct
import time
import zlib

ACCEPT_COMPRESSION = "remi-v1"


def decode_artifact(wire, encoding, size):
    if len(wire) > size:
        raise ValueError("Artifact transfer exceeds its limit")
    if encoding in (None, "", "identity"):
        return wire
    if encoding not in ("gzip", "gaussian-gzip-v1"):
        raise ValueError("Unsupported artifact compression")
    decoder = zlib.decompressobj(31)
    body = decoder.decompress(wire, size + 1)
    if len(body) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Compressed artifact is truncated, oversized or has trailing data")
    if encoding == "gzip":
        return body
    if (len(body) < 16 or body[:8] != b"REMIGAUS"
            or struct.unpack_from("<I", body, 8)[0] != 1):
        raise ValueError("Invalid compressed Gaussian header")
    count = struct.unpack_from("<I", body, 12)[0]
    if size != 16 + count * 56:
        raise ValueError("Invalid compressed Gaussian size")
    output = bytearray(size)
    output[:16] = body[:16]
    for byte in range(56):
        output[16 + byte::56] = body[16 + byte * count:16 + (byte + 1) * count]
    return bytes(output)


def read_artifact(response, size, timeout=30):
    if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        raise ValueError("Unexpected HTTP content encoding")
    deadline = time.monotonic() + timeout
    parts = []
    received = 0
    read = getattr(response, "read1", response.read)
    while received <= size:
        if time.monotonic() >= deadline:
            raise ValueError("Artifact download timed out")
        chunk = read(min(65536, size + 1 - received))
        if not chunk:
            break
        parts.append(chunk)
        received += len(chunk)
    return decode_artifact(b"".join(parts), response.headers.get("X-Remi-Compression"), size)
