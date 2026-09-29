"""The demonstration, and the one thing that has to be true about it.

The workspace is fabricated, so the tests about it are not "does it look right"
— they are "is it *internally consistent*". A demo whose fabricated platform
contradicts itself does not fail loudly; it fails by planning nothing, or by
rejecting every pair in the Matcher, in front of an audience.

That is not hypothetical. The first version had the model manifest declaring six
output frames while the data package expected three, so both pairs failed the
Matcher and the plan came out empty. Nothing raised. These tests exist because of
it: every pair must pass, every run config must agree with the manifests, and the
reading at the end must not be a perfect score.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from nib_verify import demo as demo_module
from nib_verify.catalog import discover_packages, discover_runs
from nib_verify.catalog.matching import check_pair
from nib_verify.cli import EXIT_FAILURE, EXIT_OK, main
from nib_verify.config.schema import load

QUIET = ["--progress", "quiet"]


@pytest.fixture
def built(tmp_path):
    return demo_module.build(tmp_path / "nib-demo", say=lambda _line: None)


# ======================================================================
# the workspace is consistent with itself
# ======================================================================
def test_every_pair_the_demo_puts_together_passes_the_matcher(built):
    """The one that matters. A fabricated platform that contradicts itself does
    not raise — it plans nothing, on stage."""
    catalog = discover_packages(built.packages)
    assert len(catalog.data_packages) == 1
    assert len(catalog.model_packages) == 2

    for data in catalog.data_packages:
        for model in catalog.model_packages:
            result = check_pair(data, model)
            assert result.ok, f"{data.id} x {model.id}: {result.errors}"


def test_every_run_config_names_a_model_the_catalog_has(built):
    catalog = discover_packages(built.packages)
    book = discover_runs([built.runs_root])
    assert set(book.model_ids) <= set(catalog.model_ids)
    assert book.data_ids == {demo_module.DATA_ID}


def test_the_run_horizon_reaches_the_longest_lead_the_config_asks_for(built):
    """Or the planner clips the last lead and the chart has three points."""
    book = discover_runs([built.runs_root])
    cfg = load(built.config_file)
    longest = max(max(member.leads) for member in cfg.members)
    for run in book.runs:
        leads = run.leads_within_horizon(cfg.ruler.time_step_minutes)
        assert longest in leads, f"{run.path} covers {leads}, needed {longest}"


def test_the_demo_config_can_be_loaded_and_is_a_complete_ruler(built):
    cfg = load(built.config_file)
    assert cfg.group_id == demo_module.CONFIG_ID
    assert cfg.ruler.unit == demo_module.UNIT
    assert cfg.ruler.variable == demo_module.VARIABLE
    assert cfg.ruler.time_step_minutes == demo_module.CADENCE_MINUTES
    assert {m.name for m in cfg.members} == set(demo_module.MODELS)
    # every family, so the review page has something in each tab of the picker
    assert {g.family for g in cfg.metrics} == {
        "categorical", "continuous", "moments", "spatial"
    }


def test_the_demo_has_more_than_one_model_and_more_than_one_threshold(built):
    """A single model or a single threshold would make the whole exercise look
    like a pass/fail test, which is the opposite of the point."""
    cfg = load(built.config_file)
    assert len(cfg.members) >= 2
    assert len(demo_module.LEVELS) >= 3
    assert len(demo_module.LEADS) >= 3


# ======================================================================
# the platform it fabricates is discoverable
# ======================================================================
def test_the_manifests_are_found_wherever_they_were_put(built):
    """Three unrelated directories, found by content. A demo that only worked on
    a tidy catalog tree would be a demo of the tree."""
    catalog = discover_packages(built.packages)
    assert len(catalog) == 3
    assert not catalog.problems()
    assert {p.id for p in catalog.data_packages} == {demo_module.DATA_ID}
    assert set(catalog.model_ids) == set(demo_module.MODELS)


def test_the_run_configs_are_read_as_run_configs(built):
    book = discover_runs([built.runs_root])
    assert len(book) == len(demo_module.MODELS) * demo_module.INIT_TIMES
    assert not book.skipped


def test_the_host_config_is_the_one_the_module_reads(built):
    """`review.port` comes from this file, which is why `serve` binds what it
    binds and not whatever a command line felt like."""
    from nib_verify.catalog.hostconfig import load as load_host

    host = load_host(built.config_path)
    assert host.review_address == ("127.0.0.1", built.review_port)
    assert host.data_root is not None and host.data_root.is_dir()


def test_the_workspace_says_it_is_fabricated(built):
    """Somebody will point this at something and wonder why the numbers are
    wrong. The tree says so, at the top, in the first line."""
    readme = (built.root / "README.md").read_text(encoding="utf-8")
    assert "fabricated" in readme.lower()
    assert "nothing in it is a claim" in readme.lower()
    assert "inputs/data" in readme


def test_the_data_root_is_empty_because_nothing_here_is_a_data_file(built):
    """The fields come from the service, over HTTP. A demo that read arrays off
    disk would be demonstrating a different module."""
    contents = [p.name for p in (built.root / "inputs" / "data").iterdir()]
    assert contents in ([], [".keep"])


# ======================================================================
# building it twice
# ======================================================================
def test_building_over_a_non_empty_directory_is_refused(tmp_path):
    demo_module.build(tmp_path / "nib-demo", say=lambda _l: None)
    with pytest.raises(FileExistsError, match="--force"):
        demo_module.build(tmp_path / "nib-demo", say=lambda _l: None)


def test_force_rebuilds(tmp_path):
    first = demo_module.build(tmp_path / "nib-demo", say=lambda _l: None)
    (first.config_file).unlink()
    again = demo_module.build(tmp_path / "nib-demo", force=True, say=lambda _l: None)
    assert again.config_file.is_file()


def test_an_unknown_scenario_is_refused_before_anything_is_written(tmp_path):
    """Half a workspace and then an error leaves the next attempt needing
    --force for no reason."""
    root = tmp_path / "nib-demo"
    with pytest.raises(ValueError, match="unknown scenario"):
        demo_module.build(root, scenario="sunny", say=lambda _l: None)
    assert not root.exists()


def test_the_scenario_reaches_the_generator(tmp_path):
    for name in ("simple", "convective", "patchy"):
        demo = demo_module.build(tmp_path / name, scenario=name,
                                 say=lambda _l: None)
        assert demo.scenario == name


# ======================================================================
# the survey
# ======================================================================
def test_the_survey_reports_what_it_found(built, capsys):
    lines: list[str] = []
    demo_module.survey(built, say=lines.append)
    text = "\n".join(lines)
    assert "read by content" in text
    assert demo_module.DATA_ID in text
    for model in demo_module.MODELS:
        assert model in text
    assert "2 of 2 pass the Matcher" in text


def test_the_headline_is_not_a_perfect_score(tmp_path, capsys):
    """With the bare generator every CSI is 1.0 and a broken display looks
    right. The scenario is what makes the demonstration worth giving."""
    demo = demo_module.build(tmp_path / "nib-demo", say=lambda _l: None)
    from nib_verify.access import build_access
    from nib_verify.access.mockfeed import serve_in_thread

    with serve_in_thread(build_access("mock", scenario=demo.scenario)) as url:
        demo_module.verify(demo, access=build_access("mock", scenario=demo.scenario),
                           data_url=url, say=lambda _l: None)

    rows = demo_module.headline(demo, say=lambda _l: None)
    values = [v for row in rows for v in row["values"].values() if v is not None]
    assert len(values) >= 6
    assert any(v < 0.95 for v in values), "every score is perfect; the demo shows nothing"
    assert any(v > 0.0 for v in values)


# ======================================================================
# the command
# ======================================================================
def test_the_demo_command_runs_end_to_end_and_stops(tmp_path, capsys):
    """``--no-serve`` is the whole flow minus the page, so a machine without a
    browser — or a CI runner — can still prove the chain works."""
    code = main(["demo", "--where", str(tmp_path / "nib-demo"), "--no-serve", *QUIET])
    assert code == EXIT_OK, capsys.readouterr().err
    assert (tmp_path / "nib-demo" / ".store" / "verify.sqlite").is_file()


def test_the_demo_command_says_what_it_did(tmp_path, capsys):
    assert main(["demo", "--where", str(tmp_path / "nib-demo"), "--no-serve"]) == EXIT_OK
    out = capsys.readouterr().out
    for stage in ("1/6", "2/6", "3/6", "4/6", "5/6", "6/6"):
        assert stage in out, stage
    assert "curl" in out, "the audience should be able to see it is a real service"
    assert "not in process" in out


def test_the_demo_command_refuses_a_bad_scenario(tmp_path, capsys):
    code = main(["demo", "--where", str(tmp_path / "nib-demo"),
                 "--scenario", "sunny", *QUIET])
    assert code == EXIT_FAILURE
    assert "unknown scenario" in capsys.readouterr().err


def test_the_demo_command_refuses_to_clobber(tmp_path, capsys):
    (tmp_path / "nib-demo").mkdir()
    (tmp_path / "nib-demo" / "something").write_text("x", encoding="utf-8")
    code = main(["demo", "--where", str(tmp_path / "nib-demo"), "--no-serve", *QUIET])
    assert code == EXIT_FAILURE
    assert "--force" in capsys.readouterr().err


def test_the_demo_command_is_silent_when_asked(tmp_path, capsys):
    main(["demo", "--where", str(tmp_path / "nib-demo"), "--no-serve", *QUIET])
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_the_demo_writes_where_it_was_told(tmp_path):
    main(["demo", "--where", str(tmp_path / "elsewhere"), "--no-serve", *QUIET])
    assert (tmp_path / "elsewhere" / "configs" / f"{demo_module.CONFIG_ID}.json").is_file()


# ======================================================================
# what it built, as data
# ======================================================================
def test_the_demo_describes_itself(built):
    """So a demo can be scripted, or turned into a report, without parsing the
    printout."""
    described = built.as_dict()
    assert described["scenario"] == "convective"
    assert described["review"].startswith("http://127.0.0.1:")
    assert described["packages"]
    assert described["runs"].endswith("jobs")
