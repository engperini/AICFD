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

import dataclasses
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

#: A supply plenum, where the case asks for one, is this deep and its grilles
#: this size. The depth is between the two leaves of the wall that divides the
#: gallery from the hall; the grille is what the inner leaf carries (ADR-058).
PLENUM_DEPTH = 1.2
PLENUM_GRILLE_WIDTH = 2.0




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
    sign: int = 1
    """Which side of the panel the mechanical gallery is on, for the surfaces
    that separate gallery from hall: +1 when the gallery is at lower x (the
    hall is at higher x), -1 when the gallery is at higher x. A hall with a
    gallery at each end carries both, and the sign is what tells the fan wall
    which half of its baffle pair is the intake (ADR-027)."""
    return_z: float | None = None
    """A downflow unit's RETURN face, a storey above its supply one.

    A fan wall is one vertical plane: the air leaves the gallery and arrives
    in the hall across the same face. A downflow unit is two horizontal ones
    -- it draws through its top and delivers through its bottom -- and the
    two are a unit's height apart. Carried on the supply panel rather than as
    a second panel so that everything counting units keeps counting units
    (ADR-076)."""
    of_rack: bool = False
    """This face belongs to the rack row, not to the room: the rack's own lid
    and ends, which exist so the porous zone breathes front to back and not
    out of its sides. They lie exactly on the rack box, which the drawings
    already show, so they are the rack there rather than a wall of their own
    (ADR-045). The solver does not read this."""

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
    resistance_kw: float | None = None
    """The load this cabinet's flow resistance is calibrated at, where that
    differs from what it dissipates.

    A cabinet's resistance is a property of the cabinet, not of how much heat
    it makes. An unloaded position is blanked -- that is what blanking plates
    are for -- so it resists like the cabinets either side of it and simply
    dissipates nothing. Left calibrated at its own zero load it became a hole
    through the row instead, and in the worked POD a third of the row open to
    the hot aisle dropped the measured resistance to 6% of the closed form
    (ADR-054)."""
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
    def resistance_airflow_m3s(self) -> float:
        """The flow its resistance is calibrated at: what a cabinet like this
        one draws when it is populated, whatever this one dissipates."""
        kw = self.load_kw if self.resistance_kw is None else self.resistance_kw
        return kw * self.cfm_per_kw * M3H_PER_CFM / 3600.0

    @property
    def rated_airflow_m3h(self) -> float:
        return self.rated_airflow_m3s * 3600.0

    @property
    def face_velocity_ms(self) -> float:
        return self.resistance_airflow_m3s / self.face_area if self.face_area else 0.0

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
    galleries: list[Box]
    """The mechanical galleries, in x order. One for a POD or a hall with a
    single technical corridor; two when the hall has one at each end. The
    ceiling plenum stays single either way and opens into all of them."""
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
    equipment: object | None = None
    """The unit out of `equipment/`, when the spec names one. Carries the
    design selection, which is what the coil is fitted from and so what says
    how much it transfers at the return temperature the room actually
    produces (ADR-036, ADR-063)."""
    fan_control: str = "independent"
    """`independent` -- every unit runs to its own return, which is what a
    unit with no network does. `team` -- the units of one mechanical gallery
    share the worst return any of them sees, so a unit far from the load
    opens its valve as far as the loaded one has to instead of idling at the
    setpoint (ADR-064)."""
    plenum_depth: float | None = None
    """Distance between the two leaves of the wall into the hall, where the
    case asks for a supply plenum. None is a single wall with the units
    blowing straight through it, which is the arrangement without one
    (ADR-058)."""
    supply_mesh_k: float | None = None
    """Loss coefficient of the mesh across the units' opening, where the case
    has one instead of a plenum -- the same 13 x 13 mm woven mesh that closes
    the return plenum into the gallery. The wall stays single and the room
    keeps its dimensions, which is the whole reason to choose it: a hall
    already built cannot grow 1,2 m at each end (ADR-060)."""
    blocks: list[tuple[float, float]] = field(default_factory=list)
    """The x span of each block of rack rows. A row cut by a transverse
    divider is two blocks, each a containment volume of its own, each served
    by the gallery at its end. Empty means one block spanning every rack."""
    floor_height: float | None = None
    """Depth of the raised-floor supply plenum, where a case has one. The room
    above it is unchanged and the building is taller by this (ADR-076)."""
    floor_tile_k: float | None = None
    """Loss coefficient of one perforated plate, on its gross face."""
    cage: str | None = None
    """How the customer cage round the rows is built -- `mesh` or `drywall`
    -- or None where the hall has no cage. The two are different rooms: a
    drywall cage is a partition the air cannot cross, a mesh one is a
    resistance the air pays twice, once in and once out (ADR-096)."""
    warnings: list[str] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)
    """Design criteria the HVAC does not meet. Alerts, never blockers: a
    conceptual study wants to see what an undersized plant does."""

    # --- the POD's singular names, for the one-row case -------------------------

    @property
    def gallery(self) -> Box:
        """The first gallery. A POD has exactly one and calls it *the*
        gallery; a hall with two keeps them in ``galleries``."""
        return self.galleries[0]

    @property
    def dividers(self) -> list[float]:
        """x of each wall between a gallery and the hall, in gallery order.

        The first gallery is always at lower x than the hall, the second (when
        there is one) at higher x, so the dividing walls are the hall's own
        two x faces.
        """
        walls = [self.hall.lo[0], self.hall.hi[0]]
        return walls[: len(self.galleries)]

    @property
    def rack_blocks(self) -> list[tuple[float, float]]:
        """The rack blocks, falling back to the one span every rack shares."""
        return self.blocks or ([self.rack_span()] if self.racks else [])

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
        alerts += self.plenum_alerts()
        return alerts

    def plenum_alerts(self) -> list[str]:
        """What a supply plenum is checked for before it is solved (ADR-059).

        Whether the units have the pressure for what the loop costs, answered
        from the specification alone. It needs no solve, and finding out
        after one is finding out late.

        What is NOT here is a verdict on the face velocity. It was, at 3 m/s,
        and it was wrong to be: 3,3 m/s out of a supply grille is ordinary in
        a data hall, so the alert fired on a sound design and spent the
        reader's attention for nothing. The velocity is reported among the
        derived numbers, beside the fan wall's own, and the engineer judges
        it. An alert has to earn its interruption (ADR-059).
        """
        grilles = self.plenum_grilles
        if not grilles and self.supply_mesh_k is None:
            return []
        alerts = []
        available = self.fan_available_pa()
        cost = self.loop_pressure_drop_pa
        if available is not None and cost > available:
            widen = self.widening_needed()
            alerts.append(
                f"The units do not have the pressure for this loop: the "
                f"surfaces alone cost {num(cost, 0)} Pa and the unit's curve "
                f"offers {num(available, 0)} Pa at "
                f"{num(self.unit_airflow_m3h, 0)} m3/h. That total is a LOWER "
                f"bound -- the aisles and the turns are not in it -- so the "
                f"real duty is higher still. Where it goes: "
                + ", ".join(f"{what} {num(pa, 0)} Pa"
                            for what, pa in self.pressure_budget())
                + "."
                + (f" {widen}" if widen else "")
            )
        return alerts

    def pressure_budget(self) -> list[tuple[str, float]]:
        """What each surface costs the fan, largest first.

        A total says the plant is short; the breakdown says what to change.
        """
        mesh = self.mesh_pressure_drop_pa or 0.0
        items = [
            ("the racks", self.rack_pressure_drop_pa),
            ("the return grilles", self.grille_pressure_drop_pa),
            # Named separately on a raised floor, because they are two
            # openings in two places and a reader adding the column up should
            # find the total (ADR-076).
            ("the mesh into the gallery, above the ceiling" if self.floor_height
             else "the mesh into the gallery", mesh),
            ("the mesh under the deck", mesh if self.floor_height else 0.0),
            ("the supply grilles", self.plenum_pressure_drop_pa or 0.0),
            ("the floor plates", self.floor_pressure_drop_pa or 0.0),
        ]
        return sorted(((w, pa) for w, pa in items if pa > 0),
                      key=lambda row: -row[1])

    def widening_needed(self) -> str:
        """How much bigger the supply grilles would have to be to fit.

        No threshold in it. A perforated surface costs `K rho v^2 / 2` and `v`
        is the flow over its area, so its pressure falls with the SQUARE of
        the area: to give back the pascals the unit is short, the face has to
        grow by the square root of the ratio. That answer is arithmetic from
        the datasheet and the geometry, which is why it can be stated where a
        velocity limit could not (ADR-059).
        """
        grilles = self.plenum_grilles
        cost = self.plenum_pressure_drop_pa
        available = self.fan_available_pa()
        if not grilles or not cost or available is None:
            return ""
        short = self.loop_pressure_drop_pa - available
        target = cost - short
        if short <= 0 or target <= 0:
            # Even a free opening would not close the gap: the pressure is
            # somewhere else, and widening these would be the wrong repair.
            return ""
        sides = len({g.position for g in grilles})
        area = sum(g.area for g in grilles) / sides
        return (
            f"The supply grilles are {num(cost, 0)} Pa of it: "
            f"{num(area, 1)} m2 per plenum would have to be "
            f"{num(area * (cost / target) ** 0.5, 1)} m2 to give back the "
            f"{num(short, 0)} Pa the unit is short, since their pressure "
            f"falls with the square of the face."
        )

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
        """Cross-section of the contained hot aisles together, normal to the rise.

        One chimney per hot aisle *per block*: a row cut by a transverse
        divider has a separate contained volume either side of it, and the
        gap between the blocks is not a chimney.
        """
        return sum(
            (bx1 - bx0) * (hi - lo)
            for bx0, bx1 in self.rack_blocks
            for lo, hi in self.hot_aisles
        )

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
        # Any cabinet gives the coefficient, because they all carry the same
        # one: an unloaded position is blanked, so it resists like the ones
        # either side of it and only its heat is missing (ADR-054). The guard
        # is for a hall whose standard is itself zero -- nothing installed
        # anywhere, so there is nothing to ask of the fan.
        reference = next(
            (rack for rack in self.racks if rack.resistance_airflow_m3s > 0), None
        )
        if reference is None:
            return 0.0
        _d, f = reference.darcy_forchheimer()
        return 0.5 * self.rho * f * velocity**2 * reference.depth

    @property
    def fan_teams(self) -> list[list[str]]:
        """The units that share a network, one list per mechanical gallery.

        A gallery is the group: the units in it are on one set of pipes and
        one controller, and a hall with a gallery at each end has two
        networks that know nothing of each other. Read off the geometry --
        the units at one dividing wall are one gallery's -- rather than from
        a list somebody has to keep in step with the layout (ADR-064).
        """
        groups: dict[float, list[str]] = {}
        for panel in self.fans:
            groups.setdefault(round(panel.position, 6), []).append(panel.name)
        return [names for _at, names in sorted(groups.items())]

    def team_of(self, name: str) -> list[str]:
        """The units sharing a network with this one, itself included. Just
        itself where the units run independently."""
        if self.fan_control != "team":
            return [name]
        for team in self.fan_teams:
            if name in team:
                return team
        return [name]

    @property
    def unloaded_racks(self) -> int:
        """Positions the hall has and does not load."""
        return sum(1 for rack in self.racks if rack.load_kw <= 0)

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

    @property
    def mesh_pressure_drop_pa(self) -> float | None:
        """What the mesh closing the plenum into a gallery costs, from its K.

        All of the return passes through it too, once, so it is the same
        closed form as the grilles -- and it is worth stating separately
        because nothing else in the loop is paid for by a surface nobody
        chose (ADR-048). None where the case has no such opening.
        """
        openings = [p for p in self.panels
                    if p.name.startswith("plenum_opening") and p.resistance is not None]
        area = sum(p.area for p in openings)
        if not openings or area <= 0:
            return None
        velocity = self.airflow_m3s / area
        return openings[0].resistance * 0.5 * self.rho * velocity**2

    @property
    def plenum_grilles(self) -> list[Panel]:
        """The supply grilles, by the leaf they are in.

        Returned flat; `plenum_face_velocity_ms` groups them, because with a
        gallery at each end each plenum feeds its own half of the room and the
        one that is tighter is the one that decides.
        """
        return [p for p in self.panels if p.name.startswith("supply")]

    @property
    def plenum_face_velocity_ms(self) -> float | None:
        """How fast the air leaves the tightest supply plenum, at design flow.

        This is the number that says whether the openings are big enough. A
        grille is sized on it: too small a face and the air arrives as a jet
        -- noisy, thrown across the aisle instead of into the racks -- and the
        pressure it costs goes up with its square, which the fan pays for
        twice over (ADR-059).

        Gross area, not free area: it is the face velocity a grille catalogue
        quotes and the one its pressure table is fitted to.
        """
        grilles = self.plenum_grilles
        if not grilles or self.airflow_m3s <= 0:
            return None
        # Each leaf feeds from its own gallery, and the units are split
        # between the galleries, so the flow through a leaf is its share.
        sides: dict[float, float] = {}
        for grille in grilles:
            sides[grille.position] = sides.get(grille.position, 0.0) + grille.area
        share = self.airflow_m3s / len(sides)
        worst = max(share / area for area in sides.values() if area > 0)
        return worst

    @property
    def plenum_pressure_drop_pa(self) -> float | None:
        """What the supply grilles cost at design flow, from their K.

        Closed form, like the return grilles: every cubic metre the units move
        leaves through them once.
        """
        grilles = self.plenum_grilles
        velocity = self.plenum_face_velocity_ms
        if not grilles or velocity is None or grilles[0].resistance is None:
            return None
        return grilles[0].resistance * 0.5 * self.rho * velocity**2

    @property
    def floor_tiles(self) -> list[Panel]:
        """The perforated plates in the cold aisle floor (ADR-076)."""
        return [p for p in self.panels if p.name.startswith("tile_")]

    @property
    def floor_face_velocity_ms(self) -> float | None:
        """How fast the air leaves the plates, at design flow.

        The number that says whether enough floor is open. Every cubic metre
        the units move arrives in the hall through these once, so it is the
        total flow over their total gross area -- gross, not free, because
        that is the face velocity a plate catalogue quotes and the one its
        pressure table is fitted to (ADR-059).

        A plate at 3 m/s is a jet that throws cold air over the cabinet it was
        meant to feed; the cure is more open floor, and this is the number
        that shows it. No verdict is attached: what is high for one hall is
        ordinary in another, and the engineer knows which they have.
        """
        tiles = self.floor_tiles
        if not tiles or self.airflow_m3s <= 0:
            return None
        area = sum(t.area for t in tiles)
        return self.airflow_m3s / area if area > 0 else None

    @property
    def floor_pressure_drop_pa(self) -> float | None:
        """What the plates cost at design flow, from their K.

        Closed form, like the return grilles and the plenum's: every cubic
        metre the units move crosses them once.
        """
        tiles = self.floor_tiles
        velocity = self.floor_face_velocity_ms
        if not tiles or velocity is None or tiles[0].resistance is None:
            return None
        return tiles[0].resistance * 0.5 * self.rho * velocity**2

    @property
    def loop_pressure_drop_pa(self) -> float:
        """What the surfaces the air has to cross cost it, added up.

        The racks, the return grilles, the mesh into the gallery and whichever
        supply surface the case has -- plenum grilles or floor plates -- each
        from its own closed form. A LOWER BOUND on what the unit has to
        produce, not the whole system: the aisles, the turns and the plenum's
        own velocity pressure are not in it, and only the solved field has
        those. Enough to say before a run whether a unit is obviously short
        (ADR-059).

        On a raised floor the mesh is counted TWICE, and that is not an error:
        the same opening exists at both ends of the dividing wall, once above
        the false ceiling for the return and once below the deck for the
        supply, and the air crosses both (ADR-076).
        """
        return (
            self.rack_pressure_drop_pa
            + self.grille_pressure_drop_pa
            + (self.mesh_pressure_drop_pa or 0.0) * (2 if self.floor_height else 1)
            # The mesh leaf, where the case has one instead of grilles, is a
            # `supply` surface like they are and is already in the line above.
            + (self.plenum_pressure_drop_pa or 0.0)
            + (self.floor_pressure_drop_pa or 0.0)
        )

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
class Station:
    """One station on the air loop, reported as a STREAM rather than a point.

    It used to be three probes whose mean stood for a place, and that was the
    wrong instrument: a probe measures the cell it sits in, and a room with
    cabinets of different load is not one temperature anywhere (ADR-078). A
    station names a surface the whole airflow crosses -- the units' supply, the
    cabinets' intakes, the ceiling, the units' return -- and reports the mixing
    cup of the air crossing it, with the range that air actually spans beside
    it. Nothing about it depends on where a logger was hung.

    The range is not noise to be averaged away. On a half-populated row it is
    the finding: intakes 20,0 to 20,6 degC say the containment is holding,
    and an aisle running 24 to 37 degC says the row is half empty, which it is.
    """

    name: str
    label: str
    note: str


def stations(model: Model) -> list[Station]:
    """The four stations of the air loop, in the order the air passes them.

    They are declared here and measured in `post.station_readings`, which is
    the same split the rest of the model uses: what a case HAS is geometry,
    what it DOES is the solved field.
    """
    if not model.racks:
        return []
    unit = "room unit" if model.floor_height else "fan wall"
    return [
        Station("supply", "Supply",
                f"leaving the {unit}, mixed over every unit"),
        Station("rack_intake", "Rack intake",
                "entering the cabinets, mixed over every rack by the airflow "
                "each one draws"),
        Station("aisle_exit", "Aisle exit",
                "leaving the containment through the ceiling, weighted by the "
                "mass flux crossing it"),
        Station("unit_return", "Unit return",
                f"arriving at the {unit}, mixed over every intake"),
    ]


@dataclass
class _Layout:
    """What a layout hands the model: where everything is, before snapping."""

    domain: Box
    galleries: list[Box]
    hall: Box
    rows: list[Row]
    cold_aisles: list[tuple[float, float]]
    hot_aisles: list[tuple[float, float]]
    fans: list[Panel]
    grilles: list[Panel]
    walls: list[Panel]
    blocks: list[tuple[float, float]] = field(default_factory=list)
    plenum_depth: float | None = None
    supply_mesh_k: float | None = None
    row_notes: list[str] = field(default_factory=list)
    """Widths the mesh moved, said by position rather than by face (ADR-074)."""


#: What a case uses where it names nothing. The house specification: the
#: surfaces are standards, and a case that quietly used others would produce a
#: fan duty nobody could reproduce (ADR-048).
DEFAULT_COMPONENTS = {
    "gallery_mesh": "gallery-mesh-13",
    "cage": "cage-mesh-13",
    "supply_grille": "supply-grille-2000",
    "ceiling_return": "ceiling-return-600",
    "floor_tile": "floor-tile-600",
    "containment": "containment-panel",
    "distribution_loss": "pdu-distribution-loss",
}


def component_for(spec: dict, role: str):
    """The component filling one role in this case, or None.

    A case names components under `components:`; anything it leaves out takes
    the house default. `components: {role: null}` is how a case says it has
    none of that surface at all.
    """
    from aicfd import components as library

    named = spec.get("components") or {}
    if role in named and named[role] is None:
        return None
    chosen = named.get(role) or DEFAULT_COMPONENTS.get(role)
    if not chosen:
        return None
    try:
        return library.load(chosen)
    except library.UnknownComponent:
        return None


def components_in_use(spec: dict) -> list[dict]:
    """Which component fills each role in this case, and what else could.

    The model page names the choice and shows what it costs; the numbers
    behind it are edited on the components page (ADR-048). A role the library
    has nothing for is left out rather than shown empty.
    """
    from aicfd import components as library

    out = []
    for role, label in library.ROLES.items():
        options = [library.load(name) for name in library.for_role(role)]
        if not options:
            continue
        chosen = component_for(spec, role)
        out.append({
            "role": role,
            "label": label,
            "chosen": chosen.id if chosen else None,
            "fixed": bool(chosen and chosen.fixed),
            "applied": bool(chosen and chosen.applied),
            "kind": chosen.kind if chosen else "surface",
            "free_area": chosen.free_area if chosen else None,
            "share": chosen.share if chosen else None,
            "k": round(chosen.k, 3) if chosen and chosen.k is not None else None,
            "options": [{"id": c.id, "name": c.name,
                         "free_area": c.free_area, "share": c.share,
                         "k": round(c.k, 3) if c.k is not None else None}
                        for c in options],
        })
    return out


def fan_depth(spec: dict) -> float | None:
    """How far a fan wall unit reaches back into the mechanical gallery.

    Read from the spec, like the width and the height beside it: naming a unit
    fills all three from its datasheet, and a typed value wins over that. A
    case that states none leaves the fan wall as the plane the solver sees,
    because inventing a depth would put a machine on the drawing that nothing
    in the case describes.
    """
    stated = (spec.get("fanwall") or {}).get("depth")
    return float(stated) if stated else None


def wanted_arrangement(spec: dict) -> str:
    """Which kind of machine THIS ROOM is fed by.

    A raised floor is fed by units that stand in the room and discharge
    downward through the deck; a gallery wall is fed by a wall of fans. Each
    is the wrong machine in the other's room, and the coil being right is
    what makes that wrong answer look like a right one (ADR-072, ADR-076).
    """
    return "downflow" if raised_floor_for(spec) else "fanwall"


def equipment_mismatch(unit, spec: dict) -> str | None:
    """Why this unit cannot cool this room, or None where it can.

    One rule with two readers: `equipment_for` refuses on it, and the model
    page's unit picker says beside each option what it would say. Two copies
    of it would drift, and the drift a reader meets is a picker offering a
    machine the Apply then refuses -- or, worse, greying out one that would
    have worked (ADR-092).
    """
    # A DX UNIT IS USABLE, AT ITS RATED POINT. What is not modelled is its
    # COIL: capacity against return air follows the refrigerant circuit, the
    # compressor's speed and the outdoor air its condenser rejects into
    # (ADR-073). Everything the room needs -- the airflow, the supply
    # temperature, the dimensions, the sensible capacity at the rated return
    # -- is on the sheet and as well defined as any chilled-water unit's. So
    # the case runs, the coupled re-solve is skipped because there is nothing
    # to re-ask, and `dx_limitation` below says so before the solve rather
    # than after it (ADR-097).
    wanted = wanted_arrangement(spec)
    if unit.arrangement != wanted:
        how = ("stands in the room and discharges downward"
               if unit.arrangement == "downflow"
               else "is a wall of fans in a mechanical gallery")
        needs = ("a raised floor, which this case has not got"
                 if unit.arrangement == "downflow"
                 else "no raised floor, and this case has one")
        return (
            f"{unit.model} {how}, which needs {needs}. Its coil is in the "
            f"library and correct; it is the room that does not match. Name "
            f"a {wanted} unit, or "
            + ("add `floor.enabled: true`" if wanted == "fanwall"
               else "remove `floor.enabled`")
        )
    return None


def equipment_defaults(unit, design_return_c=None) -> dict:
    """What naming this unit fills the `fanwall` block with.

    One statement of it, because it has three readers now: `equipment_for`,
    which fills a case that states nothing; the page, which shows the numbers
    the moment a unit is picked; and the change that CLEARS the previous
    unit's numbers when the unit changes (ADR-094). Three copies of this
    mapping would put three different machines in front of a reader.
    """
    point = unit.design_point(design_return_c)
    return {
        "airflow_m3h": point["airflow_m3h"],
        "capacity_kw": point["nscc_kw"],
        "power_kw": point["power_kw"],
        "supply_temp_c": point["supply_c"],
        "width": unit.size[0],
        "depth": unit.size[1],
        "height": unit.size[2],
        "static_pressure_pa": unit.selection.get("esp_pa"),
        "curve": (unit.curve or {}).get("points"),
    }


def dx_limitation(unit) -> str | None:
    """What a direct-expansion unit's result does NOT answer, or None.

    A DX unit's capacity is not a curve this software can evaluate, so the
    run holds the supply temperature at the unit's selected one instead of
    re-solving it against the return the room produces (ADR-040, ADR-097).
    The result is this room at the plant's RATED duty, which is a real answer
    to a real question and not the same question a chilled-water case answers.
    Said before the solve, because after it the number is already on a page.
    """
    if unit is None or unit.cooling == "chilled_water":
        return None
    point = unit.design or {}
    rated = point.get("nscc_kw")
    at = point.get("return_c")
    supply = point.get("supply_c")
    return (
        f"{unit.model} is a {unit.cooling} unit: its capacity follows the "
        f"refrigerant circuit and the outdoor air its condenser rejects into, "
        f"which is not modelled. This result is the room at the unit's RATED "
        + (f"point -- {num(rated, 1)} kW at {num(at, 1)} degC return, "
           f"supplying {num(supply, 1)} degC -- " if rated and at else "point ")
        + "with the supply temperature held there rather than re-solved "
        "against the return the room produces. Read the return this run "
        "reports against that rated return: the further apart they are, the "
        "less the rated capacity says about what the machine would do here"
    )


def equipment_in_use(spec: dict) -> dict | None:
    """Which unit this case names, and what else the library holds.

    The same shape as `components_in_use`, for the same reason: the choice
    belongs on the page that describes the room, and the numbers behind it on
    the page that owns the machine (ADR-048, ADR-051). Until this existed the
    unit could be changed only by editing the YAML -- the page showed its
    name, linked to its datasheet, and gave no way to pick another (ADR-092).

    A unit that does not suit this room is LISTED, with the reason. Hiding it
    answers nothing: the reader went looking for their CRAH and a picker it
    is missing from says only that the software has never heard of it. It is
    also not disabled, because ticking `floor.enabled` and choosing a
    downflow unit is one edit made of two fields, and the Apply judges the
    pair (ADR-055).
    """
    from aicfd import equipment as library

    names = library.available()
    if not names:
        return None
    chosen = (spec.get("fanwall") or {}).get("model")
    options = []
    for name in names:
        try:
            unit = library.load(name)
        except Exception:  # noqa: BLE001 -- a half-written unit is still one
            continue
        why = equipment_mismatch(unit, spec)
        options.append({
            "model": unit.model,
            "family": unit.family,
            "arrangement": unit.arrangement,
            "cooling": unit.cooling,
            "suits": why is None,
            "why": why,
            # What picking this one puts in the fields. The page writes them
            # in at once, so a reader sees the machine they chose rather than
            # the last one's numbers under its name (ADR-094).
            "defaults": {k: v for k, v in
                         equipment_defaults(unit).items() if v is not None},
        })
    return {
        "chosen": chosen if any(o["model"] == chosen for o in options) else None,
        "wants": wanted_arrangement(spec),
        "options": options,
    }


def equipment_for(spec: dict):
    """Fill the fan wall block from the library when the spec names a unit.

    `fanwall.model: CA80NPVG6` is enough to describe a machine: its
    dimensions, its airflow, its capacity, its power and its supply
    temperature all come from the manufacturer's own selections, taken at
    `fanwall.design_return_c` (the warmest selection by default, which is how
    a plant is normally sized).

    Anything written in the spec still wins. The library is a default, not a
    lock: a study of the same unit at a different external static pressure,
    or with a fan speed the selections do not cover, is a legitimate thing to
    type over the top -- and it stays visible in the spec, which is where a
    reader looks for what was assumed (ADR-036).

    Returns the Equipment, or None when the spec names none.
    """
    fan = spec.get("fanwall") or {}
    name = fan.get("model")
    if not name:
        return None
    from aicfd import equipment as library

    unit = library.load(name)
    refusal = equipment_mismatch(unit, spec)
    if refusal:
        raise ValueError(refusal)
    # The one condition a plant changes without changing the machine, and the
    # one the supply air temperature follows almost one for one once the
    # valve is open. A study at another chilled water temperature is an
    # ordinary thing to want, and it is the same unit (ADR-040).
    water = fan.get("entering_water_c")
    if water is not None:
        # The FIT is left alone. The manufacturer's selections were taken at
        # the water they were taken at, and re-reading them at another
        # temperature would silently change the water flow behind every row
        # and corrupt the conductance recovered from them. What moves is only
        # the water this plant circulates -- the machine is the same machine.
        fitted = unit.coil
        if fitted is not None:
            unit = dataclasses.replace(
                unit, _coil=dataclasses.replace(fitted, water_c=float(water))
            )
    defaults = equipment_defaults(unit, fan.get("design_return_c"))
    for key, value in defaults.items():
        if value is not None and fan.get(key) is None:
            fan[key] = value
    # The site follows the selection unless the spec says otherwise: a unit
    # selected at 750 m and run at sea level moves different air, and that is
    # a decision, not a default to inherit silently.
    site = spec.setdefault("site", {})
    if site.get("altitude_m") is None and unit.selection.get("elevation_m") is not None:
        site["altitude_m"] = unit.selection["elevation_m"]
    spec["fanwall"] = fan
    return unit


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
    unit = equipment_for(spec)
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
    # Each wall between a gallery and the hall is pierced by that gallery's fan
    # walls and, above the false ceiling, by the return opening along the whole
    # width. With a gallery at each end there are two openings and still one
    # plenum: the volume above the false ceiling is continuous across the hall,
    # so it collects from every hot aisle and feeds both galleries (ADR-027).
    dividers = [layout.hall.lo[0], layout.hall.hi[0]][: len(layout.galleries)]
    # The opening is closed with a woven security mesh, and every cubic metre
    # the plant moves passes through it once. Nothing about it is a choice --
    # it is the building -- but the fan still pays for it, so it carries the
    # mesh's loss coefficient rather than standing open (ADR-048).
    mesh = component_for(spec, "gallery_mesh")
    for i, x in enumerate(dividers):
        panels.append(
            Panel(
                "plenum_opening" if i == 0 else f"plenum_opening{i + 1}",
                "opening",
                axis=0,
                position=x,
                extent=((0.0, layout.domain.hi[1]), (ceiling, layout.domain.hi[2])),
                resistance=mesh.k if mesh else None,
                sign=1 if i == 0 else -1,
            )
        )
    # The false ceiling covers the hall only: a mechanical gallery is open to
    # the slab, which is how the return air reaches the units.
    panels.append(
        Panel(
            "ceiling",
            "wall",
            axis=2,
            position=ceiling,
            extent=((layout.hall.lo[0], layout.hall.hi[0]), (0.0, layout.domain.hi[1])),
        )
    )
    panels.extend(layout.grilles)
    panels.extend(layout.walls)

    model = Model(
        name=name,
        domain=layout.domain,
        galleries=layout.galleries,
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
        equipment=unit,
        blocks=layout.blocks,
        fan_control=str(fan.get("control", "independent")).strip().lower(),
        fan_static_pa=(
            float(fan["static_pressure_pa"]) if "static_pressure_pa" in fan else None
        ),
        fan_curve=parse_fan_curve(fan.get("curve")),
        grille_free_area=_grille_free_area(spec),
        plenum_depth=layout.plenum_depth,
        supply_mesh_k=layout.supply_mesh_k,
    )
    # Snap to the mesh *before* anyone reads the model. The drawing, the
    # summary table and the solved case then describe the same geometry -- a
    # table that quotes the nominal area while the mesh builds another one is
    # exactly the kind of quiet disagreement this module exists to prevent.
    # The floor goes under a room that is already placed: everything moves up
    # by its depth and the plenum is built beneath (ADR-076). Done here, after
    # the panels are assembled and before the mesh is snapped, so the snapper
    # sees the finished geometry and the deck lands on a grid line like every
    # other plane.
    floor = raised_floor_for(spec)
    if floor:
        _raise_onto_floor(model, floor, spec, cell)
    # After the floor, because a cage stands ON the finished floor and its
    # walls run from the deck up; before the snap, because its planes land on
    # cell faces like every other (ADR-096).
    cage = cage_for(spec)
    cage_notes: list[str] = []
    if cage:
        model.panels.extend(_cage_panels(model, cage, spec, cage_notes))
        model.cage = cage["construction"]
    # The row's own notes first: a cabinet width the mesh moved is said by
    # position, which is what a reader can act on, where the general alignment
    # check can only say a face fell between grid lines (ADR-074).
    #
    # The cage's notes are collected into a list of their own and added here
    # rather than appended to `model.warnings` as they are found: this line
    # REPLACES that list, so a note written before it disappeared -- silently,
    # which for a note saying "no cage wall is built on this side" is the
    # worst way to lose one (ADR-098).
    model.warnings = list(layout.row_notes) + cage_notes + check_mesh_alignment(model)
    # The fan placement snaps the unit's width itself (so units can be packed
    # without overlapping), so the alignment check never sees the nominal one.
    nominal = float(fan["width"]) if "width" in fan else None
    if nominal is not None and layout.fans:
        built = layout.fans[0].extent[0][1] - layout.fans[0].extent[0][0]
        if abs(built - nominal) > 1e-6:
            model.warnings.insert(
                0,
                f"fan wall width: {num(nominal, 3)} m falls between "
                f"{num(cell[1])} m grid lines; the mesh uses {num(built)} m "
                f"({(built - nominal) * 1000:+.0f} mm).",
            )
    # After snapping, not before: an alert quotes areas and velocities, and
    # the geometry those belong to is the one the mesher builds. Computed
    # first, the grille area in the alert disagreed with the area on the page
    # by a cell -- small, and exactly the kind of quiet disagreement the
    # snapping comment above exists to prevent.
    model = snap_to_mesh(model)
    # After snapping, because it is the SNAPPED plates the mesher lays: on the
    # hall that found this, 60 footprints collided before the snap and 90
    # after it, so a check run on the unsnapped geometry would have passed
    # thirty of them straight through to OpenFOAM (ADR-095).
    _plates_must_fit(model)
    model.alerts = model.hvac_alerts()
    return model


def _grille_free_area(spec: dict) -> float | None:
    """Open area of the ceiling return grilles.

    The case wins where it states one -- a study of a hall already built uses
    what is in it. Otherwise the component the case names, or the house
    standard (ADR-048).
    """
    free_area = spec["grilles"].get("free_area")
    if free_area is not None:
        return float(free_area)
    grille = component_for(spec, "ceiling_return")
    return grille.free_area if grille else None


def _plenum_k(spec: dict) -> float | None:
    """What a supply grille costs the plenum it is fed from.

    The case wins where it states one; otherwise the component the case names
    for this role, which is where a grille's free area belongs (ADR-048). A
    grille with no resistance at all is a hole in the wall, which is a
    legitimate thing to model -- it is the control case that says what the
    grilles themselves cost.
    """
    plenum = spec.get("plenum") or {}
    grille = plenum.get("grille") or {}
    if "loss_coefficient" in grille:
        return float(grille["loss_coefficient"])
    if "free_area" in grille:
        return grille_loss_coefficient(float(grille["free_area"]))
    component = component_for(spec, "supply_grille")
    return component.k if component else None


def _supply_mesh_k(spec: dict) -> float | None:
    """What the mesh across the units' opening costs (ADR-060).

    The same mesh as the one that closes the return plenum where it opens
    into a mechanical gallery, and the same component: 13 x 13 mm openings in
    3 mm woven stainless. It is one product and one house standard, so a case
    that changes it changes it on both sides of the loop, which is what a
    house standard means (ADR-048).
    """
    plenum = spec.get("plenum") or {}
    if "mesh_loss_coefficient" in plenum:
        return float(plenum["mesh_loss_coefficient"])
    mesh = component_for(spec, "gallery_mesh")
    return mesh.k if mesh else None


def _grille_k(spec: dict) -> float | None:
    if "loss_coefficient" in spec["grilles"]:
        return float(spec["grilles"]["loss_coefficient"])
    grille = component_for(spec, "ceiling_return")
    if grille is not None and spec["grilles"].get("free_area") is None:
        return grille.k  # its datasheet K where it has one, its free area where not
    free_area = _grille_free_area(spec)
    return grille_loss_coefficient(free_area) if free_area is not None else None


def row_plan(spec: dict, count: int) -> list[dict]:
    """What stands at each position of the TYPICAL row.

    A hall is bought as N identical cabinets, and that stays the default:
    `count` positions of `racks.size` at `racks.load_kw`. `racks.row` states a
    typical row instead -- a list of positions, each a cabinet or a blanking
    panel, each free to carry its own width and load -- and every row of the
    hall is built from it (ADR-074).

    Its length governs the row. A row of eleven cabinets and a 300 mm blank is
    twelve positions of two kinds, and stating the count a second time only
    invites the two to disagree.

    Each entry is a mapping and every key is optional:
      ``{}``                     a standard cabinet
      ``{"load_kw": 12}``        a cabinet at its own load
      ``{"width": 0.8}``         a wider cabinet
      ``{"blank": true, "width": 0.3}``   a blanking panel
    """
    raw = (spec.get("racks") or {}).get("row")
    if not raw:
        return [{} for _ in range(count)]
    plan = []
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(
                f"racks.row[{i}] is {entry!r}; each position is a mapping, "
                f"empty for a standard cabinet"
            )
        unknown = set(entry) - {"blank", "width", "load_kw", "type"}
        if unknown:
            raise ValueError(
                f"racks.row[{i}] has {', '.join(sorted(unknown))}; a position "
                f"takes type, blank, width and load_kw"
            )
        plan.append(dict(entry))
    return plan


def _raise_onto_floor(model: "Model", floor: dict, spec: dict, cell) -> None:
    """Lift the room onto an access floor and build the plenum under it.

    Everything already placed moves up by the floor's depth, so the room is
    the room it always was and the building is taller. Then four things are
    added under it and one is replaced (ADR-076):

    * the DECK -- the finished floor itself, a wall across the whole footprint
      at the new zero, the gallery included, because the units stand on it;
    * the OPENING in each dividing wall, from the slab to the deck and the
      full width of the hall. It is the same hole as the one above the false
      ceiling and it is closed the same way, with the same woven mesh: the
      plant pays for it once going up and once coming down;
    * the PLATES, in the cold aisle floor in front of the cabinets;
    * the UNITS' two faces, which is what the fan wall becomes here.

    A downflow unit returns through its top face and supplies through its
    bottom one, so its two faces are horizontal and a storey apart rather than
    one vertical plane. The volume the unit's own body occupies between them
    is left open to the gallery rather than walled: the body is drawn and not
    meshed, as the fan wall's depth already is (ADR-046), and sealing it would
    leave a region with no path to anywhere and no pressure reference in it.
    """
    lift = floor["height"]
    up = lambda z: z + lift  # noqa: E731

    model.domain = Box(model.domain.lo, (model.domain.hi[0], model.domain.hi[1],
                                         up(model.domain.hi[2])))
    model.hall = Box((model.hall.lo[0], model.hall.lo[1], up(model.hall.lo[2])),
                     (model.hall.hi[0], model.hall.hi[1], up(model.hall.hi[2])))
    model.galleries = [
        Box((b.lo[0], b.lo[1], up(b.lo[2])), (b.hi[0], b.hi[1], up(b.hi[2])))
        for b in model.galleries
    ]
    model.ceiling_z = up(model.ceiling_z)
    for row in model.rows:
        row.racks = [
            dataclasses.replace(r, box=Box(
                (r.box.lo[0], r.box.lo[1], up(r.box.lo[2])),
                (r.box.hi[0], r.box.hi[1], up(r.box.hi[2]))))
            for r in row.racks
        ]
    model.racks = [rack for row in model.rows for rack in row.racks]

    def lifted(panel: Panel) -> Panel:
        if panel.axis == 2:
            return dataclasses.replace(panel, position=up(panel.position))
        # The z range of a panel normal to x or y is its second in-plane axis.
        (a0, a1), (b0, b1) = panel.extent
        return dataclasses.replace(panel, extent=((a0, a1), (up(b0), up(b1))))

    units = [p for p in model.panels if p.kind == "fan"]
    others = [lifted(p) for p in model.panels if p.kind != "fan"]

    deck_k = None
    tile = component_for(spec, "floor_tile")
    mesh = component_for(spec, "gallery_mesh")
    added: list[Panel] = [
        Panel("floor_deck", "wall", axis=2, position=lift,
              extent=((model.domain.lo[0], model.domain.hi[0]),
                      (model.domain.lo[1], model.domain.hi[1])))
    ]
    dividers = [model.hall.lo[0], model.hall.hi[0]][: len(model.galleries)]
    for i, x in enumerate(dividers):
        added.append(Panel(
            "floor_opening" if i == 0 else f"floor_opening{i + 1}",
            "opening", axis=0, position=x,
            extent=((0.0, model.domain.hi[1]), (0.0, lift)),
            resistance=mesh.k if mesh else None,
            sign=1 if i == 0 else -1,
        ))
    added += _floor_tiles(model, spec, floor, lift, tile, cell)
    added += _downflow_units(model, units, spec, lift)
    model.panels = others + added
    model.floor_height = lift
    model.floor_tile_k = tile.k if tile else None
    _ = deck_k


def _floor_tiles(model: "Model", spec: dict, floor: dict, lift: float,
                 tile, cell) -> list[Panel]:
    """Perforated plates in the cold aisle floor, in front of the cabinets.

    A plate is the floor grid's own tile -- 600 mm square as the component
    states it -- and the count is how many rows of them stand between the
    cabinet's face and the aisle. Two fills a 1.2 m aisle and three an 1.8 m
    one, which is why the count is what a case sets rather than an area: the
    engineer thinks in plates because the floor is built in plates.

    A plate is as wide as the CABINET in front of it, not as wide as the grid:
    a 800 mm cabinet gets 800 mm of open floor. The depth is the component's,
    because the plates line up with the floor and not with the racks.
    """
    depth = float(tile.size[1]) if tile and getattr(tile, "size", None) else 0.6
    standard = floor["tiles_per_rack"]
    stated = rack_tiles(spec)
    out: list[Panel] = []
    for row in model.rows:
        # The plates lie in the cold aisle, which is the side the cabinets
        # face. `front_sign` says which way that is.
        face = row.front_y
        step = -depth if row.front_sign > 0 else depth
        for rack in row.racks:
            count = stated.get(rack.id, standard)
            if count <= 0:
                continue
            for n in range(count):
                edge = face + step * n
                lo, hi = sorted((edge, edge + step))
                out.append(Panel(
                    f"tile_{rack.id}_{n + 1}".replace("-", "_").replace(".", "_"),
                    "opening", axis=2, position=lift,
                    extent=((rack.box.lo[0], rack.box.hi[0]), (lo, hi)),
                    resistance=tile.k if tile else None,
                ))
    return out


def _plates_must_fit(model: "Model") -> None:
    """Two rows facing one aisle cannot each floor the whole of it.

    A plate is laid in front of a cabinet, outward from its face. Two rows
    face the SAME cold aisle from opposite sides, so an aisle of width W holds
    W/depth rows of plate between them -- not that many for each of them.

    Ask for more and the same floor is claimed twice. Nothing in the geometry
    says so: the plates are built, the summary counts them, the drawing shows
    them, and the failure arrives four steps later out of OpenFOAM, as
    `createBaffles exited 1 ... Face 39400 already in faceZone 55` -- a mesh
    face index and a zone number, about a hall the reader has just spent an
    hour describing. Measured on a 5 MW hall with 1,2 m cold aisles and two
    plates a rack: 90 of its 240 plates were laid twice (ADR-095).

    So it is refused here, where the two rows and the width of the aisle they
    share are still in hand.
    """
    plates = [p for p in model.panels if p.name.startswith("tile_")]
    if not plates:
        return
    where: dict[tuple, list[Panel]] = {}
    for plate in plates:
        where.setdefault((plate.extent[0], plate.extent[1]), []).append(plate)
    clashes = [group for group in where.values() if len(group) > 1]
    if not clashes:
        return

    def row_of(plate: Panel):
        stem = plate.name[len("tile_"):].rsplit("_", 1)[0]
        return next((row for row in model.rows
                     if any(stem == rack.id.replace("-", "_").replace(".", "_")
                            for rack in row.racks)), None)

    first = clashes[0]
    pair = [row_of(plate) for plate in first[:2]]
    names = " and ".join(sorted(row.id for row in pair if row)) or "two rows"
    depth = min(hi - lo for _x, (lo, hi) in
                ((p.extent[0], p.extent[1]) for p in plates))
    asked = max(len(group) for group in clashes)
    aisle = (abs(pair[0].front_y - pair[1].front_y)
             if all(pair) else asked * depth)
    raise ValueError(
        f"floor.tiles_per_rack: rows {names} face the same {num(aisle)} m "
        f"cold aisle from opposite sides, and each asks for {asked} plates of "
        f"{num(depth)} m in front of every cabinet -- {len(clashes)} plates "
        f"would be laid twice over the same floor. That aisle holds "
        f"{num(aisle / depth, 0)} rows of plate between the two cabinet "
        f"faces, so set floor.tiles_per_rack to "
        f"{max(1, int(aisle / 2 / depth + 1e-9))}, or widen aisles.cold to "
        f"{num(2 * asked * depth)} m"
    )

def _downflow_units(model: "Model", units: list[Panel], spec: dict,
                    lift: float) -> list[Panel]:
    """Each unit's two horizontal faces, in the place its fan wall stood.

    The rectangle is the same one the fan wall drew -- same wall, same width,
    same order along it -- so a layout swaps between the two arrangements
    without moving a machine. What changes is where the air crosses: down
    through the deck into the plenum, and in again through the top of the
    unit, a storey above.
    """
    height = float(spec["fanwall"]["height"])
    depth = float(spec["fanwall"].get("depth", 1.2))
    out: list[Panel] = []
    for unit in units:
        # A fan wall is normal to x, so its in-plane ranges are (y, z): the
        # first is its WIDTH along the dividing wall. A downflow unit's faces
        # are normal to z, whose in-plane ranges are (x, y) -- so the width
        # stays in y and the depth becomes the x range, reaching back into the
        # gallery from the wall the fan wall stood in.
        (y0, y1), _ = unit.extent
        x0, x1 = sorted((unit.position, unit.position - depth * unit.sign))
        # `sign` says which patch createBaffles hands the owner cell. For a
        # z-normal face the owner is the cell BELOW: under the supply face
        # that is the plenum, the side the air arrives on, so the supply is
        # the master and the sign is negative (ADR-076).
        out.append(Panel(
            unit.name, "fan", axis=2, position=lift,
            extent=((x0, x1), (y0, y1)), sign=-1,
            return_z=lift + height,
        ))
    return out


CAGE_CONSTRUCTIONS = ("mesh", "drywall")

#: The four walls of a cage, and where each one stands. Rows run along x and
#: PODs stack along y, so `near`/`far` close the ROW ENDS and `left`/`right`
#: run along the rows.
#:
#: A case states them only to overrule the default, which is to build every
#: side the hall wall does not already close. That default is what makes a
#: cage in the CORNER of a hall work without saying anything: two of its
#: sides are the room, and a wall on a wall is not a cage (ADR-098).
CAGE_SIDES = {
    "near": "the row end nearest the first gallery, normal to x",
    "far": "the far row end, normal to x",
    "left": "along the rows at low y",
    "right": "along the rows at high y",
}


def _cage_boundaries(spec: dict, pods: int) -> list[int]:
    """The pod boundaries a cage wall stands in, as indices into the chain.

    Boundary k is the cold aisle between pod k+1 and pod k+2. A cage over pods
    2 and 3 of seven divides the hall twice -- before pod 2 and after pod 3 --
    and both aisles carry a wall, so both are the ones `cage.aisle` widens.

    Empty unless the case states `cage.aisle`: without it every boundary is
    `aisles.cold` and the hall is the hall it always was.
    """
    cage = spec.get("cage") or {}
    if not cage.get("enabled") or cage.get("aisle") is None:
        return []
    inside = cage.get("pods") or list(range(1, pods + 1))
    try:
        first, last = min(int(p) for p in inside), max(int(p) for p in inside)
    except (TypeError, ValueError):
        return []  # cage_for refuses it by name; do not fail here as well
    sides = cage.get("sides")
    out = []
    # The boundary BELOW the first enclosed pod, and ABOVE the last. Either is
    # a wall only where there is a pod on the other side of it: at the end of
    # the hall the room closes the cage instead.
    if first > 1 and (sides is None or "left" in sides):
        out.append(first - 2)
    if last < pods and (sides is None or "right" in sides):
        out.append(last - 1)
    return [k for k in out if 0 <= k < pods - 1]


def cage_for(spec: dict) -> dict | None:
    """The customer cage this case has, or None where it has none (ADR-096).

    A cage is a security boundary inside the data hall, around one customer's
    rows. It is built one of two ways and the two are different rooms:

    * ``drywall`` -- a solid partition. The air cannot cross it at all, so
      everything inside is fed and returned through whatever openings the
      cage has, and the hall outside is a different volume.
    * ``mesh`` -- woven wire, 13 mm here. The air crosses it freely except
      for the pressure it costs, and it costs that TWICE: once going in on
      the cold side and once coming out on the hot one.

    Which one is not a detail of the drawing. A drywall cage with no designed
    opening is a room the plant does not reach; a mesh cage is a room the
    plant reaches and pays about 2 x 1/2 K rho v^2 to reach. Modelling one as
    the other is the whole answer, which is why it is a field and not an
    assumption.
    """
    raw = spec.get("cage") or {}
    if not raw.get("enabled"):
        return None
    how = str(raw.get("construction", "mesh")).strip().lower()
    if how not in CAGE_CONSTRUCTIONS:
        raise ValueError(
            f"cage.construction: {how!r} is not one of "
            + ", ".join(CAGE_CONSTRUCTIONS)
        )
    clearance = float(raw.get("clearance", 1.2))
    if not 0.1 <= clearance <= 10.0:
        raise ValueError(
            f"cage.clearance: {num(clearance)} m is outside 0.1-10 m. It is "
            "the gap from the outermost cabinet faces to the cage wall"
        )
    height = raw.get("height")
    pods = raw.get("pods")
    if pods is not None:
        try:
            pods = sorted({int(p) for p in pods})
        except (TypeError, ValueError):
            raise ValueError(
                f"cage.pods: {pods!r} is not a list of pod numbers. A cage "
                "encloses whole PODs, counted from 1 in the order they stand "
                "along the hall"
            ) from None
        if not pods:
            raise ValueError("cage.pods: an empty list encloses nothing")
        if pods != list(range(pods[0], pods[-1] + 1)):
            raise ValueError(
                f"cage.pods: {pods} is not contiguous. A cage is one "
                "rectangle, so the pods inside it have to be neighbours"
            )
    aisle = raw.get("aisle")
    if aisle is not None:
        aisle = float(aisle)
        if not 0.6 <= aisle <= 10.0:
            raise ValueError(
                f"cage.aisle: {num(aisle)} m is outside 0.6-10 m. It is the "
                "cold aisle the cage wall stands in, which both the row "
                "inside the cage and the row outside it breathe from"
            )
    sides = raw.get("sides")
    if sides is not None:
        sides = [str(s).strip().lower() for s in sides]
        unknown = sorted(set(sides) - set(CAGE_SIDES))
        if unknown:
            raise ValueError(
                f"cage.sides: {', '.join(unknown)} is not a side. They are "
                + ", ".join(f"{name} ({where})" for name, where in CAGE_SIDES.items())
            )
    return {
        "construction": how,
        "clearance": clearance,
        "height": None if height is None else float(height),
        "roof": bool(raw.get("roof", False)),
        "pods": pods,
        "sides": sides,
        "aisle": aisle,
    }


def cage_k(spec: dict) -> float | None:
    """What the air pays to cross a mesh cage, per crossing.

    None for drywall, which is a wall: there is nothing to cross.
    """
    if (cage_for(spec) or {}).get("construction") != "mesh":
        return None
    stated = (spec.get("cage") or {}).get("loss_coefficient")
    if stated is not None:
        return float(stated)
    mesh = component_for(spec, "cage")
    return mesh.k if mesh else None


def _cage_panels(model: "Model", cage: dict, spec: dict,
                 notes: list[str]) -> list[Panel]:
    """The walls of the cage, and its roof where it has one.

    The rectangle is the cabinets it encloses, grown by the clearance: a cage
    is built round the rows and the drawing dimensions it from them. Which
    cabinets is `cage.pods` -- a contiguous run of PODs, or every rack where
    the case names none. The floor is the room's floor (the deck, on a
    raised-floor hall) and the top is `cage.height`, or the false ceiling.

    WHICH SIDES ARE BUILT is the part that makes a cage placeable. A cage in
    the middle of a hall has four walls. A cage in a CORNER has two, because
    the room already closes the other two, and a wall built on the hall wall
    is not a boundary -- `topoSet` would take the boundary faces the room is
    made of and `createBaffles` would split the room's own wall in two. So a
    side whose plane lands on the hall wall is left out by default and said in
    a warning, and a case that NAMES it is refused (ADR-098).
    """
    if not model.racks:
        raise ValueError("cage.enabled: this case has no racks to enclose")
    inside = _cage_racks(model, cage)
    gap = cage["clearance"]
    x0 = min(r.box.lo[0] for r in inside) - gap
    x1 = max(r.box.hi[0] for r in inside) + gap
    y0 = min(r.box.lo[1] for r in inside) - gap
    y1 = max(r.box.hi[1] for r in inside) + gap
    z0 = model.hall.lo[2]
    z1 = z0 + cage["height"] if cage["height"] else model.ceiling_z

    room = ((model.hall.lo[0], model.hall.hi[0]), (model.hall.lo[1], model.hall.hi[1]))
    edge = {"near": (0, 0, x0), "far": (0, 1, x1),
            "left": (1, 0, y0), "right": (1, 1, y1)}

    # WHICH SIDES THE ROOM CLOSES. A clearance that reaches or passes the hall
    # wall means the room is the boundary there -- which is what a cage in a
    # corner IS -- so the side is not built and the rectangle is CLIPPED to the
    # room. Clipping is the half that matters: without it the wall that IS the
    # cage stops short of the room at both ends, and the drawing shows a
    # partition with a gap at each end that nothing in the case asked for
    # (ADR-098).
    def at_the_room(axis: int, side: int, at: float) -> bool:
        lo, hi = room[axis]
        return at <= lo + 1e-6 if side == 0 else at >= hi - 1e-6

    closed = {name for name, (axis, side, at) in edge.items()
              if at_the_room(axis, side, at)}
    asked = cage["sides"]
    if asked is None:
        wanted = [name for name in edge if name not in closed]
        for name in sorted(closed):
            notes.append(
                f"cage {name} side: the cage reaches the hall wall there, so "
                f"the room already closes it and no cage wall is built. Say "
                f"`cage.sides` to choose the walls yourself."
            )
    else:
        overlap = sorted(set(asked) & closed)
        if overlap:
            raise ValueError(
                f"cage.sides: {', '.join(overlap)} would stand on the hall "
                f"wall, which is not a boundary inside the room -- the room "
                f"already closes it. Leave it out of `cage.sides`"
            )
        wanted = [name for name in edge if name in asked]
        closed |= set(edge) - set(asked)
    if not wanted and not cage["roof"]:
        raise ValueError(
            "cage.sides: no wall is left to build. A cage that the room "
            "closes on every side is the room"
        )
    # A side the room closes runs TO the room, so every wall that is built
    # spans the whole of it.
    if "near" in closed:
        x0 = room[0][0]
    if "far" in closed:
        x1 = room[0][1]
    if "left" in closed:
        y0 = room[1][0]
    if "right" in closed:
        y1 = room[1][1]
    plan = {
        "near": (0, x0, (y0, y1)),
        "far": (0, x1, (y0, y1)),
        "left": (1, y0, (x0, x1)),
        "right": (1, y1, (x0, x1)),
    }
    # A CAGE WALL MAY NOT CUT A CABINET. With `cage.pods` the wall stands in
    # the aisle beside the pods it encloses, and too big a clearance walks it
    # into the next pod's rows -- which meshes, and models a partition through
    # the middle of somebody's cabinets.
    for name in wanted:
        axis, at, _span = plan[name]
        for rack in model.racks:
            if rack.box.lo[axis] + 1e-6 < at < rack.box.hi[axis] - 1e-6:
                room_for = min(abs(at - rack.box.lo[axis]),
                               abs(rack.box.hi[axis] - at))
                raise ValueError(
                    f"cage.clearance: {num(gap)} m puts the {name} wall at "
                    f"{num(at)} m, which is inside {rack.id}. Lose at least "
                    f"{num(room_for)} m of clearance, or put that rack in the "
                    f"cage with `cage.pods`"
                )

    # WHAT THE WALL LEAVES THE ROWS EITHER SIDE OF IT. A cage boundary stands
    # in a cold aisle that the row inside the cage and the row outside it BOTH
    # breathe from, so it halves that aisle -- and 0,60 m of cold aisle in
    # front of a row of cabinets is a different room from 1,20 m. Nothing said
    # so: the case built, meshed, and the first thing to notice was a drawing
    # (ADR-099). Said here, with both gaps and the key that widens the aisle.
    for name in wanted:
        axis, at, _span = plan[name]
        if axis != 1:
            continue
        near = _rows_beside(model, at)
        if len(near) < 2:
            continue
        (below, gap_below), (above, gap_above) = near
        # ONLY WHEN IT COSTS A ROW SOMETHING. A wall in an aisle wide enough
        # to hold it leaves both rows the cold aisle the hall was drawn with,
        # and there is nothing to report; saying it anyway would be one more
        # paragraph on every run, which is the fault this repository has
        # already had to undo once (ADR-098).
        drawn = float((spec.get("aisles") or {}).get("cold", 0.0))
        starved = [(row, gap) for row, gap in
                   ((below, gap_below), (above, gap_above))
                   if gap < drawn - 1e-6]
        if not starved:
            continue
        notes.append(
            f"cage {name} wall: it stands in the {num(gap_below + gap_above)} m "
            f"cold aisle between rows {below} and {above}, leaving "
            f"{num(gap_below)} m to {below} and {num(gap_above)} m to {above} "
            f"where `aisles.cold` draws {num(drawn)} m. "
            + ", ".join(f"{row} breathes through {num(gap)} m"
                        for row, gap in starved)
            + ". Widen that aisle with `cage.aisle`, or move the wall with "
            "`cage.clearance`."
        )

    kind = "wall" if cage["construction"] == "drywall" else "opening"
    k = cage_k(spec) if kind == "opening" else None
    panels = [
        Panel(f"cage_{name}", kind, axis=plan[name][0], position=plan[name][1],
              extent=(plan[name][2], (z0, z1)), resistance=k)
        for name in wanted
    ]
    if cage["roof"]:
        panels.append(Panel("cage_roof", kind, axis=2, position=z1,
                            extent=((x0, x1), (y0, y1)), resistance=k))
    if z1 > model.ceiling_z + 1e-6:
        raise ValueError(
            f"cage.height: {num(z1 - z0)} m reaches {num(z1)} m, above the "
            f"{num(model.ceiling_z)} m false ceiling. A cage stops at the "
            "ceiling; above it is the return plenum"
        )
    # A DRYWALL CAGE NEEDS A WAY IN. Solid walls to the ceiling, or solid
    # walls and a roof, leave a volume the supply cannot reach -- while the
    # ceiling grilles over its hot aisles still let air OUT, into the plenum.
    # A room with an exit and no entry has no steady solution, and the solver
    # does not say so politely: four ranks died on a floating point exception
    # about nine minutes in, having meshed and decomposed perfectly. Refused
    # here instead, with the two ways a real one is built (ADR-096).
    #
    # A cage the room closes on some sides is still closed: what matters is
    # whether the air has a path, and a hall wall blocks it as well as a
    # drywall one does.
    if cage["construction"] == "drywall" and (
            cage["roof"] or z1 >= model.ceiling_z - 1e-6):
        raise ValueError(
            "a drywall cage closed at the top has no way in: the supply is "
            "outside it and the ceiling grilles over its own hot aisles let "
            "air out, so nothing can reach the racks. Either give it "
            "`cage.height` below the "
            f"{num(model.ceiling_z)} m ceiling with `cage.roof` off, so the "
            "air passes over the top, or model it as `cage.construction: "
            "mesh`. A drywall cage with a door or a duct through it is real "
            "and is not modelled yet"
        )
    return panels


def _rows_beside(model: "Model", at: float) -> list:
    """The row below a y plane and the row above it, with the gap to each.

    Only the rows that FACE the plane count: a row's back is its hot aisle
    side and a wall there costs it nothing, while a wall in front of it is
    the aisle it draws through.
    """
    below = above = None
    for row in model.rows:
        lo = min(r.box.lo[1] for r in row.racks)
        hi = max(r.box.hi[1] for r in row.racks)
        face = hi if row.front_sign < 0 else lo
        if hi <= at + 1e-6 and face >= hi - 1e-6:
            if below is None or face > below[1]:
                below = (row.id, face)
        if lo >= at - 1e-6 and face <= lo + 1e-6:
            if above is None or face < above[1]:
                above = (row.id, face)
    if below is None or above is None:
        return []
    return [(below[0], at - below[1]), (above[0], above[1] - at)]


def _cage_racks(model: "Model", cage: dict) -> list:
    """The cabinets the cage encloses.

    Every rack unless the case names PODs. A pod is a pair of rows facing one
    contained hot aisle, counted from 1 along the hall, so pod 2 is rows F3
    and F4 -- the same numbering the drawing reads in.
    """
    pods = cage["pods"]
    if not pods:
        return list(model.racks)
    rows = {row.id: row for row in model.rows}
    wanted, missing = [], []
    for pod in pods:
        for offset in (1, 2):
            found = [row for row in model.rows
                     if row.id.split("B")[0] == f"F{2 * (pod - 1) + offset}"]
            if not found:
                missing.append(f"F{2 * (pod - 1) + offset}")
            wanted += found
    if missing:
        raise ValueError(
            f"cage.pods: {pods} names row(s) {', '.join(sorted(set(missing)))}, "
            f"which this hall has not got -- it has "
            f"{len(rows)} rows, so pods 1 to {len(rows) // 2}"
        )
    racks = [rack for row in wanted for rack in row.racks]
    if not racks:
        raise ValueError(f"cage.pods: {pods} encloses no cabinets")
    return racks

def raised_floor_for(spec: dict) -> dict | None:
    """The raised floor, where a case has one (ADR-076).

    An access floor turns the space under the room into a supply plenum. The
    room above it is unchanged -- same aisles, same rack heights, same false
    ceiling and the same return plenum over it -- and the BUILDING grows by
    the floor's depth, because the plenum is added under the finished floor
    rather than taken out of the room.

    It replaces the fan wall with a downflow unit and it cannot be combined
    with a supply plenum at the gallery wall: that plenum is the alternative
    way of getting air from the same units into the same aisles, and a case
    asking for both is a case that has not chosen (ADR-058, ADR-060).
    """
    raw = spec.get("floor") or {}
    if not raw.get("enabled"):
        return None
    height = float(raw.get("height", 1.0))
    if not 0.2 <= height <= 3.0:
        raise ValueError(
            f"floor.height: {height:g} m is outside 0.2-3.0 m"
        )
    tiles = int(raw.get("tiles_per_rack", 2))
    if not 0 <= tiles <= 10:
        raise ValueError(f"floor.tiles_per_rack: {tiles} is outside 0-10")
    if (spec.get("plenum") or {}).get("enabled") or \
            (spec.get("plenum") or {}).get("as_mesh"):
        raise ValueError(
            "a raised floor and a supply plenum are two ways of getting the "
            "same air from the same units into the same aisles. Choose one: "
            "`floor.enabled` with downflow units, or `plenum` with a fan wall"
        )
    return {"height": height, "tiles_per_rack": tiles}


def rack_tiles(spec: dict) -> dict[str, int]:
    """How many plates stand in front of each position, where it says its own.

    The standard is `floor.tiles_per_rack`; this is the list of positions that
    disagree, exactly as the load has always worked (ADR-054). Zero is a
    cabinet fed by nothing but what reaches it sideways, which is a real thing
    to model and the reason the field exists.
    """
    raw = (spec.get("racks") or {}).get("tiles") or {}
    out = {}
    for rack_id, value in raw.items():
        if value is None:
            continue
        count = int(value)
        if not 0 <= count <= 10:
            raise ValueError(
                f"racks.tiles[{rack_id}]: {count} is outside 0-10"
            )
        out[str(rack_id)] = count
    return out


def rack_type_defaults(spec: dict) -> dict:
    """What `racks.type` contributes: the standard cabinet, from the catalogue.

    Anything the case states still wins, here as everywhere (ADR-036). What
    the type gives is the numbers nobody should be retyping -- a cabinet's
    width, depth and height are the product's, the same in every project that
    buys it (ADR-075).
    """
    type_id = (spec.get("racks") or {}).get("type")
    if not type_id:
        return {}
    from aicfd import racklib

    rack = racklib.resolve(str(type_id))
    return {"size": list(rack.size), "load_kw": rack.load_kw}


def resolved_size(spec: dict) -> list[float]:
    """The standard cabinet: what the case says, else what its type says."""
    stated = (spec.get("racks") or {}).get("size")
    if stated is not None:
        return [float(v) for v in stated]
    defaults = rack_type_defaults(spec)
    if "size" in defaults:
        return [float(v) for v in defaults["size"]]
    raise ValueError(
        "racks.size is missing and racks.type names no cabinet to take it from"
    )


def rack_positions(row_id: str, plan: list[dict], size, load_kw: float,
                   rack_ids, loads: dict, widths: dict, blanks: dict,
                   cell_x: float | None = None,
                   warnings: list[str] | None = None) -> list[dict]:
    """One row's positions, with the per-position overrides applied.

    The plan is the pattern every row shares; the maps are where a single
    position disagrees with it. Same division as the load has always had: the
    case states the standard once and then only what differs (ADR-054).

    EACH WIDTH IS SNAPPED HERE, not each box edge afterwards. The general
    snapper moves every face to the nearest grid line, which on a row of one
    width is exact and on a row of several is not: two identical 0.8 m
    cabinets on a 0.6 m cell came out 0.60 m and 1.20 m, because their edges
    landed on opposite sides of the same line and the error accumulated along
    the row. Rounding the WIDTH instead makes every cabinet of a width the
    same width, and lays the row out on grid lines by construction (ADR-074).
    """
    out = []
    for i, entry in enumerate(plan):
        rack_id = rack_ids(i)
        blank = blanks.get(rack_id, bool(entry.get("blank", False)))
        # A position may name a cabinet type instead of typing its width. What
        # the position itself states still wins over the type (ADR-075).
        from_type: dict = {}
        if entry.get("type"):
            from aicfd import racklib

            rack = racklib.resolve(str(entry["type"]))
            from_type = {"width": rack.size[0]}
            # A ROW HAS ONE DEPTH AND ONE HEIGHT. Only the width is a
            # position's own, because the row is a single band across the hall
            # and mixing depths inside it is geometry this model does not
            # build. A type named at a position therefore contributes its
            # width and nothing else, and where its other two differ from the
            # row's that is said rather than silently dropped (ADR-075).
            if warnings is not None:
                for i_axis, axis_name in ((1, "deep"), (2, "tall")):
                    theirs, ours = rack.size[i_axis], float(size[i_axis])
                    if theirs is None or abs(theirs - ours) < 1e-9:
                        continue
                    note = (f"{rack.id} is {theirs:.3f} m {axis_name} and this "
                            f"row is {ours:.3f} m; a row has one depth and one "
                            f"height, so only its width is taken. Name it as "
                            f"`racks.type` to make the row itself its size.")
                    if note not in warnings:
                        warnings.append(note)
            # A LIQUID CABINET'S RATED DUTY IS NOT ITS AIR LOAD. The Type-E
            # rack is 225 kW and leaves ~96% of it in the coolant, so what
            # this room has to remove is the other 4% -- about 9 kW, not 225.
            # Where the type states that fraction the air share follows from
            # it, which is the manufacturer's own number rather than anybody's
            # assumption. Where it does not, nothing is guessed (ADR-075).
            if rack.load_kw is None:
                pass
            elif rack.cooling == "air":
                from_type["load_kw"] = rack.load_kw
            elif rack.liquid_fraction is not None:
                from_type["load_kw"] = rack.load_kw * (1.0 - rack.liquid_fraction)
            elif (rack_id not in loads and entry.get("load_kw") is None
                    and not blank):
                raise ValueError(
                    f"{rack_id} is a {rack.cooling}-cooled {rack.id!r} whose "
                    f"type does not say what share of its {rack.load_kw:g} kW "
                    f"leaves in the coolant. Give the position the air load it "
                    f"really puts into the hall, or state `liquid_fraction` on "
                    f"the type"
                )
        asked = float(widths.get(
            rack_id, entry.get("width", from_type.get("width", size[0]))))
        if asked <= 0:
            raise ValueError(f"{rack_id}: a position {asked:g} m wide has no width")
        width = asked
        if cell_x:
            width = max(1, round(asked / cell_x)) * cell_x
            if warnings is not None and abs(width - asked) > 1e-9:
                # Said once per WIDTH, not once per cabinet: a hall of 400
                # positions built from one typical row would otherwise repeat
                # the same sentence 40 times and bury everything else.
                note = (f"cabinets {asked:.3f} m wide fall between {cell_x:g} m "
                        f"grid lines; the mesh uses {width:.2f} m "
                        f"({(width - asked) * 1000:+.0f} mm). "
                        f"{_cell_note(asked, cell_x)}")
                if note not in warnings:
                    warnings.append(note)
        out.append({
            "id": rack_id,
            "blank": blank,
            "width": width,
            "load_kw": 0.0 if blank else float(loads.get(
                rack_id,
                entry.get("load_kw", from_type.get("load_kw", load_kw)))),
            "type": entry.get("type"),
        })
    return out


def _make_row(row_id: str, band: tuple[float, float], sign: int, x0: float,
              positions: list[dict], size: tuple[float, float, float],
              load_kw: float, rack_spec: dict | None = None,
              cell_x: float | None = None
              ) -> tuple[Row, list[Panel], float]:
    """One row, as its positions describe it: (row, blanking panels, end x).

    THE ROW STARTS ON A CELL FACE. Every width is already a whole number of
    cells (ADR-075), so a row laid out from a snapped origin puts every face on
    a cell face by construction, and `snap_to_mesh` has nothing left to move.

    Start half a cell off and it has plenty to move, one face at a time, and
    the widths come back REDISTRIBUTED. A hall whose first block began at
    12,5 m on a 0,2 m grid -- exactly a half cell, so every rounding was a coin
    toss -- laid its typical row of nine 0,60 m cabinets and six 0,80 m ones
    out as 0,80 / 0,40 / 0,60 x6 / 0,80 x7, ten centimetres longer and with a
    0,40 m cabinet in it that no specification ever asked for. The block on the
    other side of the same hall started at 25,0 m, landed on the grid, and came
    out exactly right. Two identical rows, drawn differently, and the drawing
    was telling the truth about the mesh (ADR-085).

    ``load_kw`` here is the row's STANDARD, which calibrates every cabinet's
    resistance. What a cabinet dissipates is its own; what it resists by is
    the row's, because an unloaded position is blanked and resists like the
    cabinets either side of it (ADR-054).

    A BLANKING PANEL IS NOT A ZERO-LOAD CABINET. The zero-load cabinet is a
    box that still breathes and still resists; the blank is a plate where no
    cabinet stands, and no air crosses it at all. Both are real and they say
    different things, so both exist (ADR-074).
    """
    _dx, _dy, dz = size
    if cell_x:
        x0 = round(x0 / cell_x) * cell_x
    racks, blanks, x = [], [], x0
    for place in positions:
        left, right = x, x + place["width"]
        x = right
        if not place["blank"]:
            racks.append(Rack(
                id=place["id"],
                box=Box((left, band[0], 0.0), (right, band[1], dz)),
                load_kw=place["load_kw"],
                resistance_kw=load_kw,
                airflow_axis=1,
                airflow_sign=sign,
                **(rack_spec or {}),
            ))
            continue
        # The plate faces the cold aisle, where a cabinet's front would be.
        # Only that face is closed: sealing both would leave a pocket with no
        # path to anywhere, and an isolated region is a pressure solve with no
        # reference in it. Open to the hot aisle it is dead air, which is what
        # the space behind a blanking panel is.
        blanks.append(Panel(
            f"blank_{place['id']}".replace(".", "_").replace("-", "_"),
            "wall",
            axis=1,
            position=band[0] if sign > 0 else band[1],
            extent=((left, right), (0.0, dz)),
            of_rack=True,
        ))
    return Row(row_id, band, sign, racks), blanks, x


def rack_loads(spec: dict) -> dict[str, float]:
    """The per-position loads a case states, by rack id.

    Empty for a hall filled to its nominal load everywhere, which is most of
    them. A value outside 0 and the sanity ceiling is refused here rather than
    silently clamped: a typo in a rack id is a position that never gets the
    load the engineer meant to give it, and a silent default hides that.
    """
    raw = (spec.get("racks") or {}).get("loads") or {}
    out = {}
    for rack_id, value in raw.items():
        if value is None:
            continue
        load = float(value)
        if not 0 <= load <= 200:
            raise ValueError(
                f"racks.loads[{rack_id}]: {load:g} kW is outside 0-200 kW"
            )
        out[str(rack_id)] = load
    return out


def _cell_note(asked: float, cell_x: float) -> str:
    """What x cell would carry this width exactly, where one reasonably would.

    A 25% loss on a cabinet's width is not a rounding a reader should have to
    work out the cure for. Halving the cell until the width divides into it is
    the cure, and saying so costs one sentence (ADR-074).
    """
    cell = cell_x
    for _ in range(4):
        cell /= 2.0
        if abs(round(asked / cell) * cell - asked) < 1e-9:
            return (f"An x cell of {cell:g} m would carry it exactly, at "
                    f"{(cell_x / cell) ** 1:.0f}x the cells across the row.")
    return "No halving of the x cell inside four steps carries it exactly."


def rack_widths(spec: dict) -> dict[str, float]:
    """The per-position widths a case states, by rack id (ADR-074)."""
    raw = (spec.get("racks") or {}).get("widths") or {}
    out = {}
    for rack_id, value in raw.items():
        if value is None:
            continue
        width = float(value)
        if not 0.1 <= width <= 3.0:
            raise ValueError(
                f"racks.widths[{rack_id}]: {width:g} m is outside 0.1-3.0 m"
            )
        out[str(rack_id)] = width
    return out


def rack_blanks(spec: dict) -> dict[str, bool]:
    """Which positions are blanking panels rather than cabinets (ADR-074).

    A list of ids, or a mapping to true/false where a case needs to say that
    a position the typical row blanks is a cabinet here after all.
    """
    raw = (spec.get("racks") or {}).get("blanks")
    if not raw:
        return {}
    if isinstance(raw, dict):
        return {str(k): bool(v) for k, v in raw.items() if v is not None}
    return {str(rack_id): True for rack_id in raw}


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
            of_rack=True,
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
                of_rack=True,
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


def plenum_for(spec: dict, rack_height: float) -> dict | None:
    """The supply plenum this case asks for, or None where it asks for none.

    Without a plenum the unit blows through the dividing wall straight into
    the cold aisle it faces, and where it aims is where the air goes. A
    plenum makes that wall a DOUBLE wall: the leaf the units are mounted in
    is the one that was always there, a second leaf stands a short way into
    the hall, and the cavity between them is pressurised. The room is then
    fed by the grilles in the inner leaf rather than by the units, which is
    what makes the supply even along the wall and aimed at the cold aisles
    rather than at whatever each unit happens to face.
    """
    plenum = spec.get("plenum")
    # `as_mesh` is an arrangement in its own right, not a modifier on
    # `enabled`: the page offers the two as one-or-the-other, so ticking the
    # mesh leaves `enabled` off and the case would otherwise come back with
    # no plenum at all (ADR-060).
    if not plenum or not (plenum.get("enabled", False) or plenum.get("as_mesh", False)):
        return None
    grille = plenum.get("grille") or {}
    closed = plenum.get("closed") or []
    if isinstance(closed, str):
        closed = [closed]
    return {
        # The same wall treatment realised the other way: the 13 x 13 mm mesh
        # across the opening the units blow through, and no plenum at all. A
        # hall already built cannot grow, and this is what goes in instead --
        # it keeps people out of the room and costs the fan its loss
        # coefficient, and it distributes nothing (ADR-060).
        "as_mesh": bool(plenum.get("as_mesh", False)),
        "depth": float(plenum.get("depth", PLENUM_DEPTH)),
        "width": float(grille.get("width", PLENUM_GRILLE_WIDTH)),
        # As tall as a rack unless the case says otherwise: the grille feeds
        # the cold aisle over the height the racks breathe from, and air let
        # in above them is air the racks never see.
        "height": float(grille.get("height", rack_height)),
        "closed": {str(name) for name in closed},
    }


def _plenum_panels(plenum: dict, dividers: list[float],
                   cold_aisles: list[tuple[float, float]],
                   ceiling: float, width: float, k: float | None,
                   mesh_k: float | None = None):
    """The second leaf of the hall wall, and the grilles that let it out.

    The wall between the hall and the mechanical gallery becomes a double
    wall: the one that is already there, with the units mounted in it exactly
    as before, and a second leaf a short way into the hall. Nothing about the
    fan wall changes -- it is the same opening in the same wall. What changes
    is what is on the other side of it: instead of the cold aisle it faces,
    the unit blows into the cavity between the two leaves, which pressurises,
    and the grilles in the inner leaf decide where that air leaves and in
    which direction (ADR-058).

    The cavity needs no lid. The false ceiling already covers the hall, this
    strip included, so the plenum is closed at that level and the return
    passes over it above the ceiling exactly as it did before.

    The building grows by the plenum, it is not carved out of the room: the
    clearances a case asks for are between the racks and the wall the hall
    actually has, which is now the inner leaf. That is done by the layouts,
    which start the rows past the plenum and lengthen the hall to match.
    """
    depth = plenum["depth"]
    if plenum["height"] > ceiling + 1e-9:
        raise ValueError(
            f"a supply grille {num(plenum['height'])} m tall does not fit "
            f"under a {num(ceiling)} m false ceiling"
        )

    walls: list[Panel] = []
    supplies: list[Panel] = []
    for side, divider in enumerate(dividers):
        sign = 1 if side == 0 else -1
        face = divider + sign * depth
        if plenum["as_mesh"]:
            # The whole leaf is the mesh: one surface, open over its entire
            # face, at the loss coefficient of the 13 x 13 mm mesh. There is
            # no solid wall to pierce and no grille to aim, which is exactly
            # what the arrangement is -- a barrier, not a distributor
            # (ADR-060).
            supplies.append(
                Panel(
                    "supply_mesh" if side == 0 else f"supply_mesh{side + 1}",
                    "opening",
                    axis=0,
                    position=face,
                    extent=((0.0, width), (0.0, ceiling)),
                    resistance=mesh_k,
                    sign=sign,
                )
            )
            continue
        walls.append(
            Panel(
                "plenum_wall" if side == 0 else f"plenum_wall{side + 1}",
                "wall",
                axis=0,
                position=face,
                extent=((0.0, width), (0.0, ceiling)),
                sign=sign,
            )
        )
        for index, aisle in enumerate(cold_aisles):
            mid = (aisle[0] + aisle[1]) / 2
            half = plenum["width"] / 2
            lo, hi = max(0.0, mid - half), min(width, mid + half)
            # Numbered by where it is, not by how many are open: shutting one
            # must not renumber the rest, or a case that names a closed
            # grille shuts a different one the next time it is read.
            name = f"supply{side * len(cold_aisles) + index + 1}"
            if name in plenum["closed"]:
                # A grille shut is a grille that is not there: the wall it
                # sits in closes over it, and the plenum sends its air to the
                # ones still open. That is the whole point of shutting one.
                continue
            supplies.append(
                Panel(
                    name,
                    "opening",
                    axis=0,
                    position=face,
                    extent=((lo, hi), (0.0, plenum["height"])),
                    resistance=k,
                    sign=sign,
                )
            )
    return walls, supplies


def _pod_layout(spec: dict, cell, rack_spec: dict | None = None) -> _Layout:
    gallery_depth = float(spec["gallery"]["depth"])
    hall_length, hall_width, height = (float(v) for v in spec["hall"]["size"])
    ceiling = float(spec["hall"]["ceiling"])
    cold = float(spec["aisles"]["cold"])
    hot = float(spec["aisles"]["hot"])
    size = tuple(resolved_size(spec))
    rack_dz = size[2]
    count = int(spec["racks"]["count"])
    load_kw = float(spec["racks"]["load_kw"])

    # A supply plenum lengthens the building rather than taking the room's
    # clearances: `offset_x` is measured from the wall the hall actually has,
    # which with a plenum is its inner leaf, and the hall grows by the depth
    # so nothing the case asked for moves (ADR-058).
    plenum = plenum_for(spec, rack_dz)
    # Either leaf is a wall as far as clearance is concerned -- a mesh screen
    # is as much an obstruction to a person and a cabinet door as a panel is
    # -- so the building grows by the cavity whichever one closes it, and the
    # clearances the case asked for survive. Only a case with no plenum at
    # all is the shorter room (ADR-058, ADR-060).
    plenum_depth = plenum["depth"] if plenum else 0.0
    start_x = gallery_depth + plenum_depth + float(spec["racks"]["offset_x"])
    hall_length += plenum_depth

    total_x = gallery_depth + hall_length
    domain = Box((0.0, 0.0, 0.0), (total_x, hall_width, height))
    gallery = Box((0.0, 0.0, 0.0), (gallery_depth, hall_width, height))
    hall = Box((gallery_depth, 0.0, 0.0), (total_x, hall_width, height))

    band = (cold, cold + size[1])
    hot_aisle = (cold + size[1], hall_width)
    plan = row_plan(spec, count)
    row_notes: list[str] = []
    places = rack_positions("F1", plan, size, load_kw, lambda i: f"R{i + 1}",
                            rack_loads(spec), rack_widths(spec), rack_blanks(spec),
                            cell_x=cell[0], warnings=row_notes)
    row, blank_panels, end_x = _make_row("F1", band, +1, start_x, places, size,
                                         load_kw, rack_spec, cell_x=cell[0])
    # The row is as long as its parts. With every position the standard width
    # that is count x size[0] again, and with a blank or a wider cabinet in it
    # the old arithmetic would have run the containment past the row's end
    # (ADR-074).
    span = (start_x, end_x)

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

    walls = _row_walls(row, span, rack_dz) + blank_panels
    if spec.get("containment", {}).get("enabled", True):
        walls += _containment(hot_aisle, span, rack_dz, ceiling, (band[1],))

    cold_aisles = [(0.0, cold)]
    supplies: list[Panel] = []
    if plenum:
        plenum_walls, supplies = _plenum_panels(
            plenum, [gallery_depth], cold_aisles, ceiling, hall_width,
            _plenum_k(spec), _supply_mesh_k(spec),
        )
        walls += plenum_walls

    return _Layout(domain, [gallery], hall, [row], cold_aisles, [hot_aisle],
                   fans, grilles + supplies, walls, [span],
                   plenum_depth or None,
                   _supply_mesh_k(spec) if plenum and plenum["as_mesh"] else None,
                   row_notes)


def _hall_layout(spec: dict, cell, rack_spec: dict | None = None) -> _Layout:
    """``pods`` pairs of rows across y, a fan wall in front of every cold aisle.

    Along x: the gallery, the dividing wall, a perimeter aisle, the rows, a
    perimeter aisle. Along y: a perimeter aisle (the first cold aisle), then
    for each POD a row, the contained hot aisle, a row, and a cold aisle to
    the next POD; the last cold aisle is the far perimeter. Fan walls sit in
    the dividing wall centred on their cold aisle as far as the corners and
    their neighbours allow, never overlapping.

    Two options make the hall bigger without changing any of that (ADR-027):

    ``gallery.sides: 2``
        a second mechanical gallery at the far end of the hall, with its own
        dividing wall and its share of the units, facing the other end of
        every cold aisle. The false ceiling still covers the whole hall, so
        **the return plenum stays single** and opens into both galleries.

    ``racks.blocks: 2``
        every row cut into that many blocks along its length, separated by a
        transverse aisle (``aisles.transverse``). Each block is a contained
        volume of its own -- ``pods x blocks`` of them -- and is served by the
        gallery at its end, which is what lets a long hall be driven from two
        sides without one gallery having to push air the whole length.
    """
    gallery_depth = float(spec["gallery"]["depth"])
    sides = int(spec["gallery"].get("sides", 1))
    if sides not in (1, 2):
        raise ValueError(f"gallery.sides must be 1 or 2, got {sides}")
    height = float(spec["hall"]["height"])
    ceiling = float(spec["hall"]["ceiling"])
    cold = float(spec["aisles"]["cold"])
    hot = float(spec["aisles"]["hot"])
    perimeter = float(spec["aisles"].get("perimeter", cold))
    size = tuple(resolved_size(spec))
    rack_dx, rack_dy, rack_dz = size
    per_row = int(spec["racks"]["per_row"])
    load_kw = float(spec["racks"]["load_kw"])
    pods = int(spec["pods"])
    n_blocks = int(spec["racks"].get("blocks", 1))
    if n_blocks < 1:
        raise ValueError(f"racks.blocks must be 1 or more, got {n_blocks}")
    transverse = float(spec["aisles"].get("transverse", cold)) if n_blocks > 1 else 0.0

    # SNAP THE BANDS, THEN ACCUMULATE. The hall's width is a chain -- perimeter,
    # row, hot aisle, row, cold aisle, row, ... -- and laying it out from
    # unsnapped parts leaves every boundary between cell faces, for
    # `snap_to_mesh` to round one at a time. The rounding then drifts down the
    # chain: a 2,2 m hot aisle on a 0,3 m grid came out 2,1 in three pods of a
    # hall and 2,4 in the fourth, so one contained aisle had 14% more chimney
    # than its neighbours and nothing said so. A band that cannot be exact has
    # to be inexact the SAME way everywhere, which means deciding it once, here
    # (ADR-085).
    def on_grid(value: float, axis: int) -> float:
        return max(1, round(value / cell[axis])) * cell[axis]

    cold, hot, perimeter = (on_grid(v, 1) for v in (cold, hot, perimeter))
    rack_dy = on_grid(rack_dy, 1)
    size = (size[0], rack_dy, size[2])
    if transverse:
        transverse = on_grid(transverse, 0)

    # per_row is the count in one block, so a row of two blocks holds twice it.
    # With a typical row stated, the block is as long as that row's parts --
    # a 300 mm blank makes the block 300 mm of blank longer, not 600 (ADR-074).
    plan = row_plan(spec, per_row)
    widths = rack_widths(spec)
    blank_of = rack_blanks(spec)
    row_notes: list[str] = []
    block_length = sum(
        max(1, round(float(e.get("width", rack_dx)) / cell[0])) * cell[0]
        for e in plan
    )
    row_length = n_blocks * block_length + (n_blocks - 1) * transverse
    # A supply plenum at each gallery lengthens the hall by its depth. The
    # perimeter clearance is between the racks and the wall the hall actually
    # has -- with a plenum that is its inner leaf -- so the plenum is added to
    # the building and taken out of nothing (ADR-058).
    plenum = plenum_for(spec, rack_dz)
    # Either leaf is a wall as far as clearance is concerned, so the building
    # grows by the cavity whichever one closes it (ADR-060).
    plenum_depth = plenum["depth"] if plenum else 0.0
    hall_length = perimeter + row_length + perimeter + sides * plenum_depth
    total_x = sides * gallery_depth + hall_length
    # THE AISLE AT EACH POD BOUNDARY. Every one of them is `aisles.cold`,
    # except the one or two a cage wall stands in: a wall down the middle of a
    # cold aisle halves it for the rows on BOTH sides, and a 1,20 m aisle
    # split two ways is 0,60 m of cold aisle in front of a row of cabinets,
    # which is a different room (ADR-099). `cage.aisle` widens those, and
    # those only, so the rest of the hall keeps the aisle it was drawn with.
    boundaries = [cold] * max(0, pods - 1)
    for k in _cage_boundaries(spec, pods):
        boundaries[k] = on_grid(float((spec.get("cage") or {})["aisle"]), 1)
    width = 2 * perimeter + pods * (2 * rack_dy + hot) + sum(boundaries)
    domain = Box((0.0, 0.0, 0.0), (total_x, width, height))
    hall = Box((gallery_depth, 0.0, 0.0), (gallery_depth + hall_length, width, height))
    galleries = [Box((0.0, 0.0, 0.0), (gallery_depth, width, height))]
    if sides == 2:
        galleries.append(Box((hall.hi[0], 0.0, 0.0), (total_x, width, height)))

    x0 = gallery_depth + plenum_depth + perimeter
    spans: list[tuple[float, float]] = []
    x = x0
    for _ in range(n_blocks):
        spans.append((x, x + block_length))
        x += block_length + transverse

    rows: list[Row] = []
    loads = rack_loads(spec)  # per position, where the case states one
    hot_aisles: list[tuple[float, float]] = []
    cold_aisles: list[tuple[float, float]] = [(0.0, perimeter)]
    walls: list[Panel] = []
    grilles: list[Panel] = []
    coverage = float(spec["grilles"].get("coverage", 1.0))

    def strip_of(span: tuple[float, float]) -> tuple[float, float]:
        """The grille strip over one block: centred, ``coverage`` of it long."""
        mid = (span[0] + span[1]) / 2
        half = coverage * (span[1] - span[0]) / 2
        return (mid - half, mid + half)

    y = perimeter
    for k in range(pods):
        a = (y, y + rack_dy)
        hot_aisle = (a[1], a[1] + hot)
        b = (hot_aisle[1], hot_aisle[1] + rack_dy)
        n = 2 * k
        hot_aisles.append(hot_aisle)
        for j, span in enumerate(spans):
            tag = "" if n_blocks == 1 else f"B{j + 1}"
            pair = []
            for offset, band, front in ((1, a, +1), (2, b, -1)):
                row_id = f"F{n + offset}{tag}"
                places = rack_positions(
                    row_id, plan, size, load_kw,
                    lambda i, row_id=row_id: f"{row_id}-{i + 1:02d}",
                    loads, widths, blank_of, cell_x=cell[0], warnings=row_notes,
                )
                built, blanked, end_x = _make_row(row_id, band, front, span[0],
                                                  places, size, load_kw, rack_spec,
                                                  cell_x=cell[0])
                pair.append(built)
                walls += blanked
                span = (span[0], end_x)
            rows += pair
            for row in pair:
                walls += _row_walls(row, span, rack_dz, suffix=f"_{row.id}")
            suffix = f"_{k + 1}" if n_blocks == 1 else f"_{k + 1}b{j + 1}"
            walls += _containment(hot_aisle, span, rack_dz, ceiling,
                                  (a[1], b[0]), suffix=suffix)
            grilles.append(
                Panel(
                    f"grille{len(grilles) + 1}",
                    "opening",
                    axis=2,
                    position=ceiling,
                    extent=(strip_of(span), hot_aisle),
                    resistance=_grille_k(spec),
                )
            )
        y = b[1]
        if k < pods - 1:
            cold_aisles.append((y, y + boundaries[k]))
            y += boundaries[k]
    cold_aisles.append((width - perimeter, width))

    fan = spec["fanwall"]
    fan_width = float(fan["width"])
    count = int(fan.get("count", len(cold_aisles) * sides))
    # The units split evenly between the galleries; an odd count gives the
    # extra unit to the first, as a real installation does.
    share = [count // sides + (1 if i < count % sides else 0) for i in range(sides)]
    fans: list[Panel] = []
    for side, (gallery, n_units) in enumerate(zip(galleries, share)):
        if n_units < 1:
            raise ValueError(f"{count} fan walls cannot be shared between {sides} galleries")
        extents = (
            place_fans(cold_aisles, fan_width, width, cell[1])
            if n_units == len(cold_aisles)
            else distribute_fans(n_units, fan_width, width, cell[1])
        )
        position = gallery.hi[0] if side == 0 else gallery.lo[0]
        fans += [
            Panel(f"fan{len(fans) + i + 1}", "fan", axis=0, position=position,
                  extent=(extent, (0.0, float(fan["height"]))),
                  sign=1 if side == 0 else -1)
            for i, extent in enumerate(extents)
        ]
    supplies: list[Panel] = []
    if plenum:
        plenum_walls, supplies = _plenum_panels(
            plenum, [hall.lo[0], hall.hi[0]][: len(galleries)],
            cold_aisles, ceiling, width, _plenum_k(spec), _supply_mesh_k(spec),
        )
        walls += plenum_walls

    return _Layout(domain, galleries, hall, rows, cold_aisles, hot_aisles,
                   fans, grilles + supplies, walls, spans,
                   plenum_depth or None,
                   _supply_mesh_k(spec) if plenum and plenum["as_mesh"] else None,
                   row_notes)


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
        # `replace` rather than a positional rebuild, for the same reason the
        # panels use it: a field added later lands in the wrong slot and the
        # value it displaces is gone. `resistance_kw`, inserted before `rho`,
        # arrived holding the air density -- 1,2 kW of cabinet -- which made
        # every rack forty times too resistive and nothing raised a thing.
        return dataclasses.replace(r, box=snap_box(r.box))

    model.rows = [
        Row(row.id, snap_band(row.band), row.front_sign, [snap_rack(r) for r in row.racks])
        for row in model.rows
    ]
    model.racks = [rack for row in model.rows for rack in row.racks]
    # `replace` rather than a fresh Panel: rebuilding one field by field means
    # every field added later has to be remembered here, and the one that is
    # not comes back silently as its default. That is how `of_rack` arrived
    # false on every panel the first time.
    model.panels = [
        dataclasses.replace(
            p,
            position=snap(p.position, p.axis),
            # A DOWNFLOW UNIT HAS A SECOND PLANE. `return_z` is where its top
            # face sits, a storey above the one `position` names, and it was
            # the one field here that `replace` carried across unsnapped --
            # the hazard the comment above is about, arriving in the field it
            # warns about. A 2,87 m unit on a 1,0 m floor returns at 3,87 m,
            # which is not on a 0,25 m grid, so `topoSet` selected NO faces
            # and every `fanNIntake` patch came out empty. `createBaffles`
            # was happy; the orientation check then read a face one past the
            # end of the mesh and raised `IndexError: no face 433534`. Had it
            # not, the solve would have run a plant that returns nothing
            # (ADR-095).
            return_z=None if p.return_z is None else snap(p.return_z, 2),
            extent=tuple(  # type: ignore[arg-type]
                (snap(a0, axis), snap(a1, axis))
                for axis, (a0, a1) in zip(p.in_plane_axes, p.extent)
            ),
        )
        for p in model.panels
    ]
    model.cold_aisles = [snap_band(band) for band in model.cold_aisles]
    model.hot_aisles = [snap_band(band) for band in model.hot_aisles]
    model.galleries = [snap_box(box) for box in model.galleries]
    model.hall = snap_box(model.hall)
    model.domain = snap_box(model.domain)
    # A BLOCK IS WHAT ITS ROWS TURNED OUT TO BE, not the span they were asked
    # to fill. Snapping the stored span on its own let the two disagree: the
    # rows came out 10,20 m and the block still said 10,40, so the drawing
    # dimensioned a row two hundred millimetres longer than the row, and
    # `chimney_area` measured a contained aisle that long too. Every row in a
    # block shares a span, so the rows are the answer (ADR-085).
    spans = sorted({row.span for row in model.rows if row.racks})
    model.blocks = spans or [(snap(lo, 0), snap(hi, 0)) for lo, hi in model.blocks]
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
                f"{label}: {num(value, 3)} m falls between {num(cell)} m grid "
                f"lines; the mesh uses {num(snapped)} m "
                f"({(snapped - value) * 1000:+.0f} mm)."
            )
    return warnings


#: What to call each panel where a person reads it. Internal names stay as
#: they are -- they name mesh patches and have to match the case files.
PANEL_LABEL = {
    "fan": "fan wall",
    "plenum_opening": "gallery opening",
    "plenum_opening2": "gallery opening",
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

    Everything in the tool is English (ADR-026). These are the numbers
    an engineer checks before committing to a solve -- face areas and the
    velocities they imply -- so they are spelled out rather than left to be
    recomputed from the geometry.
    """
    row = model.rack_span()
    fans = model.fans
    fan = fans[0]
    grilles = [p for p in model.panels if p.name.startswith("grille")]
    grille_area = sum(p.area for p in grilles)
    openings = [p for p in model.panels if p.name.startswith("plenum_opening")]
    plenum = openings[0]
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
            "Mechanical gallery"
            + (f" ({len(model.galleries)})" if len(model.galleries) > 1 else ""),
            f"{num(model.gallery.size[0])} m deep"
            + (", one at each end" if len(model.galleries) > 1 else ""),
            f"{num(sum(g.volume for g in model.galleries), 1)} m3",
        ),
        (
            "Data hall",
            f"{num(model.hall.size[0])} m long"
            + (
                f", rows in {len(model.rack_blocks)} blocks"
                if len(model.rack_blocks) > 1
                else ""
            ),
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
        *(
            [(
                "Supply mesh" if model.supply_mesh_k is not None
                else f"Supply grilles ({len(model.plenum_grilles)})",
                (
                    f"{num(model.plenum_depth)} m plenum, "
                    + ("closed by 13 x 13 mm mesh"
                       if model.supply_mesh_k is not None
                       else f"{face(model.plenum_grilles[0])} each")
                ),
                through(sum(g.area for g in model.plenum_grilles))
                + (
                    f" · K {num(model.plenum_grilles[0].resistance)} · "
                    f"{num(model.plenum_pressure_drop_pa or 0.0, 2)} Pa"
                    if model.plenum_grilles[0].resistance is not None
                    else ""
                ),
            )]
            if model.plenum_grilles
            else []
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
        (
            "Opening to the gallery"
            + (f" ({len(openings)})" if len(openings) > 1 else ""),
            face(plenum),
            through(sum(p.area for p in openings)),
        ),
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
        "galleries": [{"lo": list(g.lo), "hi": list(g.hi)} for g in model.galleries],
        "hall": {"lo": list(model.hall.lo), "hi": list(model.hall.hi)},
        "dividers": model.dividers,
        # Which supply architecture this is, for the document that describes
        # it: a raised floor changes the half of the loop the reader is
        # checking against a drawing (ADR-076).
        "floor_height": model.floor_height,
        "blocks": [list(span) for span in model.rack_blocks],
        "ceiling_z": model.ceiling_z,
        # The room stands this far above the slab, where a case is on an
        # access floor. The drawing shades and names the volume under it
        # (ADR-076).
        "floor_height": model.floor_height,
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
        "fan_sides": [p.sign for p in model.fans],
        # How the units decide their duty: to their own return, or as one
        # networked plant to the worst return any of them sees (ADR-064). The
        # report has to say which produced the per-unit capacity it shows.
        "fan_control": model.fan_control,
        # Drawn, never meshed. The solver sees a zero-thickness baffle pair,
        # because a fan wall is a boundary condition and not a volume -- but a
        # drawing that leaves a 1,5 m deep machine as a line gives a reader
        # checking whether the gallery holds it nothing to measure (ADR-046).
        "fan_depth_m": fan_depth(spec),
        # The cavity between the two leaves of the wall into the hall, where
        # the case asks for one. The panels say where the leaf is; this says
        # what the arrangement is, for a reader and for the report (ADR-058).
        "plenum_depth_m": model.plenum_depth,
        # What a case gets where it states nothing. The page shows these in
        # the fields rather than leaving them blank, so a reader sees the
        # house standard and either takes it or changes it -- an empty box
        # says neither what the number would be nor that there is one.
        "plenum_defaults": {
            "plenum_depth": PLENUM_DEPTH,
            "plenum_grille_width": PLENUM_GRILLE_WIDTH,
            "plenum_grille_height": (
                round(model.racks[0].box.hi[2], 3) if model.racks else None
            ),
        },
        # Which perforated surface each role uses here, and what else the
        # library offers. The model page picks; the components page edits.
        "components": components_in_use(spec),
        # The same, for the machine: which unit this case names and what else
        # is in `equipment/`. Without it the page could show the unit's name
        # and link to its datasheet, and offer no way to choose another
        # (ADR-092).
        "equipment": equipment_in_use(spec),
        # How the customer cage is built, where there is one. The drawing
        # shows a rectangle either way; the answer is not the same (ADR-096).
        "cage": model.cage,
        "cage_k": cage_k(spec),
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
                "sign": p.sign,
                "of_rack": p.of_rack,
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
            # The unit's datasheet figures, carried on the model rather than
            # left in the spec: the export writes the model, and a reader of
            # the result should not have to go back to the YAML to learn what
            # the machine was rated at.
            "unit_capacity_kw": model.unit_capacity_kw,
            "unit_power_kw": model.unit_power_kw,
            "fan_static_pa": model.fan_static_pa,
            "fan_curve": [list(p) for p in model.fan_curve] if model.fan_curve else None,
        },
        "stations": [
            {"name": s.name, "label": s.label, "note": s.note}
            for s in stations(model)
        ],
        "summary": [list(row) for row in summary_rows(model)],
        "hvac": model.hvac(),
        "alerts": model.alerts,
        "site": {"altitude_m": model.altitude_m, "pressure_pa": round(model.pressure_pa), "rho": round(model.rho, 4)},
        "warnings": model.warnings,
        "spec": spec,
    }
