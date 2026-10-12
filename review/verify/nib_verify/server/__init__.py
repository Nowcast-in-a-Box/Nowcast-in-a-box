"""The web service and the review page.

Nothing in here decides anything the rest of the package has not already
decided. The API is a thin layer over the library, the planner, the executor and
the query service, so the page and the command line behave identically — and if
they ever disagree, only one of them can be right.

The bind address is not chosen here either. ``config.yaml`` owns it, and
``nib_verify serve`` reads it.
"""

from .jobs import Job, JobRegistry
from .library import ConfigLibrary

__all__ = ["ConfigLibrary", "Job", "JobRegistry", "create_app"]


def create_app(*args, **kwargs):
    """Import lazily so the package works without fastapi installed."""
    from .app import create_app as _create_app

    return _create_app(*args, **kwargs)
