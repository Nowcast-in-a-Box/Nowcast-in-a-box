"""Reading the platform: its settings, its packages, its runs, and its rules.

The theme throughout is that nothing here assumes a directory layout.
`AGENTS.md` forbids inventing one, so packages and runs are found by walking a
root and recognising files by what they contain. Rename every directory in these
tests and they still pass.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from nib_verify.catalog import (
    find_config_file,
    discover_packages,
    discover_runs,
    load_host,
    load_manifest,
    load_run_config,
)
from nib_verify.catalog.hostconfig import HostConfigError
from nib_verify.catalog.matching import check_pair
from nib_verify.catalog.packages import PackageManifest
from nib_verify.catalog.runs import RunConfig, RunConfigError

from .conftest import (
    DATA_MANIFEST,
    MODEL_MANIFEST,
    run_config_text,
    write_json,
    write_yaml,
)

UTC = timezone.utc


# ======================================================================
# host config
# ======================================================================
def test_host_config_is_found_by_walking_up(platform, monkeypatch, tmp_path):
    monkeypatch.delenv("NIB_CONFIG", raising=False)
    nested = platform.root / "workspace" / "jobs" / "a"
    monkeypatch.chdir(nested)
    host = load_host()
    assert host.source == platform.config_path.resolve()


def test_host_config_defaults_match_the_handler():
    """The handler's own defaults, so a missing config.yaml cannot drift."""
    from nib_verify.catalog.hostconfig import (
        DEFAULT_HANDLER_PORT,
        DEFAULT_REVIEW_PORT,
        HostConfig,
    )

    fresh = HostConfig()
    assert fresh.handler_port == DEFAULT_HANDLER_PORT == 8765
    assert fresh.review_port == DEFAULT_REVIEW_PORT == 8766
    assert fresh.review_address == ("127.0.0.1", 8766)
    assert fresh.data_root is None


def test_data_root_is_resolved_against_the_config_file(platform):
    host = load_host(platform.config_path)
    assert host.data_root == platform.data_root.resolve()
    assert host.artifacts == (platform.root / "artifacts").resolve()


def test_the_environment_wins_for_the_data_root(platform, monkeypatch, tmp_path):
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    monkeypatch.setenv("NIB_DATA_ROOT", str(elsewhere))
    assert load_host(platform.config_path).data_root == elsewhere.resolve()


def test_a_missing_data_root_is_a_refusal_not_a_default(platform):
    bare = platform.root / "bare.yaml"
    write_yaml(bare, "handler:\n  port: 8765\n")
    host = load_host(bare)
    assert host.data_root is None
    with pytest.raises(HostConfigError, match="does not guess"):
        host.require_data_root()


def test_a_data_root_that_does_not_exist_is_a_refusal(platform):
    gone = platform.root / "nowhere.yaml"
    write_yaml(gone, "paths:\n  data_root: not/here\n")
    host = load_host(gone)
    with pytest.raises(HostConfigError, match="does not exist"):
        host.require_data_root()


def test_a_bad_port_is_reported(platform):
    bad = platform.root / "bad.yaml"
    write_yaml(bad, "handler:\n  port: 99999\n")
    with pytest.raises(HostConfigError, match="out of range"):
        load_host(bad)


def test_nib_config_wins_over_the_walk(platform, monkeypatch):
    monkeypatch.setenv("NIB_CONFIG", str(platform.config_path))
    assert find_config_file("/") == platform.config_path


# ======================================================================
# package manifests
# ======================================================================
def test_packages_are_found_regardless_of_directory_names(platform):
    catalog = platform.catalog()
    assert catalog.data_ids == {"radar-composite"}
    assert catalog.model_ids == {"mlcast-det"}
    assert not catalog.problems()


def test_field_specs_are_read_through_several_spellings():
    raw = {
        "id": "x",
        "offers": ["p"],
        "fields": [{"variable": "vil", "units": "kg m-2", "grid": [10, 20],
                    "cadence": 5, "frames": 4}],
    }
    spec = PackageManifest.from_mapping(raw).fields[0]
    assert spec.name == "vil"
    assert spec.unit == "kg m-2"
    assert spec.shape == (10, 20)
    assert spec.cadence_minutes == 5
    assert spec.frame_count == 4


def test_unknown_manifest_keys_are_kept_not_rejected():
    raw = {"id": "x", "offers": ["p"], "something_new": {"a": 1},
           "fields": [{"name": "vil", "quantile": 0.9}]}
    manifest = PackageManifest.from_mapping(raw)
    assert manifest.raw["something_new"] == {"a": 1}
    assert manifest.fields[0].raw["quantile"] == 0.9


def test_a_package_with_neither_offers_nor_requires_is_reported(platform):
    write_json(platform.root / "stray" / "manifest.json",
               {"id": "mystery", "fields": [{"name": "something"}]})
    catalog = discover_packages(platform.packages + [platform.root / "stray"])
    assert "mystery" in {p.id for p in catalog}
    assert any("neither offers nor requires" in p for p in catalog.problems())


def test_a_json_file_that_is_not_a_manifest_is_ignored(tmp_path):
    """Content decides. A Python package's manifest.json is not a package here."""
    write_json(tmp_path / "somewhere" / "manifest.json", {"name": "mypkg", "version": "1.0"})
    catalog = discover_packages([tmp_path])
    assert len(catalog) == 0
    assert catalog.skipped == ()


def test_duplicate_package_ids_are_reported(tmp_path):
    for name in ("one", "two"):
        write_json(tmp_path / name / "manifest.json", DATA_MANIFEST)
    catalog = discover_packages([tmp_path])
    assert len(catalog) == 2
    assert any("duplicate package id" in p for p in catalog.problems())


def test_an_unreadable_manifest_is_skipped_not_fatal(tmp_path):
    write_json(tmp_path / "good" / "manifest.json", DATA_MANIFEST)
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "manifest.json").write_text("{not json", encoding="utf-8")
    catalog = discover_packages([tmp_path])
    assert len(catalog) == 1
    assert catalog.skipped and "bad" in catalog.skipped[0]


def test_a_manifest_without_an_id_is_refused(tmp_path):
    write_json(tmp_path / "manifest.json", {"offers": ["p"]})
    with pytest.raises(Exception, match="no id"):
        load_manifest(tmp_path / "manifest.json")


def test_a_package_that_is_neither_data_nor_model_is_still_readable():
    manifest = PackageManifest.from_mapping({"id": "x", "offers": ["p"], "requires": "p"})
    assert manifest.kind == "unknown"
    assert not manifest.is_data and not manifest.is_model


# ======================================================================
# run configs
# ======================================================================
def test_run_configs_are_found_by_content(platform):
    book = platform.runbook()
    assert len(book) == 2
    assert book.model_ids == {"mlcast-det"}
    assert book.data_ids == {"radar-composite"}
    first, last = book.span()
    assert first == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)
    assert last == datetime(2026, 1, 1, 1, 0, tzinfo=UTC)


def test_a_run_config_carries_the_run_directory(platform):
    book = platform.runbook()
    directories = {r.directory.name for r in book}
    assert directories == {"a", "b"}


def test_unknown_top_level_keys_are_refused(tmp_path):
    write_yaml(tmp_path / "run.yaml",
               run_config_text() + "extra:\n  whatever: 1\n")
    path = tmp_path / "run.yaml"
    with pytest.raises(RunConfigError, match="unknown top-level key"):
        load_run_config(path)


def test_unknown_section_keys_are_refused(tmp_path):
    write_yaml(tmp_path / "run.yaml",
               'version: 1\ndata:\n  id: d\n  typo: 1\nmodel:\n  id: m\n'
               'run:\n  start_time: "2026-01-01T00:00:00Z"\n')
    with pytest.raises(RunConfigError, match="unknown key"):
        load_run_config(tmp_path / "run.yaml")


def test_a_sample_id_may_replace_the_start_time(tmp_path):
    write_yaml(tmp_path / "run.yaml",
               'version: 1\ndata:\n  id: d\nmodel:\n  id: m\n'
               'run:\n  sample_id: "case-7"\n  forecast_horizon: 3\n')
    cfg = load_run_config(tmp_path / "run.yaml")
    assert cfg.start_time is None
    assert cfg.sample_id == "case-7"
    assert cfg.when == "sample case-7"


def test_a_run_with_neither_time_nor_sample_is_refused(tmp_path):
    write_yaml(tmp_path / "run.yaml",
               'version: 1\ndata:\n  id: d\nmodel:\n  id: m\nrun:\n  forecast_horizon: 3\n')
    with pytest.raises(RunConfigError, match="start_time or run.sample_id"):
        load_run_config(tmp_path / "run.yaml")


def test_a_bad_start_time_is_reported(tmp_path):
    write_yaml(tmp_path / "run.yaml",
               'version: 1\ndata:\n  id: d\nmodel:\n  id: m\n'
               'run:\n  start_time: "last tuesday"\n')
    with pytest.raises(RunConfigError, match="not ISO-8601"):
        load_run_config(tmp_path / "run.yaml")


def test_a_broken_run_config_is_listed_not_swallowed(tmp_path):
    write_yaml(tmp_path / "good" / "run.yaml", run_config_text())
    write_yaml(tmp_path / "bad" / "run.yaml",
               'version: 1\ndata:\n  id: d\nmodel:\n  id: m\nrun:\n  start_time: "nope"\n')
    book = discover_runs([tmp_path])
    assert len(book) == 1
    assert book.skipped and "not ISO-8601" in book.skipped[0]


def test_a_yaml_file_that_is_not_a_run_config_is_ignored(tmp_path):
    write_yaml(tmp_path / "notes.yaml", "hello: world\n")
    assert len(discover_runs([tmp_path])) == 0


def test_the_horizon_is_a_frame_count_and_becomes_leads_with_the_cadence():
    cfg = RunConfig.from_mapping({
        "version": 1, "data": {"id": "d"}, "model": {"id": "m"},
        "run": {"start_time": "2026-01-01T00:00:00Z", "forecast_horizon": 3},
    })
    assert cfg.leads_within_horizon(10) == [10, 20, 30]
    assert cfg.leads_within_horizon(5) == [5, 10, 15]
    assert cfg.leads_within_horizon(0) == []


def test_a_negative_horizon_is_refused():
    with pytest.raises(RunConfigError, match="must be positive"):
        RunConfig.from_mapping({
            "version": 1, "data": {"id": "d"}, "model": {"id": "m"},
            "run": {"start_time": "2026-01-01T00:00:00Z", "forecast_horizon": -1},
        })


# ======================================================================
# matching
# ======================================================================
def _data(**overrides):
    return PackageManifest.from_mapping({**DATA_MANIFEST, **overrides})


def _model(**overrides):
    return PackageManifest.from_mapping({**MODEL_MANIFEST, **overrides})


def test_a_matching_pair_passes(platform):
    catalog = platform.catalog()
    result = check_pair(catalog.get("radar-composite"), catalog.get("mlcast-det"))
    assert result.ok, result.errors
    assert result.warnings == []


def test_a_profile_that_is_not_offered_is_a_hard_error():
    result = check_pair(_data(offers=["something-else.v1"]), _model())
    assert not result.ok
    assert "requires 'precip-nowcast.v1'" in result.errors[0]


def test_a_cadence_mismatch_is_a_hard_error():
    model = _model(output=[{**MODEL_MANIFEST["output"][0], "cadence_minutes": 5}])
    result = check_pair(_data(), model)
    assert not result.ok
    assert any("cadence differs" in e for e in result.errors)


def test_a_shape_mismatch_is_a_hard_error():
    model = _model(input=[{**MODEL_MANIFEST["input"][0], "shape": [96, 128]}])
    result = check_pair(_data(), model)
    assert not result.ok
    assert any("spatial shape differs" in e for e in result.errors)


def test_needing_more_context_frames_than_exist_is_a_hard_error():
    model = _model(context_frames={"required_count": 9, "mode": "latest"})
    result = check_pair(_data(), model)
    assert not result.ok
    assert any("needs 9 context frames" in e for e in result.errors)


def test_an_output_length_that_misses_the_target_is_a_hard_error():
    model = _model(output=[{**MODEL_MANIFEST["output"][0], "frame_count": 2}])
    result = check_pair(_data(), model)
    assert not result.ok
    assert any("emits 2 frames" in e for e in result.errors)


def test_a_field_the_data_never_declares_is_a_hard_error():
    model = _model(
        input=[{"name": "rain_rate", "unit": "mm h-1", "shape": [48, 64]}],
        requires="precip-nowcast.v1",
    )
    result = check_pair(_data(), model)
    assert not result.ok
    assert any("rain_rate" in e for e in result.errors)


def test_a_dtype_difference_is_only_a_warning():
    model = _model(input=[{**MODEL_MANIFEST["input"][0], "dtype": "float16"}])
    result = check_pair(_data(), model)
    assert result.ok
    assert any("cast" in w for w in result.warnings)


def test_an_undeclared_value_is_a_warning_not_a_refusal():
    """An incomplete manifest must remain usable; the schema is not final."""
    data = PackageManifest.from_mapping({"id": "d", "offers": ["precip-nowcast.v1"]})
    model = PackageManifest.from_mapping({"id": "m", "requires": "precip-nowcast.v1"})
    result = check_pair(data, model)
    assert result.ok
    assert any("cadence not declared" in w for w in result.warnings)
    assert any("spatial shape not declared" in w for w in result.warnings)


def test_a_domain_difference_is_a_warning():
    result = check_pair(_data(region="east-china"), _model(region="north-china"))
    assert result.ok
    assert any("region differs" in w for w in result.warnings)


def test_matching_two_data_packages_refuses():
    result = check_pair(_data(), _data())
    assert not result.ok
    assert "is not a model package" in result.errors[0]
