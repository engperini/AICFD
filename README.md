# AICFD

**AI-assisted CFD for data centre cooling.** Open source, runs on your own
machine, and written for electrical and facility engineers rather than CFD
specialists.

You describe a POD or a data hall in the units you already work in — kW per
rack, m³/h per fan wall, CFM/kW, supply temperature, site altitude — and AICFD
derives the geometry, generates an OpenFOAM case, solves it, holds the result
to eleven physical checks, and draws it as plan and sections with the field
underneath.

Everything here is meant to be reproduced and disbelieved by someone else.
There is one pipeline, one spec shape and one command that proves the whole
thing works on a fresh machine:

```bash
python3 -m aicfd verify --solve
```

---

## 1. Install and prove it works

**Any machine, including macOS — Docker.** This is the recommended path and the
only one on a Mac: OpenFOAM v1912 has no native macOS build, and the image
carries it, the Python layer and the three worked results.

```bash
git clone https://github.com/engperini/AICFD.git
cd AICFD
docker compose build        # ~10 min the first time; OpenFOAM is a big package
docker compose up           # the page, at http://localhost:8000
```

The build is not just an install: it runs `doctor`, generates a case from a
spec, produces a Word report and runs the 208 unit tests **inside the image, on
the versions that image has**. If it builds, it works — and if a dependency is
missing it says so at build time rather than on your first real command.

Apple Silicon needs nothing special: `openfoam` v1912 is published for arm64,
so the container runs natively rather than under emulation.

With the container up, `cases/`, `runs/` and `results/` are shared with the
clone, so anything you run is on your disk afterwards:

```bash
docker compose run --rm aicfd python3 -m aicfd verify
docker compose run --rm aicfd python3 -m aicfd run cases/pod-fanwall.yaml
docker compose run --rm aicfd python3 -m aicfd report pod-fanwall
```

**Ubuntu or WSL2 — native.** The reference environment; the image is this,
packaged.

```bash
sudo apt-get install -y openfoam openfoam-examples
pip install -r requirements.txt

python3 -m aicfd doctor      # is OpenFOAM usable here?
python3 -m aicfd verify      # tests + install + mesh every worked case (~1 min)
python3 -m aicfd verify --solve   # the above, plus a short solve and its checks
```

`verify` is the audit. It runs, in order: the unit tests, the OpenFOAM
install check, a full mesh of every case in `cases/` (including the internal
surgery that builds the containment and the fan walls), and — with `--solve` —
a short run of the worked POD that has to pass all eleven physical checks.
Anything that fails names itself and stops.

**What runs with no OpenFOAM at all**, natively on macOS, straight after the
clone — useful for reading a result or checking a spec without the container:

```bash
pip install -r requirements.txt
python3 -m unittest discover tests          # all 208, no solver needed
python3 -m aicfd view --case hall-double-gallery   # the three solved results
python3 -m aicfd report hall-double-gallery        # the Word document
```

## 2. Run a case

```bash
python3 -m aicfd run cases/pod-fanwall.yaml    # one POD, 8 min on one core
python3 -m aicfd view --case pod-fanwall       # the page, at localhost:8000
python3 -m aicfd report pod-fanwall            # the Word report, for circulation
```

The two worked cases in `cases/` are the reference results, and both are
documented end to end in `docs/experiments/`:

| Case | What it is | Mesh | Cost |
|---|---|---|---|
| `pod-fanwall.yaml` | one row of 3 racks, 18 kW, one fan wall | 75 600 cells | 8 min, 1 core |
| `hall-10mw.yaml` | 16 PODs, 768 racks, 9,98 MW, 35 fan walls | 329 280 cells | 11 min, 4 cores |
| `hall-double-gallery.yaml` | 5 PODs, 440 racks, 5,1 MW, a gallery at each end and rows in two blocks | 275 400 cells | settled in 7 min, 4 cores |

Start a study from one of them rather than from a blank file:

```bash
python3 -m aicfd new my-hall --from hall-10mw   # a copy, comments and all
python3 -m aicfd new my-pod                     # the commented blank POD
python3 -m aicfd view --case my-hall            # fill it in on the page
```

## 3. What the tool actually does

The air loop closes inside one box, so every surface that matters is
*internal*. A blockMesh box alone cannot express that, so the mesh is built
and then operated on:

```
blockMesh     one box, six outer walls, nothing else
topoSet       face zones for every internal surface, cell zones for racks
createBaffles turn those zones into real two-sided patches  (-overwrite)
checkMesh     confirm the surgery left a valid mesh
buoyantSimpleFoam   steady RANS, k-epsilon, buoyancy
```

| Part of the room | How it is modelled | Why |
|---|---|---|
| Rack | Darcy–Forchheimer porous block + enthalpy source | a rack is a *resistance*, not a fan: what passes through it is an outcome of the room's pressure field (ADR-011) |
| Fan wall | pair of patches on the same internal faces, both set by **mass** flow | the one place the loop is cut; volume flow would not close, since the air leaving is warmer and thinner (ADR-017) |
| Return grille | cyclic pair carrying a `porousBafflePressure` jump | the datasheet's loss coefficient, applied as physics and then checked against the field (ADR-020) |
| Containment, false ceiling, row ends | two-sided wall baffles | a rack row that is not closed on five sides leaks most of its air sideways (ADR-016) |
| Site | operating pressure from the altitude | a unit selected at 1 880 m moves air 24% lighter than at the coast (ADR-023) |

**Two galleries, one plenum.** A hall longer than about 40 m is built with a
mechanical gallery at *each* end, and its rack rows cut into blocks so each
block is fed from the end nearest to it. `gallery.sides: 2` and `racks.blocks: 2`
do that. The **return stays shared**: the false ceiling covers the whole hall,
so the plenum above it is one volume that collects from every hot aisle and
opens into both galleries — which is the redundancy the layout is bought for
(ADR-027). The one thing it changes in the mesh is which half of each fan
wall's baffle pair is the intake, and that is measured after meshing rather
than assumed.

Nothing in `cases/*.yaml` is an OpenFOAM dictionary. The mesh divisions, the
porosity coefficients, the heat sources, the boundary conditions and the
baffle surgery are all derived from engineering numbers, and the generated
case is a build artifact that is never edited by hand (ADR-004).

## 4. How to audit a result

A steady solver's residuals say how much the last iteration moved, not whether
the answer means anything. So every run is judged by **eleven identities the
physics has to satisfy**, printed by `aicfd run`, written into
`results/<name>/report.md` and shown on the page:

| Check | What it would catch |
|---|---|
| `mass_balance` | the fan walls supplying and drawing different masses |
| `sealed_envelope` | any wall or baffle passing air |
| `no_backflow` | air reversing through a fan intake |
| `energy_closure` | the return air not carrying the installed load |
| `return_path` | the hot aisle, plenum and gallery disagreeing, i.e. a volume still filling |
| `rack_resistance` | the porous zones not delivering the pressure drop they were given |
| `grille_resistance` | the same, for the ceiling grilles |
| `fan_capacity` | the POD costing more than the unit's datasheet offers |
| `settled` | a field still moving between samples |
| `ashrae_inlet` | a rack breathing air above the recommended band |
| `plausible_velocity` | a velocity field no fan or buoyancy could produce |

Three of these were written *because a run passed everything else and was
still wrong*; the story of each is in `docs/DECISIONS.md` and in the
experiment notes. Separately from the checks, the plant is sized against the
design office's rules before any CFD (capacity in kW, airflow at 158 CFM/kW).
A shortfall is an **alert, never a blocker**: a conceptual study wants to see
what an undersized plant does.

**What the coarse mesh can and cannot say.** With one rack per cell in plan,
trust the ranking of racks, the aisle-to-aisle temperatures and the hall-scale
pressure distribution. A single rack's inlet carries 1 to 2 K of uncertainty,
so do not sign off ASHRAE compliance rack by rack from it. Resolve the worst
POD at 0,20 m cells for that.

## 5. Reading the page

`aicfd view` serves two pages. Everything in the software is in English
(ADR-026).

- **Model page** — the input template on the right and what it implies on the
  left. The template is every field of the spec, grouped the way an engineer
  thinks about a hall: site, room, aisles and clearances, racks, fan walls,
  return grilles and containment, mesh and solver. Only the fields the loaded
  spec carries are shown, so a POD cannot be edited into a hall by accident.
  Beside it: the geometry as plan and two sections, the derived numbers (face
  velocities, pressure drops, HVAC sizing against load and CFM/kW), the
  mesh-snapping warnings, and a Run button. This is where a mistake is caught
  *before* paying for a solve.
- **The Word report** — `aicfd report <name>` writes a .docx from the same
  export: cover, summary with the basis of design, methodology with the
  geometry and the boundary conditions, then the checks, the convergence, the
  field maps, every rack and every unit, the conclusions and — in the document
  itself, not a footnote — what this model cannot be asked (ADR-028). It reads
  `results/<name>/` and nothing else, so a figure in the document and a view on
  the screen are two renderings of one result. It is the only part of AICFD
  that needs `python-docx` and `matplotlib`.
- **Results page** — the same three drawings with the solved field underneath
  (temperature, speed, pressure) in contour bands with the ASHRAE limits drawn
  on the legend (ADR-024); every rack painted by the temperature of the air it
  breathes, with the warmest listed and all of them downloadable as CSV; the
  eleven checks; the residual history.

## 6. The repository

```
aicfd/
  model.py     the geometry: spec -> boxes, panels, rows, fan walls, sensors.
               The single source of truth; the drawing and the mesh both read it
  case.py      the OpenFOAM case generator (blockMesh, topoSet, createBaffles,
               fvOptions, boundary conditions, the parallel pipeline)
  post.py      the analysis: patch flows, the eleven checks, per-rack inlets,
               live sampling during a run, the viewer export
  run.py       the only place that shells out to OpenFOAM (isolated env, ADR-002)
  report.py    the Word deliverable, built from the export and nothing else
  figures.py   its figures: plan, sections and charts, all 2-D
  palette.py   the page's colour ramps in Python, checked against the original
  server.py    the page's backend: model payload, parameter edits, run control
  cli.py       every command, including `verify`
  foam/        readers for OpenFOAM's own formats (fields, polyMesh, solver log)
web/
  index.html   model page          results.html  results page
  drawing.js   plan and sections, shared by both pages
  maps.js      the field maps and the contour legend
  racks.js     every rack by its inlet temperature
  colormaps.js perceptual ramps and the banded scale
  data.js  convergence.js  app.js  results.js
cases/         the worked case specs, commented line by line
results/       exported results: viewer.json, fields.bin, report.md
docs/
  DECISIONS.md the numbered decision record (ADR-001 onwards) -- why, not what
  ROADMAP.md   what exists, what is next, what is deliberately not done
  experiments/ dated write-ups of real runs, including the faults they found
tests/         186 unit tests, no OpenFOAM required
```

`runs/` holds solved cases and is not tracked: it is regenerated by
`aicfd run`.

## 7. Where the reasoning lives

- **`docs/DECISIONS.md`** is the audit trail. Every modelling choice is a
  numbered ADR with the alternative that was rejected and the measurement that
  settled it. Superseded decisions stay in the file, marked, because deleting
  them would delete the reasoning.
- **`docs/experiments/`** holds what actually happened on real runs: the fan
  wall that let air blow backwards, the rack row that delivered a fifth of its
  rated resistance because three of its faces were open, the 10 MW hall whose
  first rack in every row is starved by the aisle mouth.
- **`.claude/skills/aicfd/SKILL.md`** is the same knowledge shaped for an AI
  agent driving the tool.

## 8. Limits

- Steady state only. Transients (a unit failing, a door opening) are out of
  scope for now.
- Containment is modelled as perfect. Real containment leaks.
- No comparison against measurement yet. Every validation is an identity the
  physics must satisfy, which catches wrong models but cannot promise the real
  room behaves this way.
- Only the fan-wall architecture with a ceiling-plenum return. Raised-floor
  plenums, in-row and downflow units are not modelled yet (see ROADMAP).
- One scenario per run, one load per rack, and the coil's catalogue capacity. A
  failure case (units out of service), a per-rack load map, and the capacity a
  coil really has at the return temperature the room produces are the next
  three inputs — and they are what an independent study of a real hall of this
  shape was commissioned to answer. The gap is written out line by line in
  [`docs/reference-report-parameters.md`](docs/reference-report-parameters.md).
