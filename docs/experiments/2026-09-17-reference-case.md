# 2026-09-17 — Reproducing the reference case (M0)

> **Historical.** The hand-built OpenFOAM case this reproduces is the M0
> ground truth: it is why this project trusts `buoyantSimpleFoam` here at
> all. The case itself lives on under `.claude/skills/datacenter-cfd/`; the
> M1 tooling that post-processed it was removed in ADR-025.

**Question.** The reference case arrived from the `datacenter-cfd` skill with
validation numbers attached. Do they reproduce, and is the case physically
representative of a data hall?

**Answer.** The numbers reproduce exactly. The case is *not* physically
representative — it is a numerical smoke test, and should be labelled as one.

## Setup

| | |
|---|---|
| Machine | 4 vCPU, 15 GB RAM, Ubuntu 24.04 |
| OpenFOAM | v1912 (`1912.200626-2build3`, Ubuntu archive) |
| Case | 1 rack + 1 fan wall, room 6 × 4 × 3 m |
| Mesh | single hex block, 60 × 40 × 30 = 72,000 cells |
| Solver | `buoyantSimpleFoam`, k-epsilon, air as perfect gas |
| Command | `python -m aicfd run` |

## What reproduced

`checkMesh` reports `Mesh OK` with max non-orthogonality 0, max skewness
6.7e-14, aspect ratio 1 — expected for a uniform hex block, and a useful
baseline: any future mesh problem is the geometry's fault, not the mesher's.

| Quantity | Skill's README | Measured here |
|---|---|---|
| Runtime | ~90 s | 91.2 s (800 iterations) |
| Supply air | 17.85 °C | 17.85 °C |
| Rack mean | 18.7 °C | 18.73 °C |
| Rack peak | 19.5 °C | 19.50 °C |
| Hot aisle behind rack | ~19.3 °C | 19.3 °C |
| Monotonic warming | yes | yes (non-physical cooling 0.006 K) |

Mass balance closes to 0.07% (21.598 m³/s in, 21.614 m³/s out).

The ADR-002 environment isolation was necessary: the solvers are in `/usr/bin`
but the shell helpers `etc/bashrc` expects are absent, exactly as the skill
documented.

## What the numbers actually say

Two findings that the reproduction alone would not surface.

### 1. The case is over-ventilated by a factor of ~52

The fan wall is a fixed 1.8 m/s across the entire 4 × 3 m face:

```
12 m² × 1.8 m/s = 21.6 m³/s = 77,760 m³/h = 45,768 CFM
```

for a **5 kW** rack. The air a 5 kW load needs for a 10 K rise is:

```
5000 W / (10 K × 1.19 kg/m³ × 1005 J/kg·K) = 0.42 m³/s = 1,505 m³/h
```

So the bulk temperature rise across the room is 0.19 K — and the measured
room-average profile rises 0.26 K, confirming the arithmetic. The ~1.5 K seen
*inside* the rack is a local effect: only a sliver of that flood of air passes
through the porous zone.

This is not a bug in the case; it is a boundary condition chosen to make the
solver converge quickly, and it does that well. But it means **the reference
case says nothing about cooling performance**. A real CRAC at this scale moves
2,000–25,000 m³/h, not 77,760. Anyone reading "ΔT = 0.19 K, everything is
fine" as an engineering result would be badly misled.

Acted on: `aicfd post` now computes the required airflow for a 10 K rise and
warns when supply exceeds it by more than 3×. This case trips it.

### 2. The run stopped on a counter, not on convergence

`fvSolution` *does* set `residualControl` at 1e-4 on every field — but the run
never met it. `controlDict` caps the run at `endTime 800`, and the solver hit
that cap first. Final initial-residuals:

| Field | Final |
|---|---|
| Ux | 1.08e-4 |
| Uy | 1.39e-4 |
| Uz | 1.02e-4 |
| h | 2.77e-5 |
| p_rgh | 5.55e-5 |
| k | 1.88e-4 |
| epsilon | 7.45e-5 |

Ux, Uy and k are all still above the 1e-4 they were asked to reach. The curve
is flat by iteration 600, so the answer is not going to move much — but the run
ended on a counter, and OpenFOAM never printed "SIMPLE solution converged".

"Converged" and "hit the iteration limit" are different statements, and the
distinction is easy to lose because a log that ends at iteration 800 looks the
same either way. The tool now separates them:
`SolverLog.stopped_on_iteration_limit` is true only when the solver never
announced convergence, and it drives a warning naming the worst residual.

Generated cases (M2) get an `endTime` with enough headroom for the tolerance
they ask for, so this is a real signal rather than the normal state of affairs.

## Side effects on the code

The pipeline caught one bug in itself. `aicfd post` originally picked the first
`log.*` file alphabetically, which is `log.blockMesh` — so a perfectly converged
run was reported as having no convergence data. The validation layer failed the
run rather than passing it silently, which is the behaviour ADR-007 asks for.
Fixed by selecting the log by solver name; there is a regression test.

## Conclusion

M0 is complete: the reference case is reproducible here, `aicfd run` → `aicfd
post` → viewer works end to end, and the artefacts are committed under
`results/reference-case/`.

The reference case stays as-is — it is a fast, honest smoke test and a good
mesh baseline. It should *not* be the template for realistic cases. M2's case
generator will derive fan velocity from a real CRAC airflow rather than from a
velocity picked for convergence, which is the single change that makes the
physics representative.
