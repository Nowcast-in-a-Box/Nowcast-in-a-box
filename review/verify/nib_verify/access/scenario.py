"""Named starting points for the mock provider.

The generator in :mod:`~nib_verify.access.synthetic` has a lot of knobs, which is
right for a test that needs one specific thing and wrong for a person who wants
"something that looks like weather". This module is that second audience: a few
named bundles, each with a sentence saying what it is for.

Nothing here is a standard and nothing here is required.
:data:`SETTINGS` maps a name to constructor keyword arguments, and each bundle is
an addition to the constructor's defaults rather than a replacement for them.
Omitting ``--scenario`` altogether gives those defaults — ``dBZ`` reflectivity,
which is what the unit tests want and not what the shipped config wants.

How to use one::

    python -m nib_verify datafeed serve --access mock --scenario convective
    python -m nib_verify task run --access mock --scenario convective ...

    SyntheticAccess(**settings("convective"))               # from Python
    SyntheticAccess(**settings("convective"), peak=300.0)   # and then override

Why bother: with the generator's defaults a noiseless forecast *equals* the
truth, which is what the acceptance tests need and the worst possible thing to
develop an interface against. Every CSI comes back ``1.0``, every threshold looks
the same, and a broken display is indistinguishable from a working one.
"""

from __future__ import annotations

from typing import Any

#: On the 0-255 VIL scale the shipped config expects, but otherwise the
#: constructor's own defaults: one blob, a forecast that equals the truth, and
#: nothing missing. The scenario for "does the pipe work", not "does the metric".
SIMPLE: dict[str, Any] = {
    "peak": 250.0,
    "vmin": 0.0,
    "vmax": 255.0,
    "variable": "vil",
    "unit": "kg m-2",
}

#: Several discrete cells moving with a steady flow, two models of different
#: skill, and the coverage gaps a radar leaves. The case where thresholds matter
#: most: a lagging forecast loses the cores at a high threshold and barely notices
#: at a low one.
CONVECTIVE: dict[str, Any] = {
    "cells": 4,
    "drift": 0.05,
    "background": 4.0,
    "sigma": 0.09,
    "peak": 250.0,
    "vmin": 0.0,
    "vmax": 255.0,
    "variable": "vil",
    "unit": "kg m-2",
    "observation_names": ("radar-composite",),
    "model_ids": ("mlcast-det", "mlcast-alt"),
    "truth_noise": 4.0,
    "forecast_noise": 5.0,
    "forecast_lag_minutes": 10.0,
    "forecast_spread": 0.45,
    "skill_spread": 0.9,
    "missing_kind": "range",
    "missing_points": 60,
    "missing_runs_every": 5,
    "horizon_minutes": 60,
}

#: Stratiform rain over most of the domain. Every threshold has area, the field is
#: smooth, and a small timing error is most of the error — the opposite of
#: ``convective``, and the case that catches a scoring rule which only behaves at
#: one end of the scale.
WIDESPREAD: dict[str, Any] = {
    "cells": 2,
    "drift": 0.02,
    "background": 80.0,
    "sigma": 0.42,
    "peak": 170.0,
    "vmin": 0.0,
    "vmax": 255.0,
    "variable": "vil",
    "unit": "kg m-2",
    "observation_names": ("radar-composite",),
    "model_ids": ("mlcast-det", "mlcast-alt"),
    "truth_noise": 3.0,
    "forecast_noise": 4.0,
    "forecast_lag_minutes": 5.0,
    "skill_spread": 1.2,
    "missing_kind": "random",
    "missing_points": 40,
    "horizon_minutes": 60,
}

#: A provider that is awkward on purpose. Every third run produces nothing, one
#: lead is missing from every run that did happen, and a fifth of the grid is a
#: coverage hole. Use it to see the planner's rejection reasons and the run's
#: ``unavailable`` count do something, instead of reading about them.
PATCHY: dict[str, Any] = {
    "cells": 3,
    "drift": 0.04,
    "background": 8.0,
    "sigma": 0.12,
    "peak": 240.0,
    "vmin": 0.0,
    "vmax": 255.0,
    "variable": "vil",
    "unit": "kg m-2",
    "observation_names": ("radar-composite",),
    "model_ids": ("mlcast-det", "mlcast-alt"),
    "truth_noise": 4.0,
    "forecast_noise": 6.0,
    "forecast_lag_minutes": 10.0,
    "skill_spread": 1.0,
    "missing_kind": "range",
    "missing_points": 200,
    "missing_runs_every": 3,
    "drop_leads": (30,),
    "horizon_minutes": 60,
}

#: The ordering is the whole content of this table.
SETTINGS: dict[str, dict[str, Any]] = {
    "simple": SIMPLE,
    "convective": CONVECTIVE,
    "widespread": WIDESPREAD,
    "patchy": PATCHY,
}

DESCRIPTIONS = {
    "simple": "one blob on the VIL scale, a perfect forecast, nothing missing",
    "convective": "discrete cells on a flow, two models, radar coverage gaps",
    "widespread": "smooth stratiform rain over most of the domain",
    "patchy": "missing runs and a missing lead, on purpose",
}


def names() -> tuple[str, ...]:
    return tuple(SETTINGS)


def settings(name: str) -> dict[str, Any]:
    """The constructor arguments for ``name``.

    Raises ``ValueError`` for an unknown name and lists the real ones: a typo in
    a scenario silently generating the wrong weather would be worse than a
    refusal, and this is exactly the kind of flag that gets a typo.
    """
    if name not in SETTINGS:
        raise ValueError(
            f"unknown scenario {name!r}; expected one of {', '.join(SETTINGS)}"
        )
    return dict(SETTINGS[name])


def describe(name: str) -> str:
    return f"{name}: {DESCRIPTIONS[name]}"


def catalogue(sep: str = "\n") -> str:
    """Everything, for ``--help`` and for an error message.

    ``sep`` because argparse flattens newlines into spaces: a help string built
    with newlines arrives as one run-on line, which reads worse than no help.
    """
    width = max(len(n) for n in SETTINGS)
    return sep.join(f"{n.ljust(width)}  {DESCRIPTIONS[n]}" for n in SETTINGS)
