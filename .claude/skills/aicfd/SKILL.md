---
name: aicfd
description: Drive the AICFD toolchain in this repository - run a data center cooling simulation, post-process it, read the validation report, and explain the result to a non-CFD engineer. Use whenever the user asks to simulate, run, check, or visualise a data hall / server room / rack cooling case here, or asks what a result means. Triggers on "rodar a simulação", "simular a sala", "aicfd run", "quantos graus no rack", "abre o visualizador", "o resultado está bom?", "converged?", "ASHRAE", or any request to build a case from a room description, a DCIM spreadsheet, or an IFC/Revit export. For the underlying physics and OpenFOAM modelling choices, use the datacenter-cfd skill instead.
---

# Driving AICFD

This skill is the assistant layer described in `docs/DECISIONS.md` (ADR-006). It
drives the CLI — it never edits generated OpenFOAM dictionaries by hand, and it
never invents numbers the tool did not produce.

## The loop

```
aicfd new NAME              -> cases/NAME.yaml   (a starter room spec)
aicfd build cases/NAME.yaml -> runs/NAME/case    (generate only, no solve)
aicfd run cases/NAME.yaml   -> runs/NAME         (generate, solve, then post)
aicfd post NAME             -> results/NAME/{viewer.json,fields.bin,report.md}
aicfd view --case NAME      -> http://localhost:8000/web/?case=NAME
aicfd doctor                # only when something looks broken
```

`aicfd run` also accepts a case *directory* (that is how the bundled reference
case runs), but for anything the user describes to you, write a spec.

Run them as `python -m aicfd <command>` (or `python3` inside Docker).

A full solve of the 72k-cell reference case takes ~90 s. Run it in the
background and keep working; do not poll it in a tight loop.

## Two case shapes, and how to tell them apart

There are two generators, and the spec's shape picks one -- there is no flag.

- **A room** (`room:` + `cracs:`) is the M2 shape: supply and return are faces
  of the domain. `aicfd/case.py` builds it.
- **A fan-wall POD** (`gallery:` + `fanwall:`) closes its air loop inside one
  box: fan wall, cold aisle, racks, contained hot aisle, ceiling grilles,
  return plenum, mechanical gallery. Every surface that matters is internal, so
  `aicfd/podcase.py` meshes a box and then operates on it with `topoSet` and
  `createBaffles` (ADR-016). `cases/pod-fanwall.yaml` is the worked example.

- **A data hall** is the POD shape with `pods:` instead of `racks.count`:
  that many row-HAC-row pairs across the hall, a cold aisle between pairs and a
  perimeter aisle round the edge, and one fan wall (`fanwall.width`) in front
  of every cold aisle. `fanwall.airflow_m3h` is *per unit*. The hall's length
  and width are derived from the arrangement; give `hall.height` and
  `hall.ceiling` only. `cases/hall-10mw.yaml` is the worked example (ADR-022):
  16 PODs of 2 x 24 racks, 329 280 cells at 0,6 x 0,3 x 0,25 m, solved on 4
  cores with `solver.processors: 4`.

Do not mix keys between them. If the user describes a POD -- a fan wall, a
false ceiling with return grilles, hot-aisle containment, a mechanical gallery
-- start from `cases/pod-fanwall.yaml` and change the numbers. If they describe
a whole hall of such PODs, start from `cases/hall-10mw.yaml`.

**A hall run is read by its racks.** The report and the results page list the
warmest racks by the inlet temperature *at the top of the rack* (the worst
point, where recirculating or leaking hot air arrives first) and paint every
rack by it over the plan. The fan wall line names the most loaded unit; the
`fan_capacity` check compares that unit against the datasheet. With one rack
per cell in plan, trust the ranking of racks and the hall-scale pressure and
temperature distribution; quote a single rack's inlet with 1-2 K of
uncertainty and do not sign off ASHRAE compliance rack by rack from it.

## Reading a POD run

`aicfd run cases/<pod>.yaml` samples itself while it solves: the solver writes
its fields every `solver.sensor_interval` iterations and AICFD records four
instrumented places (cold aisle, contained hot aisle, ceiling plenum, back of
the fan wall), three points each, plus the mass and energy balances. The page
shows the same thing live.

Each place reports temperature, **static pressure relative to the fan intake**,
and speed. The pressure is `p_rgh`, so the hydrostatic column is out of it and
the numbers are what a manometer would read. Their differences are the POD's
pressure budget and they must sum to the fan wall's rise — cold aisle to hot
aisle is the drop across the racks, hot aisle to plenum is the grilles, plenum
to intake is the return path. That budget closing is another independent check
on the geometry, and `fan_rise_pa` is what sizes the machine.

**Judge a POD run by its energy balance and its drift, never by its residuals**
(ADR-018).
Every watt installed has to leave through the fan intake as warmer air. The
report prints it as "the return air carries X kW of the Y kW installed". A run
whose residuals are falling nicely and whose closure is 5% has converged on
nothing -- the thermal field has not filled the domain yet. Watch it across
samples: climbing means unconverged, settling somewhere other than 100% means
wrong.

Three checks carry the verdict and **none of them is sufficient alone**:

- `energy_closure` — the return air carries the installed load. A warm-seeded
  run satisfies this from iteration one, so it proves nothing by itself.
- `settled` — no instrumented place moved more than 0,25 K since the last
  sample. Measures speed, not distance: a plenum 3 K from its answer and
  closing at 0,1 K per hundred iterations passes.
- `return_path` — the hot aisle, the plenum and the back of the fan wall read
  within 1,5 K of each other. Nothing heats or cools the air between them, so
  at steady state they must agree; a spread is a volume still filling.

Datasheet inputs the spec takes, and what each buys (ADR-020):

- `fanwall.static_pressure_pa` and `fanwall.curve` (m³/h, Pa points) — the
  solve still imposes the rated flow, as an EC array under flow control does.
  The curve feeds `fan_capacity` (margin at the rated flow) and the reported
  uncontrolled operating point. Ask the user for the real curve; the worked
  case carries a representative one with the datasheet point on it and says so.
- `grilles.loss_coefficient` (K on the gross face velocity, from a datasheet
  dp-at-velocity point) or `grilles.free_area` (Idelchik fallback, under-reads
  a vaned grille). Feeds `grille_resistance`.
- `mesh.cell_size` may be `[x, y, z]` (ADR-021). Keep z fine enough that the
  false ceiling and rack tops land on cell faces; the model warns if not.

Checks that are not about convergence — `rack_resistance` and `grille_resistance`: it compares the
pressure drop the field delivers across the rack row against the one the rack's
own curve demands at the airflow the fan is measurably moving. **It currently
fails** — 5,3 Pa delivered against 25,8 asked. Until it passes, **never quote a
fan static pressure from a POD run**, and never compare one against a
datasheet: the model's system resistance is known to be too low. The rack
resistance in the summary table is closed form and safe to quote; the fan rise
in the sensor strip is not.

Rules that follow from that:

- **Never quote a rack temperature unless all three pass.** Say which failed
  and give the number: "closure 10%, still filling", or "closure 100% and
  settled, but the plenum is 3,2 K below the hot aisle feeding it".
- **If `no_backflow` fails**, the fan wall boundary is deciding the flow rather
  than the room. The numbers are not about the POD.
- **If `sealed_envelope` fails**, the mesh surgery leaked. Re-run
  `aicfd build` and check the face counts in `log.checkMesh` against the
  areas the generator printed.
- `functionObject`s and `postProcess` do not work in this OpenFOAM build (an
  `OSHA1stream` fault -- see the note in `aicfd/podcase.py`). Do not reach for
  them; sampling already covers it.

## Turning a description into a spec

When the user describes a room, write `cases/<name>.yaml` and run it. The spec
is the deliverable, not the OpenFOAM case -- it is what they can edit, re-run
and keep (ADR-004).

```yaml
name: datahall-a
room:
  size: [10.0, 8.0, 3.0]      # x along the airflow, y across, z up
racks:
  - {id: A1, position: [3.0, 1.0], size: [1.0, 0.6, 2.0], load_kw: 8.0}
cracs:
  - {id: CRAC01, airflow_m3h: 25000, supply_temp_c: 18.0}
mesh: {cell_size: 0.10}
```

What to ask for, and what not to:

- **CRAC airflow is mandatory and must not be guessed.** It sets the room's
  temperature rise; a plausible-looking invented number produces a
  plausible-looking wrong answer. If the user does not know it, say so and ask
  for the nameplate, or offer to work backwards from a target delta-T and tell
  them that is what you did.
- **Rack airflow is optional** and defaults to the load at an 11 K rise. Note
  that it only sizes the rack's flow resistance -- the air a rack actually gets
  is a result, not an input (ADR-011).
- **Rack size defaults to 1.0 x 0.6 x 2.0 m** (a standard 600 mm rack). Use it
  unless the user gives dimensions.
- **cell_size 0.10 m** is a good default. Coarsen to 0.15 for a first look at a
  big hall; the validator refuses anything that will not finish.
- A row of racks can be one block if the user does not care about
  rack-by-rack numbers. Say that you did it.

`aicfd build` prints what it derived -- supply velocity, bulk delta-T, per-rack
face velocity and resistance. Read that before solving: if the bulk delta-T is
under 2 K or over 20 K, the spec is wrong and a 10-minute solve will not fix it.

## Rules

1. **Read `report.md` before saying anything about a result.** It carries the
   verdict, the per-rack ASHRAE assessment and the warnings. Never describe a
   result from the raw solver log or from the field files.
2. **Report the verdict honestly, including failures.** `aicfd post` exits 2
   when validation fails. A failed check means the answer is not trustworthy —
   say so plainly and name the check, rather than reporting the temperatures as
   if they were fine.
3. **Warnings are findings, not noise.** The over-ventilation and
   iteration-limit warnings exist because they catch cases that look converged
   and mean nothing. Surface them.
4. **Never hand-edit a file under `runs/`.** Those are build artefacts and the
   next `aicfd build` overwrites them. Change `cases/<name>.yaml` and re-run.
   See ADR-004. If the spec cannot express what is needed, say so rather than
   editing the generated dictionary -- that is a gap in the tool, and silently
   working around it makes the spec and the result disagree.
5. **Do not narrate OpenFOAM internals to the user.** They asked about air
   temperature, not about `injectionRateSuSp`. Keep `d`/`f` coefficients,
   `cellZone` names and residual mechanics out of the answer unless they ask.
6. **State what the model cannot answer.** Rack inlet temperature: yes.
   Component temperature inside a server: no (ADR-003).
7. **If `rack_throughflow` fails, do not quote the rack temperatures.** A rack
   drawing a fraction of the air its load needs will read far hotter than
   reality, because AICFD models a rack as a resistance rather than a fan
   (ADR-011). Report it as "the layout lets air bypass these racks" and say the
   temperature is not a prediction. This is a known gap, not a bad room.

## Explaining a result

Lead with the number the engineer needs, then the judgement, then the caveat:

> Warmest rack inlet is 24.3 °C (R07), inside the ASHRAE recommended 18–27 °C
> band. All 12 racks pass. One caveat: R07 and R08 sit at the end of the cold
> aisle and run 4 K warmer than the rest — if you add load, add it at the CRAC
> end of the row.

Useful quantities and where they come from in `report.md`:

| The user asks | Look at |
|---|---|
| "is it cooling properly?" | the verdict line + the per-rack ASHRAE column |
| "how hot does it get?" | `Air temperature range`, and each rack's `Peak` |
| "is there enough air?" | `Supply air` vs. the over-ventilation warning |
| "did it converge?" | `Solver` line + the `residuals` check + warnings |
| "where is the hot spot?" | open the viewer; slice at rack mid-height |
| "is each rack getting air?" | the `Air drawn` column + the `rack_throughflow` check |

## Reading the viewer for the user

`aicfd view` serves it, but in a headless session you can still answer from
`results/<case>/viewer.json` — it holds the full KPI block, the checks, the
warnings and the residual history as JSON. Use it rather than re-deriving
numbers from `fields.bin`.

If the user wants a picture, run the viewer and screenshot it; do not describe a
field you have not rendered.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Could not find mandatory etc entry 'controlDict'` | An OpenFOAM command was run from an inherited shell | Always go through `aicfd run`; it isolates the environment (ADR-002) |
| `zone_<name>_populated` FAILS | The rack box misses the mesh, or `topoSet` did not run | Check the box coordinates lie inside the room; `aicfd run` runs `topoSet` for you |
| Whole room at one temperature | Almost always an empty cell zone — see above | |
| Bulk delta-T near zero | The CRACs supply far more air than the load needs | Check `airflow_m3h` against the nameplate; `aicfd build` warns about this |
| `plausible_velocity` FAILS | The field moves faster than fans or buoyancy can drive it | A setup error, not a layout one. Treat every number in the report as unreliable |
| `rack_throughflow` FAILS | Air is bypassing the racks; see rule 7 | Not fixable in the spec today — it is the rack model's limit (ADR-011) |
| Solver diverges early | Porosity coefficients or inlet velocity far from physical | Lower the relaxation factors for U and h to 0.2–0.3 and re-run |
| `residuals` FAILS | The run hit `max_iterations` without meeting its tolerance | Raise `solver.max_iterations` in the spec; generated cases always set `residualControl`, so this means it genuinely had not settled |

For the physics behind any of these — porous media coefficients, boundary
condition choices, meshing strategy — read the `datacenter-cfd` skill.

## Project conventions

- All work is documented on `main`.
- Every experiment, including the ones that fail, gets a dated note under
  `docs/experiments/`. Record what was asked, what was measured, and what it
  means — the reference-case note is the template.
- Architectural choices that a future reader would otherwise re-derive go in
  `docs/DECISIONS.md` as an ADR.
- `runs/` and `.venv/` are gitignored. `results/` is committed, so the viewer
  works immediately after a clone.
