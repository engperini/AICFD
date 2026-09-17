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
