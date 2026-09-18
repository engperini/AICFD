---
name: aicfd
description: Drive the AICFD toolchain in this repository - build a POD or data hall case, run it, read the validation report, and explain the result to a non-CFD engineer. Use whenever the user asks to simulate, run, check, size or visualise a data hall / POD / rack cooling case here, or asks what a result means. Triggers on "rodar a simulação", "simular a sala", "aicfd run", "quantos graus no rack", "abre o visualizador", "o resultado está bom?", "converged?", "ASHRAE", "quantos fan walls", or any request to build a case from a room description, a datasheet, a DCIM spreadsheet or an IFC/Revit export. For the underlying physics and OpenFOAM modelling choices, use the datacenter-cfd skill instead.
---

# Driving AICFD

The assistant layer described in `docs/DECISIONS.md` (ADR-006). It drives the
CLI. It never edits generated OpenFOAM dictionaries by hand, and it never
states a number the tool did not produce.

Start by reading `README.md` if you have not: it is the method in one page.

## The loop

```
aicfd new NAME [--from CASE] -> cases/NAME.yaml  (blank POD, or a copy of a worked case)
aicfd build cases/NAME.yaml -> runs/NAME/case    (generate only, no solve)
aicfd run cases/NAME.yaml   -> runs/NAME         (generate, solve, sample, export)
aicfd post NAME             -> results/NAME/{viewer.json,fields.bin,report.md}
aicfd view --case NAME      -> http://localhost:8000/web/?case=NAME
aicfd doctor                # when something looks broken
aicfd verify [--solve]      # the audit; run it first in a fresh sandbox
```

Run them as `python3 -m aicfd <command>`. There is **one** spec shape and one
pipeline: no generator to choose, no flag to remember.

Costs on 4 cores: the worked POD is ~8 minutes serial (75 600 cells), the 10 MW
hall ~11 minutes on 4 (329 280 cells). Launch a run detached
(`setsid nohup ... &`) and watch `runs/NAME/sensors.json`; never block a tool
call on a long solve, and never poll in a tight loop.

## Two case shapes, one model

Both are the same geometry code (ADR-022); the spec's shape picks the layout.

- **A POD** — `racks.count` and no `pods:`. One row, a contained hot aisle
  against the far wall, one fan wall. `cases/pod-fanwall.yaml`.
- **A data hall** — `pods: <n>` and `racks.per_row`. That many row-HAC-row
  pairs across the hall, a cold aisle between pairs and a perimeter aisle round
  the edge, and `fanwall.count` units spread along the gallery wall.
  `cases/hall-10mw.yaml`.

A long hall takes two more inputs, both optional (ADR-027):
`gallery.sides: 2` puts a mechanical gallery at each end and splits
`fanwall.count` between them; `racks.blocks: 2` cuts every row into blocks
separated by `aisles.transverse`, each its own contained volume, each fed by
the gallery at its end. The **return plenum stays single** and opens into both
galleries — say so when explaining the layout, because it is the reason the
arrangement is used. `cases/hall-double-gallery.yaml` is the worked case.

Start from the worked case that matches and change numbers:
`aicfd new <name> --from hall-10mw` copies it with every comment and datasheet
reference intact. Do not write a spec from memory.

The page is the same template: every field of the spec, grouped by section,
showing only what the loaded spec carries. Point the user at it
(`aicfd view --case <name>`) rather than dictating YAML to them.

## Reading a run

`aicfd run` samples itself while it solves: every `solver.sensor_interval`
iterations it records four places (cold aisle, hot aisle, ceiling plenum,
behind the fan walls), the mass and energy balances, and the rise across each
unit, into `runs/NAME/sensors.json`. The page plots the same thing live.

**Judge a run by its checks, never by its residuals.** The report prints
eleven, and all eleven have to pass before you quote a temperature:

`mass_balance`, `sealed_envelope`, `no_backflow`, `energy_closure`,
`return_path`, `rack_resistance`, `grille_resistance`, `fan_capacity`,
`settled`, `ashrae_inlet`, `plausible_velocity`.

What a failure means:

- **`energy_closure` below 100%** — the field has not filled. More iterations.
- **`return_path` spread** — a volume (usually the plenum) is still filling,
  even if everything else looks settled. More iterations.
- **`settled` fails** — the places are still moving; the answer is not steady.
- **`no_backflow` fails** — the fan wall boundary is deciding the flow rather
  than the room. The numbers are not about this design.
- **`sealed_envelope` fails** — the mesh surgery leaked. Re-run `aicfd build`
  and compare the face counts in `log.checkMesh` against the areas the
  generator printed.
- **`rack_resistance` or `grille_resistance` off by more than 25%** — the model
  is not delivering the resistance it was given. Do not quote a fan pressure.
- **`fan_capacity` fails** — the design costs more than the unit's datasheet
  offers at that flow.

Separately, the model raises **alerts** (not checks) when the plant is
undersized: installed capacity against IT load, and installed airflow against
the racks' demand at `racks.airflow_cfm_per_kw` (158 CFM/kW by default). These
warn and never block, because seeing what an undersized plant does is a
legitimate study (ADR-023).

## What to say about a result

- **Read `results/NAME/report.md`.** Never describe a result from the solver
  log or the raw fields.
- **The number that matters is the worst rack's inlet**, measured at the *top*
  of the rack, where recirculating or leaking air arrives first. The page and
  the report both lead with it.
- **State the resolution honestly.** On a hall mesh (one rack per cell in
  plan), the ranking of racks and the hall-scale pressure field are the result;
  a single rack's inlet carries 1 to 2 K. Do not sign off ASHRAE compliance
  rack by rack from a hall run.
- **Quote the pressure the way the fan wall sees it**: the rise across the most
  loaded unit against the datasheet's external static pressure.
- If the user asks for a number the run does not contain, say so and offer the
  run that would produce it.

## Datasheet inputs, and what each buys

- `fanwall.airflow_m3h`, `capacity_kw`, `power_kw`, `count` — per unit, as a
  datasheet gives them. Feed the sizing alerts and the reports (ADR-023).
- `fanwall.static_pressure_pa` and `curve` (m³/h, Pa points) — the solve still
  imposes the rated flow, as an EC array under flow control does. They feed
  `fan_capacity` and the uncontrolled operating point. Ask for the real curve;
  a worked case that carries a representative one says so in a comment.
- `grilles.loss_coefficient` (K on the gross face velocity) or `free_area`
  (Idelchik fallback, under-reads a vaned grille). Feeds `grille_resistance`
  (ADR-020).
- `site.altitude_m` — sets the operating pressure of every field. A unit
  selected at altitude moves lighter air; getting this wrong shifts the return
  temperature by kelvins (ADR-023).
- `mesh.cell_size` may be `[x, y, z]` (ADR-021). Keep z fine enough that the
  false ceiling, the fan wall top and the rack tops land on cell faces. The
  model warns when a plane does not, and snaps it. On a hall with blocks, keep
  `aisles.perimeter` and `aisles.transverse` on the x cell too, or every block
  edge is snapped by up to half a cell.

## What the tool cannot yet be asked

A user who has seen a consultant's CFD report will ask for these. Say plainly
that they are not modelled rather than approximating them, and point at
`docs/reference-report-parameters.md`, which compares AICFD line by line
against one such study of a real hall:

- **a failure case** (units out of service, N+2 against N) -- one scenario per
  run today;
- **the capacity a coil actually has** at the return temperature the room
  produces, as opposed to its catalogue rating at the selection point. This is
  the number that decides whether a plant has reserve, and `fan_capacity`
  today is a pressure check, not this;
- **a per-rack load map**, including unloaded positions;
- **PDU or other ancillary heat** outside the racks.

## Rules

1. **Never invent an input.** Fan wall airflow, capacity and rack load set the
   answer. If the user does not know one, say so and ask for the nameplate, or
   work backwards from a target and say that is what you did.
2. **Check `aicfd build` before solving.** It prints the derived geometry, the
   face velocities, the HVAC sizing and the mesh-snapping warnings. A bulk ΔT
   under 2 K or over 25 K means the spec is wrong, and a long solve will not
   fix it.
3. **A run that fails its checks is not a result.** Report the failure and what
   it implies, never the temperatures behind it.
4. **The spec is the deliverable**, not the OpenFOAM case. It is what the user
   edits, re-runs and keeps (ADR-004).
5. `functionObject`s and `postProcess` are broken in this OpenFOAM build (an
   `OSHA1stream` fault — see the note in `aicfd/case.py`). Sampling already
   covers it; do not reach for them.
6. **The software is English, end to end** (ADR-026): the page, the specs and
   their comments, the reports, the CLI, the code and the docs. Reply to the
   user in whatever language they write in; that is conversation, not
   software.
7. **Record what you learn.** A modelling decision goes in `docs/DECISIONS.md`
   as a numbered ADR; a real run that found something goes in
   `docs/experiments/` with its numbers. That record is the reason the next
   engineer can trust this.
