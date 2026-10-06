# AICFD geometry export — the contract

AICFD meshes a data hall as **one axis-aligned block with baffle surgery**
(blockMesh + createBaffles). Racks are cell zones (boxes with a heat source
and a resistance), everything else is a face zone (a plane that becomes a
wall, an opening, a porous surface or a cooling unit's faces). AICFD's whole
downstream — the thirteen physical checks, the coupled coil solve, the
figures, the report, the page — reads only that list of typed, named,
axis-aligned solids.

The converter's job is to produce that list from the drawing, **already
correct**, so that AICFD verifies it and runs it. AICFD does not repair
geometry. Anything it cannot mesh with an axis-aligned block it refuses by
name; anything ambiguous it refuses rather than guesses.

Two files:

| file | holds |
|---|---|
| `<hall>.stl` | the geometry: one ASCII `solid` per object, named `type:id[:attr]` |
| `<hall>.yaml` | everything that is not geometry: loads, units' selections, setpoints, porous coefficients, site, solver, mesh cell size |

Write both in English (AICFD's convention: every software artefact is in
English; the report it produces is too).

---

## 1. Coordinate conventions

- **Metres**, three decimals are enough.
- **Right-handed, z up.** x and y are the plan axes; which is which does not
  matter to AICFD, but every solid follows the same choice.
- **Origin** at the inner corner of the air volume: min x, min y of the hall
  envelope, and **z = 0 at the bottom of the lowest air space** — the
  structural slab under the raised-floor plenum when there is one, the
  finished floor when there is not. So on a hall with a 1,0 m raised floor
  the deck is a panel at z = 1,0 and the racks stand on it (their box starts
  at z = 1,0).
- **Everything inside the `hall` solid.** Galleries, the underfloor plenum
  and the return plenum above the false ceiling are part of the air volume
  and inside it. Nothing is outside it; nothing touches it from outside.
- **Every coordinate is a multiple of the mesh cell size** the sidecar
  states (`mesh.cell_size`, e.g. 0,2 m). Quantise in the converter, and
  report every quantisation you make (§7). AICFD snaps what is off-grid and
  lists it in the report; a converter that quantises leaves that list empty,
  which is the goal.

---

## 2. The STL

ASCII STL. One `solid … endsolid` block per object. The name after `solid`
is the object's identity and type; it is the only channel STL has for
meaning, so it is mandatory and structured:

```
solid <type>:<id>[:<attr>]
  facet normal nx ny nz
    outer loop
      vertex x y z
      vertex x y z
      vertex x y z
    endloop
  endfacet
  ...
endsolid <type>:<id>[:<attr>]
```

Rules for the name:

- `type` from the table in §3, lower case.
- `id`: letters, digits, `-` and `_`; unique within its type; the names the
  drawing uses (`F2-01`, `CRAC-01`, `c3`), because the report will print them.
- `attr` only where the type requires one (a rack's front, a fan wall's
  blowing direction).
- ASCII only, no spaces.

Rules for the triangles:

- **Every facet normal is one of ±x, ±y, ±z** (|component| = 1 on one axis,
  0 on the others, tolerance 1e-6). A solid with any other normal is refused
  with its name.
- **A box is 12 triangles** (six faces, two each), outward normals,
  right-hand winding. AICFD reads a box as its bounding box and checks the
  triangles are that box; a rotated box, a wedge, a box with a chamfer are
  refused.
- **A panel is 2 triangles**: one rectangle, zero thickness, normal along
  the axis it is flat on. A wall drawn as a thin box in the DWG (a 120 mm
  partition, a 50 mm lid) is collapsed by the converter to its mid-plane and
  reported (§7). AICFD does not accept thin boxes as panels.
- No degenerate triangles, no duplicate vertices within a facet, no solid
  with fewer than 2 or more than 12 triangles.

---

## 3. Types

| type | geometry | attr | what it is | what AICFD does with it |
|---|---|---|---|---|
| `hall` | box | — | the whole air volume: hall, galleries, underfloor plenum, return plenum | the mesh block; exactly one, mandatory |
| `rack:<id>:<front>` | box | `+x` `-x` `+y` `-y`: the direction the **intake face** looks | a cabinet position, loaded or empty | porous cell zone with the load the sidecar gives it; rows, cold and hot aisles are inferred from fronts |
| `unit:<id>[:<dir>]` | box | for a fan wall: the direction it blows into the hall; downflow units take none | a cooling unit's body | intake and supply faces; downflow (top = return, bottom = supply into the plenum) or fan wall (the face towards the hall), from the equipment catalogue's `arrangement` |
| `wall:<id>` | panel | — | a solid internal partition: the gallery dividing walls, row-end closures, containment side walls | adiabatic wall baffle |
| `lid:<id>` | horizontal panel | — | a containment roof over an aisle, at rack-top height | adiabatic wall baffle; which aisle is contained is read from where the lids are |
| `door:<id>` | vertical panel | — | a containment end door | adiabatic wall baffle |
| `deck` | horizontal panel | — | the raised floor, spanning the hall | wall baffle; the plenum is below it; exactly one or none |
| `ceiling` | horizontal panel | — | the false ceiling, spanning the hall | wall baffle; the return plenum is above it; exactly one or none |
| `opening:<id>` | panel | — | a free opening in a wall (gallery to plenum below the deck, gallery to return plenum above the ceiling) | nothing is built; it documents the hole and AICFD checks the wall around it closes |
| `mesh:<id>` | panel | — | woven security mesh across a gallery opening | porous baffle, K from the sidecar component |
| `cage:<id>` | panel | — | a customer cage wall | porous baffle (mesh) or wall (drywall), from the sidecar |
| `grille:<id>` | horizontal panel in the ceiling plane | — | a ceiling return grille | porous baffle, K from the sidecar component |
| `tile:<id>` | horizontal panel in the deck plane | — | a perforated floor plate | porous baffle, K from the sidecar component |
| `blank:<id>` | vertical panel at a rack position | — | a blanking panel where no cabinet stands | adiabatic wall on the row's face |
| `supply:<id>` | vertical panel in the plane of a `wall:plenum_*` | — | a supply grille in the hall's wall, fed from the supply plenum behind it | porous baffle, K from the sidecar's `components.supply_grille` |

Notes on the types:

- **`rack`.** The front attribute is what makes an aisle cold or hot: two
  rows whose fronts face each other enclose a cold aisle, two whose backs
  face each other a hot one. A rack without a front is refused. A position
  that carries no load (an ODF, a future position) is still a `rack` — it
  stands in the row and the air has to get round it — with load 0 in the
  sidecar. A position with **no cabinet** is either nothing (air) or a
  `blank` panel; never an empty rack box.
- **A supply plenum (fan walls behind a double wall).** Where the units are
  mounted in an outer leaf and blow into a cavity, the cavity is the supply
  plenum (AICFD `plenum.enabled`, ADR-058): the dividing `wall:gallery_*`
  stands at the units' leaf (the unit box touches it; the mesh above the
  ceiling is in it), a second `wall:plenum_<side>` panel stands at the hall's
  own wall from the floor to the false ceiling, and the `supply:*` grilles lie
  in that second panel. The `ceiling` spans between the dividing walls, so it
  covers the cavity too and the return passes over it. A downflow hall has
  none of this.
- **`unit`.** The box is the machine's envelope. For a downflow unit AICFD
  takes the top face as the return and the bottom face as the supply into
  the plenum; the box therefore starts at the deck's height. For a fan wall
  the box stands in the gallery against the dividing wall and `:dir` says
  which way it blows; AICFD cuts the intake on the gallery side and the
  supply on the hall side of the dividing wall at the unit's footprint.
  Units are numbered in the report in the order §5 defines.
- **Panels that meet.** A `lid` rests on the rack tops and on the rows' end
  closures; a `door` closes the aisle between the two rows' faces; a `wall`
  that closes a row end runs from deck (or floor) to rack top. AICFD does not
  extend panels to meet each other: a 20 mm gap between a lid and a door is
  a 20 mm leak in the model, and the `sealed_envelope` check will not see it
  as a fault. Make them meet exactly, on the grid.
- **`deck`** is one panel spanning the **whole envelope** -- the hall and
  the galleries -- because the raised floor runs under the units too: a
  downflow unit stands on the deck, draws from the gallery above it and
  discharges through its footprint into the plenum beneath (ADR-076). A
  deck that stopped at the dividing wall would leave the unit's supply and
  return in the same volume. The dividing wall continues below the deck,
  where the gallery's plenum opens into the hall's through a `mesh` (or
  `opening`) panel. **`ceiling`** spans the hall's inner extent between the
  dividing walls only: the galleries are open up to the slab, and the return
  plenum over the hall reaches them through the `mesh`/`opening` panel in
  the dividing wall above the ceiling. Tiles are cut into the deck plane at
  their own coordinates; grilles into the ceiling plane; the units' supply
  faces into the deck at their footprint. The converter writes the deck and
  the ceiling as one rectangle each; AICFD subtracts the cut-outs.
- **A row that faces a cold aisle alone** (no facing row) closes the far
  side of that aisle with a vertical `wall` panel from the deck to the lid,
  so the contained aisle is a box. The converter reports it as a correction.

---

## 4. Consistency rules the converter enforces

AICFD refuses a file that breaks any of these, naming the solid. The
converter checks them first, so the refusal never reaches the engineer.

1. Exactly one `hall`; every other solid's bounding box lies inside it
   (tolerance 1e-6), and nothing coincides with the hall's own faces except
   `deck`, `ceiling` and panels that end on the hall walls.
2. Every coordinate of every vertex is a multiple of `mesh.cell_size`
   (per axis, if the cell is anisotropic), to 1e-6.
3. Racks do not overlap racks, units, or panels; two racks that touch share
   a face exactly (no overlap, no gap smaller than a cell).
4. Every rack has a front; every rack's front face is free — no other solid
   lies against it. Its back face is either free (hot aisle) or against a
   wall (a row against the perimeter is allowed but is what the drawing
   says, not a default).
5. Racks that touch in a line, with the same front and the same height,
   form a row. A row is straight and its cabinets are contiguous or
   separated by `blank` panels or by air; a cabinet on its own is a row of
   one and is reported as such.
6. Every unit lies in a gallery: outside the region bounded by the dividing
   walls, with (fan wall) one face against a dividing wall or (downflow) its
   bottom face at the deck's height and above the plenum.
7. `deck` (if present) is at one height, spans the whole envelope, and every
   rack's box starts at that height; every `tile` lies in the deck's plane;
   every downflow unit's bottom face is at that height.
8. `ceiling` (if present) is at one height above every rack and every unit;
   every `grille` lies in its plane; every `lid` is below it.
9. Every `lid` spans an aisle from one row's face to the facing row's face
   (cold-aisle containment) or from one row's back to the other's (hot-aisle
   containment), at the rows' top height; a hall contains one kind of aisle
   or none, never both.
10. Every `door` is vertical, in the plane of a row end, and spans the aisle
    a lid covers, from deck (or floor) to lid.
11. Every `opening` and `mesh` panel lies in the plane of a `wall` and
    inside that wall's extent.
12. Panels do not cross racks or units; panels of different types do not
    overlap in area (they may share edges).
13. Names are unique per type; every rack named in the sidecar's loads
    exists in the STL; every unit the sidecar's `out_of_service` names
    exists.

---

## 5. Ordering and naming that the report will use

- **Units** are numbered `1…N` in the report and tagged with the equipment's
  noun (`CRAC-01`, `CRAH-01`, `FW-01`). Order: by gallery (the gallery at
  lower x first, then higher x; if galleries are along y, lower y first),
  then by position along the gallery wall, ascending. Name the units in the
  STL to match the drawing (`CRAC-01` …) and lay them out so this order and
  the drawing's agree; if they cannot, the drawing's names win and the
  report prints them.
- **Racks** keep the drawing's names. The recommended pattern is
  `<row>-<position>` (`F2-01`), rows lettered/numbered along the axis
  perpendicular to the rows, positions along the row. AICFD groups racks
  into rows by geometry (§4.5), not by name; the name is what the report
  prints beside every temperature.
- **Aisles and pods** are not named in the STL; AICFD names them from the
  rows they lie between.

---

## 6. The sidecar YAML

Everything a drawing cannot say. It is AICFD's case file **without** the
room-building keys, plus `geometry`.

```yaml
name: dh04-1mw                 # the case name: the results folder and the report's slug
geometry:
  file: dh04-1mw.stl           # relative to this file
  source: "DH04 fit-out, sheet M-102 rev C, 2026-09-12"   # printed in the basis of design

site:
  altitude_m: 0.0

racks:
  load_kw: 4.7                 # default for every rack the list below does not name
  airflow_cfm_per_kw: 158      # the design reference, or the client's 170
  loads:                       # per rack, by the STL's rack ids
    F1-01: 20
    F1-10: 0                   # an ODF: stands in the row, makes no heat
    F1-11: 8.33
  # no size, per_row, count, blocks, widths: they are in the STL

fanwall:                       # the cooling units, whatever they are called
  model: P3100DA               # from AICFD's equipment library
  airflow_m3h: 27500           # per unit, as operated
  capacity_kw: 100.5
  power_kw: 7.5
  supply_temp_c: 22.0          # the setpoint the plant is asked to hold
  control: team                # or independent
  static_pressure_pa: 50       # only where the sheet does not carry it
  out_of_service: []           # the failure scenario: [1] or [CRAC-01, CRAC-08]
  # no count, width, height, depth, offset, pitch: they are in the STL

components:                    # the porous surfaces' loss coefficients
  cage: cage-mesh-13           # applies to every cage:* panel
  ceiling_return: ceiling-return-600-open   # every grille:*
  floor_tile: floor-tile-600   # every tile:*
  gallery_mesh: gallery-mesh-13             # every mesh:*
  supply_grille: supply-grille-2000         # every supply:* (plenum wall)
cage:
  construction: mesh           # mesh | drywall, for the cage:* panels

mesh:
  cell_size: [0.2, 0.2, 0.2]   # the grid every STL coordinate is on

solver:
  max_iterations: 2000
  sensor_interval: 100
  processors: 16

figures:                       # the drawings the case was built from (ADR-130):
  - {file: hall-04.png, caption: "The layout as read from the drawing"}
  - {file: hall-04.3d.png, caption: "The 3D model"}
  - {file: hall-04.sections.png, caption: "Sections: heights and closures"}
```

Keys the sidecar must **not** contain (AICFD refuses a geometry case that
carries them, because they would contradict the file): `pods`, `aisles`,
`gallery`, `hall.height`, `hall.ceiling`, `hall.size`, `racks.size`,
`racks.per_row`, `racks.count`, `racks.blocks`, `fanwall.count`,
`fanwall.width/height/depth/offset/pitch`, `floor.height`,
`floor.tiles_per_rack/tiles_across`, `grilles.*`, `containment.*`,
`cage.pods/sides/aisle/clearance/height/roof`, `plenum.*`.

---

## 7. What the converter reports with the files

A short text (`<hall>.export.txt`) the engineer reads before running:

- source drawing, sheet, revision, date, units and scale used;
- the cell size the coordinates were quantised to, and **every quantisation
  that moved a coordinate by more than 1 mm**, as "rack F2-01 x 6.583 →
  6.600 (+17 mm)";
- every thin box collapsed to a panel, with the thickness dropped;
- counts: racks (and how many at zero load), rows, units, lids, doors,
  walls, tiles, grilles, cage panels, openings;
- the heights: deck, rack top, lids, ceiling, hall;
- anything in the drawing that was **left out** because it is not one of
  the types (cable trays, columns, PDUs, ramps, furniture) — by name, so the
  omission is a decision on the record and not an accident.

---

## 8. Self-check before handing over

Run these on the produced files; each maps to a refusal AICFD would give.

- [ ] one `hall`, everything inside it
- [ ] every facet normal on an axis; boxes 12 facets, panels 2
- [ ] every coordinate on the grid of `mesh.cell_size`
- [ ] every rack has a front; no rack overlaps anything
- [ ] rows are straight and contiguous; a single-cabinet row is intended
- [ ] every unit is in a gallery; downflow units start at the deck
- [ ] the deck spans the envelope and the ceiling the hall; tiles in the deck plane, grilles in the ceiling plane
- [ ] lids at rack-top height spanning face to face (or back to back); doors close them
- [ ] no gaps between panels that are meant to meet
- [ ] the sidecar names only racks and units that exist, and no room-building keys
- [ ] the export report lists every quantisation, collapse and omission

Then, in AICFD (once the geometry path is in place):

```
aicfd geometry check dh04-1mw.yaml
```

prints what AICFD understood — "156 racks in 12 rows, 7 contained cold
aisles, 14 downflow units in 2 galleries, deck at 1,00 m, ceiling at 5,00 m,
2 solids refused: …" — and the engineer compares it with the drawing before
any mesh is built.

---

## 9. Worked minimum

A hall of two facing rows of two cabinets, cold aisle contained, two
downflow units in one gallery, raised floor. Cell 0,2 m. Only the names and
one box are written out; the rest follows the same pattern.

```
solid hall                      # 0..12.0 x 0..8.0 x 0..6.6
solid deck                      # z = 1.0, x 0..12.0, y 0..8.0 (the whole envelope: the units stand on it)
solid ceiling                   # z = 5.0, x 4.0..12.0, y 0..8.0 (the hall only; the gallery is x 0..4.0, open to the slab)
solid wall:gallery              # x = 4.0 plane, y 0..8.0, z 0..6.6, minus the openings below
solid mesh:gallery_plenum       # x = 4.0, y 0..8.0, z 0..1.0  (the wall below the deck is woven mesh, ADR-076)
solid mesh:gallery_return       # x = 4.0, y 0..8.0, z 5.0..6.6 (the wall above the ceiling is mesh, ADR-048)
solid unit:CRAC-01              # box x 0.6..3.2, y 1.0..1.8, z 1.0..3.0 (downflow, no attr)
solid unit:CRAC-02              # box x 0.6..3.2, y 5.0..5.8, z 1.0..3.0
solid rack:A-01:+y              # box x 6.0..6.8, y 4.4..5.6, z 1.0..3.2 ; front looks +y
solid rack:A-02:+y              # box x 6.8..7.6, y 4.4..5.6, z 1.0..3.2
solid rack:B-01:-y              # box x 6.0..6.8, y 6.8..8.0, z 1.0..3.2 ; front looks -y
solid rack:B-02:-y              # box x 6.8..7.6, y 6.8..8.0, z 1.0..3.2
solid lid:c1                    # z = 3.2, x 6.0..7.6, y 5.6..6.8  (over the cold aisle)
solid door:c1_w                 # x = 6.0 plane, y 5.6..6.8, z 1.0..3.2
solid door:c1_e                 # x = 7.6 plane, y 5.6..6.8, z 1.0..3.2
solid tile:A-01                 # z = 1.0, x 6.0..6.6, y 5.6..6.2
solid tile:A-02 ... tile:B-02
solid grille:h1                 # z = 5.0 over the hot side, x 6.0..7.6, y 3.8..4.4
```

The box for `rack:A-01:+y`, in full:

```
solid rack:A-01:+y
  facet normal 0 -1 0
    outer loop
      vertex 6.0 4.4 1.0
      vertex 6.8 4.4 3.2
      vertex 6.8 4.4 1.0
    endloop
  endfacet
  facet normal 0 -1 0
    outer loop
      vertex 6.0 4.4 1.0
      vertex 6.0 4.4 3.2
      vertex 6.8 4.4 3.2
    endloop
  endfacet
  ... (the other five faces, two facets each, normals +y, -x, +x, -z, +z)
endsolid rack:A-01:+y
```

Sidecar for it: `racks.load_kw: 10`, `fanwall.model`, `airflow_m3h`,
`supply_temp_c`, the four components, `mesh.cell_size: [0.2, 0.2, 0.2]`.

---

## 10. Out of scope, on purpose

- Rotated, curved or sloping geometry — AICFD's block mesh cannot carry it;
  rectify in the converter or it is refused.
- Obstructions that are not one of the types (a column, a duct, a cable
  tray, a PDU): left out, and listed by name in the export report so the
  omission is a decision on the record. A later revision of this contract
  may add an `obstacle:<id>` box type; until it does, they are out.
- Loads, unit selections, control, water temperatures: never in the STL,
  always in the sidecar; the drawing does not know them.
- The conversion itself (layers, blocks, scale, units of the DWG): the
  converter's own concern; this contract is only about what comes out.


## The package (AICFD ADR-135)

What is handed over is `<name>.aicfd.zip`, not the loose files:

    manifest.json            {"format": "aicfd-package/1", "project", "scenarios",
                              "geometry": {"file": "geometry.stl", "sha256"}, "source",
                              "written_by", "created", "grid_alerts"}
    geometry.stl             the contract STL above, renamed
    figures/<name>.png ...   the plan, the 3D view and the sections
    scenarios/<name>.yaml    the sidecar, with geometry.file: geometry.stl (and its sha256)
                             and figures pointing at figures/
    source/answers.yaml      the answers the run was given
    source/export.txt        the export report

Nothing else may be in it; AICFD refuses an unknown member by name. It checks
the hash, builds every scenario against the geometry, and only then writes
`cases/<project>/`; a scenario whose STL is later replaced by hand is refused.
