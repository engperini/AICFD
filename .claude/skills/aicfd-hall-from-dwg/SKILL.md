---
name: aicfd-hall-from-dwg
description: Turn a data hall layout drawing (DWG or DXF) into what AICFD runs -- the named-solid STL, the sidecar case YAML, a verification plan PNG and an export report -- in one pass, with the corrections already applied and every one of them on the record. Use whenever a user sends a data hall DWG/DXF, asks to "prepare the drawing for the CFD", "extract the racks/grilles/units from the layout", "generate the STL for AICFD", or wants to check or correct an extraction already made. The user answers the questions the pipeline prints in answers.yaml; the pipeline is re-run, never hand-edited.
---

# A data hall drawing, made ready for AICFD

**The input is the DWG.** One command decodes it (LibreDWG, bundled), reads
it and writes six files. The engineer looks at the plan, reads the report,
answers what the drawing could not say, and the command is run again.
Nothing is edited by hand: the DXF, the JSON, the STL, the YAML and the PNG
all come from the same model, so they never disagree.

```
python3 scripts/hall_from_dwg.py --dwg LAYOUT.dwg --answers answers.yaml --out OUT/
```

`--dxf` in place of `--dwg` takes a DXF the user exported from AutoCAD
(2018 format) -- the fallback when a DWG will not decode (structural damage,
a version LibreDWG does not read); ask for the DWG zipped, a DXF 2018 export
or the DWG saved as 2013, in that order. The `.dxf` the pipeline WRITES is
an output for conference, never fed back in.

| file | what it is |
|---|---|
| **`<name>.aicfd.zip`** | **the deliverable**: geometry, scenario, figures and answers in one file, which AICFD takes with `aicfd import` or the page's *Import package* and unpacks into `cases/<name>/` (AICFD ADR-135). Written only when the self-check passes |
| `<name>.png` | the verification plan: cabinets with tags, loads and **fronts**; cold-aisle lids, doors and end panels; floor tiles; ceiling return grilles; cage panels; dividing walls; units |
| `<name>.dxf` | the treated drawing: the same model on `CFD_*` layers in the DWG's own coordinates, to overlay on the original in AutoCAD |
| `<name>.stl` | the geometry AICFD reads: one named solid per object (`reference/contract.md`) |
| `<name>.view.stl`, `<name>.view/` | the same model **to open in another program** (FreeCAD, MeshLab, Blender…): no hall box, 20 mm plates, tiles on the deck, grilles under the ceiling; the folder has one STL per element type to show or hide; `containment.stl` is lids + doors + end panels + blank panels, one sealed set |
| `<name>.3d.png` | two z-buffer renders of the view model (opaque, correct occlusion, false ceiling removed) |
| `<name>.sections.png` | a longitudinal section through the rows and cross sections through a cold aisle and a hot band -- the heights and closures, to check before anything is run |
| `<name>.yaml` | the sidecar case file for the STL: loads, plant, components, mesh, solver -- everything a drawing cannot say -- and the three figures, which AICFD prints in the report's basis of design |
| `<name>.export.txt` | corrections applied, every quantisation over 1 mm, what was left out, open questions, self-check |
| `<name>.model.json` | the model the five above were written from |

Dependencies: `pip install ezdxf matplotlib pyyaml`. `bin/dwg2dxf` (LibreDWG)
converts a DWG; `chmod +x` it if needed. A DXF exported from AutoCAD (2018
format) is the safer input when the user can make one.

## Procedure

1. **Read `reference/reference.yaml`** -- the heights, rules and plant of
   the reference case (the AICFD hall that ran and closed). The drawing
   supplies positions; the reference supplies everything else. Do not ask
   the user for figures that are in it unless the drawing contradicts one.
2. **Write `answers.yaml`** from what the user has said (copy
   `templates/answers.yaml`). The minimum is the hall's name as drawn
   (`hall: "DATA HALL 04"`) or a point inside it (`anchor_xy`). Add the
   loads the user has given (`loads:` per tag, `racks.load_kw` for the
   rest) and any override of the reference.
3. **Read the Basis of Design when the user sends one** (`pdftotext -layout`
   and search for rack / fan wall / ceiling / slab heights, the plant's model,
   airflow and capacity, HOT or COLD aisle containment, liquid-cooling air
   fractions, N+2 counts). Put what it says in `answers.yaml` with the page in
   a comment; where it disagrees with the drawing, ask, with both figures.
4. **Ask the user for the elevations** -- every one of them, by element:
   raised floor depth (`heights.plenum`), cabinet height (`heights.rack`),
   cooling unit height (`heights.unit`), deck to false ceiling
   (`heights.ceiling`), deck to slab (`heights.hall`). The pipeline builds
   with the reference's figures so there is something to look at, but each
   height not in `answers.yaml` stays an OPEN QUESTION in the report and
   is printed as "REFERENCE, unconfirmed". A model is not delivered as
   final with an unconfirmed height.
5. **Run the pipeline.** Exit 0: files written, self-check passed. Exit 1:
   refused -- the message says what is missing (an anchor, a row whose
   front cannot be read, a cage panel through a cabinet); fix
   `answers.yaml` or ask the user, and run again. Exit 3: written but the
   self-check failed; the files are not to be run -- read the `x` lines.
6. **Look at the PNG and the 3D preview yourself** before showing it: count the rows, check
   every front tick faces a lid (or every back a chimney), every aisle has two doors, the cage panel is
   where the drawing puts it, the units are in the galleries, a fan-wall hall
   has its supply plenum and grilles in the hall's wall. If something
   is wrong, it is wrong in the model: fix the cause (an answer, a
   calibration in `reference.yaml`), never the picture.
7. **Relay to the user**: the plan PNG, the 3D views PNG and the sections
   PNG together (always the three), the GRID ALERTS, the COUNTS block and
   the CORRECTIONS block of `export.txt` in their language,
   and the OPEN QUESTIONS with the `answers.yaml` key each one maps to.
   Measurements go in the text, never written onto the drawing. Do not run
   a CFD until the user has validated the model.
8. **When the user answers or corrects**, put it in `answers.yaml` and run
   again -- the whole set is regenerated. Deliver the whole set; the
   `.view.stl` is what the user opens elsewhere to check the 3D. The
   **`<name>.aicfd.zip` is what AICFD takes**: one file, imported on the
   page or with `aicfd import`, which builds the scenario before it writes
   anything and stamps it with the STL's hash (no parametric case is
   derived, because a parametric room cannot reproduce a drawn hall and two
   inputs that disagree confuse). From then on the room is fixed and the
   scenario is edited in AICFD -- loads, units, failed units, setpoint,
   supply grille size, mesh, solver; a change to the room is a new run of
   this skill and a new import. The PNG and the report are what the
   engineer signs off.

## The rules the pipeline applies (all in `reference/reference.yaml`)

- **Positions from the drawing, everything else from the reference.**
  Cabinets and units are where the drawing puts them, to the mesh cell;
  heights (plenum, rack, unit, ceiling, slab), the plant, the components,
  the mesh and the solver are the reference's unless `answers.yaml` says
  otherwise. The report prints which is which.
- **Classification by dimension and position, never by layer.** A cabinet
  is a 0,6 or 0,8 x 1,2 m rectangle inside the hall; a unit is a rectangle
  of the plant unit's footprint inside a gallery; a cold aisle is the
  drafter's containment box between two rows (or, failing that, the band
  of floor tiles drawn); the cage is the closed rectangle inside the hall
  around cabinets, or the segments on a CAGE/MESH layer. Layer names are
  hints for the report, not inputs.
- **A cabinet's front looks at its cold aisle.** The aisle a row's face
  touches decides the front; a row with a cold aisle on neither side is a
  question (`fronts:`), one with both is refused.
- **Cold aisles are contained** (lid at rack-top height, a door at each
  end); a row that faces an aisle alone gets a vertical panel on the far
  side. Floor tiles: 600 mm, `floor(width/0.6)` across (2 for 1,2 m, 3 for
  1,8 m), `ceil(row/0.6)` along -- at least as long as the rows, never
  under a cabinet. **The containment covers the whole tile strip**, and
  every part of a row line with no cabinet on it (the strip running past a
  short row, a gap in the row) is closed with a **blank panel** from the
  deck to the rack top: rows, blanks, lid and doors are one sealed set. The
  self-check refuses a lid edge left open.
- **The room is the hot side, and each hot aisle takes the count of the
  cold aisle across the row.** Sweeping from the north: where a cold aisle
  has N tiles, the hot aisle on the other side of the racks gets N return
  grilles (600 mm), in contiguous strips from the racks' back outward. What
  is left of a wide hot aisle is one gap on the far side -- never in the
  middle. A band too narrow for the count is capped and reported.
  `return_grilles.mode: fill` covers the bands instead.
- **The dividing walls are panels on their hall-side face**; the gallery
  takes the wall's thickness. Above the false ceiling and below the deck
  the wall is woven mesh (the reference case's architecture).
- **A hot-contained hall is the mirror**: the room is the cold side, the
  drafter's box (on a HAC layer, or `containment.aisle: hot`) is the hot
  aisle: chimney panels from the rack tops to the ceiling over each row's
  back, doors floor to ceiling, return grilles filling the ceiling over it,
  blank panels on every part of the row line with no cabinet. Cabinets
  whose FRONT/BACK marks contradict the box are turned round and reported.
- **Fan walls make a supply plenum**: units standing off the hall's wall
  behind a second leaf blow into the cavity, and `supply:` grilles in the
  hall's wall (one per cold aisle, centred, 2,0 m wide, the cabinets'
  height, unless `plenum.grilles` places them) let the air out. A hall on
  the slab (`heights.plenum: 0`) has no deck, no tiles and fan walls; a
  liquid-cooled cabinet carries its AIR share of the IT load.
- **Every coordinate lands on the mesh grid** (0,2 m). Row starts and
  depths are rounded; cabinet widths are rounded one by one and laid
  contiguously so a row never overlaps itself. Every move over 1 mm is in
  the report.
- **A cell that does not carry the drawing is a GRID ALERT, never a silent
  fix.** A width the x cell does not divide grows (0,8 m on 0,3 m is
  0,9 m) and the row grows towards its far end; rows a few millimetres
  apart in the drawing can round to opposite sides of a grid line. Nothing
  is realigned: the report says which rows moved, by how much, the
  clearances to the walls before and after, why, and the x cell that
  carries every drawn width exactly. Relay the alerts before the run, and
  prefer an x cell that divides the cabinet widths (`[0.2, 0.3, 0.2]` for
  0,6 / 0,8 m cabinets).
- **Tags come from the drawing** when they are unique; rows whose tags
  repeat (RB01.. in every row) or are missing are renamed `F<n>-<nn>`,
  rows counted from the north, positions from the west -- and the report
  says so.
- **Loads**: a text like `10kW`, `ODF`, `FUTURE` near a cabinet; else
  `loads:` in answers; else `racks.load_kw`. A future position takes
  `racks.future_kw`. The total is printed for the user to check against
  the hall's rating.
- **Left out, on the record**: PDUs, RPPs, panels, columns, furniture --
  every closed rectangle inside the envelope that nothing claimed is
  listed under LEFT OUT.

## What never to do

- Never edit `<name>.stl`, `<name>.yaml` or `<name>.model.json` by hand.
  A correction is an answer or a calibration; run the pipeline again.
- Never invent geometry the drawing does not carry: no doors in the
  envelope, no cabinets the layout does not show, no cold aisle where no
  box or tile band is drawn (ask `fronts:` instead).
- Never carry a client's name into a file that will be committed to the
  AICFD repository: `name:` is the hall, not the customer.
- Never trust the picture over the report: a plan can look right with a
  16 mm gap between a lid and a door; the self-check is what says the
  model is sealed.

## Files

- `scripts/hall_from_dwg.py` -- the command; `scripts/hallkit/` -- the stages
  (`dxfread`, `extract`, `model`, `stl`, `viewstl`, `render`, `sections`, `sidecar`, `plan`, `dxfout`, `check`, `report`, `package`)
- `reference/reference.yaml` -- calibration, heights, rules, plant, solver
- `reference/contract.md` -- what AICFD accepts: the STL naming and the sidecar
- `reference/dwg-conventions.md` -- what the drawings of this family look like,
  and what the pipeline makes of each convention
- `templates/answers.yaml` -- every answer key, commented
- `bin/dwg2dxf` -- LibreDWG converter (master build; the 0.13 release fails on
  recent AutoCAD files)
