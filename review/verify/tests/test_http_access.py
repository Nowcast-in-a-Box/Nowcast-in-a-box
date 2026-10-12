"""The HTTP client, against the reference service, over a real socket.

The service is a real one on a real port. A stub transport would test everything
about the client except the parts that can be wrong: whether a socket left open
by the last call is still usable, and whether a server that is not there looks
different from one that is.

The test worth reading first is
:func:`test_a_wrong_address_is_an_error_not_an_empty_archive`. Every request to a
mistyped address 404s, and mapping a 404 to ``FieldUnavailable`` would turn that
into a run that verifies nothing and reports success.
"""

from __future__ import annotations

import http.client
import json
import urllib.request
from datetime import timedelta

import numpy as np
import pytest

from nib_verify.access import (
    FieldUnavailable,
    HttpAccess,
    HttpAccessError,
    SyntheticAccess,
    wire,
)

from .conftest import T0, make_access, serve_data


# ======================================================================
# the contract, over HTTP
# ======================================================================
def test_capabilities_cross_the_socket(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        caps = access.capabilities()
    assert caps.cadence_minutes == 10
    assert caps.observation_names == ("radar-composite",)
    assert caps.model_ids == ("mlcast-det",)
    assert caps.unit_of("radar-composite") == "kg m-2"


def test_capabilities_are_asked_for_once(data_service):
    """Every caller from config validation onwards wants them, and they cannot
    change while the process runs."""
    url, counted = data_service
    with HttpAccess(url) as access:
        for _ in range(5):
            access.capabilities()
    assert counted.log.calls.get("capabilities") == 1


def test_observations_can_be_listed(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        times = access.list_observations("radar-composite", T0, T0 + timedelta(hours=1))
    assert len(times) == 7
    assert times[0] == T0
    assert all(t.tzinfo is not None for t in times)
    assert (times[1] - times[0]) == timedelta(minutes=10)


def test_an_observation_field_crosses_the_socket(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        field = access.get_observation("radar-composite", T0)
    assert field.coords.shape == (24, 32)
    assert field.valid_time == T0
    assert field.unit == "kg m-2"
    assert field.name == "radar-composite"
    assert field.data.dtype == np.float64
    assert field.missing.any(), "the fixture provider has missing points"


def test_what_comes_back_is_what_the_in_process_provider_has():
    """The client is meant to be transparent: same numbers, different wire."""
    local = make_access(missing_points=150)
    from nib_verify.access import CountingAccess

    with serve_data(CountingAccess(make_access(missing_points=150))) as url:
        with HttpAccess(url) as access:
            remote = access.get_observation("radar-composite", T0)

    direct = local.get_observation("radar-composite", T0)
    assert np.array_equal(remote.data, direct.data, equal_nan=True)
    assert np.array_equal(remote.missing, direct.missing)
    assert np.array_equal(remote.coords.lon, direct.coords.lon)
    assert np.array_equal(remote.coords.lat, direct.coords.lat)


def test_a_prediction_field_crosses_the_socket(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        leads = access.list_predictions("mlcast-det", T0)
        field = access.get_prediction("mlcast-det", T0, leads[0])
    assert leads == [10, 20, 30, 40, 50, 60]
    assert field.valid_time == T0 + timedelta(minutes=leads[0])
    assert field.coords.shape == (20, 26)
    assert field.name == "mlcast-det"


def test_the_server_was_asked_for_exactly_what_the_client_asked_for(data_service):
    """A client that quietly asked for something else would still pass every
    test above, because the answer would still be a plausible field."""
    url, counted = data_service
    with HttpAccess(url) as access:
        access.get_observation("radar-composite", T0)
        access.get_prediction("mlcast-det", T0, 20)
        access.list_observations("radar-composite", T0, T0 + timedelta(hours=1))

    assert counted.log.calls["get_observation"] == 1
    assert counted.log.calls["get_prediction"] == 1
    assert counted.log.calls["list_observations"] == 1


def test_nothing_is_cached(data_service):
    """Reading the same field twice is two reads. Caching here would break the
    one thing the module promises about reads, silently."""
    url, counted = data_service
    with HttpAccess(url) as access:
        for _ in range(4):
            access.get_observation("radar-composite", T0)
    assert counted.log.calls["get_observation"] == 4


# ======================================================================
# the contract's single exception
# ======================================================================
def test_an_unknown_name_is_unavailable(data_service):
    url, _ = data_service
    with HttpAccess(url) as access, pytest.raises(FieldUnavailable, match="unknown"):
        access.get_observation("nobody-serves-this", T0)


def test_an_off_cadence_time_is_unavailable_not_interpolated(data_service):
    url, _ = data_service
    with HttpAccess(url) as access, pytest.raises(FieldUnavailable, match="cadence"):
        access.get_observation("radar-composite", T0 + timedelta(seconds=1))


def test_an_unlisted_lead_is_unavailable(data_service):
    url, _ = data_service
    with HttpAccess(url) as access, pytest.raises(FieldUnavailable, match="lead 25"):
        access.get_prediction("mlcast-det", T0, 25)


def test_an_unknown_model_is_unavailable(data_service):
    url, _ = data_service
    with HttpAccess(url) as access, pytest.raises(FieldUnavailable, match="unknown"):
        access.list_predictions("no-such-model", T0)


# ======================================================================
# ... and everything else is not
# ======================================================================
def test_a_wrong_address_is_an_error_not_an_empty_archive(data_service):
    """The trap. Every request to a mistyped address 404s; if that came back as
    ``FieldUnavailable`` the planner would report a run with no data in it, and
    the run would look like it succeeded."""
    url, _ = data_service
    with HttpAccess(url + "/wrong-prefix") as access:
        with pytest.raises(HttpAccessError) as caught:
            access.get_observation("radar-composite", T0)
    assert "not a field access endpoint" in str(caught.value)
    assert "Check the address" in str(caught.value)


def test_a_server_that_is_not_there_is_an_error():
    with HttpAccess("http://127.0.0.1:1") as access:  # nothing listens on port 1
        with pytest.raises(HttpAccessError, match="could not reach"):
            access.capabilities()


def test_the_address_appears_once_in_a_transport_error(data_service):
    """A doubled path prefix in the message sends the reader to the wrong place."""
    url, _ = data_service
    with HttpAccess(url + "/wrong-prefix") as access:
        with pytest.raises(HttpAccessError) as caught:
            access.capabilities()
    assert "wrong-prefix/wrong-prefix" not in str(caught.value)


def test_a_body_that_is_not_json_is_an_error():
    with serve_data(make_access()) as url, HttpAccess(url) as access:
        access._connection = _Stub(status=200, body=b"<html>hello</html>")
        with pytest.raises(HttpAccessError, match="not JSON"):
            access.capabilities()


def test_a_server_error_is_an_error_not_an_unavailable_field():
    with serve_data(make_access()) as url, HttpAccess(url) as access:
        access._connection = _Stub(
            status=500, body=json.dumps({"error": "internal", "detail": "boom"}).encode()
        )
        with pytest.raises(HttpAccessError, match="500"):
            access.capabilities()


def test_a_bad_request_is_an_error_not_an_unavailable_field():
    with serve_data(make_access()) as url, HttpAccess(url) as access:
        access._connection = _Stub(
            status=400, body=json.dumps({"error": "bad-request", "detail": "nope"}).encode()
        )
        with pytest.raises(HttpAccessError, match="bad-request"):
            access.capabilities()


def test_a_payload_that_does_not_hold_together_is_an_error(data_service):
    """The decoder's rules reach the caller as one exception, not as a crash
    halfway through a run."""
    url, _ = data_service
    with HttpAccess(url) as access:
        access._connection = _Stub(
            status=200,
            body=json.dumps({
                "name": "x", "variable": "v", "unit": "dBZ",
                "valid_time": "2026-01-01T00:00:00Z",
                "lon": wire.encode_array(np.zeros(2)),
                "lat": wire.encode_array(np.zeros(2)),
                "values": wire.encode_array(np.zeros((3, 3))),
            }).encode(),
        )
        with pytest.raises(HttpAccessError, match="unusable field"):
            access.get_observation("radar-composite", T0)


# ======================================================================
# transport
# ======================================================================
class _Stub:
    """Stands in for one connection, to answer with a body of our choosing."""

    def __init__(self, *, status: int, body: bytes) -> None:
        self._status, self._body = status, body

    def request(self, *args, **kwargs) -> None:
        return None

    def getresponse(self):
        status, body = self._status, self._body

        class _Response:
            def read(self_inner) -> bytes:
                return body

            status = self._status
            reason = "stub"

        return _Response()

    def close(self) -> None:
        return None


class _Flaky(_Stub):
    """Fails the first ``n`` requests the way a closed keep-alive socket does."""

    def __init__(self, inner, failures: int) -> None:
        self.inner, self.left = inner, failures
        super().__init__(status=200, body=b"{}")

    def request(self, *args, **kwargs):
        if self.left > 0:
            self.left -= 1
            raise http.client.RemoteDisconnected("remote closed the connection")
        return self.inner.request(*args, **kwargs)

    def getresponse(self):
        return self.inner.getresponse()


def test_a_closed_keep_alive_is_retried_once(data_service):
    """Servers close idle sockets, and a person looking at the review page is an
    idle socket. This must not surface as a failed run."""
    url, _ = data_service
    with HttpAccess(url) as access:
        access._connection = _Flaky(access._connect(), 1)
        field = access.get_observation("radar-composite", T0)
    assert field.coords.shape == (24, 32)


def test_a_connection_that_keeps_failing_gives_up_after_two_attempts(monkeypatch):
    """One retry, not a loop: a provider that is genuinely down should fail a
    run in seconds, not hold the lock until somebody notices."""
    attempts: list[int] = []

    class Dead:
        def request(self, *args, **kwargs):
            raise http.client.RemoteDisconnected("remote closed the connection")

        def close(self) -> None:
            return None

    with serve_data(make_access()) as url, HttpAccess(url) as access:
        def connect():
            attempts.append(1)
            return Dead()

        monkeypatch.setattr(access, "_connect", connect)
        with pytest.raises(HttpAccessError, match="could not reach"):
            access.capabilities()

    assert len(attempts) == 2


def test_the_connection_is_reused_across_calls(data_service):
    """Not a performance claim: a client that reconnected every time would pass
    every other test here, and would make the retry logic untestable."""
    url, _ = data_service
    with HttpAccess(url) as access:
        access.capabilities()
        first = access._connection
        for _ in range(10):
            access.get_observation("radar-composite", T0)
        assert access._connection is first


def test_the_client_can_be_closed_twice(data_service):
    url, _ = data_service
    access = HttpAccess(url)
    access.get_observation("radar-composite", T0)
    access.close()
    access.close()


# ======================================================================
# addressing
# ======================================================================
def test_a_path_prefix_is_kept(data_service):
    """A provider behind a reverse proxy is not a special case."""
    url, _ = data_service
    with HttpAccess(url + "/") as access:
        assert access.get_observation("radar-composite", T0).unit == "kg m-2"


@pytest.mark.parametrize("bad", ["127.0.0.1:8770", "ftp://host/data", "http://", ""])
def test_an_address_that_is_not_an_address_is_refused(bad):
    with pytest.raises(ValueError):
        HttpAccess(bad)


def test_the_scheme_and_port_are_understood():
    assert (HttpAccess("http://a").scheme, HttpAccess("http://a").port) == ("http", 80)
    assert (HttpAccess("https://a").port, HttpAccess("https://a:8443").port) == (443, 8443)


def test_an_unknown_encoding_is_refused_at_construction(data_service):
    url, _ = data_service
    with pytest.raises(ValueError, match="unknown encoding"):
        HttpAccess(url, encoding="msgpack")


def test_describe_says_where_and_whether_anything_was_odd(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        assert url in access.describe()
        access.warnings.append("something")
        assert "1 warning" in access.describe()


# ======================================================================
# the human-readable encoding
# ======================================================================
def test_a_field_can_be_asked_for_as_readable_json(data_service):
    """``?encoding=list`` exists so a person can read a response with curl."""
    url, _ = data_service
    target = (f"{url}/observation?name=radar-composite"
              f"&valid_time=2026-01-01T00%3A00%3A00Z&encoding=list")
    with urllib.request.urlopen(target, timeout=10) as response:
        payload = json.loads(response.read())
    assert payload["values"]["encoding"] == "list"
    assert isinstance(payload["values"]["data"], list)
    assert len(payload["values"]["data"]) == 24


def test_a_client_can_be_built_to_use_it(data_service):
    url, _ = data_service
    with HttpAccess(url, encoding="list") as access:
        field = access.get_observation("radar-composite", T0)
    assert field.coords.shape == (24, 32)


def test_an_unknown_encoding_on_the_wire_is_a_bad_request(data_service):
    url, _ = data_service
    target = f"{url}/observation?name=radar-composite&valid_time=2026-01-01T00%3A00%3A00Z&encoding=msgpack"
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(target, timeout=10)
    assert caught.value.code == 400
    body = json.loads(caught.value.read())
    assert body["error"] == "bad-request"


# ======================================================================
# the service describes itself
# ======================================================================
def test_health_says_which_protocol_and_what_it_serves(data_service):
    url, _ = data_service
    with urllib.request.urlopen(f"{url}/health", timeout=10) as response:
        payload = json.loads(response.read())
    assert payload["ok"] is True
    assert payload["protocol"] == wire.PROTOCOL
    assert payload["cadence_minutes"] == 10
    assert payload["observation_names"] == ["radar-composite"]


def test_the_reference_service_serves_any_provider():
    """It wraps an interface, not the synthetic provider.

    ``Tiny`` below is written from scratch against the five methods, the way the
    platform's provider will be. If the service had special-cased the generator,
    this would not work.
    """
    from nib_verify.access import Capabilities, Coords, Field, FieldUnavailable

    class Tiny:
        name = "tiny-obs"

        def capabilities(self):
            return Capabilities(
                cadence_minutes=60,
                observation_names=(self.name,),
                model_ids=("tiny-model",),
                units={self.name: "mm", "tiny-model": "mm"},
            )

        def _field(self, who, when):
            return Field(
                data=np.arange(6, dtype=float).reshape(2, 3),
                coords=Coords(lon=np.array([0.0, 1.0, 2.0]), lat=np.array([0.0, 1.0])),
                valid_time=when,
                unit="mm",
                variable="rain",
                name=who,
            )

        def list_observations(self, name, t0, t1):
            return [T0] if name == self.name and t0 <= T0 <= t1 else []

        def get_observation(self, name, valid_time):
            if name != self.name or valid_time != T0:
                raise FieldUnavailable(f"{name} has nothing at {valid_time}")
            return self._field(name, valid_time)

        def list_predictions(self, model_id, init_time):
            return [60] if model_id == "tiny-model" else []

        def get_prediction(self, model_id, init_time, lead_minutes):
            if lead_minutes != 60:
                raise FieldUnavailable(f"no lead {lead_minutes}")
            return self._field(model_id, init_time + timedelta(minutes=60))

    with serve_data(Tiny()) as url, HttpAccess(url) as access:
        assert access.capabilities().cadence_minutes == 60
        assert access.get_observation("tiny-obs", T0).unit == "mm"
        assert access.list_predictions("tiny-model", T0) == [60]
        assert access.get_prediction("tiny-model", T0, 60).coords.shape == (2, 3)
