"""Offline regression: six-dataset batch helper reuses original NiB dispatcher."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pytest

from scripts.download_mlcast_sample import select_contiguous_times
from scripts import mlcast_download_six as batch


def test_auto_time_selects_nearest_complete_run():
    base = np.datetime64("2023-07-01T00:00:00", "ns")
    # Gap after the fifth frame, then four more frames.
    mins = np.array([0, 5, 10, 15, 20, 40, 45, 50, 55], dtype="int64")
    times = base + mins * np.timedelta64(1, "m")
    preferred = dt.datetime(2023, 7, 1, 0, 35, tzinfo=dt.timezone.utc)
    selected = select_contiguous_times(times, 5, 3, preferred)
    assert [str(t.astype("datetime64[m]")) for t in selected] == [
        "2023-07-01T00:10", "2023-07-01T00:15", "2023-07-01T00:20"]
    assert np.all(np.diff(selected) == np.timedelta64(5, "m"))


def test_auto_time_handles_missing_anchor_and_single_frame():
    base = np.datetime64("2023-07-01T00:00", "ns")
    stamps = base + np.arange(5) * np.timedelta64(10, "m")
    preferred = dt.datetime(2023, 6, 1, tzinfo=dt.timezone.utc)
    out = select_contiguous_times(stamps, 10, 3, preferred)
    np.testing.assert_array_equal(out, stamps[:3])
    out_one = select_contiguous_times(stamps, 10, 1, preferred)
    np.testing.assert_array_equal(out_one, stamps[:1])
    with pytest.raises(ValueError, match="No 10 observations available"):
        select_contiguous_times(stamps, 10, 10, preferred)


def test_auto_time_rejects_nonmonotonic_and_no_continuous_window():
    base = np.datetime64("2023-07-01T00:00", "ns")
    preferred = dt.datetime(2023, 7, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(ValueError, match="strictly increasing"):
        select_contiguous_times(base + np.array([0, 5, 5])*np.timedelta64(1,"m"), 5, 2, preferred)
    with pytest.raises(ValueError, match="No 3 consecutive"):
        select_contiguous_times(base + np.array([0, 10, 20, 30])*np.timedelta64(1,"m"), 5, 3, preferred)


def test_six_registry_already_present_without_dispatcher_changes():
    from adapters.__main__ import load_catalog, adapter_for
    catalog = json.loads((batch.Path(__file__).parents[2] / "adapters" / "mlcast_sources.json").read_text())
    ids = {d["id"] for d in load_catalog()["data_adapters"]}
    assert len(batch.OTHER_SIX) == 6
    for dataset_id in batch.OTHER_SIX:
        info = catalog[dataset_id]
        assert info["source"] in ids
        assert adapter_for(info["source"]).capabilities().channels.variables[0].id == info["variable"]
    assert "it_dpc_sri_5min" not in batch.OTHER_SIX


def test_six_batch_continues_on_one_failure_and_uses_separate_folders(tmp_path, monkeypatch):
    seen_downloads, seen_requests = [], []
    requested = ("radklim_hourly", "dmi_10_minutes", "fr_mf_prate_5min")

    def fake_download(args):
        dataset_id = args[args.index("--dataset")+1]
        seen_downloads.append((dataset_id, list(args)))
        if dataset_id == "dmi_10_minutes":
            raise IOError("simulated temporary remote failure")
        folder = tmp_path / "mlcast" / dataset_id
        (folder / "sample.zarr").mkdir(parents=True)
        (folder / "request.yaml").write_text("source: x", encoding="utf-8")

    def fake_request(root, dataset_id, **kwargs):
        seen_requests.append(dataset_id)
        path = tmp_path / "mlcast" / dataset_id / "request_custom.yaml"
        path.write_text("source: x", encoding="utf-8")
        return path

    monkeypatch.setattr(batch, "download_one", fake_download)
    monkeypatch.setattr(batch, "make_request", fake_request)
    report = batch.prepare_six(str(tmp_path), requested, verify=False, size=32, download_steps=8, history_steps=3)
    assert [dataset_id for dataset_id, args in seen_downloads] == list(requested)
    assert all("--auto-time" in args for _, args in seen_downloads)
    assert seen_requests == ["radklim_hourly", "fr_mf_prate_5min"]
    assert report["dmi_10_minutes"]["status"] == "failed"
    assert report["fr_mf_prate_5min"]["status"] == "prepared"
    assert report["radklim_hourly"]["status"] == "prepared"
    saved = json.loads((tmp_path / "mlcast" / "download_report.json").read_text(encoding="utf-8"))
    assert len(saved) == 3
    assert len({saved[x]["zarr"] for x in requested}) == 3
