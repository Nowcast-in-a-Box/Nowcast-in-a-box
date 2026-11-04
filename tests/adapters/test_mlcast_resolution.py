"""Check MLCast native grid spacing without inventing metric resolution."""
import numpy as np
import pytest
import xarray as xr
from unittest.mock import patch

from adapters.__main__ import adapter_for
from adapters.mlcast_common import native_grid_spacing
from interfaces.data import DataRequest
from scripts.mlcast_inspect import print_mlcast_qa
from tests.adapters.test_mlcast_local import fake_dataset


def test_it_projected_grid_metric_resolution_and_qa(tmp_path, capsys):
    (tmp_path / 'sample.zarr').mkdir()
    dataset = fake_dataset('RR', 5)
    dataset.x.attrs['units'] = 'm'
    dataset.y.attrs['units'] = 'm'
    req = DataRequest(
        source='mlcast_it_dpc_sri_5min', data_dir=str(tmp_path), data_root=None,
        initial_time='2023-07-01T12:00:00Z', history_steps=3,
        interval_minutes=5, channels=('RR',), bbox=(7.3, 49.4, 8.8, 50.8),
    )
    with patch('adapters.mlcast_common.xr.open_zarr', return_value=dataset):
        bundle = adapter_for(req.source).load(req)
    assert bundle['metadata']['spatial_resolution_m'] == {'x': 1000.0, 'y': 1000.0}
    assert bundle['metadata']['grid_spacing']['x']['units'] == 'm'
    assert bundle['metadata']['grid_spacing']['y']['step'] == 1000.0
    assert bundle['data']['2023-07-01T12:00:00Z']['RR'].shape[0] < 7
    print_mlcast_qa(bundle)
    report = capsys.readouterr().out
    assert 'native x/y step : dx=1000 m, dy=1000 m' in report
    assert 'spatial resolution: 1 km x 1 km' in report


def test_native_spacing_respects_units_and_axis_order():
    field = xr.DataArray(
        np.ones((3, 4)), dims=('y', 'x'),
        coords={'y': ('y', [2.0, 1.5, 1.0], {'units': 'km'}),
                'x': ('x', [0.0, 0.5, 1.0, 1.5], {'units': 'km'})})
    spacing, metric = native_grid_spacing(field)
    assert spacing['x']['step'] == pytest.approx(0.5)
    assert metric == {'x': 500.0, 'y': 500.0}


def test_degree_grid_does_not_get_mislabeled_as_kilometres():
    field = xr.DataArray(np.ones((3, 4)), dims=('lat', 'lon'),
        coords={'lat': ('lat', [41, 42, 43], {'units': 'degrees_north'}),
                'lon': ('lon', [10, 11, 12, 13], {'units': 'degrees_east'})})
    spacing, metric = native_grid_spacing(field)
    assert spacing['x']['units'] == 'degrees_east'
    assert metric is None


def test_irregular_and_missing_units_are_not_assumed_metric():
    irregular = xr.DataArray(np.ones((3, 4)), dims=('y', 'x'),
        coords={'y': ('y', [0, 1000, 2000], {'units': 'm'}),
                'x': ('x', [0, 1000, 2100, 3000], {'units': 'm'})})
    assert native_grid_spacing(irregular) == (None, None)
    no_units = irregular.assign_coords(x=('x', [0, 1000, 2000, 3000]))
    no_units.coords['x'].attrs.clear()
    spacing, metric = native_grid_spacing(no_units)
    assert spacing['x']['units'] == 'unknown'
    assert metric is None


def test_qa_old_bundle_infers_grid_from_frame(capsys):
    arr = xr.DataArray(np.ones((2, 3)), dims=('y', 'x'),
        coords={'y': ('y', [0, 1000], {'units': 'm'}),
                'x': ('x', [0, 1000, 2000], {'units': 'm'})},
        attrs={'units': 'kg m-2 h-1'})
    key = '2023-07-01T12:00:00Z'
    bundle = {'source': 'mlcast_it_dpc_sri_5min',
              'request': {'data_dir': 'mlcast/it_dpc_sri_5min', 'region': {'bbox': [10, 40, 11, 41]}},
              'data': {key: {'RR': arr}}, 'source_files': {key: {'RR': ['missing.zarr']}},
              'metadata': {}}
    print_mlcast_qa(bundle)
    assert 'spatial resolution: 1 km x 1 km' in capsys.readouterr().out


def _belgian_style_field(crs_wkt):
    """Project x/y are metre-spaced but coordinate attrs omit their units."""
    return xr.DataArray(
        np.ones((3, 4)), dims=('y', 'x'),
        coords={
            'x': ('x', [100000.0, 101000.0, 102000.0, 103000.0]),
            'y': ('y', [200000.0, 201000.0, 202000.0]),
            'spatial_ref': xr.DataArray(0, attrs={'crs_wkt': crs_wkt}),
        },
        attrs={'units': 'kg m-2 h-1'},
    )


def test_belgium_projected_crs_supplies_missing_metric_units(capsys):
    from pyproj import CRS
    frame = _belgian_style_field(CRS.from_epsg(31370).to_wkt())
    spacing, resolution_m = native_grid_spacing(frame)
    assert spacing['x']['step'] == 1000.0
    assert spacing['x']['units'] == 'm'
    assert spacing['y']['units'] == 'm'
    assert spacing['x']['unit_source'] == 'projected_crs'
    assert resolution_m == {'x': 1000.0, 'y': 1000.0}
    assert frame.coords['x'].attrs == {}  # no ungrounded modification of source metadata
    key = '2021-07-14T15:55:00Z'
    bundle = {
        'source': 'mlcast_be_rmi_radclim_mfb_5min',
        'request': {'data_dir': 'mlcast/be_rmi_radclim_mfb_5min',
                    'region': {'bbox': [3, 49, 6, 52]}},
        'data': {key: {'rain_rate': frame}},
        'source_files': {key: {'rain_rate': ['missing.zarr']}},
        'metadata': {'grid_spacing': spacing, 'spatial_resolution_m': resolution_m},
    }
    print_mlcast_qa(bundle)
    report = capsys.readouterr().out
    assert 'native x/y step : dx=1000 m, dy=1000 m' in report
    assert 'grid units source: projected CRS' in report
    assert 'spatial resolution: 1 km x 1 km' in report


def test_crs_geographic_degrees_not_reported_as_metres():
    from pyproj import CRS
    frame = _belgian_style_field(CRS.from_epsg(4326).to_wkt())
    spacing, resolution_m = native_grid_spacing(frame)
    assert spacing['x']['units'] == 'unknown'
    assert resolution_m is None


def test_missing_or_invalid_crs_never_assumes_metres():
    frame = _belgian_style_field('garbled CRS string')
    spacing, resolution_m = native_grid_spacing(frame)
    assert spacing['x']['units'] == 'unknown'
    assert resolution_m is None


def test_explicit_geographic_units_not_overridden_by_projected_crs():
    from pyproj import CRS
    frame = _belgian_style_field(CRS.from_epsg(31370).to_wkt())
    frame.coords['x'].attrs['units'] = 'degrees_east'
    spacing, resolution_m = native_grid_spacing(frame)
    assert spacing['x']['units'] == 'degrees_east'
    assert resolution_m is None


def test_belgium_crs_resolution_stored_in_real_databundle_path(tmp_path):
    from pyproj import CRS
    sample = tmp_path / 'sample.zarr'
    sample.mkdir()
    ds = fake_dataset('rain_rate', 5).assign_coords(
        spatial_ref=xr.DataArray(0, attrs={'spatial_ref': CRS.from_epsg(31370).to_wkt()}))
    req = DataRequest(
        source='mlcast_be_rmi_radclim_mfb_5min', data_dir=str(sample), data_root=None,
        initial_time='2023-07-01T12:00:00Z', history_steps=3,
        interval_minutes=5, channels=('rain_rate',), bbox=(7.3, 49.4, 8.8, 50.8))
    with patch('adapters.mlcast_common.xr.open_zarr', return_value=ds):
        bundle = adapter_for(req.source).load(req)
    spacing = bundle['metadata']['grid_spacing']
    assert spacing['x']['unit_source'] == 'projected_crs'
    assert bundle['metadata']['spatial_resolution_m'] == {'x': 1000.0, 'y': 1000.0}
    assert len(bundle['data']) == 3
