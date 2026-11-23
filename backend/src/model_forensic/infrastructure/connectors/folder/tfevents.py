"""TensorBoard event files (``events.out.tfevents.*``) → scalar points, without TensorFlow or TensorBoard.

An event file is a TFRecord stream (``uint64 length · uint32 crc · data · uint32 crc``) of ``Event`` protobufs. The
fields read: ``Event.wall_time`` (1, double), ``Event.step`` (2, int64), ``Event.summary`` (5) →
``Summary.value`` (1) → ``Value.tag`` (1) with either ``simple_value`` (2, float — PyTorch / Lightning
``add_scalar``) or a scalar ``tensor`` (8 — TF2 ``tf.summary.scalar``: ``float_val`` 5 / ``double_val`` 6 /
``tensor_content`` 4). Images, histograms and text are skipped. A file still being written ends mid-record: the
records before it are kept. Untrusted input: lengths are bounded, nothing is executed.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

MAX_RECORD = 64 << 20


def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = out = 0
    while i < len(b):
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        if not c & 0x80:
            return out, i
        shift += 7
        if shift > 63:
            break
    raise ValueError("bad varint")


def _fields(b: bytes) -> Iterator[tuple[int, int, int | bytes]]:
    """(field number, wire type, value) — an int for varints, the raw bytes otherwise."""
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        fn, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
            yield fn, wt, v
        elif wt == 1:
            yield fn, wt, b[i : i + 8]
            i += 8
        elif wt == 2:
            n, i = _varint(b, i)
            yield fn, wt, b[i : i + n]
            i += n
        elif wt == 5:
            yield fn, wt, b[i : i + 4]
            i += 4
        else:
            raise ValueError(f"unsupported wire type {wt}")


def _raw(v: int | bytes) -> bytes:
    return v if isinstance(v, bytes) else b""


def _int(v: int | bytes) -> int:
    return v if isinstance(v, int) else 0


def _tensor_scalar(b: bytes) -> float | None:
    dtype = 0
    vals: list[float] = []
    content = b""
    for fn, wt, v in _fields(b):
        if fn == 1 and wt == 0:
            dtype = _int(v)
        elif fn == 4 and wt == 2:
            content = _raw(v)
        elif fn == 5:  # float_val (packed: wt 2, or one: wt 5)
            raw = _raw(v)
            vals += [x for (x,) in struct.iter_unpack("<f", raw[: len(raw) // 4 * 4])]
        elif fn == 6:  # double_val
            raw = _raw(v)
            vals += [x for (x,) in struct.iter_unpack("<d", raw[: len(raw) // 8 * 8])]
    if vals:
        return float(vals[0])
    if dtype == 1 and len(content) >= 4:  # DT_FLOAT
        return float(struct.unpack("<f", content[:4])[0])
    if dtype == 2 and len(content) >= 8:  # DT_DOUBLE
        return float(struct.unpack("<d", content[:8])[0])
    return None


def _value(b: bytes) -> tuple[str, float | None]:
    tag, num = "", None
    for fn, wt, x in _fields(b):
        if fn == 1 and wt == 2:
            tag = _raw(x).decode("utf-8", errors="replace")
        elif fn == 2 and wt == 5:
            num = float(struct.unpack("<f", _raw(x))[0])
        elif fn == 8 and wt == 2:
            num = _tensor_scalar(_raw(x))
    return tag, num


def read_scalars(path: Path) -> Iterator[tuple[str, float, float, float]]:
    """``(tag, step, value, wall_time)`` for every scalar in the file."""
    with path.open("rb") as fh:
        while True:
            head = fh.read(12)
            if len(head) < 12:
                return
            (n,) = struct.unpack("<Q", head[:8])
            if n > MAX_RECORD:
                return
            data = fh.read(n)
            if len(data) < n or len(fh.read(4)) < 4:
                return  # a record still being written
            try:
                wall, step, summary = 0.0, 0, b""
                for fn, wt, v in _fields(data):
                    if fn == 1 and wt == 1:
                        wall = float(struct.unpack("<d", _raw(v))[0])
                    elif fn == 2 and wt == 0:
                        step = _int(v)
                    elif fn == 5 and wt == 2:
                        summary = _raw(v)
                for fn, wt, val in _fields(summary):
                    if fn == 1 and wt == 2:
                        tag, num = _value(_raw(val))
                        if tag and num is not None:
                            yield tag, float(step), num, wall
            except (ValueError, struct.error):
                continue  # one malformed record: skip it, keep reading
