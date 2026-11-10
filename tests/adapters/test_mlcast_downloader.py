"""Offline smoke test for Windows-style direct script invocation and publication."""
import datetime as dt
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np
import pytest
import xarray as xr
import yaml


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / 'scripts' / 'download_mlcast_sample.py'


def test_downloader_local_publication(tmp_path, monkeypatch):
    # Run through file rather than importing the scripts namespace package, so
    # this also tests bootstrap when the project root is absent from sys.path.
    spec = importlib.util.spec_from_file_location('download_mlcast_sample', SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    fake_fs = types.SimpleNamespace(S3FileSystem=lambda **_kw: types.SimpleNamespace(get_mapper=lambda key: key))
    monkeypatch.setitem(sys.modules, 's3fs', fake_fs)
    initial = np.datetime64('2023-07-01T12:00:00', 'ns')
    steps = np.arange(5)[::-1] * np.timedelta64(5, 'm')
    times = initial - steps
    ds = xr.Dataset({
        'RR': (('time','y','x'), np.ones((5, 16, 16)), {'units':'mm/h'})
    }, coords={
        'time': times, 'y': np.arange(16), 'x': np.arange(16),
        'lat': ('y', np.linspace(35, 43, 16)),
        'lon': ('x', np.linspace(7, 15, 16)),
    })
    monkeypatch.setattr(mod.xr, 'open_zarr', lambda *a, **kw: ds)

    def fake_to_zarr(self, store, *args, **kwargs):
        store = Path(store)
        store.mkdir(parents=True)
        (store / '.zmetadata').write_text('{}')
    monkeypatch.setattr(xr.Dataset, 'to_zarr', fake_to_zarr)
    # CI runner may use Python > 3.12, but the downloader targets 3.8-3.12.
    original_version = sys.version_info
    monkeypatch.setattr(sys, 'version_info', (3, 12, 0, 'final', 0))
    monkeypatch.setattr(sys, 'argv', ['download_mlcast_sample.py', '--dataset',
       'it_dpc_sri_5min', '--history-steps', '5', '--download-steps', '5', '--size', '8',
       '--output-root', str(tmp_path)])
    try:
        mod.main()
        sample = tmp_path / 'mlcast' / 'it_dpc_sri_5min' / 'sample.zarr'
        req_path = sample.parent / 'request.yaml'
        assert sample.is_dir() and req_path.is_file()
        req = yaml.safe_load(req_path.read_text())
        assert req['history_steps'] == 5
        assert req['channels'] == ['RR']
        assert req['region']['bbox'][0] < req['region']['bbox'][2]
        with pytest.raises(SystemExit, match='Already downloaded'):
            mod.main()
    finally:
        monkeypatch.setattr(sys, 'version_info', original_version)
