# Author: sonderlau
# Last modified: 2026-10-07
# Modified by: sonderlau
"""SEVIR VIL source.

The processed file stores normalized VIL in [0, 1]. This source restores
raw VIL [0, 255] and returns the six context frames. It does not select
the five frames a model consumes.
"""

from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path

from interfaces.data import (
    ChannelSpec,
    DataBundle,
    DataCapabilities,
    DataRequest,
    RuntimeDataAdapter,
    SpatialSpec,
    VariableSpec,
    bundle_from_parts,
    loader_config,
)
from interfaces.errors import ConstraintError

# Dataset. Fixed before any request runs.
ID = "sevir"
VERSION = "1.0.0"
TITLE = "SEVIR VIL"
INSTRUMENT = "VIL"
VARIABLE_ID = "vil"
VARIABLE_LONG_NAME = "vertically_integrated_liquid"
VARIABLE_UNITS = "raw"
CHANNELS = "closed"
NATIVE_CADENCE_MINUTES = 5
CROP = False
FORMATS = ("hdf5",)
H5_NAME = "sevir_vil.h5"
CONTEXT_FRAMES = 6
TARGET_FRAMES = 20
GRID_SHAPE = (128, 128)
CADENCE = dt.timedelta(minutes=NATIVE_CADENCE_MINUTES)
RAW_MAX = 255.0


class Source(RuntimeDataAdapter):
    def inspect_sample(self, root: Path) -> dict:
        from utils.hdf_sample import inspect_hdf
        from utils.sample_files import first_file

        path = first_file(root, lambda p: p.name == H5_NAME)
        return inspect_hdf(root, path, ("inputs",))

    CAPABILITIES = DataCapabilities(
        id=ID,
        version=VERSION,
        title=TITLE,
        instrument=INSTRUMENT,
        channels=ChannelSpec(
            mode=CHANNELS,
            variables=(VariableSpec(VARIABLE_ID, VARIABLE_LONG_NAME, VARIABLE_UNITS),),
        ),
        spatial=SpatialSpec(crop=CROP),
        native_cadence_minutes=NATIVE_CADENCE_MINUTES,
        formats=FORMATS,
    )

    def validate(self, request: DataRequest) -> None:
        super().validate(request)
        if request.interval_minutes != NATIVE_CADENCE_MINUTES:
            raise ConstraintError(
                f"interval_minutes must be {NATIVE_CADENCE_MINUTES} for {ID}"
            )

    def list_samples(
        self,
        path: Path,
        *,
        catalog: Path | None = None,
        offset: int = 0,
        limit: int = 100,
        day: str | None = None,
    ) -> dict:
        import h5py

        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError("sample pagination requires offset>=0 and 1<=limit<=500")
        events = {}
        for row in _catalog_rows(catalog):
            event_id = row["id"]
            if event_id in events:
                raise ValueError("duplicate VIL event in catalog")
            events[event_id] = row

        # SEVIR pages by HDF5 row, without a day filter.
        with h5py.File(path, "r") as handle:
            total = len(handle["event_id"])
            ids = handle["event_id"][offset : offset + limit]
            starts = handle["start_time_utc"][offset : offset + limit]
            counts = (
                int(handle.attrs["input_frames"]),
                int(handle.attrs["output_frames"]),
            )
        if counts != (CONTEXT_FRAMES, TARGET_FRAMES):
            raise ValueError("unsupported processed SEVIR frame counts")

        samples = []
        grids = {}
        for index, (raw_id, raw_start) in enumerate(zip(ids, starts), start=offset):
            event_id = _text(raw_id)
            start = _parse_utc(_text(raw_start))
            item = {
                "sample_id": f"{event_id}@{_stamp(start)}",
                "event_id": event_id,
                "row": index,
                "context_start_time": _stamp(start),
                "init_time": _stamp(start + CADENCE * (CONTEXT_FRAMES - 1)),
                "lead_minutes": _lead_minutes(),
                "ready": False,
            }
            try:
                if event_id not in grids:
                    if event_id not in events:
                        raise ValueError("event missing from catalog")
                    grid = _grid_from_row(events[event_id])
                    x, y = grid["x"], grid["y"]
                    grids[event_id] = {
                        "crs": grid["crs"],
                        "axis_units": grid["axis_units"],
                        "shape": list(GRID_SHAPE),
                        "extent": [
                            float(value) for value in (x[0], y[0], x[-1], y[-1])
                        ],
                        "resolution": float(x[1] - x[0]),
                        "source": grid["source"],
                    }
                item.update(grid=grids[event_id], ready=True)
            except ValueError as exc:
                item["reason"] = str(exc)
            samples.append(item)
        return {
            "data_id": ID,
            "total": total,
            "offset": offset,
            "limit": limit,
            "samples": samples,
        }

    def check_window(
        self, path: Path, event_id: str, start: str, *, catalog: Path | None = None
    ) -> dict:
        import h5py

        wanted = _hdf5_time(start)
        with h5py.File(path, "r") as handle:
            if len(_window_rows(handle, wanted, event_id)) != 1:
                raise FileNotFoundError(f"SEVIR window not found: {event_id} {wanted}")
        _read_grid(catalog, event_id)
        return {
            "event_id": event_id,
            "context_start_time": _stamp(_parse_utc(start)),
            "ready": True,
        }

    def read_window(
        self,
        path: Path,
        event_id: str,
        start: str,
        *,
        catalog: Path | None = None,
        center_lon: float | None = None,
        center_lat: float | None = None,
    ) -> tuple[dict, dict]:
        if not event_id:
            raise ValueError("SEVIR requires an explicit event_id")
        window = _read_sample(path, start, event_id)
        return window, _read_grid(catalog, event_id)

    def load(self, request: DataRequest) -> DataBundle:
        self.validate(request)
        config = loader_config(request)
        path = Path(config["data_dir"]) / H5_NAME
        if not path.is_file():
            raise FileNotFoundError(f"SEVIR file does not exist: {path}")

        sample = _read_sample(path, request.initial_time)
        start = _parse_utc(request.initial_time)
        data = {}
        source_files = {}
        times = []
        for index in range(CONTEXT_FRAMES):
            stamp = start + CADENCE * index
            key = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            times.append(key)
            data[key] = {VARIABLE_ID: sample["context"][index]}
            source_files[key] = {VARIABLE_ID: [str(path)]}

        caps = self.capabilities()
        return bundle_from_parts(
            caps.id,
            request,
            data,
            source_files,
            {
                "loader_module": "adapters.sevir.source",
                "version": caps.version,
                "n_times": len(data),
                "n_channel_fields": len(data),
                "row": sample["row"],
                "event_id": sample["event_id"],
                "event_type": sample["event_type"],
                "target": sample["target"],
                "target_lead_minutes": _lead_minutes(),
                "init_time": times[-1],
                "context_times": times,
            },
        )


def _text(value) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    return str(value)


def _parse_utc(value: str) -> dt.datetime:
    out = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if out.tzinfo is None:
        out = out.replace(tzinfo=dt.timezone.utc)
    return out.astimezone(dt.timezone.utc)


def _stamp(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _lead_minutes() -> list[int]:
    return [NATIVE_CADENCE_MINUTES * (index + 1) for index in range(TARGET_FRAMES)]


def _hdf5_time(value: str) -> str:
    return _parse_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _match_row(start_times, wanted: str) -> int:
    matches = [
        index for index, value in enumerate(start_times) if _text(value) == wanted
    ]
    if len(matches) > 1:
        raise ConstraintError(
            "ambiguous SEVIR time; use read_window with event_id and start"
        )
    if matches:
        return matches[0]
    raise FileNotFoundError(f"SEVIR sample not found for start time {wanted}")


def _window_rows(handle, wanted: str, event_id: str) -> list[int]:
    if not event_id:
        raise ValueError("SEVIR requires an explicit event_id")
    return [
        index
        for index, (start, event) in enumerate(
            zip(handle["start_time_utc"][:], handle["event_id"][:])
        )
        if _text(start) == wanted and _text(event) == event_id
    ]


def _read_sample(path: Path, start: str, event_id: str | None = None) -> dict:
    import h5py
    import numpy as np

    wanted = _hdf5_time(start)
    with h5py.File(path, "r") as handle:
        if event_id is None:
            row = _match_row(handle["start_time_utc"][:], wanted)
        else:
            rows = _window_rows(handle, wanted, event_id)
            if not rows:
                raise FileNotFoundError(f"SEVIR window not found: {event_id} {wanted}")
            if len(rows) != 1:
                raise ValueError(
                    "ambiguous SEVIR window; select event_id and start_time"
                )
            row = rows[0]

        context = np.asarray(handle["inputs"][row], dtype=np.float32) * RAW_MAX
        target = np.asarray(handle["targets"][row], dtype=np.float32) * RAW_MAX
        sample = {
            "context": context,
            "target": target,
            "event_id": _text(handle["event_id"][row]),
            "row": row,
            "event_type": _text(handle["event_type"][row]),
        }
        for name, frames, count in (
            ("context", context, CONTEXT_FRAMES),
            ("target", target, TARGET_FRAMES),
        ):
            expected = (count, *GRID_SHAPE)
            if frames.shape != expected:
                if event_id is None:
                    raise ConstraintError(
                        f"SEVIR {name} shape {frames.shape} is not {expected}"
                    )
                raise ValueError(
                    "SEVIR requires six context and twenty target 128x128 frames"
                )

        if event_id is None:
            # Existing load files need not carry runtime frame metadata.
            return sample
        event_time = _parse_utc(_text(handle["time_utc"][row]))
        offsets = np.asarray(handle["frame_minutes"][row], dtype=int)
        frame_indices = np.asarray(handle["frame_idx"][row], dtype=int)

    frame_shape = (CONTEXT_FRAMES + TARGET_FRAMES,)
    if offsets.shape != frame_shape or frame_indices.shape != frame_shape:
        raise ValueError("incomplete SEVIR frame metadata")
    valid_times = [event_time + dt.timedelta(minutes=int(value)) for value in offsets]
    if valid_times[0] != _parse_utc(start) or any(
        b - a != CADENCE for a, b in zip(valid_times, valid_times[1:])
    ):
        raise ValueError(
            "SEVIR frame metadata does not match the selected time/cadence"
        )
    if not np.isfinite(context).all() or not np.isfinite(target).all():
        raise ValueError("SEVIR input has undeclared non-finite values")

    times = [_stamp(value) for value in valid_times]
    return {
        **sample,
        "data_id": ID,
        "cadence_minutes": NATIVE_CADENCE_MINUTES,
        "variable": VARIABLE_ID,
        "unit": VARIABLE_UNITS,
        "context_times": times[:CONTEXT_FRAMES],
        "target_times": times[CONTEXT_FRAMES:],
        "context_start_time": times[0],
        "init_time": times[CONTEXT_FRAMES - 1],
        "lead_minutes": _lead_minutes(),
        "sample_id": f"{event_id}@{times[0]}",
    }


def _catalog_rows(catalog: Path | None):
    if catalog is None:
        raise ValueError("SEVIR requires a catalog")
    with Path(catalog).open(newline="", encoding="utf-8-sig") as stream:
        yield from (row for row in csv.DictReader(stream) if row["img_type"] == "vil")


def _read_grid(catalog: Path | None, event_id: str) -> dict:
    matches = [row for row in _catalog_rows(catalog) if row["id"] == event_id]
    if len(matches) != 1:
        raise ValueError("CATALOG must identify exactly one VIL event")
    return _grid_from_row(matches[0])


def _grid_from_row(row: dict) -> dict:
    import numpy as np
    from pyproj import CRS, Transformer

    # Processed bilinear samples cannot reconstruct a sentinel-contaminated mask.
    if float(row["pct_missing"]) != 0:
        raise ValueError(
            "processed SEVIR event has missing pixels; mask-aware preprocessing required"
        )
    crs = CRS.from_user_input(row["proj"])
    if not crs.is_projected:
        raise ValueError("SEVIR requires an explicit projected CRS")
    transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x0, y0 = transform.transform(float(row["llcrnrlon"]), float(row["llcrnrlat"]))
    x1, y1 = transform.transform(float(row["urcrnrlon"]), float(row["urcrnrlat"]))
    width, height = float(row["width_m"]), float(row["height_m"])
    if not np.allclose([x1 - x0, y1 - y0], [width, height], atol=1, rtol=0):
        raise ValueError("CATALOG corners and projected dimensions disagree")
    native_x, native_y = int(row["size_x"]), int(row["size_y"])
    if (native_y, native_x) != (384, 384):
        raise ValueError("processed SEVIR grid requires a 384x384 source")
    ny, nx = GRID_SHAPE
    # Upstream pixel zero is the lower-left reference for align_corners=False.
    x = x0 + ((np.arange(nx) + 0.5) * native_x / nx - 0.5) * width / native_x
    y = y0 + ((np.arange(ny) + 0.5) * native_y / ny - 0.5) * height / native_y
    return {
        "crs": crs.to_wkt(),
        "x": x,
        "y": y,
        "axis_units": "m",
        "source": "CATALOG.csv + align_corners=False pixel mapping",
        "native_shape": [native_y, native_x],
        "missing_policy": "reject_pct_missing_nonzero",
    }


ADAPTER = Source()
