"""Read one numeric HDF5 cell from a bounded list of product datasets."""

from math import isfinite, prod

import h5py

MAX_CHUNK_BYTES = 8 * 1024 * 1024


def inspect_hdf(root, path, candidates):
    with h5py.File(path, "r") as handle:
        for name in candidates:
            obj = handle
            for part in name.split("/"):
                if not isinstance(obj, h5py.Group):
                    break
                link = obj.get(part, getlink=True)
                if link is None:
                    break
                if not isinstance(link, h5py.HardLink):
                    raise ValueError("indirect HDF links are not allowed")
                obj = obj[part]
            else:
                if not isinstance(obj, h5py.Dataset):
                    continue
                props = obj.id.get_create_plist()
                if obj.is_virtual or props.get_external_count():
                    raise ValueError("external or virtual storage is not allowed")
                if obj.dtype.kind not in "iuf" or not obj.shape or 0 in obj.shape:
                    raise ValueError("expected a nonempty numeric image")
                index = (0,) * obj.ndim
                if obj.chunks:
                    decoded = prod(obj.chunks) * obj.dtype.itemsize
                    if decoded > MAX_CHUNK_BYTES:
                        raise ValueError("decoded chunk exceeds probe limit")
                    info = obj.id.get_chunk_info_by_coord(index)
                    if not info.size:
                        raise ValueError("first chunk is not stored")
                    if info.size > MAX_CHUNK_BYTES:
                        raise ValueError("stored chunk exceeds probe limit")
                elif not obj.id.get_storage_size():
                    raise ValueError("dataset has no stored values")
                value = obj[index].item()
                return {
                    "sample": path.relative_to(root).as_posix(),
                    "dataset": name,
                    "index": list(index),
                    "value": value if isfinite(value) else None,
                }
    raise ValueError("no supported image dataset")
