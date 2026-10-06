# What the drawings look like, and what the pipeline makes of them

Calibrated on a family of layout drawings (DATA HALL 04, September 2026). The
pipeline classifies by **dimension and position**; the layer names below are
what that family used and are listed so a reader can find things in the
DWG, not because the pipeline depends on them.

## Units and scale

The architect's DWG is in **millimetres** (`$INSUNITS = 4`); the pipeline
scales by 1/1000. A drawing whose header says "unitless" is taken as metres
and the report says so; pass `--units mm` when it is not.

## The rooms

| what | drawn as | seen in | the pipeline |
|---|---|---|---|
| the hall | a closed polyline with the hall's name text inside | `00_AREA TOTAL` | the smallest closed rectangle over 100 m² containing the anchor |
| the galleries | closed polylines 2–6 m wide against the hall's long sides | `ARQ_PISO ELEV` | rectangles of that width touching the hall (gap ≤ 0,5 m) |
| the dividing walls | the **gap** between hall and gallery contours (~200 mm); or thin rectangles inside one outer contour | `00_AREA TOTAL`, `CFD_PAREDES` (treated DXF) | a panel on the hall-side face; the gallery takes the thickness |
| the envelope | the union of hall and galleries | — | the `hall` box; nothing outside it |

Galleries along y in the file are handled by transposing the drawing; the
report says when that happened.

**A double wall (fan-wall halls, DATA HALL 03).** Two thin masonry
contours run parallel between the technical room and the hall, ~1,2 m
apart: the fan walls are mounted in the outer one and the inner one is the
hall's wall. The pipeline reads the cavity as the **supply plenum**: the
dividing wall moves to the units' leaf, the hall's wall becomes
`wall:plenum_<side>` with `supply:*` grilles in it, one per cold aisle,
centred, 2,0 m wide and the cabinets' height (the plan does not draw them;
`plenum.grilles` in answers.yaml places them from the elevation). Read from
the units standing off the hall's wall (`plenum.min_depth_m`), so a leaf
drawn on one side only (DH03's PL-0001) still gives a plenum on both.

## Cabinets and rows

| what | drawn as | the pipeline |
|---|---|---|
| a cabinet | a closed 0,6 × 1,2 or 0,8 × 1,2 m rectangle, or a block INSERT of one | `rack`, sizes from `rack_plan_sizes_m` ± 50 mm |
| its tag | TEXT/ATTRIB like `A01`, `RB07`, `F2-01` within 1 m of the centre | assigned uniquely (each text once); repeated-per-row tags (`RB01..` in every row) → rows renamed `F<n>-<nn>` |
| its load | TEXT like `10kW`, `20kW`, `ODF`, `FUTURE` within 1,5 m | kW, 0 (ODF), `racks.future_kw` |
| a row | cabinets sharing a y band, touching | rows from the north are F1, F2…; positions from the west |
| its front | `FRONT` / `BACK` (`FRENTE` / `COSTAS`) texts within a third of a cabinet of a face | the row's front by majority; a row whose marks contradict the containment box is turned round and reported (a mirrored POD block) |
| a liquid-cooled cabinet | `LIQUID-COOLED SERVER RACK 225 kW`, `LIQUID-COOLED NETWORK RACK 32 kW`, `SPARE … (0~225 kW)`, `SHUFFLE BOX`, `ODF` inside it | IT load from the label; the AIR share (`liquid_cooling.air_fraction`) goes to the sidecar; a SPARE takes `liquid_cooling.spare_kw` |

Seen in DH04: rows on a cable-tray layer (`LEITO ARAMADO`); RB06 missing
and RB07/RB16 duplicated in one row → the row was renamed; the cage's rows
A–D with unique tags → kept.

## Aisles and containment

| what | drawn as | the pipeline |
|---|---|---|
| a contained cold aisle | a closed rectangle 1,0–2,1 m wide, ≥ 2,4 m long, between two rows | `00_eNCLAUSURAMENTO` in DH04; decides the rows' **fronts** |
| a contained hot aisle | the same box, drawn on a `… HAC …` layer, or `containment.aisle: hot` in answers (the BoD says HOT AISLE CONTAINMENT) | chimney panels from the rack tops to the false ceiling over each row's back, doors floor to ceiling, return grilles filling the ceiling over it; the rows under one box are one POD |
| the same, in a treated DXF | bands of 600 mm tiles | tile bands are read as cold aisles when no box exists |
| a lone row facing an aisle | a box with a row on one side only | lid + doors + a vertical closure panel on the far side (reported) |
| hot aisles | nothing (the room) | inferred: the band between two backs, or behind a row to the wall/cage |

The colours or hatches the drafter uses for aisles carry no meaning to the
pipeline: a box between rows is a cold aisle, whatever its colour.

## The cage

| drawn as | the pipeline |
|---|---|
| a closed rectangle inside the hall around cabinets (≥ 3 × 4 m, smaller than the hall) | panels on the sides that do not coincide (± 50 mm) with the hall's walls |
| LINE segments on a layer named `…CAGE…`, `…MESH…`, `…TELA…` | one panel per axis-aligned segment |
| internal demarcations (cage / ROFR) | left out — they are lines, not walls |

Panels run from the deck to the false ceiling. A panel that crosses a
cabinet is refused by name.

## The plant

| what | drawn as | the pipeline |
|---|---|---|
| a unit | a rectangle of the unit's footprint (2,553 × 0,873 m for the P3100DA; the drafter's box is 0,89 deep) inside a gallery; often **exploded LINEs** | `unit`, from closed rectangles or from clusters of connected lines whose outline is the rectangle |
| a fan wall | a block INSERT (`EFW500C…`, `FANWALL…`, `CRAH…`: `units.block_pattern`) in a technical room, thousands of lines deep | `unit` from the block's footprint; nested sub-blocks de-duplicated; `unit:<id>:+x/-x` blowing into the hall (or into the supply plenum) |
| its tag | `UE-47`, `CRAC-01`, `FW-3` within 2 m | kept; an untagged unit is named `U<n>` and reported |

Units sit on the deck; the top face is the return (from the gallery), the
bottom face the supply (into the plenum), as the reference case's downflow
units do. A hall with no raised floor (`heights.plenum: 0`) has fan walls:
the box stands on the slab against the dividing wall and blows through it.

## The Basis of Design

A BoD (PDF) is not read by the pipeline, but the procedure says to search
its text for what the drawing cannot carry: the heights (rack, fan wall,
false ceiling, slab), the plant (model, airflow, capacity, supply
temperature, redundancy), the containment kind, the liquid-cooling air
fractions, the design CFM/kW. Every figure taken from it goes into
`answers.yaml` with the page it came from in a comment. What the BoD and the
drawing disagree on (6+2 fan walls in the text, 8 drawn) is a question for
the user, printed with both figures.

## What is left out, by design

PDUs / RPPs (0,88 × 3,49 m), QACs (0,42 × 0,84), columns, furniture,
demarcation lines, dimension texts: every closed rectangle inside the
envelope that nothing claimed is printed under LEFT OUT so the omission is
a decision, not an accident. A later contract revision may add an
`obstacle` type.

## Adding a site with other sizes

Edit `reference.yaml`: `rack_plan_sizes_m` (a 0,7 m cabinet), `unit_plan_size_m`
(another unit), `gallery_width_range_m`, `cold_aisle_width_range_m`. The
classifier reads only those.
