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
  fault.
- **Rack fans.** A rack is a resistance today, so a rack in a pressure trough
  passes less air than its load needs instead of pulling harder. The 10 MW hall
  shows this at the first rack of every row. Momentum sources would fix it.
- **Raised-floor plenums** with perforated tiles, and **in-row / downflow**
  unit types.
- **Per-rack loads from a DCIM export**, instead of one load for the hall.
  Unloaded positions are porous media without a source, and where they sit
  decides how evenly the units load.
- **Ancillary heat that is not in a rack** — PDU dissipation as a share of the
  IT load, released in its own zone. The figure is stated — 2 %, on the
  components page, editable — and marked as not yet read by the solver. Two
  per cent of a 5 MW hall is 100 kW, a fan wall's worth, so a model that
  counts only rack load undercounts the room by about one unit. What is
  missing is a zone for the distribution equipment and a source in it: the
  heat arrives in the room rather than at a rack face, so it reaches a rack
  inlet only through the room.
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
