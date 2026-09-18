# AICFD — Roadmap and Architecture

AI-assisted CFD for data center cooling. Open source, runs locally, built for
electrical and facility engineers rather than CFD specialists.

## The problem this solves

Commercial data center CFD (6SigmaRoom, Future Facilities, CoolSim) costs tens of
thousands per seat and locks the model in a proprietary format. Underneath, it runs
the same physics OpenFOAM does: steady-state Navier-Stokes with buoyancy, racks and
perforated tiles as Darcy-Forchheimer porous media, CRAC units as velocity patches.

What the commercial tools actually sell is not the solver. It is the *interface*: you
describe a room in engineering units (kW per rack, CFM per CRAC, supply temperature)
instead of writing OpenFOAM dictionaries. AICFD rebuilds that interface on top of
OpenFOAM, and uses an AI assistant to cover the remaining gap.

## Design principles

1. **Engineering units in, engineering answers out.** The user types `5 kW` and
   `2500 m3/h`, never `injectionRateSuSp { h (5000 0); }`. Every OpenFOAM dictionary
   is generated, never hand-edited by the user.
2. **The AI assistant is a first-class part of the UX, not a chatbot bolted on.**
   Claude Code reads the repo's skills, translates a description in plain language
   into a validated room spec, runs the case, and explains the result. No API key,
   no per-token cost to the user.
3. **Every result is reproducible from a single file.** A case is fully described by
   one `room.yaml`. The OpenFOAM case directory is a build artifact and is gitignored.
4. **Physical validation is mandatory, not optional.** Every run emits a validation
   report (mass balance, monotonic temperature rise, residuals, mesh quality). A run
   that fails validation is reported as failed, even if the solver exited 0.
5. **No build step for the viewer.** The web UI is static files. `docker compose up`
   and open a browser. No npm install for the end user.

## Architecture

```
                        ┌──────────────────────────────────┐
   "sala de 10x8x3 m,   │  Claude Code + .claude/skills/   │
    12 racks de 8 kW,   │  (the AI assistant layer)        │
    2 CRACs de 25000    └───────────────┬──────────────────┘
    m3/h a 18 C"                        │ writes / reads
                                        ▼
                               ┌─────────────────┐
                               │   room.yaml     │  the single source of truth
                               └────────┬────────┘
                                        │ aicfd build
                                        ▼
   ┌────────────────────────────────────────────────────────────────────┐
   │  aicfd/spec.py   validated schema, engineering units (ADR-010)      │
   │  aicfd/case.py   spec -> OpenFOAM dictionaries                      │
   │  aicfd/run.py    clean-env runner (ADR-002), residual parsing       │
   │  aicfd/post.py   structured-grid fields -> KPIs + web payload       │
   └────────────────────────────────┬───────────────────────────────────┘
                                    │ aicfd run / aicfd post
                                    ▼
                          ┌───────────────────────┐
                          │ results/<case>/       │
                          │   viewer.json  (geometry, KPIs, metadata)
                          │   fields.bin   (packed float32 scalar/vector fields)
                          │   report.md    (validation + KPIs, human readable)
                          └──────────┬────────────┘
                                     │ aicfd view
                                     ▼
                          ┌───────────────────────┐
                          │ web/  static Three.js │  3D room, draggable slice planes,
                          │       viewer          │  KPI panel, convergence chart
                          └───────────────────────┘
```

### Component responsibilities

| Component | Owns | Does NOT own |
|---|---|---|
| `aicfd/spec.py` | Units, validation, defaults, sanity limits | Any OpenFOAM knowledge |
| `aicfd/model.py` | Spec -> derived geometry, mesh snapping (ADR-014) | Any OpenFOAM knowledge |
| `aicfd/podcase.py` | Fan-wall POD -> OpenFOAM, via mesh surgery (ADR-016) | Running anything |
| `aicfd/podpost.py` | POD KPIs, the energy-balance verdict, live sampling (ADR-018) | Rendering |
| `aicfd/server.py` | Serving the page, the editable-parameter gate (ADR-015) | Physics, geometry |
| `aicfd/case.py` | The whole OpenFOAM dictionary vocabulary | Running anything |
| `aicfd/foam/` | Reading a solved case back off disk | Writing one |
| `aicfd/run.py` | Process execution, environment isolation, log parsing | Interpreting physics |
| `aicfd/post.py` | Turning a mesh into KPIs and web payloads | Rendering |
| `web/` | Rendering and interaction | Any physics or unit conversion |
| `.claude/skills/` | Natural language <-> spec, result interpretation | Being required to run |

The split matters: the CLI must be fully usable without the AI assistant, and the
assistant must add nothing the CLI cannot do. That keeps the project honest as open
source — nobody is forced through an LLM to use it.

## Room spec (target shape)

```yaml
name: datahall-a
room:
  size: [10.0, 8.0, 3.0]        # x (airflow), y, z (height), meters
  raised_floor: 0.6              # m, 0 = no plenum
racks:
  - id: R01
    position: [3.0, 1.5]         # x, y of the front-bottom-left corner
    size: [0.6, 1.0, 2.0]        # depth, width, height
    load_kw: 8.0
    airflow_m3h: 2400            # optional; derived from load + target dT if omitted
    orientation: +x              # front faces +x
cracs:
  - id: CRAC01
    type: fanwall                # fanwall | downflow | inrow
    patch: xmin
    airflow_m3h: 25000
    supply_temp_c: 18.0
mesh:
  cell_size: 0.10                # m; coarser = faster
solver:
  max_iterations: 1000
```

Everything else (Darcy-Forchheimer coefficients, enthalpy source terms, patch
velocities, turbulence initialization) is derived. The user never sees it unless they
ask for `aicfd build --explain`.

## Milestones

### M0 — Ground truth (no code, real data) — **done**
Install OpenFOAM, run the inherited reference case, confirm it converges **here**,
and record the actual numbers. The reference case arrived with validation numbers
from another environment; those are a claim until reproduced.

Reproduced exactly (91 s, 800 iterations, rack peak 19.50 °C), and the physics
review found the case is over-ventilated by 52x — a numerical smoke test, not a
representative data hall. Write-up:
[`experiments/2026-09-17-reference-case.md`](experiments/2026-09-17-reference-case.md).

### M1 — Web viewer over real results — **done**
Static Three.js viewer reading `viewer.json` + `fields.bin` produced from the M0 run.
Room geometry, rack boxes, CRAC patch, draggable slice on any axis, three field
views, a numeric readout under the cursor, KPI panel, validation checks and the
convergence chart, in light and dark. This is the "practical" half — it is what
makes the output readable to someone who is not a CFD engineer.

Also landed ahead of M3, because M0 needed them: `aicfd run/post/view/doctor`
and the isolated OpenFOAM runner.

### M2 — Parametric case generation — **done**
`room.yaml` -> validated spec -> generated OpenFOAM case. Multi-rack. Fan velocity
derived from real CRAC airflow, rack resistance from a pressure drop at rated flow,
heat sources from kW, mesh divisions from a target cell size, rack boxes snapped to
cell boundaries. `aicfd new` and `aicfd build`.

This is where AICFD stopped being "one hardcoded case". It is also the fix for the
52x over-ventilation M0 found: the supply patch velocity is now
`airflow / wall area` rather than a number chosen for convergence.

### M3 — CLI + Docker — **done**
`aicfd new | build | run | post | view | doctor`. Dockerfile with OpenFOAM baked in,
`docker compose up` serving the viewer. Target: a Windows user with Docker Desktop
goes from clone to a rendered result in under 15 minutes.

### M4 — Assistant skill + engineering KPIs — *skill done, KPIs next*
The `aicfd` skill teaching Claude Code to drive the CLI end to end is in place.
Remaining: KPIs an electrical
engineer actually reports: per-rack inlet temperature vs. ASHRAE A1–A4 envelopes,
RCI (Rack Cooling Index), RTI (Return Temperature Index), recirculation and bypass
fractions, CRAC redundancy check (N+1 failure scenario).

### M4b — Rack fans (moved up from M5)
Racks as momentum sources on top of their resistance, so a rack draws its rated
airflow instead of whatever the room happens to push through it. Measured need:
the first realistic generated case gave every rack 21% of the air its load
required, which over-predicts rack temperatures by tens of degrees. See
[`experiments/2026-09-17-generated-case-velocities.md`](experiments/2026-09-17-generated-case-velocities.md).

### M4c — The page as the pre-run interface — **done**
`aicfd view` opens the model before it is solved: two sections and a plan at one
shared scale, drawn from the same geometry object the mesher consumes (ADR-014),
plus a parameter form, the derived face areas and velocities, and the
convergence chart. Solid where the section cuts, dashed for anything off the
plane.

A run is then followed rather than waited out: four instrumented places (cold
aisle, contained hot aisle, ceiling plenum, back of the fan wall), three points
each, sampled every `solver.sensor_interval` iterations, with the mass and
energy balances alongside. The sensors are drawn on the sections too, so their
placement is checked before the run rather than argued about after it.

### M5 — Real data hall features — *in progress*
Hot aisle containment and the ceiling-plenum return are modelled in
`aicfd/model.py` and drawn (`cases/pod-fanwall.yaml`): a fan wall discharging
into the cold aisle, racks turned 90° to it, a contained hot aisle rising to
the false ceiling, three ceiling grilles over the racks, a return plenum, and
the opening back into the mechanical gallery.

The generator is `aicfd/podcase.py`: internal surfaces as baffles, openings
as holes left in their face zones, rack porosity oriented along y, and the fan
wall as the one place the air loop is cut (ADR-016, ADR-017).
`aicfd/podpost.py` judges the result by its energy balance (ADR-018).

Datasheet data now enters as physics and returns as checks (ADR-020): return
grilles are pressure-jump baffles with K from a catalogue point, the fan wall
carries its P–Q curve for margin and uncontrolled operating point. Cells are
sized per axis (ADR-021) and the worked case runs at 0,2 × 0,2 × 0,1 m — 75 600
cells, minutes rather than half an hour. The results page leads with plan and
sections over the field (temperature, speed, pressure) using the same drawings
checked before the run; the 3-D scene is secondary.

The same parts scale to a hall (ADR-022): `pods:` lays out row–HAC–row pairs
with a fan wall per cold aisle, each unit its own mass-flow pair, walls of a
kind sharing a zone, and `solver.processors` running the solver under MPI
with the sampler stitching each write. `cases/hall-10mw.yaml` — 16 PODs, 768
racks, 17 fan walls, 329 280 cells — is the conceptual-design worked case, and
the results page paints every rack by the inlet temperature at its top.

Remaining: raised-floor plenums with perforated tiles, in-row and downflow
CRAC types, rack-level airflow curves, per-rack loads from a DCIM export, a
leakage path for containment that is not perfect — real containment is not —
and a refinement study upward from the working mesh.

### M6 — BIM import
IFC (exported from federated Revit) -> filtered geometry -> room spec. Only the
categories that affect airflow: walls, slab, ceiling, racks, CRAC units. Everything
else is discarded. Loads come from a DCIM export or spreadsheet, never from Revit.

## Non-goals (explicit)

- **Transient simulation.** Steady-state only, until someone has a concrete need for
  thermal ride-through analysis. Transient multiplies runtime by 100x.
- **Radiation.** Negligible next to forced convection in a data hall.
- **Meshing arbitrary CAD.** snappyHexMesh over an STL is supported in M6, but the
  primary path stays blockMesh over axis-aligned boxes — it is what makes a run take
  90 seconds instead of an afternoon.
- **Being a general CFD tool.** AICFD models data halls. That constraint is what
  lets the interface stay in kW and CFM.

## How this repo is run

- All work is documented on `main`. Experiments are run locally (Docker or a Claude
  Code session), and the results, including failed ones, are committed under
  `docs/experiments/`.
- Every architectural decision that is not obvious gets an entry in
  [`DECISIONS.md`](DECISIONS.md), with the reason, so a future contributor does not
  have to re-derive it.
