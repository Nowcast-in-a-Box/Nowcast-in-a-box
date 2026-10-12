"""The conformance checklist, and proof that it can fail.

`spec/field-access.md` ends with a list of conditions a provider has to meet.
:func:`~nib_verify.access.conformance.check_provider` runs them, and the first
half of this file shows the providers that ship with the module passing.

The second half is the part that matters. A checklist that every provider passes
is decoration, so each broken provider here breaks exactly one guarantee and the
test asserts *which* line catches it. If a check is ever weakened into something
that cannot fail, one of these stops failing and the suite says so.
"""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest

from nib_verify.access import (
    Capabilities,
    Field,
    FieldUnavailable,
    HttpAccess,
    SyntheticAccess,
    check_provider,
)
from nib_verify.access.conformance import CANDIDATE_DAYS

from .conftest import make_access, serve_data


def failing(report) -> set[str]:
    return {check.name for check in report.failures}


def named(report, name: str):
    matches = [c for c in report.checks if c.name == name]
    assert len(matches) == 1, f"expected one {name!r} check, got {len(matches)}"
    return matches[0]


# ======================================================================
# providers that should pass
# ======================================================================
def test_the_synthetic_provider_conforms():
    report = check_provider(make_access())
    assert report.ok, report.render()
    assert report.capabilities.cadence_minutes == 10


def test_a_provider_that_has_holes_conforms_and_proves_it():
    """With nothing missing, the missing-data checks have no witness and are
    reported as skips. This is the run where they are actually exercised."""
    report = check_provider(make_access(missing_points=300))

    assert report.ok, report.render()
    check = named(report, "missing points come back as NaN, never as zero")
    assert not check.skipped
    assert check.ok
    assert "300 missing" in check.detail


def test_a_finite_check_with_nothing_to_check_is_a_skip_not_a_pass():
    """The whole point of the report is not to say "conforms" about something
    that never ran."""
    report = check_provider(make_access())          # no missing points
    check = named(report, "missing points come back as NaN, never as zero")
    assert check.skipped
    assert check.ok
    assert report.ok                                # a skip is not a failure


def test_a_conforming_report_says_what_it_sampled():
    report = check_provider(make_access())
    assert "observation" in report.sampled
    assert "prediction" in report.sampled
    assert "radar-composite" in report.sampled["observation"]


def test_the_report_renders_as_text_and_as_json():
    report = check_provider(make_access())
    text = report.render()
    assert "conforms" in text
    assert "capabilities() reports a cadence" in text

    payload = report.as_dict()
    assert payload["ok"] is True
    assert payload["capabilities"]["cadence_minutes"] == 10
    assert all({"name", "ok", "skipped", "detail"} <= set(c) for c in payload["checks"])


def test_a_provider_with_no_observations_is_still_checkable():
    report = check_provider(make_access(observation_names=("a",), model_ids=("m",)))
    assert report.ok


# ======================================================================
# providers that should not
# ======================================================================
class Interpolating(SyntheticAccess):
    """Answers any time, which is exactly what the contract forbids."""

    def _require_aligned(self, t):
        return None


class WrongUnit(SyntheticAccess):
    """Says dBZ, hands back mm/h. A threshold of 35 means nothing across that."""

    def get_observation(self, name, valid_time):
        field = super().get_observation(name, valid_time)
        return Field(data=field.data, coords=field.coords, valid_time=field.valid_time,
                     unit="mm/h", variable=field.variable, name=field.name, mask=field.mask)


class LyingLeads(SyntheticAccess):
    """Lists a lead it cannot serve.

    The planner trusts this list, so a wrong entry becomes a task that dies
    later instead of a rejection now. Modelled from both sides, because a
    provider that lists a lead and then serves it is not broken — the defect is
    the disagreement between the two.
    """

    def list_predictions(self, model_id, init_time):
        return sorted(set(super().list_predictions(model_id, init_time)) | {15})

    def get_prediction(self, model_id, init_time, lead_minutes):
        if int(lead_minutes) == 15:
            raise FieldUnavailable("lead 15 was never produced")
        return super().get_prediction(model_id, init_time, lead_minutes)


class OffCadenceTimes(SyntheticAccess):
    """Serves times five minutes apart while declaring a ten-minute cadence."""

    def list_observations(self, name, t0, t1):
        return [t + timedelta(minutes=5 * i)
                for i, t in enumerate(super().list_observations(name, t0, t1))]


class NoUnits(SyntheticAccess):
    def capabilities(self):
        caps = super().capabilities()
        return Capabilities(caps.cadence_minutes, caps.observation_names,
                            caps.model_ids, caps.retrospective, {})


class Broken(SyntheticAccess):
    def capabilities(self):
        raise RuntimeError("the provider fell over on startup")


class GrumpyListing(SyntheticAccess):
    def list_observations(self, name, t0, t1):
        raise RuntimeError("the archive is not mounted")


def test_a_property_checked_on_several_fields_is_one_line():
    report = check_provider(make_access(missing_points=50))
    matches = [c for c in report.checks
               if c.name == "missing points come back as NaN, never as zero"]
    assert len(matches) == 1
    assert matches[0].subjects == 4          # one observation and three leads
    assert "4 field(s)" in matches[0].full_detail


@pytest.mark.parametrize("provider,name", [
    (Interpolating, "an off-cadence time raises rather than interpolating"),
    (WrongUnit, "the observation unit is the one capabilities declares"),
    (LyingLeads, "every listed lead can be fetched"),
    (OffCadenceTimes, "every served time is on the cadence"),
    (NoUnits, "every name declares a unit"),
])
def test_a_broken_guarantee_is_caught_by_name(provider, name):
    report = check_provider(provider())
    assert not report.ok, report.render()
    assert name in failing(report), report.render()


def test_a_provider_that_cannot_describe_itself_is_reported_not_raised():
    """One failure, and the checker survives to print it."""
    report = check_provider(Broken())

    assert not report.ok
    assert len(report.checks) == 1
    assert report.capabilities is None
    assert "fell over on startup" in report.checks[0].detail
    assert report.render().startswith("  [FAIL]")


def test_a_provider_that_throws_while_listing_is_a_failed_check():
    report = check_provider(GrumpyListing())
    assert not report.ok
    assert "list_observations answers" in failing(report)
    assert "not mounted" in report.render()


# ======================================================================
# the same checklist, against a service
# ======================================================================
def test_the_reference_service_conforms(data_service):
    url, _ = data_service
    with HttpAccess(url) as access:
        report = check_provider(access)
    assert report.ok, report.render()


def test_a_service_that_does_not_declare_its_holes_is_caught():
    """The one guarantee that only the client can enforce.

    The provider below writes NaN and leaves the mask empty, so the payload says
    "no holes" while carrying holes. The decoder widens the mask — the metrics
    stay correct — and the warning is what turns an invisible lie into a line in
    the report.
    """

    class Undeclared(SyntheticAccess):
        def get_observation(self, name, valid_time):
            field = super().get_observation(name, valid_time)
            data = field.data.copy()
            data[0, :3] = np.nan            # a hole, with no mask to say so
            return Field(data=data, coords=field.coords, valid_time=field.valid_time,
                         unit=field.unit, variable=field.variable, name=field.name,
                         mask=None)

    with serve_data(Undeclared()) as url, HttpAccess(url) as access:
        report = check_provider(access)
        # the numbers were still made safe, which is why this is enforced in the
        # decoder rather than only reported here
        assert access.warnings

    assert not report.ok
    assert "the provider declares what it is missing" in failing(report)
    assert any("not declared missing" in c.detail for c in report.failures)


def test_a_service_with_a_wrong_unit_is_caught():
    with serve_data(WrongUnit()) as url, HttpAccess(url) as access:
        report = check_provider(access)
    assert "the observation unit is the one capabilities declares" in failing(report)


def test_checking_a_dead_address_is_an_error_not_a_report():
    """``check_provider`` reports on a provider. A provider that is not there is
    not reported on; it is a failure to connect."""
    from nib_verify.access import HttpAccessError

    with HttpAccess("http://127.0.0.1:1") as access:
        report = check_provider(access)
    assert not report.ok
    assert "capabilities() answers" in failing(report)
    assert "could not reach" in report.render()

    with pytest.raises(HttpAccessError):
        access.capabilities()


def test_the_checklist_only_uses_the_five_methods():
    """If it reached for anything else it would not work against the platform's
    provider, which is the only reason it exists."""

    class Recorder:
        def __init__(self, inner):
            self.inner, self.seen = inner, set()

        def __getattr__(self, name):
            value = getattr(self.inner, name)   # raises if the inner has none,
            self.seen.add(name)                 # so probing for extras is not
            return value                        # mistaken for using them

    recorder = Recorder(make_access(missing_points=50))
    check_provider(recorder)

    assert recorder.seen <= {
        "capabilities", "list_observations", "get_observation",
        "list_predictions", "get_prediction",
    }, recorder.seen


def test_the_candidate_days_are_not_a_guess_that_hides_a_provider():
    """If nothing is found on any candidate day the checks skip rather than
    fail — and the report says why, instead of claiming conformance."""
    report = check_provider(make_access(), days=())
    assert report.ok
    assert named(report, "list_observations finds something").skipped
    assert "0 candidate window" in report.render() or "candidate window" in report.render()


def test_the_default_candidate_days_span_more_than_one_year():
    """An archive that stops a year ago is still a provider."""
    assert max(CANDIDATE_DAYS) - min(CANDIDATE_DAYS) > timedelta(days=365)
