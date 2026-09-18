# What a full study asks for, and what AICFD takes today

**Source.** *CFD Analysis of VIN03 Data Hall*, Maders Consulting for Ascenty,
technical report rev. 04, September 2026 — a steady-state study of a real 5 MW
hall of exactly the arrangement AICFD gained in ADR-027: 14 fan wall units in
two mechanical galleries, 438 racks in 10 rows cut into two blocks, hot-aisle
containment, ceiling-plenum return. Solved in HELYX 4.5.1 on 21,6 M cells.

This file is the gap analysis. It exists so the next input added to AICFD is
the one that buys the most, and so a reader can see exactly where the tool
stands against a study a consultancy was paid for. It is not a list of
features; each line says what the parameter *decides*, because a parameter
that decides nothing is not worth an input field.

---

## 1. What AICFD already takes, and the report confirms is the right set

| Input | The report's value | Why it earns its place |
|---|---|---|
| `site.altitude_m` | 750 m | The unit was selected there. Air at 750 m is 8 % lighter than at sea level; the selection's airflow and its mass flow only agree if the solve runs at the site's pressure (ADR-023). |
| `fanwall.airflow_m3h` (per unit) | 104 181 m³/h | The boundary condition itself. |
| `fanwall.capacity_kw` (net sensible) | 432,6 kW | The report is explicit that this is **net**: 454,0 kW gross less the 21,4 kW the fan array returns to the air. Every comparison is against the net figure. |
| `fanwall.power_kw` | 21,4 kW | What makes the capacity net. |
| `fanwall.supply_temp_c` | 21,9 °C | Fixed on the supply face, exactly as AICFD does. |
| `fanwall.static_pressure_pa` | 100 Pa ESP | The budget the room is allowed to spend. |
| `fanwall.count` | 14 | With ADR-027, split between the two galleries. |
| `racks.size`, `load_kw`, `per_row`, `blocks` | 0,6 × 1,2 × 2,2 m, 12 kW, 10 rows, 2 blocks | |
| `aisles.hot`, `cold`, `perimeter`, `transverse` | contained hot aisles, 4 internal cold aisles, half-width aisle at each end wall | |
| `hall.height`, `hall.ceiling` | slab 7,60 m, suspended ceiling 5,45 m | |
| rack porosity | Darcy–Forchheimer, *d* = 3,2053 × 10⁶ m⁻², *f* = 18,724 m⁻¹ (Almoli 2013) | The report names this **"the largest single source of uncertainty in the absolute temperatures reported"**. AICFD derives the same coefficients from a nominal 25 Pa drop at the rack's rated airflow (ADR-011), which is the same physics with a more auditable input. |
| ASHRAE A1 recommended 18–27 °C | the acceptance criterion | AICFD's `ashrae_inlet` check. |
| energy closure | 100,1–100,2 % | AICFD's `energy_closure` check. |

Two modelling choices are identical and worth recording as independent
confirmation: **the racks have no fans** — the flow through a rack is a result
of the room's pressure field, not an input (ADR-011) — and **the envelope is
adiabatic**, which is conservative for sizing the plant.

---

## 2. What to add, in the order that buys the most

### 2.1 Redundancy and a failure scenario — *the whole point of the report*

```yaml
fanwall:
  count: 14
  redundancy: 2            # N+2: 12 units carry the load
  out_of_service: [4, 11]  # the failure case to solve
```

The report's finding is not the temperature. It is that the racks barely notice
losing two units (mean intake moves **0,02 K**) while the plant goes from 95,6 %
to **101,5 %** of the capacity available to it. A study that only reports the
normal condition would have concluded the installation has ample margin.

One rule has to come with it, and it is not obvious: **the total airflow to the
room is held constant between the two cases.** 12 units at their catalogue
121 545 m³/h set the total; the 14 installed then share that same total at
104 181 m³/h each. So the normal condition runs every unit *below* its catalogue
airflow, and losing two raises the airflow per unit by 16,7 % — which is what
raises the heat removed per unit, not any change in the room. A unit out of
service becomes an adiabatic no-slip wall on both faces.

### 2.2 Coil capacity at the operating point — turns a pass into a margin

```yaml
fanwall:
  rating_return_c: 34.0    # entering air at the selection point
  entering_water_c: 18.0
```

A chilled-water coil's sensible capacity is not a constant: it rises with the
difference between entering air and entering water. The hall never reaches the
selection point — the return is 32,4 °C, not 34,0 — so **no unit can deliver its
catalogue rating**, and the 14 units that look like 6 056 kW against a 5 100 kW
load are really 5 346 kW. That is the difference between "19 % margin" and
"4 % margin", and in the failure case between "margin" and "over capacity".

This is the single most valuable addition. It converts AICFD's `fan_capacity`
check (today a pressure check) into the report's headline number, *plant
utilisation of available capacity*, and it is arithmetic on quantities the solve
already produces.

### 2.3 A per-rack load map

```yaml
racks:
  load_map: loads/vin03.csv   # row, position, kW  (0 = installed, no load)
```

Already on the roadmap; the report shows why it is not cosmetic. 412 of 438
positions are loaded, the 17 empty ones are **dispersed**, and the report
attributes the plant's thermal uniformity (1,35 K of spread across 14 units) to
that dispersal — "not from any property of the units". A uniform load cannot
reproduce that, and it cannot show the hot spot a clustered load makes. An
unloaded position is porous media with no source, not a hole.

### 2.4 Ancillary heat that is not in a rack

```yaml
loads:
  pdu_fraction: 0.02       # PDU loss, as a share of IT load
```

2,0 % of the IT load — 100 kW on 5 000 — released in the electrical room as a
uniform volumetric source. Small, trivial to implement as a second
`scalarSemiImplicitSource`, and it is 2 % straight off the plant's margin.

### 2.5 Two scenarios in one spec, one mesh, one report

```yaml
scenarios:
  - {name: normal, note: "all units in service"}
  - {name: failure, fanwall: {out_of_service: [4, 11]}}
```

The report *is* a comparison: every plane, every table and every chart shows the
two cases side by side on one colour scale, and both share the same mesh and the
same numerics, so "the differences are attributable to the boundary conditions
that were varied and not to discretisation". That sentence is only available if
the two cases are solved from one mesh. This changes `aicfd run` and the report
writer more than it changes the model.

### 2.6 Numbers as window means, not last-iteration values

```yaml
solver:
  average_over: 500        # iterations
```

Every number in the report is a mean over the final 500 iterations, and it
reports the **peak-to-peak range (0,94 K) and the drift (0,32 K)** of the
monitors within that window alongside it. AICFD samples every
`solver.sensor_interval` and has a `settled` check, which is the same idea
stopping one step short. Averaging and quoting the window is how a steady RANS
result that still wobbles by a kelvin is honestly reported.

### 2.7 The ceiling return as a slot, not the whole aisle

```yaml
grilles:
  width: 1.60              # opening over each hot aisle
```

The report's ceiling opening is 1,60 m wide over each hot aisle and carries **no
condition at all** — a plain internal face. AICFD covers the whole aisle and
applies a datasheet loss coefficient (ADR-020). Ours is the more conservative
model and the one a real grilled ceiling needs; the missing input is the
*width*, so the opening can be a slot narrower than the aisle.

### 2.8 Units on a pressure boundary, and why that has to be declarable

Fan walls 01 and 14 in the report are set by **pressure** rather than flow rate,
and they are the two units that exceed their rating — drawing 49,5 kg·s⁻¹
against 38,9 elsewhere. The report flags this plainly: "this result follows
directly from a modelling choice and is flagged for verification against
commissioning data."

AICFD sets every unit by mass flow (ADR-017), which cannot produce that
artefact — but it also cannot show what an end-of-row unit really draws. If a
pressure-driven unit is ever offered, it must be declared per unit in the spec
and named in the report, never chosen silently.

### 2.9 Reportable planes, named in the spec

```yaml
report:
  planes: {z: [0.5, 1.0, 2.0, 3.0, 4.0, 5.0]}
  ashrae_class: A1
```

The report walks the hall at z = 0,50 / 1,00 / 2,00 / 3,00 / 4,00 / 5,00 m —
below the intakes, at rack mid-height, at the chimney mouth, inside the chimney,
approaching the ceiling openings, and under the ceiling — plus sections along
and across. Each height answers a different question, and the set is worth
carrying in the spec so the deliverable is reproducible rather than composed by
hand.

---

## 3. What AICFD will not chase

- **21,6 M cells with surface and volumetric refinement.** The report meshes at
  0,10 m base with one refinement level at the rack, fan wall, containment and
  ceiling-opening faces. AICFD's hall runs at 275 k cells with one rack per cell
  in plan, on purpose: a conceptual study that takes eleven minutes is a
  different instrument from a verification study that takes a cluster. The right
  response is to keep saying so — a single rack's intake carries 1 to 2 K here —
  not to pretend otherwise.
- **3-D renderings.** Plan and sections, drawn to scale with the field
  underneath, answer every question the report's isometric views answer, and
  they can be read quantitatively (ADR-024, ADR-025).
- **Isosurfaces.** The report's "volume above 34 °C" is a good picture; the same
  information is in the per-rack table AICFD already exports.

---

## 4. Where this leaves the tool

AICFD today reproduces the *arrangement*, the *boundary conditions* and the
*acceptance criterion* of a paid study of a real hall, on a mesh two orders of
magnitude coarser, in minutes rather than on a cluster, from a 90-line spec a
facility engineer can read.

What it cannot yet do is the thing the study was commissioned for: **state how
much reserve the plant has when a unit fails.** That needs §2.1 and §2.2 — a
failure scenario and coil capacity at the operating point — and nothing else on
this list comes close in value. They are the next work.
