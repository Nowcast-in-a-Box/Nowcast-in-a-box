import csv

import h5py
import pytest

from ingestion.samples import list_samples


def test_sample_listing_uses_metadata_only_and_reports_readiness(tmp_path):
    path = tmp_path / "metadata-only.h5"
    with h5py.File(path, "w") as handle:
        handle.attrs["input_frames"] = 6
        handle.attrs["output_frames"] = 20
        handle.create_dataset("event_id", data=[b"one", b"two"])
        handle.create_dataset("start_time_utc", data=[b"2017-06-27 00:35:00"] * 2)
    catalog = tmp_path / "CATALOG.csv"
    with catalog.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["id", "img_type", "pct_missing"])
        writer.writeheader()
        writer.writerow({"id": "one", "img_type": "vil", "pct_missing": 1})
    result = list_samples(path, catalog, limit=1)
    assert result["total"] == 2
    sample = result["samples"][0]
    assert sample["init_time"] == "2017-06-27T01:00:00Z"
    assert not sample["ready"]
    assert "mask-aware" in sample["reason"]
    second = list_samples(path, catalog, offset=1, limit=1)["samples"][0]
    assert second["event_id"] == "two"
    assert "event missing" in second["reason"]
    with pytest.raises(ValueError, match="pagination"):
        list_samples(path, catalog, limit=501)
