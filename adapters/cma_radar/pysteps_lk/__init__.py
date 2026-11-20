"""Official pySTEPS Lucas-Kanade baseline for CMA CREF.

The algorithm can run standalone through :mod:`model` and is also packaged as
an offline NiB Model Container through ``runtime.yaml`` and ``Dockerfile``.
"""

from .model import predict

__all__ = ["predict"]
