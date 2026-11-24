"""Bounded, lossless artifact transport shared by CLI and embedded clients."""
import hashlib
import os
from pathlib import Path
import struct
import tempfile
import time
import zlib

from .atomic import replace_file

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
    return unshuffle_gaussian(body, size)


def unshuffle_gaussian(body, size):
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


def artifact_chunks(response, size, *, timeout=300, progress=None, cancelled=lambda: False):
    if response.headers.get("Content-Encoding", "identity").lower() not in ("", "identity"):
        raise ValueError("Unexpected HTTP content encoding")
    encoding = response.headers.get('X-Remi-Compression', 'identity')
    if encoding not in ('', 'identity', 'gzip', 'gaussian-gzip-v1'):
        raise ValueError('Unsupported artifact compression')
    length = response.headers.get('Content-Length')
    length = int(length) if length is not None else None
    if length is not None and not 0 <= length <= size:
        raise ValueError('Artifact transfer exceeds its limit')
    deadline = time.monotonic() + timeout
    decoder = zlib.decompressobj(31) if encoding in ('gzip', 'gaussian-gzip-v1') else None
    received = 0
    decoded = 0
    read = getattr(response, "read1", response.read)
    while received <= size:
        if cancelled():
            raise InterruptedError('Download cancelled')
        if time.monotonic() >= deadline:
            raise TimeoutError("Artifact download timed out")
        if length is not None and received == length:
            break
        chunk = read(min(65536, size + 1 - received))
        if not chunk:
            break
        received += len(chunk)
        if received > size or (length is not None and received > length):
            raise ValueError('Artifact transfer exceeds its limit')
        if progress:
            progress({'state': 'downloading', 'receivedBytes': received, 'totalBytes': length or size})
        if decoder is None:
            decoded += len(chunk)
            yield chunk
        else:
            pending = chunk
            while True:
                output = decoder.decompress(pending, 65536)
                pending = decoder.unconsumed_tail
                decoded += len(output)
                if decoded > size or decoder.unused_data:
                    raise ValueError('Invalid compressed artifact length')
                if output:
                    yield output
                if not pending and len(output) < 65536:
                    break
    if cancelled():
        raise InterruptedError('Download cancelled')
    if (decoded != size or (length is not None and received != length)
            or (decoder is not None and (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail))):
        raise ValueError('Artifact is truncated, oversized or has trailing data')


def read_artifact(response, size, timeout=300, *, progress=None, cancelled=lambda: False):
    body = b''.join(artifact_chunks(response, size, timeout=timeout, progress=progress, cancelled=cancelled))
    return unshuffle_gaussian(body, size) if response.headers.get('X-Remi-Compression') == 'gaussian-gzip-v1' else body


def download_artifact(response, path, size, checksum, *, progress=None, cancelled=lambda: False, timeout=300):
    """Stream to a private spool; only verified original bytes replace the cache."""
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix='.dl-', dir=path.parent)
    temporary = Path(name)
    shuffled = response.headers.get('X-Remi-Compression') == 'gaussian-gzip-v1'
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, 'w+b') as stream:
            for chunk in artifact_chunks(response, size, timeout=timeout, progress=progress, cancelled=cancelled):
                stream.write(chunk)
                if not shuffled:
                    digest.update(chunk)
            if progress:
                progress({'state': 'verifying'})
            if shuffled:
                # Native Gaussian assets are capped at 16 MiB by the caller.
                stream.seek(0)
                body = unshuffle_gaussian(stream.read(), size)
                digest.update(body)
                stream.seek(0)
                stream.write(body)
            if digest.hexdigest() != checksum:
                raise ValueError('Artifact checksum mismatch')
            stream.flush()
            os.fsync(stream.fileno())
        replace_file(temporary, path, cancelled=cancelled)
        return str(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
