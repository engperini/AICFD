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
aicfd doctor      # only when something looks broken
aicfd run [case] [--name NAME]     -> runs/NAME   (solves, then posts)
aicfd post NAME                    -> results/NAME/{viewer.json,fields.bin,report.md}
aicfd view --case NAME             -> http://localhost:8000/web/?case=NAME
```

Run them as `python -m aicfd <command>` (or `python3` inside Docker).

A full solve of the 72k-cell reference case takes ~90 s. Run it in the
background and keep working; do not poll it in a tight loop.

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
4. **Never hand-edit a file under `runs/`.** Those are build artefacts. If a
   case needs to change, change the case under `cases/` (or the template) and
   re-run. See ADR-004.
5. **Do not narrate OpenFOAM internals to the user.** They asked about air
   temperature, not about `injectionRateSuSp`. Keep `d`/`f` coefficients,
   `cellZone` names and residual mechanics out of the answer unless they ask.
6. **State what the model cannot answer.** Rack inlet temperature: yes.
   Component temperature inside a server: no (ADR-003).

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
| Solver diverges early | Porosity coefficients or inlet velocity far from physical | Lower the relaxation factors for U and h to 0.2–0.3 and re-run |
| `residuals` FAILS | The run hit its iteration limit while still moving | Raise `endTime`, or set `residualControl` so it stops on physics |

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
