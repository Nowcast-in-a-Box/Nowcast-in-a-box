"""The field provider contract, over HTTP.

This is the client. It is what ``--access http`` builds, and it is what the
platform's own provider will be reached through once there is one: the module
never learns a path, a file format, or a catalog layout, only an address.

The transport is ``http.client`` from the standard library, kept open across
calls and reopened when a server closes an idle socket. Nothing above this file
notices: the contract is five method calls, and how many sockets they cost is not
part of it.

Two failure modes are worth naming, because both are easy to get backwards:

* **A 404 is not always a missing field.** If the address is wrong, every request
  404s. Mapping that to :class:`FieldUnavailable` would make a typo look like an
  archive with nothing in it — a run that verifies nothing and reports success.
  So only a 404 that carries this protocol's error object counts as unavailable;
  anything else is a loud :class:`HttpAccessError`.
* **A dropped keep-alive is not an outage.** Servers close idle connections, and
  a person staring at the review page is an idle connection. One retry on a
  transport error, and only for the GETs this contract is made of.
"""

from __future__ import annotations

import http.client
import json
from datetime import datetime
from typing import Any, Mapping
from urllib.parse import urlencode, urlsplit

from . import wire
from .base import Capabilities, Field, FieldUnavailable

DEFAULT_TIMEOUT = 30.0

#: Present on every request so a server log can tell this module from a browser.
USER_AGENT = "nib-verify/0.1 (+field-access)"


class HttpAccessError(RuntimeError):
    """The provider could not be reached, or answered with something unusable.

    Distinct from :class:`FieldUnavailable`, which means the provider answered
    correctly and does not have that field.
    """


class HttpAccess:
    """A :class:`~nib_verify.access.base.FieldAccess` backed by an HTTP service.

    ``base_url`` may carry a path prefix, so a provider mounted behind a reverse
    proxy as ``https://host/nib/data`` works without special handling.

    Every returned :class:`Field` has been through
    :func:`~nib_verify.access.wire.decode_field`, which is where the contract's
    guarantees are enforced rather than assumed. Anything a provider got wrong
    that is recoverable is counted in :attr:`warnings` and left readable, rather
    than thrown away or silently repaired.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        encoding: str = wire.DEFAULT_ENCODING,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        scheme, host, port = _split_base(base_url)
        self.base_url = base_url.rstrip("/")
        self.scheme = scheme
        self.host = host
        self.port = port
        self.prefix = urlsplit(base_url).path.rstrip("/")
        self.timeout = float(timeout)
        if encoding not in wire.ENCODINGS:
            raise ValueError(f"unknown encoding {encoding!r}; expected {wire.ENCODINGS}")
        self.encoding = encoding

        self._connection: http.client.HTTPConnection | None = None
        self.requests = 0
        self.warnings: list[str] = []
        self._capabilities: Capabilities | None = None

    # ------------------------------------------------------------------
    def describe(self) -> str:
        return f"http {self.base_url}{f' [{len(self.warnings)} warning(s)]' if self.warnings else ''}"

    def close(self) -> None:
        self._drop()

    def __enter__(self) -> "HttpAccess":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # the contract
    # ------------------------------------------------------------------
    def capabilities(self) -> Capabilities:
        """Cached: it cannot change while the process runs, and every caller
        from configuration validation onwards asks for it."""
        if self._capabilities is None:
            self._capabilities = wire.decode_capabilities(self._get("/capabilities"))
        return self._capabilities

    def list_observations(
        self, name: str, t0: datetime, t1: datetime
    ) -> list[datetime]:
        payload = self._get("/observations", name=name,
                            t0=wire.time_to_wire(t0), t1=wire.time_to_wire(t1))
        return _times(payload, "times")

    def get_observation(self, name: str, valid_time: datetime) -> Field:
        payload = self._get("/observation", name=name,
                            valid_time=wire.time_to_wire(valid_time),
                            encoding=self.encoding)
        return self._field(payload)

    def list_predictions(self, model_id: str, init_time: datetime) -> list[int]:
        payload = self._get("/predictions", model_id=model_id,
                            init_time=wire.time_to_wire(init_time))
        raw = payload.get("lead_minutes") if isinstance(payload, Mapping) else None
        if raw is None:
            raise HttpAccessError("the /predictions response has no 'lead_minutes'")
        try:
            return [int(x) for x in raw]
        except (TypeError, ValueError) as exc:
            raise HttpAccessError(f"lead_minutes must be whole numbers: {exc}") from exc

    def get_prediction(
        self, model_id: str, init_time: datetime, lead_minutes: int
    ) -> Field:
        payload = self._get("/prediction", model_id=model_id,
                            init_time=wire.time_to_wire(init_time),
                            lead_minutes=int(lead_minutes), encoding=self.encoding)
        return self._field(payload)

    # ------------------------------------------------------------------
    def _field(self, payload: Any) -> Field:
        try:
            return wire.decode_field(payload, on_warning=self.warnings.append)
        except wire.WireError as exc:
            raise HttpAccessError(f"the provider sent an unusable field: {exc}") from exc

    # ------------------------------------------------------------------
    # transport
    # ------------------------------------------------------------------
    def _connect(self) -> http.client.HTTPConnection:
        if self._connection is None:
            factory = (http.client.HTTPSConnection if self.scheme == "https"
                       else http.client.HTTPConnection)
            self._connection = factory(self.host, self.port, timeout=self.timeout)
        return self._connection

    def _drop(self) -> None:
        if self._connection is not None:
            try:
                self._connection.close()
            except Exception:  # noqa: BLE001 - closing a dead socket is not news
                pass
            self._connection = None

    def _get(self, path: str, **params: Any) -> Any:
        """One GET, with a single retry on a transport error."""
        query = urlencode({k: v for k, v in params.items() if v is not None})
        target = path + (f"?{query}" if query else "")
        # ``target`` is what a reader should see; the path prefix is an
        # implementation detail of where the service is mounted.
        display = f"{self.base_url}{target}"
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}

        last: Exception | None = None
        for attempt in (0, 1):
            try:
                connection = self._connect()
                connection.request("GET", f"{self.prefix}{target}", headers=headers)
                response = connection.getresponse()
                # read fully, or the connection cannot be reused
                body = response.read()
                status = response.status
                reason = response.reason
                self.requests += 1
                return self._interpret(status, reason, body, display)
            except HttpAccessError:
                raise
            except (http.client.HTTPException, OSError) as exc:
                # A server closing an idle keep-alive socket looks exactly like
                # this, and is not worth reporting. A refused connection is
                # indistinguishable here either, so it is tried twice and then
                # reported with its own text.
                self._drop()
                last = exc
                if attempt == 1:
                    break
        raise HttpAccessError(
            f"could not reach the field provider at {display}: {last}"
        )

    def _interpret(self, status: int, reason: str, body: bytes, url: str) -> Any:
        try:
            payload = json.loads(body.decode("utf-8")) if body else None
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HttpAccessError(
                f"the provider answered {status} with something that is not JSON "
                f"({len(body)} bytes) for {url}: {exc}"
            ) from exc

        if status == 200:
            return payload

        is_ours = isinstance(payload, Mapping) and "error" in payload
        code, detail = wire.decode_error(payload)

        if status == 404 and is_ours and code in wire.UNAVAILABLE_CODES:
            raise FieldUnavailable(detail or f"{url} is not available")

        if status == 404 and not is_ours:
            # Not one of ours: the endpoint is not there at all. This is the
            # wrong-address case, and it must not look like an empty archive.
            raise HttpAccessError(
                f"{url} is not a field access endpoint (404, and the body is not "
                f"this protocol's error object). Check the address."
            )

        raise HttpAccessError(
            f"the provider answered {status} {reason} for {url}"
            + (f": {code}: {detail}" if detail else "")
        )


# ======================================================================
def _split_base(base_url: str) -> tuple[str, str, int]:
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url must start with http:// or https://, got {base_url!r}"
        )
    if not parts.hostname:
        raise ValueError(f"base_url has no host: {base_url!r}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, parts.hostname, int(port)


def _times(payload: Any, key: str) -> list[datetime]:
    raw = payload.get(key) if isinstance(payload, Mapping) else None
    if raw is None:
        raise HttpAccessError(f"the response has no {key!r}")
    if not isinstance(raw, list):
        raise HttpAccessError(f"{key!r} must be a list")
    out = []
    for item in raw:
        try:
            out.append(wire.time_from_wire(item))
        except wire.WireError as exc:
            raise HttpAccessError(f"the provider sent an unusable time: {exc}") from exc
    return out
