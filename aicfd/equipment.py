"""The equipment library: what a unit actually does, as a table.

A fan wall's datasheet prints one capacity, and that capacity is true at one
return air temperature — the one the unit was selected for. A chilled-water
coil transfers more when the air reaching it is warmer, and a real room almost
never returns air at the selection point. Comparing the heat a hall produces
against a single catalogue figure therefore compares it against something the
plant will not do (ADR-036).

So a unit is described here by a *table*: several manufacturer selections of
the same machine at the same conditions, differing only in the air it
receives. Anything between the rows is interpolated; anything outside them is
refused rather than extrapolated, because a coil curve is not a straight line
and the ends are exactly where guessing is worst.

Nothing in this module models physics. It reads what the manufacturer said and
interpolates between the points they gave.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from aicfd.yamledit import (
    INDENT,
    dig,
    find,
    merge,
    number,
    render,
    replace_list,
    set_scalar,
)

LIBRARY = Path(__file__).resolve().parent.parent / "equipment"

#: What each row of `capacity` carries, and the label a reader sees.
QUANTITIES = {
    "nscc_kw": "net sensible capacity (kW)",
    "airflow_m3h": "unit return airflow (m3/h)",
    "power_kw": "unit power input (kW)",
    "supply_c": "supply air (degC)",
}


#: "nobody has tried to fit a coil to this unit yet", as distinct from None,
#: which means "tried, and the selections do not support one".
_UNFITTED = object()


class UnknownModel(LookupError):
    """No file in the library for this model name."""


class OutsideTheTable(ValueError):
    """Asked for a return temperature the selections do not cover."""


@dataclass(frozen=True)
class Equipment:
    """One unit, as its manufacturer characterised it."""

    model: str
    family: str
    size: tuple[float, float, float]
    selection: dict
    capacity: tuple[dict, ...]
    """Rows sorted by ``return_c``, each carrying every key of QUANTITIES."""
    fans: dict
    curve: dict
    coil_split: float | None = None
    """How the coil's resistance divides at the design point, where the
    manufacturer states it. Otherwise `aicfd.coil.DEFAULT_AIR_SPLIT`."""
    design: dict = None
    """THE selection this unit is described by, or {} for a unit that names
    none. `capacity` is reference: see aicfd.coil."""
    weight_kg: float | None = None
    source: Path | None = None
    _coil: object = None
    """Filled on first use by `coil`; `_UNFITTED` until then."""

    # --- the table ------------------------------------------------------------

    @property
    def span(self) -> tuple[float, float]:
        """The return temperatures the unit's selections cover."""
        if self.capacity:
            return (self.capacity[0]["return_c"], self.capacity[-1]["return_c"])
        point = float(self.design["return_c"])
        return (point, point)

    def at(self, return_c: float, quantity: str) -> float:
        """One quantity at a return air temperature, interpolated.

        Linear between the two rows either side. The manufacturer's own points
        are reproduced exactly, which is the property that matters: a study
        run at a selection point quotes the selection, not an approximation of
        it.
        """
        if quantity not in QUANTITIES:
            raise KeyError(f"{quantity!r} is not in the table; have {list(QUANTITIES)}")
        low, high = self.span
        if not low - 1e-9 <= return_c <= high + 1e-9:
            raise OutsideTheTable(
                f"{self.model} was selected from {low:g} to {high:g} degC of "
                f"return air; {return_c:.2f} degC is outside that. A coil "
                f"curve is not a straight line, so this is not extrapolated -- "
                f"ask for a selection at that condition and add it to "
                f"equipment/{self.model}.yaml."
            )
        rows = self.capacity
        for a, b in zip(rows, rows[1:]):
            if a["return_c"] <= return_c <= b["return_c"]:
                span = b["return_c"] - a["return_c"]
                t = (return_c - a["return_c"]) / span if span else 0.0
                return a[quantity] + t * (b[quantity] - a[quantity])
        return rows[0][quantity] if return_c <= low else rows[-1][quantity]

    def available_kw(self, return_c: float) -> float:
        """Net sensible capacity at the air the unit really receives.

        The number a plant should be judged against. The catalogue figure is
        this evaluated at the selection point and nowhere else.
        """
        return self.at(return_c, "nscc_kw")

    @property
    def design_leaving_water_c(self) -> float | None:
        """What the selection says the water leaves at, for comparison with
        what a condition off the selection would ask of it (ADR-063)."""
        value = (self.selection or {}).get("leaving_water_c")
        return float(value) if value is not None else None

    @property
    def coil(self):
        """The heat exchanger behind the table, or None.

        Fitted from the selections themselves rather than asked for: what a
        manufacturer sends is a set of selections, and a tool that needed
        anything more would be a tool nobody could feed. With it the unit can
        be asked what it does at a condition it was never selected at, which
        is every condition a real room produces (ADR-039).
        """
        if self._coil is not _UNFITTED:
            return self._coil
        from aicfd import coil as model

        try:
            fitted, problem = model.fit(self), None
        except model.CannotFit as missing:
            # Not a second kind of unit, a file that is not finished. One
            # selection is all the fit needs and it is what every unit
            # carries, so this says which field is absent rather than
            # switching to a different way of answering (ADR-063).
            fitted, problem = None, str(missing)
        object.__setattr__(self, "_coil", fitted)
        object.__setattr__(self, "_coil_problem", problem)
        return fitted

    @property
    def coil_problem(self) -> str | None:
        """Why this unit has no coil, where it has none."""
        self.coil
        return getattr(self, "_coil_problem", None)

    def covers(self, return_c: float) -> bool:
        low, high = self.span
        return low - 1e-9 <= return_c <= high + 1e-9

    def design_point(self, return_c: float | None = None) -> dict:
        """Every quantity at one return temperature, for sizing before a solve.

        With no temperature, the unit's own design selection where it has
        one, and otherwise the warmest reference row.
        """
        if return_c is None and self.design.get("return_c") is not None:
            # The one selection this unit is described by. Preferred over any
            # row of `capacity`, which is reference (ADR-039).
            return {"return_c": float(self.design["return_c"]),
                    "nscc_kw": float(self.design["nscc_kw"]),
                    "airflow_m3h": float(self.design["airflow_m3h"]),
                    "power_kw": float(self.design.get("power_kw") or 0.0),
                    "supply_c": float(self.design["supply_c"])}
        target = self.span[1] if return_c is None else return_c
        return {"return_c": target,
                **{q: self.at(target, q) for q in QUANTITIES}}

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "family": self.family,
            "size": list(self.size),
            "weight_kg": self.weight_kg,
            "fans": self.fans,
            "selection": self.selection,
            "design": self.design,
            "capacity": [dict(row) for row in self.capacity],
            "curve": self.curve,
            "span": list(self.span),
            "quantities": QUANTITIES,
        }


# --- the library --------------------------------------------------------------


def path_for(model: str) -> Path:
    return LIBRARY / f"{model}.yaml"


def available() -> list[str]:
    """Every model the library holds, alphabetically."""
    if not LIBRARY.is_dir():
        return []
    return sorted(p.stem for p in LIBRARY.glob("*.yaml"))


def load(model: str) -> Equipment:
    """Read one unit out of the library."""
    path = path_for(model)
    if not path.is_file():
        raise UnknownModel(
            f"no equipment file for {model!r} in {LIBRARY}. "
            f"Have: {', '.join(available()) or '(none)'}"
        )
    return parse(yaml.safe_load(path.read_text()), source=path)


def parse(raw: dict, source: Path | None = None) -> Equipment:
    """Validate a unit's description and sort its table.

    Validated here rather than trusted, because every number in it ends up in
    a report: a missing column would otherwise surface as a KeyError halfway
    through post-processing a run that took eleven minutes.
    """
    # Completeness is not checked here. ONE selection describes a unit -- the
    # design selection -- and whether it is there is the coil fit's business:
    # `CannotFit` names the missing field, and `coil_problem` carries it to
    # the result (ADR-063). A second gate here demanded a design selection or
    # else a pair of capacity rows, and a reader holding a single selection
    # read that as a minimum of two. It never was the rule, and a half-written
    # unit has to be loadable to be finished (ADR-065).
    rows = raw.get("capacity") or []
    table = []
    for i, row in enumerate(rows):
        missing = [k for k in ("return_c", *QUANTITIES) if row.get(k) is None]
        if missing:
            raise ValueError(
                f"{raw.get('model', '?')} capacity row {i + 1} is missing "
                f"{', '.join(missing)}"
            )
        table.append({k: float(row[k]) for k in ("return_c", *QUANTITIES)})
    table.sort(key=lambda r: r["return_c"])
    if len({r["return_c"] for r in table}) != len(table):
        raise ValueError(f"{raw.get('model', '?')} has two rows at the same return_c")
    size = raw.get("size") or [0.0, 0.0, 0.0]
    return Equipment(
        model=raw["model"],
        family=raw.get("family", ""),
        size=tuple(float(v) for v in size),  # type: ignore[arg-type]
        selection=dict(raw.get("selection") or {}),
        capacity=tuple(table),
        fans=dict(raw.get("fans") or {}),
        curve=dict(raw.get("curve") or {}),
        design=dict(raw.get("design") or {}),
        coil_split=(raw.get("coil") or {}).get("air_split"),
        weight_kg=raw.get("weight_kg"),
        source=source,
        _coil=_UNFITTED,
    )


#: The fields the page may edit, as dotted paths into the file. `model` is not
#: here on purpose: renaming a unit in place would silently break every spec
#: that points at the old name, so a new name is a NEW FILE (`save_as`).
EDITABLE = (
    "family",
    "size",
    "weight_kg",
    "fans.count",
    "fans.type",
    "fans.module",
    "fans.modulation",
    "selection.elevation_m",
    "selection.esp_pa",
    "selection.entering_water_c",
    "selection.leaving_water_c",
    "selection.entering_air_rh",
    "design.return_c",
    "design.supply_c",
    "design.airflow_m3h",
    "design.nscc_kw",
    "design.power_kw",
    "curve.measured",
)

#: These files are written with two spaces per level, and the surgical editing
#: below relies on it. Anything hand-written that uses a different indent will
#: simply not be found, and the value will be left alone rather than corrupted.


def save(model: str, raw: dict) -> Equipment:
    """Write edits back, keeping every comment in the file.

    Parsed first: a table edited into an invalid state must fail before it
    replaces a good one on disk.

    Then each field is swapped in *textually*, because `yaml.safe_dump` would
    rewrite the whole file and throw away every comment in it — and in an
    equipment file the comments are the provenance: which selections these
    numbers came from, who issued them, on what date, at what conditions. A
    table of numbers nobody can trace is worth less than no table (ADR-036).
    """
    current = yaml.safe_load(path_for(model).read_text()) if path_for(model).is_file() else {}
    unit = parse(merge(current, {**raw, "model": model}))
    path = path_for(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        path.write_text(_fresh(unit, raw))
        return unit

    lines = path.read_text().splitlines()
    for dotted in EDITABLE:
        value = dig(raw, dotted.split("."))
        if value is not None:
            set_scalar(lines, dotted.split("."), value)
    if raw.get("capacity") is not None:
        replace_list(lines, ["capacity"], [_row(r) for r in unit.capacity])
    points = (raw.get("curve") or {}).get("points")
    if points is not None:
        replace_list(
            lines, ["curve", "points"],
            [f"{INDENT * 2}- [{number(q)}, {number(p)}]" for q, p in points],
        )
    path.write_text("\n".join(lines) + "\n")
    return unit


def save_as(new_model: str, source_model: str, raw: dict) -> Equipment:
    """Copy a unit under a new name, then apply the edits to the copy.

    The case for this is the ordinary one: the same coil sold by a different
    manufacturer, or the same machine under a new part number. Renaming in
    place would break every spec pointing at the old name without saying so,
    and both units usually need to exist anyway — one for the studies already
    run, one for the new work.

    The copy carries a line saying where it came from, because a table whose
    numbers were measured on another machine has to say so somewhere.
    """
    if not new_model or "/" in new_model or "\\" in new_model:
        raise ValueError(f"{new_model!r} is not a usable model name")
    target = path_for(new_model)
    if target.exists():
        raise ValueError(f"{new_model} already exists in the library")
    source = path_for(source_model)
    if not source.is_file():
        raise UnknownModel(f"no equipment file for {source_model!r}")
    lines = source.read_text().splitlines()
    set_scalar(lines, ["model"], new_model)
    header = (
        f"# Copied from {source_model}. Every number below is that unit's",
        "# until someone replaces it: check the selections before quoting this",
        "# machine's capacity from them.",
        "#",
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join([*header, *lines]) + "\n")
    try:
        return save(new_model, raw)
    except Exception:
        target.unlink(missing_ok=True)  # never leave a broken copy behind
        raise


# --- writing into a file without disturbing it --------------------------------


def _row(row: dict) -> str:
    return (
        f"{INDENT}- {{"
        + ", ".join(f"{k}: {number(row[k])}" for k in ("return_c", *QUANTITIES))
        + "}"
    )


def _fresh(unit: Equipment, raw: dict) -> str:
    """A whole file, for a unit the library has never seen."""
    body = {
        "model": unit.model,
        "family": unit.family,
        "size": list(unit.size),
        **({"weight_kg": unit.weight_kg} if unit.weight_kg else {}),
        **({"fans": unit.fans} if unit.fans else {}),
        **({"selection": unit.selection} if unit.selection else {}),
        "capacity": [dict(row) for row in unit.capacity],
        **({"curve": unit.curve} if unit.curve else {}),
    }
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)
