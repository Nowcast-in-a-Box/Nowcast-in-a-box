"""Regression checks: MLCast is registered via the existing NiB dispatcher.

No new central routing logic; legacy entries and interfaces remain intact.
"""
import importlib
import importlib.util
import json
from pathlib import Path

from adapters.__main__ import adapter_for, load_catalog
from interfaces.data import DataRequest, DataBundle, DeclaredDataAdapter

LEGACY_IDS = ("cma_radar", "gk2a", "himawari9", "fy4b", "mtg", "sevir")
MLCAST_IDS = (
    "mlcast_radklim_hourly", "mlcast_radklim_5_minutes",
    "mlcast_dmi_10_minutes", "mlcast_it_dpc_sri_5min",
    "mlcast_uk_metoffice_5min", "mlcast_be_rmi_radclim_mfb_5min",
    "mlcast_fr_mf_prate_5min",
)


def test_legacy_sources_still_selected_by_original_adapter_for():
    catalog = load_catalog()
    ids = [record["id"] for record in catalog["data_adapters"]]
    assert ids[:len(LEGACY_IDS)] == list(LEGACY_IDS)
    for source in LEGACY_IDS:
        # MTG has an optional native HDF5 decompression dependency; this
        # architecture test must not force installation of its runtime.
        if source == "mtg" and importlib.util.find_spec("hdf5plugin") is None:
            continue
        assert adapter_for(source) is importlib.import_module("adapters.{}.source".format(source)).ADAPTER


def test_all_mlcast_sources_use_existing_catalog_and_abc():
    records = {record["id"]: record for record in load_catalog()["data_adapters"]}
    assert all(source in records for source in MLCAST_IDS)
    for source in MLCAST_IDS:
        obj = adapter_for(source)
        assert obj is importlib.import_module("adapters.{}.source".format(source)).ADAPTER
        assert isinstance(obj, DeclaredDataAdapter)
        assert obj.capabilities().id == source
        assert records[source]["path"] == source


def test_mlcast_uses_unmodified_shared_request_and_bundle_classes():
    from adapters.mlcast_common import MLCastZarrSource
    assert issubclass(MLCastZarrSource, DeclaredDataAdapter)
    assert issubclass(DataBundle, dict)
    assert "data_root" in DataRequest.__dataclass_fields__
    assert "initial_time" in DataRequest.__dataclass_fields__
    assert "bbox" in DataRequest.__dataclass_fields__
