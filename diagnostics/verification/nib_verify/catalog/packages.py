"""Package manifests.

`AGENTS.md` forbids inventing a catalog tree for data and models:

    Do not invent a catalog tree for data and models until that layout is designed.

So this module never assumes one. It *discovers* manifests by walking a root the
caller names, and it recognises a manifest by **what the file contains**, not by
where it sits. Move every package somewhere else and discovery still works.

The manifest schema itself is still `design` status in the architecture map, and
the `FieldSpec` it refers to lives in a repository we do not have. So parsing is
tolerant: known keys are read through a list of accepted spellings, anything
unrecognised is kept verbatim in :attr:`PackageManifest.raw`, and nothing is
rejected for having a shape we have not seen yet.

What we do insist on is the one thing the RFC is firm about: a package declares
either what it *offers* (data) or what it *requires* (a model). Without that we
cannot tell the two apart, and guessing would be worse than reporting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

MANIFEST_FILE_NAME = "manifest.json"

DATA = "data"
MODEL = "model"
UNKNOWN = "unknown"


class ManifestError(ValueError):
    """A manifest that cannot be read at all."""


# ----------------------------------------------------------------------
# FieldSpec
# ----------------------------------------------------------------------
#: accepted spellings, in order of preference.  The schema is not final, so we
#: read generously rather than losing a package to a renamed key.
_ALIASES: dict[str, tuple[str, ...]] = {
    "name": ("name", "variable", "field", "id"),
    "unit": ("unit", "units", "unit_string"),
    "dtype": ("dtype", "dtypes", "type"),
    "shape": ("shape", "spatial_shape", "grid", "grid_shape"),
    "cadence_minutes": ("cadence_minutes", "cadence", "time_step_minutes",
                        "cadence_min"),
    "frame_count": ("frame_count", "frame_counts", "frames", "frame_count_target"),
    "fill_value": ("fill_value", "fill", "no_data", "nodata"),
    "missing": ("missing", "missing_value", "mask"),
}

#: keys we consume ourselves; everything else is preserved in ``raw``
_KNOWN_FIELD_KEYS = frozenset(k for names in _ALIASES.values() for k in names)


@dataclass(frozen=True)
class FieldSpec:
    """One field a package declares, with whatever detail it gave us."""

    name: str | None = None
    unit: str | None = None
    dtype: str | None = None
    shape: tuple[int, ...] | None = None
    cadence_minutes: int | None = None
    frame_count: Any = None
    fill_value: Any = None
    raw: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "FieldSpec":
        if not isinstance(raw, Mapping):
            return cls(raw={"value": raw})
        return cls(
            name=_text(_first(raw, "name")),
            unit=_text(_first(raw, "unit")),
            dtype=_text(_first(raw, "dtype")),
            shape=_shape(_first(raw, "shape")),
            cadence_minutes=_int(_first(raw, "cadence_minutes")),
            frame_count=_first(raw, "frame_count"),
            fill_value=_first(raw, "fill_value"),
            raw=dict(raw),
        )

    def describe(self) -> str:
        bits = [self.name or "(unnamed)"]
        if self.unit:
            bits.append(f"[{self.unit}]")
        if self.dtype:
            bits.append(self.dtype)
        if self.shape:
            bits.append("x".join(str(s) for s in self.shape))
        if self.cadence_minutes:
            bits.append(f"{self.cadence_minutes}min")
        if self.frame_count is not None:
            bits.append(f"{self.frame_count} frames")
        return " ".join(bits)


def _first(raw: Mapping[str, Any], key: str) -> Any:
    for alias in _ALIASES[key]:
        if alias in raw:
            return raw[alias]
    return None


# ----------------------------------------------------------------------
# PackageManifest
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class PackageManifest:
    """A data or model package, as it describes itself."""

    id: str
    kind: str
    path: Path | None = None
    offers: tuple[str, ...] = ()
    requires: str | None = None
    profile: str | None = None
    context_frames: Mapping[str, Any] = field(default_factory=dict)
    fields: tuple[FieldSpec, ...] = ()
    input_fields: tuple[FieldSpec, ...] = ()
    output_fields: tuple[FieldSpec, ...] = ()
    raw: Mapping[str, Any] = field(default_factory=dict)

    # -- convenience ----------------------------------------------------
    @property
    def is_data(self) -> bool:
        return self.kind == DATA

    @property
    def is_model(self) -> bool:
        return self.kind == MODEL

    @property
    def all_fields(self) -> tuple[FieldSpec, ...]:
        return self.fields + self.input_fields + self.output_fields

    @property
    def cadence_minutes(self) -> int | None:
        for spec in self.all_fields:
            if spec.cadence_minutes:
                return spec.cadence_minutes
        value = self.raw.get("cadence_minutes") or self.raw.get("cadence")
        return _int(value)

    @property
    def required_context_frames(self) -> int | None:
        value = self.context_frames.get("required_count")
        return _int(value)

    @property
    def context_mode(self) -> str | None:
        value = self.context_frames.get("mode")
        return str(value) if value is not None else None

    def fields_named(self, *names: str) -> list[FieldSpec]:
        wanted = {n.lower() for n in names}
        return [f for f in self.all_fields if f.name and f.name.lower() in wanted]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "path": str(self.path) if self.path else None,
            "offers": list(self.offers),
            "requires": self.requires,
            "context_frames": dict(self.context_frames),
            "fields": [f.describe() for f in self.all_fields],
            "cadence_minutes": self.cadence_minutes,
            "ruler_defaults": ruler_defaults(self),
        }

    def describe(self) -> str:
        if self.is_data:
            detail = f"offers {', '.join(self.offers) or '(nothing)'}"
        elif self.is_model:
            detail = f"requires {self.requires or '(nothing)'}"
            if self.required_context_frames:
                detail += f", needs {self.required_context_frames} context frames"
        else:
            detail = "declares neither offers nor requires"
        cadence = self.cadence_minutes
        if cadence:
            detail += f", {cadence} min"
        return f"{self.id:<20} [{self.kind}] {detail}"

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], path: Path | None = None) -> "PackageManifest":
        if not isinstance(raw, Mapping):
            raise ManifestError("manifest must be a JSON object")

        pid = raw.get("id") or raw.get("name") or raw.get("package")
        if not pid:
            raise ManifestError("manifest has no id")

        offers = _text_tuple(raw.get("offers"))
        requires = _text(raw.get("requires") or raw.get("requires_profile"))

        if offers and requires:
            kind = UNKNOWN
        elif offers:
            kind = DATA
        elif requires:
            kind = MODEL
        else:
            kind = UNKNOWN

        return cls(
            id=str(pid),
            kind=kind,
            path=path,
            offers=offers,
            requires=requires,
            profile=_text(raw.get("profile")),
            context_frames=_mapping(raw.get("context_frames")),
            fields=_field_list(raw.get("fields")),
            input_fields=_field_list(raw.get("input")),
            output_fields=_field_list(raw.get("output")),
            raw=dict(raw),
        )


# ----------------------------------------------------------------------
# discovery
# ----------------------------------------------------------------------
def looks_like_manifest(raw: Any) -> bool:
    """True when a parsed JSON object is plausibly a package manifest.

    The only structural claim the RFC makes is ``offers`` / ``requires``, so
    that is what we test for. A random ``manifest.json`` from a Python package
    will not have them.
    """
    return isinstance(raw, Mapping) and (
        "offers" in raw or "requires" in raw or "fields" in raw
    )


def find_manifest_files(root: str | Path) -> list[Path]:
    """Every ``manifest.json`` under ``root``, recursively, sorted for stability."""
    base = Path(root)
    if not base.exists():
        return []
    if base.is_file():
        return [base] if base.name.lower() == MANIFEST_FILE_NAME else []
    return sorted(
        p for p in base.rglob("*")
        if p.is_file() and p.name.lower() == MANIFEST_FILE_NAME
    )


def load_manifest(path: str | Path) -> PackageManifest:
    import json

    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{p}: {exc}") from None
    if not looks_like_manifest(raw):
        raise ManifestError(f"{p}: does not look like a package manifest")
    return PackageManifest.from_mapping(raw, path=p)


def ruler_defaults(package: PackageManifest) -> dict[str, Any]:
    """What a manifest can answer for the ruler, and nothing it cannot.

    A ground truth package already knows its unit and its step, so the form asks
    the package instead of asking a person to retype what the platform already
    declared. Everything it does not declare comes back as ``None`` and the form
    leaves that box empty: filling it in with a guess would be inventing the
    ruler, and the ruler is the part that decides whether two numbers are
    comparable.

    ``extent`` and ``resolution`` are the interesting case, because a manifest
    usually names a *region* (``east-china``) rather than a grid. A region name is
    not an extent, so nothing is derived from it — the box stays empty and
    somebody has to say where the grid actually is.
    """
    field = _primary_field(package)
    return {
        "variable": field.name if field else None,
        "unit": field.unit if field else None,
        "time_step_minutes": package.cadence_minutes,
        "extent": _extent(_declared(package, field, "extent", "bounds")),
        "resolution": _number(_declared(package, field, "resolution")),
    }


def _primary_field(package: PackageManifest) -> FieldSpec | None:
    """The field a config would verify: the first one that has a name."""
    for spec in package.all_fields:
        if spec.name:
            return spec
    return package.all_fields[0] if package.all_fields else None


def _declared(package: PackageManifest, field: FieldSpec | None, *names: str) -> Any:
    """A value from the manifest, looking on the package and then on the field.

    Two places because both are plausible and neither is wrong. The alias list is
    kept short on purpose — it goes away when the manifest schema is settled, the
    same as the rest of the tolerant reading in this module.
    """
    for source in (package.raw or {}, field.raw if field else {}):
        for name in names:
            value = source.get(name)
            if value is not None:
                return value
    return None


def _extent(value: Any) -> list[float] | None:
    """``(lon0, lat0, lon1, lat1)``, or nothing at all."""
    if isinstance(value, Mapping):
        keys = ("lon_min", "lat_min", "lon_max", "lat_max")
        numbers = [_number(value.get(k)) for k in keys]
        return numbers if all(n is not None for n in numbers) else None      # type: ignore[return-value]
    if isinstance(value, (list, tuple)) and len(value) == 4:
        numbers = [_number(v) for v in value]
        return numbers if all(n is not None for n in numbers) else None      # type: ignore[return-value]
    return None


def _number(value: Any) -> float | None:
    """A float, or ``None``. ``True`` is not a number here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass
class Catalog:
    """The packages we found, indexed by id."""

    packages: tuple[PackageManifest, ...] = ()
    roots: tuple[Path, ...] = ()
    skipped: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[PackageManifest]:
        return iter(self.packages)

    def __len__(self) -> int:
        return len(self.packages)

    def __bool__(self) -> bool:
        return bool(self.packages)

    # -- views ----------------------------------------------------------
    @property
    def data_packages(self) -> list[PackageManifest]:
        return [p for p in self.packages if p.is_data]

    @property
    def model_packages(self) -> list[PackageManifest]:
        return [p for p in self.packages if p.is_model]

    @property
    def data_ids(self) -> set[str]:
        return {p.id for p in self.data_packages}

    @property
    def model_ids(self) -> set[str]:
        return {p.id for p in self.model_packages}

    def get(self, package_id: str) -> PackageManifest | None:
        for p in self.packages:
            if p.id == package_id:
                return p
        return None

    def requires(self, model_id: str) -> str | None:
        p = self.get(model_id)
        return p.requires if p else None

    def offering(self, profile: str) -> list[PackageManifest]:
        return [p for p in self.data_packages if profile in p.offers]

    def problems(self) -> list[str]:
        """Human-readable structural complaints, for `catalog check`."""
        out = list(self.skipped)
        seen: dict[str, Path | None] = {}
        for p in self.packages:
            if p.id in seen:
                other = seen[p.id]
                out.append(
                    f"duplicate package id {p.id!r}: {other} and {p.path}"
                )
            seen[p.id] = p.path
            if p.kind == UNKNOWN:
                out.append(f"{p.id}: declares neither offers nor requires")
            if p.is_data and not p.fields:
                out.append(f"{p.id}: data package declares no fields")
            if p.is_model and not (p.input_fields or p.fields):
                out.append(f"{p.id}: model package declares no input fields")
            if p.is_model and not p.output_fields:
                out.append(f"{p.id}: model package declares no output fields")
        return out

    def as_dict(self) -> dict:
        return {
            "roots": [str(r) for r in self.roots],
            "packages": [p.as_dict() for p in self.packages],
            "skipped": list(self.skipped),
            "problems": self.problems(),
        }


def discover(roots: Sequence[str | Path]) -> Catalog:
    """Walk ``roots`` and collect every package manifest found.

    Discovery is by content, and the two failure modes are kept apart on
    purpose:

    * a ``manifest.json`` that is not shaped like a package manifest is
      **ignored** — other projects ship files with that name;
    * one that *is* shaped like a package manifest but cannot be read is
      recorded in ``skipped``, because that is a real problem someone wants to
      see.
    """
    import json
    resolved: list[Path] = []
    found: list[PackageManifest] = []
    skipped: list[str] = []
    seen_paths: set[Path] = set()

    for root in roots:
        base = Path(root)
        if not base.exists():
            skipped.append(f"root does not exist: {base}")
            continue
        resolved.append(base)
        for path in find_manifest_files(base):
            real = path.resolve()
            if real in seen_paths:
                continue
            seen_paths.add(real)
            try:
                raw = json.loads(path.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
                skipped.append(f"{path}: {exc}")
                continue
            if not looks_like_manifest(raw):
                # some other project's manifest.json; not ours to report
                continue
            try:
                found.append(PackageManifest.from_mapping(raw, path=path))
            except ManifestError as exc:
                skipped.append(f"{path}: {exc}")

    found.sort(key=lambda p: (p.kind, p.id))
    return Catalog(packages=tuple(found), roots=tuple(resolved), skipped=tuple(skipped))


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------
def _field_list(value: Any) -> tuple[FieldSpec, ...]:
    if value is None:
        return ()
    if isinstance(value, Mapping):
        # a mapping of name -> spec is also plausible
        return tuple(
            FieldSpec.from_mapping({**v, "name": v.get("name", k)})
            if isinstance(v, Mapping) else FieldSpec(name=str(k), raw={"value": v})
            for k, v in value.items()
        )
    if isinstance(value, (list, tuple)):
        return tuple(FieldSpec.from_mapping(v) for v in value)
    return ()


def _mapping(value: Any) -> Mapping[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _text_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value if v not in (None, ""))
    return ()


def _int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _shape(value: Any) -> tuple[int, ...] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        # {"height": h, "width": w} and similar
        keys = ("height", "width", "channels", "time")
        ordered = [value[k] for k in keys if k in value]
        value = ordered or list(value.values())
    if isinstance(value, (list, tuple)):
        out = []
        for item in value:
            number = _int(item)
            if number is None:
                return None
            out.append(number)
        return tuple(out) or None
    return None
