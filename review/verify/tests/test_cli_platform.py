"""The command line against a platform that already has packages and runs.

Where ``test_cli.py`` covers the verification commands, this file covers the
platform-facing ones: reading the host's settings, finding packages and runs,
planning, and managing the roster. Everything runs off a miniature platform tree
built in ``tmp_path``.
"""

from __future__ import annotations

from datetime import date

import pytest

from nib_verify.cli import EXIT_FAILURE, EXIT_OK, main
from nib_verify.config import dump, load
from nib_verify.store import Store

from .conftest import MODEL_MANIFEST, make_config, write_json, write_yaml

EXPOSED_YAML = """review:
  host: 0.0.0.0
  port: 8766
"""

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


def _packages(platform) -> list[str]:
    out: list[str] = []
    for root in platform.packages:
        out += ["--packages", str(root)]
    return out


# ======================================================================
# the platform's own settings
# ======================================================================
def test_host_show_reads_the_platforms_config(platform, capsys):
    assert main(["host", "show", "--host-config", str(platform.config_path)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "127.0.0.1:8765" in out
    assert "127.0.0.1:8766" in out
    assert str(platform.data_root.resolve()) in out


def test_host_show_is_honest_when_there_is_no_data_root(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("NIB_DATA_ROOT", raising=False)
    bare = tmp_path / "bare.yaml"
    bare.write_text("handler:\n  port: 8765\n", encoding="utf-8")
    assert main(["host", "show", "--host-config", str(bare)]) == EXIT_OK
    assert "(not set)" in capsys.readouterr().out


def test_host_show_as_json(platform, capsys):
    import json

    assert main(["host", "show", "--host-config", str(platform.config_path),
                 "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["review"]["port"] == 8766
    assert payload["paths"]["data_root"].endswith("data")


# ======================================================================
# catalog
# ======================================================================
def test_catalog_packages_lists_what_was_found(platform, capsys):
    assert main(["catalog", "packages", *_packages(platform)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "radar-composite" in out
    assert "precip-nowcast.v1" in out
    assert "mlcast-det" in out
    assert "48x64" in out


def test_catalog_packages_complains_about_an_empty_root(tmp_path, capsys):
    code = main(["catalog", "packages", "--packages", str(tmp_path)])
    assert code == EXIT_FAILURE
    assert "no package manifests found" in capsys.readouterr().err


def test_catalog_runs_lists_the_runs(platform, capsys):
    assert main(["catalog", "runs", "--runs", str(platform.runs_root)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "2 run(s)" in out
    assert "2026-01-01T00:00:00Z" in out
    assert "horizon=6" in out


def test_catalog_check_passes_on_a_healthy_tree(platform, capsys):
    code = main(["catalog", "check", *_packages(platform)])
    assert code == EXIT_OK
    assert "no problems" in capsys.readouterr().out


def test_catalog_match_reports_a_matching_pair(platform, capsys):
    assert main(["catalog", "match", *_packages(platform)]) == EXIT_OK
    assert "ok" in capsys.readouterr().out


def test_catalog_match_is_nonzero_when_a_pair_fails(platform, capsys):
    model_path = platform.packages[1] / "deep" / "down" / "model" / "manifest.json"
    write_json(model_path, {**MODEL_MANIFEST, "requires": "something-else.v1"})
    code = main(["catalog", "match", *_packages(platform)])
    assert code == EXIT_FAILURE
    assert "FAIL" in capsys.readouterr().out


def test_a_missing_scan_root_is_named(tmp_path, capsys, monkeypatch):
    """`AGENTS.md`: never invent a host path — and say which flag would fix it."""
    monkeypatch.delenv("NIB_DATA_ROOT", raising=False)
    monkeypatch.delenv("NIB_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    code = main(["catalog", "runs"])
    captured = capsys.readouterr()
    assert code == EXIT_FAILURE
    assert "nowhere to look for runs" in captured.err
    assert "--runs DIR" in captured.err


# ======================================================================
# planning
# ======================================================================
def test_task_plan_uses_the_discovered_runs(config_file, platform, capsys):
    code = main(["task", "plan", "--config", str(config_file),
                 "--runs", str(platform.runs_root), *MOCK])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "tasks    6" in out
    assert "leads    [10, 20, 30]" in out


def test_task_plan_says_what_it_rejected_and_exits_nonzero(config_file, platform, capsys):
    """Quietly reporting zero tasks would look like success."""
    cfg = load(config_file)
    dump(cfg.with_member(cfg.member("mlcast-det"), retire_on=date(2025, 12, 31)),
         config_file)

    code = main(["task", "plan", "--config", str(config_file),
                 "--runs", str(platform.runs_root)])
    out = capsys.readouterr().out
    assert code == EXIT_FAILURE
    assert "inactive" in out


def test_task_plan_verbose_explains_each_rejection(config_file, platform, capsys):
    cfg = load(config_file)
    dump(cfg.with_member(cfg.member("mlcast-det"), retire_on=date(2025, 12, 31)),
         config_file)
    main(["task", "plan", "--config", str(config_file),
          "--runs", str(platform.runs_root), "--verbose"])
    out = capsys.readouterr().out
    assert "the member was not on the roster on that date" in out
    assert "is active" in out


def test_task_plan_can_probe_the_provider(config_file, platform, capsys):
    code = main(["task", "plan", "--config", str(config_file),
                 "--runs", str(platform.runs_root), *MOCK, "--probe"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "coverage" in out
    assert "available: 6" in out


def test_task_plan_as_json_is_machine_readable(config_file, platform, capsys):
    import json

    code = main(["task", "plan", "--config", str(config_file),
                 "--runs", str(platform.runs_root), "--json"])
    assert code == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["tasks"] == 6
    assert payload["span"]["from"] == "2026-01-01T00:00:00Z"
    assert len(payload["tasks_detail"]) == 6


def test_the_plan_alias_works(config_file, platform, capsys):
    assert main(["plan", "--config", str(config_file),
                 "--runs", str(platform.runs_root)]) == EXIT_OK
    assert "tasks    6" in capsys.readouterr().out


def test_the_plan_lists_leads_outside_the_horizon(config_file, platform, capsys):
    from nib_verify.catalog.runs import discover as discover_runs
    from tests.conftest import run_config_text, write_yaml

    short = platform.root / "short" / "j"
    write_yaml(short / "run.yaml", run_config_text(horizon=2))

    main(["task", "plan", "--config", str(config_file), "--runs", str(short),
          "--verbose"])
    out = capsys.readouterr().out
    assert "tasks    2" in out
    assert "left out" in out


# ======================================================================
# running from the discovered runs
# ======================================================================
def test_task_run_takes_its_init_times_from_the_runs(config_file, platform, capsys):
    root = str(platform.root / "verify-root")
    code = main(["run", "--config", str(config_file), "--root", root,
                 "--runs", str(platform.runs_root), *MOCK])
    assert code == EXIT_OK
    assert "6 total, 6 ran" in capsys.readouterr().out


def test_running_the_same_runs_twice_reads_nothing(config_file, platform, capsys):
    root = str(platform.root / "verify-root")
    args = ["run", "--config", str(config_file), "--root", root,
            "--runs", str(platform.runs_root), *MOCK]
    assert main(args) == EXIT_OK
    capsys.readouterr()
    assert main(args) == EXIT_OK
    assert "0 ran, 6 skipped" in capsys.readouterr().out


# ======================================================================
# the rest of the config commands
# ======================================================================
def test_config_create_writes_and_refuses_to_overwrite(config_file, tmp_path, capsys):
    out = tmp_path / "new.json"
    assert main(["config", "create", str(out), "--from", str(config_file)]) == EXIT_OK
    assert out.is_file()
    capsys.readouterr()

    assert main(["config", "create", str(out), "--from", str(config_file)]) == EXIT_FAILURE
    assert "exists" in capsys.readouterr().err

    assert main(["config", "create", str(out), "--from", str(config_file),
                 "--force"]) == EXIT_OK


def test_config_list_shows_the_ruler_fingerprint(config_file, capsys):
    assert main(["config", "list", str(config_file.parent)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "vil-test" in out
    assert "ruler" in out
    assert make_config().ruler.fingerprint in out


def test_config_roster_add_then_retire(config_file, capsys):
    assert main(["config", "roster", "add", str(config_file),
                 "--member", "simvp", "--leads", "10,20"]) == EXIT_OK
    assert "added simvp" in capsys.readouterr().out
    assert load(config_file).member("simvp").leads == (10, 20)

    assert main(["config", "roster", "retire", str(config_file),
                 "--member", "simvp", "--on", "2026-06-01"]) == EXIT_OK
    assert load(config_file).member("simvp").active_to == date(2026, 6, 1)


def test_config_roster_refuses_a_member_that_is_already_there(config_file, capsys):
    code = main(["config", "roster", "add", str(config_file),
                 "--member", "mlcast-det", "--leads", "10"])
    assert code == EXIT_FAILURE
    assert "already on the roster" in capsys.readouterr().err


def test_config_roster_needs_leads(config_file, capsys):
    code = main(["config", "roster", "add", str(config_file), "--member", "x"])
    assert code == EXIT_FAILURE
    assert "--leads is required" in capsys.readouterr().err


def test_config_roster_add_can_carry_an_active_window(config_file, capsys):
    main(["config", "roster", "add", str(config_file), "--member", "simvp",
          "--leads", "10", "--active-from", "2026-02-01"])
    assert load(config_file).member("simvp").active_from == date(2026, 2, 1)


def test_config_delete_removes_only_that_configs_rows(config_file, platform, capsys):
    root = platform.root / "verify-root"
    main(["run", "--config", str(config_file), "--root", str(root),
          "--runs", str(platform.runs_root), *MOCK])
    capsys.readouterr()

    group = load(config_file).group_id
    with Store(root / "verify.sqlite") as store:
        before = store.count_rows("cat", filters={"group_id": group})
    assert before > 0

    assert main(["config", "delete", str(config_file), "--root", str(root)]) == EXIT_OK
    assert "removed stored rows" in capsys.readouterr().out

    with Store(root / "verify.sqlite") as store:
        assert store.count_rows("cat", filters={"group_id": group}) == 0
        assert store.task_counts(group) == {}


def test_config_delete_keeps_other_configs(config_file, platform, capsys):
    """Deleting one config must not take the other's numbers with it."""
    from dataclasses import replace

    cfg = load(config_file)
    other = replace(cfg, group_id="a-second-config", name="second")
    other_path = config_file.parent / "other.json"
    dump(other, other_path)

    root = platform.root / "verify-root"
    main(["run", "--config", str(config_file), "--root", str(root), *MOCK,
          "--init", "2026-01-01T00:00Z"])
    main(["run", "--config", str(other_path), "--root", str(root), *MOCK,
          "--init", "2026-01-01T00:00Z"])
    capsys.readouterr()

    with Store(root / "verify.sqlite") as store:
        assert store.count_rows("cat", filters={"group_id": cfg.group_id}) > 0

    main(["config", "delete", str(config_file), "--root", str(root)])

    with Store(root / "verify.sqlite") as store:
        assert store.count_rows("cat", filters={"group_id": cfg.group_id}) == 0
        assert store.count_rows("cat", filters={"group_id": "a-second-config"}) > 0


def test_config_delete_on_a_missing_file(config_file, tmp_path, capsys):
    assert main(["config", "delete", str(tmp_path / "nope.json"),
                 "--root", str(tmp_path)]) == EXIT_FAILURE
    assert "does not exist" in capsys.readouterr().err


# ======================================================================
# serve
# ======================================================================
def _serve_args(platform, tmp_path):
    return [
        "serve", "--root", str(tmp_path / "verify-root"),
        "--configs", str(tmp_path / "configs"),
        "--host-config", str(platform.config_path),
        *MOCK,
        *[x for r in platform.packages for x in ("--packages", str(r))],
        "--runs", str(platform.runs_root),
    ]


def test_serve_takes_its_bind_address_from_config_yaml(platform, tmp_path, monkeypatch, capsys):
    """AGENTS.md puts the review page on loopback, at a port config.yaml owns."""
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port, log_level: seen.update(host=host, port=port))

    code = main(_serve_args(platform, tmp_path))
    assert code == EXIT_OK, capsys.readouterr().err
    assert seen == {"host": "127.0.0.1", "port": 8766}
    assert "127.0.0.1:8766" in capsys.readouterr().out


def test_serve_refuses_a_non_loopback_address(platform, tmp_path, monkeypatch, capsys):
    """Obeying config.yaml off loopback is the one thing not to do quietly."""
    import uvicorn

    started = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(True))

    exposed = platform.root / "exposed.yaml"
    write_yaml(exposed, EXPOSED_YAML)

    args = _serve_args(platform, tmp_path)
    args[args.index("--host-config") + 1] = str(exposed)
    code = main(args)

    assert code == EXIT_FAILURE
    assert started == []
    assert "not loopback" in capsys.readouterr().err


def test_serve_can_be_told_to_expose_itself_anyway(platform, tmp_path, monkeypatch, capsys):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port, log_level: seen.update(host=host, port=port))

    exposed = platform.root / "exposed.yaml"
    write_yaml(exposed, EXPOSED_YAML)

    args = _serve_args(platform, tmp_path)
    args[args.index("--host-config") + 1] = str(exposed)
    args.append("--allow-remote")
    assert main(args) == EXIT_OK
    assert seen["host"] == "0.0.0.0"


def test_serve_can_override_the_port(platform, tmp_path, monkeypatch, capsys):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port, log_level: seen.update(port=port))
    assert main(_serve_args(platform, tmp_path) + ["--port", "9000"]) == EXIT_OK
    assert seen["port"] == 9000


def test_serve_starts_even_when_the_provider_is_unavailable(platform, tmp_path, monkeypatch, capsys):
    """The page should come up and say so, rather than refusing to start."""
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: None)

    args = _serve_args(platform, tmp_path)
    args[args.index("--access") + 1] = "real"          # deliberately not implemented
    assert main(args) == EXIT_OK
    assert "unavailable" in capsys.readouterr().out
