# Author: sonderlau
# Last modified: 2026-10-07
# Modified by: sonderlau
"""SEVIR VIL source.

The processed file stores normalized VIL in [0, 1]. This source restores
raw VIL [0, 255] and returns the six context frames. It does not select
the five frames a model consumes.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from interfaces.data import (
    ChannelSpec,
    DataBundle,
    DataCapabilities,
    DataRequest,
    DeclaredDataAdapter,
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
RAW_MAX = 255.0


class Source(DeclaredDataAdapter):
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

    def load(self, request: DataRequest) -> DataBundle:
        self.validate(request)
        config = loader_config(request)
        path = Path(config["data_dir"]) / H5_NAME
        if not path.is_file():
            raise FileNotFoundError(f"SEVIR file does not exist: {path}")

        import h5py
        import numpy as np

        wanted = _hdf5_time(request.initial_time)
        with h5py.File(path, "r") as handle:
            start_times = handle["start_time_utc"][:]
            row = _match_row(start_times, wanted)
            context = np.asarray(handle["inputs"][row], dtype=np.float32) * RAW_MAX
            target = np.asarray(handle["targets"][row], dtype=np.float32) * RAW_MAX
            event_id = _text(handle["event_id"][row])
            event_type = _text(handle["event_type"][row])

        if context.shape != (CONTEXT_FRAMES, 128, 128):
            raise ConstraintError(
                f"SEVIR context shape {context.shape} is not (6, 128, 128)"
            )
        if target.shape != (TARGET_FRAMES, 128, 128):
            raise ConstraintError(
                f"SEVIR target shape {target.shape} is not (20, 128, 128)"
            )

        start = _parse_utc(request.initial_time)
        data = {}
        source_files = {}
        times = []
        for index in range(CONTEXT_FRAMES):
            stamp = start + dt.timedelta(minutes=NATIVE_CADENCE_MINUTES * index)
            key = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
            times.append(key)
            data[key] = {VARIABLE_ID: context[index]}
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
                "row": row,
                "event_id": event_id,
                "event_type": event_type,
                "target": target,
                "target_lead_minutes": [
                    NATIVE_CADENCE_MINUTES * (CONTEXT_FRAMES + index)
                    for index in range(TARGET_FRAMES)
                ],
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
    return out.astimezone(dt.timezone.utc).replace(tzinfo=None)


def _hdf5_time(value: str) -> str:
    return _parse_utc(value).strftime("%Y-%m-%d %H:%M:%S")


def _match_row(start_times, wanted: str) -> int:
    for index, value in enumerate(start_times):
        if _text(value) == wanted:
            return index
    raise FileNotFoundError(f"SEVIR sample not found for start time {wanted}")


ADAPTER = Source()
