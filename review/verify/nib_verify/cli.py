"""Command line entry point.

Three promises to whatever calls us, because a handler is expected to call us on
a timer and has to be able to tell what happened:

1. **Exit codes.** 0 means everything asked for succeeded, including "there was
   nothing to do". Non-zero means something failed.
2. **Progress.** Human-readable lines on stdout. Follows ``NIB_PROGRESS``:
   ``quiet`` prints nothing, ``plain`` (the default) prints one line per task,
   ``json`` prints one JSON object per line.
3. **A lock.** ``--root`` holds a single-instance lock, so a slow run cannot be
   started twice.

Where the platform already knows a path we read it from there rather than
inventing one: the host's ``config.yaml`` supplies the data root, and packages
and runs are found by walking a root the caller names.  See
:mod:`nib_verify.catalog` for why discovery is by content and not by directory.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from .access.base import describe_capabilities
from .access.factory import NO_ADDRESS, PROVIDERS, build_access
from .access.scenario import catalogue as scenario_catalogue
from .access.scenario import names as scenario_names
from .catalog.hostconfig import HostConfigError, load as load_host
from .catalog.matching import check_pair
from .catalog.packages import Catalog, discover as discover_packages
from .catalog.runs import discover as discover_runs
from .config.schema import (
    ConfigError,
    Member,
    VerificationConfig,
    dump,
    load,
)
from .core.metrics import list_metrics as metric_catalog
from .core.planner import plan as plan_tasks
from .core.planner import planned_runs
from .query.service import QueryError, available_expansions, query_scores
from .runner.executor import run_tasks
from .runner.lock import Locked, SingleInstanceLock
from .store.db import Store

EXIT_OK = 0
EXIT_FAILURE = 1
EXIT_USAGE = 2

UTC = timezone.utc


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (ConfigError, QueryError, Locked, HostConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


# ======================================================================
# host
# ======================================================================
def cmd_host_show(args) -> int:
    host = load_host(args.host_config)
    if args.json:
        print(json.dumps(host.as_dict(), indent=2))
        return EXIT_OK

    if host.source:
        print(f"config   {host.source}")
    else:
        print("config   (none found; using the handler's own defaults)")
    print(f"handler  {host.handler_host}:{host.handler_port}")
    print(f"review   {host.review_host}:{host.review_port}   <- where we serve")
    print(f"artifacts {host.artifacts}")
    print(f"data_root {host.data_root or '(not set)'}")
    return EXIT_OK


# ======================================================================
# catalog
# ======================================================================
def cmd_catalog_packages(args) -> int:
    catalog = _load_catalog(args)
    if args.json:
        print(json.dumps(catalog.as_dict(), indent=2))
        return EXIT_OK

    _print_roots(catalog.roots, catalog.skipped)
    if not catalog:
        print("no package manifests found")
        return EXIT_OK

    if catalog.data_packages:
        print("data")
        for pkg in catalog.data_packages:
            print(f"  {pkg.describe()}")
            for spec in pkg.fields:
                print(f"      {spec.describe()}")
    if catalog.model_packages:
        print("models")
        for pkg in catalog.model_packages:
            print(f"  {pkg.describe()}")
            for spec in pkg.input_fields:
                print(f"      in   {spec.describe()}")
            for spec in pkg.output_fields:
                print(f"      out  {spec.describe()}")
    unknown = [p for p in catalog if p.kind == "unknown"]
    if unknown:
        print("unclassified")
        for pkg in unknown:
            print(f"  {pkg.describe()}")
    return EXIT_OK


def cmd_catalog_runs(args) -> int:
    book = discover_runs(_resolve_roots(args.runs, args, "runs"))
    if args.json:
        print(json.dumps(book.as_dict(), indent=2))
        return EXIT_OK

    _print_roots(book.roots, book.skipped)
    if not book:
        print("no run configs found")
        return EXIT_OK

    first, last = book.span()
    if first:
        print(f"{len(book)} run(s) from {_stamp(first)} to {_stamp(last)}")
    else:
        print(f"{len(book)} run(s)")
    print(f"models: {', '.join(sorted(book.model_ids))}")
    print(f"data:   {', '.join(sorted(book.data_ids))}")
    print()
    for run in book:
        print(f"  {run.describe()}")
    return EXIT_OK


def cmd_catalog_check(args) -> int:
    catalog = _load_catalog(args)
    problems = catalog.problems()
    _print_roots(catalog.roots, catalog.skipped)
    if not problems:
        print(f"{len(catalog)} package(s), no problems")
        return EXIT_OK
    for problem in problems:
        print(f"  ! {problem}", file=sys.stderr)
    if args.json:
        print(json.dumps({"problems": problems}, indent=2))
    return EXIT_FAILURE


def cmd_catalog_match(args) -> int:
    catalog = _load_catalog(args)
    if args.data and args.model:
        pairs = [(args.data, args.model)]
    else:
        pairs = [(d.id, m.id) for d in catalog.data_packages for m in catalog.model_packages]

    failures = 0
    rows = []
    for data_id, model_id in pairs:
        data = catalog.get(data_id)
        model = catalog.get(model_id)
        if data is None:
            print(f"error: no package {data_id!r}", file=sys.stderr)
            return EXIT_FAILURE
        if model is None:
            print(f"error: no package {model_id!r}", file=sys.stderr)
            return EXIT_FAILURE
        result = check_pair(data, model)
        rows.append(result.as_dict())
        if not result.ok:
            failures += 1
        if not args.json:
            print(result.describe())

    if args.json:
        print(json.dumps({"pairs": rows, "failures": failures}, indent=2))
    return EXIT_OK if failures == 0 else EXIT_FAILURE


# ======================================================================
# config
# ======================================================================
def cmd_config_create(args) -> int:
    cfg = load(args.from_file)
    if not args.force and Path(args.out).exists():
        print(f"error: {args.out} exists; pass --force to overwrite", file=sys.stderr)
        return EXIT_FAILURE
    dump(cfg, args.out)
    print(f"wrote {args.out}")
    _print_config(cfg)
    return EXIT_OK


def cmd_config_show(args) -> int:
    cfg = load(args.config)
    if args.json:
        print(cfg.to_json())
    else:
        _print_config(cfg)
    return EXIT_OK


def cmd_config_list(args) -> int:
    directory = Path(args.directory)
    if not directory.is_dir():
        print(f"error: {directory} is not a directory", file=sys.stderr)
        return EXIT_FAILURE
    found = []
    for path in sorted(list(directory.glob("*.json")) + list(directory.glob("*.yaml"))
                       + list(directory.glob("*.yml"))):
        try:
            found.append(load(path))
        except ConfigError as exc:
            print(f"  ! {path.name}: {exc}", file=sys.stderr)
    if args.json:
        print(json.dumps([c.to_dict() for c in found], indent=2))
        return EXIT_OK
    if not found:
        print(f"no verification configs in {directory}")
        return EXIT_OK
    print(f"{'id':<24} {'ruler':<18} {'members':>7} {'metrics':>7}  name")
    for cfg in found:
        metrics = sum(len(g.items) for g in cfg.metrics)
        print(f"{cfg.group_id:<24} {cfg.ruler_fingerprint:<18} "
              f"{len(cfg.members):>7} {metrics:>7}  {cfg.name}")
    return EXIT_OK


def cmd_config_delete(args) -> int:
    path = Path(args.config)
    if not path.is_file():
        print(f"error: {path} does not exist", file=sys.stderr)
        return EXIT_FAILURE
    cfg = load(path)
    path.unlink()
    print(f"deleted {path}")

    store_path = Path(args.root) / "verify.sqlite"
    if store_path.exists():
        store = Store(store_path)
        try:
            removed = store.delete_group(cfg.group_id)
        finally:
            store.close()
        if removed:
            summary = ", ".join(f"{k}={v}" for k, v in sorted(removed.items()))
            print(f"removed stored rows for {cfg.group_id!r}: {summary}")
        else:
            print(f"nothing stored for {cfg.group_id!r}")
    return EXIT_OK


def cmd_config_roster(args) -> int:
    cfg = load(args.config)
    if args.roster_action == "add":
        member = Member(
            name=args.member,
            variable=args.variable or cfg.ruler.variable,
            unit=args.unit or cfg.ruler.unit,
            leads=tuple(_lead_list(args.leads)),
            active_from=_date(args.active_from),
            active_to=_date(args.active_to),
        )
        updated = cfg.with_member(member)
        change = f"added {member.name} with leads {list(member.leads)}"
    else:
        on = _date(args.on) or date.today()
        existing = cfg.member(args.member)
        if existing.active_to and existing.active_to == on:
            print(f"{existing.name} is already retired on {on}; nothing to do")
            return EXIT_OK
        member = replace(existing, active_to=on)
        updated = cfg.with_member(member, retire_on=on)
        change = f"retired {member.name} on {on}"

    problems = updated.allows_modification_of(cfg)
    if problems:
        print(f"error: this change needs a new config ({problems})", file=sys.stderr)
        return EXIT_FAILURE

    dump(updated, args.config)
    print(f"{change}; wrote {args.config}")
    return EXIT_OK


def cmd_config_validate(args) -> int:
    cfg = load(args.config)
    access = _make_access(args)
    caps = access.capabilities()

    problems = []
    if cfg.ruler.time_step_minutes != caps.cadence_minutes:
        problems.append(
            f"ruler step {cfg.ruler.time_step_minutes} min but the provider "
            f"serves {caps.cadence_minutes} min"
        )
    if cfg.ruler.ground_truth not in caps.observation_names:
        problems.append(
            f"truth source {cfg.ruler.ground_truth!r} is not offered; "
            f"provider has {list(caps.observation_names)}"
        )
    for member in cfg.members:
        if member.name not in caps.model_ids:
            problems.append(
                f"member {member.name!r} is unknown; provider has {list(caps.model_ids)}"
            )
            continue
        unit = caps.unit_of(member.name)
        if unit and unit != cfg.ruler.unit:
            problems.append(
                f"member {member.name!r} declares {member.unit!r} but the "
                f"provider says {unit!r}"
            )
        leads = set(access.list_predictions(member.name, _sample_time(access)))
        missing = sorted(set(member.leads) - leads)
        if missing:
            problems.append(f"member {member.name!r} does not produce leads {missing}")

    if problems:
        for problem in problems:
            print(f"  ! {problem}", file=sys.stderr)
        print(f"config {cfg.group_id!r}: {len(problems)} problem(s)", file=sys.stderr)
        return EXIT_FAILURE

    print(f"config {cfg.group_id!r}: ok  (ruler {cfg.ruler_fingerprint})")
    return EXIT_OK


# ======================================================================
# metrics
# ======================================================================
def cmd_metrics_list(args) -> int:
    rows = metric_catalog()
    if args.json:
        print(json.dumps(rows, indent=2))
        return EXIT_OK
    print(f"{'method':<8} {'family':<10} {'parameters':<22} label")
    for row in rows:
        params = ", ".join(row["params"]) or "-"
        print(f"{row['method']:<8} {row['family']:<10} {params:<22} {row['label']}")
    return EXIT_OK


# ======================================================================
# task
# ======================================================================
def cmd_task_plan(args) -> int:
    cfg = load(args.config)
    catalog = _load_catalog(args, required=False)
    book = discover_runs(_resolve_roots(args.runs, args, "runs"))
    access = _make_access(args) if args.probe else None

    result = plan_tasks(cfg, book, catalog=catalog or None, access=access, probe=args.probe)

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return EXIT_OK

    print(f"config   {cfg.group_id}  (ruler {cfg.ruler_fingerprint})")
    print(f"runs     {len(book)} discovered")
    print(f"tasks    {len(result.tasks)}"
          + (f"  over {len(result.init_times())} init time(s)" if result.tasks else ""))
    if result.tasks:
        print(f"members  {', '.join(result.members)}")
        print(f"leads    {result.leads}")
    if result.rejections:
        print()
        print("rejected runs")
        for reason, count in sorted(result.reasons().items()):
            print(f"  {count:>3}  {reason:<16} {reason_text(reason)}")
        if args.verbose:
            print()
            for rejection in result.rejections:
                when = _stamp(rejection.init_time) if rejection.init_time else "?"
                print(f"  {when}  {rejection.model_id}: {rejection.detail}")
    if result.notes and args.verbose:
        print()
        for note in result.notes:
            print(f"  note: {note}")
    if result.coverage:
        print()
        print("coverage")
        for key, value in result.coverage.items():
            if key == "missing_examples":
                continue
            print(f"  {key}: {value}")
    return EXIT_OK if result.tasks or not book else EXIT_FAILURE


def cmd_run(args) -> int:
    root = Path(args.root)
    cfg = load(args.config)
    access = _make_access(args)
    progress = args.progress or os.environ.get("NIB_PROGRESS", "plain")

    inits = [_parse_time(x) for x in (args.init or [])]
    if not inits:
        book = discover_runs(_resolve_roots(args.runs, args, "runs"))
        planned = plan_tasks(cfg, book)
        runs = planned_runs(planned)
    else:
        runs = [
            _run_ref(m.name, t) for t in inits for m in cfg.members
        ]

    if not runs:
        if progress != "quiet":
            print("nothing to do")
        return EXIT_OK

    with SingleInstanceLock(root):
        store = Store(root / "verify.sqlite")
        try:
            stats = run_tasks(
                access, store, cfg, runs,
                force=args.force,
                on_progress=_progress_printer(progress),
            )
        finally:
            store.close()

    if progress != "quiet":
        print(
            f"tasks: {stats.total} total, {stats.ran} ran, {stats.skipped} skipped, "
            f"{stats.unavailable} unavailable, {stats.failed} failed"
        )
    for message in stats.messages:
        print(f"  {message}", file=sys.stderr)

    return EXIT_OK if stats.failed == 0 else EXIT_FAILURE


# ======================================================================
# query
# ======================================================================
def cmd_query(args) -> int:
    root = Path(args.root)
    cfg = load(args.config) if args.config else None
    store = Store(root / "verify.sqlite")
    try:
        result = query_scores(
            store, cfg,
            method=args.method,
            s=_parse_filters(args.s or []),
            g=args.g or None,
            limit=args.limit,
        )
    finally:
        store.close()

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return EXIT_OK

    print(f"# {result['meta']['label']} ({result['meta']['method']})")
    if cfg is not None:
        print(f"# config {cfg.group_id}, ruler {cfg.ruler_fingerprint}")
    print(f"# dims: {', '.join(result['dims']) or '(none)'}")
    print("\t".join(result["dims"] + ["value", "n_valid"]))
    for record in result["records"]:
        cells = [_fmt(record[d]) for d in result["dims"]]
        cells += [_fmt(record["value"]), _fmt(record["n_valid"])]
        print("\t".join(cells))
    print(f"# {result['sample']['n_records']} records, "
          f"{result['sample']['n_valid']:.0f} valid samples", file=sys.stderr)
    return EXIT_OK


def cmd_expansions(args) -> int:
    print("\n".join(available_expansions(args.method)))
    return EXIT_OK


def cmd_chart(args) -> int:
    """Draw one query as SVG.

    Writes to stdout by default, so ``nib-verify chart ... > curve.svg`` works;
    ``--out`` is for when this is one step in a script that has other things to
    say on stdout.
    """
    from .report import chart_for, line_chart

    root = Path(args.root)
    cfg = load(args.config) if args.config else None
    store = Store(root / "verify.sqlite")
    try:
        chart = chart_for(
            store, cfg,
            method=args.method,
            s=_parse_filters(args.s or []),
            x=args.x,
            series=tuple(args.series or ()),
        )
    finally:
        store.close()

    svg = line_chart(chart, width=args.width, height=args.height,
                     y_min=args.y_min, y_max=args.y_max)
    if args.out:
        Path(args.out).write_text(svg, encoding="utf-8")
        if args.progress != "quiet":
            drawn = sum(1 for s in chart.series if s.defined)
            print(f"{args.out}: {drawn} series, {len(chart.values())} points",
                  file=sys.stderr)
    else:
        sys.stdout.write(svg)
    return EXIT_OK


# ======================================================================
# serve
# ======================================================================
def cmd_datafeed_serve(args) -> int:
    """Serve the selected provider at the contract's addresses.

    Loopback by default, and no refusal-to-start when the provider is unhappy:
    the service comes up and answers ``/health`` so whoever started it can see
    what it is serving. A data service that refuses to bind tells you less than
    one that binds and reports.
    """
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        print("error: the field service needs fastapi and uvicorn: "
              "pip install -e '.[web]'", file=sys.stderr)
        return EXIT_FAILURE

    from .access.mockfeed import create_data_app

    try:
        access = _make_access(args)
    except (ConfigError, HostConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE

    app = create_data_app(access)
    if args.progress != "quiet":
        caps = access.capabilities()
        print(f"serving {describe_capabilities(caps)}")
        print(f"on http://{args.host}:{args.port}  —  try "
              f"http://{args.host}:{args.port}/capabilities")

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return EXIT_OK


def cmd_datafeed_check(args) -> int:
    """Run the checklist in spec/field-access.md against a provider.

    Either a local one (``--access mock``) or a running service (``--url``).
    The exit code is the answer: this is meant to be usable as a gate before a
    long run, or in CI against the platform's provider once it exists.
    """
    from .access.conformance import check_provider

    url = getattr(args, "url", None)
    if url:
        access = build_access("http", base_url=url)
    else:
        try:
            access = _make_access(args)
        except (ConfigError, HostConfigError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return EXIT_FAILURE

    try:
        report = check_provider(access)
    finally:
        close = getattr(access, "close", None)
        if close:
            close()

    if args.json:
        print(json.dumps(report.as_dict(), indent=2, default=str))
    else:
        print(report.render())

    if url:
        print(f"\nsource: {url}")
    return EXIT_OK if report.ok else EXIT_FAILURE


def cmd_serve(args) -> int:
    """Start the review page where ``config.yaml`` says it belongs.

    `AGENTS.md`: the review page runs on the host, binds loopback only, and
    reads its bind address from ``config.yaml``. So the address is not chosen
    here — it is read, and a non-loopback address is refused rather than
    quietly obeyed.
    """
    try:
        import uvicorn  # noqa: F401
    except ModuleNotFoundError:
        print("error: the web service needs fastapi and uvicorn: "
              "pip install -e '.[web]'", file=sys.stderr)
        return EXIT_FAILURE

    from .server import create_app

    host_cfg = load_host(args.host_config)
    bind_host, bind_port = host_cfg.review_address
    if args.port:
        bind_port = args.port

    if not _is_loopback(bind_host) and not args.allow_remote:
        print(
            f"error: review.host is {bind_host}, which is not loopback. "
            "config.yaml and AGENTS.md both put the review page on 127.0.0.1; "
            "pass --allow-remote if you really mean to expose it.",
            file=sys.stderr,
        )
        return EXIT_FAILURE

    access = None
    access_error = None
    try:
        access = _make_access(args)
    except (ConfigError, HostConfigError, NotImplementedError) as exc:
        access_error = str(exc)

    app = create_app(
        root=args.root,
        configs=args.configs,
        packages=args.packages or [],
        runs=args.runs or [],
        host=host_cfg,
        access=access,
        access_error=access_error,
    )

    if args.progress != "quiet":
        print(f"NiB verification on http://{bind_host}:{bind_port}")
        print(f"  configs   {Path(args.configs).resolve()}")
        print(f"  root      {Path(args.root).resolve()}")
        print(f"  packages  {[str(p) for p in (args.packages or [])] or '(none)'}")
        print(f"  runs      {[str(p) for p in (args.runs or [])] or '(none)'}")
        print(f"  provider  {'ready' if access else f'unavailable: {access_error}'}")

    import uvicorn

    uvicorn.run(app, host=bind_host, port=bind_port,
                log_level=args.log_level or "info")
    return EXIT_OK


def _is_loopback(host: str) -> bool:
    if host in ("localhost", "::1"):
        return True
    try:
        import ipaddress

        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


# ======================================================================
# parser
# ======================================================================
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nib-verify", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    def add_access(sp):
        sp.add_argument("--access", default="mock", choices=PROVIDERS,
                        help="which field provider to use: mock (generated), "
                             "replay (.npy on disk), http/real (a service). "
                             "Default: mock")
        sp.add_argument("--scenario", metavar="NAME",
                        help="a named bundle of mock settings, for --access mock:\n"
                             + scenario_catalogue(sep="; "))
        sp.add_argument("--data-url", metavar="URL",
                        help="address of a field service speaking the HTTP "
                             "contract; required by --access http/real")
        sp.add_argument("--mock", action="append", metavar="KEY=VALUE",
                        help="option for the mock provider, e.g. "
                             "--mock peak=250 --mock unit='kg m-2' "
                             "(replay takes --mock root=DIR)")

    def add_host(sp):
        sp.add_argument("--host-config", help="path to the platform's config.yaml")

    def add_sources(sp, *, root_required: bool = False):
        add_host(sp)
        sp.add_argument("--packages", action="append", metavar="DIR",
                        help="root to scan for package manifests; repeatable")
        sp.add_argument("--runs", action="append", metavar="DIR",
                        help="root to scan for run configs; repeatable")
        sp.add_argument("--root", required=root_required, metavar="DIR",
                        help="verification root: holds the store and the lock"
                             ", and doubles as a scan root")

    # -- host -----------------------------------------------------------
    h = sub.add_parser("host", help="the platform's own settings")
    hs = h.add_subparsers(dest="subcommand", required=True)
    hshow = hs.add_parser("show", help="print the resolved host settings")
    add_host(hshow)
    hshow.add_argument("--json", action="store_true")
    hshow.set_defaults(handler=cmd_host_show)

    # -- catalog --------------------------------------------------------
    c = sub.add_parser("catalog", help="what the platform has")
    cs = c.add_subparsers(dest="subcommand", required=True)

    pkgs = cs.add_parser("packages", help="find package manifests")
    add_sources(pkgs)
    pkgs.add_argument("--json", action="store_true")
    pkgs.set_defaults(handler=cmd_catalog_packages)

    runsp = cs.add_parser("runs", help="find run configs")
    add_sources(runsp)
    runsp.add_argument("--json", action="store_true")
    runsp.set_defaults(handler=cmd_catalog_runs)

    check = cs.add_parser("check", help="structural complaints about the packages")
    add_sources(check)
    check.add_argument("--json", action="store_true")
    check.set_defaults(handler=cmd_catalog_check)

    match = cs.add_parser("match", help="which data-model pairs are allowed")
    add_sources(match)
    match.add_argument("--data")
    match.add_argument("--model")
    match.add_argument("--json", action="store_true")
    match.set_defaults(handler=cmd_catalog_match)

    # -- config ---------------------------------------------------------
    cfgp = sub.add_parser("config", help="create, inspect and validate configs")
    cfgs = cfgp.add_subparsers(dest="subcommand", required=True)

    create = cfgs.add_parser("create", help="write a config from a spec file")
    create.add_argument("out")
    create.add_argument("--from", dest="from_file", required=True, metavar="SPEC")
    create.add_argument("--force", action="store_true")
    create.set_defaults(handler=cmd_config_create)

    show = cfgs.add_parser("show", help="print a config")
    show.add_argument("config")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_config_show)

    listing = cfgs.add_parser("list", help="list the configs in a directory")
    listing.add_argument("directory")
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(handler=cmd_config_list)

    delete = cfgs.add_parser("delete", help="remove a config and its stored quantities")
    delete.add_argument("config")
    delete.add_argument("--root", required=True)
    delete.set_defaults(handler=cmd_config_delete)

    validate = cfgs.add_parser("validate", help="check a config against the provider")
    validate.add_argument("config")
    add_access(validate)
    validate.set_defaults(handler=cmd_config_validate)

    roster = cfgs.add_parser("roster", help="add or retire a member")
    roster.add_argument("roster_action", choices=("add", "retire"))
    roster.add_argument("config")
    roster.add_argument("--member", required=True)
    roster.add_argument("--leads", help="comma-separated minutes, for add")
    roster.add_argument("--variable")
    roster.add_argument("--unit")
    roster.add_argument("--active-from", dest="active_from")
    roster.add_argument("--active-to", dest="active_to")
    roster.add_argument("--on", help="retirement date, for retire")
    roster.set_defaults(handler=cmd_config_roster)

    # -- metrics --------------------------------------------------------
    m = sub.add_parser("metrics", help="what can be computed")
    ms = m.add_subparsers(dest="subcommand", required=True)
    mlist = ms.add_parser("list", help="list available metrics")
    mlist.add_argument("--json", action="store_true")
    mlist.set_defaults(handler=cmd_metrics_list)

    # -- task -----------------------------------------------------------
    t = sub.add_parser("task", help="plan and run verification tasks")
    ts = t.add_subparsers(dest="subcommand", required=True)

    def add_run_args(sp):
        sp.add_argument("--config", required=True)
        add_sources(sp, root_required=True)
        add_access(sp)
        sp.add_argument("--init", action="append",
                        help="init time, ISO-8601 UTC; repeat for several. "
                             "Omit to take them from the discovered run configs")
        sp.add_argument("--force", action="store_true",
                        help="recompute tasks already recorded as done")
        sp.add_argument("--progress", choices=("quiet", "plain", "json"))
        sp.set_defaults(handler=cmd_run)

    add_run_args(ts.add_parser("run", help="execute tasks"))
    add_run_args(sub.add_parser("run", help="alias for `task run`"))

    def add_plan_args(sp):
        sp.add_argument("--config", required=True)
        add_sources(sp)
        add_access(sp)
        sp.add_argument("--probe", action="store_true",
                        help="ask the provider which frames it can serve")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--verbose", action="store_true")
        sp.set_defaults(handler=cmd_task_plan)

    add_plan_args(ts.add_parser("plan", help="what this config would do, and what it cannot"))
    add_plan_args(sub.add_parser("plan", help="alias for `task plan`"))

    # -- query ----------------------------------------------------------
    q = sub.add_parser("query", help="aggregate stored quantities")
    q.add_argument("--method", required=True)
    q.add_argument("--config")
    q.add_argument("--root", required=True)
    q.add_argument("-s", action="append", metavar="KEY=VALUE",
                   help="filter: time / dtime / member / level / half_window")
    q.add_argument("-g", action="append",
                   help="expansion dimension; repeat, order matters")
    q.add_argument("--limit", type=int, default=20000)
    q.add_argument("--json", action="store_true")
    q.set_defaults(handler=cmd_query)

    e = sub.add_parser("expansions", help="which dimensions a metric can expand by")
    e.add_argument("--method", required=True)
    e.set_defaults(handler=cmd_expansions)

    # -- chart ----------------------------------------------------------
    c = sub.add_parser("chart", help="draw one query as an SVG line chart")
    c.add_argument("--method", required=True)
    c.add_argument("--config")
    c.add_argument("--root", required=True)
    c.add_argument("-s", action="append", metavar="KEY=VALUE",
                   help="filter: time / dtime / member / level / half_window")
    c.add_argument("-x", dest="x", default="lead_minutes", metavar="COLUMN",
                   help="column along the x axis (default: lead_minutes)")
    c.add_argument("--series", action="append", metavar="COLUMN",
                   help="column that separates one line from another; "
                        "repeat to cross two (default: member)")
    c.add_argument("--out", metavar="FILE", help="write here instead of stdout")
    c.add_argument("--width", type=int, default=760)
    c.add_argument("--height", type=int, default=440)
    c.add_argument("--y-min", dest="y_min", type=float,
                   help="fix the y axis instead of fitting it to the data")
    c.add_argument("--y-max", dest="y_max", type=float)
    c.add_argument("--progress", choices=("quiet", "plain"), default="plain")
    c.set_defaults(handler=cmd_chart)

    # -- datafeed -------------------------------------------------------
    # The simulated data interface. Serves any provider at the addresses in
    # spec/field-access-http.md, so the client can be built and tested before
    # the platform's own service exists -- and so a team implementing it has
    # something running to compare against.
    df = sub.add_parser("datafeed", help="the field service, and its conformance")
    dfs = df.add_subparsers(dest="subcommand", required=True)

    dfs_serve = dfs.add_parser("serve", help="serve a provider over HTTP")
    add_access(dfs_serve)
    dfs_serve.add_argument("--host", default="127.0.0.1",
                           help="bind address (default: 127.0.0.1)")
    dfs_serve.add_argument("--port", type=int, default=8770,
                           help="bind port (default: 8770)")
    dfs_serve.add_argument("--log-level", dest="log_level", default="info",
                           choices=("critical", "error", "warning", "info", "debug"))
    dfs_serve.add_argument("--progress", choices=("quiet", "plain"), default="plain")
    dfs_serve.set_defaults(handler=cmd_datafeed_serve)

    dfs_check = dfs.add_parser(
        "check", help="run the contract's conformance checklist")
    dfs_check.add_argument("--url", metavar="URL",
                           help="check a running service instead of a local provider")
    add_access(dfs_check)
    dfs_check.add_argument("--json", action="store_true")
    dfs_check.set_defaults(handler=cmd_datafeed_check)

    # -- serve ----------------------------------------------------------
    s = sub.add_parser("serve", help="start the review page")
    s.add_argument("--root", required=True, help="verification root: store and lock")
    s.add_argument("--configs", required=True, metavar="DIR",
                   help="directory holding the verification configs")
    s.add_argument("--packages", action="append", metavar="DIR",
                   help="root to scan for package manifests; repeatable")
    s.add_argument("--runs", action="append", metavar="DIR",
                   help="root to scan for run configs; repeatable")
    add_host(s)
    add_access(s)
    s.add_argument("--port", type=int, help="override review.port from config.yaml")
    s.add_argument("--allow-remote", action="store_true",
                   help="bind a non-loopback address (config.yaml says loopback)")
    s.add_argument("--log-level", dest="log_level",
                   choices=("critical", "error", "warning", "info", "debug"))
    s.add_argument("--progress", choices=("quiet", "plain"), default="plain")
    s.set_defaults(handler=cmd_serve)

    return p


# ======================================================================
# plumbing
# ======================================================================
def _load_catalog(args, *, required: bool = True) -> Catalog:
    roots = _resolve_roots(args.packages, args, "packages", required=required)
    if not roots:
        return Catalog()
    catalog = discover_packages(roots)
    if required and not catalog:
        raise HostConfigError(
            f"no package manifests found under {[str(r) for r in roots]}. "
            "Point --packages at a directory that has them."
        )
    return catalog


def _resolve_roots(explicit, args, label: str, *, required: bool = True) -> list[Path]:
    """``--packages``/``--runs``, else ``--root``, else the host's data root.

    The last step is the only case where a path is not given to us, and it reads
    the platform's own ``config.yaml`` rather than guessing. If that has no data
    root this fails — as `AGENTS.md` requires — and it says *which* lookup could
    not be answered, so the message points at the flag that would fix it.
    """
    if explicit:
        return [Path(p) for p in explicit]
    root = getattr(args, "root", None)
    if root:
        return [Path(root)]

    host = load_host(getattr(args, "host_config", None))
    if host.data_root is None or not host.data_root.exists():
        if not required:
            return []
        raise HostConfigError(
            f"nowhere to look for {label}: pass --{label} DIR, or --root DIR, "
            "or set paths.data_root in config.yaml. "
            "This module does not guess a host path."
        )
    return [host.data_root]


def _make_access(args):
    kind = getattr(args, "access", "mock")
    kwargs = _mock_kwargs(getattr(args, "mock", None) or [], kind)
    if kind == "replay" and "root" not in kwargs:
        raise ConfigError("the replay provider needs --mock root=DIR")
    if kind in ("http", "real"):
        url = getattr(args, "data_url", None)
        if not url:
            raise ConfigError(NO_ADDRESS)
        kwargs["base_url"] = url

    name = getattr(args, "scenario", None)
    if name:
        if kind != "mock":
            raise ConfigError(
                f"--scenario {name!r} is a bundle of mock settings, and this run "
                f"uses --access {kind}. Drop the scenario, or pass its settings "
                f"through --mock."
            )
        kwargs["scenario"] = name

    try:
        return build_access(kind, **kwargs)
    except ValueError as exc:
        # A bad scenario name, a bad --mock value: both are the caller's typo,
        # and neither should reach the user as a traceback.
        raise ConfigError(str(exc)) from exc


def _mock_kwargs(pairs: list[str], kind: str = "mock") -> dict:
    """Turn ``--mock key=value`` pairs into constructor arguments.

    Types come from the constructor's own defaults, so the CLI never has to keep
    a second list of what each option means. The constructor is the *selected
    provider's*: checking every key against the synthetic one meant the help
    could say "replay takes --mock root=DIR" while `root` was rejected before it
    could ever be used.
    """
    if not pairs:
        return {}
    import inspect

    params = dict(inspect.signature(_provider_class(kind).__init__).parameters)
    params.pop("self", None)
    out: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep:
            raise ConfigError(f"expected KEY=VALUE, got {pair!r}")
        if key not in params:
            known = ", ".join(sorted(params))
            raise ConfigError(
                f"unknown --mock option {key!r} for --access {kind}; known: {known}"
            )
        out[key] = _coerce_like(raw, params[key].default)
    return out


def _provider_class(kind: str):
    """The class ``--mock`` is validating against."""
    from .access.http import HttpAccess
    from .access.replay import ReplayAccess
    from .access.synthetic import SyntheticAccess

    return {"mock": SyntheticAccess, "replay": ReplayAccess}.get(kind, HttpAccess)


def _coerce_like(raw: str, default):
    if isinstance(default, tuple):
        items = [x.strip() for x in raw.split(",") if x.strip()]
        element = default[0] if default else None
        if element is None or isinstance(element, str):
            return tuple(items)
        return tuple(_coerce_scalar(x, element) for x in items)
    return _coerce_scalar(raw, default)


def _coerce_scalar(raw: str, like):
    if isinstance(like, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(like, int):
        return int(raw)
    if isinstance(like, float):
        return float(raw)
    if isinstance(like, datetime):
        return _parse_time(raw)
    return raw


def _run_ref(member: str, init_time: datetime):
    from .runner.executor import RunRef

    return RunRef(member=member, init_time=init_time)


def _sample_time(access) -> datetime:
    for obj in (access, getattr(access, "inner", None)):
        t = getattr(obj, "t_start", None)
        if t is not None:
            return t
    return datetime(2026, 1, 1, tzinfo=UTC)


def _progress_printer(mode: str):
    if mode == "quiet":
        return None
    if mode == "json":
        def emit_json(line: str) -> None:
            print(json.dumps({"event": "task", "detail": line}), flush=True)
        return emit_json

    def emit_plain(line: str) -> None:
        print(line, flush=True)
    return emit_plain


def _print_roots(roots, skipped) -> None:
    if roots:
        print(f"scanned: {', '.join(str(r) for r in roots)}")
    for note in skipped:
        print(f"  ! {note}", file=sys.stderr)


def _print_config(cfg: VerificationConfig) -> None:
    r = cfg.ruler
    print(f"config   {cfg.group_id}  ({cfg.name})")
    print(f"ruler    {r.ground_truth} / {r.variable} [{r.unit}] "
          f"step {r.time_step_minutes} min")
    print(f"         extent {list(r.extent)} @ {r.resolution}  "
          f"fingerprint {r.fingerprint}")
    print("members")
    for m in cfg.members:
        window = ""
        if m.active_from or m.active_to:
            window = f"  active {m.active_from or '...'} .. {m.active_to or '...'}"
        print(f"         {m.name:<16} leads {list(m.leads)}{window}")
    print("metrics")
    for g in cfg.metrics:
        extra = ""
        if g.levels:
            extra += f"  levels {list(g.levels)}"
        if g.half_windows:
            extra += f"  windows {list(g.half_windows)}"
        print(f"         {g.family:<12} {', '.join(g.items)}{extra}")


def reason_text(reason: str) -> str:
    from .core.planner import REASON_TEXT

    return REASON_TEXT.get(reason, "")


def _lead_list(text: str | None) -> list[int]:
    if not text:
        raise ConfigError("--leads is required, e.g. --leads 10,30,60")
    out = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    if not out:
        raise ConfigError("--leads listed no lead times")
    return out


def _date(text: str | None) -> date | None:
    if not text:
        return None
    return date.fromisoformat(text.strip())


def _parse_time(text: str) -> datetime:
    raw = text.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _parse_filters(pairs: list[str]) -> dict:
    out: dict = {}
    for pair in pairs:
        if "=" not in pair:
            raise QueryError(f"expected KEY=VALUE, got {pair!r}")
        key, _, raw = pair.partition("=")
        if "," in raw:
            value: object = [_maybe_number(x) for x in raw.split(",") if x != ""]
        else:
            value = _maybe_number(raw)
        out[key.strip()] = value
    return out


def _maybe_number(text: str):
    if text.lower() in ("", "none", "null"):
        return None
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


def _fmt(value) -> str:
    if value is None:
        return "nan"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return f"{value:.6g}"
    return str(value)


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    raise SystemExit(main())
