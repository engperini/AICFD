"""A hall read from a DRAWING: the named-solid STL and its sidecar (ADR-131).

The parametric layouts build a room from parameters -- pods, one typical row,
one cold aisle, one perimeter. A hall drawn by an architect is never that
regular, so a second input exists: the geometry as drawn, extracted from the
DWG by `aicfd-hall-from-dwg` into an STL of named, axis-aligned solids (one
per cabinet, unit, panel and opening, every coordinate on the mesh grid) and
a sidecar YAML that carries only what a drawing cannot say -- loads, plant,
components, mesh, solver. The contract between the two is the skill's
`reference/contract.md`; this module is its reader.

What comes out is the SAME `Model` the parametric layouts produce: the same
boxes, rows, aisles and panels under the same names, so the mesher, the
solver, the checks, the page and the report run unchanged. Every solid type
maps to one thing the model already has:

    hall            the domain
    wall:gallery_*  the dividing walls (built by the mesher from the hall's x faces)
    mesh/opening    the holes in them: plenum_opening above the ceiling,
                    floor_opening below the deck, at the mesh's K
    wall:plenum_*   the inner leaf of a double wall -> plenum_wall; supply:* -> supply grilles
    rack:id:front   a Rack; rows are the cabinets sharing a band and a front
    unit:id[:dir]   a fan panel: vertical in the dividing wall (fan wall) or a
                    horizontal pair on the deck (downflow)
    lid/door/wall   containment_lid / containment_door / containment_wall / containment_side
    blank           blank
    cage            cage_* (a resistance for mesh, a wall for drywall)
    grille / tile   grille* in the ceiling, tile_* in the deck, at their components' K
    deck / ceiling  floor_deck and ceiling

Nothing here decides a size, a count or a position: the drawing did. What
this module decides is only what the parametric path also decides after the
boxes are placed -- which aisle is cold, which racks the cage holds, what a
grille costs the fan.
"""
from __future__ import annotations

import dataclasses
import re
from pathlib import Path

from aicfd.model import (
    CFM_PER_KW,
    Box,
    Model,
    Panel,
    Rack,
    Row,
    _plates_must_fit,
    air_density,
    cage_k,
    check_mesh_alignment,
    component_for,
    equipment_for,
    num,
    out_of_service,
    parse_cell_size,
    parse_fan_curve,
    site_pressure,
    snap_to_mesh,
)

#: The solid types the contract defines. Anything else in the file is refused
#: by name rather than skipped: a converter that writes a type this reader
#: does not know has geometry the solver will not see.
TYPES = ("hall", "rack", "unit", "wall", "lid", "door", "deck", "ceiling",
         "opening", "mesh", "cage", "grille", "tile", "blank", "supply")

#: A gap between cabinets wider than this makes a second block of the row --
#: a walkway between two columns of PODs -- rather than a missing position.
ROW_SPLIT_GAP = 1.0


@dataclasses.dataclass
class Solid:
    name: str
    kind: str
    id: str
    attr: str
    lo: tuple[float, float, float]
    hi: tuple[float, float, float]
    facets: int

    @property
    def flat_axis(self) -> int | None:
        """The axis a panel is flat on, or None for a box."""
        flat = [k for k in range(3) if abs(self.hi[k] - self.lo[k]) < 1e-9]
        return flat[0] if len(flat) == 1 else None

    @property
    def box(self) -> Box:
        return Box(self.lo, self.hi)

    def panel(self, name: str, kind: str, **extra) -> Panel:
        """This flat solid as a Panel of that name and kind."""
        axis = self.flat_axis
        if axis is None:
            raise ValueError(f"{self.name}: a {self.kind} must be a flat panel, and this one is a box")
        others = [k for k in range(3) if k != axis]
        return Panel(name, kind, axis=axis, position=self.lo[axis],
                     extent=((self.lo[others[0]], self.hi[others[0]]),
                             (self.lo[others[1]], self.hi[others[1]])), **extra)


def read_stl(path: str | Path) -> list[Solid]:
    """Every named solid of an ASCII STL, with its bounding box."""
    solids: list[Solid] = []
    name = None
    pts: list[tuple[float, float, float]] = []
    facets = 0
    with open(path) as handle:
        for line in handle:
            t = line.split()
            if not t:
                continue
            if t[0] == "solid":
                name, pts, facets = (t[1] if len(t) > 1 else ""), [], 0
            elif t[0] == "facet":
                facets += 1
            elif t[0] == "vertex":
                pts.append((float(t[1]), float(t[2]), float(t[3])))
            elif t[0] == "endsolid":
                if name is None or not pts:
                    raise ValueError(f"{path}: a solid with no vertices")
                parts = name.split(":")
                solids.append(Solid(
                    name, parts[0], parts[1] if len(parts) > 1 else "",
                    parts[2] if len(parts) > 2 else "",
                    tuple(min(p[k] for p in pts) for k in range(3)),  # type: ignore[arg-type]
                    tuple(max(p[k] for p in pts) for k in range(3)),  # type: ignore[arg-type]
                    facets,
                ))
                name = None
    if not solids:
        raise ValueError(f"{path}: no named solid in the file; is it ASCII STL?")
    return solids


def is_geometry_case(spec: dict) -> bool:
    return bool(((spec or {}).get("geometry") or {}).get("file"))


def geometry_path(spec: dict) -> Path:
    """Where the STL is: as given, beside the case file, or under cases/."""
    raw = str(spec["geometry"]["file"])
    candidates = [Path(raw)]
    base = spec.get("_base")
    if base:
        candidates.append(Path(base) / raw)
    candidates.append(Path.cwd() / raw)
    try:
        from aicfd.cli import CASES_DIR

        candidates.append(Path(CASES_DIR) / raw)
    except Exception:  # pragma: no cover - the cli is always importable
        pass
    for path in candidates:
        if path.is_file():
            return path
    raise ValueError(
        f"geometry.file: {raw} is not a file beside the case, in the working "
        f"folder or under cases/. The STL the case names has to travel with it"
    )


def _on_grid(v: float, cell: float) -> bool:
    return abs(v / cell - round(v / cell)) < 1e-6


def _row_id(rack_id: str) -> str:
    """`F1-01` -> `F1`, `A01` -> `A`, `RB07` -> `RB`: the row a cabinet's id names."""
    m = re.match(r"^(.*?)-\d+$", rack_id)
    if m:
        return m.group(1)
    m = re.match(r"^([A-Za-z]+)\d+$", rack_id)
    return m.group(1) if m else rack_id


def build(spec: dict) -> Model:
    """The Model of a drawn hall, from its STL and sidecar."""
    path = geometry_path(spec)
    spec.pop("_base", None)
    solids = read_stl(path)
    cell = parse_cell_size((spec.get("mesh") or {}).get("cell_size", 0.2))
    name = spec.get("name", path.stem)

    by: dict[str, list[Solid]] = {}
    for s in solids:
        if s.kind not in TYPES:
            raise ValueError(f"{path.name}: solid {s.name!r} is of type {s.kind!r}, which the contract does not define "
                             f"({', '.join(TYPES)})")
        by.setdefault(s.kind, []).append(s)
        for p in (s.lo, s.hi):
            for k in range(3):
                if not _on_grid(p[k], cell[k]):
                    raise ValueError(
                        f"{path.name}: {s.name} has a vertex at {'xyz'[k]} = {p[k]:g}, which is not on the "
                        f"{num(cell[k])} m mesh the sidecar states. Every coordinate lands on a cell face "
                        f"(quantise in the converter, or change mesh.cell_size)")
    seen: dict[str, Solid] = {}
    for s in solids:
        if s.name in seen:
            raise ValueError(f"{path.name}: solid {s.name!r} appears twice")
        seen[s.name] = s
    if len(by.get("hall", [])) != 1:
        raise ValueError(f"{path.name}: {len(by.get('hall', []))} `hall` solids; exactly one is the air volume")
    if len(by.get("ceiling", [])) != 1:
        raise ValueError(f"{path.name}: a `ceiling` panel is required (the false ceiling with the return plenum above it)")
    if len(by.get("deck", [])) > 1:
        raise ValueError(f"{path.name}: more than one `deck`")

    domain = by["hall"][0].box
    W, L, slab = domain.hi
    ceiling = by["ceiling"][0]
    ceiling_z = ceiling.lo[2]
    deck = by["deck"][0] if by.get("deck") else None
    floor_z = deck.lo[2] if deck else 0.0

    # ---- the dividing walls and the galleries -------------------------------
    def full_width(s: Solid) -> bool:
        return s.lo[1] <= domain.lo[1] + 1e-6 and s.hi[1] >= L - 1e-6

    walls = by.get("wall", [])
    dividers = sorted({s.lo[0] for s in walls if s.flat_axis == 0 and full_width(s)
                       and s.hi[2] >= slab - 1e-6 and s.lo[2] <= floor_z + 1e-6})
    if not dividers:
        raise ValueError(f"{path.name}: no dividing wall (a `wall:` panel normal to x, the hall's full width, floor to slab) "
                         f"-- a hall needs a mechanical gallery")
    if len(dividers) > 2:
        raise ValueError(f"{path.name}: {len(dividers)} dividing walls; a hall has one gallery or one at each end")
    mid = W / 2
    west = [x for x in dividers if x < mid]
    east = [x for x in dividers if x >= mid]
    if not west:
        raise ValueError(f"{path.name}: the only gallery is at high x. AICFD keeps the first gallery at low x: "
                         f"mirror the drawing (the converter's transposition) so the gallery lies at x = 0")
    xw = west[0]
    xe = east[0] if east else W
    hall = Box((xw, 0.0, floor_z), (xe, L, slab))
    galleries = [Box((0.0, 0.0, floor_z), (xw, L, slab))]
    if east:
        galleries.append(Box((xe, 0.0, floor_z), (W, L, slab)))
    divider_x = [hall.lo[0], hall.hi[0]][: len(galleries)]

    panels: list[Panel] = []

    def side_of(x: float) -> int:
        """0 for the low-x gallery's wall, 1 for the high-x one."""
        return min(range(len(divider_x)), key=lambda i: abs(divider_x[i] - x))

    def suffix(i: int) -> str:
        return "" if i == 0 else str(i + 1)

    # ---- the holes in the dividing walls: mesh above the ceiling, below the deck ----
    mesh = component_for(spec, "gallery_mesh")
    for s in by.get("mesh", []) + by.get("opening", []):
        if s.flat_axis != 0 or not any(abs(s.lo[0] - x) < 1e-6 for x in divider_x):
            raise ValueError(f"{path.name}: {s.name} is not in the plane of a dividing wall; the contract's openings are the "
                             f"gallery's holes above the false ceiling and below the deck")
        i = side_of(s.lo[0])
        k = mesh.k if (mesh and s.kind == "mesh") else None
        sign = 1 if i == 0 else -1
        if s.lo[2] >= ceiling_z - 1e-6:
            panels.append(s.panel(f"plenum_opening{suffix(i)}", "opening", resistance=k, sign=sign))
        elif deck is not None and s.hi[2] <= floor_z + 1e-6:
            panels.append(s.panel(f"floor_opening{suffix(i)}", "opening", resistance=k, sign=sign))
        else:
            raise ValueError(f"{path.name}: {s.name} is an opening in the dividing wall between the deck and the false "
                             f"ceiling; a unit's face is a `unit`, and nothing else opens there")

    # ---- the ceiling, the deck ------------------------------------------------
    panels.append(ceiling.panel("ceiling", "wall"))
    tile = component_for(spec, "floor_tile")
    if deck is not None:
        panels.append(deck.panel("floor_deck", "wall"))
        for s in by.get("tile", []):
            if s.flat_axis != 2 or abs(s.lo[2] - floor_z) > 1e-6:
                raise ValueError(f"{path.name}: {s.name} is not in the deck's plane")
            panels.append(s.panel("tile_" + re.sub(r"[^A-Za-z0-9]", "_", s.id), "opening",
                                  resistance=tile.k if tile else None))
    elif by.get("tile"):
        raise ValueError(f"{path.name}: floor tiles without a `deck`")

    # ---- the supply plenum: the inner leaf and its grilles ----------------------
    plenum_depth = None
    supply = component_for(spec, "supply_grille")
    plenum_walls = [s for s in walls if s.flat_axis == 0 and full_width(s) and s.lo[0] not in dividers
                    and s.hi[2] <= ceiling_z + 1e-6 and s.lo[2] <= floor_z + 1e-6]
    for s in sorted(plenum_walls, key=lambda s: s.lo[0]):
        i = side_of(s.lo[0])
        depth = abs(s.lo[0] - divider_x[i])
        plenum_depth = depth if plenum_depth is None else plenum_depth
        panels.append(s.panel(f"plenum_wall{suffix(i)}", "wall", sign=1 if i == 0 else -1))
    supplies = sorted(by.get("supply", []), key=lambda s: (side_of(s.lo[0]), s.lo[1]))
    for n, s in enumerate(supplies, start=1):
        if s.flat_axis != 0 or not any(abs(s.lo[0] - w.lo[0]) < 1e-6 for w in plenum_walls):
            raise ValueError(f"{path.name}: {s.name} is not in the plane of a `wall:plenum_*` leaf")
        i = side_of(s.lo[0])
        panels.append(s.panel(f"supply{n}", "opening", resistance=supply.k if supply else None, sign=1 if i == 0 else -1))

    # ---- the cabinets and their rows ---------------------------------------
    R = spec.get("racks") or {}
    default_kw = float(R.get("load_kw", 0.0))
    loads = {str(k): float(v) for k, v in (R.get("loads") or {}).items() if v is not None}
    altitude = float((spec.get("site") or {}).get("altitude_m", 0.0))
    fan = spec.get("fanwall") or {}
    supply_c = float(fan.get("supply_temp_c", 20.0))
    rho = air_density(site_pressure(altitude), supply_c)
    cfm = float(R.get("airflow_cfm_per_kw", CFM_PER_KW))
    racks: list[Rack] = []
    fronts: dict[str, int] = {}
    for s in by.get("rack", []):
        if s.attr not in ("+y", "-y"):
            raise ValueError(f"{path.name}: {s.name}: a cabinet's front is `+y` or `-y` (rows run along x); "
                             f"{s.attr!r} is not supported")
        if s.flat_axis is not None:
            raise ValueError(f"{path.name}: {s.name} is flat; a cabinet is a box")
        kw = loads.get(s.id, default_kw)
        # EVERY CABINET RESISTS AT THE HALL'S STANDARD LOAD (`racks.load_kw`),
        # whatever it dissipates, as the parametric rows are built: the
        # closed-form drop the check compares the field against takes one
        # coefficient for the row, and a cabinet calibrated at its own 20 kW
        # would make the field and the form disagree by the square of the
        # flow ratio (ADR-054).
        racks.append(Rack(s.id, s.box, kw, airflow_axis=1, airflow_sign=-1 if s.attr == "+y" else 1,
                          cfm_per_kw=cfm, resistance_kw=default_kw, rho=rho))
        fronts[s.id] = -1 if s.attr == "+y" else 1
    if not racks:
        raise ValueError(f"{path.name}: no `rack:` solid; nothing to cool")
    # A ROW IS WHAT THE CONVERTER NAMED: the cabinets sharing a band, a front
    # and a row id (`F13-01`..`F13-13` are row F13, whatever the gaps between
    # them). Cabinets whose ids name no row are grouped by band and split at
    # a walkway.
    groups: dict[tuple, list[Rack]] = {}
    for r in racks:
        groups.setdefault((round(r.box.lo[1], 6), round(r.box.hi[1], 6), fronts[r.id], _row_id(r.id)), []).append(r)
    rows: list[Row] = []
    used_ids: dict[str, int] = {}
    for (y0, y1, sign, rid), members in sorted(groups.items(), key=lambda kv: (kv[0][0], min(r.box.lo[0] for r in kv[1]))):
        members.sort(key=lambda r: r.box.lo[0])
        blocks: list[list[Rack]] = [[members[0]]]
        for r in members[1:]:
            if rid == r.id and r.box.lo[0] - blocks[-1][-1].box.hi[0] > ROW_SPLIT_GAP + 1e-6:
                blocks.append([r])
            else:
                blocks[-1].append(r)
        for block in blocks:
            n = used_ids.get(rid, 0) + 1
            used_ids[rid] = n
            rows.append(Row(rid if n == 1 else f"{rid}b{n}", (y0, y1), sign, block))
    rack_top = max(r.box.hi[2] for r in racks)
    # each contiguous run of cabinets is a closed porous box: a lid over it and
    # a wall at each end, exactly as the parametric rows are closed
    for row in rows:
        runs: list[list[Rack]] = [[row.racks[0]]]
        for r in row.racks[1:]:
            if r.box.lo[0] - runs[-1][-1].box.hi[0] > 1e-6:
                runs.append([r])
            else:
                runs[-1].append(r)
        for k, run in enumerate(runs, start=1):
            x0, x1 = run[0].box.lo[0], run[-1].box.hi[0]
            z1 = max(r.box.hi[2] for r in run)
            tag = f"_{row.id}_{k}".replace("-", "_")
            panels.append(Panel(f"rack_top{tag}", "wall", axis=2, position=z1, extent=((x0, x1), row.band), of_rack=True))
            for edge in (x0, x1):
                panels.append(Panel(f"rack_end{tag}_{edge:g}".replace(".", "_"), "wall", axis=0, position=edge,
                                    extent=(row.band, (floor_z, z1)), of_rack=True))

    # ---- the aisles: read off the fronts, band by band ------------------------
    bands = sorted({row.band for row in rows})
    cold_aisles: list[tuple[float, float]] = []
    hot_aisles: list[tuple[float, float]] = []

    def faces_up(band) -> bool:   # the rows in this band draw from higher y
        return all(row.front_sign < 0 for row in rows if row.band == band)

    def faces_down(band) -> bool:
        return all(row.front_sign > 0 for row in rows if row.band == band)

    first, last = bands[0], bands[-1]
    if first[0] > hall.lo[1] + 1e-6:
        (cold_aisles if faces_down(first) else hot_aisles).append((hall.lo[1], first[0]))
    for a, b in zip(bands, bands[1:]):
        if b[0] <= a[1] + 1e-6:
            continue
        gap = (a[1], b[0])
        if faces_up(a) and faces_down(b):
            cold_aisles.append(gap)
        elif faces_down(a) and faces_up(b):
            hot_aisles.append(gap)
        else:
            # one row faces the gap and the other turns its back: a cold aisle
            # for the one and a hot band for the other; the band is counted
            # by what the air in it is, which the facing row decides
            (cold_aisles if faces_up(a) or faces_down(b) else hot_aisles).append(gap)
    if last[1] < hall.hi[1] - 1e-6:
        (cold_aisles if faces_up(last) else hot_aisles).append((last[1], hall.hi[1]))
    if not cold_aisles or not hot_aisles:
        raise ValueError(f"{path.name}: the cabinets' fronts leave no {'cold' if not cold_aisles else 'hot'} aisle; "
                         f"check the `:+y` / `:-y` attributes")

    # ---- the containment, the blanks, the cage --------------------------------
    contained = None
    if by.get("lid"):
        contained = "cold"
    for s in by.get("lid", []):
        panels.append(s.panel("containment_lid_" + s.id, "wall"))
    for s in by.get("door", []):
        # A door in the plane of a dividing wall is the wall: the mesher builds
        # the wall there already, and a second zone on the same faces is what
        # createBaffles refuses ("Face ... already in faceZone").
        if s.flat_axis == 0 and any(abs(s.lo[0] - x) < 1e-6 for x in divider_x):
            continue
        panels.append(s.panel("containment_door_" + s.id, "wall"))
    for s in walls:
        if s.lo[0] in dividers and s.flat_axis == 0 and full_width(s) and s.hi[2] >= slab - 1e-6:
            continue   # a dividing wall: the mesher builds it from the hall's x face
        if s in plenum_walls:
            continue
        if s.flat_axis == 1 and s.lo[2] >= rack_top - 1e-6 and s.hi[2] >= ceiling_z - 1e-6:
            contained = contained or "hot"
            panels.append(s.panel("containment_wall_" + s.id, "wall"))
        else:
            panels.append(s.panel("containment_side_" + s.id, "wall"))
    for s in by.get("blank", []):
        panels.append(s.panel("blank_" + re.sub(r"[^A-Za-z0-9]", "_", s.id), "wall"))
    cage_solids = by.get("cage", [])
    cage_construction = None
    cage_racks: tuple[str, ...] = ()
    if cage_solids:
        spec.setdefault("cage", {})
        spec["cage"]["enabled"] = True
        cage_construction = str(spec["cage"].get("construction", "mesh"))
        k = cage_k(spec)
        for s in cage_solids:
            panels.append(s.panel("cage_" + s.id, "wall", resistance=k))
        # which cabinets the cage holds: the rectangle its panels and the hall's
        # walls close, and the cabinets whose centre lies in it
        ys = sorted(s.lo[1] for s in cage_solids if s.flat_axis == 1)
        xs = sorted(s.lo[0] for s in cage_solids if s.flat_axis == 0)
        def caged_side(axis: int, lines: list[float], lo: float, hi: float) -> tuple[float, float]:
            """Between two panels; or, with one, the side that holds the cabinets
            -- the fewer of them where both do, because a cage is one customer's
            corner of the hall, not the rest of it."""
            if len(lines) >= 2:
                return lines[0], lines[-1]
            if not lines:
                return lo, hi
            before = [r for r in racks if r.box.hi[axis] <= lines[0] + 1e-6]
            after = [r for r in racks if r.box.lo[axis] >= lines[0] - 1e-6]
            if not after or (before and len(before) <= len(after)):
                return lo, lines[0]
            return lines[0], hi

        cy0, cy1 = caged_side(1, ys, hall.lo[1], hall.hi[1])
        cx0, cx1 = caged_side(0, xs, hall.lo[0], hall.hi[0])
        cage_racks = tuple(r.id for r in racks
                           if cx0 - 1e-6 <= (r.box.lo[0] + r.box.hi[0]) / 2 <= cx1 + 1e-6
                           and cy0 - 1e-6 <= (r.box.lo[1] + r.box.hi[1]) / 2 <= cy1 + 1e-6)

    # ---- the return grilles -----------------------------------------------------
    grille = component_for(spec, "ceiling_return")
    for n, s in enumerate(sorted(by.get("grille", []), key=lambda s: (s.lo[1], s.lo[0])), start=1):
        if s.flat_axis != 2 or abs(s.lo[2] - ceiling_z) > 1e-6:
            raise ValueError(f"{path.name}: {s.name} is not in the false ceiling's plane")
        panels.append(s.panel(f"grille{n}", "opening", resistance=grille.k if grille else None))

    # ---- the units ---------------------------------------------------------------
    units = by.get("unit", [])
    if not units:
        raise ValueError(f"{path.name}: no `unit:` solid; nothing moves the air")

    def gallery_of(s: Solid) -> int:
        for i, g in enumerate(galleries):
            if g.lo[0] - 1e-6 <= s.lo[0] and s.hi[0] <= g.hi[0] + 1e-6:
                return i
        raise ValueError(f"{path.name}: {s.name} does not stand in a gallery")

    units.sort(key=lambda s: (gallery_of(s), s.lo[1]))
    downflow = deck is not None and not any(s.attr for s in units)
    fans: list[Panel] = []
    tags: dict[str, str] = {}
    for n, s in enumerate(units, start=1):
        i = gallery_of(s)
        fname = f"fan{n}"
        tags[s.id] = fname
        if downflow:
            if abs(s.lo[2] - floor_z) > 1e-6:
                raise ValueError(f"{path.name}: {s.name}: a downflow unit stands on the deck (z = {floor_z:g}), "
                                 f"this one starts at {s.lo[2]:g}")
            fans.append(Panel(fname, "fan", axis=2, position=floor_z, extent=((s.lo[0], s.hi[0]), (s.lo[1], s.hi[1])),
                              sign=-1, return_z=s.hi[2]))
        else:
            want = "+x" if i == 0 else "-x"
            if s.attr != want:
                raise ValueError(f"{path.name}: {s.name}: a fan wall in this gallery blows {want} into the hall, "
                                 f"the solid says {s.attr!r}")
            face = s.hi[0] if i == 0 else s.lo[0]
            if abs(face - divider_x[i]) > 1e-6:
                raise ValueError(f"{path.name}: {s.name}: its face is at x = {face:g}, {abs(face - divider_x[i]):.2f} m "
                                 f"off the dividing wall at x = {divider_x[i]:g}; a fan wall is mounted in the wall")
            fans.append(Panel(fname, "fan", axis=0, position=divider_x[i], extent=((s.lo[1], s.hi[1]), (s.lo[2], s.hi[2])),
                              sign=1 if i == 0 else -1))
    panels = fans + panels

    # ---- what the sidecar cannot say and the drawing decided: told to the spec ----
    # so the readers that look at the spec -- the equipment check, the page,
    # the report -- see the room the STL builds
    fan.setdefault("count", len(fans))
    if downflow:
        fan.setdefault("width", units[0].hi[1] - units[0].lo[1])
        fan.setdefault("depth", units[0].hi[0] - units[0].lo[0])
    else:
        fan.setdefault("width", units[0].hi[1] - units[0].lo[1])
        fan.setdefault("depth", units[0].hi[0] - units[0].lo[0])
    fan.setdefault("height", units[0].hi[2] - units[0].lo[2])
    spec["fanwall"] = fan
    if deck is not None:
        spec["floor"] = {**(spec.get("floor") or {}), "enabled": True, "height": floor_z}
    else:
        spec.pop("floor", None)
    spec["containment"] = {"enabled": contained is not None, **({"aisle": contained} if contained else {})}
    if plenum_depth is not None:
        spec["plenum"] = {**(spec.get("plenum") or {}), "enabled": True, "depth": plenum_depth}
    spec["hall"] = {**(spec.get("hall") or {}), "height": slab - floor_z, "ceiling": ceiling_z - floor_z}
    spec["geometry"]["solids"] = len(solids)

    unit = equipment_for(spec)
    asked_off = fan.get("out_of_service")
    if asked_off:
        items = asked_off if isinstance(asked_off, (list, tuple)) else [asked_off]
        fan["out_of_service"] = [tags.get(str(item).strip(), item) for item in items]
    fans_off = out_of_service(fan, fans)

    model = Model(
        name=name,
        domain=domain,
        galleries=galleries,
        hall=hall,
        ceiling_z=ceiling_z,
        cold_aisles=cold_aisles,
        hot_aisles=hot_aisles,
        rows=rows,
        racks=[r for row in rows for r in row.racks],
        panels=panels,
        airflow_m3h=float(fan["airflow_m3h"]) * max(1, len(fans) - len(fans_off)),
        fans_off=fans_off,
        supply_temp_c=supply_c,
        cell_size=cell,
        unit_capacity_kw=float(fan["capacity_kw"]) if "capacity_kw" in fan else None,
        unit_power_kw=float(fan["power_kw"]) if "power_kw" in fan else None,
        altitude_m=altitude,
        equipment=unit,
        blocks=sorted({row.span for row in rows}),
        fan_control=str(fan.get("control", "independent")).strip().lower(),
        fan_static_pa=float(fan["static_pressure_pa"]) if "static_pressure_pa" in fan else None,
        fan_curve=parse_fan_curve(fan.get("curve")),
        grille_free_area=grille.free_area if grille else None,
        grille_size=float((spec.get("grilles") or {}).get("size", 0.6)),
        plenum_depth=plenum_depth,
    )
    model.floor_height = floor_z if deck is not None else None
    model.floor_tile_k = tile.k if (deck is not None and tile) else None
    model.contained = contained
    model.cage = cage_construction
    model.cage_racks = cage_racks
    model.warnings = check_mesh_alignment(model)
    model.warnings.insert(0, (
        f"Geometry read from {path.name}: {len(solids)} solids -- {len(racks)} cabinets in {len(rows)} rows, "
        f"{len(fans)} units, {len(by.get('grille', []))} return grilles, {len(by.get('tile', []))} floor plates, "
        f"{len(supplies)} supply grilles, {len(cage_solids)} cage panels (ADR-131)."
    ))
    if fans_off:
        model.warnings.insert(1, (
            f"Units out of service: {', '.join(fans_off)} -- {len(fans_off)} of {len(fans)} (ADR-129)."
        ))
    model = snap_to_mesh(model)
    _plates_must_fit(model)
    model.alerts = model.hvac_alerts()
    return model
