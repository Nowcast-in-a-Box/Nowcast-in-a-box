"""Run the local latest-config workflow without a manual download step.

The flow is deliberately explicit about trust boundaries:

    authenticated ArcoDataHub snapshot -> local Data Adapter -> run directory
    -> network-isolated pySTEPS model container

The adapter and model never receive credentials, and the model container has
no network.  A fresh operational run records only context and forecast; it
does not fabricate future observations for diagnostics.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime as dt
import getpass
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import xarray as xr
import yaml

from adapters.__main__ import adapter_for
from interfaces.data import request_from_mapping
from utils.stored_fields import create_run

DEFAULT_CONFIG = (
    Path(__file__).resolve().parents[1] / "configs" / "arco-pysteps-latest.yaml"
)


def _configure_windows_asyncio() -> None:
    """Use the selector loop before fsspec/aiohttp create their first loop.

    The Windows proactor loop can emit a second, misleading ``Event loop is
    closed`` exception while aiohttp cleans up a failed HTTP connection.
    """
    if os.name == "nt":
        policy = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
        if policy is not None:
            asyncio.set_event_loop_policy(policy())


def _http_status(error: BaseException) -> int | None:
    """Find an HTTP status through exception wrapping without exposing headers."""
    current, seen = error, set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(current, "status", None) or getattr(current, "code", None)
        if isinstance(status, int):
            return status
        current = current.__cause__ or current.__context__
    return None


def _raise_authentication_error(status: int) -> None:
    raise RuntimeError(
        "ArcoDataHub rejected the configured credentials or this account does "
        "not have access to opera-plus-it-vmi (HTTP " + str(status) + "). "
        "Run --setup-access-url to paste the portal Access URL (or "
        "--setup-credentials), then run "
        "--check-access to confirm the subscription includes this dataset."
    ) from None


def _proxy_opener(proxy: str | None):
    if proxy and not proxy.startswith(("http://", "https://")):
        raise ValueError("arco.proxy must be an http:// or https:// URL")
    # An empty mapping intentionally disables inherited Windows proxy settings.
    mappings = {"http": proxy, "https": proxy} if proxy else {}
    return urllib.request.build_opener(urllib.request.ProxyHandler(mappings))


def _preflight_access(
    url: str, username: str, access_key: str, proxy: str | None
) -> None:
    """Check credentials with one small synchronous request before opening Zarr."""
    token = base64.b64encode(f"{username}:{access_key}".encode()).decode()
    request = urllib.request.Request(
        url.rstrip("/") + "/.zmetadata", headers={"Authorization": "Basic " + token}
    )
    try:
        with _proxy_opener(proxy).open(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError(
                    "Arco metadata preflight returned HTTP " + str(response.status)
                )
            response.read(1)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            _raise_authentication_error(exc.code)
        raise RuntimeError(
            "Arco metadata preflight failed with HTTP " + str(exc.code) + "."
        ) from None
    except urllib.error.URLError:
        raise RuntimeError(
            "Cannot reach ArcoDataHub metadata. Check Internet access; use arco.proxy "
            "only if this network requires an explicit HTTP proxy."
        ) from None


def _with_retries(label: str, operation, attempts: int, delay_seconds: float):
    """Retry only transient remote reads and report a compact, useful status."""
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except Exception as exc:
            status = _http_status(exc)
            if status in (401, 403):
                _raise_authentication_error(status)
            retryable = status is None or status in (408, 425, 429, 500, 502, 503, 504)
            if not retryable or attempt == attempts:
                suffix = "" if status is None else " (HTTP " + str(status) + ")"
                raise RuntimeError(
                    label
                    + " failed after "
                    + str(attempt)
                    + " attempt(s)"
                    + suffix
                    + "."
                ) from None
            wait = delay_seconds * (2 ** (attempt - 1))
            suffix = "" if status is None else " (HTTP " + str(status) + ")"
            print(
                f"{label} failed{suffix}; retrying in {wait:g}s ({attempt}/{attempts})",
                flush=True,
            )
            time.sleep(wait)


def _utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _key(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def credentials() -> tuple[str, str]:
    """Read credentials without accepting or persisting a secret in YAML/source."""
    username = os.environ.get("ARCODATAHUB_USERNAME", "").strip()
    access_key = os.environ.get("ARCODATAHUB_ACCESS_KEY", "").strip()
    if username and access_key:
        return username, access_key
    private = Path.home() / ".nib" / "arcodatahub.json"
    if private.is_file():
        try:
            stored = json.loads(private.read_text(encoding="utf-8"))
            username = str(stored.get("username", "")).strip()
            access_key = str(stored.get("access_key", "")).strip()
        except (OSError, ValueError, TypeError) as exc:
            raise ValueError(
                f"Cannot read private Arco credentials at {private}"
            ) from exc
    if not username or not access_key:
        raise ValueError(
            "Arco credentials are unavailable. Set ARCODATAHUB_USERNAME and "
            "ARCODATAHUB_ACCESS_KEY, or create the private ~/.nib/arcodatahub.json "
            "using the existing fetcher's --setup-credentials once."
        )
    return username, access_key


def setup_credentials() -> Path:
    """Prompt once and store credentials outside the repository and YAML files."""
    path = Path.home() / ".nib" / "arcodatahub.json"
    print("ArcoDataHub credentials are stored only in your private user profile.")
    username = input("ArcoDataHub username: ").strip()
    access_key = getpass.getpass("ArcoDataHub access key (input hidden): ").strip()
    if not username or not access_key or ":" in username:
        raise ValueError("Provide a nonempty username (without ':') and access key")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".arco-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"username": username, "access_key": access_key}, handle, indent=2
            )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            # Windows ACLs are controlled by the profile directory; do not fail
            # setup merely because POSIX mode bits are unavailable.
            pass
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def setup_access_url() -> Path:
    """Store credentials from the portal's copied Zarr URL without displaying it."""
    raw = getpass.getpass("Paste ArcoDataHub Zarr access URL (input hidden): ").strip()
    parsed = urllib.parse.urlsplit(raw)
    username = urllib.parse.unquote(parsed.username or "")
    access_key = urllib.parse.unquote(parsed.password or "")
    expected_path = "/S3/opera-plus-it-vmi.zarr"
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.arcodatahub.com"
        or parsed.path.rstrip("/") != expected_path
        or not username
        or not access_key
    ):
        raise ValueError(
            "Paste the complete HTTPS Access URL copied for opera-plus-it-vmi "
            "from the ArcoDataHub My datasets page."
        )
    path = Path.home() / ".nib" / "arcodatahub.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".arco-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {"username": username, "access_key": access_key}, handle, indent=2
            )
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def _open_remote(url: str, username: str, access_key: str, proxy: str | None = None):
    """Open Arco's Zarr store with an explicit network policy.

    ``trust_env=True`` made the latest-config path inherit an unrelated
    Windows proxy.  A failed proxy connection can make aiohttp emit a noisy
    ``Event loop is closed`` exception while finalising, hiding the useful
    connection error.  Arco is direct by default; deployments that require a
    proxy must opt in through ``arco.proxy``.
    """
    import aiohttp

    token = base64.b64encode(f"{username}:{access_key}".encode()).decode()
    _proxy_opener(proxy)  # validate explicit proxy before asynchronous setup
    options = {"proxy": proxy} if proxy else {}
    return xr.open_dataset(
        url,
        engine="zarr",
        consolidated=True,
        chunks={},
        storage_options={
            **options,
            "client_kwargs": {
                "trust_env": False,
                "headers": {"Authorization": "Basic " + token},
                "timeout": aiohttp.ClientTimeout(
                    total=600, sock_connect=30, sock_read=120
                ),
            },
        },
    )


def _normalise_time(dataset: xr.Dataset) -> xr.Dataset:
    if "time" not in dataset.variables or dataset["time"].ndim != 1:
        raise ValueError(
            "Arco dataset must expose one explicit one-dimensional time coordinate"
        )
    if not np.issubdtype(dataset["time"].dtype, np.datetime64):
        dataset = xr.decode_cf(dataset)
    if not np.issubdtype(dataset["time"].dtype, np.datetime64) or "vmi" not in dataset:
        raise ValueError("Arco dataset has no decoded time coordinate or vmi variable")
    return dataset.set_coords("time")


def _projected_crop(
    dataset: xr.Dataset, bbox: tuple[float, float, float, float]
) -> xr.Dataset:
    from pyproj import CRS, Transformer

    mapping_name = dataset.vmi.attrs.get("grid_mapping") or dataset.vmi.encoding.get(
        "grid_mapping"
    )
    if not mapping_name or mapping_name not in dataset:
        raise ValueError(
            "Arco VMI lacks the CF projection required for a safe ROI crop"
        )
    attrs = dataset[mapping_name].attrs
    crs = CRS.from_wkt(attrs["crs_wkt"]) if attrs.get("crs_wkt") else CRS.from_cf(attrs)
    west, south, east, north = bbox
    if west >= east or south >= north:
        raise ValueError("region.bbox requires west < east and south < north")
    xmin, ymin, xmax, ymax = Transformer.from_crs(
        4326, crs, always_xy=True
    ).transform_bounds(west, south, east, north, densify_pts=101)
    indexers = {}
    for axis, low, high in (("x", xmin, xmax), ("y", ymin, ymax)):
        values = np.asarray(dataset[axis].values)
        if values.ndim != 1 or values.size < 2 or not np.isfinite(values).all():
            raise ValueError(
                f"Arco {axis} coordinate is not a valid one-dimensional grid"
            )
        delta = np.diff(values)
        if not ((delta > 0).all() or (delta < 0).all()):
            raise ValueError(f"Arco {axis} coordinate is not monotonic")
        selected = np.flatnonzero((values >= low) & (values <= high))
        if not selected.size:
            raise ValueError("The requested bbox has no Arco grid cells")
        indexers[axis] = slice(int(selected.min()), int(selected.max()) + 1)
    # Do not attach/download full-domain 2-D geolocation arrays to the cache.
    trimmed = dataset.drop_vars(
        [name for name in ("lat", "lon", "latitude", "longitude") if name in dataset]
    )
    return trimmed.isel(indexers)


def fetch_snapshot(config: dict, repo: Path) -> tuple[Path, list[str]]:
    arco, adapter = config["arco"], config["adapter"]
    steps, interval = int(arco["history_steps"]), int(arco["interval_minutes"])
    if steps != 3:
        raise ValueError(
            "This real-time optical-flow workflow requires exactly history_steps: 3"
        )
    if interval != 5:
        raise ValueError("OPERA+DPC VMI is configured at its native 5-minute cadence")
    attempts = int(arco.get("retry_attempts", 3))
    delay_seconds = float(arco.get("retry_delay_seconds", 2))
    if attempts < 1 or delay_seconds < 0:
        raise ValueError(
            "arco.retry_attempts must be >= 1 and retry_delay_seconds must be >= 0"
        )
    username, access_key = credentials()
    _configure_windows_asyncio()
    _preflight_access(str(arco["url"]), username, access_key, arco.get("proxy"))
    try:
        remote = _with_retries(
            "Opening Arco Zarr metadata",
            lambda: _open_remote(
                str(arco["url"]), username, access_key, arco.get("proxy")
            ),
            attempts,
            delay_seconds,
        )
        try:
            dataset = _normalise_time(remote)
            last = _utc(str(dataset.attrs["last_valid"]))
            finish = (
                last
                if str(arco.get("end", "latest")).lower() == "latest"
                else _utc(str(arco["end"]))
            )
            if finish > last:
                raise ValueError("Configured end is newer than Arco last_valid")
            wanted = [
                finish - dt.timedelta(minutes=interval * i)
                for i in range(steps - 1, -1, -1)
            ]
            wanted64 = np.array(
                [np.datetime64(item.replace(tzinfo=None), "ns") for item in wanted]
            )
            if not np.isin(wanted64, dataset.time.values).all():
                raise ValueError(
                    "Arco is missing an exact requested five-minute frame; nearest-time substitution is disabled"
                )
            selected = _projected_crop(
                dataset.sel(time=wanted64), tuple(adapter["region"]["bbox"])
            )
            frames = [
                _with_retries(
                    "Reading Arco frame " + str(index + 1) + "/" + str(steps),
                    lambda index=index: selected.isel(
                        time=slice(index, index + 1)
                    ).load(scheduler="synchronous"),
                    attempts,
                    delay_seconds,
                )
                for index in range(steps)
            ]
            snapshot = xr.concat(
                frames,
                dim="time",
                data_vars="minimal",
                coords="minimal",
                compat="override",
            )
            if not np.isfinite(np.asarray(snapshot.vmi.values)).any(axis=(1, 2)).all():
                raise ValueError(
                    "At least one requested VMI frame has no valid pixels in the ROI"
                )
            root = (repo / arco["snapshot_root"] / adapter["source"]).resolve()
            root.mkdir(parents=True, exist_ok=True)
            temp = Path(tempfile.mkdtemp(prefix=".partial-", dir=root))
            final = root / (
                finish.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
            )
            try:
                for variable in snapshot.variables:
                    snapshot[variable].encoding = {}
                snapshot.to_zarr(
                    temp / "sample.zarr", mode="w", zarr_format=2, consolidated=True
                )
                request = {
                    "source": adapter["source"],
                    "data_dir": str(final / "sample.zarr"),
                    "initial_time": _key(finish),
                    "history_steps": steps,
                    "interval_minutes": interval,
                    "channels": adapter["channels"],
                    "region": adapter["region"],
                }
                (temp / "request.yaml").write_text(
                    yaml.safe_dump(request, sort_keys=False), encoding="utf-8"
                )
                (temp / "qa.json").write_text(
                    json.dumps(
                        {
                            "source_url": arco["url"],
                            "selected_end": _key(finish),
                            "times": [_key(item) for item in wanted],
                            "shape": list(snapshot.vmi.shape),
                        },
                        indent=2,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                temp.rename(final)
            except Exception:
                import shutil

                shutil.rmtree(temp, ignore_errors=True)
                raise
        finally:
            try:
                remote.close()
            except RuntimeError as close_error:
                if "Event loop is closed" not in str(close_error):
                    raise
                print(
                    "Warning: Arco HTTP cleanup completed after its event loop closed.",
                    file=sys.stderr,
                )
    except Exception as exc:
        # Do not echo exception text: HTTP client errors can include request
        # headers.  A 401/403 is actionable only as an account/permission
        # problem, never as a retryable model or proxy failure.
        status = _http_status(exc)
        if status in (401, 403):
            _raise_authentication_error(status)
        if not isinstance(exc, RuntimeError):
            raise
        if "Event loop is closed" in str(exc):
            raise RuntimeError(
                "Arco connection cleanup failed after a network error. The workflow now "
                "uses direct access by default; retry once, or configure arco.proxy explicitly "
                "if your network requires an HTTP proxy."
            ) from exc
        raise
    return final, [_key(item) for item in wanted]


def existing_snapshot(path: str) -> tuple[Path, list[str], dict]:
    """Validate a prior immutable fetch for an offline replay of the same flow."""
    snapshot = Path(path).resolve()
    request_path = snapshot / "request.yaml"
    if not (snapshot / "sample.zarr").is_dir() or not request_path.is_file():
        raise FileNotFoundError("--snapshot must contain sample.zarr and request.yaml")
    request = yaml.safe_load(request_path.read_text(encoding="utf-8"))
    if (
        not isinstance(request, dict)
        or request.get("source") != "mlcast_opera_plus_it_vmi"
    ):
        raise ValueError("--snapshot is not an OPERA+DPC VMI local snapshot")
    finish = _utc(str(request["initial_time"]))
    steps, interval = int(request["history_steps"]), int(request["interval_minutes"])
    if steps != 3 or interval != 5:
        raise ValueError("--snapshot must contain exactly three five-minute VMI frames")
    return (
        snapshot,
        [
            _key(finish - dt.timedelta(minutes=interval * index))
            for index in range(steps - 1, -1, -1)
        ],
        request,
    )


def _largest_complete_rectangle(mask: np.ndarray) -> tuple[slice, slice]:
    """Return the largest axis-aligned all-true rectangle in a coverage mask.

    The previous row-and-column intersection was needlessly strict: one NaN
    at a raster edge discarded its entire row *and* column.  This histogram
    algorithm instead keeps the largest contiguous fully observed rectangle,
    without interpolating or relabelling missing coverage as no echo.
    """
    if mask.ndim != 2:
        raise ValueError("coverage mask must be two-dimensional")
    height = np.zeros(mask.shape[1], dtype=np.int32)
    best = (0, 0, 0, 0, 0)  # area, top, bottom-exclusive, left, right-exclusive
    for bottom, row in enumerate(mask):
        height = np.where(row, height + 1, 0)
        stack: list[int] = []
        for column in range(mask.shape[1] + 1):
            current = int(height[column]) if column < mask.shape[1] else 0
            while stack and current < int(height[stack[-1]]):
                peak = stack.pop()
                high = int(height[peak])
                left = stack[-1] + 1 if stack else 0
                area = high * (column - left)
                if area > best[0]:
                    best = (area, bottom - high + 1, bottom + 1, left, column)
            stack.append(column)
    if best[0] == 0:
        raise ValueError("No finite VMI pixels are available in every context frame")
    _, top, bottom, left, right = best
    return slice(top, bottom), slice(left, right)


def materialise_run(
    bundle,
    run_root: Path,
    run_id: str,
    atlas_revision: str,
    no_echo_dbz: float,
    min_grid: tuple[int, int] = (32, 32),
) -> Path:
    fields = bundle["data"]
    times = sorted(fields)
    frames = [fields[item]["vmi"] for item in times]
    values = np.stack([np.asarray(frame.values, dtype=np.float32) for frame in frames])
    # -9998 is explicitly documented as covered/no echo. It is safe to map only
    # this sentinel to the chosen radar floor; all other missing values remain errors.
    no_echo = values == -9998
    values[no_echo] = np.float32(no_echo_dbz)
    complete = np.isfinite(values).all(axis=0)
    rows, cols = _largest_complete_rectangle(complete)
    output_shape = (rows.stop - rows.start, cols.stop - cols.start)
    if output_shape[0] < min_grid[0] or output_shape[1] < min_grid[1]:
        raise ValueError(
            "Largest complete VMI rectangle is "
            + str(output_shape)
            + f", below required minimum {min_grid}; enlarge the configured ROI."
        )
    values = values[:, rows, cols]
    y_dim, x_dim = frames[0].dims
    x = np.asarray(frames[0][x_dim].values, dtype=float)[cols]
    y = np.asarray(frames[0][y_dim].values, dtype=float)[rows]
    if np.all(np.diff(x) < 0):
        x, values = x[::-1], values[:, :, ::-1]
    if np.all(np.diff(y) < 0):
        y, values = y[::-1], values[:, ::-1, :]
    if not (
        np.all(np.diff(x) > 0) and np.all(np.diff(y) > 0) and np.isfinite(values).all()
    ):
        raise ValueError("The VMI adapter did not yield a complete monotonic grid")
    init_time = times[-1]
    window = {
        "data_id": "mlcast_opera_plus_it_vmi",
        "variable": "vmi",
        "unit": "dBZ",
        "cadence_minutes": 5,
        "context": values,
        "context_times": times,
        "context_start_time": times[0],
        "init_time": init_time,
        "event_id": init_time[:10].replace("-", ""),
        "sample_id": f"mlcast-opera-plus-it-vmi@{init_time}",
        "row": 0,
        "provenance": {
            "adapter": "mlcast_opera_plus_it_vmi",
            "source_snapshot": bundle["metadata"]["local_store"],
            "no_echo_policy": {"source_value": -9998, "mapped_to_dbz": no_echo_dbz},
            "missing_coverage_policy": "rejected; largest contiguous fully finite rectangle passed to pySTEPS",
            "adapter_grid_shape": list(complete.shape),
            "model_grid_shape": list(output_shape),
        },
    }
    grid = {
        "crs": str(frames[0].attrs.get("grid_mapping", "CF projected grid")),
        "x": x,
        "y": y,
        "axis_units": "native projected coordinates",
        "source": "Arco OPERA+DPC VMI cropped grid",
    }
    run_root.mkdir(parents=True, exist_ok=True)
    return create_run(run_root, run_id, window, grid, atlas_revision=atlas_revision)


def run_container(repo: Path, run: Path, model: dict):
    image = str(model["image"])
    inspected = subprocess.run(
        ["docker", "image", "inspect", image], cwd=repo, capture_output=True
    )
    if inspected.returncode and bool(model.get("build_if_missing", True)):
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "adapters/mlcast_opera_plus_it_vmi/pysteps_lk/Dockerfile",
                "-t",
                image,
                ".",
            ],
            cwd=repo,
            check=True,
        )
    elif inspected.returncode:
        raise RuntimeError(
            f"Docker image is absent: {image}; set model.build_if_missing: true"
        )
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--read-only",
            "--tmpfs",
            "/tmp",
            "-e",
            "MPLCONFIGDIR=/tmp",
            "-v",
            f"{run.resolve()}:/run",
            image,
            "--run",
            "/run",
            "--algorithm-manifest",
            "/opt/nib/pysteps/algorithm-manifest.json",
        ],
        cwd=repo,
        check=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--setup-credentials",
        action="store_true",
        help="Prompt once and save Arco credentials in the private user profile",
    )
    parser.add_argument(
        "--setup-access-url",
        action="store_true",
        help="Paste the portal's copied Zarr URL (hidden) and save its credentials",
    )
    parser.add_argument(
        "--check-access",
        action="store_true",
        help="Validate configured Arco credentials and dataset permission only",
    )
    parser.add_argument(
        "--skip-container",
        action="store_true",
        help="Fetch, adapt, and materialise only",
    )
    parser.add_argument(
        "--snapshot",
        help="Reuse an existing immutable snapshot instead of fetching; useful for offline replay",
    )
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if (
        not isinstance(config, dict)
        or config.get("schema") != "nib.local-latest-pysteps.v1"
    ):
        raise ValueError("Unsupported local workflow configuration")
    if args.setup_credentials:
        path = setup_credentials()
        print("Credentials saved to:", path)
        print("Run --check-access before starting a download.")
        return
    if args.setup_access_url:
        path = setup_access_url()
        print("Credentials extracted and saved to:", path)
        print("Run --check-access before starting a download.")
        return
    if args.check_access:
        username, access_key = credentials()
        _configure_windows_asyncio()
        _preflight_access(
            str(config["arco"]["url"]),
            username,
            access_key,
            config["arco"].get("proxy"),
        )
        print("ArcoDataHub access check passed for opera-plus-it-vmi.")
        return
    repo = Path(__file__).resolve().parents[1]
    if args.snapshot:
        snapshot, times, snapshot_request = existing_snapshot(args.snapshot)
        adapter_config = {
            "source": snapshot_request["source"],
            "channels": snapshot_request["channels"],
            "region": snapshot_request["region"],
        }
    else:
        snapshot, times = fetch_snapshot(config, repo)
        adapter_config = config["adapter"]
    request_config = {
        "source": adapter_config["source"],
        "data_dir": str(snapshot / "sample.zarr"),
        "initial_time": times[-1],
        "history_steps": 3,
        "interval_minutes": 5,
        "channels": adapter_config["channels"],
        "region": adapter_config["region"],
    }
    request = request_from_mapping(request_config)
    adapter = adapter_for(request.source)
    adapter.validate(request)
    bundle = adapter.load(request)
    run_id = (
        "arco-vmi-"
        + times[-1].replace("-", "").replace(":", "")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    minimum = tuple(int(value) for value in config["model"].get("min_grid", [32, 32]))
    if len(minimum) != 2 or min(minimum) < 3:
        raise ValueError(
            "model.min_grid must contain two integer dimensions, both >= 3"
        )
    run = materialise_run(
        bundle,
        (repo / config["runs"]["root"]).resolve(),
        run_id,
        str(config["runs"]["atlas_revision"]),
        float(config["model"]["no_echo_dbz"]),
        minimum,
    )
    print(f"Snapshot: {snapshot}")
    print(f"Run: {run}")
    if args.skip_container:
        print("Container skipped by request")
        return
    run_container(repo, run, config["model"])
    print(f"Forecast: {run / 'forecasts' / 'pysteps_lk' / 'fields.npz'}")


if __name__ == "__main__":
    try:
        main()
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print("ERROR:", exc, file=sys.stderr)
        raise SystemExit(1)
