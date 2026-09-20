"""The rack type catalogue: what a cabinet IS, kept out of the case.

A case says how many cabinets stand where and what each one carries. What a
cabinet *is* -- how wide, how deep, how tall -- is a property of the product,
the same for every project that buys it, and typing it into each case is how
two halls of the same cabinet end up 5 mm apart with nobody able to say which
is right (ADR-075).

So `racks/<id>.yaml` holds one cabinet type, quoting the document it came
from, and a case names it:

    racks:
      type: generic-600-1200-45u     # the standard cabinet
      row:
        - {type: type-e-liquid-225kw}
        - {type: shuffle-box-600-1200-48u}

Anything the case states still wins, as everywhere else in this software
(ADR-036): a type is a default, not a lock.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

LIBRARY = Path(__file__).resolve().parent.parent / "racks"

#: The types THIS REPOSITORY ships. See `equipment.SHIPPED`: `available()`
#: lists whatever is in `racks/`, which is these plus the engineer's own, and
#: only these are this project's to guarantee (ADR-077).
SHIPPED = (
    "generic-600-1200-45u",
    "generic-800-1200-46u",
    "generic-800-1200-48u",
    "liquid-network-800",
    "meta-600-1200",
    "meta-800-1300",
    "odf-special-600-300",
    "shuffle-box-600-1200-48u",
    "sum3-high-density",
    "sum3-low-density-compute",
    "sum3-low-density-network",
    "type-e-liquid-225kw",
)


class UnknownRackType(LookupError):
    pass


@dataclass(frozen=True)
class RackType:
    """One cabinet, as its source document states it."""

    id: str
    name: str
    size: tuple[float | None, float | None, float | None]
    """Width, depth, height in metres. A None is a dimension the source marks
    for confirmation, and it is left None rather than guessed."""
    cooling: str = "air"
    load_kw: float | None = None
    u_height: int | None = None
    weight_kg: float | None = None
    liquid_fraction: float | None = None
    """Where the heat leaves in coolant rather than air, the share that does.
    A liquid-cooled cabinet puts a fraction of its duty into the room, and a
    CFD that takes the whole duty is modelling a room that does not exist."""
    source: str = ""
    note: str = ""

    @property
    def complete(self) -> bool:
        return all(v is not None for v in self.size)

    @property
    def missing(self) -> list[str]:
        return [n for n, v in zip(("width", "depth", "height"), self.size)
                if v is None]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "size": list(self.size),
            "cooling": self.cooling,
            "load_kw": self.load_kw,
            "u_height": self.u_height,
            "weight_kg": self.weight_kg,
            "liquid_fraction": self.liquid_fraction,
            "complete": self.complete,
            "missing": self.missing,
            "source": self.source.strip(),
            "note": self.note.strip(),
        }


def available() -> list[str]:
    """Every type the catalogue holds, alphabetically."""
    return sorted(p.stem for p in LIBRARY.glob("*.yaml"))


def load(type_id: str) -> RackType:
    path = LIBRARY / f"{type_id}.yaml"
    if not path.is_file():
        raise UnknownRackType(
            f"no rack type {type_id!r} in {LIBRARY}. "
            f"Have: {', '.join(available()) or '(none)'}"
        )
    return parse(yaml.safe_load(path.read_text()))


def parse(raw: dict) -> RackType:
    size = raw.get("size") or [None, None, None]
    if len(size) != 3:
        raise ValueError(f"{raw.get('id', '?')}: size is width, depth, height")
    return RackType(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        size=tuple(None if v is None else float(v) for v in size),  # type: ignore[arg-type]
        cooling=str(raw.get("cooling") or "air"),
        load_kw=None if raw.get("load_kw") is None else float(raw["load_kw"]),
        u_height=None if raw.get("u_height") is None else int(raw["u_height"]),
        weight_kg=None if raw.get("weight_kg") is None else float(raw["weight_kg"]),
        liquid_fraction=(None if raw.get("liquid_fraction") is None
                         else float(raw["liquid_fraction"])),
        source=str(raw.get("source") or ""),
        note=str(raw.get("note") or ""),
    )


def resolve(type_id: str) -> RackType:
    """A type, refused unless every dimension it needs is stated.

    A cabinet whose depth its own document marks "further confirmation" is not
    placeable, and filling the gap with a plausible number is how a row that
    does not fit gets built with nothing saying so (ADR-071).
    """
    rack = load(type_id)
    if not rack.complete:
        missing = ", ".join(rack.missing)
        one = len(rack.missing) == 1
        raise ValueError(
            f"rack type {rack.id!r} does not state its {missing}, because its "
            f"source does not. Ask the supplier for "
            f"{'that number' if one else 'those numbers'}, or state "
            f"{'it' if one else 'them'} in the case"
        )
    return rack
