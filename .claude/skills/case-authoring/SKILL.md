---
name: case-authoring
description: Turn a REAL data hall project into an AICFD case file - read the drawings, the mechanical specification PDF, the fan wall selection sheet, the rack schedule or the RFP, and write cases/<name>.yaml with every number traced to the document it came from. Use whenever someone hands over project documents and wants a CFD case built from them, asks which AICFD key a measurement goes in, asks why a derived hall does not match a drawing, or asks what a failing check says about the inputs. Triggers on "monta o caso a partir desse projeto", "tenho as plantas do data hall", "transformar esse PDF em simulacao", "preencher o YAML", "qual chave usar para", "a planta diz 54 m e o modelo deu 51", "build a case from this layout", "rack schedule", "fan wall selection", "memorial descritivo", "RFP". For driving the toolchain once a case exists - run, view, report - use the aicfd skill instead. For the physics and the OpenFOAM modelling choices, use datacenter-cfd.
---

# Authoring an AICFD case from a real project

You have been given a project and have to produce **one file**:
`cases/<name>.yaml`. The schema is small. The translation is not, and the
translation is where a case goes quietly wrong: numbers that are all
plausible, all in range and all from the wrong document build a clean mesh,
solve, pass twelve checks and answer a question about a building that does not
exist. Nothing downstream catches that, because every stage after the spec is
faithful to the spec.

**`reference/CASE-AUTHORING.md` is the manual.** It is the whole procedure with
every key, every range and every trap. Read it before writing a case — this
file is the shape of the work, not a substitute for it.

Write the case and its comments in **English**, whatever language the source
documents and the conversation are in (ADR-026).

## The rule that outranks the rest

**Never invent a number.** Every value is measured, selected or assumed, and
the comment beside it says which:

```yaml
height: 7.5                 # 7.60 m on the section, snapped to the 0.25 m mesh
capacity_kw: 432.6          # NSCC at 750 m, 100 Pa ESP, 34.0 C entering air
airflow_cfm_per_kw: 158     # design reference; no rack datasheet in the pack
```

There is no fourth kind. A number with no comment cannot be told from a number
somebody made up. **When a document and the geometry disagree, the document
wins and the comment records the gap** — never adjust a clearance until the
arithmetic lands on the drawing's figure. Manual §1, §9.

## Work in this order

Each step needs the one before it. Doing them out of order is how a mesh gets
chosen before the geometry it has to resolve is known.

1. **Arrangement** — POD or hall; galleries at one or both ends; how the supply
   reaches the aisle (direct, supply plenum, mesh, raised floor); whether the
   hot aisle is contained; independent or networked units. Read off the plan
   and the section. Manual §4.
2. **The room** — slab height, false ceiling, gallery depth. From the sections.
3. **The racks** — cabinet size, per row, rows, load. From the layout and the
   rack schedule or the power table. Manual §6 for a row that is not uniform.
4. **The plant** — which unit, how many, and the *selection* behind its
   numbers. From the manufacturer's selection sheet, never a catalogue page.
5. **The perforated surfaces** — ceiling grilles, floor plates, supply grilles,
   the mesh over the plenum opening. Manual §8.
6. **The mesh** — so every plane above lands on a cell face. Manual §7.
7. **The solver** — iterations, cores.
8. **Build, read the warnings, reconcile.** Manual §10. Only then run.

## The mistake to check for first

**A data hall has no `hall.size`.** Its length and width are derived, and a
size typed into a case with `pods:` is silently ignored:

```
block_length = sum of the position widths in the typical row   (each snapped to cell x)
row_length   = blocks x block_length + (blocks - 1) x aisles.transverse
hall_length  = 2 x aisles.perimeter + row_length + sides x plenum.depth
total_x      = gallery.sides x gallery.depth + hall_length
total_y      = 2 x aisles.perimeter + pods x (2 x rack_depth + aisles.hot) + (pods - 1) x aisles.cold
```

Read backwards, that is the extraction: given a measured hall, solve for the
inputs. `pods` counts **pairs of rows facing a contained hot aisle** — a hall
with ten rack rows is `pods: 5`, and counting rows instead builds a hall twice
the size that passes every check. Manual §2.

## Ask, do not guess

Some things cannot be read off a drawing and change the answer. When the pack
does not settle them, ask rather than assume — and if you must assume, say so
in the comment and carry on:

- whether the hot aisle is **contained** (a roof and doors on the drawing);
- whether the units are **networked** (`fanwall.control: team`) — a controls
  question, not a unit question;
- the **selection conditions** behind every plant figure: elevation, external
  static pressure, entering water and entering air. A capacity without them is
  not this machine's;
- what the power table **includes** — PDU losses are commonly in it and are not
  rack load;
- which positions are **blanking panels** rather than empty cabinets. They are
  different surfaces: a blank passes no air, a 0 kW cabinet still breathes.

## Finish

In this repository, prove it:

```bash
python3 -m aicfd build cases/<name>.yaml --out /tmp/check   # geometry + warnings
python3 -m aicfd run cases/<name>.yaml                      # the solve
python3 -m aicfd report <name>                              # the Word report
```

Outside it, hand over the YAML and say which commands run it.

The case is finished when every warning is gone or explained in a comment, the
derived geometry is reconciled against the drawing, the total load matches the
power table, and every number carries one of the three comments. Manual §12.

When a check fails, read it as a statement about the **inputs** — a red
`rack_resistance` is usually a surface that was never built, not a solver
problem. Manual §11.

## Keeping this skill true

`reference/CASE-AUTHORING.md` is a copy of `docs/CASE-AUTHORING.md`, carried
here so the skill works when it is uploaded on its own. A test holds the two
byte for byte; after editing the manual, refresh it with:

```bash
cp docs/CASE-AUTHORING.md .claude/skills/case-authoring/reference/
```

To package this skill for upload, zip the folder itself — the archive has to
contain `case-authoring/SKILL.md`, not a bare `SKILL.md`:

```bash
cd .claude/skills && zip -r ../../case-authoring-skill.zip case-authoring
```
