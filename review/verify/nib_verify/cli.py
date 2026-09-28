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

Every command takes ``--root``. The handler supplies it; nothing here invents a
path.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .access.factory import build_access
from .config.schema import ConfigError, VerificationConfig, dump, load
from .core.metrics import list_metrics as metric_catalog
from .query.service import QueryError, available_expansions, query_scores
from .runner.executor import RunRef, run_tasks
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
    except (ConfigError, QueryError, Locked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_FAILURE


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------
def cmd_config_show(args) -> int:
    cfg = load(args.config)
    if args.json:
        print(cfg.to_json())
    else:
        _print_config(cfg)
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
        if member.name in caps.model_ids:
            leads = set(access.list_predictions(member.name, _sample_time(access)))
            missing = sorted(set(member.leads) - leads)
            if missing:
                problems.append(
                    f"member {member.name!r} does not produce leads {missing}"
                )

    if problems:
        for p in problems:
            print(f"  ! {p}", file=sys.stderr)
        print(f"config {cfg.group_id!r}: {len(problems)} problem(s)", file=sys.stderr)
        return EXIT_FAILURE

    print(f"config {cfg.group_id!r}: ok  (ruler {cfg.ruler_fingerprint})")
    return EXIT_OK


def cmd_config_convert(args) -> int:
    cfg = load(args.config)
    dump(cfg, args.out)
    print(f"wrote {args.out}")
    return EXIT_OK


# ----------------------------------------------------------------------
# metrics
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# run
# ----------------------------------------------------------------------
def cmd_run(args) -> int:
    root = Path(args.root)
    cfg = load(args.config)
    access = _make_access(args)

    inits = [_parse_time(x) for x in (args.init or [])]
    if not inits:
        print("error: give at least one --init (ISO-8601, UTC)", file=sys.stderr)
        return EXIT_USAGE

    runs = [
        RunRef(member=m.name, init_time=t)
        for t in inits
        for m in cfg.members
    ]

    progress = args.progress or os.environ.get("NIB_PROGRESS", "plain")

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
    for msg in stats.messages:
        print(f"  {msg}", file=sys.stderr)

    return EXIT_OK if stats.failed == 0 else EXIT_FAILURE


# ----------------------------------------------------------------------
# query
# ----------------------------------------------------------------------
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
    head = "\t".join(result["dims"] + ["value", "n_valid"])
    print(head)
    for rec in result["records"]:
        cells = [_fmt(rec[d]) for d in result["dims"]]
        cells += [_fmt(rec["value"]), _fmt(rec["n_valid"])]
        print("\t".join(cells))
    print(f"# {result['sample']['n_records']} records, "
          f"{result['sample']['n_valid']:.0f} valid samples", file=sys.stderr)
    return EXIT_OK


def cmd_expansions(args) -> int:
    print("\n".join(available_expansions(args.method)))
    return EXIT_OK


# ----------------------------------------------------------------------
# plumbing
# ----------------------------------------------------------------------
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nib-verify", description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)

    def add_root(sp):
        sp.add_argument("--root", required=True,
                        help="system root; holds verify.sqlite and the lock")

    def add_access(sp):
        sp.add_argument("--access", default="mock", choices=("mock", "replay", "real"),
                        help="which field provider to use (default: mock)")
        sp.add_argument("--mock", action="append", metavar="KEY=VALUE",
                        help="option for the mock provider, e.g. "
                             "--mock peak=250 --mock unit='kg m-2' "
                             "(replay takes --mock root=DIR)")

    c = sub.add_parser("config", help="create, inspect and validate configs")
    cs = c.add_subparsers(dest="subcommand", required=True)

    show = cs.add_parser("show", help="print a config")
    show.add_argument("config")
    show.add_argument("--json", action="store_true")
    show.set_defaults(handler=cmd_config_show)

    validate = cs.add_parser("validate", help="check a config against the provider")
    validate.add_argument("config")
    add_access(validate)
    validate.set_defaults(handler=cmd_config_validate)

    convert = cs.add_parser("convert", help="rewrite a config in another format")
    convert.add_argument("config")
    convert.add_argument("out")
    convert.set_defaults(handler=cmd_config_convert)

    m = sub.add_parser("metrics", help="what can be computed")
    ms = m.add_subparsers(dest="subcommand", required=True)
    mlist = ms.add_parser("list", help="list available metrics")
    mlist.add_argument("--json", action="store_true")
    mlist.set_defaults(handler=cmd_metrics_list)

    r = sub.add_parser("run", help="execute tasks")
    r.add_argument("--config", required=True)
    add_root(r)
    add_access(r)
    r.add_argument("--init", action="append",
                   help="init time, ISO-8601 UTC; repeat for several")
    r.add_argument("--force", action="store_true",
                   help="recompute tasks already recorded as done")
    r.add_argument("--progress", choices=("quiet", "plain", "json"))
    r.set_defaults(handler=cmd_run)

    q = sub.add_parser("query", help="aggregate stored quantities")
    q.add_argument("--method", required=True)
    q.add_argument("--config")
    add_root(q)
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

    return p


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


def _make_access(args):
    """Build the provider, applying any ``--mock`` overrides."""
    kind = getattr(args, "access", "mock")
    kwargs = _mock_kwargs(getattr(args, "mock", None) or [])
    if kind == "replay" and "root" not in kwargs:
        raise ConfigError("the replay provider needs --mock root=DIR")
    return build_access(kind, **kwargs)


def _mock_kwargs(pairs: list[str]) -> dict:
    """Turn ``--mock key=value`` pairs into constructor arguments.

    Types come from the constructor's own defaults, so the CLI never has to
    keep a second list of what each option means.
    """
    if not pairs:
        return {}
    import inspect

    from .access.synthetic import SyntheticAccess

    params = dict(inspect.signature(SyntheticAccess.__init__).parameters)
    params.pop("self", None)
    out: dict = {}
    for pair in pairs:
        key, sep, raw = pair.partition("=")
        key = key.strip()
        if not sep:
            raise ConfigError(f"expected KEY=VALUE, got {pair!r}")
        if key not in params:
            known = ", ".join(sorted(params))
            raise ConfigError(f"unknown --mock option {key!r}; known: {known}")
        out[key] = _coerce_like(raw, params[key].default)
    return out


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


def _sample_time(access) -> datetime:
    """A time to probe leads at. Any cadence-aligned instant will do."""
    for obj in (access, getattr(access, "inner", None)):
        t = getattr(obj, "t_start", None)
        if t is not None:
            return t
    return datetime(2026, 1, 1, tzinfo=UTC)


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
        value: object = raw
        if "," in raw:
            parts = [p for p in raw.split(",") if p != ""]
            value = [_maybe_number(p) for p in parts]
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


if __name__ == "__main__":
    raise SystemExit(main())
