"""The web service: the review page, and the API behind it.

`AGENTS.md` says the review page runs on the host, reads its bind address from
``config.yaml``, and binds loopback only. So :func:`create_app` takes an already
resolved :class:`HostConfig` and never decides an address itself; the ``serve``
command reads ``review.host`` and ``review.port`` and passes them to uvicorn.

The API is deliberately thin. Almost every endpoint is a few lines over
:class:`~nib_verify.server.library.ConfigLibrary`,
:func:`~nib_verify.core.planner.plan`, :func:`~nib_verify.query.service.query_scores`
or :class:`~nib_verify.server.jobs.JobRegistry`. There is no second
implementation of anything here: if the page can do it, the command line can do
it, because they call the same functions.

Three rules the API enforces rather than trusts the page to respect:

* a config change that touches the ruler is refused, not written;
* metrics are appended, never removed;
* an empty plan caused by rejections is reported as such, with reasons.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog.hostconfig import HostConfig, HostConfigError
from ..catalog.matching import check_pair
from ..catalog.packages import (
    ManifestError, discover as discover_packages, ruler_defaults,
)
from ..catalog.runs import RunConfigError, discover as discover_runs
from ..config.schema import (
    ConfigError,
    Member,
    MetricGroup,
    VerificationConfig,
)
from ..core.metrics import list_families, list_metrics as metric_list
from ..core.planner import plan as plan_tasks
from ..core.planner import planned_runs
from ..query.service import QueryError, available_expansions, query_scores
from ..runner.executor import RunRef, run_tasks
from ..runner.lock import Locked, SingleInstanceLock
from ..store.db import Store
from .jobs import JobRegistry
from .library import ConfigLibrary, ConfigNotFound

# Imported at module level, and guarded, for a reason that is easy to miss: this
# module uses ``from __future__ import annotations``, so every annotation is a
# string resolved against module globals. Importing Request inside create_app
# would leave it unresolvable, FastAPI would fall back to reading it as a query
# parameter, and every endpoint taking a Request would answer 422.
try:  # pragma: no cover - exercised by whichever branch the environment takes
    from fastapi import Body, FastAPI, Query, Request
    from fastapi.responses import FileResponse, JSONResponse, Response
    from fastapi.staticfiles import StaticFiles
    HAS_FASTAPI = True
except ModuleNotFoundError:  # pragma: no cover
    Body = FastAPI = Query = Request = Response = None
    FileResponse = JSONResponse = StaticFiles = None
    HAS_FASTAPI = False

UTC = timezone.utc

STATIC_DIR = Path(__file__).parent / "static"

#: storage family -> the family name a config uses.  Two vocabularies exist
#: because a config groups metrics by what they mean and the store groups them by
#: how they accumulate; the API speaks the config's, since that is what a user
#: sees and types.


def create_app(
    *,
    root: str | Path,
    configs: str | Path | ConfigLibrary,
    packages: list[str | Path] | None = None,
    runs: list[str | Path] | None = None,
    host: HostConfig | None = None,
    access=None,
    access_error: str | None = None,
):
    """Build the app around a verification root, a config directory and a provider."""
    if not HAS_FASTAPI:  # pragma: no cover
        raise RuntimeError(
            "the web service needs fastapi and uvicorn: pip install -e '.[web]'"
        )

    root = Path(root)
    library = configs if isinstance(configs, ConfigLibrary) else ConfigLibrary(configs)
    package_roots = [Path(p) for p in (packages or [])]
    run_roots = [Path(p) for p in (runs or [])]
    jobs = JobRegistry()
    host = host or HostConfig()

    app = FastAPI(
        title="NiB verification",
        version="0.1.0",
        description="Metrics from saved prediction and truth. Thresholds change "
                    "without rerunning inference.",
    )

    # ---------------------------------------------------------------- errors
    @app.exception_handler(ConfigNotFound)
    async def _missing(request: Request, exc: Exception):
        return JSONResponse(
            status_code=404,
            content={"error": "NotFound", "detail": str(exc)},
        )

    @app.exception_handler(ConfigError)
    @app.exception_handler(QueryError)
    @app.exception_handler(ManifestError)
    @app.exception_handler(RunConfigError)
    @app.exception_handler(HostConfigError)
    @app.exception_handler(Locked)
    async def _bad_request(request: Request, exc: Exception):
        return JSONResponse(
            status_code=400,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )

    @app.exception_handler(KeyError)
    async def _not_found(request: Request, exc: KeyError):
        return JSONResponse(
            status_code=404,
            content={"error": "NotFound", "detail": str(exc).strip("'")},
        )

    # ---------------------------------------------------------------- helpers
    @contextmanager
    def store():
        s = Store(root / "verify.sqlite")
        try:
            yield s
        finally:
            s.close()

    def catalog_payload() -> dict:
        catalog = discover_packages(package_roots) if package_roots else None
        book = discover_runs(run_roots) if run_roots else None
        pairs = []
        if catalog is not None and catalog.data_packages and catalog.model_packages:
            for data in catalog.data_packages:
                for model in catalog.model_packages:
                    pairs.append(check_pair(data, model).as_dict())
        payload = {
            "packages": catalog.as_dict() if catalog is not None else None,
            "runs": book.as_dict() if book is not None else None,
            "pairs": pairs,
            "ok_pairs": [p for p in pairs if p["ok"]],
        }
        payload["leads"] = available_leads(catalog, book)
        return payload

    def make_access(access_kind: str | None = None):
        """The provider, or a clear refusal naming what is missing."""
        if access is not None:
            return access
        raise ConfigError(
            access_error
            or "no field provider is configured on this server. Start it with "
               "--access mock (or --access replay --mock root=DIR)."
        )

    def runs_for(body: dict) -> list[RunRef]:
        """Turn the request's run selection into executor run references."""
        explicit = body.get("init") or []
        if explicit:
            inits = [_parse_time(t) for t in explicit]
            cfg = library.get(body["config"])
            return [RunRef(member=m.name, init_time=t) for t in inits for m in cfg.members]

        book = discover_runs(run_roots) if run_roots else None
        if book is None:
            return []
        cfg = library.get(body["config"])
        plan = plan_tasks(cfg, book)
        return planned_runs(plan)

    # ================================================================ meta
    @app.get("/api/health")
    def health():
        return {
            "ok": True,
            "service": "nib-verify",
            "root": str(root),
            "configs": str(library.directory),
            "host": host.as_dict(),
            "running_jobs": len(jobs.busy()),
        }

    @app.get("/api/host")
    def get_host():
        return host.as_dict()

    @app.get("/api/catalog")
    def get_catalog():
        return catalog_payload()

    @app.get("/api/metrics")
    def get_metrics():
        # The families come with the words the page shows and the ones a new
        # config starts with, so neither is written down twice.
        return {"metrics": metric_list(), "families": list_families()}

    @app.get("/api/metrics/{method}/dimensions")
    def get_dimensions(method: str):
        return {"method": method, "dimensions": available_expansions(method)}

    # ============================================================== configs
    @app.get("/api/configs")
    def list_configs():
        return {"configs": library.entries(), "broken": library.broken()}

    @app.get("/api/configs/{group_id}")
    def get_config(group_id: str):
        cfg = library.get(group_id)
        payload = cfg.to_dict()
        payload.update(library.summary(cfg))
        with store() as s:
            init_times, _ = s.task_axes(group_id)
            payload["stored"] = {
                "tasks": s.task_counts(group_id),
                "families": s.families_present(group_id),
                "missing_obs_times": len(s.obs_gaps(group_id)),
                "runs": s.run_history(group_id, limit=10),
                # The window the config was actually verified over, so a form can
                # open on a range that has something in it. "The last week" taken
                # off the wall clock is the right default for a live system and an
                # empty page for a replayed one, and there is no way for the page
                # to tell the two apart.
                "init_span": [init_times[0], init_times[-1]] if init_times else None,
            }
        payload["metrics_available"] = available_metrics(cfg)
        # The page draws the legend and the canvas draws the lines. One palette,
        # handed over rather than re-derived: a key whose swatch is a different
        # colour from the line it names is worse than no key.
        payload["series_colours"] = series_colours(cfg)
        return payload

    @app.post("/api/configs", status_code=201)
    def create_config(body: dict = Body(...)):
        spec = _strip_fingerprints(body.get("spec") or body)
        spec.setdefault("group", {})
        spec = fill_ruler_from_ground_truth(spec, package_roots)
        cfg = VerificationConfig.from_dict(spec)

        problems = _validate_against_catalog(cfg, package_roots)
        if body.get("strict", True) and problems:
            raise ConfigError("; ".join(problems))

        library.create(cfg, overwrite=bool(body.get("overwrite")))
        return {"created": cfg.group_id, "config": library.summary(cfg),
                "warnings": problems}

    @app.delete("/api/configs/{group_id}")
    def delete_config(group_id: str):
        cfg = library.get(group_id)
        path = library.delete(group_id)
        with store() as s:
            removed = s.delete_group(group_id)
        return {"deleted": str(path), "removed_rows": removed,
                "group_id": cfg.group_id}

    @app.post("/api/configs/{group_id}/roster", status_code=201)
    def add_member(group_id: str, body: dict = Body(...)):
        cfg = library.get(group_id)
        name = _required(body, "name")
        # Changing the leads of a member already on the roster should not require
        # restating what it produces. The config already knows, and asking again
        # turns a one-field edit into an easy way to get the unit wrong -- which
        # is the failure this whole check exists to catch.
        existing = next((m for m in cfg.members if m.name == name), None)
        unit = body.get("unit") or (existing.unit if existing else cfg.ruler.unit)
        if unit != cfg.ruler.unit:
            raise ConfigError(
                f"a member cannot be in {unit!r} when the ruler is in "
                f"{cfg.ruler.unit!r}; thresholds are stored against the ruler, "
                "so a member in another unit would make them meaningless"
            )
        member = Member(
            name=name,
            variable=body.get("variable") or (existing.variable if existing else ""),
            unit=unit,
            leads=tuple(_int_list(body.get("leads"))),
            active_from=_parse_date(body.get("active_from")),
            active_to=_parse_date(body.get("active_to")),
        )
        updated = library.set_member(group_id, member)
        return {"config": library.summary(updated), "member": member.name}

    @app.post("/api/configs/{group_id}/roster/{name}/retire")
    def retire_member(group_id: str, name: str, body: dict = Body(default={})):
        on = _parse_date(body.get("on")) or date.today()
        updated = library.retire_member(group_id, name, on)
        return {"config": library.summary(updated), "member": name, "retired_on": on}

    @app.post("/api/configs/{group_id}/metrics", status_code=201)
    def append_metrics(group_id: str, body: dict = Body(...)):
        group = MetricGroup(
            family=_required(body, "family"),
            items=tuple(body.get("items") or ()),
            levels=tuple(float(x) for x in (body.get("levels") or ())),
            half_windows=tuple(int(x) for x in (body.get("half_windows") or ())),
        )
        updated = library.add_metrics(group_id, group)
        return {"config": library.summary(updated), "added": list(group.items)}

    # ================================================================ plan
    @app.post("/api/plan")
    def make_plan(body: dict = Body(default={})):
        """Preview.  Accepts a saved config id or an unsaved spec."""
        if body.get("config"):
            cfg = library.get(body["config"])
        elif body.get("spec"):
            cfg = VerificationConfig.from_dict(_strip_fingerprints(body["spec"]))
        else:
            raise ConfigError("pass either a saved config id or a spec")

        book = discover_runs(run_roots) if run_roots else None
        provider = access if body.get("probe") else None
        result = plan_tasks(cfg, book or [], catalog=_catalog_or_none(package_roots),
                            access=provider, probe=bool(body.get("probe") and provider))
        payload = result.as_dict()
        payload["warnings"] = _validate_against_catalog(cfg, package_roots)
        payload["runs_discovered"] = len(book) if book is not None else 0
        return payload

    # ================================================================ runs
    @app.post("/api/runs", status_code=202)
    def start_run(body: dict = Body(...)):
        cfg = library.get(_required(body, "config"))
        refs = runs_for(body)
        force = bool(body.get("force"))
        plan_payload = plan_tasks(
            cfg, discover_runs(run_roots) if run_roots else []
        ).as_dict()

        # Saying "nothing to do" beats saying "busy": one is about the request,
        # the other about the server, and the request's answer is the useful one.
        if not refs:
            return {
                "started": False,
                "reason": "nothing to do",
                "plan": plan_payload,
            }

        running = jobs.busy("run")
        if running:
            return {
                "started": False,
                "reason": "busy",
                "detail": "a run is already in progress; one at a time",
                "jobs": [j.id for j in running],
                "plan": plan_payload,
            }

        provider = make_access()
        root_dir = root

        def work(report):
            with SingleInstanceLock(root_dir):
                s = Store(root_dir / "verify.sqlite")
                try:
                    stats = run_tasks(provider, s, cfg, refs, force=force,
                                      on_progress=report)
                finally:
                    s.close()
            return {"stats": stats.as_dict(), "plan": plan_payload}

        job = jobs.submit("run", f"{cfg.group_id}: {len(refs)} run(s)", work,
                          group=cfg.group_id)
        return {"started": True, "job": job.id, "state": job.state,
                "runs": len(refs), "plan": plan_payload}

    @app.get("/api/jobs")
    def list_jobs():
        return {"jobs": jobs.list()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str, after: int = Query(0, ge=0)):
        return jobs.view(job_id, after)

    # =============================================================== query
    @app.get("/api/scores")
    def get_scores(request: Request):
        params = request.query_params
        config_id = params.get("config")
        method = params.get("method")
        if not method:
            raise QueryError("method is required")
        cfg = library.get(config_id) if config_id else None

        with store() as s:
            result = query_scores(
                s, cfg,
                method=method,
                s=_filters_from_query(params),
                g=params.getlist("g"),
                limit=int(params.get("limit") or 20000),
            )
        return result

    @app.get("/api/chart")
    def get_chart(request: Request):
        """The same query the table runs, drawn.

        A separate endpoint rather than a flag on ``/api/scores``: the page loads
        this with ``<img src>``, so it has to answer with an image and not with
        JSON, and it has to be able to fail as an image too.
        """
        from ..report import (chart_for, chart_panels, line_chart, mesh_for,
                              mesh_svg, small_multiples)

        params = request.query_params
        kind = (params.get("kind") or "line").lower()
        if kind not in ("line", "bar", "mesh"):
            raise QueryError(f"unknown chart kind {kind!r}; line, bar or mesh")
        method = params.get("method")
        if not method:
            raise QueryError("method is required")
        config_id = params.get("config")
        cfg = library.get(config_id) if config_id else None

        with store() as s:
            if kind == "mesh":
                svg = mesh_svg(mesh_for(
                    s, cfg,
                    method=method,
                    s=_filters_from_query(params),
                    time_dim=params.get("x") or "init_time",
                    lead_dim=params.get("series") or "lead_minutes",
                    panel_dims=("member", *params.getlist("panel")),
                ))
            else:
                panels = params.getlist("panel")
                if panels:
                    labels, charts = chart_panels(
                        s, cfg,
                        method=method,
                        s=_filters_from_query(params),
                        x=params.get("x") or "lead_minutes",
                        series=tuple(params.getlist("series")) or ("member",),
                        panel_dims=tuple(panels),
                    )
                    svg = small_multiples(
                        charts, labels,
                        kind="bar" if kind == "bar" else "line",
                        width=_int_param(params, "width", 820))
                else:
                    chart = chart_for(
                        s, cfg,
                        method=method,
                        s=_filters_from_query(params),
                        x=params.get("x") or "lead_minutes",
                        series=tuple(params.getlist("series")) or ("member",),
                    )
                    svg = line_chart(chart, width=_int_param(params, "width", 760),
                                     height=_int_param(params, "height", 440),
                                     kind="bar" if kind == "bar" else "line")
        return Response(
            content=svg,
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-store"},
        )

    # ============================================================== static
    if STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

        @app.get("/")
        def index():
            return FileResponse(str(STATIC_DIR / "index.html"))

    else:  # pragma: no cover - only when the package is installed oddly
        @app.get("/")
        def index_missing():
            return JSONResponse({"error": "the static files are missing"})

    app.state.library = library
    app.state.jobs = jobs
    app.state.host = host
    app.state.root = root
    return app


# ======================================================================
# shared helpers
# ======================================================================
def series_colours(cfg: VerificationConfig) -> dict[str, str]:
    """Which colour each member is drawn in, by roster order.

    Roster order and not the order the records came back in: hiding a model must
    not renumber the ones left and change every colour on the chart.
    """
    from ..report import PALETTE

    return {member.name: PALETTE[index % len(PALETTE)]
            for index, member in enumerate(cfg.members)}


def available_metrics(cfg: VerificationConfig) -> list[dict]:
    """Which stored quantities this config can currently answer for."""
    out = []
    for group in cfg.metrics:
        for item in group.items:
            out.append({
                "method": item,
                "family": group.family,
                "levels": list(group.levels),
                "half_windows": list(group.half_windows),
            })
    return out


def _catalog_or_none(roots):
    if not roots:
        return None
    catalog = discover_packages(roots)
    return catalog or None


def available_leads(catalog, book) -> dict[str, Any]:
    """Which lead times each model could be verified at, from its own runs.

    The forecast horizon in a run config is a *frame count*, so turning it into
    minutes needs one cadence — and there is exactly one, because a config has a
    single time step and every model on its roster runs on it. The source
    package declares it; a model's own declaration is the fallback.

    The union across a model's runs, not the intersection: a model whose first
    run had a shorter horizon can still be verified to the longer lead on the
    runs that had it. Which runs reach which lead is the planner's business, and
    it clips per run and says so in a note.
    """
    cadence = None
    if catalog is not None:
        for package in catalog.data_packages + catalog.model_packages:
            if package.cadence_minutes:
                cadence = int(package.cadence_minutes)
                break
    if book is None:
        return {"cadence_minutes": cadence, "by_model": {}, "note": "no run configs"}

    by_model: dict[str, list[int]] = {}
    for model_id in sorted(book.model_ids):
        leads: set[int] = set()
        for run in book.for_model(model_id):
            leads.update(run.leads_within_horizon(cadence or 0))
        by_model[model_id] = sorted(leads)
    note = None
    if cadence is None and by_model:
        note = ("no package declares a cadence, so a frame count cannot be turned "
                "into minutes")
    return {"cadence_minutes": cadence, "by_model": by_model, "note": note}


def fill_ruler_from_ground_truth(spec: dict, roots) -> dict:
    """Fill in the ruler fields the ground truth package can answer for.

    The page fills these boxes as soon as a ground truth is picked; this applies
    the same rule where the config is actually built, so a caller that posts
    nothing but ``ruler.ground_truth`` gets a usable config rather than a
    complaint about a unit it was never asked to look up.

    Two limits, both deliberate. Only *blanks* are filled, so an edited value is
    never overwritten. And only what the manifest actually declares is used: an
    extent or a resolution it does not declare is left alone, because the ruler
    needs one and a guess here would silently decide what the numbers mean.
    """
    if not roots:
        return spec
    ruler = spec.get("ruler")
    if not isinstance(ruler, Mapping):
        return spec
    truth = ruler.get("ground_truth")
    if not truth:
        return spec

    package = discover_packages(roots).get(str(truth))
    if package is None:
        return spec

    derived = ruler_defaults(package)
    filled = dict(ruler)
    for field, value in derived.items():
        if field == "variable" or value is None:
            continue                     # ``variable`` is not part of the ruler
        if filled.get(field) in (None, "", []):
            filled[field] = list(value) if isinstance(value, (list, tuple)) else value
    return {**spec, "ruler": filled}


def _validate_against_catalog(cfg: VerificationConfig, roots) -> list[str]:
    """Non-fatal complaints about a config, given what the platform ships.

    These are warnings and not refusals on purpose. The catalog is discovered,
    not authoritative: a config may legitimately name a package that lives
    outside the roots this server was told about, and refusing it would make the
    interface unusable the moment discovery is incomplete.
    """
    catalog = _catalog_or_none(roots)
    if catalog is None:
        return []

    problems: list[str] = []
    data = catalog.get(cfg.ruler.ground_truth)
    if data is None:
        problems.append(
            f"truth source {cfg.ruler.ground_truth!r} is not among the "
            f"{len(catalog)} package(s) found"
        )
    else:
        cadence = data.cadence_minutes
        if cadence and cadence != cfg.ruler.time_step_minutes:
            problems.append(
                f"the ruler steps every {cfg.ruler.time_step_minutes} min but "
                f"{cfg.ruler.ground_truth} runs at {cadence} min"
            )

    for member in cfg.members:
        if catalog.get(member.name) is None:
            problems.append(
                f"member {member.name!r} is not among the packages found"
            )
    return problems


def _strip_fingerprints(spec: dict) -> dict:
    """A spec round-tripped through the API may carry a computed fingerprint."""
    out = json.loads(json.dumps(spec))
    ruler = out.get("ruler")
    if isinstance(ruler, dict):
        ruler.pop("fingerprint", None)
    return out


def _int_param(params, name: str, fallback: int) -> int:
    """A size from the query string, bounded.

    The value ends up in an SVG header and in the arithmetic behind every
    coordinate, so it is clamped rather than trusted: an unbounded width is a
    cheap way to make the service allocate.
    """
    try:
        value = int(params.get(name) or fallback)
    except (TypeError, ValueError):
        raise QueryError(f"{name} must be a whole number") from None
    return max(200, min(value, 4000))


def _required(body: dict, key: str) -> str:
    value = body.get(key)
    if not value:
        raise ConfigError(f"{key} is required")
    return str(value)


def _int_list(value) -> list[int]:
    if value is None or value == "":
        raise ConfigError("leads are required, e.g. [10, 30, 60]")
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",") if p.strip()]
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        parts = [value]
    try:
        return [int(p) for p in parts]
    except (TypeError, ValueError):
        raise ConfigError(f"leads must be whole minutes, got {value!r}") from None


def _parse_date(value) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value))


def _parse_time(value) -> datetime:
    raw = str(value).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _filters_from_query(params) -> dict:
    """Query-string filters, in MetEva's vocabulary."""
    out: dict[str, Any] = {}
    lead_from, lead_to = params.get("lead_from"), params.get("lead_to")
    if lead_from or lead_to:
        # A two-element list is a range to ``_translate``, which is what "from X
        # to Y" means and what a single ``lead=`` value does not: that one is a
        # list. Keeping them apart matters -- ``lead=10,60`` is the two leads 10
        # and 60, and ``lead_from=10&lead_to=60`` is every lead between them.
        out["dtime"] = [_number(lead_from, 0), _number(lead_to, 10 ** 9)]
    elif params.get("lead"):
        out["dtime"] = _numbers(params["lead"])
    if params.get("level"):
        out["level"] = _numbers(params["level"])
    if params.get("half_window"):
        out["half_window"] = _numbers(params["half_window"])
    if params.getlist("member"):
        out["member"] = params.getlist("member")
    time_from, time_to = params.get("time_from"), params.get("time_to")
    if time_from or time_to:
        out["time"] = [time_from or "0001-01-01T00:00:00Z",
                       time_to or "9999-12-31T23:59:59Z"]
    init_from, init_to = params.get("init_from"), params.get("init_to")
    if init_from or init_to:
        out["init_time"] = [init_from or "0001-01-01T00:00:00Z",
                            init_to or "9999-12-31T23:59:59Z"]
    return out


def _number(text, fallback):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return fallback
    return int(value) if value.is_integer() else value


def _numbers(text: str):
    parts = [p.strip() for p in str(text).split(",") if p.strip()]
    out = []
    for part in parts:
        try:
            number = float(part)
        except ValueError:
            out.append(part)
            continue
        out.append(int(number) if number.is_integer() else number)
    return out
