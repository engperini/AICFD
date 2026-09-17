# 2026-09-17 — Why generated cases blew up (M2)

**Question.** The first multi-rack case built by the generator reported a peak
air speed of 29.7 m/s in a room supplied at 0.22 m/s. Where did the momentum
come from?

**Answer.** `prghPressure` on the return patch. It forces the *static* pressure
equal at floor and ceiling, which in a 3 m room imposes a ~35 Pa jump that
drives a 7 m/s recirculation out of nothing. The fix is one line; the useful
part is the method, and the check that now catches this class of error on its
own.

## The symptom

`aicfd run cases/datahall-small.yaml` (6 racks, 48 kW, 12,000 m³/h) failed
validation: mass balance 6.4% out, residuals stuck at 7e-3 after 2000
iterations, and a peak speed of 29.7 m/s. The buoyant ceiling for this room is

```
sqrt(2 · g · ΔT/T₀ · H) = sqrt(2 · 9.81 · 14/293 · 3) ≈ 1.7 m/s
```

so the field was roughly seventeen times above anything the physics could
drive. The median over the whole domain was 4.7 m/s — this was not a local
spike, the entire room was spinning.

## Ruling things out

**Not a divergence.** Residuals fell to ~1e-3 by iteration 700 and then
oscillated. A blow-up looks different: residuals climb.

**Not the turbulence model — but a real bug found on the way.** The k-epsilon
initialisation floored epsilon at 1e-4 independently of k. Those two quantities
jointly encode a length scale, `Cmu^0.75 · k^1.5 / ε`; clamping one of them
replaced the requested 0.30 m with 0.0041 m and collapsed the turbulent
viscosity to 3.1e-5 m²/s — twice molecular. The room behaved as if laminar at
its own scale.

Fixed by deriving epsilon from k and the length scale, and by taking the
velocity scale as the larger of the supply and the buoyant velocity (0.22 vs
1.1 m/s here — a data hall's supply face velocity is not what stirs it). Peak
speed fell 29.7 → 21.3 m/s. A genuine bug, not the cause.

**Not the layout, and not low airflow.** Same room and rack as the reference
case, sweeping only the CRAC rating:

| Supply | Face velocity | Peak speed |
|---|---|---|
| 77,760 m³/h | 1.80 m/s | 8.46 m/s |
| 20,000 m³/h | 0.46 m/s | 8.25 m/s |
| 6,000 m³/h | 0.14 m/s | 16.43 m/s |
| 1,800 m³/h | 0.04 m/s | 17.88 m/s |

The hand-written reference case, at the airflow in the top row, peaks at
**2.05 m/s**. Same geometry, same load, same solver — so the fault was in what
the generator writes, at every airflow.

## Bisecting

Diffing the generated case against the reference left exactly two differences.
Running all four combinations on the reference's own 72k-cell mesh:

| Variant | Peak speed |
|---|---|
| as generated | 14.31 m/s |
| `p_rgh` return → `fixedValue` | **2.71 m/s** |
| `alphat` walls → Jayatilleke | 14.31 m/s |
| both | 2.71 m/s |

Unambiguous, and the alphat rows are bit-identical to their partners: with
adiabatic walls that wall function changes nothing.

## Why prghPressure is wrong here

`buoyantSimpleFoam` solves for the modified pressure

```
p_rgh = p − ρ (g · x) = p + ρ g z
```

In a still room the *static* pressure p carries a hydrostatic gradient, so
`p_rgh` is the uniform one. That is what `fixedValue` on the outlet expresses.

`prghPressure` takes a static pressure and converts it, so `p uniform 101325`
asserts the static pressure is identical at floor and ceiling. Over 3 m that
contradicts the hydrostatic column by

```
ρ g H = 1.2 · 9.81 · 3 ≈ 35 Pa
```

and 35 Pa across the outlet drives `sqrt(2 · 35 / 1.2) ≈ 7.6 m/s` — the order
actually observed. The boundary condition was manufacturing the circulation.

`prghPressure` is the right choice when you genuinely know the static pressure
at a boundary, such as an opening to outdoor air at a known height. A room's
return grille is not that.

## What was changed

1. Return patch: `fixedValue` on `p_rgh`, with the reasoning in a comment
   beside it so nobody "improves" it back.
2. `alphat` walls: Jayatilleke. It changes nothing while the walls are
   adiabatic, but it is the correct form once one is not, and matching the
   reference removes a difference that cost time here.
3. k-epsilon initialisation, as above.
4. **A new validation check, `plausible_velocity`.** Every other check passed
   on this run: the mass balance was only 6% out, temperatures looked
   unremarkable, the rack zones were populated. Nothing flagged that the answer
   was physically impossible. The check now compares the peak speed against
   five times the larger of the supply and buoyant velocities, and fails the
   run with a warning saying the temperatures are unreliable too, since they
   come from the same field.

That last point is the real lesson. The validation layer was written to catch
setup mistakes (ADR-007) and it caught none of this, because every individual
quantity was plausible in isolation. A check on whether the *flow itself* is
attainable is a different kind of question, and it would have failed this run
on the first solve instead of after an afternoon of bisection.

## After the fix: what the multi-rack case actually says

Re-running `cases/datahall-small.yaml` (6 racks, 48 kW, 12,000 m³/h):

| | Before | After |
|---|---|---|
| Peak air speed | 29.67 m/s | **1.49 m/s** |
| Rack inlets | 30.8–32.3 °C | 23.1–24.7 °C |
| `plausible_velocity` | — | PASS (1.49 vs a 14.6 m/s ceiling) |

The velocity field is now physical and every rack inlet sits inside the ASHRAE
recommended band. Two things remain, and they are different in kind.

### The racks are starving, and that is the rack model's limit

Integrating Ux over each rack's mid-depth plane:

| Rack | Load | Air drawn | Needed at 11 K | Ratio |
|---|---|---|---|---|
| A1 | 6 kW | 337 m³/h | 1,642 | 21% |
| A2 | 8 kW | 488 m³/h | 2,189 | 22% |
| A3 | 8 kW | 482 m³/h | 2,189 | 22% |
| A4 | 8 kW | 472 m³/h | 2,189 | 22% |
| A5 | 6 kW | 344 m³/h | 1,642 | 21% |
| A6 | 12 kW | 675 m³/h | 3,284 | 21% |

Only 23% of the supply passes through the racks; 3,368 m³/h goes over the top
instead. With one fifth of the air it needs, each rack cooks — peak temperatures
of 46–62 °C inside the zones.

**That is not a prediction.** It is ADR-011 showing its teeth: a rack modelled
as a pure flow resistance has no way to pull its rated airflow, so whenever
bypass is easier than passing through, the air bypasses. A real rack's fans
would move their rated CFM regardless of what the room does, and the
temperatures would be far lower.

Two things follow. `rack_throughflow` is now a validation check, so this
condition fails the run and says in plain terms that the temperatures are a
statement about bypass, not about the racks. And rack fans as momentum sources
move from "M5, sometime" to the next thing worth building — without them AICFD
systematically over-predicts rack temperatures in exactly the layouts an
engineer would ask about.

### p_rgh does not converge

Every field settles below 1e-3 except pressure, which plateaus around 8e-3 and
oscillates. The physical reason is visible in the geometry: a 2 m wall of racks
across a 3 m room forces all the bypass air through the 1 m gap at the ceiling,
and that shear layer sheds. There is no steady state for a steady solver to
find.

Options, none of them free: accept a looser tolerance and report the run as
converged-in-the-mean, switch to a transient solver and average (100x the
runtime), or treat it as a signal that the *layout* is the problem — which for
this example it partly is. Left open deliberately; guessing here would be worse
than the honest FAIL the report currently gives.

## A check that was wrong

`monotonic_heating` — "the cross-section average temperature rises along the
flow" — failed this run, reporting 5.3 K of "non-physical cooling". It was
neither non-physical nor a problem: in a room with recirculation, hot air
travelling back along the ceiling genuinely makes the x-average non-monotonic.
The check held only for the single-rack, single-pass reference case it was
written against.

Replaced with `no_air_below_supply`: nothing in the room may be colder than the
supply, which is the only cold source. That is a true invariant in every
topology, and it still catches the discretisation undershoots the original
check was reaching for. The temperature profile is kept as a KPI.

A check that fails correct results is worse than no check — it teaches the user
to ignore the verdict.
