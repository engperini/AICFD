"""The component library: what the air has to get through, as a free area.

A data hall's air passes through four perforated surfaces on its way round the
loop, and each of them costs pressure: the grilles in the false ceiling, the
woven mesh that closes the plenum where it opens into a mechanical gallery,
the perforated plates of a raised floor, and -- as leakage rather than by
design -- the panels that contain an aisle. None of them is a machine. Each is
a percentage of open area, and from that percentage the pressure it costs
follows.

They are held here rather than in the case file because they are standards, not
choices: a house specification fixes them once, every hall built to it uses the
same numbers, and a case that quietly used others would produce a fan duty
nobody could reproduce. The case names a component; the component says what it
is.

Nothing in this module models physics. `model.grille_loss_coefficient` turns a
free area into a loss coefficient, and the solver turns that into a pressure
jump; this reads what the specification says.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from aicfd.yamledit import dig, find, merge, render, set_scalar

LIBRARY = Path(__file__).resolve().parent.parent / "components"

#: The components THIS REPOSITORY ships. See `equipment.SHIPPED`: a house
#: standard committed here is this project's to guarantee, and a component an
#: engineer adds to the folder is theirs (ADR-077).
SHIPPED = (
    "ceiling-return-600",
    "ceiling-return-600-eggcrate",
    "ceiling-return-600-open",
    "ceiling-return-600-perforated",
    "containment-panel",
    "floor-tile-600",
    "gallery-mesh-13",
    "pdu-distribution-loss",
    "supply-grille-2000",
    "supply-grille-2000-open",
)

#: Where a component goes, and what it is called on the page. A role is the
#: surface, not the product: a hall has one kind of ceiling return grille
#: whoever supplies it.
ROLES = {
    "gallery_mesh": "Plenum to mechanical gallery",
    "ceiling_return": "Ceiling return grilles",
    "supply_grille": "Plenum supply grilles",
    "floor_tile": "Raised floor plates",
    "containment": "Aisle containment",
    "distribution_loss": "Power distribution",
}


#: How a component is drawn on the page. The pattern is generated from the
#: component's own numbers, so the open fraction of the swatch IS its free
#: area -- a photograph would look more like the product and say less about
#: the one quantity that decides anything (ADR-049).
PATTERNS = ("woven", "eggcrate", "perforated", "slotted", "joint", "none")


class UnknownComponent(LookupError):
    pass


@dataclass(frozen=True)
class Component:
    """One perforated surface, as a specification states it."""

    id: str
    name: str
    role: str
    free_area: float | None = None
    """Open area over gross area. The one number that decides the pressure.
    None for a component that is not a surface at all."""
    kind: str = "surface"
    """`surface` -- something the air passes through, described by a free area.
    `load` -- heat the room carries that is not in a rack, described by a share
    of the IT load (ADR-049)."""
    share: float | None = None
    """For a `load`: the fraction of the IT load it dissipates."""
    applied: bool = True
    """False where the library holds the number but the solver does not use it
    yet. Said on the page rather than left for a reader to discover from a
    result that does not move."""
    pattern: str = "none"
    """Which aperture the page draws for it."""
    size: tuple[float, float] | None = None
    """Face of one piece, in metres, where the surface is made of pieces."""
    aperture: str | None = None
    """How the openings are formed, in the specification's own words."""
    loss_coefficient: float | None = None
    """K from a datasheet's own dp table, where one is given. It wins over the
    free area, because a measurement beats a correlation."""
    fixed: bool = False
    """Architecture rather than a choice: the same in every hall, and not
    offered for editing (ADR-048)."""
    adjustable: tuple[float, float] | None = None
    """Where a damper lets the free area be set on site, its range."""
    note: str = ""

    @property
    def k(self) -> float | None:
        """Loss coefficient on the face velocity over the gross area.

        None for a component that is not a surface: a share of the IT load has
        no face for air to cross.
        """
        from aicfd.model import grille_loss_coefficient

        if self.kind != "surface" or self.free_area is None:
            return None
        if self.loss_coefficient is not None:
            return float(self.loss_coefficient)
        return grille_loss_coefficient(self.free_area)

    @property
    def role_label(self) -> str:
        return ROLES.get(self.role, self.role)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "role": self.role,
            "role_label": self.role_label,
            "free_area": self.free_area,
            "kind": self.kind,
            "share": self.share,
            "applied": self.applied,
            "pattern": self.pattern,
            "size": list(self.size) if self.size else None,
            "aperture": self.aperture,
            "loss_coefficient": self.loss_coefficient,
            "k": round(self.k, 4) if self.k is not None else None,
            "fixed": self.fixed,
            "adjustable": list(self.adjustable) if self.adjustable else None,
            "note": self.note,
        }


# --- the library --------------------------------------------------------------


def path_for(component: str) -> Path:
    return LIBRARY / f"{component}.yaml"


def available() -> list[str]:
    if not LIBRARY.is_dir():
        return []
    return sorted(p.stem for p in LIBRARY.glob("*.yaml"))


def for_role(role: str) -> list[str]:
    """Every component that can fill this role, alphabetically."""
    return sorted(c for c in available() if load(c).role == role)


def load(component: str) -> Component:
    path = path_for(component)
    if not path.is_file():
        raise UnknownComponent(
            f"no component {component!r} in {LIBRARY}; have: "
            + (", ".join(available()) or "none")
        )
    return parse(yaml.safe_load(path.read_text()) or {}, path)


def parse(raw: dict, source: Path | None = None) -> Component:
    where = f" in {source}" if source else ""
    kind = str(raw.get("kind") or "surface")
    if kind not in ("surface", "load"):
        raise ValueError(f"component{where} has an unknown kind {kind!r}")
    required = ("id", "role", "free_area") if kind == "surface" else ("id", "role", "share")
    missing = [k for k in required if raw.get(k) is None]
    if missing:
        raise ValueError(f"component{where} is missing: {', '.join(missing)}")
    free = float(raw["free_area"]) if raw.get("free_area") is not None else None
    if free is not None and not 0 < free <= 1:
        raise ValueError(
            f"free_area is a ratio of open to gross area, so 0 < x <= 1; got {free}"
        )
    share = float(raw["share"]) if raw.get("share") is not None else None
    if share is not None and not 0 <= share < 1:
        raise ValueError(
            f"share is a fraction of the IT load, so 0 <= x < 1; got {share}"
        )
    pattern = str(raw.get("pattern") or "none")
    if pattern not in PATTERNS:
        raise ValueError(f"component{where} draws an unknown pattern {pattern!r}")
    size = raw.get("size")
    span = raw.get("adjustable")
    return Component(
        id=str(raw["id"]),
        name=str(raw.get("name") or raw["id"]),
        role=str(raw["role"]),
        free_area=free,
        kind=kind,
        share=share,
        applied=bool(raw.get("applied", True)),
        pattern=pattern,
        size=tuple(float(v) for v in size) if size else None,
        aperture=raw.get("aperture"),
        loss_coefficient=(float(raw["loss_coefficient"])
                          if raw.get("loss_coefficient") is not None else None),
        fixed=bool(raw.get("fixed", False)),
        adjustable=tuple(float(v) for v in span) if span else None,
        note=str(raw.get("note") or "").strip(),
    )


#: What the page may write back. `id`, `role` and `fixed` are absent on
#: purpose: a role is what the surface IS, an id is what cases point at, and
#: a component marked fixed is the building rather than a choice.
EDITABLE = (
    "name",
    "free_area",
    "share",
    "loss_coefficient",
    "aperture",
    "note",
)


def save(component: str, changes: dict) -> Component:
    """Write the page's draft back over this component, comments and all."""
    path = path_for(component)
    if not path.is_file():
        raise UnknownComponent(f"no component {component!r} to save over")
    raw = yaml.safe_load(path.read_text()) or {}
    if raw.get("fixed"):
        raise ValueError(
            f"{component} is fixed architecture and is not edited here "
            f"(ADR-048)"
        )
    merged = merge(raw, {k: v for k, v in changes.items() if k in EDITABLE})
    parse(merged, path)  # refuse a draft that does not describe a component
    lines = path.read_text().splitlines()
    for key in EDITABLE:
        value = dig(merged, [key])
        if value is not None and not set_scalar(lines, [key], value):
            lines.append(f"{key}: {render(value)}")
    return _write_checked(path, "\n".join(lines) + "\n")


def _write_checked(path: Path, text: str) -> Component:
    """Write the file only once it is known to parse back to a component.

    The editor rewrites lines in place to keep the comments, and a rule that
    mishandles one shape of value damages the FILE rather than the value: a
    folded note whose continuation lines were left behind swallowed the keys
    under it, and the library stopped loading. That happened. A save that
    cannot be read back is not written.
    """
    try:
        component = parse(yaml.safe_load(text) or {}, path)
    except Exception as error:  # noqa: BLE001 - any parse failure is the same answer
        raise ValueError(
            f"the edit would leave {path.name} unreadable, so it was not "
            f"written: {error}"
        ) from error
    path.write_text(text)
    return component
