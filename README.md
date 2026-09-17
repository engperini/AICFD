# AICFD

**AI-assisted CFD for data center cooling.** Open source, runs on your own
machine, and built for electrical and facility engineers rather than CFD
specialists.

You describe a room in the units you already work in — kW per rack, m³/h per
CRAC, supply temperature — and AICFD generates an OpenFOAM case, solves it,
checks the result against physics, and renders it in your browser.

<!-- Add docs/images/viewer.png once a realistic multi-rack case exists. -->

## Why

Commercial data hall CFD costs tens of thousands per seat. Underneath, it runs
the same physics OpenFOAM does: steady-state Navier-Stokes with buoyancy, racks
and perforated tiles as Darcy-Forchheimer porous media, CRAC units as velocity
patches. What those tools really sell is the interface. AICFD rebuilds that
interface on free software.

## Quick start

### With Docker (any OS)

```bash
git clone https://github.com/engperini/AICFD && cd AICFD
docker compose up --build        # builds the image, serves the viewer
```

Then open <http://localhost:8000/web/?case=reference-case>.

To solve a case inside the container:

```bash
docker compose run --rm aicfd python3 -m aicfd run cases/datahall-small.yaml
```

### Without Docker (Ubuntu / WSL2)

```bash
sudo apt-get install -y openfoam openfoam-examples
pip install -r requirements.txt

python -m aicfd doctor                      # confirm OpenFOAM is usable
python -m aicfd run cases/datahall-small.yaml   # 6 racks, 48 kW, 2 CRACs
python -m aicfd view                        # open the viewer
```

## Describing a room

A case is one YAML file, in the units you already use:

```yaml
name: datahall-a
room:
  size: [10.0, 8.0, 3.0]        # x (along the airflow), y (across), z (height), m
racks:
  - {id: A1, position: [3.0, 1.0], size: [1.0, 0.6, 2.0], load_kw: 8.0}
  - {id: A2, position: [3.0, 1.7], size: [1.0, 0.6, 2.0], load_kw: 12.0}
cracs:
  - {id: CRAC01, airflow_m3h: 6000, supply_temp_c: 20.0}
mesh: {cell_size: 0.10}
```

`aicfd new <name>` writes a starter file; `aicfd build` shows you what it derived
before committing to a solve:

```
Case 'datahall-small'
  Room            8 x 5 x 3 m
  Mesh            80x50x30 = 120,000 cells (0.100 m)
  IT load         48.0 kW across 6 rack(s)
  Supply air      12,000 m3/h at 20.0 degC
  Supply velocity 0.222 m/s (over 15.00 m2)
  Design bulk dT  12.0 K
```

You never see an OpenFOAM dictionary. The supply patch velocity, the rack
resistance coefficients, the enthalpy sources and the mesh divisions are all
derived from the numbers above — and the spec is validated before anything runs,
so a rack outside the room or a mesh too fine to finish is caught in
milliseconds rather than after a ten-minute solve.

## What you get

`aicfd run` solves a case and `aicfd post` turns it into three things:

- **`results/<case>/report.md`** — operating point, per-rack inlet temperatures
  against the ASHRAE envelopes, and a pass/fail list of physical checks.
- **`results/<case>/viewer.json` + `fields.bin`** — the web viewer's payload.
- **A verdict.** A converged solve is not automatically a correct one. Every
  run is checked for mass balance, monotonic heating along the flow path,
  residual thresholds, and — the one that catches real mistakes — whether each
  rack's cell zone actually contains cells. An empty zone converges beautifully
  to a room with no heat load in it.

The viewer shows the room in 3D with a draggable slice plane, air temperature
judged against the ASHRAE recommended band, temperature rise above supply, air
speed, a numeric readout under the cursor, and the residual history.

## Commands

| Command | What it does |
|---|---|
| `aicfd new <name>` | Write a starter room spec into `cases/<name>.yaml` |
| `aicfd build <spec.yaml>` | Generate the OpenFOAM case and print what was derived |
| `aicfd doctor` | Check that the OpenFOAM utilities are installed and reachable |
| `aicfd run <spec.yaml>` | Generate, solve into `runs/`, then post-process |
| `aicfd post <name>` | Re-run post-processing on an existing run |
| `aicfd view [--case N]` | Serve the viewer on <http://localhost:8000> |

Run them as `python -m aicfd <command>`.

## With the AI assistant

AICFD ships two [Claude Code](https://claude.com/claude-code) skills in
`.claude/skills/`. Open this repository in Claude Code and describe what you
want in plain language — it will build the case, run it, read the report, and
tell you what the result means:

> "Simulate a 10 × 8 × 3 m room with 12 racks at 8 kW and two CRACs at
> 25,000 m³/h supplying 18 °C. Which racks are outside the ASHRAE envelope?"

The assistant drives the same CLI you would, so nothing it does is a black box,
and the tool is fully usable without it. No API key, no per-use cost.

## Status

Working today: describe a multi-rack room in YAML, solve it, validate it, and
read it in the browser. The `docs/experiments/` notes record what was measured,
including
[the reference case's 52x over-ventilation](docs/experiments/2026-09-17-reference-case.md)
— the finding that shaped how airflow is specified.

Next: engineering KPIs (RCI, RTI, recirculation and bypass fractions, N+1
failure scenarios), then containment, raised-floor plenums and in-row units, then
IFC import from a federated Revit model.

Full plan: [`docs/ROADMAP.md`](docs/ROADMAP.md).
Design decisions and their reasons: [`docs/DECISIONS.md`](docs/DECISIONS.md).

## Limitations — read these before trusting a result

- **Room-level answers only.** Racks are porous zones, so AICFD can tell you the
  air temperature at a rack inlet. It cannot tell you a component temperature
  inside a server.
- **Steady state.** No thermal ride-through or transient failure analysis.
- **No radiation.** Negligible next to forced convection in a data hall, but
  wrong if you have large glazing or solar gain.
- **Per-rack airflow is a result, not an input.** A rack is a flow resistance,
  not a fan, so the air it actually gets falls out of the solution. That is
  deliberate — it is what makes recirculation and air starvation visible — but it
  means you cannot pin a rack to its rated CFM.
- **One CRAC topology.** Today every CRAC is a fan wall on the upstream face.
  Downflow and in-row units are planned.
- **A validated run is not a validated model.** The checks confirm the solver
  produced a self-consistent answer to the question you asked. Whether the
  boundary conditions describe your actual room is on you — and the reference
  case is a worked example of how badly that can go (its fan wall moves 52× the
  air its load needs).

## Development

```bash
python -m unittest discover tests
```

The suite uses only `unittest` plus the project's own two dependencies. It
covers the spec validation and unit conversions, the case generator (including a
round trip through the reader), the OpenFOAM field and log parsers, and the KPI
pass. Cases that need a solved run skip cleanly if you have not run one.

## License

MIT. OpenFOAM itself is GPL-3.0 and is not redistributed here — the Docker image
installs it from the Ubuntu archive at build time.
