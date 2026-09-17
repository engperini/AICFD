"""Derive the full geometry of a fan-wall POD from its spec.

This is the single place that turns the numbers an engineer types into boxes,
planes and openings in room coordinates. Both the case generator and the web
page consume it, so the drawing on screen and the mesh that gets solved can
never disagree -- a drawing that is merely *about* the model is worth very
little when the point of looking at it is to catch a mistake before a solve.

Coordinates, in metres:

    x   0 at the back of the mechanical gallery, running through the gallery,
        across the dividing wall, and down the cold aisle of the data hall
    y   0 at the cold-aisle wall, across the aisle, the rack row, and the hot
        aisle to the far wall
    z   0 at the floor, up through the false ceiling to the slab

The air loop the geometry has to close:

    fan wall discharge -> cold aisle -> through the racks -> contained hot
    aisle -> ceiling grilles -> return plenum -> opening above the dividing
    wall -> mechanical gallery -> fan wall intake
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

RHO_AIR = 1.19
CP_AIR = 1005.0


@dataclass(frozen=True)
class Box:
    """An axis-aligned volume, in metres."""

    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(h - l for l, h in zip(self.lo, self.hi))  # type: ignore[return-value]

    @property
    def volume(self) -> float:
        dx, dy, dz = self.size
        return dx * dy * dz


@dataclass(frozen=True)
class Panel:
    """A flat internal wall or opening: a box that is flat on ``axis``."""

    name: str
    kind: str
    """wall | opening | fan"""
    axis: int
    """0=x, 1=y, 2=z -- the axis the panel is normal to."""
    position: float
    extent: tuple[tuple[float, float], tuple[float, float]]
    """The two in-plane ranges, in axis order, skipping ``axis``."""

    @property
    def area(self) -> float:
        (a0, a1), (b0, b1) = self.extent
        return (a1 - a0) * (b1 - b0)

    @property
    def in_plane_axes(self) -> tuple[int, int]:
        return tuple(a for a in range(3) if a != self.axis)  # type: ignore[return-value]

    def box(self, thickness: float = 0.0) -> Box:
        """The panel as a (possibly zero-thickness) box, for drawing."""
        lo = [0.0, 0.0, 0.0]
        hi = [0.0, 0.0, 0.0]
        lo[self.axis] = self.position - thickness / 2
        hi[self.axis] = self.position + thickness / 2
        for axis, (a0, a1) in zip(self.in_plane_axes, self.extent):
            lo[axis], hi[axis] = a0, a1
        return Box(tuple(lo), tuple(hi))  # type: ignore[arg-type]


@dataclass
class Rack:
    id: str
    box: Box
    load_kw: float
    airflow_axis: int = 1
    """The axis air passes along, front to back."""
    airflow_sign: int = 1

    @property
    def load_w(self) -> float:
        return self.load_kw * 1000.0

    @property
    def face_area(self) -> float:
        """Area presented to the airflow."""
        size = self.box.size
        return math.prod(size[a] for a in range(3) if a != self.airflow_axis)


@dataclass
class Model:
    """The complete derived geometry, ready to mesh or to draw."""

    name: str
    domain: Box
    gallery: Box
    hall: Box
    ceiling_z: float
    cold_aisle: tuple[float, float]
    rack_band: tuple[float, float]
    hot_aisle: tuple[float, float]
    racks: list[Rack]
    panels: list[Panel]
    airflow_m3h: float
    supply_temp_c: float
    cell_size: float
    warnings: list[str] = field(default_factory=list)

    # --- derived quantities ---------------------------------------------------

    @property
    def airflow_m3s(self) -> float:
        return self.airflow_m3h / 3600.0

    @property
    def total_load_w(self) -> float:
        return sum(rack.load_w for rack in self.racks)

    @property
    def design_delta_t_k(self) -> float:
        if self.airflow_m3s <= 0:
            return float("inf")
        return self.total_load_w / (self.airflow_m3s * RHO_AIR * CP_AIR)

    @property
    def divisions(self) -> tuple[int, int, int]:
        size = self.domain.size
        return tuple(max(1, round(size[a] / self.cell_size)) for a in range(3))  # type: ignore[return-value]

    @property
    def n_cells(self) -> int:
        nx, ny, nz = self.divisions
        return nx * ny * nz

    def panel(self, name: str) -> Panel:
        for panel in self.panels:
            if panel.name == name:
                return panel
        raise KeyError(name)

    def face_velocity(self, panel_name: str) -> float:
        panel = self.panel(panel_name)
        return self.airflow_m3s / panel.area if panel.area else float("inf")

    @property
    def chimney_area(self) -> float:
        """Cross-section of the contained hot aisle, normal to the rise."""
        row = self.rack_span()
        return (row[1] - row[0]) * (self.hot_aisle[1] - self.hot_aisle[0])

    def rack_span(self) -> tuple[float, float]:
        return (
            min(rack.box.lo[0] for rack in self.racks),
            max(rack.box.hi[0] for rack in self.racks),
        )


def build_model(spec: dict) -> Model:
    """Derive the geometry from a spec mapping (already validated upstream)."""
    name = spec.get("name", "case")
    cell = float(spec.get("mesh", {}).get("cell_size", 0.10))

    gallery_depth = float(spec["gallery"]["depth"])
    hall_length, hall_width, height = (float(v) for v in spec["hall"]["size"])
    ceiling = float(spec["hall"]["ceiling"])

    cold = float(spec["aisles"]["cold"])
    hot = float(spec["aisles"]["hot"])
    rack_dx, rack_dy, rack_dz = (float(v) for v in spec["racks"]["size"])
    count = int(spec["racks"]["count"])
    load_kw = float(spec["racks"]["load_kw"])
    start_x = gallery_depth + float(spec["racks"]["offset_x"])

    total_x = gallery_depth + hall_length
    domain = Box((0.0, 0.0, 0.0), (total_x, hall_width, height))
    gallery = Box((0.0, 0.0, 0.0), (gallery_depth, hall_width, height))
    hall = Box((gallery_depth, 0.0, 0.0), (total_x, hall_width, height))

    cold_aisle = (0.0, cold)
    rack_band = (cold, cold + rack_dy)
    hot_aisle = (cold + rack_dy, hall_width)

    racks = [
        Rack(
            id=f"R{i + 1}",
            box=Box(
                (start_x + i * rack_dx, rack_band[0], 0.0),
                (start_x + (i + 1) * rack_dx, rack_band[1], rack_dz),
            ),
            load_kw=load_kw,
            airflow_axis=1,
            airflow_sign=1,
        )
        for i in range(count)
    ]
    row = (start_x, start_x + count * rack_dx)

    fan = spec["fanwall"]
    fan_height = float(fan["height"])
    fan_width = (0.0, float(fan.get("width", cold)))

    panels: list[Panel] = []

    # The wall between gallery and hall, and the two things that pierce it.
    panels.append(
        Panel(
            "fan",
            "fan",
            axis=0,
            position=gallery_depth,
            extent=(fan_width, (0.0, fan_height)),
        )
    )
    panels.append(
        Panel(
            "plenum_opening",
            "opening",
            axis=0,
            position=gallery_depth,
            extent=((0.0, hall_width), (ceiling, height)),
        )
    )

    # The false ceiling, and the grilles that pierce it.
    panels.append(
        Panel(
            "ceiling",
            "wall",
            axis=2,
            position=ceiling,
            extent=((gallery_depth, total_x), (0.0, hall_width)),
        )
    )
    grille = float(spec["grilles"]["size"])
    grille_count = int(spec["grilles"].get("count", count))
    for i in range(grille_count):
        panels.append(
            Panel(
                f"grille{i + 1}",
                "opening",
                axis=2,
                position=ceiling,
                extent=(
                    (row[0] + i * grille, row[0] + (i + 1) * grille),
                    (hot_aisle[0], hot_aisle[0] + grille),
                ),
            )
        )

    # Hot aisle containment: a chimney from the racks up to the ceiling.
    if spec.get("containment", {}).get("enabled", True):
        panels.append(
            Panel(
                "containment_roofwall",
                "wall",
                axis=1,
                position=rack_band[1],
                extent=((row[0], row[1]), (rack_dz, ceiling)),
            )
        )
        for edge in row:
            panels.append(
                Panel(
                    f"containment_door_{edge:g}".replace(".", "_"),
                    "wall",
                    axis=0,
                    position=edge,
                    extent=((hot_aisle[0], hot_aisle[1]), (0.0, ceiling)),
                )
            )

    model = Model(
        name=name,
        domain=domain,
        gallery=gallery,
        hall=hall,
        ceiling_z=ceiling,
        cold_aisle=cold_aisle,
        rack_band=rack_band,
        hot_aisle=hot_aisle,
        racks=racks,
        panels=panels,
        airflow_m3h=float(fan["airflow_m3h"]),
        supply_temp_c=float(fan.get("supply_temp_c", 20.0)),
        cell_size=cell,
    )
    # Snap to the mesh *before* anyone reads the model. The drawing, the
    # summary table and the solved case then describe the same geometry -- a
    # table that quotes the nominal area while the mesh builds another one is
    # exactly the kind of quiet disagreement this module exists to prevent.
    model.warnings = check_mesh_alignment(model)
    return snap_to_mesh(model)


def snap_to_mesh(model: Model) -> Model:
    """Move every plane onto the nearest cell face."""
    cell = model.cell_size

    def snap(value: float) -> float:
        return round(value / cell) * cell

    def snap_box(box: Box) -> Box:
        return Box(
            tuple(snap(v) for v in box.lo),  # type: ignore[arg-type]
            tuple(snap(v) for v in box.hi),  # type: ignore[arg-type]
        )

    model.racks = [
        Rack(r.id, snap_box(r.box), r.load_kw, r.airflow_axis, r.airflow_sign)
        for r in model.racks
    ]
    model.panels = [
        Panel(
            p.name,
            p.kind,
            p.axis,
            snap(p.position),
            tuple((snap(a0), snap(a1)) for a0, a1 in p.extent),  # type: ignore[arg-type]
        )
        for p in model.panels
    ]
    model.cold_aisle = (snap(model.cold_aisle[0]), snap(model.cold_aisle[1]))
    model.rack_band = (snap(model.rack_band[0]), snap(model.rack_band[1]))
    model.hot_aisle = (snap(model.hot_aisle[0]), snap(model.hot_aisle[1]))
    model.ceiling_z = snap(model.ceiling_z)
    return model


def check_mesh_alignment(model: Model) -> list[str]:
    """Every plane in the model has to land on a cell face.

    ``boxToFace`` and ``boxToCell`` select by cell centre. A plane that falls
    mid-cell gives a ragged selection that depends on floating-point rounding,
    and an internal wall built from it is silently wrong -- it does not fail,
    it just leaks. So this is checked up front and reported in the units the
    user typed.
    """
    warnings: list[str] = []
    cell = model.cell_size

    def misaligned(value: float) -> bool:
        steps = value / cell
        return abs(steps - round(steps)) > 1e-9

    checked: list[tuple[str, float]] = [
        ("altura do forro", model.ceiling_z),
        ("corredor frio", model.cold_aisle[1]),
        ("profundidade do rack", model.rack_band[1]),
    ]
    for axis, label in enumerate(("comprimento", "largura", "altura")):
        checked.append((f"{label} do domínio", model.domain.size[axis]))
    for rack in model.racks:
        for axis, label in enumerate(("x", "y", "z")):
            checked.append((f"rack {rack.id} em {label}", rack.box.hi[axis]))
    for panel in model.panels:
        checked.append((f"posição d{article(panel)}", panel.position))
        for (a0, a1) in panel.extent:
            checked.append((f"borda d{article(panel)}", a1))

    reported: set[str] = set()
    for label, value in checked:
        if misaligned(value) and label not in reported:
            reported.add(label)
            snapped = round(value / cell) * cell
            warnings.append(
                f"{label}: {num(value, 3)} m não cai na malha de "
                f"{num(cell)} m; a malha vai usar {num(snapped)} m "
                f"({(snapped - value) * 1000:+.0f} mm)."
            )
    return warnings


#: What to call each panel where a person reads it. Internal names stay as
#: they are -- they name mesh patches and have to match the case files.
PANEL_PT = {
    "fan": "o fan wall",
    "plenum_opening": "a abertura para a galeria",
    "ceiling": "o forro",
    "containment_roofwall": "a parede do enclausuramento",
}


def article(panel: Panel) -> str:
    """The panel's name in Portuguese, with its article, for a warning."""
    if panel.name in PANEL_PT:
        return PANEL_PT[panel.name]
    if panel.name.startswith("grille"):
        return f"a grelha {panel.name.removeprefix('grille')}"
    if panel.name.startswith("containment_door"):
        return "a porta do enclausuramento"
    return f"o painel {panel.name}"


def summary_rows(model: Model) -> list[tuple[str, str, str]]:
    """The derived numbers, as rows for the page and the CLI.

    User-facing strings are in Portuguese, like the rest of the interface; the
    code, comments and docs around them stay in English. These are the numbers
    an engineer checks before committing to a solve -- face areas and the
    velocities they imply -- so they are spelled out rather than left to be
    recomputed from the geometry.
    """
    row = model.rack_span()
    fan = model.panel("fan")
    grilles = [p for p in model.panels if p.name.startswith("grille")]
    grille_area = sum(p.area for p in grilles)
    plenum = model.panel("plenum_opening")
    dx, dy, dz = model.domain.size

    def face(panel: Panel) -> str:
        (a0, a1), (b0, b1) = panel.extent
        return f"{num(a1 - a0)} x {num(b1 - b0)} m"

    def through(area: float) -> str:
        if not area:
            return "-"
        return f"{num(area)} m² · {num(model.airflow_m3s / area)} m/s"

    return [
        ("Domínio", f"{num(dx)} x {num(dy)} x {num(dz)} m", f"{num(model.n_cells, 0)} células"),
        (
            "Galeria mecânica",
            f"{num(model.gallery.size[0])} m de profundidade",
            f"{num(model.gallery.volume, 1)} m³",
        ),
        (
            "Data hall",
            f"{num(model.hall.size[0])} m de comprimento",
            f"forro a {num(model.ceiling_z)} m",
        ),
        (
            "Carga de TI",
            f"{len(model.racks)} x {model.racks[0].load_kw:g} kW"
            if model.racks
            else "-",
            f"{num(model.total_load_w / 1000, 1)} kW no total",
        ),
        (
            "Vazão",
            f"{num(model.airflow_m3h, 0)} m³/h a {num(model.supply_temp_c, 1)} °C",
            f"ΔT de projeto {num(model.design_delta_t_k, 1)} K",
        ),
        ("Face do fan wall", face(fan), through(fan.area)),
        (
            f"Grelhas do forro ({len(grilles)})",
            f"{num(grilles[0].extent[0][1] - grilles[0].extent[0][0])} m quadradas"
            if grilles
            else "-",
            through(grille_area),
        ),
        (
            "Chaminé do corredor quente",
            f"{num(row[1] - row[0])} x "
            f"{num(model.hot_aisle[1] - model.hot_aisle[0])} x {num(model.ceiling_z)} m",
            through(model.chimney_area),
        ),
        ("Abertura para a galeria", face(plenum), through(plenum.area)),
    ]


def num(value: float, decimals: int = 2) -> str:
    """Format a number the way the interface writes them: 1.234,56.

    Python's own separators are the other way round, so the two are swapped in
    one place rather than in each of the thirty strings below.
    """
    return (
        f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    )


# --- serialisation ------------------------------------------------------------


def to_dict(model: Model, spec: dict) -> dict:
    """The model as JSON for the web page.

    The page draws from this and nothing else, so what is on screen is the
    geometry that will be meshed -- including the snapping already applied.
    """
    return {
        "name": model.name,
        "cell_size": model.cell_size,
        "divisions": list(model.divisions),
        "cells": model.n_cells,
        "domain": {"lo": list(model.domain.lo), "hi": list(model.domain.hi)},
        "gallery": {"lo": list(model.gallery.lo), "hi": list(model.gallery.hi)},
        "hall": {"lo": list(model.hall.lo), "hi": list(model.hall.hi)},
        "ceiling_z": model.ceiling_z,
        "aisles": {
            "cold": list(model.cold_aisle),
            "racks": list(model.rack_band),
            "hot": list(model.hot_aisle),
        },
        "racks": [
            {
                "id": r.id,
                "lo": list(r.box.lo),
                "hi": list(r.box.hi),
                "load_kw": r.load_kw,
                "airflow_axis": r.airflow_axis,
            }
            for r in model.racks
        ],
        "panels": [
            {
                "name": p.name,
                "kind": p.kind,
                "axis": p.axis,
                "position": p.position,
                "extent": [list(e) for e in p.extent],
                "area": round(p.area, 4),
            }
            for p in model.panels
        ],
        "operating": {
            "airflow_m3h": model.airflow_m3h,
            "supply_temp_c": model.supply_temp_c,
            "load_kw": model.total_load_w / 1000.0,
            "design_delta_t_k": round(model.design_delta_t_k, 2),
            "chimney_velocity_ms": round(
                model.airflow_m3s / model.chimney_area, 3
            ) if model.chimney_area else None,
        },
        "summary": [list(row) for row in summary_rows(model)],
        "warnings": model.warnings,
        "spec": spec,
    }
