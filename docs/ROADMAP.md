# AICFD — architecture, state and what is next

AI-assisted CFD for data centre cooling. Open source, runs locally, built for
electrical and facility engineers rather than CFD specialists.

`README.md` is how to use it. This file is what exists, what does not, and why.

## The problem this solves

Commercial data hall CFD (6SigmaRoom, Future Facilities, CoolSim) costs tens of
thousands per seat and locks the model in a proprietary format. Underneath, it
runs the same physics OpenFOAM does: steady RANS with buoyancy, racks as
Darcy–Forchheimer porous media, cooling units as boundary conditions.

What those tools actually sell is not the solver. It is the *interface*: you
describe a room in engineering units instead of writing OpenFOAM dictionaries.
AICFD rebuilds that interface on OpenFOAM, and uses an AI assistant to cover
the remaining gap.

## Design principles

1. **Engineering units in, engineering answers out.** The user types `13 kW`
   and `76 650 m³/h`, never `injectionRateSuSp { h (13000 0); }`. Every
   OpenFOAM dictionary is generated and never hand-edited (ADR-004).
2. **One model, one pipeline.** A POD and a 10 MW hall are the same geometry
   code with lists of different lengths (ADR-022). There is no second path to
   drift out of sync.
3. **The AI assistant is part of the product, not a chatbot bolted on.** It
   drives the same CLI a person drives, so anything it does can be redone by
   hand and audited afterwards (ADR-003, ADR-006).
4. **Every result is reproducible from one file.** A case is fully described by
   its spec; `runs/` and `results/` are build artifacts and are not tracked.
   The three worked results live in `reference/`, which the tool never writes
   to, so reproducing one cannot collide with the copy in the repository
   (ADR-032).
5. **Physical validation is mandatory.** Eleven identities the physics has to
   satisfy, printed on every run. A run that fails them is reported as failed
   even when the solver exits 0 (ADR-018).
6. **No build step.** The page is static files; the tests need no OpenFOAM.
   True in Docker too: the image carries OpenFOAM and the libraries, and the
   code is mounted from the working copy, so a `git pull` takes effect without
   a rebuild (ADR-034).
7. **A result that cannot be circulated is not finished.** `aicfd report`
   writes the Word document a reviewer marks up, from the same export the page
   draws, with the limits inside it rather than in a footnote (ADR-028).

## What exists

The whole tool is the fan-wall architecture with a ceiling-plenum return,
at two scales:

```
spec (YAML)  ->  aicfd/model.py   geometry: boxes, panels, rows, fan walls, sensors
             ->  aicfd/case.py    blockMesh -> topoSet -> createBaffles -> checkMesh
             ->  buoyantSimpleFoam (serial, or decomposePar + mpirun)
             ->  aicfd/post.py    patch flows, eleven checks, per-rack inlets, export
             ->  web/             plan and sections over the field, in the browser
             ->  aicfd/report.py  the Word deliverable, from the same export

equipment/<model>.yaml  a unit as a TABLE of manufacturer selections, so a
                        coil is judged at the air it actually receives
```

Modelling choices, each with an ADR: racks as porous blocks with an enthalpy
source (ADR-011); the fan wall as an internal baffle pair set by mass flow
(ADR-017); return grilles as cyclic pairs carrying a datasheet pressure jump
(ADR-020); containment, false ceiling and row ends as two-sided wall baffles
(ADR-016); per-axis cells (ADR-021); the site's altitude setting the operating
pressure, and the plant sized against the design office's rules before any CFD
(ADR-023).

A hall may have a mechanical gallery at **each end** (`gallery.sides: 2`) and
rows cut into blocks along their length (`racks.blocks`), with the return plenum
staying a single shared volume above the whole hall (ADR-027).

Three worked cases carry the evidence: `cases/pod-fanwall.yaml` (one POD,
18 kW), `cases/hall-10mw.yaml` (16 PODs, 768 racks, 9,98 MW, a Vertiv CA40
selection) and `cases/hall-double-gallery.yaml` (5 MW, two galleries, two rack
blocks, a Vertiv CA80 selection, built against a real hall studied
independently, and agreeing with that study's warmest rack intake to within
0,1 K on a mesh 78 times coarser). All three are written up in
`docs/experiments/`.

## What is next

The order below is set by [`reference-report-parameters.md`](reference-report-parameters.md),
a line-by-line comparison against an independent CFD study of a real 5 MW hall
of the same architecture. The first two items are what that study was
commissioned to answer and AICFD cannot yet say.

- **A failure scenario and the redundancy it tests.** `fanwall.redundancy` and
  `fanwall.out_of_service`, with the total airflow to the room held constant
  between the cases so only the number of units sharing it changes. Without it
  a study reports the normal condition and calls the margin ample.
- **Two scenarios from one mesh**, compared side by side on one colour scale,
  so the difference between them is attributable to the boundary conditions and
  not to discretisation.
- **Reported numbers as means over a window** (`solver.average_over`), quoted
  with the peak-to-peak range and drift of the monitors inside it.
- **Leakage.** Containment is modelled as perfect. Real containment leaks, and
  the leak is what decides the top-of-rack temperature in a marginal design.
  The figure is now stated — 5 % permeability, on the components page — and
  the page says it is not yet read by the solver. What is missing is the
  panels becoming porous baffles instead of walls, and `sealed_envelope`
  learning that a containment carrying flow is the design rather than a
  fault. The colocation requirement (below) splits the 5 % in two: **3 %
  rack leakage**, distributed around the rack and between servers, and **2 %
  containment leakage**, evenly above the racks and at the joins — two
  different places, so two different porous surfaces.
- **Rack fans.** A rack is a resistance today, so a rack in a pressure trough
  passes less air than its load needs instead of pulling harder. The 10 MW hall
  shows this at the first rack of every row. Momentum sources would fix it.
- **Raised-floor plenums** with perforated tiles, and **in-row / downflow**
  unit types.
- **Per-rack loads from a DCIM export**, instead of one load for the hall.
  Unloaded positions are porous media without a source, and where they sit
  decides how evenly the units load.
- **Ancillary heat that is not in a rack** — PDU/RPP dissipation as a share of
  the IT load, released in its own zone. The figure is stated — 2 %, on the
  components page, editable — and marked as not yet read by the solver. Two
  per cent of a 1 MW hall is about 110 kW (the figure the mechanical team
  uses for DH04), a unit's worth, so a model that counts only rack load
  undercounts the room by about one unit. What is missing is a zone for the
  distribution equipment and a source in it: the heat arrives in the room
  rather than at a rack face, so it reaches a rack inlet only through the
  room. Same family, same TODO: **the envelope**. Every wall is adiabatic
  today (stated in the limitations); the mechanical load sheet for the same
  hall carries 25,7 kW through the walls, 11,3 kW through the roof, 5,6 kW
  through partitions, 1 kW of lighting, 1,7 kW of people and 17 kW of
  ventilation against 1.045 kW of IT — 6 % on top of the racks. Wall and
  roof become heat-flux boundaries from `U·A·ΔT` or from a stated load, and
  lighting, people and fresh-air load become room sources.
- **A refinement study upward** from the working mesh: the coarse-versus-fine
  comparison so far went *coarser* and agreed, which is evidence but not a
  convergence study.
- **Comparison against measurement.** Every validation today is an identity the
  physics must satisfy. That class of check has caught several real faults, but
  it cannot promise the built room behaves this way.
- **More units in the library.** One model is characterised (ADR-036); every
  other fan wall in use needs its own set of selections. The gap that found
  itself immediately: the CA80NPVG6's selections start at 35 °C, and a hall at
  158 CFM/kW returns about 34, so that unit needs selections at 33 and 34 °C
  before it can be judged in such a hall.
- **BIM import.** IFC from a federated Revit model, filtered to the categories
  that affect airflow, into a spec. Loads still come from DCIM, never Revit.

## Requirements from a colocation client's CFD specification (Rev 1.2.1)

A hyperscale colocation client issues its CFD consultants a requirements
document (Rev 00 Aug-2023, Rev 01 Apr-2024, read here at Rev 1.2.1). It lists
OpenFOAM among its approved tools, and it is the closest thing to an
acceptance specification this project has been measured against. What
follows is every internal-CFD requirement in it, against what AICFD does
today. The external (wind, generator plume) and transient studies it also
asks for are outside this tool's scope and stay so (see Non-goals); they are
listed at the end so the gap is on record.

**Already met.**

- *Pass criterion at the server inlet with N units.* The report ranks every
  rack by top-of-rack intake and judges it; the ASHRAE A1 recommended limit is
  the current threshold. See the TODO on the threshold below.
- *Hot-aisle containment as the default.* It is (ADR-100); cold-aisle
  containment is available where the design has it.
- *Supply fan heat gain, from the manufacturer.* The unit's net sensible
  capacity nets the fan heat off the selection sheet (ADR-070, ADR-073), and
  the report states the fan power beside it.
- *Room cooling equipment schedule per unit* — airflow, sensible capacity,
  supply/return air, chilled water supply and return. Section 4.6 and the
  coil tables carry these; the water temperatures are in the selection rows.
- *Floor grille specification appended* — free area, K, face velocity, and
  the drop the field shows against the drop the datasheet asks (the
  `floor_resistance` check and the surfaces table).
- *Planes at mid-rack height, at top-rack height, above the false ceiling,
  and vertical cross-sections*, for temperature, velocity and pressure —
  sections 4.3 and 4.4. Their 2 m AFFL plane is our top-of-rack plane; see
  the sensor-height TODO.
- *A unit that cannot hold its setpoint delivers what it can.* The feedback
  put it as: "if the maximum return is 30 °C and the ΔT is 12 K, a unit
  seeing 32 °C delivers 20 °C, not 18 — otherwise the CFD passes while the
  fan wall rejects more heat than the machine supports, which is a physical
  incoherence." That is exactly what the coupled solve does: each unit's
  coil answers at the return it receives, a unit at full valve or at its
  compressor ceiling delivers `return − capacity/(m·cp)` and the field is
  re-solved with that supply (ADR-063, ADR-103, ADR-118, ADR-125), and the
  `coil_closure` check fails any field solved with air the plant cannot make
  (ADR-123). On the 1 MW DX hall two units saturate and deliver 20,4 °C
  against an 18,8 °C setpoint, and the report says so. The ΔT is the coil's
  own at that return rather than a fixed 12 K, which is the stronger
  statement of the same rule.

**TODO — inputs the requirement fixes and the case should take by name.**

- [ ] **Leakage 5 % = 3 % rack + 2 % containment**, as two porous surfaces
  (see *Leakage* above). Today: containment perfect, racks sealed.
- [ ] **BMS cold-aisle sensor at 2.000 mm AFFL.** Today the report reads the
  intake at the top of the rack (2,2 m on a 48U cabinet) and as the face
  mean. Add a `sensors.bms_height_m` station: the temperature at 2,0 m on
  the cold-aisle face, per rack and per aisle, reported beside the top-of-
  rack figure — it is what the plant's control will actually see.
- [ ] **Airflow 170 CFM/kW at the unit supply = 162 through the servers +
  8 leakage.** Today `racks.airflow_cfm_per_kw` is one figure (158 default)
  and the racks have no leakage path. Take the two figures and route the
  8 CFM/kW through the rack-leakage surface.
- [ ] **Rack 48U, 600 × 1066,8 × 2299 mm**, uniform load in the cabinet.
  Today's default is 0,6 × 1,2 × 2,2 m; the case can already state it, and
  a `racks.preset: colo-48u` would save retyping it.
- [ ] **90 % of contracted IT** as the modelled load, stated on the cover and
  in the basis of design as a utilisation factor rather than folded into the
  per-rack kW.
- [ ] **Pass threshold as a case input**: 29,4 °C (85 °F) for this client's
  general cooling types, 33,3 °C for evaporative with CMxC, with the ASHRAE
  A1 27 °C staying the default. The check and the figures' marked limits
  follow it.
- [ ] **PDU/RPP losses, 2 % of the hall load, in their own zone**, and **the
  envelope gains** (see *Ancillary heat* above).
- [ ] **Design day from the ASHRAE n = 50 sheet**, worst DB (air-cooled
  plant) or worst WB (evaporative), stated in the basis of design and used
  for the DX condenser ambient (ADR-118 holds it at the selection's today).

**TODO — scenarios the requirement runs and the tool should script.**

- [ ] **I1 → I2: N+1 with all units, then each unit out in turn to find the
  worst N.** This is the *failure scenario* item above, with the search over
  which unit to remove automated and the worst case carried forward as the
  baseline.
- [ ] **I3 / I3.a: maximum-density racks packed into the worst airflow spot
  of each row found in I2**, without and then with spacing. Needs the
  per-rack airflow-deficit finding (section 5 now names the worst cabinet)
  to pick the spot, and per-position loads (the cage schedule already does
  this by hand).
- [ ] **I4 / I4.a: one cold aisle at maximum density, the rest at average.**
  Per-position loads again.
- [ ] **Two scenarios compared on one mesh and one colour scale** (already
  listed above) — every case of the set is a delta from I2.

**TODO — outputs the requirement asks for that the report lacks.**

- [ ] **SIT bin table**: racks counted into ≤ 29,4 / 29,4–32,2 / 32,2–35 /
  35–37 / … / > 45 °C, one column per scenario. The ranking exists; the
  binning and the side-by-side do not.
- [ ] **Max, min and average on every plane plot**, printed on the figure.
- [ ] **Temperature streamlines** (planned; stage 1 = section + plenum plan
  coloured by temperature, stage 2 = capture index).
- [ ] **Velocity vector plots** marking low-pressure / back-pressure zones
  and recirculation — the pressure plan exists; vectors do not.
- [ ] **Floor grille airflow and discharge temperature, graphically**, per
  grille — the numbers exist per plate in the post; a figure does not.
- [ ] **Thermal mass inputs listed** (slab, rack steel at 5 kg/RU, servers at
  ≤ 15 kg and 500 J/kg·K, effectiveness 0,8, units, ceiling, walls) — only
  meaningful with a transient solve; record them in the case so the
  hand-off to a transient tool has them.

**Out of scope, on record.** External CFD (wind roses, generator exhaust
contaminant, intake uplift bins, equipment de-rate) and transient CFD
(utility-to-generator transfer, chiller restart, 295 s above threshold,
thermal stores). AICFD is a steady internal tool by design; where a project
needs these, they are a separate study, and the steady result here is its
starting state.

## Non-goals

- **Transient simulation.** Steady state only. Transients multiply runtime by
  about 100.
- **Radiation.** Negligible next to forced convection in a data hall.
- **Meshing arbitrary CAD.** The primary path stays blockMesh over axis-aligned
  boxes plus baffle surgery; it is what makes a hall run in minutes.
- **Being a general CFD tool.** AICFD models data halls. That constraint is
  what lets the interface stay in kW and m³/h.

## History, in one line each

Kept because the reasoning behind the current shape is in the failures, not in
the successes. Full write-ups in `docs/experiments/`.

- **M0 — ground truth.** Ran an inherited hand-built OpenFOAM case here and
  reproduced its numbers, then found it was over-ventilated 52× and was a smoke
  test rather than a data hall.
- **M1 — the viewer.** Static page reading `viewer.json` + `fields.bin`, so a
  result is readable by someone who is not a CFD engineer.
- **M2 — parametric rooms.** A `room:` + `cracs:` spec whose supply and return
  were faces of the domain. **Removed** in ADR-025: a real hall's air loop
  closes inside the box, and keeping a second generator alive that could not
  express that was a liability, not an option.
- **M3 — CLI and Docker.** Clone to rendered result on a clean machine.
- **M4 — the assistant skill, and the page as the pre-run interface.** Catch a
  mistake before paying for a solve; follow a run instead of waiting it out.
- **M5 — the fan-wall POD, then the hall.** Internal surfaces as baffles, the
  fan wall as the one place the loop is cut, datasheets in and checks out, and
  the same parts repeated to 10 MW.

## How this repo is run

- All work is documented on `main`. Experiments, including the ones that
  failed, are committed under `docs/experiments/` with their numbers.
- Every non-obvious decision gets a numbered entry in
  [`DECISIONS.md`](DECISIONS.md), with the alternative that was rejected, so a
  future contributor does not have to re-derive it. Superseded entries stay,
  marked.
- `python3 -m aicfd verify` is what a reviewer runs to disbelieve all of it.
