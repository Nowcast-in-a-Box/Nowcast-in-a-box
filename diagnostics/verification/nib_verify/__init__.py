"""NiB verification module (D1).

One config, one set of verification tasks.  Three stages: scheduling turns a
config into tasks, metrics turn each frame pair into additive quantities, and
queries re-aggregate those quantities into numbers.

Public entry points:

* ``nib_verify.access``  — the field contract, plus the mock providers
* ``nib_verify.config``  — the verification config and its mutability rules
* ``nib_verify.core``    — regridding, additive quantities, metric formulas
* ``nib_verify.store``   — SQLite storage for quantities and task state
* ``nib_verify.runner``  — task execution
* ``nib_verify.query``   — the read path
"""

__version__ = "0.1.0"
