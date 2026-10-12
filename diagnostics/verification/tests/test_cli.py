"""The command line, exercised the way a handler would call it."""

from __future__ import annotations

import json

import pytest

from nib_verify.cli import EXIT_FAILURE, EXIT_OK, EXIT_USAGE, main

from .conftest import make_config

MOCK = [
    "--access", "mock",
    "--mock", "peak=250",
    "--mock", "unit=kg m-2",
    "--mock", "variable=vil",
    "--mock", "obs_shape=48,64",
    "--mock", "model_shape=40,52",
]


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(make_config().to_json(), encoding="utf-8")
    return path


# ----------------------------------------------------------------------
def test_metrics_list_is_readable(capsys):
    assert main(["metrics", "list"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ts" in out
    assert "half_window" in out


def test_metrics_list_as_json(capsys):
    assert main(["metrics", "list", "--json"]) == EXIT_OK
    rows = json.loads(capsys.readouterr().out)
    assert {r["method"] for r in rows} >= {"ts", "rmse", "fss"}
    assert "csi" not in {r["method"] for r in rows}, "csi is an alias, not a second metric"


def test_config_show_prints_the_ruler(config_file, capsys, tmp_path):
    assert main(["config", "show", str(config_file)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "vil-test" in out
    assert "radar-composite" in out
    assert "fingerprint" in out
    assert str(tmp_path) not in out


def test_config_show_as_json_round_trips(config_file, capsys):
    assert main(["config", "show", str(config_file), "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert make_config().to_dict() == payload


def test_validate_accepts_a_matching_provider(config_file, capsys):
    assert main(["config", "validate", str(config_file), *MOCK]) == EXIT_OK
    assert "ok" in capsys.readouterr().out


def test_validate_rejects_a_unit_mismatch(config_file, capsys):
    code = main(["config", "validate", str(config_file), "--access", "mock"])
    assert code == EXIT_FAILURE
    assert "kg m-2" in capsys.readouterr().err


def test_validate_rejects_a_cadence_mismatch(config_file, capsys):
    code = main(["config", "validate", str(config_file), *MOCK, "--mock", "cadence_minutes=5"])
    assert code == EXIT_FAILURE
    assert "10 min" in capsys.readouterr().err


def test_unknown_mock_option_is_refused(config_file, capsys):
    code = main(["config", "validate", str(config_file), *MOCK, "--mock", "nonsense=1"])
    assert code == EXIT_FAILURE
    assert "unknown --mock option" in capsys.readouterr().err


def test_missing_config_file_is_reported(capsys):
    assert main(["config", "show", "nope.json"]) == EXIT_FAILURE
    assert "error" in capsys.readouterr().err


# ----------------------------------------------------------------------
def test_run_with_no_init_and_no_runs_does_nothing_happily(config_file, tmp_path, capsys):
    """"There was nothing to do" is a success, not a failure."""
    code = main(["run", "--config", str(config_file), "--root", str(tmp_path / "r"), *MOCK])
    assert code == EXIT_OK
    assert "nothing to do" in capsys.readouterr().out


def test_run_then_query_end_to_end(config_file, tmp_path, capsys):
    root = str(tmp_path / "root")
    init = ["--init", "2026-01-01T00:00Z", "--init", "2026-01-01T01:00Z"]

    assert main(["run", "--config", str(config_file), "--root", root, *MOCK, *init]) == EXIT_OK
    out = capsys.readouterr().out
    assert "6 total, 6 ran" in out

    assert main(["query", "--method", "ts", "--config", str(config_file), "--root", root,
                 "-s", "level=181,219", "-g", "lead_minutes", "-g", "level"]) == EXIT_OK
    lines = [l for l in capsys.readouterr().out.splitlines() if l and not l.startswith("#")]
    assert lines[0] == "lead_minutes\tlevel\tvalue\tn_valid"
    assert len(lines) == 7, "header plus 3 leads x 2 levels"

    assert main(["query", "--method", "ts", "--config", str(config_file), "--root", root,
                 "-s", "level=181,219", "-g", "lead_minutes", "-g", "level",
                 "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["meta"]["group"]["ruler"]["fingerprint"]
    assert len(payload["records"]) == 6


def test_a_second_run_skips_everything(config_file, tmp_path, capsys):
    root = str(tmp_path / "root")
    init = ["--init", "2026-01-01T00:00Z"]
    assert main(["run", "--config", str(config_file), "--root", root, *MOCK, *init]) == EXIT_OK
    capsys.readouterr()
    assert main(["run", "--config", str(config_file), "--root", root, *MOCK, *init]) == EXIT_OK
    assert "0 ran, 3 skipped" in capsys.readouterr().out


def test_progress_quiet_is_truly_silent(config_file, tmp_path, capsys):
    """A timer-driven handler reads the exit code, not the output."""
    root = str(tmp_path / "root")
    code = main(["run", "--config", str(config_file), "--root", root, *MOCK,
                 "--init", "2026-01-01T00:00Z", "--progress", "quiet"])
    captured = capsys.readouterr()
    assert code == EXIT_OK
    assert captured.out == ""
    assert captured.err == ""


def test_progress_json_is_one_object_per_task(config_file, tmp_path, capsys):
    root = str(tmp_path / "root")
    main(["run", "--config", str(config_file), "--root", root, *MOCK,
          "--init", "2026-01-01T00:00Z", "--progress", "json"])
    lines = [l for l in capsys.readouterr().out.splitlines() if l.startswith("{")]
    assert len(lines) == 3
    assert all(json.loads(l)["event"] == "task" for l in lines)


def test_a_run_failure_exits_nonzero(config_file, tmp_path, capsys):
    """Wrong unit: every task fails, so the exit code must not be 0."""
    root = str(tmp_path / "root")
    code = main(["run", "--config", str(config_file), "--root", root,
                 "--access", "mock", "--init", "2026-01-01T00:00Z"])
    captured = capsys.readouterr()
    assert code == EXIT_FAILURE
    assert "3 failed" in captured.out
    assert "meaningless" in captured.err


def test_expansions_lists_the_available_dimensions(capsys):
    assert main(["expansions", "--method", "fss"]) == EXIT_OK
    dims = capsys.readouterr().out.split()
    assert "half_window" in dims
    assert "level" in dims


def test_unknown_metric_is_reported_with_alternatives(config_file, tmp_path, capsys):
    code = main(["query", "--method", "nope", "--root", str(tmp_path)])
    assert code == EXIT_FAILURE
    assert "ts" in capsys.readouterr().err


# ======================================================================
# chart
# ======================================================================
@pytest.fixture
def verified(config_file, tmp_path):
    """A store with one verified run in it, for the chart tests to draw."""
    root = tmp_path / "store"
    assert main(["task", "run", "--config", str(config_file), "--root", str(root),
                 "--init", "2026-01-01T00:00:00Z", *MOCK, "--progress", "quiet"]) == EXIT_OK
    return config_file, root


def test_chart_writes_svg_to_stdout(verified, capsys):
    """``> curve.svg`` has to work, or the command is useless in a script."""
    config, root = verified
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-s", "level=181", "-x", "lead_minutes", "--series", "member"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("<?xml")
    assert "<svg" in out and "</svg>" in out


def test_chart_writes_svg_to_a_file(verified, capsys, tmp_path):
    config, root = verified
    target = tmp_path / "curve.svg"
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-s", "level=181", "--out", str(target)])
    assert code == EXIT_OK
    captured = capsys.readouterr()
    assert captured.out == "", "nothing on stdout, so it can still be piped"
    assert target.read_text(encoding="utf-8").startswith("<?xml")
    assert "series" in captured.err            # the summary goes to stderr


def test_chart_is_silent_when_asked(verified, capsys, tmp_path):
    config, root = verified
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "--out", str(tmp_path / "c.svg"), "--progress", "quiet"])
    assert code == EXIT_OK
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_chart_draws_the_metric_that_was_asked_for(verified, capsys):
    config, root = verified
    assert main(["chart", "--method", "rmse", "--config", str(config),
                 "--root", str(root)]) == EXIT_OK
    assert "Root mean square error" in capsys.readouterr().out


def test_chart_says_no_samples_rather_than_failing(verified, capsys):
    """An empty chart is an answer. A missing file is not."""
    config, root = verified
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-s", "level=99999"])
    assert code == EXIT_OK
    assert "no samples" in capsys.readouterr().out


def test_chart_refuses_a_column_it_cannot_draw(verified, capsys):
    config, root = verified
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-x", "nonsense"])
    assert code == EXIT_FAILURE
    assert "cannot expand by" in capsys.readouterr().err


def test_chart_honours_a_pinned_axis(verified, capsys):
    config, root = verified
    code = main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-s", "level=181", "--y-min", "0", "--y-max", "1"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert ">1.0<" in out and ">0.0<" in out


def test_chart_expands_by_the_columns_it_was_given(verified, capsys):
    config, root = verified
    assert main(["chart", "--method", "ts", "--config", str(config), "--root", str(root),
                 "-s", "dtime=30", "-x", "level", "--series", "member"]) == EXIT_OK
    out = capsys.readouterr().out
    assert ">219<" in out, "the levels are the x axis"
    assert ">30<" not in out, "the filtered lead is not"
