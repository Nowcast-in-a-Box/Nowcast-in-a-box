"""Read one physical radar chunk from the downloader's local Zarr v2 format."""

import json
import math
import re
from pathlib import Path

import numpy as np
from numcodecs import blosc, zstd

from utils.sample_files import first_file

_LIMIT = 8 * 1024 * 1024


def _read(path, limit):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"Not a bounded regular file: {path.name}")
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"File exceeds probe limit: {path.name}")
    return data


def inspect_zarr(root: Path, variable: str) -> dict:
    root = root.resolve(strict=True)

    def matches(path):
        return (
            path.parent.name == variable
            and (root / path).parent.parent.suffix.lower() == ".zarr"
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", path.name) is not None
        )

    chunk = root / first_file(root, matches)
    if not chunk.resolve(strict=True).is_relative_to(root):
        raise ValueError("Zarr chunk outside data root")
    meta = json.loads(_read(chunk.parent / ".zarray", 65536))
    if (
        meta["zarr_format"] != 2
        or meta.get("filters")
        or meta.get("dimension_separator", ".") != "."
    ):
        raise ValueError("Expected an unfiltered dot-separated Zarr v2 array")
    if not isinstance(meta["dtype"], str) or not re.fullmatch(
        r"(?:[<>][iuf][248]|[|<>][iu]1)", meta["dtype"]
    ):
        raise ValueError("Unsupported Zarr numeric type")
    dtype = np.dtype(meta["dtype"])
    shape, chunks = meta["shape"], meta["chunks"]
    for dims in (shape, chunks):
        if (
            not isinstance(dims, list)
            or len(dims) != 3
            or any(type(n) is not int or n < 1 for n in dims)
        ):
            raise ValueError("Expected three positive Zarr dimensions")
    index = [int(k) * n for k, n in zip(chunk.name.split("."), chunks)]
    if any(i >= n for i, n in zip(index, shape)):
        raise ValueError("Chunk outside Zarr shape")
    size = math.prod(chunks) * dtype.itemsize
    if size > _LIMIT:
        raise ValueError("Decoded chunk exceeds probe limit")
    data = _read(chunk, _LIMIT)
    codec = meta["compressor"]
    if codec is not None:
        if codec["id"] == "blosc":
            if (
                len(data) < 16
                or int.from_bytes(data[4:8], "little") != size
                or int.from_bytes(data[12:16], "little") != len(data)
            ):
                raise ValueError("Invalid Blosc chunk size")
            data = blosc.decompress(data, bytearray(size))
        elif codec["id"] == "zstd":
            data = zstd.decompress(data, bytearray(size))
        else:
            raise ValueError("Unsupported Zarr compressor")
    if len(data) != size:
        raise ValueError("Unexpected Zarr chunk size")
    value = np.frombuffer(data, dtype=dtype, count=1).item()
    return {
        "sample": chunk.relative_to(root).as_posix(),
        "variable": variable,
        "index": index,
        "value": value if math.isfinite(value) else None,
    }
