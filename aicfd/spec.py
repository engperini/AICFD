"""The room spec: what the user writes, in the units they already work in.

A case is fully described by one YAML file (ADR-004). This module owns the
schema, the unit conversions and the validation -- and nothing else. It holds no
OpenFOAM knowledge at all; ``aicfd.case`` is the only module that does.

Validation is hand-written rather than delegated to a schema library on purpose.
The audience is an engineer who typed a number wrong, not a developer reading a
stack trace, so every message names the item, the field, the value and the fix.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Air at ~20 C, 1 atm.
RHO_AIR = 1.19  # kg/m3
CP_AIR = 1005.0  # J/(kg K)

M3H_PER_CFM = 1.69901

#: Default temperature rise used to size a rack's airflow when none is given.
#: 11 K is typical of enterprise IT at the rack outlet.
DEFAULT_RACK_DELTA_T = 11.0

#: Default pressure drop across a populated rack at its rated airflow, in Pa.
#: Manufacturer data ranges roughly 15-40 Pa; 25 is a reasonable middle.
DEFAULT_RACK_PRESSURE_DROP = 25.0

AXES = ("x", "y", "z")


class SpecError(ValueError):
    """A room spec that cannot be turned into a valid case."""


@dataclass
class Rack:
    """One rack, or one row modelled as a single porous block."""

    id: str
    position: tuple[float, float]
    """x, y of the lower corner of the rack footprint, in metres."""
    size: tuple[float, float, float] = (1.0, 0.6, 2.0)
    """depth (along airflow), width, height, in metres. Default: a 600 mm rack."""
    load_kw: float = 5.0
    airflow_m3h: float | None = None
    """Rated airflow. Sizes the rack's flow resistance -- see the note below."""
    pressure_drop_pa: float = DEFAULT_RACK_PRESSURE_DROP
    base_z: float = 0.0

    @property
    def load_w(self) -> float:
        return self.load_kw * 1000.0

    @property
    def face_area_m2(self) -> float:
        """Area presented to the airflow (width x height)."""
        return self.size[1] * self.size[2]

    @property
    def rated_airflow_m3h(self) -> float:
        """The rack's airflow, given or derived from its load."""
        if self.airflow_m3h is not None:
            return self.airflow_m3h
        return self.load_w / (DEFAULT_RACK_DELTA_T * RHO_AIR * CP_AIR) * 3600.0

    @property
    def face_velocity_ms(self) -> float:
        return self.rated_airflow_m3h / 3600.0 / self.face_area_m2

    @property
    def lo(self) -> tuple[float, float, float]:
        return (self.position[0], self.position[1], self.base_z)

    @property
    def hi(self) -> tuple[float, float, float]:
        return tuple(self.lo[i] + self.size[i] for i in range(3))  # type: ignore[return-value]


@dataclass
class Crac:
    """A cooling unit, modelled as a supply patch on one wall of the room."""

    id: str
    airflow_m3h: float
    supply_temp_c: float = 18.0
    patch: str = "xmin"

    @property
    def airflow_m3s(self) -> float:
        return self.airflow_m3h / 3600.0


@dataclass
class Mesh:
    cell_size: float = 0.1
    """Target cell edge, in metres. Halving it multiplies cells by 8."""


@dataclass
class Solver:
    max_iterations: int = 2000
    residual_tolerance: float = 1e-4
    write_interval: int = 250


@dataclass
class RoomSpec:
    name: str
    size: tuple[float, float, float]
    """x (along the airflow), y (across), z (height), in metres."""
    racks: list[Rack] = field(default_factory=list)
    cracs: list[Crac] = field(default_factory=list)
    mesh: Mesh = field(default_factory=Mesh)
    solver: Solver = field(default_factory=Solver)
    warnings: list[str] = field(default_factory=list)

    # --- derived quantities the generator and the report both need -----------

    @property
    def total_load_w(self) -> float:
        return sum(rack.load_w for rack in self.racks)

    @property
    def total_supply_m3s(self) -> float:
        return sum(crac.airflow_m3s for crac in self.cracs)

    @property
    def supply_temp_c(self) -> float:
        """Flow-weighted mean supply temperature across all CRACs."""
        if not self.cracs:
            return 18.0
        total = self.total_supply_m3s
        if total == 0:
            return self.cracs[0].supply_temp_c
        return (
            sum(c.airflow_m3s * c.supply_temp_c for c in self.cracs) / total
        )

    @property
    def supply_patch_area_m2(self) -> float:
        """Area of the wall the CRACs blow through (the whole yz face)."""
        return self.size[1] * self.size[2]

    @property
    def supply_velocity_ms(self) -> float:
        """The number that fixes the physics: real airflow over real area.

        This is the single value that made the inherited reference case
        unrepresentative -- it used a velocity picked for fast convergence
        rather than one derived from a CRAC's rating.
        """
        return self.total_supply_m3s / self.supply_patch_area_m2

    @property
    def design_delta_t_k(self) -> float:
        """Bulk temperature rise this room will show if the load is as stated."""
        if self.total_supply_m3s == 0:
            return float("inf")
        return self.total_load_w / (self.total_supply_m3s * RHO_AIR * CP_AIR)

    @property
    def divisions(self) -> tuple[int, int, int]:
        return tuple(  # type: ignore[return-value]
            max(1, round(self.size[axis] / self.mesh.cell_size)) for axis in range(3)
        )

    @property
    def cell_size_actual(self) -> tuple[float, float, float]:
        divisions = self.divisions
        return tuple(  # type: ignore[return-value]
            self.size[axis] / divisions[axis] for axis in range(3)
        )

    @property
    def n_cells(self) -> int:
        nx, ny, nz = self.divisions
        return nx * ny * nz


# --- loading -----------------------------------------------------------------


def load(path: str | Path) -> RoomSpec:
    """Read and validate a room spec from a YAML file."""
    path = Path(path)
    try:
        raw = yaml.safe_load(path.read_text())
    except yaml.YAMLError as error:
        raise SpecError(f"{path}: not valid YAML -- {error}") from error
    if not isinstance(raw, dict):
        raise SpecError(f"{path}: expected a mapping at the top level")
    spec = from_dict(raw, default_name=path.stem)
    return spec


def from_dict(raw: dict, default_name: str = "case") -> RoomSpec:
    """Build and validate a :class:`RoomSpec` from parsed YAML."""
    room = _require_mapping(raw, "room")
    size = _vector(room, "size", 3, "room.size")

    racks = [
        _rack(entry, index) for index, entry in enumerate(raw.get("racks") or [], 1)
    ]
    cracs = [
        _crac(entry, index) for index, entry in enumerate(raw.get("cracs") or [], 1)
    ]

    mesh_raw = raw.get("mesh") or {}
    solver_raw = raw.get("solver") or {}
    spec = RoomSpec(
        name=str(raw.get("name") or default_name),
        size=size,
        racks=racks,
        cracs=cracs,
        mesh=Mesh(cell_size=float(mesh_raw.get("cell_size", 0.1))),
        solver=Solver(
            max_iterations=int(solver_raw.get("max_iterations", 2000)),
            residual_tolerance=float(solver_raw.get("residual_tolerance", 1e-4)),
            write_interval=int(solver_raw.get("write_interval", 250)),
        ),
    )
    validate(spec)
    return spec


def _rack(entry, index: int) -> Rack:
    if not isinstance(entry, dict):
        raise SpecError(f"racks[{index}]: expected a mapping")
    rack_id = str(entry.get("id") or f"R{index:02d}")
    position = _vector(entry, "position", 2, f"rack '{rack_id}' position")
    size = (
        _vector(entry, "size", 3, f"rack '{rack_id}' size")
        if "size" in entry
        else (1.0, 0.6, 2.0)
    )
    airflow = entry.get("airflow_m3h")
    if airflow is None and "airflow_cfm" in entry:
        airflow = float(entry["airflow_cfm"]) * M3H_PER_CFM
    return Rack(
        id=rack_id,
        position=position,  # type: ignore[arg-type]
        size=size,  # type: ignore[arg-type]
        load_kw=float(entry.get("load_kw", 5.0)),
        airflow_m3h=float(airflow) if airflow is not None else None,
        pressure_drop_pa=float(
            entry.get("pressure_drop_pa", DEFAULT_RACK_PRESSURE_DROP)
        ),
        base_z=float(entry.get("base_z", 0.0)),
    )


def _crac(entry, index: int) -> Crac:
    if not isinstance(entry, dict):
        raise SpecError(f"cracs[{index}]: expected a mapping")
    crac_id = str(entry.get("id") or f"CRAC{index:02d}")
    airflow = entry.get("airflow_m3h")
    if airflow is None and "airflow_cfm" in entry:
        airflow = float(entry["airflow_cfm"]) * M3H_PER_CFM
    if airflow is None:
        raise SpecError(
            f"CRAC '{crac_id}': airflow_m3h (or airflow_cfm) is required. "
            "This is the number that determines the room's temperature rise, "
            "so AICFD will not guess it."
        )
    return Crac(
        id=crac_id,
        airflow_m3h=float(airflow),
        supply_temp_c=float(entry.get("supply_temp_c", 18.0)),
        patch=str(entry.get("patch", "xmin")),
    )


# --- validation ---------------------------------------------------------------


def validate(spec: RoomSpec) -> None:
    """Raise on anything that would produce a meaningless case; warn on the rest."""
    spec.warnings = []

    for axis, value in zip(AXES, spec.size):
        if value <= 0:
            raise SpecError(f"room.size {axis} must be positive, got {value}")
        if not 0.5 <= value <= 200:
            raise SpecError(
                f"room.size {axis} is {value} m, outside the supported 0.5-200 m. "
                "Check the units -- sizes are metres, not millimetres."
            )

    if not spec.racks:
        raise SpecError("the room has no racks, so there is nothing to cool")
    if not spec.cracs:
        raise SpecError(
            "the room has no CRACs, so there is no air supply. Add at least one "
            "with its airflow_m3h."
        )

    seen: dict[str, Rack] = {}
    for rack in spec.racks:
        if rack.id in seen:
            raise SpecError(f"two racks share the id '{rack.id}'")
        seen[rack.id] = rack

        if rack.load_kw <= 0:
            raise SpecError(f"rack '{rack.id}': load_kw must be positive, got {rack.load_kw}")
        if rack.load_kw > 100:
            spec.warnings.append(
                f"Rack '{rack.id}' is {rack.load_kw} kW. Above ~50 kW a rack is "
                "normally liquid-cooled, which this air-side model does not "
                "represent."
            )
        for axis, value in zip(AXES, rack.size):
            if value <= 0:
                raise SpecError(
                    f"rack '{rack.id}': size {axis} must be positive, got {value}"
                )
        for axis in range(3):
            if rack.lo[axis] < 0 or rack.hi[axis] > spec.size[axis]:
                raise SpecError(
                    f"rack '{rack.id}' extends from {rack.lo[axis]:.2f} to "
                    f"{rack.hi[axis]:.2f} m on {AXES[axis]}, outside the "
                    f"0-{spec.size[axis]:.2f} m room"
                )

    for first, second in _pairs(spec.racks):
        if _overlaps(first, second):
            raise SpecError(
                f"racks '{first.id}' and '{second.id}' overlap in space. "
                "Two heat sources in the same cells is not a valid model."
            )

    for crac in spec.cracs:
        if crac.airflow_m3h <= 0:
            raise SpecError(
                f"CRAC '{crac.id}': airflow_m3h must be positive, got {crac.airflow_m3h}"
            )
        if not -20 <= crac.supply_temp_c <= 40:
            raise SpecError(
                f"CRAC '{crac.id}': supply_temp_c is {crac.supply_temp_c}, which is "
                "not a plausible supply air temperature"
            )
        if crac.patch != "xmin":
            raise SpecError(
                f"CRAC '{crac.id}': patch '{crac.patch}' is not supported yet. "
                "Only 'xmin' (a fan wall on the upstream face) works today; "
                "downflow and in-row units are planned for M5."
            )

    if spec.mesh.cell_size <= 0:
        raise SpecError(f"mesh.cell_size must be positive, got {spec.mesh.cell_size}")

    _validate_mesh_resolution(spec)
    _validate_airflow(spec)


def _validate_mesh_resolution(spec: RoomSpec) -> None:
    cells = spec.n_cells
    if cells > 5_000_000:
        raise SpecError(
            f"mesh.cell_size {spec.mesh.cell_size} m gives {cells:,} cells, which "
            "will not finish in reasonable time. Try a coarser cell_size."
        )
    if cells > 1_500_000:
        spec.warnings.append(
            f"{cells:,} cells is a long run (tens of minutes). Start coarser, "
            "confirm the answer looks right, then refine."
        )

    # A rack thinner than a couple of cells is not resolved: topoSet will give it
    # a ragged zone, or none at all, and the load lands in the wrong place.
    cell = spec.cell_size_actual
    for rack in spec.racks:
        thin = [
            f"{AXES[axis]} ({rack.size[axis]:.2f} m / {cell[axis]:.3f} m cells)"
            for axis in range(3)
            if rack.size[axis] < 2 * cell[axis]
        ]
        if thin:
            raise SpecError(
                f"rack '{rack.id}' is thinner than two cells on {', '.join(thin)}. "
                f"Reduce mesh.cell_size to at most "
                f"{min(rack.size) / 2:.3f} m, or model the row as one larger block."
            )


def _validate_airflow(spec: RoomSpec) -> None:
    delta_t = spec.design_delta_t_k
    if delta_t < 2:
        spec.warnings.append(
            f"The CRACs supply {spec.total_supply_m3s * 3600:,.0f} m3/h for "
            f"{spec.total_load_w / 1000:.1f} kW, a bulk rise of only "
            f"{delta_t:.1f} K. That is far more air than the load needs, so the "
            "result will show almost no temperature variation."
        )
    elif delta_t > 20:
        spec.warnings.append(
            f"The CRACs supply {spec.total_supply_m3s * 3600:,.0f} m3/h for "
            f"{spec.total_load_w / 1000:.1f} kW, a bulk rise of {delta_t:.1f} K. "
            "Check the airflow -- this room is short of air and will run hot."
        )

    rack_demand = sum(rack.rated_airflow_m3h for rack in spec.racks) / 3600.0
    if rack_demand > spec.total_supply_m3s:
        spec.warnings.append(
            f"The racks are rated for {rack_demand * 3600:,.0f} m3/h in total but "
            f"the CRACs supply {spec.total_supply_m3s * 3600:,.0f} m3/h. The "
            "shortfall has to come from recirculated hot air, so expect elevated "
            "rack inlet temperatures."
        )

    velocity = spec.supply_velocity_ms
    if velocity > 5:
        spec.warnings.append(
            f"Supply face velocity is {velocity:.1f} m/s across the whole wall, "
            "which is unusually fast and may hurt convergence."
        )


def _pairs(items):
    for i, first in enumerate(items):
        for second in items[i + 1 :]:
            yield first, second


def _overlaps(a: Rack, b: Rack) -> bool:
    return all(a.lo[axis] < b.hi[axis] and b.lo[axis] < a.hi[axis] for axis in range(3))


def _require_mapping(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise SpecError(f"missing or malformed '{key}:' section")
    return value


def _vector(source: dict, key: str, length: int, label: str) -> tuple[float, ...]:
    value = source.get(key)
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise SpecError(
            f"{label}: expected {length} numbers like "
            f"[{', '.join(['0.0'] * length)}], got {value!r}"
        )
    try:
        return tuple(float(v) for v in value)
    except (TypeError, ValueError) as error:
        raise SpecError(f"{label}: {value!r} contains something that is not a number") from error
