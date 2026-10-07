"""Errors shared by data and model adapters."""


class ConstraintError(ValueError):
    """A request or bundle violates an adapter constraint."""


class WeightsNotReady(RuntimeError):
    """Inference cannot run because the weight file is missing or does not match."""
