"""The one JSON writer for every PitWall shard (SPEC 3.1).

    dump_json(obj, path) -> (bytes, hash12)     plain JSON  (manifest.json, models.json)
    dump_gz(obj, path)   -> (bytes, hash12)     gzip level 9, header mtime 0, no file name

Serialization: `json.dumps(obj, separators=(",", ":"), ensure_ascii=False, allow_nan=False)`.
Before dumping, `clean()` converts numpy scalars/arrays to Python, enums to their values,
NaN/inf to None and -0.0 to 0.0.  Dict key order is preserved (callers fix it).
Output is a pure function of the object: same object -> same bytes.

`hash12` is the first 12 hex chars of SHA-256 of the bytes written to disk.
"""
import gzip
import hashlib
import json
import math
import os
import zlib
from enum import Enum
from typing import Any, Optional, Tuple

try:  # numpy is a hard dependency of the pipeline; guard only to keep this module importable alone
    import numpy as np
except Exception:  # pragma: no cover
    np = None  # type: ignore


def clean(x: Any) -> Any:
    """Recursively convert `x` to plain JSON-safe Python."""
    if x is None or isinstance(x, (bool, str)):
        return x
    if isinstance(x, Enum):
        return clean(x.value)
    if np is not None:
        if isinstance(x, np.ndarray):
            return [clean(v) for v in x.tolist()]
        if isinstance(x, np.generic):
            return clean(x.item())
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if math.isnan(x) or math.isinf(x):
            return None
        return 0.0 if x == 0.0 else x  # -0.0 -> 0.0
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [clean(v) for v in x]
    if hasattr(x, "item"):
        return clean(x.item())
    raise TypeError(f"cannot serialise {type(x).__name__}")


def dumps(obj: Any) -> bytes:
    return json.dumps(
        clean(obj), separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def hash12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def gzip_bytes(data: bytes) -> bytes:
    """gzip level 9, mtime 0, empty file name field: deterministic for a given zlib."""
    co = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    body = co.compress(data) + co.flush()
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"  # deflate, no flags, mtime 0, XFL 2 (max), OS unknown
    trailer = (zlib.crc32(data) & 0xFFFFFFFF).to_bytes(4, "little") + (len(data) & 0xFFFFFFFF).to_bytes(4, "little")
    return header + body + trailer


def _write(path: str, data: bytes) -> None:
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def dump_json(obj: Any, path: str) -> Tuple[bytes, str]:
    data = dumps(obj) + b"\n"
    _write(path, data)
    return data, hash12(data)


def dump_gz(obj: Any, path: str) -> Tuple[bytes, str]:
    data = gzip_bytes(dumps(obj))
    _write(path, data)
    return data, hash12(data)


def read_gz(path: str) -> Any:
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] == b"\x1f\x8b":
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8"))


def read_json(path: str) -> Any:
    return read_gz(path)  # handles both


# ---- rounding helpers (SPEC 3.1 units and rounding) -----------------------------------------

def rnd(x: Any, nd: int) -> Optional[float]:
    """round(float(x), nd); None/NaN/inf -> None; -0.0 -> 0.0."""
    if x is None:
        return None
    if isinstance(x, bool):
        return float(x)
    f = float(x)
    if math.isnan(f) or math.isinf(f):
        return None
    f = round(f, nd)
    return 0.0 if f == 0.0 else f


def rint(x: Any) -> Optional[int]:
    """norm_epa / unitless_epa: integer."""
    if x is None:
        return None
    f = float(x)
    if math.isnan(f) or math.isinf(f):
        return None
    return int(round(f))
