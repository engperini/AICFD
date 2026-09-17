# 2026-09-17 — Building and validating a fan-wall POD

**Question.** Can AICFD model a real POD — fan wall, cold aisle, racks turned
90° to it, contained hot aisle, ceiling grilles, return plenum, mechanical
gallery — and can an electrical engineer tell from the output whether to
believe it?

**Answer so far.** The geometry and the mesh are right to the face. The air
loop is watertight. Two boundary-condition faults were found and fixed, both
of which produced runs that converged and reported plausible numbers. The
convergence verdict had to be rebuilt twice. The physics result — a contained
hot aisle at the design temperature — is in hand; the fully settled field is
not, at the time of writing.

## The case

`cases/pod-fanwall.yaml`. Mechanical gallery 3 m deep at full 8 m height, data
hall 6 m long × 4,2 m wide, false ceiling at 6,5 m with a 1,5 m return plenum
above it. Cold aisle 1,8 m, rack row 1,2 m deep, hot aisle 1,2 m. Three racks
of 6 kW at 0,6 m pitch, fronts at 90° to the cold aisle flow. Fan wall 4 m tall
× 1,8 m wide discharging into the cold aisle only. Three 0,60 m ceiling grilles
directly over the racks on the hot-aisle side. Hot-aisle containment from the
rack tops to the false ceiling. 5 000 m³/h at 20 °C. 0,10 m cells, 302 400 of
them.

Design point: 18 kW into 5 000 m³/h is a 10,8 K rise, so the return should be
30,8 °C. Rack demand at an 11 K rise is 4 926 m³/h — 99% of supply.

## The mesh is right to the face

Every internal surface is a `createBaffles` patch made from a `topoSet` face
zone, with the openings left as holes in those zones. Each cell face is
0,01 m², so `checkMesh`'s face counts are a direct area audit:

| surface | faces | area | expected |
|---|---|---|---|
| divider | 2 010 | 20,10 m² | 33,60 − 7,20 (fan) − 6,30 (plenum) |
| forro | 2 412 | 24,12 m² | 25,20 − 3 × 0,36 (grilles) |
| containment roof wall | 774 | 7,74 m² | 1,80 × 4,30 |
| containment doors | 780 each | 7,80 m² | 1,20 × 6,50 |
| fan | 720 | 7,20 m² | 1,80 × 4,00 |
| racks | 1 584 cells | 1,584 m³ | 0,6 × 1,2 × 2,2 |

Non-orthogonality 0, max skewness 1,2e-13.

Two traps cost time and are now enforced in code. `boxToFace` selects by face
centre *whatever the face's orientation*, so a thin box round a plane also
catches everything running perpendicular through it — 2 218 faces where 540
were wanted. Every selection is narrowed with `normalToFace`. And
`createBaffles` without `-overwrite` writes the modified mesh into a new time
directory: `checkMesh` and the solver then both read the mesh that was supposed
to be replaced, the run succeeds, and there are no internal walls in it at all.

## The envelope is watertight

Reading `phi` on every patch after 300 iterations: the fan pair carries
−1,67183 and +1,67190 kg/s (4 parts in 100 000 apart) and **every baffle wall
carries exactly zero**. With the containment sealed, the only path from the
cold side to the grilles is through the racks, which is the whole point of the
geometry and is what the M2 case could not achieve — there, 77% of the supply
went over the top of the row and the racks drew 21% of what their load needed.

An early measurement said only 3 175 m³/h of 5 016 crossed the rack band. That
was wrong: it integrated cell-centre velocity instead of face flux, which is
off by tens of percent across a porous zone. Everything in `podpost` now reads
patch values.

## Fault 1 — the fan was letting air blow backwards through it

`pressureInletOutletVelocity` on the intake lets the pressure field decide
which way air crosses the patch. Measured: **3,9 kg/s reversing into the
gallery against a net of 1,7** — 2,3× the throughput, steady across 300
iterations, so not a transient. The intake is 7,2 m² at the end of a 100 m³
gallery moving 0,19 m/s, and local eddies reverse it freely.

No fan does that. Worse, on every reversed face the `inletOutlet` temperature
was pinned to the seeded return value, so the boundary was *inventing* heat the
racks never produced and the energy balance could never close.

Both sides now prescribe the same mass flow — `flowRateInletVelocity` and
`flowRateOutletVelocity`. Mass rather than volume, because the air leaving is
warmer and thinner than the air arriving (≈1% at a 10 K rise) and a closed loop
has nowhere to put the difference. No patch fixes `p_rgh` any more, so its level
is pinned with `pRefCell`/`pRefValue`. After the fix: supply and intake agree to
0,000%, backflow exactly 0,00000 kg/s.

## Fault 2 — the convergence verdict, twice

**The residuals said nothing.** They fell steadily for 300 iterations on a case
returning air at 20,5 °C against a design return of 30,8. Reading the residual
plot, the run looked like it was converging nicely.

What does say something is the energy balance: every watt installed has to leave
through the intake as warmer air, `cp · Σ phi_i (T_i − T_supply)`. It needs no
reference solution and an engineer can check it — *the racks make 18 kW, the air
is carrying 17,6, so I am looking at the answer.*

**Then the balance said too much.** Seeding the initial field warm downstream of
the racks (see below) makes the balance read 101,2% at iteration 100, because
the seed put the return temperature there and the intake is where the balance is
measured. Underneath, the contained hot aisle was swinging 27,5 → 25,1 → 26,5 °C
between samples.

So a run is judged on two things. A closed balance says the field is
*consistent*; the instrumented places holding still between samples says it is
*settled*. `drift()` reports the largest move any place made since the previous
sample; `settled` fails above 0,25 K. Neither is a verdict alone.

The earlier claim in this repository that energy closure is "the number that
cannot be satisfied by accident" was too strong. It cannot be satisfied by
accident by a *solver*. It can be satisfied trivially by an initial condition —
which is exactly what a good initial condition does.

## Watching a run instead of waiting it out

Four instrumented places, three points each, averaged, with the spread between
the three reported alongside: the middle of the cold aisle and of the contained
hot aisle at rack height, the plenum above the grilles, and the back of the fan
wall. The points come from the geometry and are drawn on the sections, so the
placement is checked before the run.

`probes` would have been the obvious mechanism and does not work in this
OpenFOAM build: any functionObject at all kills the solver at "Starting time
loop" with `error in IOstream "sha1"`, an `OSHA1stream` fault inside
`functionObjectList`'s dictionary digest — the same fault that makes
`postProcess` unusable. A one-point, one-field `probes` entry triggers it, so it
is the packaged library, not the dictionary. Instead the solver writes its
fields at the sensor interval and `podpost.Sampler` reads each time directory as
it lands, guarded by OpenFOAM's own end-of-file footer. Same cells, same
iterations.

## How long a cold start takes, and why

From a uniform 20 °C field the solver has to carry heat all the way round the
loop before the balance can begin to close. The mechanical gallery alone is
100 m³ of a 302 m³ domain and sits in the return path.

| iteration | cold aisle | hot aisle | plenum | fan back | closure |
|---|---|---|---|---|---|
| 100 | 20,0 | 20,5 | 20,0 | 20,0 | 0,0% |
| 300 | 20,1 | 25,8 | 20,0 | 20,0 | 0,0% |
| 600 | 20,4 | 29,9 | 21,1 | 20,0 | 0,1% |
| 900 | 20,6 | 30,9 | 23,4 | 20,0 | 0,1% |
| 1 200 | 20,6 | 31,3 | 25,1 | 20,0 | 0,2% |
| 1 400 | 20,5 | 31,4 | 26,3 | 20,1 | 1,7% |

**The contained hot aisle settles at 31,3 °C against a design return of
30,8** — the containment works, geometrically all the air goes through the
racks, and it leaves with the ΔT the load dictates. It took 1 300 iterations for
the heat to reach the gallery at all.

## Seeding the field, and proving the seed did not choose the answer

A steady solve's initial condition does not appear in its converged solution;
it only decides what getting there costs. So `0/T` is seeded with the loop's
topology: supply temperature upstream of the racks, supply plus the design rise
everywhere the air has already been through them. This is ordinary practice —
OpenFOAM ships `setFields` for it; commercial codes call it patching a region,
hybrid or FMG initialisation, or mapping from a previous solution.

The argument has one assumption: that the steady problem has a unique solution.
A buoyancy-driven room need not, and then the initial condition selects between
branches. So the same case is also run from a uniform field and
`podpost.compare()` checks the two land in the same place, within 1 K at every
instrumented place.

At equal iteration count (600), what the seed actually bought:

| place | cold start | warm start | expected |
|---|---|---|---|
| cold aisle | 20,4 | 20,4 | ~20 |
| hot aisle | 29,9 | 29,9 | ~31 |
| plenum | 21,1 | 27,8 | ~31 |
| fan back | 20,0 | 30,9 | ~31 |

The hot aisle is *identical*. The seed is discontinuous, so the hot aisle has to
re-establish either way — it dipped to 25,1 before climbing back. The whole
benefit is in the plenum and the gallery, which is precisely where the cold
start's cost was.

## Fault 3 — three walls missing, and a factor of five

The rack row delivered 5,3 Pa where its own curve, at the airflow the fan was
measurably moving, demanded 25,8. Two isolated duct cases reproduced the
analytic drop to 0,2%, so the porous coefficients were right. The obvious next
suspect was the blocked-axis coefficients, and testing that first required a
measurement worth trusting — which is what actually found the fault.

Every reading until then came from cell-centre velocity, which is meaningless
inside a porous zone and beside a baffle. `aicfd/foam/polymesh.py` now labels
each internal face by the axis it is normal to, from the offset between its
owner and neighbour cells on a structured box, which makes `phi` — the exact
conserved flux — readable through any plane. That capability was needed anyway:
this OpenFOAM build cannot run the functionObject that would otherwise report
it.

Through the rack zone's faces, the row as first built:

| face | flow | |
|---|---|---|
| front of the racks, y = 1,8 | +1 106 m³/h | **22%** |
| row end, x = 5,0 | +1 651 m³/h | |
| row end, x = 6,8 | +1 521 m³/h | |
| top, z = 2,2 | −781 m³/h | |
| outlet, y = 3,0 | +5 059 m³/h | the fan's whole duty |

The containment sealed the hot aisle, but the row's own ends and tops were
open. The cold aisle wrapped around and poured in sideways, which costs a few
cells of blocked axis against 1,2 m of the flow axis — the short circuit wins
every time. Reducing the blocked coefficients would have widened exactly the
path the air was taking.

A rack is a closed box that breathes front to back. Closing the ends took the
front from 22% to 57%; the air moved upstairs and came down through the tops
instead. Closing those too:

| | air through the rack fronts | resistance delivered |
|---|---|---|
| row open | 22% | 21% |
| ends closed | 57% | 53% |
| **box closed** | **100%** | **104%** |

Proportional at every step — the delivered resistance simply tracked the air
that was actually crossing the racks.

## The result

With the row closed, at iteration 400, **all nine checks pass**:

```
Fan wall        5 000 m³/h (1,672 kg/s) at 20,0 °C
Return air      30,8 °C (dT 10,8 K)
Heat carried    18,17 kW of 18,00 kW installed (101%)
Fan wall rise   29,0 Pa of the 100 Pa on the datasheet (29%)
Rack row        26,6 Pa across the row, in the field

Place                  Temp    dP vs intake    Speed
Corredor frio          20,0            29,0     0,20
Corredor quente        30,6             2,0     0,43
Plenum do forro        30,5            -1,0     0,39
Costas do fan wall     30,8             0,0     0,24

Rack              Load    Inlet   Outlet    Rise   ASHRAE
R1                6,0 kW   20,6     30,8    10,2 K   ok
R2                6,0 kW   21,0     30,6     9,7 K   ok
R3                6,0 kW   20,7     30,8    10,1 K   ok
```

The design point is 18 kW into 5 000 m³/h — a 10,8 K rise and a 30,8 °C return.
The solved field gives exactly that. Rack inlets sit 0,6 to 1,0 K above the
supply, which is the containment doing its job: with the row closed there is no
path from the hot aisle back to a rack face.

## Open

- The cold/warm agreement test (ADR-019) has not been run on the corrected
  geometry. Until it has, the seeded field is unproven for this case.
- The ceiling grilles are modelled as fully open holes. A real return grille
  has a free area around 50% and a loss coefficient, so some of the 71 Pa of
  headroom against the datasheet is resistance that is simply not in the model
  yet.
- `settled` is a speed test and `return_path` a distance test; both pass here,
  but neither would catch a field drifting as a whole. No check yet does.
