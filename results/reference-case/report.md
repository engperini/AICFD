# reference-case -- results at t=800

**Verdict: PASS** (6/6 checks passed)

## Operating point

| Quantity | Value |
|---|---|
| IT load | 5.0 kW |
| Supply air | 77,760 m3/h (45,768 CFM) at 17.85 degC |
| Bulk temperature rise | 0.19 K |
| Air temperature range | 17.85 to 19.5 degC |
| Peak air speed | 2.046 m/s |
| Mesh | 72,000 cells |
| Solver | 800 iterations in 91.19 s |

## Racks

| Rack | Load | Inlet | Mean | Peak | Rise | ASHRAE |
|---|---|---|---|---|---|---|
| rack | 5.0 kW | 17.86 degC | 18.73 degC | 19.5 degC | 0.86 K | below recommended (18.0-27.0 degC), still allowable for class A1 |

## Validation checks

- **PASS** `mass_balance` -- inlet 21.598 m3/s vs outlet 21.614 m3/s (0.07% difference)
- **PASS** `monotonic_heating` -- air warms from 17.85 to 18.11 degC along x (total non-physical cooling: 0.006 K)
- **PASS** `residuals` -- worst final initial-residual 1.88e-04 (k)
- **PASS** `zone_rack_populated` -- cell zone 'rack': 1200 cells
- **PASS** `ashrae_rack` -- rack inlet 17.86 degC -- below recommended (18.0-27.0 degC), still allowable for class A1
- **PASS** `plausible_velocity` -- peak air speed 2.05 m/s against a plausible ceiling of 9.00 m/s (supply 1.80 m/s, buoyancy over a 1.6 K spread 0.58 m/s)

## Warnings

- The solver stopped at the iteration limit (800 iterations) without meeting its residualControl tolerance; residuals were still 1.9e-04. Either raise endTime in system/controlDict so it can finish, or accept this tolerance deliberately -- right now the run stops on a counter rather than on physics.
- Rack 'rack' is fed at 17.86 degC, below the ASHRAE recommended minimum of 18.0 degC. No equipment risk, but raising the supply setpoint would cut chiller energy at no thermal cost.
- Supply airflow is 52x what this load needs for a 10 K rise (77,760 m3/h supplied vs 1,505 m3/h required). The room-level temperature rise is therefore near zero and the result says little about real cooling performance.
