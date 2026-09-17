"""Read a case's geometry and loads back out of its OpenFOAM dictionaries.

Deriving this from the dictionaries rather than from ``room.yaml`` means
post-processing works on any case the solver accepted -- including the
hand-written reference case that predates the spec format. When the generator
lands (M2), the spec becomes the source of truth for *building* a case and this
module stays the source of truth for *reading one back*.

The parsers are deliberately narrow: they understand the axis-aligned,
single-block vocabulary AICFD generates, and raise rather than guess on anything
else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_COMMENTS = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_VEC3 = r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)"


@dataclass
class Box:
    """An axis-aligned box in metres."""

    lo: tuple[float, float, float]
    hi: tuple[float, float, float]

    @property
    def size(self) -> tuple[float, float, float]:
        return tuple(h - l for l, h in zip(self.lo, self.hi))  # type: ignore[return-value]


@dataclass
class HeatSource:
    """A volumetric heat load bound to a cell zone."""

    zone: str
    watts: float


@dataclass
class CaseGeometry:
    """Everything the viewer and the KPI pass need to know about a case."""

    room: Box
    divisions: tuple[int, int, int]
    patches: dict[str, str] = field(default_factory=dict)
    zones: dict[str, Box] = field(default_factory=dict)
    heat_sources: list[HeatSource] = field(default_factory=list)
    inlet_velocity: tuple[float, float, float] | None = None
    inlet_temperature_k: float | None = None
    inlet_patch: str | None = None

    @property
    def n_cells(self) -> int:
        nx, ny, nz = self.divisions
        return nx * ny * nz

    @property
    def total_load_w(self) -> float:
        return sum(source.watts for source in self.heat_sources)


def read_case(case_dir: str | Path) -> CaseGeometry:
    """Parse a case directory into a :class:`CaseGeometry`."""
    case = Path(case_dir)
    room, divisions, patches = _read_block_mesh(case / "system" / "blockMeshDict")

    geometry = CaseGeometry(room=room, divisions=divisions, patches=patches)

    topo = case / "system" / "topoSetDict"
    if topo.exists():
        geometry.zones = _read_topo_set(topo)

    fv_options = case / "constant" / "fvOptions"
    if fv_options.exists():
        geometry.heat_sources = _read_heat_sources(fv_options)

    _read_inlet(case, geometry)
    return geometry


def _strip(path: Path) -> str:
    return _COMMENTS.sub(" ", path.read_text())


def _read_block_mesh(
    path: Path,
) -> tuple[Box, tuple[int, int, int], dict[str, str]]:
    text = _strip(path)

    vertices = [
        tuple(float(v) for v in m.groups())
        for m in re.finditer(_VEC3, _section(text, "vertices"))
    ]
    if len(vertices) != 8:
        raise ValueError(
            f"{path}: expected 8 vertices (one hex block), found {len(vertices)}. "
            "AICFD only reads single-block, axis-aligned meshes."
        )
    lo = tuple(min(v[axis] for v in vertices) for axis in range(3))
    hi = tuple(max(v[axis] for v in vertices) for axis in range(3))

    block = re.search(r"hex\s*\([^)]*\)\s*" + _VEC3, text)
    if not block:
        raise ValueError(f"{path}: could not read the hex block's cell divisions")
    divisions = tuple(int(float(n)) for n in block.groups())

    scale = re.search(r"\bscale\s+([-\d.eE+]+)\s*;", text)
    factor = float(scale.group(1)) if scale else 1.0
    room = Box(
        lo=tuple(v * factor for v in lo),  # type: ignore[arg-type]
        hi=tuple(v * factor for v in hi),  # type: ignore[arg-type]
    )

    patches = {
        m.group(1): m.group(2)
        for m in re.finditer(
            r"(\w+)\s*\{\s*type\s+(\w+)\s*;", _section(text, "boundary")
        )
    }
    return room, divisions, patches  # type: ignore[return-value]


def _read_topo_set(path: Path) -> dict[str, Box]:
    """Cell zones defined by ``boxToCell``, keyed by zone name."""
    zones: dict[str, Box] = {}
    for action in re.finditer(r"\{([^{}]*)\}", _strip(path)):
        body = action.group(1)
        if "boxToCell" not in body:
            continue
        name = re.search(r"\bname\s+(\w+)\s*;", body)
        box = re.search(r"\bbox\s+" + _VEC3 + r"\s*" + _VEC3, body)
        if name and box:
            values = [float(v) for v in box.groups()]
            zones[name.group(1)] = Box(
                lo=tuple(values[:3]),  # type: ignore[arg-type]
                hi=tuple(values[3:]),  # type: ignore[arg-type]
            )
    return zones


def _read_heat_sources(path: Path) -> list[HeatSource]:
    """Enthalpy (``h``) or internal-energy (``e``) sources, in watts."""
    sources: list[HeatSource] = []
    text = _strip(path)
    for match in re.finditer(r"scalarSemiImplicitSourceCoeffs\s*\{", text):
        body = text[match.end() : _closing_brace(text, match.end() - 1)]
        zone = re.search(r"\bcellZone\s+(\w+)\s*;", body)
        rate = re.search(r"\b[he]\s+\(\s*([-\d.eE+]+)\s+[-\d.eE+]+\s*\)", body)
        if zone and rate:
            sources.append(HeatSource(zone=zone.group(1), watts=float(rate.group(1))))
    return sources


def _read_inlet(case: Path, geometry: CaseGeometry) -> None:
    """Find the fixed-velocity patch and its supply temperature."""
    u_file = case / "0" / "U"
    if not u_file.exists():
        return
    u_text = _strip(u_file)
    boundary = u_text[u_text.index("boundaryField") :]

    for name in geometry.patches:
        entry = re.search(
            rf"\b{re.escape(name)}\s*\{{(.*?)\}}", boundary, re.DOTALL
        )
        if not entry or "fixedValue" not in entry.group(1):
            continue
        vector = re.search(r"uniform\s*" + _VEC3, entry.group(1))
        if not vector:
            continue
        velocity = tuple(float(v) for v in vector.groups())
        if all(component == 0.0 for component in velocity):
            continue  # a fixed *wall*, not a supply
        geometry.inlet_patch = name
        geometry.inlet_velocity = velocity  # type: ignore[assignment]
        break

    t_file = case / "0" / "T"
    if geometry.inlet_patch and t_file.exists():
        t_text = _strip(t_file)
        entry = re.search(
            rf"\b{re.escape(geometry.inlet_patch)}\s*\{{(.*?)\}}",
            t_text[t_text.index("boundaryField") :],
            re.DOTALL,
        )
        if entry:
            value = re.search(r"uniform\s+([-\d.eE+]+)", entry.group(1))
            if value:
                geometry.inlet_temperature_k = float(value.group(1))


def _section(text: str, keyword: str) -> str:
    """The parenthesised list following ``keyword``."""
    start = text.index(keyword) + len(keyword)
    open_index = text.index("(", start)
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return text[open_index + 1 : i]
    raise ValueError(f"unbalanced parentheses after '{keyword}'")


def _closing_brace(text: str, open_index: int) -> int:
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    raise ValueError("unbalanced braces")
