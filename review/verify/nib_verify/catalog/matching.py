"""Data–model matching.

A local stand-in for module **A2 Matcher**, which the architecture map still
lists as `design`. The rules are taken verbatim from
`nib-architecture/rfc/matching.md`, so that when A2 lands we can delete this
file and call theirs instead.

    Pair a data package and a model package by task profile, then by the field
    constraints we already check. ... An empty intersection is a hard error: the
    UI must not offer that pair.

Two deliberate behaviours:

* A comparison we cannot make because neither side declared the value is a
  **warning**, not an error. Refusing a pair over a missing manifest field would
  make an incomplete manifest unusable, and the schema is not final.
* **Nothing here converts an array.** If a pair needs conversion it is not a
  pair; that is exactly the mistake the RFC is written against.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .packages import FieldSpec, PackageManifest


@dataclass
class MatchResult:
    data_id: str
    model_id: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {
            "data": self.data_id,
            "model": self.model_id,
            "ok": self.ok,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }

    def describe(self) -> str:
        mark = "ok  " if self.ok else "FAIL"
        line = f"{mark} {self.data_id} + {self.model_id}"
        for problem in self.errors:
            line += f"\n       ! {problem}"
        for note in self.warnings:
            line += f"\n       ~ {note}"
        return line


def check_pair(data: PackageManifest, model: PackageManifest) -> MatchResult:
    """Apply the Matcher's hard errors and warnings to one pair."""
    result = MatchResult(data_id=data.id, model_id=model.id)

    if not data.is_data:
        result.errors.append(f"{data.id} is not a data package")
    if not model.is_model:
        result.errors.append(f"{model.id} is not a model package")
    if result.errors:
        return result

    # 1. profile intersection — the first and decisive check
    if not data.offers:
        result.errors.append(f"{data.id} offers no task profile")
    elif model.requires is None:
        result.errors.append(f"{model.id} requires no task profile")
    elif model.requires not in data.offers:
        result.errors.append(
            f"{model.id} requires {model.requires!r} but {data.id} offers "
            f"{list(data.offers)}"
        )

    # 2. cadence
    data_cadence = data.cadence_minutes
    model_cadence = model.cadence_minutes
    if data_cadence and model_cadence and data_cadence != model_cadence:
        result.errors.append(
            f"cadence differs: {data.id} is {data_cadence} min, "
            f"{model.id} is {model_cadence} min"
        )
    elif not (data_cadence and model_cadence):
        result.warnings.append("cadence not declared by both sides; not checked")

    # 3. context frames
    needed = model.required_context_frames
    available = _data_context_frames(data)
    if needed is not None and available is not None and needed > available:
        result.errors.append(
            f"{model.id} needs {needed} context frames but {data.id} provides "
            f"{available}"
        )
    elif needed is not None and available is None:
        result.warnings.append(
            f"{model.id} needs {needed} context frames; {data.id} does not say how "
            "many it has"
        )

    # 4. spatial shape
    data_shape = _shape_of(data, data.fields or data.input_fields)
    model_shape = _shape_of(model, model.input_fields or model.fields)
    if data_shape and model_shape and data_shape != model_shape:
        result.errors.append(
            f"spatial shape differs: {data.id} is {data_shape}, "
            f"{model.id} is {model_shape}"
        )
    elif not (data_shape and model_shape):
        result.warnings.append("spatial shape not declared by both sides; not checked")

    # 5. output length against the data target
    target_frames = _data_target_frames(data)
    output_frames = _frame_count_of(model.output_fields)
    if target_frames is not None and output_frames is not None:
        if target_frames != output_frames:
            result.errors.append(
                f"{model.id} emits {output_frames} frames but {data.id} expects "
                f"{target_frames} target frames"
            )

    # 6. fields the model needs but the data does not declare
    missing = _missing_fields(data, model)
    if missing:
        result.errors.append(
            f"{data.id} does not declare {missing}, which {model.id} requires"
        )

    # warnings only
    _dtype_notes(data, model, result)
    _domain_notes(data, model, result)
    return result


def compatible_pairs(
    data_packages, model_packages, *, limit_warnings: bool = True
) -> list[MatchResult]:
    """Every pair that passes.  Intended for a UI picker, not a report."""
    out = []
    for data in data_packages:
        for model in model_packages:
            result = check_pair(data, model)
            if result.ok and (not limit_warnings or not result.warnings):
                out.append(result)
    return out


# ----------------------------------------------------------------------
def _data_context_frames(data: PackageManifest) -> int | None:
    for spec in data.fields:
        value = _as_int(spec.frame_count)
        if value is not None:
            return value
    declared = data.raw.get("context_frames")
    if isinstance(declared, dict):
        value = declared.get("frame_count") or declared.get("count")
        return _as_int(value)
    return _as_int(data.raw.get("context_frame_count"))


def _data_target_frames(data: PackageManifest) -> int | None:
    declared = data.raw.get("target_frames")
    if declared is not None:
        return _as_int(declared)
    for spec in data.fields:
        frames = spec.raw.get("target_frame_count")
        value = _as_int(frames)
        if value is not None:
            return value
    return None


def _frame_count_of(specs) -> int | None:
    for spec in specs:
        value = _as_int(spec.frame_count)
        if value is not None:
            return value
    return None


def _shape_of(package: PackageManifest, specs) -> tuple[int, ...] | None:
    for spec in specs:
        if spec.shape:
            return tuple(spec.shape)
    declared = package.raw.get("spatial_shape") or package.raw.get("shape")
    if isinstance(declared, (list, tuple)):
        out = []
        for item in declared:
            value = _as_int(item)
            if value is None:
                return None
            out.append(value)
        return tuple(out) or None
    return None


def _missing_fields(data: PackageManifest, model: PackageManifest) -> list[str]:
    """Model inputs the data package never mentions, when both speak of fields."""
    wanted = {f.name.lower() for f in model.input_fields if f.name}
    if not wanted:
        wanted = {f.name.lower() for f in model.fields if f.name}
    if not wanted:
        return []
    have = {f.name.lower() for f in data.fields if f.name}
    if not have:
        return []  # the data side is silent; not our place to judge
    return sorted(wanted - have)


def _dtype_notes(data: PackageManifest, model: PackageManifest, result: MatchResult) -> None:
    data_dtypes = {f.name.lower(): f.dtype for f in data.fields if f.name}
    for spec in (model.input_fields or model.fields):
        if not spec.name:
            continue
        theirs = data_dtypes.get(spec.name.lower())
        if theirs and spec.dtype and theirs != spec.dtype:
            result.warnings.append(
                f"dtype differs for {spec.name}: {theirs} -> {spec.dtype} "
                "(the model adapter casts)"
            )


def _domain_notes(data: PackageManifest, model: PackageManifest, result: MatchResult) -> None:
    for key in ("region", "domain", "training_domain"):
        theirs = data.raw.get(key)
        ours = model.raw.get(key)
        if theirs and ours and theirs != ours:
            result.warnings.append(
                f"{key} differs: {data.id} says {theirs!r}, {model.id} says {ours!r}"
            )


def _as_int(value) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
