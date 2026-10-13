from datetime import datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pytest

from ingestion.sevir import read_grid, read_window
from utils.stored_fields import (
    StoredFields,
    FieldUnavailable,
    create_run,
    add_forecast,
)

UTC = timezone.utc
START = datetime(2017, 6, 27, 0, 35, tzinfo=UTC)


def fixture_window():
    times = [
        (START + timedelta(minutes=i * 5)).isoformat().replace("+00:00", "Z")
        for i in range(26)
    ]
    return {
        "context": np.zeros((6, 4, 4), np.float32),
        "target": np.ones((20, 4, 4), np.float32),
        "context_times": times[:6],
        "target_times": times[6:],
        "context_start_time": times[0],
        "init_time": times[5],
        "sample_id": "event@" + times[0],
        "event_id": "event",
        "row": 0,
    }


@pytest.fixture
def run(tmp_path):
    grid = {
        "x": np.arange(4) * 3000.0,
        "y": np.arange(4) * 3000.0,
        "crs": "EPSG:3857",
        "axis_units": "m",
    }
    directory = create_run(
        tmp_path, "run-1", fixture_window(), grid, atlas_revision="unpublished"
    )
    return directory


def test_cached_twenty_leads_and_no_overwrite(run):
    values = np.ones((20, 4, 4), np.float32)
    values[0, 0, 0] = np.nan
    add_forecast(run, "wadepre", "0.1.0", "a" * 64, values, list(range(5, 101, 5)))
    access = StoredFields(run)
    init = START + timedelta(minutes=25)
    for lead in access.list_predictions("wadepre", init):
        field = access.get_prediction("wadepre", init, lead)
        assert field["valid_time"] == init + timedelta(minutes=lead)
        assert field["unit"] == "raw"
    assert access.file_reads == 1
    assert access.get_prediction("wadepre", init, 5)["mask"][0, 0]
    with pytest.raises(FileExistsError):
        add_forecast(run, "wadepre", "0.1.0", "a" * 64, values, list(range(5, 101, 5)))
    with pytest.raises(FieldUnavailable):
        access.get_prediction("wadepre", init, 7)
    with pytest.raises(FieldUnavailable):
        access.get_prediction("wadepre", init + timedelta(minutes=5), 5)


def test_tampered_cube_and_path_rejected(run):
    manifest = json.loads((run / "manifest.json").read_text())
    descriptor = manifest["observations"]["target"]
    (run / descriptor["path"]).write_bytes(b"tampered")
    with pytest.raises(ValueError, match="checksum"):
        StoredFields(run).cube(descriptor)
    descriptor["path"] = "../outside.npz"
    with pytest.raises(ValueError, match="contained"):
        StoredFields(run).cube(descriptor)


def test_unique_run_and_time_alignment(run):
    with pytest.raises(FileExistsError):
        create_run(
            run.parent, run.name, fixture_window(), {}, atlas_revision="unpublished"
        )
    with pytest.raises(ValueError, match="increase strictly"):
        add_forecast(run, "wadepre", "0.1.0", "a" * 64, np.zeros((2, 4, 4)), [5, 0])


def test_legacy_reader_rejects_ambiguous_time():
    from adapters.sevir.source import _match_row
    from interfaces.errors import ConstraintError

    with pytest.raises(ConstraintError, match="ambiguous"):
        _match_row([b"2017-06-27 00:35:00"] * 2, "2017-06-27 00:35:00")


def test_ambiguous_sevir_and_exact_event(tmp_path):
    import h5py

    path = tmp_path / "samples.h5"
    with h5py.File(path, "w") as handle:
        for key, values in {
            "start_time_utc": [b"2017-06-27 00:35:00"] * 2,
            "time_utc": [b"2017-06-27 00:35:00"] * 2,
            "event_id": [b"one", b"two"],
            "event_type": [b"test"] * 2,
            "frame_minutes": np.tile(np.arange(26) * 5, (2, 1)),
            "frame_idx": np.tile(np.arange(26), (2, 1)),
            "inputs": np.zeros((2, 6, 128, 128)),
            "targets": np.ones((2, 20, 128, 128)),
        }.items():
            handle.create_dataset(key, data=values)
    with pytest.raises(ValueError, match="ambiguous"):
        read_window(path, "2017-06-27T00:35:00Z")
    result = read_window(path, "2017-06-27T00:35:00Z", "two")
    assert result["event_id"] == "two"
    assert result["init_time"] == "2017-06-27T01:00:00Z"
    assert result["target_times"][0] == "2017-06-27T01:05:00Z"
    assert result["lead_minutes"] == list(range(5, 101, 5))


def test_catalog_grid_and_missing_rejection(tmp_path):
    import csv
    from pyproj import Transformer

    crs = "+proj=laea +lat_0=38 +lon_0=-98 +units=m +a=6370997 +ellps=sphere"
    transform = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    lon0, lat0 = transform.transform(-684000, -328000)
    lon1, lat1 = transform.transform(-300000, 56000)
    row = {
        "id": "event",
        "img_type": "vil",
        "pct_missing": 0,
        "proj": crs,
        "llcrnrlon": lon0,
        "llcrnrlat": lat0,
        "urcrnrlon": lon1,
        "urcrnrlat": lat1,
        "width_m": 384000,
        "height_m": 384000,
        "size_x": 384,
        "size_y": 384,
    }
    path = tmp_path / "CATALOG.csv"

    def write():
        with path.open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=row)
            writer.writeheader()
            writer.writerow(row)

    write()
    grid = read_grid(path, "event")
    assert grid["x"][0] == pytest.approx(-683000)
    assert grid["y"][0] == pytest.approx(-327000)
    assert np.allclose(np.diff(grid["x"]), 3000)
    row["pct_missing"] = 1
    write()
    with pytest.raises(ValueError, match="mask-aware"):
        read_grid(path, "event")
