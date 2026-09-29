"""The whole module, end to end, from nothing to a chart.

This exists for one reason: to show somebody the thing working without asking
them to assemble a platform first. Everything it builds is fabricated, and it
says so in the workspace it writes — the point is that the *module* is real, and
that the only fictional part is the platform it is pointed at.

What it builds, in order:

1. **A platform.** A ``config.yaml``, three package manifests in three unrelated
   directories, and a dozen run configs. Deliberately not a tidy catalog tree:
   discovery here is by content, and a demo that only worked on a conventional
   layout would be a demo of the layout.
2. **A catalogue**, read out of that tree by :mod:`nib_verify.catalog`.
3. **A verification config** — one ruler, two members, five thresholds, four
   metric families.
4. **A data service**, on a real socket, serving the simulated fields. Not a stub
   and not in-process: it is the same :class:`HttpAccess` client that will talk to
   the platform's own service when there is one.
5. **A verification run** that goes over that socket.
6. **The review page**, pointed back at the same service, so a re-run from the
   browser also goes over HTTP.

The stages are printed as they happen, because the printout is half the point.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

UTC = timezone.utc

#: One config, and the id is stable so a demo can be re-run over an old one.
CONFIG_ID = "vil-demo"
DEMO_NAME = "VIL verification, demonstration"

#: Two models, in order of skill: the generator makes the second one worse, which
#: is what makes a comparison worth showing.
MODELS = ("mlcast-det", "mlcast-alt")
DATA_ID = "radar-composite"
VARIABLE = "vil"
UNIT = "kg m-2"

#: The 0-255 VIL scale. Five thresholds, chosen so that the answers differ at
#: each one — a single threshold would make the whole exercise look like a
#: pass/fail test.
LEVELS = (16.0, 74.0, 133.0, 181.0, 219.0)

#: Leads are the config's, and the runs carry them; the generator's horizon has
#: to cover the largest or the planner clips it.
LEADS = (10, 20, 30, 60)
CADENCE_MINUTES = 10

#: The manifest numbers have to agree with each other or the Matcher is right to
#: refuse the pair, and a demo whose first act is a rejection teaches the wrong
#: thing. The data package supplies history *and* the target window; a model
#: consumes some of the history and produces the target frames.
CONTEXT_FRAMES = 3
TARGET_FRAMES = 6                       # 6 x 10 minutes = the 60-minute lead
SOURCE_FRAMES = CONTEXT_FRAMES + TARGET_FRAMES
FORECAST_HORIZON_FRAMES = TARGET_FRAMES

#: How many init times the fabricated platform has produced.
INIT_TIMES = 6

#: The extent and resolution the config declares. Entered by hand today; the
#: module resamples whatever it is given onto this grid.
EXTENT = (110.0, 30.0, 120.0, 42.0)
RESOLUTION = 0.5


@dataclass
class Demo:
    """Where everything ended up."""

    root: Path
    scenario: str
    review_port: int
    configs: Path
    store: Path
    packages: list[Path] = field(default_factory=list)
    runs_root: Path | None = None
    config_path: Path | None = None
    data_url: str | None = None

    @property
    def config_file(self) -> Path:
        return self.configs / f"{CONFIG_ID}.json"

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "scenario": self.scenario,
            "config": str(self.config_file),
            "store": str(self.store),
            "packages": [str(p) for p in self.packages],
            "runs": str(self.runs_root) if self.runs_root else None,
            "review": f"http://127.0.0.1:{self.review_port}/",
            "data_url": self.data_url,
        }


# ======================================================================
# what gets written
# ======================================================================
def data_manifest() -> dict:
    return {
        "id": DATA_ID,
        "offers": ["precip-nowcast.v1"],
        "region": "east-china",
        "fields": [{
            "name": VARIABLE,
            "unit": UNIT,
            "dtype": "float32",
            "shape": [48, 64],
            "cadence_minutes": CADENCE_MINUTES,
            "frame_count": SOURCE_FRAMES,
            "target_frame_count": TARGET_FRAMES,
        }],
    }


def model_manifest(model_id: str) -> dict:
    return {
        "id": model_id,
        "requires": "precip-nowcast.v1",
        "region": "east-china",
        "context_frames": {"required_count": CONTEXT_FRAMES, "mode": "latest"},
        "input": [{"name": VARIABLE, "unit": UNIT, "dtype": "float32",
                   "shape": [48, 64]}],
        "output": [{
            "name": VARIABLE,
            "unit": UNIT,
            "dtype": "float32",
            "shape": [48, 64],
            "cadence_minutes": CADENCE_MINUTES,
            "frame_count": TARGET_FRAMES,
        }],
    }


def run_config_text(model_id: str, start: str) -> str:
    """A run config, byte for byte the shape ``contracts/README.md`` describes."""
    return (
        "version: 1\n"
        f"data:\n  id: {DATA_ID}\n"
        f"model:\n  id: {model_id}\n"
        "run:\n"
        f"  start_time: \"{start}\"\n"
        f"  forecast_horizon: {FORECAST_HORIZON_FRAMES}\n"
    )


def host_config_text(review_port: int) -> str:
    """The platform's own settings, in the platform's own file.

    ``review.port`` is read by ``serve`` rather than passed to it, which is the
    behaviour ``AGENTS.md`` asks for and worth showing: the module does not
    decide where it listens, it is told.
    """
    return (
        "# Fabricated for the demo. The module reads this and does not edit it.\n"
        "handler:\n  host: 127.0.0.1\n  port: 8765\n"
        f"review:\n  host: 127.0.0.1\n  port: {review_port}\n"
        "paths:\n  artifacts: artifacts\n  data_root: inputs/data\n"
    )


def verification_config():
    from .config.schema import Member, MetricGroup, Ruler, VerificationConfig

    return VerificationConfig(
        group_id=CONFIG_ID,
        name=DEMO_NAME,
        ruler=Ruler(
            ground_truth=DATA_ID, unit=UNIT,
            time_step_minutes=CADENCE_MINUTES, extent=EXTENT, resolution=RESOLUTION,
        ),
        members=tuple(
            Member(name=model_id, variable=VARIABLE, unit=UNIT, leads=LEADS)
            for model_id in MODELS
        ),
        metrics=(
            MetricGroup(family="categorical", items=("ts", "bias", "ets", "pod", "far"),
                        levels=LEVELS),
            MetricGroup(family="continuous", items=("me", "mae", "rmse")),
            MetricGroup(family="moments", items=("corr",)),
            MetricGroup(family="spatial", items=("fss",), levels=LEVELS,
                        half_windows=(1, 2)),
        ),
    )


WORKSPACE_README = """\
# This directory is fabricated

It exists so the verification module can be demonstrated without the platform,
and **nothing in it is a claim about how the platform stores anything**. The
directories are named the way they are to make that obvious:

    packages/bundle-a/radar-composite/manifest.json     a data package
    packages/bundle-b/mlcast-det/manifest.json          a model package
    packages/deep/nested/down/here/manifest.json        a model package

Three unrelated subtrees. The module finds all three, because it identifies a
package by whether it has an `offers` or a `requires` key and not by where it
sits. Point it at the real workspace and it will read that instead.

    workspace/jobs/<init>-<model>/run.yaml              run configs, v1
    configs/vil-demo.json                               a verification config

`config.yaml` is the platform's; the module reads it and never writes to it. In
particular `review.port` comes from there, which is why `serve` binds 127.0.0.1
and not whatever a command line felt like.

`inputs/data` is empty on purpose. The fields do not come from disk here — they
come from the field service, over HTTP, exactly as they will come from the
platform's own service. Nothing in this tree is read as a data file.
"""


# ======================================================================
# build
# ======================================================================
def build(root: Path, *, scenario: str = "convective", review_port: int = 8766,
          force: bool = False, say: Callable[[str], None] = print) -> Demo:
    """Lay out the whole workspace. Does not start anything."""
    from .access import scenario as scenarios

    root = Path(root).resolve()
    if root.exists() and any(root.iterdir()) and not force:
        raise FileExistsError(
            f"{root} is not empty. Pass --force to rebuild over it, or --where "
            "somewhere-else."
        )
    # Fail on an unknown scenario before writing half a workspace.
    scenarios.settings(scenario)

    say(f"  1/6  platform     fabricating a workspace in {root}")
    demo = Demo(root=root, scenario=scenario, review_port=review_port,
                configs=root / "configs", store=root / ".store")

    _write(Path(root / "config.yaml"), host_config_text(review_port))
    _write(Path(root / "inputs" / "data" / ".keep"), "")
    demo.config_path = root / "config.yaml"
    say("         config.yaml         review 127.0.0.1:%d, data root inputs/data"
        % review_port)

    # Three manifests in three unrelated places, on purpose.
    writes = {
        root / "packages" / "bundle-a" / "radar-composite" / "manifest.json": data_manifest(),
        root / "packages" / "bundle-b" / "mlcast-det" / "manifest.json": model_manifest(MODELS[0]),
        root / "packages" / "deep" / "nested" / "down" / "here" / "manifest.json":
            model_manifest(MODELS[1]),
    }
    for path, payload in writes.items():
        _json(path, payload)
    demo.packages = [root / "packages"]
    say("         packages            %d manifests, in unrelated directories" % len(writes))

    demo.runs_root = root / "workspace" / "jobs"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    count = 0
    for hour in range(INIT_TIMES):
        moment = (start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for model_id in MODELS:
            _write(
                demo.runs_root / f"{moment.replace(':', '')}-{model_id}" / "run.yaml",
                run_config_text(model_id, moment),
            )
            count += 1
    say(f"         run configs         {count} runs ({INIT_TIMES} init times x "
        f"{len(MODELS)} models)")

    demo.configs.mkdir(parents=True, exist_ok=True)
    _json(demo.config_file, verification_config().to_dict())
    _write(root / "README.md", WORKSPACE_README)
    return demo


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _json(path: Path, payload: Any) -> None:
    _write(path, json.dumps(payload, indent=2) + "\n")


# ======================================================================
# look at it
# ======================================================================
def survey(demo: Demo, *, say: Callable[[str], None] = print):
    """Read the catalogue back out, the way the module itself would."""
    from .catalog import discover_packages, discover_runs

    catalog = discover_packages(demo.packages)
    book = discover_runs([demo.runs_root])

    say("  2/6  catalog      read by content, not by layout")
    say("         data packages       %d   %s"
        % (len(catalog.data_packages), ", ".join(catalog.data_ids) or "none"))
    say("         model packages      %d   %s"
        % (len(catalog.model_packages), ", ".join(catalog.model_ids) or "none"))

    from .catalog.matching import compatible_pairs

    every = len(catalog.data_packages) * len(catalog.model_packages)
    pairs = compatible_pairs(catalog.data_packages, catalog.model_packages)
    say("         compatible pairs    %d of %d pass the Matcher" % (len(pairs), every))
    say("         runs                %d, %s" % (len(book), ", ".join(book.model_ids) or "none"))
    if catalog.problems():
        say("         skipped             %d unreadable manifest(s)" % len(catalog.problems()))
    return catalog, book


def show_config(demo: Demo, *, say: Callable[[str], None] = print):
    from .config.schema import load

    cfg = load(demo.config_file)
    say("  3/6  config       %s  ruler %s" % (cfg.group_id, cfg.ruler.fingerprint))
    say("         %s, %d members %s, leads %s, %d thresholds"
        % (cfg.ruler.ground_truth, len(cfg.members),
           "/".join(m.name for m in cfg.members), list(LEADS), len(LEVELS)))
    families = sorted({g.family for g in cfg.metrics})
    say("         %d metric families: %s" % (len(families), ", ".join(families)))
    return cfg


# ======================================================================
# run it, over a socket
# ======================================================================
@contextmanager
def datafeed(demo: Demo, *, say: Callable[[str], None] = print, port: int = 0):
    """The simulated platform's data service, on a real port.

    Kept alive for the length of the demo, so the same URL serves the initial
    run and every re-run from the review page.
    """
    from .access import build_access
    from .access.mockfeed import serve_in_thread

    access = build_access("mock", scenario=demo.scenario)
    caps = access.capabilities()
    say("  4/6  datafeed     serving the %r scenario" % demo.scenario)
    say("         %s" % _describe(caps))

    with serve_in_thread(access, port=port) as url:
        demo.data_url = url
        say("         %s   <- a real service on a real socket" % url)
        say("         curl %s/capabilities" % url)
        yield access, url


def _describe(caps) -> str:
    return (
        f"{caps.cadence_minutes}-minute cadence, "
        f"observations {list(caps.observation_names)}, "
        f"models {list(caps.model_ids)}"
    )


def verify(demo: Demo, *, access, data_url: str,
           say: Callable[[str], None] = print):
    """Plan from the run configs, then verify through the HTTP client."""
    from .access import build_access
    from .access.probe import CountingAccess
    from .catalog import discover_runs
    from .config.schema import load
    from .core.planner import plan, planned_runs
    from .runner.executor import run_tasks
    from .store import Store

    cfg = load(demo.config_file)
    book = discover_runs([demo.runs_root])

    say("  5/6  verify       through that service, over HTTP -- not in process")
    result = plan(cfg, book, catalog=_catalog(demo), access=access, probe=True)
    if result.is_empty:
        for reason in result.reasons().items():
            say("         rejected: %s" % (reason,))
        raise RuntimeError("nothing was planned; the demo workspace is inconsistent")
    say("         planned             %d tasks over %d init time(s)"
        % (len(result.tasks), len(result.init_times())))

    client = CountingAccess(build_access("real", base_url=data_url))
    demo.store.mkdir(parents=True, exist_ok=True)
    store = Store(demo.store / "verify.sqlite")
    try:
        run_tasks(client, store, cfg, planned_runs(result))
        facts = store.task_counts(cfg.group_id)
    finally:
        store.close()
    say("         ran                 %d done, %d unavailable, %d failed, %d skipped"
        % (facts.get("done", 0), facts.get("unavailable", 0),
           facts.get("failed", 0), facts.get("skipped", 0)))
    # The counters matter: they are what the acceptance condition is stated in.
    # Adding a threshold has to leave this second number exactly where it is.
    say("         fetches             %d observation, %d prediction, over the socket"
        % (client.log.calls.get("get_observation", 0),
           client.log.calls.get("get_prediction", 0)))
    return client.log


def _catalog(demo: Demo):
    from .catalog import discover_packages

    return discover_packages(demo.packages)


# ======================================================================
# show the answer
# ======================================================================
def headline(demo: Demo, *, say: Callable[[str], None] = print) -> list[dict]:
    """The one table worth putting at the end of a terminal."""
    from .config.schema import load
    from .query.service import query_scores
    from .store import Store

    cfg = load(demo.config_file)
    store = Store(demo.store / "verify.sqlite")
    try:
        result = query_scores(store, cfg, method="ts", s={"level": [181.0]},
                              g=["member", "lead_minutes"])
    finally:
        store.close()

    rows = _pivot(result["records"], LEADS)
    say("")
    say("         CSI at %g, by lead time" % 181.0)
    header = "           " + "".join("%7s" % lead for lead in LEADS)
    say(header)
    for row in rows:
        cells = "".join(
            "%7s" % ("--" if row["values"].get(lead) is None else f"{row['values'][lead]:.3f}")
            for lead in LEADS
        )
        say("         %-11s%s" % (row["member"], cells))
    return rows


def _pivot(records: Sequence[dict], leads: Sequence[int]) -> list[dict]:
    tables: dict[str, dict[int, float | None]] = {}
    for record in records:
        tables.setdefault(record["member"], {})[int(record["lead_minutes"])] = record["value"]
    return [
        {"member": member, "values": {lead: table.get(lead) for lead in leads}}
        for member, table in sorted(tables.items())
    ]


def next_steps(demo: Demo, *, say: Callable[[str], None] = print) -> None:
    say("")
    say("  6/6  review       http://127.0.0.1:%d/" % demo.review_port)
    say("")
    say("  Try, in the browser:")
    say("    Results  - pick a config, tick member and lead_minutes, Run query.")
    say("               The chart below the table is the same query, drawn.")
    say("    Results  - set Levels to 133 and Run query again. The numbers change")
    say("               and nothing is recomputed: that is the acceptance condition.")
    say("    Configs  - open %s. The ruler cannot be edited; metrics can only be" % CONFIG_ID)
    say("               added to. Both are refused by the service, not the page.")
    say("    New config - the wizard, end to end, including the catalog picker.")
    say("")
    say("  And from another terminal, while this runs:")
    say("    curl %s/capabilities" % (demo.data_url or "<data-url>"))
    say("    python -m nib_verify host show --host-config %s" % demo.config_path)
    say("    python -m nib_verify task plan --config %s --runs %s --probe"
        % (demo.config_file, demo.runs_root))


# ======================================================================
# the one command
# ======================================================================
def serve_review(demo: Demo, *, data_url: str, access: str = "real",
                 log_level: str = "warning") -> None:
    """Bring up the review page, pointed at the same data service. Blocks."""
    import uvicorn

    from .access import build_access
    from .catalog.hostconfig import load as load_host
    from .server import create_app

    app = create_app(
        root=demo.store,
        configs=demo.configs,
        packages=demo.packages,
        runs=[demo.runs_root],
        host=load_host(demo.config_path),
        access=build_access(access, base_url=data_url) if access == "real" else
        build_access(access, scenario=demo.scenario),
    )
    # From config.yaml, the same way ``serve`` does it. The demo would otherwise
    # be the one place that quietly does the thing AGENTS.md forbids.
    host, port = load_host(demo.config_path).review_address
    uvicorn.run(app, host=host, port=port, log_level=log_level)


def wait_for(url: str, *, timeout: float = 20.0) -> bool:
    """Poll a URL until it answers. Only used to make the demo's timing pleasant."""
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(url, timeout=2).read(1)
            return True
        except Exception:
            time.sleep(0.1)
    return False
