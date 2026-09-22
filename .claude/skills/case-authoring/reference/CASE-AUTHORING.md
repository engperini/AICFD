# Filling a case from a real project

**Who this is for.** An AI agent that has been handed a real data hall project
— drawings, a PDF of the mechanical specification, a fan wall selection, a rack
schedule, an RFP — and has to produce one file: `cases/<name>.yaml`, the input
AICFD builds a mesh and a solve from.

It is a manual, not a schema dump. The schema is small; the hard part is the
translation, and the translation is where a case goes quietly wrong. A number
that is plausible, in range, and from the wrong document produces a clean run,
twelve green checks and an answer about a building that does not exist.

Everything the software says, writes and draws is in English (ADR-026). Write
the case and its comments in English too, whatever language the source
documents are in.

---

## 1. The rule that outranks the rest

**Never invent a number.** Every value in the case is one of exactly three
things, and the comment beside it says which:

| | what it looks like |
|---|---|
| **measured** | it is on a drawing or in a table. Cite the document. `height: 7.5  # 7.60 m on the section, snapped to the 0.25 m mesh` |
| **selected** | it is a manufacturer's figure at a stated duty. Cite the selection, including its conditions. `capacity_kw: 432.6  # NSCC at 750 m, 100 Pa ESP, 34.0 C entering air, 18 C water` |
| **assumed** | nobody told you. Say so, say why this value, and say what it would take to replace it. `airflow_cfm_per_kw: 158  # design reference; no rack datasheet in the pack` |

There is no fourth kind. A number with no comment is indistinguishable from a
number someone made up, and six months later nobody — including you — can tell
them apart.

**When a document and the geometry disagree, the document wins and the comment
records the gap.** The 53 m hall whose derived length comes out 51.0 m is a
51.0 m model of a 53 m hall, and the case says so in one line. Silently editing
a clearance until the arithmetic lands on 53 m makes a model that matches the
drawing and misrepresents every aisle in it.

---

## 2. What you state, and what the software derives

This is the single most common authoring mistake, so it comes before the key
reference.

**A POD** (no `pods:` key) is one row of racks in a box you dimension yourself.
You state `hall.size: [x, y, z]`.

**A data hall** (`pods: N`) has **no `hall.size`**. Its length and width are
*derived* from the parts, and typing a size is not an error the parser catches
— it is ignored. The arithmetic, exactly as `_hall_layout` does it:

```
block_length  = sum of the position widths in the typical row       (each snapped to cell x)
row_length    = blocks x block_length + (blocks - 1) x aisles.transverse
hall_length   = 2 x aisles.perimeter + row_length + sides x plenum.depth
total_x       = gallery.sides x gallery.depth + hall_length          (then snapped to cell x)
total_y       = 2 x aisles.perimeter + pods x (2 x rack_depth + aisles.hot) + (pods - 1) x aisles.cold
                                                                     (then snapped to cell y)
```

**The last step is the mesh.** The overall size is snapped to the cell, so a
part that falls between grid lines moves the total by up to half a cell: a
4,77 m gallery on 0,20 m cells makes a 22,34 m room the mesh builds at 22,40 m.
Derive the nominal number, then snap it — and the software says which plane it
moved, by name, in the summary (ADR-074, ADR-114).

Read those backwards and they are your extraction procedure. Given a drawing
with a measured hall, you solve for the inputs:

- **x** is set by `racks.per_row` and the cabinet width. 22 cabinets of 0.6 m
  is a 13.2 m block; two blocks with a 3.0 m transverse aisle is 29.4 m of row;
  1.8 m of perimeter at each end makes a 33.0 m hall inside the galleries.
- **y** is set by `pods` and the aisle widths. Five pods of (1.2 + 1.2 + 1.2 =
  3.6 m of rack and hot aisle) with 2.7 m cold aisles between them and 1.8 m of
  perimeter is 32.4 m.
- The **cold aisles are between pods**, and the perimeter clearance is what
  stands in for the outermost one. A hall drawn with a full cold aisle against
  each end wall is a hall whose `aisles.perimeter` is that aisle's width, not a
  code clearance.
- A **cage wall standing in a cold aisle widens that one boundary**: the
  clearance is a gap on both faces of the wall, so the aisle there is at least
  `2 x cage.clearance` (and at least `aisles.cold`), whatever `cage.aisle`
  says. Contain the cold aisle and it is wider still in use, because each side
  closes with a side of its own and the rest of the clearance is walkway
  (ADR-110). Add the difference to `total_y` for every boundary a cage wall
  stands in.

`pods` counts **pairs of rows facing a contained hot aisle**. A hall with ten
rack rows is `pods: 5`. Counting rows instead of pods builds a hall twice the
size, passes every check, and is wrong.

---

## 3. The extraction order

Work in this order. Each step needs the one before it, and doing them out of
order is how a mesh gets chosen before the geometry it has to resolve is known.

1. **Arrangement** (§4). POD or hall; galleries at one end or both; how the
   supply reaches the aisle; whether the hot aisle is contained. This is read
   off the plan and the section, not off a table.
2. **The room**, in metres: slab height, false ceiling height, gallery depth.
   From the sections.
3. **The racks**: cabinet size, how many per row, how many rows, what each one
   carries. From the layout drawing and the rack schedule or the power table.
4. **The plant**: which unit, how many, and the *selection* behind its numbers.
   From the manufacturer's selection sheet — never from a catalogue page.
5. **The perforated surfaces**: ceiling grilles, floor plates, supply grilles,
   the mesh over the plenum opening. From the grille schedule and its
   datasheets.
6. **The mesh** (§7), chosen so every plane above lands on a cell face.
7. **The solver**: iterations, cores.
8. **Build it, read the warnings, reconcile** (§10), and only then run.

---

## 4. The arrangement decision tree

Four independent decisions. Get them from the drawings; each one changes which
keys exist.

**How many galleries.** `gallery.sides: 1` (default) is a mechanical gallery at
one end. `2` puts one at each end and feeds each rack block from the end
nearest to it. The return plenum above the false ceiling stays a **single
volume** either way — it spans the whole hall and feeds both galleries
(ADR-027). A hall longer than about 40 m driven from one end is asking one
gallery to push air the whole length; if the drawing shows plant rooms at both
ends, it is `sides: 2`.

**How the supply reaches the cold aisle.** Exactly one of:

| | keys | what it is |
|---|---|---|
| direct | *(nothing)* | the unit blows through the dividing wall straight at whatever it faces |
| supply plenum | `plenum.enabled: true`, `plenum.depth`, `plenum.grille.*` | the wall doubled, the cavity pressurised, grilles in the inner leaf deciding where the air leaves (ADR-058) |
| mesh across the opening | `plenum.as_mesh: true` | no plenum, a 13 x 13 mm woven mesh over the opening. For a hall already built that cannot grow (ADR-060) |
| raised floor | `floor.enabled: true`, `floor.height`, `floor.tiles_per_rack` | downflow units blowing under an access floor, plates in the cold aisle (ADR-076) |

`floor` and `plenum` together are refused at build time, with a message saying
so: they are two ways of getting the same air from the same units into the same
aisles, and a case asking for both has not chosen.

**Which aisle is contained.** `containment.enabled: true` closes an aisle, and
`containment.aisle` says which — they are two different rooms, not two names
for one:

| | what is built | where the return grille is | needs |
|---|---|---|---|
| **hot** (the default) | walls from the rack tops to the false ceiling, doors at each end: a chimney | at the top of the chimney | nothing |
| **cold** | a lid over the cold aisle at rack height, doors up to it | over the **hot** aisle, which is now open to the room | a raised floor |

With a cold aisle contained, the room itself is the hot volume. A sealed cold
aisle's only way in is the floor, so `cold` without `floor.enabled` is refused:
a supply blown into the room outside the aisle cannot reach it, and the racks
would draw from a closed box.

An uncontained aisle is a different room thermally again, not a small
correction.

**Whether a customer cage encloses the rows.** A cage is a security boundary
inside the hall, and the two ways it is built are two different rooms:

| | keys | what the air does |
|---|---|---|
| no cage | *(nothing)* | the hall is one volume |
| mesh | `cage.enabled: true`, `cage.construction: mesh` | crosses it, paying K once going in on the cold side and again coming out on the hot one |
| drywall | `cage.construction: drywall`, `cage.height` below the ceiling | cannot cross it at all, so it goes over the top |

**Where the cage stands** is `cage.pods` and `cage.sides`. Three arrangements,
all from the same two keys:

| | keys | |
|---|---|---|
| in the middle of the hall | *(neither)* | four walls round every rack |
| over some rows only | `pods: [1, 2]` | four walls round pods 1 and 2; the rest of the hall is outside |
| against the wall, dividing the hall | `pods: [1, 2]`, `sides: [right]` | one wall, spanning the room, with the hall closing the other three |

A clearance that reaches the hall wall drops that side and says so in a
warning — a cage in a corner has two walls, not four, and a wall built on the
hall wall is not a boundary at all.

Measured on a POD with a cage round its row, everything else identical: the
drywall cage costs 3.5% more fan rise and puts the peak speed up 10.4%
(ADR-096). A drywall cage closed at the top — full height, or with a roof —
is **refused**: the supply is outside it and the ceiling grilles over its own
hot aisles still let air out, so the racks have an exit and no entry.

**Whether the units run independently or as a team.** `fanwall.control: team`
makes the units sharing a gallery all run to the worst return any of them sees,
which is what a networked control system does (ADR-064). Default is
`independent`. Read this off the controls specification, not off the unit.

---

## 5. Key reference

Ranges are the ones the software enforces; a value outside them is refused with
a message naming the key. `vector3` is `[x, y, z]`. `cell_size` takes one value
or three.

### Identity and site

| key | type | range | where the number comes from |
|---|---|---|---|
| `name` | str | | the case's own name; must match the file name |
| `site.altitude_m` | float | 0 to 5000 | the site's elevation. It sets air density, so it changes every mass flow in the model. The fan wall selection states the elevation it was taken at — use that one, and if it differs from the site's, that disagreement is the finding |

### The room

| key | type | range | where the number comes from |
|---|---|---|---|
| `pods` | int | 1 to 60 | pairs of rows facing a contained hot aisle. Present = data hall, absent = POD |
| `hall.size` | vector3 | 0.5 to 500 | **POD only.** Length x, width y, floor to slab z. A hall derives these (§2) |
| `hall.height` | float | 2.5 to 30 | floor to slab, from the section |
| `hall.ceiling` | float | 2 to 20 | the false ceiling. Above it is the return plenum. Must be below `hall.height` and above the racks |
| `gallery.depth` | float | 0.5 to 20 | the mechanical gallery behind the units, from the plan. It has no false ceiling — that is how the return air reaches the units |
| `gallery.sides` | int | 1 to 2 | §4 |

### Aisles and clearances

| key | type | range | where the number comes from |
|---|---|---|---|
| `aisles.cold` | float | 0.6 to 10 | between pods, the face the units supply |
| `aisles.hot` | float | 0.6 to 10 | between the two rows of a pod, contained |
| `aisles.perimeter` | float | 0.6 to 20 | clearance round the rows on both axes. It is also the outermost cold aisle |
| `aisles.transverse` | float | 0.6 to 20 | the aisle between rack blocks, when `racks.blocks` is 2 or more; ignored when it is 1, and defaults to `aisles.cold`. It is what makes each block a containment volume of its own |

### Racks

| key | type | range | where the number comes from |
|---|---|---|---|
| `racks.count` | int | 1 to 60 | **POD only**: racks in the single row |
| `racks.per_row` | int | 1 to 100 | **hall only**: cabinets per row **per block**. A row of two blocks holds twice this |
| `racks.blocks` | int | 1 to 6 | rows cut into blocks along their length |
| `racks.load_kw` | float | 0.1 to 200 | the typical load per position. Per-position loads override it |
| `racks.size` | vector3 | 0.1 to 3 | width x, depth y, height z. Take it from the cabinet schedule, or name a `racks.type` and omit this |
| `racks.type` | str | | a file in `racks/`, which supplies size and a default load (ADR-075). Prefer this to typing numbers |
| `racks.offset_x` | float | 0 to 50 | **POD only**: where the row starts, from the gallery wall |
| `racks.airflow_cfm_per_kw` | float | 20 to 400 | what a cabinet draws per kW. 158 is the design reference (ADR-023); a rack datasheet beats it |
| `racks.row` | list | | the typical row, position by position (§6) |
| `racks.loads` | map | 0 to 200 each | per-position loads, by rack id (§6) |
| `racks.widths` | map | 0.1 to 3 each | per-position widths, by rack id (§6) |
| `racks.blanks` | list or map | | which positions are blanking panels (§6) |

### The plant

| key | type | range | where the number comes from |
|---|---|---|---|
| `fanwall.model` | str | | a file in `equipment/`, also a picker on the model page. Naming it does not override anything you type — it lets the result be judged against the capacity the coil **has** at the return air this hall produces, instead of the one catalogue figure (ADR-036). The unit must suit the room: a `downflow` unit needs `floor.enabled`, a `fanwall` one needs no raised floor, and the build refuses the mismatch by name (ADR-092) |
| `fanwall.count` | int | 1 to 200 | units installed. With two galleries the split is automatic and an odd count gives the extra to the first |
| `fanwall.airflow_m3h` | float | 100 to 500000 | **per unit**, as a datasheet gives it. If the plant is N+2, the installed units share the flow the N units would deliver — state the shared figure and say so |
| `fanwall.capacity_kw` | float | 0.1 to 5000 | net sensible per unit (NSCC), gross less the fan power returned to the air |
| `fanwall.power_kw` | float | 0 to 500 | electrical input per unit |
| `fanwall.width` | float | 0.3 to 20 | unit width, from the dimensional drawing |
| `fanwall.height` | float | 0.5 to 20 | unit height |
| `fanwall.depth` | float | 0.1 to 10 | how far the unit reaches into the gallery. Drawn, not meshed (ADR-046) |
| `fanwall.supply_temp_c` | float | -10 to 40 | supply air at the selection point |
| `fanwall.static_pressure_pa` | float | 0 to 2000 | external static pressure the unit was selected at |
| `fanwall.control` | `team` or `independent` | | §4 |
| `fanwall.curve` | list of `[m3h, Pa]` | | the unit's P-Q curve. Anchor it on the selection point |

### Supply plenum, mesh, raised floor

| key | type | range | where the number comes from |
|---|---|---|---|
| `plenum.enabled` | bool | | §4 |
| `plenum.depth` | float | 0.3 to 6 | the cavity between the two leaves. Default 1.2 |
| `plenum.grille.width` | float | 0.3 to 12 | one supply grille's width. Default 2.0 |
| `plenum.grille.height` | float | 0.3 to 6 | one supply grille's height. Defaults to the rack height — air let in above the racks is air the racks never see |
| `plenum.closed` | list of names | | grilles that are shut in this study |
| `plenum.mesh_loss_coefficient` | float | | overrides the component's K for the mesh across the opening |
| `plenum.as_mesh` | bool | | §4 |
| `floor.enabled` | bool | | §4 |
| `floor.height` | float | 0.2 to 3 | the access floor's depth. The building grows by it; the room above is unchanged |
| `floor.tiles_per_rack` | int | 0 to 10 | perforated plates in the cold aisle per cabinet, laid outward from the cabinet face. **Two rows face the same cold aisle**, so an aisle of width W holds W/0.6 rows of plate between them — not that many for each. Asking for more is refused by name (ADR-095) |
| `floor.tiles_across` | int | 1 to 20 | plate rows **across the aisle**, counting both sides of it — the way a floor is described, and the only way to an odd number: one plate each side of a 1,20 m aisle is two, two each is four, three needs this key. The two rows split them, the odd one going to the row nearer `y = 0` (ADR-106) |

### Perforated surfaces

| key | type | range | where the number comes from |
|---|---|---|---|
| `grilles.size` | float | 0.1 to 3 | one ceiling return grille, from the ceiling grid |
| `grilles.count` | int | 1 to 200 | **POD only**: 600 mm modules along the row |
| `grilles.coverage` | float | 0.05 to 1 | **hall only**: the fraction of each hot-aisle block the grille strip covers |
| `grilles.across` | int | 1 to 20 | 600 mm modules **across** the hot aisle — three of them fill a 1,80 m aisle. Counting switches that direction from the aisle's full width to the module grid |
| `grilles.along` | int | 1 to 200 | **hall only**: 600 mm modules **along** each block, instead of the `coverage` fraction. Counted modules have to land on the mesh, so the cell on that axis must divide the module — 0,60, 0,30, 0,20, 0,15 m — and a case that cannot is refused by name (ADR-106) |
| `grilles.free_area` | float | 0.05 to 1 | open area fraction, from the grille datasheet |
| `grilles.loss_coefficient` | float | 0 to 100 | K from the grille datasheet (ADR-020) |
| `components.ceiling_return` | str | | a file in `components/` with role `ceiling_return`, instead of the two numbers above |
| `components.supply_grille` | str | | role `supply_grille` |
| `components.floor_tile` | str | | role `floor_tile` |
| `components.gallery_mesh` | str | | role `gallery_mesh`: the mesh over the plenum opening, which every cubic metre passes through once (ADR-048) |
| `containment.enabled` | bool | | §4 |
| `containment.aisle` | `hot` or `cold` | | which aisle is closed. `hot` is a chimney from the rack tops to the false ceiling with the return grille on top; `cold` is a lid at rack height, the racks discharging into a hot room. Cold needs a raised floor — a sealed cold aisle's only way in is the floor (ADR-100) |
| `cage.enabled` | bool | | §4 |
| `cage.construction` | `mesh` or `drywall` | | §4. The answer, not a detail of the drawing |
| `cage.aisle` | float | 0.6 to 10 | the cold aisle the cage wall stands in. It can only WIDEN what the clearance needs: the aisle is always at least `2 x cage.clearance` and at least `aisles.cold`, because the clearance is a gap on BOTH faces of the wall, and a case that states less is widened with a note (ADR-109). The extra of a wider aisle goes to the row outside the cage |
| `cage.clearance` | float | 0.1 to 10 | from the outermost cabinet faces to the cage wall. A side whose plane reaches the hall wall is **not built** — the room closes it — and the rectangle is clipped to the room so the walls that *are* built run wall to wall |
| `cage.pods` | list of int | | **YAML only.** Which PODs are inside, counted from 1 along the hall and contiguous. Pod 2 is rows F3 and F4. Omitted, the cage encloses every rack |
| `cage.sides` | list | `near`, `far`, `left`, `right` | **YAML only.** Which walls to build. `near`/`far` close the row ends (normal to x), `left`/`right` run along the rows. Omitted, every side the room does not already close is built |
| `cage.height` | float | 1 to 20 | top of the cage. Omitted, it runs to the false ceiling |
| `cage.roof` | bool | | close the top with the same construction |
| `cage.loss_coefficient` | float | | K for a mesh cage, where the case states its own instead of the component's |
| `components.cage` | str | | role `cage`: which mesh, when the construction is `mesh` |

### Mesh and solver

| key | type | range | where the number comes from |
|---|---|---|---|
| `mesh.cell_size` | cell_size | 0.02 to 1 | §7. One value or `[x, y, z]` |
| `solver.max_iterations` | int | 10 to 20000 | a cap, not a target |
| `solver.residual_tolerance` | float | | 1.0e-4 unless there is a reason |
| `solver.sensor_interval` | int | 10 to 5000 | how often the run reports partial results |
| `solver.warm_start` | bool | | seed the loop's topology (ADR-019). Leave it true |
| `solver.processors` | int | 1 to 64 | cores. More than the machine has is refused by MPI, not by AICFD |
| `solver.couple` | bool | | re-solve the coil against the return air the room actually produces. True by default |
| `solver.coupling_passes` | int | | how many times, at most |
| `solver.coupling_segment` | int | | iterations per coupling pass |

---

## 6. Positions: when a row is not uniform

A real hall is not `N` identical cabinets. It has blanking panels where the
build stopped, network cabinets at the ends, a 300 mm ODF frame, and an empty
position reserved for phase 2. There are two levels of detail, and they
compose.

**The typical row** — `racks.row`, a list, one entry per position. **Its length
governs the row**; do not also state `per_row`, because the two will disagree.
Every key in an entry is optional:

```yaml
racks:
  row:
    - {}                                  # a standard cabinet
    - {load_kw: 16}                       # a cabinet at its own load
    - {type: liquid-network-800}          # a cabinet from racks/
    - {width: 0.8}                        # a wider cabinet
    - {blank: true, width: 0.3}           # a blanking panel
```

A position takes `type`, `blank`, `width` and `load_kw` and nothing else; an
unknown key is refused by name.

**A worked one.** `cases/hall-cage-1mw.yaml` carries a customer's real rack
schedule this way: 52 positions in the cage, 20 kW cabinets at the row ends,
10 kW through the middle, five ODF positions at zero and twelve future
positions at 8,33 kW — 509,96 kW in all — against the rest of the hall spread
evenly. Read it beside the drawing it came from: every number in it is one a
reader can find on that sheet.

**Per-position overrides** — by **rack id**, for the ones that differ from the
typical row. The id is built by the layout, and you must use its exact form:

| | id |
|---|---|
| POD | `R1`, `R2`, … in row `F1` |
| hall, one block | `F1-01`, `F1-02`, … `F2-01`, … — rows numbered from `y = 0` |
| hall, blocks | `F1B1-01`, `F1B2-01`, … — row, block, then position |

```yaml
racks:
  loads:                      # kW, 0 to 200
    F1B1-07: 0                # reserved for phase 2
    F1B1-08: 32.0             # the high-density pair
  widths:
    F1B1-12: 0.3              # the ODF frame
  blanks:
    - F1B1-19                 # a list of ids, all blanked
    - F1B1-20
```

`blanks` also takes a mapping, for the case where the typical row blanks a
position that is a cabinet here after all: `{F1B1-19: false}`.

**A width narrower than the x cell is snapped up to it.** A 300 mm ODF frame
in a hall meshed at 0.60 m along x becomes a 600 mm position — the block grows
by 300 mm and the row is no longer the row on the drawing. The build says so,
names the cabinet, and tells you which cell would carry it exactly (ADR-074):

```
cabinets 0.300 m wide fall between 0.6 m grid lines; the mesh uses 0.60 m
(+300 mm). An x cell of 0.3 m would carry it exactly, at 2x the cells across
the row.
```

Either halve the x cell or accept the snap and say so in the comment. What you
must not do is state the narrow width, not read the warning, and report a row
length the building does not have.

A typo in a rack id is the failure mode to watch for. It is **not** an error —
the id simply matches nothing and that position quietly keeps the typical
load. After building, read the model page or the summary and confirm the total
load is the one the power table says.

**Blanking panels are solid, and they are meshed** (ADR-081). A blank is a wall
across the row, not a rack at zero kW. A position that is an empty cabinet
still breathing is `load_kw: 0`, and the two are different rooms: one lets air
through and one does not.

---

## 7. Choosing the mesh

The mesh is not a quality dial. **Every plane in the model has to land on a
cell face**, and the cell size is what decides whether it does. Get this wrong
and the software snaps the geometry for you, warns, and builds a room slightly
unlike the one you specified.

Set each axis from what it has to resolve:

| axis | what lands on it | typical |
|---|---|---|
| **x** | the cabinet width, the block length, the transverse aisle, the fan wall width | 0.60 m (one rack per cell) down to 0.20 m |
| **y** | the rack depth, the hot aisle, the cold aisle, the perimeter | 0.30 m, or 0.20 m for detail |
| **z** | the false ceiling, the slab, the rack height, the fan wall height, the raised floor deck | 0.25 m, or 0.10 m for a POD |

The procedure:

1. List every dimension on that axis from §2 and §5.
2. Pick the largest cell that divides all of them. Where nothing does, pick the
   cell that divides the ones that matter most — the ceiling and the rack
   height — and accept a warning on the rest.
3. **Read the warnings the build prints.** They name the plane, the value asked
   for, and the value built. Each one is a decision you now own.

A coarse mesh is a legitimate choice for conceptual design; the report says
what resolution it was run at and what that supports. A mesh that snapped four
planes without anyone reading the warnings is not.

---

## 8. The libraries: name a file, do not retype it

Three libraries, and the same rule for each: **if the thing exists in the
library, name it; if it does not and the project has a real one, add a file.**
A number retyped into a case is a number that will disagree with the next case.

| | folder | named by | holds |
|---|---|---|---|
| fan wall units | `equipment/` | `fanwall.model` | the manufacturer's selections as a **table**, because a chilled-water coil's capacity is not a constant: one unit gives 504.9 kW at 35 °C return and 734.3 kW at 41 °C (ADR-036) |
| cabinets | `racks/` | `racks.type`, `racks.row[i].type` | width, depth, height and a default load, as the project's documents state them (ADR-075) |
| perforated surfaces | `components/` | `components.<role>` | free area and loss coefficient. Roles: `ceiling_return`, `supply_grille`, `floor_tile`, `gallery_mesh`, `cage`, `containment`, `distribution_loss` |

**Changing the unit takes the old unit's numbers with it.** Picking another
machine on the page replaces the airflow, capacity, power, supply temperature,
the three dimensions, the static pressure and the P-Q curve with the new one's
datasheet (ADR-094). Anything you type after that still wins, and stays in the
file where a reader can see it was a decision. In a hand-edited case the rule
is the older one: the library fills only what the case leaves blank, so
changing `fanwall.model` by hand and leaving the numbers gives you the right
name over the wrong machine — delete them and let the build fill them.

**Which unit suits which room.** A unit's file states its `arrangement`. A
`fanwall` unit is a wall of fans in a mechanical gallery and needs no raised
floor; a `downflow` unit stands in the room and discharges through the deck, so
it needs `floor.enabled: true`. Choosing one for the wrong room is refused at
build time with a sentence naming both halves, and the model page's picker
offers every unit with the same sentence beside the ones that do not fit — so
a CRAH is visible on a hall without a raised floor, and says what it would
take (ADR-092).

**Chilled water and direct expansion.** A unit's file also states its
`cooling`. Both run, and both are modelled: a DX evaporator is the same finned
bank with one side boiling, so it is fitted from the same kind of selection
(ADR-103). What differs is where the cold comes from, and therefore what the
fit needs and what it cannot answer:

| | `chilled_water` | `dx` |
|---|---|---|
| the coil is measured from | the entering chilled water | the **apparatus dew point** the selection implies |
| the fit needs | `selection.entering_water_c` and `leaving_water_c` | the air's humidity (`design.return_wb_c`, or a return RH) and the sensible/total split (`design.gross_sensible_kw`, `gross_total_kw`) |
| the duty is controlled by | the water valve | the compressors, staging and unloading |
| what is **not** modelled | — | the **condensing** side: capacity follows the outdoor air, and the result holds it at `selection.outside_air_c` |

Both re-solve the supply temperature against the room, pass by pass (ADR-040),
and both answer at the return the room really produces rather than at the
plate. A thin DX sheet still fits: with no `gross_total_kw` the coil is taken
as dry, with no `gross_sensible_kw` the gross duty is the net plus the stated
fan power, and each assumption is listed in the report's limitations. What a DX
file should carry, where the sheet prints it, is the whole performance table —
`equipment/P3100DA.yaml` is the pattern.

**When you add a file**, it carries its source in its own words: which document,
which revision, who issued it, when. The existing files are the pattern —
`equipment/CA80NPVG6.yaml` names the selection tool version and the date it was
printed, because a selection is only true at the conditions it was taken at.

**What the case still wins.** Naming a unit does not override anything you
type. Every value in `fanwall:` beats the file. Naming it buys the coil model,
which is what lets the report say what this machine will do in *this* room
rather than what the catalogue says it did in the selection program's.

---

## 9. Comments are part of the deliverable

Read any case in `cases/` before writing one. The style is not decoration: a
case is read by an engineer who was not in the room when it was written, and
the comments are the only thing that tells them whether a number is a
measurement or a guess.

What a comment carries:

- **the source**, specifically enough to find again — the drawing number, the
  selection, the table;
- **the arithmetic**, where a number is a product of others: `440 positions x
  11.59 = 5,100 kW`;
- **what was snapped, and to what**: `7.60 m on site; snapped to the 0.25 m mesh`;
- **what was simplified, and which way**: `the site's two return corridors are
  7 m and 11 m; modelled symmetric at 9 m`;
- **what would replace an assumption**: `a per-rack load map is the next input
  to add`.

The header comment says what the arrangement **is** and traces the air loop
through it in three or four lines. Somebody who reads only that should be able
to picture the room.

---

## 10. Reconciling with the real building

Build the case before you run it, and read what it says:

```bash
python3 -m aicfd build cases/<name>.yaml --out /tmp/check
```

It prints a summary and every warning. Go through them one at a time:

**A dimension was snapped.** Either accept it and record it in the comment, or
change the cell size so it does not need snapping (§7). Never change the
building to suit the mesh.

**A derived hall does not match the drawing.** Expected, within reason. Account
for the difference: 51.0 x 32.4 against a measured 54.64 x 31.98 is two
galleries modelled symmetric where the real ones differ, and that sentence goes
in the case. A difference you cannot account for means an input is wrong —
usually `pods` counted as rows, or a cabinet depth taken from the wrong
schedule.

**The total load is not the power table's.** Check the per-position ids for
typos (§6) and check whether the table includes PDU losses that AICFD does not.

**The plant does not cover the load.** `count x capacity_kw` against the room's
kW is arithmetic you can do before any solve. If it does not cover it, that is
a finding about the project, not a reason to edit the case.

---

## 11. What the solve will judge

A finished run is held to up to thirteen checks. They test the **model**, so a
FAIL is usually a statement about your inputs:

| check | a FAIL usually means |
|---|---|
| `mass_balance` | the solve has not converged, or a surface is missing |
| `sealed_envelope` | air is leaving the domain somewhere it should not — a wall that was never built |
| `no_backflow` | an opening is being driven the wrong way; often a plant/room flow mismatch |
| `energy_closure` | the heat in does not match the heat out: loads or airflow are inconsistent |
| `return_path` | the air is not going where the arrangement says it goes |
| `rack_resistance` | the rack zones are not delivering their rated drop — commonly an unmeshed surface letting air round them |
| `grille_resistance` | the ceiling grille K or free area does not match what the flow is doing |
| `plenum_resistance` | the same, for the supply plenum |
| `floor_resistance` | the same, for the raised floor plates |
| `fan_capacity` | the units cannot do what the case asks of them |
| `settled` | not converged; raise `max_iterations` |
| `ashrae_inlet` | rack intake temperatures outside the envelope — a real result, not necessarily a modelling fault |
| `plausible_velocity` | a velocity nothing in a data hall produces; look for a geometry error |

`plenum_resistance` and `floor_resistance` appear only where the case has that
arrangement.

---

## 12. Before you hand the case over

```bash
python3 -m aicfd build cases/<name>.yaml --out /tmp/check   # geometry + warnings
python3 -m unittest discover tests                          # nothing broke
python3 -m aicfd run cases/<name>.yaml                      # the solve
python3 -m aicfd report <name>                              # the Word report
```

The case is finished when: every warning is either gone or explained in a
comment; the derived geometry is reconciled against the drawing; the total load
matches the power table; every number has one of the three comments from §1;
and `aicfd build` prints no error.

---

## 13. A worked translation

`cases/hall-double-gallery.yaml` is the reference example, and it is a real
project: a 5 MW Ascenty hall, studied independently by Maders Consulting with
HELYX 4.5.1. Read it beside this manual. What each project fact became:

| the project said | the case says |
|---|---|
| plant rooms at both ends of a 53 m hall | `gallery.sides: 2` |
| return corridors 7 m and 11 m | `gallery.depth: 9.0`, with a comment saying they were modelled symmetric |
| slab at 7.60 m, false ceiling at 5.45 m | `height: 7.5`, `ceiling: 5.5`, both with the on-site figure in the comment |
| ten rack rows | `pods: 5` |
| rows cut in two by a cross aisle | `racks.blocks: 2`, `aisles.transverse: 3.0` |
| 5,000 kW IT in 412 of 438 positions, plus 100 kW of PDU loss | `load_kw: 11.59` over 440 positions, with the arithmetic and the simplification in the comment |
| Vertiv CWA selection at 750 m, 100 Pa, 34 °C entering | `fanwall.model`, and every selection figure carried across with its conditions |
| 14 units installed, N+2 | `count: 14`, `airflow_m3h` the **shared** figure, explained |
| Koolair 20.2 ceiling grilles | `grilles.free_area: 0.80`, `loss_coefficient: 2.4` |

---

## 14. What not to do

- Do not state `hall.size` on a case with `pods:`. It is ignored (§2).
- Do not count rack rows as `pods`. It is pairs of rows (§2).
- Do not use `per_row` together with `racks.row`. The row's length governs (§6).
- Do not model a blanking panel as a rack at 0 kW. They are different surfaces (§6).
- Do not take a fan wall capacity from a catalogue page. It is true at one
  return temperature and the room will not produce that one (§8).
- Do not combine `floor` and `plenum`. The build refuses it (§4).
- Do not adjust a clearance to make the derived hall match a drawing (§10).
- Do not leave a snapping warning unread (§7).
- Do not assume a cage's construction. Mesh and drywall are different rooms,
  and a plan shows the same rectangle for both (§4).
- Do not write a number without a comment (§1).

---

## 15. Keeping this file current

`tests/test_case_authoring.py` holds this manual to the code: every key
documented here is one the software reads, every key the software offers is
documented here, the ranges match, the check names match, and every library
file named here exists.

That is deliberate and it is the only reason to trust the file. A manual for an
agent is worse than no manual when it is out of date: the agent cannot tell,
follows it, and produces a case that will not build. So when a key is added to
the software, that test fails until it is added here too — which is the point.
