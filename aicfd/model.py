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

#: Temperature rise used to size a rack's own airflow when none is given.
#: 11 K is typical of enterprise IT at the rack outlet.
RACK_DELTA_T = 11.0

#: Pressure drop across a populated rack at its rated airflow, in Pa.
#: Manufacturer data ranges roughly 15-40 Pa; 25 is a reasonable middle.
RACK_PRESSURE_DROP = 25.0


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


#: Darcy-Forchheimer resistance across a rack's blocked axes. Large enough to
#: force air front-to-back as a real rack does, small enough not to wreck the
#: pressure solve.
BLOCKED_D = 1.0e5
BLOCKED_F = 500.0

#: Viscous (Darcy) term on the flow axis. Small and fixed: at rack face
#: velocities the drop is inertial, and a non-zero d keeps the source
#: well-behaved as velocity approaches zero.
FLOW_AXIS_D = 20.0


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

    @property
    def depth(self) -> float:
        """How far the air travels inside the rack -- the porous path length."""
        return self.box.size[self.airflow_axis]

    @property
    def rated_airflow_m3s(self) -> float:
        """What the rack's own fans would move, from its load and RACK_DELTA_T.

        This sizes the flow *resistance*, not the flow. A rack modelled as a
        porous block is a resistance, not a fan (ADR-011): what actually passes
        through it is an outcome of the room's pressure field.
        """
        return self.load_w / (RACK_DELTA_T * RHO_AIR * CP_AIR)

    @property
    def face_velocity_ms(self) -> float:
        return self.rated_airflow_m3s / self.face_area if self.face_area else 0.0

    def darcy_forchheimer(self, pressure_drop_pa: float = RACK_PRESSURE_DROP):
        """Resistance coefficients (d, f) along the rack's flow axis.

        The drop through a populated rack at its rated airflow is dominated by
        the inertial term, so the whole of it is assigned there:

            dp = 0.5 * rho * f * u^2 * L   ->   f = 2 * dp / (rho * u^2 * L)
        """
        velocity = self.face_velocity_ms
        if velocity <= 0 or self.depth <= 0:
            return FLOW_AXIS_D, 0.0
        f = 2.0 * pressure_drop_pa / (RHO_AIR * velocity**2 * self.depth)
        return FLOW_AXIS_D, f


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
    fan_static_pa: float | None = None
    """External static pressure from the unit's datasheet, if given."""
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

    @property
    def rack_pressure_drop_pa(self) -> float:
        """What the rack row costs at the airflow actually passing it.

        Computed from the resistance the spec asked for, not read out of the
        solved field. With hot-aisle containment every cubic metre the fan
        moves goes through the racks, so the flow is known exactly and this is
        closed form -- which is what makes it a *check* on the field rather
        than a repeat of it.
        """
        if not self.racks:
            return 0.0
        face = sum(rack.face_area for rack in self.racks)
        if face <= 0:
            return 0.0
        velocity = self.airflow_m3s / face
        _d, f = self.racks[0].darcy_forchheimer()
        return 0.5 * RHO_AIR * f * velocity**2 * self.racks[0].depth

    @property
    def rack_demand_m3s(self) -> float:
        """What the racks would draw at their rated airflow, all together."""
        return sum(rack.rated_airflow_m3s for rack in self.racks)

    def rack_span(self) -> tuple[float, float]:
        return (
            min(rack.box.lo[0] for rack in self.racks),
            max(rack.box.hi[0] for rack in self.racks),
        )


@dataclass(frozen=True)
class SensorGroup:
    """A handful of points whose average stands for one place in the POD.

    A single probe in a recirculating room reports the eddy it happens to sit
    in. Three points spread across the place being asked about report the
    place. Each group is averaged before anything is shown, so what the page
    plots is "the cold aisle", not "cell 143 872".
    """

    name: str
    label: str
    points: tuple[tuple[float, float, float], ...]
    note: str = ""


def sensors(model: Model) -> list[SensorGroup]:
    """Where to measure, in the places an engineer would put a data logger.

    The heights and offsets mirror how a real POD is instrumented: rack
    mid-height in the aisles, inside the plenum above the grilles, and on the
    back of the fan wall where the return air arrives.
    """
    if not model.racks:
        return []

    rack_mid_z = model.racks[0].box.hi[2] / 2
    columns = tuple(
        (rack.box.lo[0] + rack.box.hi[0]) / 2 for rack in model.racks[:3]
    ) or (model.hall.lo[0] + 1.0,)
    # Fewer than three racks still gets three columns, spread along the row.
    if len(columns) < 3:
        lo, hi = model.rack_span()
        columns = tuple(lo + (hi - lo) * f for f in (0.2, 0.5, 0.8))

    mid = lambda band: (band[0] + band[1]) / 2  # noqa: E731
    fan = model.panel("fan")
    fan_top = fan.extent[1][1]

    return [
        SensorGroup(
            "cold_aisle",
            "Corredor frio",
            tuple((x, mid(model.cold_aisle), rack_mid_z) for x in columns),
            "no meio do corredor, à altura dos racks",
        ),
        SensorGroup(
            "hot_aisle",
            "Corredor quente",
            tuple((x, mid(model.hot_aisle), rack_mid_z) for x in columns),
            "dentro do enclausuramento, à altura dos racks",
        ),
        SensorGroup(
            "plenum",
            "Plenum do forro",
            tuple(
                (x, mid(model.hot_aisle), (model.ceiling_z + model.domain.hi[2]) / 2)
                for x in columns
            ),
            "acima das grelhas de retorno",
        ),
        SensorGroup(
            "fan_back",
            "Costas do fan wall",
            tuple(
                (
                    max(model.hall.lo[0] - 0.3, model.cell_size),
                    mid(fan.extent[0]),
                    fan_top * fraction,
                )
                for fraction in (0.25, 0.5, 0.75)
            ),
            "na galeria, onde o ar de retorno chega ao fan wall",
        ),
    ]


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

    # A rack is a closed box that breathes front to back, so every face of the
    # row except those two is a wall. Leaving any of them open lets the cold
    # aisle into the porous zone sideways, and that path is almost free: a few
    # cells of blocked axis against 1,2 m of the flow axis. Measured, in order,
    # as each was closed -- share of the fan's duty entering through the rack
    # fronts: 22% with the row open, 57% with the ends closed, and the rest
    # pouring in through the tops. The row delivered a fifth, then half, of its
    # rated pressure drop, and the containment was not doing what the drawing
    # said it was.
    panels.append(
        Panel(
            "rack_top",
            "wall",
            axis=2,
            position=rack_dz,
            extent=((row[0], row[1]), (rack_band[0], rack_band[1])),
        )
    )
    for edge in row:
        panels.append(
            Panel(
                f"rack_end_{edge:g}".replace(".", "_"),
                "wall",
                axis=0,
                position=edge,
                extent=((rack_band[0], rack_band[1]), (0.0, rack_dz)),
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
        fan_static_pa=(
            float(fan["static_pressure_pa"]) if "static_pressure_pa" in fan else None
        ),
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
        (
            "Resistência dos racks",
            f"{num(model.rack_pressure_drop_pa)} Pa na vazão do fan wall",
            f"{num(RACK_PRESSURE_DROP, 0)} Pa nominais por rack",
        ),
        (
            "Pressão do fan wall",
            f"{num(model.fan_static_pa, 0)} Pa de datasheet"
            if model.fan_static_pa
            else "não informada",
            f"racks consomem {num(model.rack_pressure_drop_pa / model.fan_static_pa * 100, 0)}%"
            if model.fan_static_pa
            else "-",
        ),
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
        "sensors": [
            {
                "name": group.name,
                "label": group.label,
                "note": group.note,
                "points": [list(point) for point in group.points],
            }
            for group in sensors(model)
        ],
        "summary": [list(row) for row in summary_rows(model)],
        "warnings": model.warnings,
        "spec": spec,
    }
