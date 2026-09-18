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
R_AIR = 287.05
KELVIN = 273.15
SEA_LEVEL_PA = 101325.0

#: Airflow a rack needs per kilowatt of IT, the design office's rule of thumb:
#: 158 CFM/kW. At sea level and 20 degC that is an 11 K rise through the rack;
#: at altitude the same volume carries less air, and the rise grows.
CFM_PER_KW = 158.0
M3H_PER_CFM = 1.699011


def air_density(pressure_pa: float, temp_c: float) -> float:
    return pressure_pa / (R_AIR * (temp_c + KELVIN))


def site_pressure(altitude_m: float) -> float:
    """Standard-atmosphere pressure at ``altitude_m`` -- what the site's air
    weighs. A fan wall selected for 1 880 m moves 24% less mass per cubic metre
    than the same unit at the coast, and its datasheet says so twice: actual
    and standard air."""
    return SEA_LEVEL_PA * (1.0 - 2.25577e-5 * altitude_m) ** 5.25588

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
    resistance: float | None = None
    """Loss coefficient K for an opening that resists the air (a grille):
    dp = K * rho * v^2 / 2 with v the face velocity over the gross area."""

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
    cfm_per_kw: float = CFM_PER_KW
    """How much air the rack's own fans draw per kilowatt."""
    rho: float = RHO_AIR
    """Air density where the rack stands, for its resistance coefficients."""

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
        """What the rack's own fans would move: its load times the CFM/kW rule.

        This sizes the flow *resistance*, not the flow. A rack modelled as a
        porous block is a resistance, not a fan (ADR-011): what actually passes
        through it is an outcome of the room's pressure field.
        """
        return self.load_kw * self.cfm_per_kw * M3H_PER_CFM / 3600.0

    @property
    def rated_airflow_m3h(self) -> float:
        return self.rated_airflow_m3s * 3600.0

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
        f = 2.0 * pressure_drop_pa / (self.rho * velocity**2 * self.depth)
        return FLOW_AXIS_D, f


@dataclass
class Row:
    """A row of racks across the hall, all breathing the same way.

    ``front_sign`` is the direction the air travels through the racks along
    y: +1 when the cold aisle is at lower y and the air moves towards higher
    y, -1 when the row faces the other way. A POD's two rows face each other
    across the contained hot aisle, so one is +1 and the other -1.
    """

    id: str
    band: tuple[float, float]
    front_sign: int
    racks: list[Rack]

    @property
    def front_y(self) -> float:
        return self.band[0] if self.front_sign > 0 else self.band[1]

    @property
    def back_y(self) -> float:
        return self.band[1] if self.front_sign > 0 else self.band[0]

    @property
    def span(self) -> tuple[float, float]:
        return (
            min(r.box.lo[0] for r in self.racks),
            max(r.box.hi[0] for r in self.racks),
        )


@dataclass
class Model:
    """The complete derived geometry, ready to mesh or to draw.

    A fan-wall POD is one row, one contained hot aisle against the far wall
    and one fan wall. A data hall is the same parts repeated across y: rows in
    pairs facing a contained hot aisle, cold aisles between the pairs, and a
    fan wall in the dividing wall in front of each cold aisle. So everything
    that a POD has one of, the model holds a list of, and the POD is the list
    of length one.
    """

    name: str
    domain: Box
    gallery: Box
    hall: Box
    ceiling_z: float
    cold_aisles: list[tuple[float, float]]
    hot_aisles: list[tuple[float, float]]
    rows: list[Row]
    racks: list[Rack]
    panels: list[Panel]
    airflow_m3h: float
    """Total supply, all fan walls together."""
    supply_temp_c: float
    cell_size: tuple[float, float, float]
    """Cell edge along x, y, z. A fan-wall POD wants finer cells in z than in
    plan -- the false ceiling and rack tops are horizontal planes that must land
    on cell faces, while a 0,2 m plan cell resolves a 0,6 m rack fine."""
    fan_static_pa: float | None = None
    """External static pressure from the unit's datasheet at the rated flow."""
    fan_curve: tuple[tuple[float, float], ...] | None = None
    """The unit's P-Q curve as (m3/h, Pa) points, flow ascending, if given."""
    grille_free_area: float | None = None
    """Free-area ratio of the return grilles, from their datasheet."""
    unit_capacity_kw: float | None = None
    """Net sensible cooling of one fan wall unit, from its datasheet."""
    unit_power_kw: float | None = None
    """Electrical input of one unit, from its datasheet."""
    altitude_m: float = 0.0
    warnings: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    """Design criteria the HVAC does not meet. Alerts, never blockers: a
    conceptual study wants to see what an undersized plant does."""

    # --- the POD's singular names, for the one-row case -------------------------

    @property
    def cold_aisle(self) -> tuple[float, float]:
        return self.cold_aisles[0]

    @property
    def hot_aisle(self) -> tuple[float, float]:
        return self.hot_aisles[0]

    @property
    def rack_band(self) -> tuple[float, float]:
        return self.rows[0].band

    # --- fan walls --------------------------------------------------------------

    @property
    def fans(self) -> list[Panel]:
        return [p for p in self.panels if p.kind == "fan"]

    @property
    def fan_count(self) -> int:
        return max(1, len(self.fans))

    @property
    def unit_airflow_m3h(self) -> float:
        """What each fan wall moves. The datasheet speaks per unit."""
        return self.airflow_m3h / self.fan_count

    @property
    def fan_face_velocity_ms(self) -> float:
        fans = self.fans
        if not fans or fans[0].area <= 0:
            return float("inf")
        return self.unit_airflow_m3h / 3600.0 / fans[0].area

    # --- derived quantities ---------------------------------------------------

    @property
    def airflow_m3s(self) -> float:
        return self.airflow_m3h / 3600.0

    @property
    def pressure_pa(self) -> float:
        """Operating pressure of the site's air, from its altitude."""
        return site_pressure(self.altitude_m)

    @property
    def rho(self) -> float:
        """Density of the supply air at the site."""
        return air_density(self.pressure_pa, self.supply_temp_c)

    # --- HVAC sizing against the design office's rules --------------------------

    @property
    def rack_demand_m3h(self) -> float:
        return sum(rack.rated_airflow_m3h for rack in self.racks)

    def hvac(self) -> dict:
        """Does the plant cover the load, in kilowatts and in cubic metres?

        Two ratios the design office checks before any CFD: installed sensible
        capacity against IT load, and installed airflow against what the racks
        draw at their CFM/kW. Both are reported; neither stops a run.
        """
        load_kw = self.total_load_w / 1000.0
        capacity_kw = (
            self.unit_capacity_kw * self.fan_count if self.unit_capacity_kw else None
        )
        demand = self.rack_demand_m3h
        return {
            "units": self.fan_count,
            "unit_capacity_kw": self.unit_capacity_kw,
            "unit_airflow_m3h": self.unit_airflow_m3h,
            "unit_power_kw": self.unit_power_kw,
            "load_kw": round(load_kw, 1),
            "capacity_kw": round(capacity_kw, 1) if capacity_kw else None,
            "capacity_ratio": round(capacity_kw / load_kw, 3) if capacity_kw and load_kw else None,
            "airflow_m3h": round(self.airflow_m3h),
            "airflow_needed_m3h": round(demand),
            "airflow_ratio": round(self.airflow_m3h / demand, 3) if demand else None,
            "cfm_per_kw": self.racks[0].cfm_per_kw if self.racks else CFM_PER_KW,
            "units_needed": (
                max(
                    math.ceil(load_kw / self.unit_capacity_kw) if self.unit_capacity_kw else 0,
                    math.ceil(demand / self.unit_airflow_m3h) if self.unit_airflow_m3h else 0,
                )
            ),
        }

    def hvac_alerts(self) -> list[str]:
        h = self.hvac()
        alerts = []
        if h["capacity_ratio"] is not None and h["capacity_ratio"] < 1.0:
            alerts.append(
                f"Cooling capacity is below the load: {h['units']} x "
                f"{num(h['unit_capacity_kw'], 1)} kW = {num(h['capacity_kw'], 0)} kW for "
                f"{num(h['load_kw'], 0)} kW of IT ({num(h['capacity_ratio'] * 100, 0)}%). "
                f"{h['units_needed']} units would be needed."
            )
        if h["airflow_ratio"] is not None and h["airflow_ratio"] < 1.0:
            alerts.append(
                f"Airflow is below what the racks draw: {num(h['airflow_m3h'], 0)} m3/h "
                f"against {num(h['airflow_needed_m3h'], 0)} m3/h at {h['cfm_per_kw']:g} CFM/kW "
                f"({num(h['airflow_ratio'] * 100, 0)}%). {h['units_needed']} units would be needed."
            )
        return alerts

    @property
    def total_load_w(self) -> float:
        return sum(rack.load_w for rack in self.racks)

    @property
    def design_delta_t_k(self) -> float:
        if self.airflow_m3s <= 0:
            return float("inf")
        return self.total_load_w / (self.airflow_m3s * self.rho * CP_AIR)

    @property
    def divisions(self) -> tuple[int, int, int]:
        size = self.domain.size
        return tuple(  # type: ignore[return-value]
            max(1, round(size[a] / self.cell_size[a])) for a in range(3)
        )

    def cell(self, axis: int) -> float:
        return self.cell_size[axis]

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
        """Flow over area. A fan wall passes its own share, everything else the lot."""
        panel = self.panel(panel_name)
        if not panel.area:
            return float("inf")
        flow = self.unit_airflow_m3h / 3600.0 if panel.kind == "fan" else self.airflow_m3s
        return flow / panel.area

    @property
    def chimney_area(self) -> float:
        """Cross-section of the contained hot aisles together, normal to the rise."""
        row = self.rack_span()
        return sum((row[1] - row[0]) * (hi - lo) for lo, hi in self.hot_aisles)

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
        return 0.5 * self.rho * f * velocity**2 * self.racks[0].depth

    @property
    def grille_pressure_drop_pa(self) -> float:
        """What the return grilles cost at the fan's airflow, from their K.

        All of the return passes through them, so the face velocity is the
        fan's flow over the grilles' gross area and the drop is closed form --
        the same status as the rack drop: a check on the field, not a repeat.
        """
        grilles = [p for p in self.panels if p.name.startswith("grille")]
        area = sum(p.area for p in grilles)
        if not grilles or area <= 0 or grilles[0].resistance is None:
            return 0.0
        velocity = self.airflow_m3s / area
        return grilles[0].resistance * 0.5 * self.rho * velocity**2

    def fan_available_pa(self, flow_m3h: float | None = None) -> float | None:
        """Static pressure the unit can produce at ``flow_m3h``, from its curve.

        With only a rated point on the datasheet this is that point; with the
        curve it is interpolated. Beyond the curve's last point it is zero --
        the fan cannot deliver more than free-delivery flow.
        """
        flow = self.unit_airflow_m3h if flow_m3h is None else flow_m3h
        if self.fan_curve:
            points = self.fan_curve
            if flow <= points[0][0]:
                return points[0][1]
            for (q0, p0), (q1, p1) in zip(points, points[1:]):
                if q0 <= flow <= q1:
                    t = (flow - q0) / (q1 - q0) if q1 > q0 else 0.0
                    return p0 + t * (p1 - p0)
            return 0.0
        return self.fan_static_pa

    def fan_operating_point(self, system_rise_pa: float) -> tuple[float, float] | None:
        """Where the fan would run if nothing controlled its speed.

        The POD's resistance scales with flow squared from the solved point;
        the fan's curve comes from the datasheet. Their crossing is the flow an
        uncontrolled unit at full speed would actually deliver. An EC fan wall
        under flow control holds the rated flow instead, which is what the
        solve assumes -- this number says how much margin that control has.
        """
        unit = self.unit_airflow_m3h
        if not self.fan_curve or system_rise_pa <= 0 or unit <= 0:
            return None
        system = lambda q: system_rise_pa * (q / unit) ** 2  # noqa: E731
        lo, hi = 0.0, self.fan_curve[-1][0]
        for _ in range(60):
            mid = (lo + hi) / 2
            available = self.fan_available_pa(mid) or 0.0
            if available > system(mid):
                lo = mid
            else:
                hi = mid
        q = (lo + hi) / 2
        return (round(q, 1), round(system(q), 2))

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
    mid = lambda band: (band[0] + band[1]) / 2  # noqa: E731
    lo, hi = model.rack_span()
    columns = tuple(lo + (hi - lo) * f for f in (0.2, 0.5, 0.8))

    def spread(bands: list[tuple[float, float]], z: float) -> tuple:
        """Three points: across three aisles when there are that many, else
        along the one aisle. A hall's three probes sit in its first, middle
        and last aisle so the mean stands for the hall, not for one corner."""
        if len(bands) >= 3:
            picked = (bands[0], bands[len(bands) // 2], bands[-1])
            return tuple((mid((lo, hi)), mid(band), z) for band in picked)
        return tuple((x, mid(bands[0]), z) for x in columns)

    plenum_z = (model.ceiling_z + model.domain.hi[2]) / 2
    fans = model.fans
    behind_x = max(model.hall.lo[0] - 0.3, model.cell(0))
    if len(fans) >= 3:
        picked = (fans[0], fans[len(fans) // 2], fans[-1])
        fan_points = tuple(
            (behind_x, mid(fan.extent[0]), fan.extent[1][1] * 0.5) for fan in picked
        )
    else:
        fan = fans[0]
        fan_points = tuple(
            (behind_x, mid(fan.extent[0]), fan.extent[1][1] * fraction)
            for fraction in (0.25, 0.5, 0.75)
        )

    return [
        SensorGroup(
            "cold_aisle",
            "Cold aisle",
            spread(model.cold_aisles, rack_mid_z),
            "mid-aisle, at rack height",
        ),
        SensorGroup(
            "hot_aisle",
            "Hot aisle",
            spread(model.hot_aisles, rack_mid_z),
            "inside the containment, at rack height",
        ),
        SensorGroup(
            "plenum",
            "Ceiling plenum",
            spread(model.hot_aisles, plenum_z),
            "above the return grilles",
        ),
        SensorGroup(
            "fan_back",
            "Behind the fan wall",
            fan_points,
            "in the gallery, where the return air reaches the units",
        ),
    ]


@dataclass
class _Layout:
    """What a layout hands the model: where everything is, before snapping."""

    domain: Box
    gallery: Box
    hall: Box
    rows: list[Row]
    cold_aisles: list[tuple[float, float]]
    hot_aisles: list[tuple[float, float]]
    fans: list[Panel]
    grilles: list[Panel]
    walls: list[Panel]


def build_model(spec: dict) -> Model:
    """Derive the geometry from a spec mapping (already validated upstream).

    Two layouts share everything after the boxes are placed:

    * a POD -- ``racks.count`` racks in one row, the hot aisle contained
      against the far wall, one fan wall on the cold aisle;
    * a data hall -- ``pods`` pairs of rows of ``racks.per_row`` racks facing a
      contained hot aisle, cold aisles between the pairs and round the edge,
      and one fan wall in front of every cold aisle.
    """
    name = spec.get("name", "case")
    cell = parse_cell_size(spec.get("mesh", {}).get("cell_size", 0.10))
    ceiling = float(spec["hall"]["ceiling"])
    fan = spec["fanwall"]
    altitude = float(spec.get("site", {}).get("altitude_m", 0.0))
    supply_c = float(fan.get("supply_temp_c", 20.0))
    rack_spec = dict(
        cfm_per_kw=float(spec["racks"].get("airflow_cfm_per_kw", CFM_PER_KW)),
        rho=air_density(site_pressure(altitude), supply_c),
    )

    layout = (
        _hall_layout(spec, cell, rack_spec) if "pods" in spec else _pod_layout(spec, cell, rack_spec)
    )
    racks = [rack for row in layout.rows for rack in row.racks]

    panels: list[Panel] = list(layout.fans)
    # The wall between gallery and hall is pierced by the fan walls and, above
    # the false ceiling, by the return opening along the whole width.
    panels.append(
        Panel(
            "plenum_opening",
            "opening",
            axis=0,
            position=layout.hall.lo[0],
            extent=((0.0, layout.domain.hi[1]), (ceiling, layout.domain.hi[2])),
        )
    )
    panels.append(
        Panel(
            "ceiling",
            "wall",
            axis=2,
            position=ceiling,
            extent=((layout.hall.lo[0], layout.domain.hi[0]), (0.0, layout.domain.hi[1])),
        )
    )
    panels.extend(layout.grilles)
    panels.extend(layout.walls)

    model = Model(
        name=name,
        domain=layout.domain,
        gallery=layout.gallery,
        hall=layout.hall,
        ceiling_z=ceiling,
        cold_aisles=layout.cold_aisles,
        hot_aisles=layout.hot_aisles,
        rows=layout.rows,
        racks=racks,
        panels=panels,
        # The spec's airflow is per unit, as the datasheet gives it.
        airflow_m3h=float(fan["airflow_m3h"]) * max(1, len(layout.fans)),
        supply_temp_c=supply_c,
        cell_size=cell,
        unit_capacity_kw=float(fan["capacity_kw"]) if "capacity_kw" in fan else None,
        unit_power_kw=float(fan["power_kw"]) if "power_kw" in fan else None,
        altitude_m=altitude,
        fan_static_pa=(
            float(fan["static_pressure_pa"]) if "static_pressure_pa" in fan else None
        ),
        fan_curve=parse_fan_curve(fan.get("curve")),
        grille_free_area=_grille_free_area(spec),
    )
    # Snap to the mesh *before* anyone reads the model. The drawing, the
    # summary table and the solved case then describe the same geometry -- a
    # table that quotes the nominal area while the mesh builds another one is
    # exactly the kind of quiet disagreement this module exists to prevent.
    model.warnings = check_mesh_alignment(model)
    # The fan placement snaps the unit's width itself (so units can be packed
    # without overlapping), so the alignment check never sees the nominal one.
    nominal = float(fan["width"]) if "width" in fan else None
    if nominal is not None and layout.fans:
        built = layout.fans[0].extent[0][1] - layout.fans[0].extent[0][0]
        if abs(built - nominal) > 1e-6:
            model.warnings.insert(
                0,
                f"fan wall width: {num(nominal, 3)} m is not on the {num(cell[1])} m "
                f"grid; the mesh will use {num(built)} m "
                f"({(built - nominal) * 1000:+.0f} mm).",
            )
    model.alerts = model.hvac_alerts()
    return snap_to_mesh(model)


def _grille_free_area(spec: dict) -> float | None:
    free_area = spec["grilles"].get("free_area")
    return float(free_area) if free_area is not None else None


def _grille_k(spec: dict) -> float | None:
    if "loss_coefficient" in spec["grilles"]:
        return float(spec["grilles"]["loss_coefficient"])
    free_area = _grille_free_area(spec)
    return grille_loss_coefficient(free_area) if free_area is not None else None


def _make_row(row_id: str, band: tuple[float, float], sign: int, x0: float,
              count: int, size: tuple[float, float, float], load_kw: float,
              rack_ids, rack_spec: dict | None = None) -> Row:
    dx, _dy, dz = size
    racks = [
        Rack(
            id=rack_ids(i),
            box=Box((x0 + i * dx, band[0], 0.0), (x0 + (i + 1) * dx, band[1], dz)),
            load_kw=load_kw,
            airflow_axis=1,
            airflow_sign=sign,
            **(rack_spec or {}),
        )
        for i in range(count)
    ]
    return Row(row_id, band, sign, racks)


def _row_walls(row: Row, span: tuple[float, float], rack_dz: float, suffix: str = "") -> list[Panel]:
    """A rack is a closed box that breathes front to back, so every face of the
    row except those two is a wall. Leaving any of them open lets the cold
    aisle into the porous zone sideways, and that path is almost free: a few
    cells of blocked axis against 1,2 m of the flow axis. Measured, in order,
    as each was closed -- share of the fan's duty entering through the rack
    fronts: 22% with the row open, 57% with the ends closed, and the rest
    pouring in through the tops. The row delivered a fifth, then half, of its
    rated pressure drop, and the containment was not doing what the drawing
    said it was.
    """
    walls = [
        Panel(
            f"rack_top{suffix}",
            "wall",
            axis=2,
            position=rack_dz,
            extent=((span[0], span[1]), (row.band[0], row.band[1])),
        )
    ]
    for edge in span:
        walls.append(
            Panel(
                f"rack_end{suffix}_{edge:g}".replace(".", "_"),
                "wall",
                axis=0,
                position=edge,
                extent=((row.band[0], row.band[1]), (0.0, rack_dz)),
            )
        )
    return walls


def _containment(hot: tuple[float, float], span: tuple[float, float], rack_dz: float,
                 ceiling: float, sides: tuple[float, ...], suffix: str = "") -> list[Panel]:
    """Hot aisle containment: a chimney from the racks up to the ceiling.

    ``sides`` are the y positions that need a wall above the racks -- a POD's
    hot aisle has the room wall on one side, a hall's has racks on both.
    """
    panels = []
    for i, y in enumerate(sides):
        name = "containment_roofwall" if not suffix and len(sides) == 1 else f"containment_wall{suffix}_{'ab'[i]}"
        panels.append(
            Panel(name, "wall", axis=1, position=y, extent=((span[0], span[1]), (rack_dz, ceiling)))
        )
    for edge in span:
        panels.append(
            Panel(
                f"containment_door{suffix}_{edge:g}".replace(".", "_"),
                "wall",
                axis=0,
                position=edge,
                extent=((hot[0], hot[1]), (0.0, ceiling)),
            )
        )
    return panels


def _pod_layout(spec: dict, cell, rack_spec: dict | None = None) -> _Layout:
    gallery_depth = float(spec["gallery"]["depth"])
    hall_length, hall_width, height = (float(v) for v in spec["hall"]["size"])
    ceiling = float(spec["hall"]["ceiling"])
    cold = float(spec["aisles"]["cold"])
    hot = float(spec["aisles"]["hot"])
    size = tuple(float(v) for v in spec["racks"]["size"])
    rack_dz = size[2]
    count = int(spec["racks"]["count"])
    load_kw = float(spec["racks"]["load_kw"])
    start_x = gallery_depth + float(spec["racks"]["offset_x"])

    total_x = gallery_depth + hall_length
    domain = Box((0.0, 0.0, 0.0), (total_x, hall_width, height))
    gallery = Box((0.0, 0.0, 0.0), (gallery_depth, hall_width, height))
    hall = Box((gallery_depth, 0.0, 0.0), (total_x, hall_width, height))

    band = (cold, cold + size[1])
    hot_aisle = (cold + size[1], hall_width)
    row = _make_row("F1", band, +1, start_x, count, size, load_kw, lambda i: f"R{i + 1}", rack_spec)
    span = (start_x, start_x + count * size[0])

    fan = spec["fanwall"]
    fans = [
        Panel(
            "fan",
            "fan",
            axis=0,
            position=gallery_depth,
            extent=((0.0, float(fan.get("width", cold))), (0.0, float(fan["height"]))),
        )
    ]

    grille = float(spec["grilles"]["size"])
    grille_count = int(spec["grilles"].get("count", count))
    grilles = [
        Panel(
            f"grille{i + 1}",
            "opening",
            axis=2,
            position=ceiling,
            extent=(
                (span[0] + i * grille, span[0] + (i + 1) * grille),
                (hot_aisle[0], hot_aisle[0] + grille),
            ),
            resistance=_grille_k(spec),
        )
        for i in range(grille_count)
    ]

    walls = _row_walls(row, span, rack_dz)
    if spec.get("containment", {}).get("enabled", True):
        walls += _containment(hot_aisle, span, rack_dz, ceiling, (band[1],))

    return _Layout(domain, gallery, hall, [row], [(0.0, cold)], [hot_aisle], fans, grilles, walls)


def _hall_layout(spec: dict, cell, rack_spec: dict | None = None) -> _Layout:
    """``pods`` pairs of rows across y, a fan wall in front of every cold aisle.

    Along x: the gallery, the dividing wall, a perimeter aisle, the rows, a
    perimeter aisle. Along y: a perimeter aisle (the first cold aisle), then
    for each POD a row, the contained hot aisle, a row, and a cold aisle to
    the next POD; the last cold aisle is the far perimeter. Fan walls sit in
    the dividing wall centred on their cold aisle as far as the corners and
    their neighbours allow, never overlapping.
    """
    gallery_depth = float(spec["gallery"]["depth"])
    height = float(spec["hall"]["height"])
    ceiling = float(spec["hall"]["ceiling"])
    cold = float(spec["aisles"]["cold"])
    hot = float(spec["aisles"]["hot"])
    perimeter = float(spec["aisles"].get("perimeter", cold))
    size = tuple(float(v) for v in spec["racks"]["size"])
    rack_dx, rack_dy, rack_dz = size
    per_row = int(spec["racks"]["per_row"])
    load_kw = float(spec["racks"]["load_kw"])
    pods = int(spec["pods"])

    row_length = per_row * rack_dx
    hall_length = perimeter + row_length + perimeter
    total_x = gallery_depth + hall_length
    width = 2 * perimeter + pods * (2 * rack_dy + hot) + (pods - 1) * cold
    domain = Box((0.0, 0.0, 0.0), (total_x, width, height))
    gallery = Box((0.0, 0.0, 0.0), (gallery_depth, width, height))
    hall = Box((gallery_depth, 0.0, 0.0), (total_x, width, height))
    x0 = gallery_depth + perimeter
    span = (x0, x0 + row_length)

    rows: list[Row] = []
    hot_aisles: list[tuple[float, float]] = []
    cold_aisles: list[tuple[float, float]] = [(0.0, perimeter)]
    walls: list[Panel] = []
    grilles: list[Panel] = []
    coverage = float(spec["grilles"].get("coverage", 1.0))
    strip = (
        (span[0] + span[1]) / 2 - coverage * row_length / 2,
        (span[0] + span[1]) / 2 + coverage * row_length / 2,
    )

    y = perimeter
    for k in range(pods):
        a = (y, y + rack_dy)
        hot_aisle = (a[1], a[1] + hot)
        b = (hot_aisle[1], hot_aisle[1] + rack_dy)
        n = 2 * k
        row_a = _make_row(f"F{n + 1}", a, +1, x0, per_row, size, load_kw,
                          lambda i, n=n: f"F{n + 1}-{i + 1:02d}", rack_spec)
        row_b = _make_row(f"F{n + 2}", b, -1, x0, per_row, size, load_kw,
                          lambda i, n=n: f"F{n + 2}-{i + 1:02d}", rack_spec)
        rows += [row_a, row_b]
        hot_aisles.append(hot_aisle)
        for row in (row_a, row_b):
            walls += _row_walls(row, span, rack_dz, suffix=f"_{row.id}")
        walls += _containment(hot_aisle, span, rack_dz, ceiling, (a[1], b[0]), suffix=f"_{k + 1}")
        grilles.append(
            Panel(
                f"grille{k + 1}",
                "opening",
                axis=2,
                position=ceiling,
                extent=(strip, hot_aisle),
                resistance=_grille_k(spec),
            )
        )
        y = b[1]
        if k < pods - 1:
            cold_aisles.append((y, y + cold))
            y += cold
    cold_aisles.append((width - perimeter, width))

    fan = spec["fanwall"]
    fan_width = float(fan["width"])
    count = int(fan.get("count", len(cold_aisles)))
    extents = (
        place_fans(cold_aisles, fan_width, width, cell[1])
        if count == len(cold_aisles)
        else distribute_fans(count, fan_width, width, cell[1])
    )
    fans = [
        Panel(f"fan{i + 1}", "fan", axis=0, position=gallery_depth,
              extent=(extent, (0.0, float(fan["height"]))))
        for i, extent in enumerate(extents)
    ]
    return _Layout(domain, gallery, hall, rows, cold_aisles, hot_aisles, fans, grilles, walls)


def place_fans(aisles: list[tuple[float, float]], width: float, wall: float,
               cell: float) -> list[tuple[float, float]]:
    """One fan wall per cold aisle, centred on it where the wall allows.

    A 4,5 m unit in front of a 1,8 m aisle overhangs the racks either side, so
    the units at the two corners cannot be centred -- they sit flush with the
    corner -- and a neighbour that would then overlap is pushed along by one
    cell. Every edge lands on the mesh so the snapping later changes nothing.
    """
    snap = lambda v: round(v / cell) * cell  # noqa: E731
    width = snap(width)
    starts = []
    for lo, hi in aisles:
        start = snap((lo + hi) / 2 - width / 2)
        starts.append(min(max(start, 0.0), snap(wall - width)))
    for i in range(1, len(starts)):  # push right off the previous unit
        starts[i] = max(starts[i], starts[i - 1] + width + cell)
    starts[-1] = min(starts[-1], snap(wall - width))  # back inside the far corner
    for i in range(len(starts) - 2, -1, -1):  # and left off the next one
        starts[i] = min(starts[i], starts[i + 1] - width - cell)
    if starts[0] < -1e-9:
        raise ValueError(
            f"{len(aisles)} fan walls of {width:g} m do not fit a {wall:g} m wall"
        )
    return [(round(s, 6), round(s + width, 6)) for s in starts]


def distribute_fans(count: int, width: float, wall: float,
                    cell: float) -> list[tuple[float, float]]:
    """``count`` units spread evenly along the wall, whatever the aisles.

    This is how a fan-wall hall is actually built once the plant is sized by
    capacity rather than by aisle: units side by side along the gallery wall
    with the gaps the count leaves. Edges land on the mesh; the units may not
    overlap.
    """
    snap = lambda v: round(v / cell) * cell  # noqa: E731
    width = snap(width)
    if count * width > wall + 1e-9:
        raise ValueError(f"{count} fan walls of {width:g} m do not fit a {wall:g} m wall")
    pitch = wall / count
    starts = [snap((i + 0.5) * pitch - width / 2) for i in range(count)]
    for i in range(1, count):
        starts[i] = max(starts[i], starts[i - 1] + width)
    starts[-1] = min(starts[-1], snap(wall - width))
    for i in range(count - 2, -1, -1):
        starts[i] = min(starts[i], starts[i + 1] - width)
    return [(round(s, 6), round(s + width, 6)) for s in starts]


def snap_to_mesh(model: Model) -> Model:
    """Move every plane onto the nearest cell face, axis by axis."""

    def snap(value: float, axis: int) -> float:
        cell = model.cell(axis)
        return round(value / cell) * cell

    def snap_box(box: Box) -> Box:
        return Box(
            tuple(snap(v, a) for a, v in enumerate(box.lo)),  # type: ignore[arg-type]
            tuple(snap(v, a) for a, v in enumerate(box.hi)),  # type: ignore[arg-type]
        )

    def snap_band(band: tuple[float, float]) -> tuple[float, float]:
        return (snap(band[0], 1), snap(band[1], 1))

    def snap_rack(r: Rack) -> Rack:
        return Rack(
            r.id, snap_box(r.box), r.load_kw, r.airflow_axis, r.airflow_sign,
            r.cfm_per_kw, r.rho,
        )

    model.rows = [
        Row(row.id, snap_band(row.band), row.front_sign, [snap_rack(r) for r in row.racks])
        for row in model.rows
    ]
    model.racks = [rack for row in model.rows for rack in row.racks]
    model.panels = [
        Panel(
            p.name,
            p.kind,
            p.axis,
            snap(p.position, p.axis),
            tuple(  # type: ignore[arg-type]
                (snap(a0, axis), snap(a1, axis))
                for axis, (a0, a1) in zip(p.in_plane_axes, p.extent)
            ),
            p.resistance,
        )
        for p in model.panels
    ]
    model.cold_aisles = [snap_band(band) for band in model.cold_aisles]
    model.hot_aisles = [snap_band(band) for band in model.hot_aisles]
    model.ceiling_z = snap(model.ceiling_z, 2)
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

    def misaligned(value: float, axis: int) -> bool:
        steps = value / model.cell(axis)
        return abs(steps - round(steps)) > 1e-9

    checked: list[tuple[str, float, int]] = [("ceiling height", model.ceiling_z, 2)]
    for band in model.cold_aisles:
        checked.append(("cold aisle", band[1], 1))
    for band in model.hot_aisles:
        checked.append(("hot aisle", band[1], 1))
    for row in model.rows:
        checked.append((f"rack depth (row {row.id})", row.band[1], 1))
    for axis, label in enumerate(("length", "width", "height")):
        checked.append((f"domain {label}", model.domain.size[axis], axis))
    # Racks share a size, so one warning stands for all of them; a hall has
    # hundreds and a list that long says nothing a list of one does not.
    for rack in model.racks:
        for axis, label in enumerate(("width", "depth", "height")):
            checked.append((f"rack {label}", rack.box.hi[axis], axis))
    for panel in model.panels:
        checked.append((f"{article(panel)} position", panel.position, panel.axis))
        for axis, (a0, a1) in zip(panel.in_plane_axes, panel.extent):
            checked.append((f"{article(panel)} edge", a1, axis))

    reported: set[str] = set()
    for label, value, axis in checked:
        cell = model.cell(axis)
        if misaligned(value, axis) and label not in reported:
            reported.add(label)
            snapped = round(value / cell) * cell
            warnings.append(
                f"{label}: {num(value, 3)} m is not on the {num(cell)} m grid; "
                f"the mesh will use {num(snapped)} m "
                f"({(snapped - value) * 1000:+.0f} mm)."
            )
    return warnings


#: What to call each panel where a person reads it. Internal names stay as
#: they are -- they name mesh patches and have to match the case files.
PANEL_LABEL = {
    "fan": "fan wall",
    "plenum_opening": "gallery opening",
    "ceiling": "false ceiling",
    "containment_roofwall": "containment wall",
}


def article(panel: Panel) -> str:
    """The panel's name as a person reads it, for a warning."""
    if panel.name in PANEL_LABEL:
        return PANEL_LABEL[panel.name]
    for prefix, name in (
        ("grille", "return grille"),
        ("containment_door", "containment door"),
        ("containment_wall", "containment wall"),
        ("rack_top", "rack top"),
        ("rack_end", "row end"),
        ("fan", "fan wall"),
    ):
        if panel.name.startswith(prefix):
            return name
    return f"panel {panel.name}"


def parse_cell_size(raw) -> tuple[float, float, float]:
    """``0.1`` or ``[0.2, 0.2, 0.1]`` -> a cell edge per axis."""
    if isinstance(raw, (int, float)):
        value = float(raw)
        return (value, value, value)
    values = [float(v) for v in raw]
    if len(values) != 3:
        raise ValueError(f"mesh.cell_size needs one value or three, got {raw!r}")
    return (values[0], values[1], values[2])


def parse_fan_curve(raw) -> tuple[tuple[float, float], ...] | None:
    """``[[m3h, Pa], ...]`` from the datasheet, sorted by flow."""
    if not raw:
        return None
    points = sorted((float(q), float(p)) for q, p in raw)
    return tuple(points)


def grille_loss_coefficient(free_area: float) -> float:
    """K for a thin sharp-edged grille from its free-area ratio (Idelchik).

        K = (0.707 * (1 - s)^0.375 + 1 - s)^2 / s^2

    referred to the face velocity over the gross area. An egg-crate return
    grille at 80% free area gives K ~ 0.5; a 50% perforated plate ~ 4.4. It is
    the standard thin-plate correlation and it is what a datasheet's "dp at
    face velocity" table is usually fitted to; when the datasheet gives K or a
    dp table directly, ``grilles.loss_coefficient`` overrides this.
    """
    s = min(max(free_area, 0.05), 1.0)
    return (0.707 * (1 - s) ** 0.375 + (1 - s)) ** 2 / s**2


def summary_rows(model: Model) -> list[tuple[str, str, str]]:
    """The derived numbers, as rows for the page and the CLI.

    User-facing strings are in Portuguese, like the rest of the interface; the
    code, comments and docs around them stay in English. These are the numbers
    an engineer checks before committing to a solve -- face areas and the
    velocities they imply -- so they are spelled out rather than left to be
    recomputed from the geometry.
    """
    row = model.rack_span()
    fans = model.fans
    fan = fans[0]
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
        return f"{num(area)} m2 · {num(model.airflow_m3s / area)} m/s"

    return [
        ("Domain", f"{num(dx)} x {num(dy)} x {num(dz)} m", f"{num(model.n_cells, 0)} cells"),
        (
            "Mechanical gallery",
            f"{num(model.gallery.size[0])} m deep",
            f"{num(model.gallery.volume, 1)} m3",
        ),
        (
            "Data hall",
            f"{num(model.hall.size[0])} m long",
            f"ceiling at {num(model.ceiling_z)} m",
        ),
        (
            "IT load",
            (
                f"{len(model.racks)} x {model.racks[0].load_kw:g} kW"
                + (f" in {len(model.rows)} rows" if len(model.rows) > 1 else "")
            )
            if model.racks
            else "-",
            f"{num(model.total_load_w / 1000, 1)} kW in all",
        ),
        (
            "Supply airflow",
            (
                f"{len(fans)} x {num(model.unit_airflow_m3h, 0)} = "
                if len(fans) > 1
                else ""
            )
            + f"{num(model.airflow_m3h, 0)} m3/h at {num(model.supply_temp_c, 1)} degC",
            f"design bulk dT {num(model.design_delta_t_k, 1)} K",
        ),
        (
            "Fan wall face" + (f" ({len(fans)} units)" if len(fans) > 1 else ""),
            face(fan),
            f"{num(fan.area)} m2 · {num(model.fan_face_velocity_ms)} m/s per unit",
        ),
        (
            f"Return grilles ({len(grilles)})",
            (
                f"{num(grilles[0].extent[0][1] - grilles[0].extent[0][0])} m square"
                if len(model.rows) == 1
                else f"{face(grilles[0])} strip over each hot aisle"
            )
            + (
                f", {num(model.grille_free_area * 100, 0)}% free area"
                if model.grille_free_area
                else ""
            )
            if grilles
            else "-",
            through(grille_area)
            + (
                f" · K {num(grilles[0].resistance)} · {num(model.grille_pressure_drop_pa, 1)} Pa"
                if grilles and grilles[0].resistance is not None
                else ""
            ),
        ),
        (
            "Hot aisle chimney"
            + (f" ({len(model.hot_aisles)})" if len(model.hot_aisles) > 1 else ""),
            f"{num(row[1] - row[0])} x "
            f"{num(model.hot_aisle[1] - model.hot_aisle[0])} x {num(model.ceiling_z)} m",
            through(model.chimney_area),
        ),
        ("Opening to the gallery", face(plenum), through(plenum.area)),
        (
            "Rack resistance",
            f"{num(model.rack_pressure_drop_pa)} Pa at the supply airflow",
            f"{num(RACK_PRESSURE_DROP, 0)} Pa nominal per rack",
        ),
        *hvac_rows(model),
        (
            "Fan wall pressure",
            (
                f"{num(model.fan_available_pa() or 0, 0)} Pa available at "
                f"{num(model.unit_airflow_m3h, 0)} m3/h per unit"
                + (" (curve)" if model.fan_curve else " (datasheet)")
            )
            if model.fan_available_pa()
            else "not given",
            (
                f"racks + grilles ask for "
                f"{num(model.rack_pressure_drop_pa + model.grille_pressure_drop_pa, 1)} Pa "
                f"({num((model.rack_pressure_drop_pa + model.grille_pressure_drop_pa) / model.fan_available_pa() * 100, 0)}%)"
            )
            if model.fan_available_pa()
            else "-",
        ),
    ]


def hvac_rows(model: Model) -> list[tuple[str, str, str]]:
    """The design office's two sizing checks, as summary rows."""
    h = model.hvac()
    rows = [
        (
            "Site air",
            f"{num(model.altitude_m, 0)} m altitude · {num(model.pressure_pa / 1000, 1)} kPa",
            f"rho {num(model.rho, 3)} kg/m3 at {num(model.supply_temp_c, 1)} degC",
        ),
        (
            "Rack air demand",
            f"{h['cfm_per_kw']:g} CFM/kW · {num(h['airflow_needed_m3h'], 0)} m3/h",
            (
                f"cooling supplies {num(h['airflow_m3h'], 0)} m3/h "
                f"({num(h['airflow_ratio'] * 100, 0)}%)"
                if h["airflow_ratio"] is not None
                else "-"
            ),
        ),
    ]
    if h["capacity_kw"] is not None:
        rows.append(
            (
                "Cooling capacity",
                f"{h['units']} x {num(h['unit_capacity_kw'], 1)} kW = {num(h['capacity_kw'], 0)} kW",
                f"IT load {num(h['load_kw'], 0)} kW ({num(h['capacity_ratio'] * 100, 0)}%)",
            )
        )
    else:
        rows.append(("Cooling capacity", "not given (fanwall.capacity_kw)", "-"))
    if h["unit_power_kw"]:
        rows.append(
            (
                "Cooling power input",
                f"{h['units']} x {num(h['unit_power_kw'], 1)} kW = {num(h['unit_power_kw'] * h['units'], 0)} kW",
                f"{num(h['unit_power_kw'] * h['units'] / h['load_kw'] * 100, 1)}% of the IT load",
            )
        )
    return rows


def num(value: float, decimals: int = 2) -> str:
    """Format a number the way the interface writes them: 1,234.56.

    One place, so a thousands separator never has to be decided again further
    down. The whole tool -- code, page, report -- is in English (ADR-026).
    """
    return f"{value:,.{decimals}f}"


# --- serialisation ------------------------------------------------------------


def to_dict(model: Model, spec: dict) -> dict:
    """The model as JSON for the web page.

    The page draws from this and nothing else, so what is on screen is the
    geometry that will be meshed -- including the snapping already applied.
    """
    return {
        "name": model.name,
        "cell_size": list(model.cell_size),
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
        "cold_aisles": [list(band) for band in model.cold_aisles],
        "hot_aisles": [list(band) for band in model.hot_aisles],
        "rows": [
            {
                "id": row.id,
                "band": list(row.band),
                "front_sign": row.front_sign,
                "racks": [r.id for r in row.racks],
            }
            for row in model.rows
        ],
        "fans": [p.name for p in model.fans],
        "racks": [
            {
                "id": r.id,
                "lo": list(r.box.lo),
                "hi": list(r.box.hi),
                "load_kw": r.load_kw,
                "airflow_axis": r.airflow_axis,
                "airflow_sign": r.airflow_sign,
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
                "resistance": p.resistance,
            }
            for p in model.panels
        ],
        "operating": {
            "airflow_m3h": model.airflow_m3h,
            "unit_airflow_m3h": model.unit_airflow_m3h,
            "fan_count": model.fan_count,
            "fan_face_velocity_ms": round(model.fan_face_velocity_ms, 3),
            "supply_temp_c": model.supply_temp_c,
            "load_kw": model.total_load_w / 1000.0,
            "design_delta_t_k": round(model.design_delta_t_k, 2),
            "chimney_velocity_ms": round(
                model.airflow_m3s / model.chimney_area, 3
            ) if model.chimney_area else None,
            "rack_drop_pa": round(model.rack_pressure_drop_pa, 2),
            "grille_drop_pa": round(model.grille_pressure_drop_pa, 2),
            "fan_available_pa": model.fan_available_pa(),
            "fan_curve": [list(p) for p in model.fan_curve] if model.fan_curve else None,
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
        "hvac": model.hvac(),
        "alerts": model.alerts,
        "site": {"altitude_m": model.altitude_m, "pressure_pa": round(model.pressure_pa), "rho": round(model.rho, 4)},
        "warnings": model.warnings,
        "spec": spec,
    }
