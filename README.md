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

Two pages, and they take the case differently:

| | address | |
|---|---|---|
| **model** | `http://localhost:8000/web/` | the spec and the geometry it implies, before any solve. The case is the one the server was started with, because this page asks the server for it — `docker compose up` opens `hall-double-gallery`; change the line in `docker-compose.yml`, or run `aicfd view --case <name>`. |
| **results** | `http://localhost:8000/web/results.html?case=hall-double-gallery` | a solved result. This page is static: it resolves `?case=` against `results/` first and `reference/` second, so any name with an export works — `hall-double-gallery`, `hall-10mw`, `pod-fanwall`. |

**Your results shadow the shipped ones.** The three worked results are tracked
under `reference/` and the tool never writes there; anything you solve goes to
`results/`, which is not tracked (ADR-032). So the page has something to show
the moment you clone, re-running a worked case can never collide with the copy
in the repository, and `git pull` does not fight your own runs.

The build is not just an install: it runs `doctor`, generates a case from a
spec, produces a Word report and runs the unit tests **inside the image, on the
versions that image has**. If it builds, it works — and if a dependency is
missing it says so at build time rather than on your first real command.

**After that, `git pull && docker compose up` is enough.** The image carries the
slow half — OpenFOAM and the Python dependencies — and the code comes from your
working copy, so a pull takes effect immediately with no rebuild (ADR-034).
Rebuild only when `requirements.txt` or the `Dockerfile` changes.

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
python3 -m unittest discover tests          # the whole suite, no solver needed
python3 -m aicfd view --case hall-double-gallery   # the three solved results
python3 -m aicfd report hall-double-gallery        # the Word document
                                                  # (or the button on the results page)
```

## 2. Run a case

```bash
python3 -m aicfd run cases/pod-fanwall.yaml    # one POD, 8 min on one core
python3 -m aicfd view --case pod-fanwall       # the page, at localhost:8000
python3 -m aicfd report pod-fanwall            # the Word report, for circulation
python3 -m aicfd stop pod-fanwall              # stop a run without losing it
```

**Stopping a run.** `aicfd stop`, and the Stop button on the page, ask the
solver to finish the iteration it is on, write the field and exit — OpenFOAM's
own mechanism, not a signal (ADR-031). The run then exports and is held to the
same eleven checks, so what you get is a *result*: partial, and honest about it.
A run cut short before it settled fails `settled`; one stopped after it settled
passes all eleven, which is how `results/pod-fanwall` was produced.

The three worked cases in `cases/` are the reference results, and each is
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
| Fan wall capacity | a table of manufacturer selections, read at the return air the unit really gets | a coil's rating is true at one return temperature and no other: the worked CA80 delivers 505 kW at 35 °C and 734 kW at 41 (ADR-036) |

**Two galleries, one plenum.** A hall longer than about 40 m is built with a
mechanical gallery at *each* end, and its rack rows cut into blocks so each
block is fed from the end nearest to it. `gallery.sides: 2` and `racks.blocks: 2`
do that. The **return stays shared**: the false ceiling covers the whole hall,
so the plenum above it is one volume that collects from every hot aisle and
opens into both galleries — which is the redundancy the layout is bought for
(ADR-027). The one thing it changes in the mesh is which half of each fan
wall's baffle pair is the intake, and that is measured after meshing rather
than assumed.

**A supply plenum, where the hall has one.** `plenum.enabled` doubles the wall
between the hall and the mechanical gallery: the units stay in the leaf they
were always in, a second leaf stands `plenum.depth` (1,2 m) into the hall, and
the cavity between them is pressurised. The inner leaf carries a supply grille
in front of each cold aisle — as tall as a rack, 2 m wide, at the free area
its component states, and any of them can be shut. The room is then fed by the
grilles rather than by the units, which is what makes the supply even along
the wall and aimed at the aisles. The hall is longer by the depth of the
cavity, so the clearances a case asks for are unchanged (ADR-058).
`cases/pod-plenum.yaml` is `pod-fanwall` with that one change, for comparing
the two.

The velocity through the supply grilles — the flow over their gross face — is
reported among the derived numbers beside the fan wall's own, and nothing
passes judgement on it: what is high in one hall is ordinary in another. What
is checked before any run, because it is the machine's own datasheet
answering, is whether the units have the pressure for what the loop costs
(ADR-059). The worked 5 MW hall with default
2 m grilles fails the first at 7,5 m/s and 35 Pa, and says how much grille it
would take (ADR-059).

**A mesh leaf instead of grilles.** `plenum.as_mesh` doubles the wall the same
way and closes the hall side with the same 13 x 13 mm woven mesh used on the
return, open over its whole face, rather than with a wall with grilles in it.
It aims nothing — where each unit points is still where its air goes — and it
is a wall as far as clearance goes, so the room is the room the plenum gives.
Only a case with no plenum at all is shorter (ADR-060). `cases/pod-mesh.yaml`
is that case, against `cases/pod-plenum.yaml`'s grilles.

Editing a case from the page keeps the file: only the values that changed are
rewritten, comments and layout and all, and an Apply that changes nothing
leaves the file byte for byte as it was (ADR-061).

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
| `return_path` | the air the units draw not being the air the aisles passed, i.e. a leak into the return or a volume still filling |
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

`aicfd view` serves five pages. Everything in the software is in English
(ADR-026).

- **Model page** — the input template on the right and what it implies on the
  left. The template is every field of the spec, grouped the way an engineer
  thinks about a hall: site, room, aisles and clearances, racks, fan walls,
  return grilles and containment, mesh and solver. Only the fields the loaded
  spec carries are shown, so a POD cannot be edited into a hall by accident.
  Beside it: the geometry as plan and two sections, the derived numbers (face
  velocities, pressure drops, HVAC sizing against load and CFM/kW), the
  mesh-snapping warnings, and a Run button. Each drawing takes a full-width
  row and carries its own `−`/`+` magnification up to 8×, the same as on the
  results page, so a containment gap or where a fan wall edge lands on the
  mesh grid can be looked at closely. This is where a mistake is caught
  *before* paying for a solve.
- **Equipment page** — the fan wall itself:
  `http://localhost:8000/web/equipment.html?model=CA80NPVG6`, or the unit's
  name beside the fan wall group on the model page. Three cards, all editable:
  who the machine is (manufacturer, model, a note), the **design selection**
  the coil is fitted to (return, supply, airflow, sensible capacity, fan
  power, entering and leaving water), and the **P–Q curve** — static pressure
  against airflow, a row at a time. Save writes the YAML back in place; **Save
  as new unit** writes a second file under `equipment/` and leaves the
  original untouched, so a variant is a new machine rather than an edit to the
  one a solved result already cites.

  The design selection is what the coil model is built from (ADR-039,
  ADR-042): one
  line of a manufacturer's selection — the air it was selected at and the
  water it was selected with — fixes the conductance, and from there the unit
  answers at any return temperature and any airflow the room gives it. Edit
  those five numbers and every capacity in the next run follows.

- **Components page** — `http://localhost:8000/web/components.html`, or
  `components ▸` beside the return-air group on the model page. The surfaces
  the air passes through, each described by one number — its open area over
  its gross area — with the loss coefficient that follows shown live beside
  it: the grilles in the false ceiling, the woven mesh closing the return
  plenum into a mechanical gallery, the plates of a raised floor, the leakage
  a containment has, and the share of the IT load that power distribution
  dissipates. These are house standards rather than per-case numbers, so
  saving one changes every element of that kind in every case that names it
  (ADR-048). Three of them are marked as not yet read by the solver, and the
  page says what a run answers instead.

- **Rack page** — `http://localhost:8000/web/racks.html`, or `positions ▸`
  beside the rack group. The hall's standard load at the top and every
  position below it, each free to carry its own — zero included, which is a
  cabinet that exists and dissipates nothing (ADR-054). Filter to a row or a
  block and set what it shows in one go. The case file stores only the
  positions that differ, so raising the standard later moves every rack that
  never disagreed with it, and everything here reaches the solver: a
  position's load is its own heat source. An empty one keeps the row's
  resistance, because an empty cabinet is blanked rather than left open.

- **The Word report** — `aicfd report <name>`, or the **Word report** button on
  the results page, writes a .docx from the same export: cover, summary with the
  basis of design, methodology with the geometry, the boundary conditions and
  the cooling unit's own capacity table, then the checks, the convergence, the
  field maps, every rack and every unit, the conclusions and — in the document
  itself, not a footnote — what this model cannot be asked (ADR-028). It reads
  the export and nothing else, so a figure in the document and a view on the
  screen are two renderings of one result, and it writes into `reports/<name>/`
  so producing a document never modifies a result. It is the only part of AICFD
  that needs `python-docx` and `matplotlib`.
- **Results page** — the same three drawings with the solved field underneath
  (temperature, speed, pressure) in contour bands with the ASHRAE limits drawn
  on the legend (ADR-024); every rack painted by the temperature of the air it
  breathes, with the warmest listed and all of them downloadable as CSV; the
  eleven checks; the residual history.

## 6. The repository

```
aicfd/
  model.py     the geometry: spec -> boxes, panels, rows, fan walls, stations.
               The single source of truth; the drawing and the mesh both read it
  case.py      the OpenFOAM case generator (blockMesh, topoSet, createBaffles,
               fvOptions, boundary conditions, the parallel pipeline)
  post.py      the analysis: patch flows, the eleven checks, per-rack inlets,
               live sampling during a run, the viewer export
  equipment.py the unit library: capacity against the air a coil receives
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
equipment/     one file per fan wall model: the manufacturer's selections, as
               a table. `fanwall.model: <name>` in a spec is enough to
               describe the machine
reference/     the worked results, tracked: the evidence the README and
               the ADRs cite. The tool never writes here
results/       what YOU solve -- viewer.json, fields.bin, report.md, the Word
               report -- and what the page prefers. Not tracked
docs/
  DECISIONS.md the numbered decision record (ADR-001 onwards) -- why, not what
  ROADMAP.md   what exists, what is next, what is deliberately not done
  experiments/ dated write-ups of real runs, including the faults they found
tests/         the unit tests, no OpenFOAM required
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
- **`docs/CASE-AUTHORING.md`** is the procedure for the other direction: a real
  project — drawings, a mechanical specification, a fan wall selection, a rack
  schedule — turned into one case file. Written for an agent doing that work,
  and held to the code by a test, because a stale manual is worse than none
  when the reader cannot tell (ADR-090).
- **`.claude/skills/`** is the same knowledge shaped for an AI agent: `aicfd`
  drives the tool, `case-authoring` turns a real project into a case file
  (it carries its own copy of the manual, so it works uploaded on its own),
  and `datacenter-cfd` holds the physics.

## 8. Limits

- Steady state only. Transients (a unit failing, a door opening) are out of
  scope for now.
- Containment is modelled as perfect. Real containment leaks.
- No comparison against measurement yet. Every validation is an identity the
  physics must satisfy, which catches wrong models but cannot promise the real
  room behaves this way.
- Only the fan-wall architecture with a ceiling-plenum return. Raised-floor
  plenums, in-row and downflow units are not modelled yet (see ROADMAP).
- One scenario per run, and one load per rack. A failure case (units out of
  service) and a per-rack load map are the next two inputs; the gap is written
  out line by line in
  [`docs/reference-report-parameters.md`](docs/reference-report-parameters.md).
  The third — the capacity a coil really has at the return temperature the
  room produces — is now covered by the equipment library (ADR-036), for any
  unit whose selections you have.
