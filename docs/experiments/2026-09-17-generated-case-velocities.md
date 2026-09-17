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
