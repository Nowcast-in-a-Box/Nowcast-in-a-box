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
import shutil
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

UTC = timezone.utc

#: The demonstration config, and the id is stable so the demo can be re-run over
#: an old workspace.
CONFIG_ID = "vil-demo"
DEMO_NAME = "VIL verification, demonstration"


@dataclass(frozen=True)
class Product:
    """One fabricated observation product: what it is called, and what it is.

    Several products of one storm are not several storms. They are the same
    weather on different scales, with their own instrument noise and their own
    coverage — which is what a multi-source evaluation has to cope with, and
    what makes the ground-truth picker in the wizard worth having.

    Two of these are in ``kg m-2`` and one is not, on purpose: a unit is the
    ruler's business, so a product in another unit is another config.
    """

    id: str
    variable: str
    unit: str
    scale: float
    vmin: float
    vmax: float
    noise: float
    missing_kind: str
    missing_points: int
    frames: int
    resolution: float
    extent: tuple[float, float, float, float]
    shape: tuple[int, int]


@dataclass(frozen=True)
class Forecast:
    """One fabricated model, and the product it nowcasts.

    Declaration order is skill order: the generator makes each one worse than
    the one before, which is what makes a comparison worth showing.
    """

    id: str
    source: str


@dataclass(frozen=True)
class Exercise:
    """One config: a ruler, its thresholds, and the leads worth asking for.

    Two of them, and they share almost nothing, because the two products share
    almost nothing. A radar composite is a fine grid and a short question — will
    the core still be there in an hour. An infrared rain estimate is a coarse grid
    and a longer one, and its thresholds are in different units altogether.

    So the thresholds are the ruler's business: 133 is a number about ``kg m-2``
    and 8 is a number about ``mm h-1``, and neither can be read off the other.
    The store keeps their answers apart because the configs are apart.
    """

    id: str
    name: str
    source: str
    leads: tuple[int, ...]
    levels: tuple[float, ...]
    window: tuple[int, ...]

    @property
    def headline_level(self) -> float:
        """The middle threshold — where the answers differ most between models."""
        return self.levels[len(self.levels) // 2]


#: The two observation products the fabricated platform serves: one from the
#: radar, one from the satellite. Different instruments, different units,
#: different coverage, and the same storm underneath both of them.
#:
#: The satellite product is an estimate of the same rain from above, so it is on a
#: coarser grid and reaches further ahead: half-hourly steps out to two hours is a
#: claim an infrared estimate can make and a radar extrapolation cannot. A radar
#: sees the cores; an infrared estimate sees the anvil over them. Both are
#: ``vil``-shaped fields here, which is the one place the fabrication is cheaper
#: than the world.
SOURCES: tuple[Product, ...] = (
    Product("radar-composite", "vil", "kg m-2", scale=1.0, vmin=0.0, vmax=255.0,
            noise=4.0, missing_kind="range", missing_points=60,
            frames=6, resolution=0.25,
            extent=(110.0, 30.0, 120.0, 42.0), shape=(48, 64)),
    Product("satellite-rain", "rain_rate", "mm h-1", scale=0.16, vmin=0.0, vmax=45.0,
            noise=6.0, missing_kind="random", missing_points=150,
            frames=12, resolution=0.5,
            extent=(104.0, 24.0, 126.0, 48.0), shape=(56, 72)),
)

#: The domain every model's grid covers. Not either product's: a forecast model
#: has its own grid, coarser than both, and resampling onto a ruler is our job.
#: It has to be the union of the two products or the part of a ruler outside it
#: would come back as a gap for no reason worth explaining.
MODEL_EXTENT = (104.0, 24.0, 126.0, 48.0)
MODEL_SHAPE = (48, 64)

#: Two models for each product, in order of skill: the generator makes the second
#: one worse, which is what makes a comparison worth showing. A model belongs to
#: one product because its output is in that product's unit, and the config built
#: on the other product could not take it.
FORECASTS: tuple[Forecast, ...] = (
    Forecast("mlcast-det", "radar-composite"),
    Forecast("mlcast-alt", "radar-composite"),
    Forecast("satcast-det", "satellite-rain"),
    Forecast("satcast-alt", "satellite-rain"),
)

#: One config per product, and each one answers its own product's question.
EXERCISES: tuple[Exercise, ...] = (
    Exercise(CONFIG_ID, DEMO_NAME, "radar-composite",
             leads=(10, 20, 30, 60),
             levels=(16.0, 74.0, 133.0, 181.0, 219.0),
             window=(1, 2)),
    Exercise("rain-demo", "Rain rate verification, demonstration", "satellite-rain",
             leads=(30, 60, 90, 120),
             levels=(0.5, 2.0, 5.0, 12.0),
             window=(2, 4)),
)

#: The demonstration config's own numbers, named so a reader does not have to
#: remember which of the two exercises they came from.
CONFIG_SOURCE = EXERCISES[0].source
DATA_ID = CONFIG_SOURCE
VARIABLE = "vil"
UNIT = "kg m-2"
LEADS = EXERCISES[0].leads
LEVELS = EXERCISES[0].levels


def pairs() -> tuple[tuple[Forecast, Product], ...]:
    """Every (model, product) the fabricated platform has run.

    A model runs against any product in its own unit, which is what makes more
    than one observation source worth having: the same model, fed two different
    views of the storm, verified against the same truth. A product in another
    unit is another config, and it has a run too.
    """
    return tuple(
        (forecast, source)
        for forecast in FORECASTS
        for source in SOURCES
        if source.unit == source_of(forecast.source).unit
    )


def source_of(product: str) -> Product:
    for one in SOURCES:
        if one.id == product:
            return one
    raise KeyError(f"no such product {product!r}")


def exercise(ident: str) -> Exercise:
    for one in EXERCISES:
        if one.id == ident:
            return one
    raise KeyError(f"no such config {ident!r}")


MODELS = tuple(f.id for f in FORECASTS if f.source == CONFIG_SOURCE)

#: How much worse each model is than the one before it. Part of the catalogue
#: rather than of the weather: it has to be re-tuned when the roster grows, or
#: the fifth model lags the truth by an hour.
SKILL_SPREAD = 0.4

SKILL_SPREAD = 0.4

#: The time step. Both products report on the same cadence, which is what lets
#: one provider serve them; how far ahead each is asked to look is not the same
#: question and is not asked in the same units of time.
CADENCE_MINUTES = 10

#: The manifest numbers have to agree with each other or the Matcher is right to
#: refuse the pair, and a demo whose first act is a rejection teaches the wrong
#: thing. The data package supplies history *and* the target window; a model
#: consumes some of the history and produces the target frames. How many target
#: frames is the product's own: a radar composite offers an hour, an infrared
#: rain estimate two.
CONTEXT_FRAMES = 3

#: How many init times the fabricated platform has produced.
INIT_TIMES = 6


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
    def config_files(self) -> list[Path]:
        return [self.configs / f"{spec.id}.json" for spec in EXERCISES]

    @property
    def config_file(self) -> Path:
        """The demonstration one. The other is beside it in the same directory."""
        return self.config_files[0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "scenario": self.scenario,
            "config": str(self.config_file),
            "configs": [str(path) for path in self.config_files],
            "store": str(self.store),
            "packages": [str(p) for p in self.packages],
            "runs": str(self.runs_root) if self.runs_root else None,
            "review": f"http://127.0.0.1:{self.review_port}/",
            "data_url": self.data_url,
        }


# ======================================================================
# what gets written
# ======================================================================
def data_manifest(source: Product) -> dict:
    return {
        "id": source.id,
        "offers": ["precip-nowcast.v1"],
        "region": "east-china",
        "fields": [{
            "name": source.variable,
            "unit": source.unit,
            "dtype": "float32",
            "shape": list(source.shape),
            "cadence_minutes": CADENCE_MINUTES,
            "frame_count": CONTEXT_FRAMES + source.frames,
            "target_frame_count": source.frames,
            "extent": list(source.extent),
            "resolution": source.resolution,
        }],
    }


def model_manifest(forecast: Forecast, source: Product) -> dict:
    return {
        "id": forecast.id,
        "requires": "precip-nowcast.v1",
        "region": "east-china",
        "context_frames": {"required_count": CONTEXT_FRAMES, "mode": "latest"},
        "input": [{"name": source.variable, "unit": source.unit, "dtype": "float32",
                   "shape": list(source.shape)}],
        "output": [{
            "name": source.variable,
            "unit": source.unit,
            "dtype": "float32",
            "shape": list(source.shape),
            "cadence_minutes": CADENCE_MINUTES,
            "frame_count": source.frames,
        }],
    }


def run_config_text(forecast: Forecast, source: Product, start: str) -> str:
    """A run config, byte for byte the shape ``contracts/README.md`` describes.

    The horizon is the product's, in frames: the number that becomes minutes is
    the config's time step, and a run cannot reach a lead the model never claimed.
    """
    return (
        "version: 1\n"
        f"data:\n  id: {source.id}\n"
        f"model:\n  id: {forecast.id}\n"
        "run:\n"
        f"  start_time: \"{start}\"\n"
        f"  forecast_horizon: {source.frames}\n"
    )


def catalogue(scenario: str) -> dict:
    """The provider's settings: the scenario's weather, this catalogue's names.

    Two things kept apart on purpose. The scenario says what the weather *does* —
    cells, drift, how wrong a forecast gets. This table says what *exists* — which
    products and which models, in which units. The manifests written into the
    workspace describe the second, so they are written from the same table, and
    the service and the tree cannot disagree about what is out there.
    """
    from .access.scenario import settings as scenario_settings

    return {
        **scenario_settings(scenario),
        "observation_names": tuple(s.id for s in SOURCES),
        "model_ids": tuple(f.id for f in FORECASTS),
        "skill_spread": SKILL_SPREAD,
        # The provider has to be able to serve the longest horizon any model
        # claims, which is the satellite's. A scenario's own horizon is about the
        # weather; how far ahead anybody looks is the catalogue's business.
        "horizon_minutes": max(s.frames for s in SOURCES) * CADENCE_MINUTES,
        # Each product comes on its own footprint -- a radar sees the range of one
        # radar. The model grid is its own too, and has to cover both or the part
        # of a ruler outside it is a gap that means nothing.
        "model_extent": MODEL_EXTENT,
        "model_shape": MODEL_SHAPE,
        "sources": {
            s.id: {
                "variable": s.variable, "unit": s.unit, "scale": s.scale,
                "vmin": s.vmin, "vmax": s.vmax, "noise": s.noise,
                "missing_kind": s.missing_kind,
                "missing_points": s.missing_points,
                "extent": s.extent,
                "shape": s.shape,
            }
            for s in SOURCES
        },
        "model_sources": {f.id: f.source for f in FORECASTS},
    }


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


def verification_config(spec: Exercise | None = None):
    """The config for one exercise. The roster is every model in its unit."""
    from .config.schema import Member, MetricGroup, Ruler, VerificationConfig

    spec = spec or EXERCISES[0]
    source = source_of(spec.source)
    return VerificationConfig(
        group_id=spec.id,
        name=spec.name,
        ruler=Ruler(
            ground_truth=spec.source, unit=source.unit,
            time_step_minutes=CADENCE_MINUTES, extent=source.extent,
            resolution=source.resolution,
        ),
        members=tuple(
            Member(name=f.id, variable=source.variable, unit=source.unit, leads=spec.leads)
            for f in FORECASTS if f.source == spec.source
        ),
        metrics=(
            MetricGroup(family="yes_or_no",
                        items=("ts", "bias", "ets", "pod", "far", "dts"),
                        levels=spec.levels),
            MetricGroup(family="continuous", items=("me", "mae", "rmse")),
            MetricGroup(family="moments", items=("corr",)),
            MetricGroup(family="spatial", items=("fss",), levels=spec.levels,
                        half_windows=spec.window),
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

    if force:
        # "Rebuild over it" has to mean the parts this command owns. Leaving the
        # store behind would leave the old quantities in place under the old
        # names — a workspace that looks rebuilt and answers every query with a
        # gap. Only paths the demo itself writes are ever removed.
        for name in ("config.yaml", "README.md", "configs", "inputs", "packages",
                     "workspace", ".store"):
            victim = root / name
            if victim.is_dir():
                shutil.rmtree(victim, ignore_errors=True)
            elif victim.exists():
                victim.unlink()

    say(f"  1/6  platform     fabricating a workspace in {root}")
    demo = Demo(root=root, scenario=scenario, review_port=review_port,
                configs=root / "configs", store=root / ".store")

    _write(Path(root / "config.yaml"), host_config_text(review_port))
    _write(Path(root / "inputs" / "data" / ".keep"), "")
    demo.config_path = root / "config.yaml"
    say("         config.yaml         review 127.0.0.1:%d, data root inputs/data"
        % review_port)

    # Manifests in unrelated directories, on purpose: they are found by what they
    # declare, not by where they were put, and the tree says so by not looking
    # like a catalogue.
    places = [root / "packages" / "bundle-a",
              root / "packages" / "bundle-b",
              root / "packages" / "deep" / "nested" / "down" / "here"]
    writes: list[tuple[Path, dict]] = []
    for index, source in enumerate(SOURCES):
        writes.append((places[index % len(places)] / source.id / "manifest.json",
                       data_manifest(source)))
    for index, forecast in enumerate(FORECASTS):
        writes.append((places[(index + 1) % len(places)] / forecast.id / "manifest.json",
                       model_manifest(forecast, source_of(forecast.source))))
    for path, payload in writes:
        _json(path, payload)
    demo.packages = [root / "packages"]
    say("         packages            %d manifests (%d products, %d models), "
        "in unrelated directories" % (len(writes), len(SOURCES), len(FORECASTS)))
    for source in SOURCES:
        say("         %-18s %s [%s]" % (source.id, source.variable, source.unit))

    demo.runs_root = root / "workspace" / "jobs"
    start = datetime(2026, 1, 1, tzinfo=UTC)
    count = 0
    for hour in range(INIT_TIMES):
        moment = (start + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for forecast, source in pairs():
            _write(
                demo.runs_root
                / f"{moment.replace(':', '')}-{source.id}-{forecast.id}" / "run.yaml",
                run_config_text(forecast, source, moment),
            )
            count += 1
    say(f"         run configs         {count} runs ({INIT_TIMES} init times x "
        f"{len(pairs())} model/source pairs)")

    demo.configs.mkdir(parents=True, exist_ok=True)
    for spec in EXERCISES:
        _json(demo.configs / f"{spec.id}.json", verification_config(spec).to_dict())
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


def show_configs(demo: Demo, *, say: Callable[[str], None] = print) -> list:
    """Read both configs back. Two rulers, two rosters, two sets of levels."""
    from .config.schema import load

    say("  3/6  config       %d configs, one per product" % len(demo.config_files))
    out = []
    for spec in EXERCISES:
        cfg = load(demo.configs / f"{spec.id}.json")
        out.append(cfg)
        say("         %-10s ruler %s  %s [%s] @ %g"
            % (cfg.group_id, cfg.ruler.fingerprint, cfg.ruler.ground_truth,
               cfg.ruler.unit, cfg.ruler.resolution))
        say("         %-10s %d members %s, leads %s, levels %s"
            % ("", len(cfg.members), "/".join(m.name for m in cfg.members),
               list(spec.leads), list(spec.levels)))
        families = sorted({g.family for g in cfg.metrics})
        say("         %-10s %d metric families: %s"
            % ("", len(families), ", ".join(families)))
    return out


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

    access = build_access("mock", **catalogue(demo.scenario))
    caps = access.capabilities()
    say("  4/6  datafeed     serving the %r scenario" % demo.scenario)
    say("         %s" % _describe(caps))

    with serve_in_thread(access, port=port) as url:
        demo.data_url = url
        say("         %s   <- a real service on a real socket" % url)
        say("         curl %s/capabilities" % url)
        yield access, url


def _describe(caps) -> str:
    seen: list[str] = []
    for name in caps.observation_names:
        unit = caps.unit_of(name) or "?"
        seen.append(f"{name} [{unit}]")
    return (
        f"{caps.cadence_minutes}-minute cadence, "
        f"observations {', '.join(seen)}, "
        f"{len(caps.model_ids)} models"
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

    book = discover_runs([demo.runs_root])
    catalog = _catalog(demo)

    say("  5/6  verify       through that service, over HTTP -- not in process")
    client = CountingAccess(build_access("real", base_url=data_url))
    demo.store.mkdir(parents=True, exist_ok=True)
    store = Store(demo.store / "verify.sqlite")
    try:
        for spec in EXERCISES:
            cfg = load(demo.configs / f"{spec.id}.json")
            result = plan(cfg, book, catalog=catalog, access=access, probe=True)
            if result.is_empty:
                for reason in result.reasons().items():
                    say("         rejected: %s" % (reason,))
                raise RuntimeError(
                    "nothing was planned for %s; the demo workspace is inconsistent"
                    % spec.id
                )
            before = dict(client.log.calls)
            run_tasks(client, store, cfg, planned_runs(result))
            facts = store.task_counts(cfg.group_id)
            say("         %-11s planned %3d tasks over %d init time(s)"
                % (spec.id, len(result.tasks), len(result.init_times())))
            say("         %-11s ran     %3d done, %d unavailable, %d failed, %d skipped"
                % ("", facts.get("done", 0), facts.get("unavailable", 0),
                   facts.get("failed", 0), facts.get("skipped", 0)))
            # The counters matter: they are what the acceptance condition is
            # stated in. Adding a threshold has to leave this second number
            # exactly where it is.
            say("         %-11s fetches %3d observation, %d prediction, over the socket"
                % ("",
                   client.log.calls.get("get_observation", 0)
                   - before.get("get_observation", 0),
                   client.log.calls.get("get_prediction", 0)
                   - before.get("get_prediction", 0)))
    finally:
        store.close()
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

    store = Store(demo.store / "verify.sqlite")
    out: list[dict] = []
    try:
        for spec in EXERCISES:
            cfg = load(demo.configs / f"{spec.id}.json")
            level = spec.headline_level
            result = query_scores(store, cfg, method="ts", s={"level": [level]},
                                  g=["member", "lead_minutes"])
            rows = _pivot(result["records"], spec.leads)
            out.extend(rows)
            say("")
            say("         %s: CSI at %g %s, by lead time"
                % (spec.id, level, cfg.ruler.unit))
            say("           " + "".join("%7s" % lead for lead in spec.leads))
            for row in rows:
                cells = "".join(
                    "%7s" % ("--" if row["values"].get(lead) is None
                             else f"{row['values'][lead]:.3f}")
                    for lead in spec.leads
                )
                say("         %-11s%s" % (row["member"], cells))
    finally:
        store.close()
    return out


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
        build_access(access, **catalogue(demo.scenario)),
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
