"""The ``datafeed`` commands, and the ``real`` that is no longer a hole.

Two things are being pinned down here. The first is that the simulated data
interface is reachable from the command line at all. The second is the promise
that replaced ``--access real`` raising: it still cannot quietly verify against
generated numbers, it just fails at a different moment and for a different
reason — there is an address to supply, and this module will not invent one.
"""

from __future__ import annotations

import json

import pytest

from nib_verify.access.factory import NO_ADDRESS
from nib_verify.cli import EXIT_FAILURE, EXIT_OK, main

from .conftest import make_access, serve_data


# ======================================================================
# datafeed check
# ======================================================================
def test_check_conforms_for_the_generator(capsys):
    assert main(["datafeed", "check", "--access", "mock"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "conforms" in out
    assert "capabilities() reports a cadence" in out


def test_check_exercises_the_missing_data_checks_when_there_is_missing_data(capsys):
    """With nothing missing those checks have no witness, and the report says so
    rather than calling it a pass."""
    main(["datafeed", "check", "--access", "mock"])
    assert "[skip] missing points come back as NaN" in capsys.readouterr().out

    assert main(["datafeed", "check", "--access", "mock",
                 "--mock", "missing_points=99"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "[ok  ] missing points come back as NaN" in out
    assert "99 missing" in out


def test_check_can_say_it_in_json(capsys):
    assert main(["datafeed", "check", "--access", "mock", "--json"]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["capabilities"]["cadence_minutes"] == 10
    assert payload["checks"]


def test_check_fails_on_a_provider_that_does_not_conform(monkeypatch, capsys):
    """The exit code is the answer, so it has to be able to be non-zero."""
    from nib_verify.access import Capabilities

    class NoCadence:
        def capabilities(self):
            return Capabilities(cadence_minutes=0, observation_names=("a",),
                                model_ids=(), units={"a": "dBZ"})

    import nib_verify.cli as cli

    monkeypatch.setattr(cli, "_make_access", lambda args: NoCadence())
    assert main(["datafeed", "check", "--access", "mock"]) == EXIT_FAILURE
    out = capsys.readouterr().out
    assert "does NOT conform" in out
    assert "[FAIL] capabilities() reports a cadence" in out


def test_check_against_a_running_service_works(data_service, capsys):
    url, _ = data_service
    assert main(["datafeed", "check", "--url", url]) == EXIT_OK
    out = capsys.readouterr().out
    assert "conforms" in out
    assert f"source: {url}" in out


def test_check_says_where_it_looked_when_nothing_is_there(capsys):
    assert main(["datafeed", "check", "--url", "http://127.0.0.1:1"]) == EXIT_FAILURE
    out = capsys.readouterr().out
    assert "does NOT conform" in out
    assert "could not reach" in out


def test_check_reports_a_bad_address_as_unreachable_but_keeps_going(data_service, capsys):
    """``--url`` is optional and the address may be wrong; either way the command
    answers with a report rather than a traceback."""
    url, _ = data_service
    assert main(["datafeed", "check", "--url", url + "/nope"]) == EXIT_FAILURE
    assert "not a field access endpoint" in capsys.readouterr().out


# ======================================================================
# datafeed serve
# ======================================================================
def test_serve_binds_where_it_is_told(monkeypatch, capsys):
    import uvicorn

    seen = {}
    monkeypatch.setattr(uvicorn, "run",
                        lambda app, host, port, log_level: seen.update(host=host, port=port))

    assert main(["datafeed", "serve", "--access", "mock", "--port", "9100"]) == EXIT_OK
    assert seen == {"host": "127.0.0.1", "port": 9100}
    assert "serving" in capsys.readouterr().out


def test_serve_says_what_it_is_serving(monkeypatch, capsys):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: None)
    main(["datafeed", "serve", "--access", "mock", "--mock", "model_ids=a,b",
          "--mock", "observation_names=x"])
    out = capsys.readouterr().out
    assert "cadence=10min" in out
    assert "'x'" in out and "'a'" in out


def test_serve_can_be_quiet(monkeypatch, capsys):
    import uvicorn

    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: None)
    assert main(["datafeed", "serve", "--access", "mock",
                 "--progress", "quiet"]) == EXIT_OK
    assert capsys.readouterr().out == ""


# ======================================================================
# --access real is an address, not a hole
# ======================================================================
@pytest.mark.parametrize("command", [
    ["datafeed", "check"],
    ["datafeed", "serve"],
])
def test_real_without_an_address_is_refused(command, capsys):
    code = main([*command, "--access", "real"])
    assert code == EXIT_FAILURE, command
    assert "does not guess one" in capsys.readouterr().err


def test_a_verification_run_refuses_a_real_provider_with_no_address(
        tmp_path, platform, capsys):
    """Every command that can build a provider has to refuse identically, or the
    one that does not becomes the hole."""
    config = _write_config(tmp_path)
    code = main(["task", "run", "--config", str(config),
                 "--root", str(tmp_path / "store"),
                 "--runs", str(platform.runs_root), "--access", "real"])
    assert code == EXIT_FAILURE
    assert "does not guess one" in capsys.readouterr().err


def test_a_plan_refuses_a_real_provider_with_no_address_when_probing(
        tmp_path, platform, capsys):
    config = _write_config(tmp_path)
    code = main(["task", "plan", "--config", str(config),
                 "--runs", str(platform.runs_root), "--access", "real", "--probe"])
    assert code == EXIT_FAILURE
    assert "does not guess one" in capsys.readouterr().err


def _write_config(tmp_path):
    from .conftest import make_config
    from nib_verify.config.schema import dump

    path = tmp_path / "cfg.json"
    dump(make_config(), path)
    return path


def test_http_without_an_address_is_refused_the_same_way(capsys):
    assert main(["datafeed", "check", "--access", "http"]) == EXIT_FAILURE
    assert "does not guess one" in capsys.readouterr().err


def test_the_refusal_names_the_flag_that_would_fix_it(capsys):
    main(["datafeed", "check", "--access", "real"])
    err = capsys.readouterr().err
    assert "--data-url" in err
    assert "--access mock" in err
    assert NO_ADDRESS.split(".")[0] in err


def test_real_and_http_are_the_same_provider():
    """Not two implementations that happen to agree: one provider, two names."""
    from nib_verify.access import HttpAccess, build_access

    a = build_access("real", base_url="http://127.0.0.1:9999")
    b = build_access("http", data_url="http://127.0.0.1:9999")
    assert isinstance(a, HttpAccess) and isinstance(b, HttpAccess)
    assert (a.base_url, a.port) == (b.base_url, b.port)


def test_an_unknown_provider_name_lists_the_real_ones():
    from nib_verify.access.factory import build_access

    with pytest.raises(ValueError, match="mock.*replay.*http.*real"):
        build_access("onnx")


# ======================================================================
# and it really does verify through a socket
# ======================================================================
def test_a_run_reads_its_fields_over_http(tmp_path, platform, capsys):
    """The whole point: same config, same answer, fields from a service."""
    config = _write_config(tmp_path)

    with serve_data(make_access(missing_points=150)) as url:
        assert main([
            "task", "run", "--config", str(config), "--root", str(tmp_path / "store"),
            "--runs", str(platform.runs_root),
            "--access", "real", "--data-url", url, "--progress", "quiet",
        ]) == EXIT_OK

    assert main([
        "query", "--method", "ts", "--config", str(config),
        "--root", str(tmp_path / "store"), "--json",
        "-s", "level=181,219", "-g", "lead_minutes",
    ]) == EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample"]["n_records"] == 3
    assert all(r["n_valid"] > 0 for r in payload["records"])


def test_the_same_run_gives_the_same_numbers_either_way(tmp_path, platform, capsys):
    """Transparency, asserted: the client adds a socket and changes nothing."""
    config = _write_config(tmp_path)
    # Every parameter that shapes the field has to match, sigma included: this
    # test is only worth having because a mismatch shows up in the readings.
    mock_args = ["--mock", "peak=250", "--mock", "unit=kg m-2", "--mock", "variable=vil",
                 "--mock", "sigma=0.14",
                 "--mock", "obs_shape=24,32", "--mock", "model_shape=20,26",
                 "--mock", "horizon_minutes=60", "--mock", "missing_points=150"]

    def readings(root):
        assert main([
            "query", "--method", "ts", "--config", str(config), "--root", str(root),
            "--json", "-s", "level=181,219", "-g", "lead_minutes", "-g", "level",
        ]) == EXIT_OK
        return json.loads(capsys.readouterr().out)["records"]

    common = ["task", "run", "--config", str(config),
              "--runs", str(platform.runs_root), "--progress", "quiet"]
    assert main([*common, "--root", str(tmp_path / "over-http"),
                 "--access", "mock", *mock_args]) == EXIT_OK
    over_http = readings(tmp_path / "over-http")

    with serve_data(make_access(missing_points=150)) as url:
        assert main([*common, "--root", str(tmp_path / "over-socket"),
                     "--access", "real", "--data-url", url]) == EXIT_OK
    over_socket = readings(tmp_path / "over-socket")

    assert over_http == over_socket
    assert over_http, "an empty comparison would pass for the wrong reason"
