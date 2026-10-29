"""The shared MLCast inspector must expose full metadata without pixel dumps."""
import numpy as np
import xarray as xr
from scripts.mlcast_inspect import print_mlcast_all_attrs


def test_inspector_full_attrs(capsys):
    arr = xr.DataArray(
        np.array([[2.0, 3.0]], dtype='float32'), dims=('y', 'x'),
        coords={'y': ('y', [0.0], {'units': 'm'}),
                'x': ('x', [1000.0, 2000.0], {'units': 'm'})},
        attrs={'units': 'kg m-2 h-1', 'long_name': 'rain rate'},
    )
    bundle = {
        'source': 'mlcast_fr_mf_prate_5min',
        'request': {'data_dir': 'mlcast/fr_mf_prate_5min'},
        'source_files': {'2022-07-01T12:00:00Z': {'prate': ['demo_data/sample.zarr']}},
        'metadata': {'spatial_resolution_m': {'x': 1000.0, 'y': 1000.0}},
        'data': {'2022-07-01T12:00:00Z': {'prate': arr}},
    }
    print_mlcast_all_attrs(bundle)
    output = capsys.readouterr().out
    assert 'DataBundle ALL ATTRIBUTES' in output
    assert 'spatial_resolution_m' in output
    assert '2022-07-01T12:00:00Z' in output
    assert 'long_name' in output
    assert 'coordinate x' in output
    assert 'kg m-2 h-1' in output
    assert 'array([[2.' not in output
