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

LIBRARY = Path(__file__).resolve().parent.parent / "equipment"

#: What each row of `capacity` carries, and the label a reader sees.
QUANTITIES = {
    "nscc_kw": "net sensible capacity (kW)",
    "airflow_m3h": "unit return airflow (m3/h)",
    "power_kw": "unit power input (kW)",
    "supply_c": "supply air (degC)",
}


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
    weight_kg: float | None = None
    source: Path | None = None

    # --- the table ------------------------------------------------------------

    @property
    def span(self) -> tuple[float, float]:
        """The return temperatures the selections cover."""
        return (self.capacity[0]["return_c"], self.capacity[-1]["return_c"])

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

    def covers(self, return_c: float) -> bool:
        low, high = self.span
        return low - 1e-9 <= return_c <= high + 1e-9

    def design_point(self, return_c: float | None = None) -> dict:
        """Every quantity at one return temperature, for sizing before a solve.

        With no temperature, the warmest selection: it is the one a plant is
        normally sized on, and it is the only row that is a choice rather than
        an interpolation.
        """
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
    rows = raw.get("capacity") or []
    if len(rows) < 2:
        raise ValueError(
            f"{raw.get('model', '?')} needs at least two selections to "
            f"interpolate between; got {len(rows)}"
        )
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
        weight_kg=raw.get("weight_kg"),
        source=source,
    )


def save(model: str, raw: dict) -> Equipment:
    """Write an edited capacity table back, keeping the rest of the file.

    Parsed first on purpose: a table edited into an invalid state must fail
    before it replaces a good one on disk.

    Then the rows are swapped in *textually*, because `yaml.safe_dump` would
    rewrite the whole file and throw away every comment in it — and in an
    equipment file the comments are the provenance: which selections these
    numbers came from, who issued them, on what date, at what conditions.
    A table of numbers nobody can trace is worth less than no table (ADR-036).
    """
    unit = parse({**raw, "model": model})
    path = path_for(model)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        "  - {"
        + ", ".join(
            f"{key}: {_number(row[key])}" for key in ("return_c", *QUANTITIES)
        )
        + "}"
        for row in unit.capacity
    ]
    if path.is_file():
        path.write_text(_replace_block(path.read_text(), "capacity", rows))
    else:
        path.write_text(
            yaml.safe_dump({**raw, "model": model}, sort_keys=False, allow_unicode=True)
        )
    return unit


def _number(value: float) -> str:
    """Round-trip a float without a trailing `.0` on whole numbers."""
    return f"{value:g}"


def _replace_block(text: str, key: str, rows: list[str]) -> str:
    """Swap the list under ``key:`` for ``rows``, leaving everything else.

    Everything else includes the comment lines between ``key:`` and its first
    item, which is where the column meanings are written.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == f"{key}:")
    except StopIteration as error:
        raise ValueError(f"no {key}: block to replace") from error
    first = next(
        (i for i in range(start + 1, len(lines)) if lines[i].lstrip().startswith("- ")),
        None,
    )
    if first is None:
        raise ValueError(f"the {key}: block has no items")
    end = first
    while end < len(lines) and (
        lines[end].lstrip().startswith("- ") or not lines[end].strip()
    ):
        if not lines[end].strip():
            break
        end += 1
    return "\n".join([*lines[:first], *rows, *lines[end:]]) + "\n"
