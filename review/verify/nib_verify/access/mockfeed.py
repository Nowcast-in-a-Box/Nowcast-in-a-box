"""The reference field service: any provider, served over HTTP.

This is the "simulated data interface". It wraps *any*
:class:`~nib_verify.access.base.FieldAccess` and exposes it at the addresses in
``spec/field-access-http.md``, so the client in :mod:`~nib_verify.access.http`
can be developed and tested against something real before the platform's own
service exists.

It is deliberately thin. There is no data model here, no caching, no index and no
storage layer: every request is forwarded to the provider it was built around.
Two things follow from that, and both matter more than they look:

* **It is also the reference implementation.** A team implementing the contract
  can read this file end to end in a few minutes and see exactly what each
  address has to answer. That is worth more than anything clever.
* **It cannot hide a provider's mistakes.** The guarantees in the contract are
  enforced in :mod:`~nib_verify.access.wire`, which both sides import, so a
  provider that returns a field with no unit fails here, in front of whoever is
  running it, rather than three steps later inside a metric.

Start it with::

    python -m nib_verify datafeed serve --access mock --port 8770

and point a verifier at it with::

    python -m nib_verify datafeed check --url http://127.0.0.1:8770
    python -m nib_verify task run --access http --data-url http://127.0.0.1:8770 ...
"""

from __future__ import annotations

from typing import Any

from . import wire
from .base import FieldAccess, FieldUnavailable

try:  # pragma: no cover - exercised by whichever branch the environment takes
    from fastapi import FastAPI, Query, Request
    from fastapi.responses import JSONResponse
    HAS_FASTAPI = True
except ModuleNotFoundError:  # pragma: no cover
    FastAPI = Query = Request = None
    JSONResponse = None
    HAS_FASTAPI = False


def create_data_app(access: FieldAccess, *, title: str = "NiB field access") -> "FastAPI":
    """Serve ``access`` at the contract's addresses.

    The provider is captured as given. If the caller wants to see what was asked
    for — and in tests that is usually the point — wrap it in
    :class:`~nib_verify.access.probe.CountingAccess` first.
    """
    if not HAS_FASTAPI:
        raise RuntimeError(
            "the reference field service needs FastAPI: pip install -e '.[web]'"
        )

    app = FastAPI(
        title=title,
        version=f"protocol {wire.PROTOCOL}",
        description="The field access contract, served over HTTP.",
    )

    # ------------------------------------------------------------------
    # error shape
    # ------------------------------------------------------------------
    # Registered as handlers rather than as try/except in each route so that the
    # routes read like the contract they implement. Everything the client has to
    # understand passes through here.
    @app.exception_handler(FieldUnavailable)
    async def _unavailable(request: "Request", exc: FieldUnavailable) -> "JSONResponse":
        return JSONResponse(
            status_code=404,
            content=wire.encode_error(_classify(access, request, exc), str(exc)),
        )

    @app.exception_handler(wire.WireError)
    async def _bad_wire(request: "Request", exc: wire.WireError) -> "JSONResponse":
        return JSONResponse(
            status_code=400,
            content=wire.encode_error(wire.BAD_REQUEST, str(exc)),
        )

    # ------------------------------------------------------------------
    # the contract
    # ------------------------------------------------------------------
    @app.get("/health", summary="Is this a field service, and which protocol?")
    def health() -> dict:
        caps = access.capabilities()
        return {
            "ok": True,
            "protocol": wire.PROTOCOL,
            "cadence_minutes": caps.cadence_minutes,
            "observation_names": list(caps.observation_names),
            "model_ids": list(caps.model_ids),
            "retrospective": caps.retrospective,
        }

    @app.get("/capabilities", summary="cadence, names, and whether history is served")
    def capabilities() -> dict:
        return wire.encode_capabilities(access.capabilities())

    @app.get("/observations", summary="the times a name has data for, within [t0, t1]")
    def list_observations(
        name: str = Query(..., description="an observation name"),
        t0: str = Query(..., description="ISO-8601 UTC, inclusive"),
        t1: str = Query(..., description="ISO-8601 UTC, inclusive"),
    ) -> dict:
        times = access.list_observations(
            name, wire.time_from_wire(t0), wire.time_from_wire(t1)
        )
        return {"name": name, "times": [wire.time_to_wire(t) for t in times]}

    @app.get("/observation", summary="one observation field at one valid time")
    def get_observation(
        name: str = Query(...),
        valid_time: str = Query(..., description="ISO-8601 UTC, exactly"),
        encoding: str = Query(wire.DEFAULT_ENCODING, description="base64 or list"),
    ) -> dict:
        field = access.get_observation(name, wire.time_from_wire(valid_time))
        return wire.encode_field(field, encoding=_encoding(encoding))

    @app.get("/predictions", summary="the leads a run actually produced")
    def list_predictions(
        model_id: str = Query(...),
        init_time: str = Query(..., description="ISO-8601 UTC"),
    ) -> dict:
        leads = access.list_predictions(model_id, wire.time_from_wire(init_time))
        return {"model_id": model_id, "lead_minutes": [int(x) for x in leads]}

    @app.get("/prediction", summary="one prediction field for one lead")
    def get_prediction(
        model_id: str = Query(...),
        init_time: str = Query(...),
        lead_minutes: int = Query(..., description="whole minutes"),
        encoding: str = Query(wire.DEFAULT_ENCODING),
    ) -> dict:
        field = access.get_prediction(
            model_id, wire.time_from_wire(init_time), int(lead_minutes)
        )
        return wire.encode_field(field, encoding=_encoding(encoding))

    return app


# ======================================================================
def _encoding(name: str) -> str:
    if name not in wire.ENCODINGS:
        raise wire.WireError(
            f"unknown encoding {name!r}; expected one of {list(wire.ENCODINGS)}"
        )
    return name


def _classify(access: FieldAccess, request: "Request", exc: FieldUnavailable) -> str:
    """Say *why* in a way a log can be grepped, using only declared facts.

    The client turns all of these into the same exception, because the contract
    has one. The distinction exists so that "the model does not produce that
    lead" and "the archive has nothing at that hour" do not read identically in a
    server log at three in the morning.
    """
    params = request.query_params
    try:
        caps = access.capabilities()
    except Exception:  # noqa: BLE001 - classifying must never mask the 404
        return "unavailable"

    name = params.get("name")
    if name is not None and name not in caps.observation_names:
        return "unknown-name"
    model_id = params.get("model_id")
    if model_id is not None and model_id not in caps.model_ids:
        return "unknown-name"

    if model_id is not None and params.get("lead_minutes") is not None:
        try:
            leads = access.list_predictions(
                model_id, wire.time_from_wire(params.get("init_time", ""))
            )
        except Exception:  # noqa: BLE001
            return "unavailable"
        if int(params["lead_minutes"]) not in leads:
            return "no-such-run"

    return "unavailable"


def run(access: FieldAccess, *, host: str, port: int, log_level: str = "info") -> None:
    """Serve ``access`` until interrupted."""
    import uvicorn

    uvicorn.run(create_data_app(access), host=host, port=port, log_level=log_level)
