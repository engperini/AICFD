# coarse-probe -- results at t=1000

**Verdict: FAIL** (6/15 checks passed)

## Operating point

| Quantity | Value |
|---|---|
| IT load | 48.0 kW |
| Supply air | 11,999 m3/h (7,062 CFM) at 20.0 degC |
| Bulk temperature rise | 12.04 K |
| Air temperature range | 20.32 to 34.56 degC |
| Peak air speed | 21.315 m/s |
| Mesh | 15,000 cells |
| Solver | 1200 iterations in 29.75 s |

## Racks

| Rack | Load | Inlet | Mean | Peak | Rise | ASHRAE |
|---|---|---|---|---|---|---|
| A1 | 6.0 kW | 29.92 degC | 32.13 degC | 34.38 degC | 2.21 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A2 | 8.0 kW | 29.39 degC | 32.07 degC | 34.43 degC | 2.68 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A3 | 8.0 kW | 28.16 degC | 31.21 degC | 33.14 degC | 3.05 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A4 | 8.0 kW | 28.4 degC | 31.07 degC | 32.86 degC | 2.66 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A5 | 6.0 kW | 29.39 degC | 31.56 degC | 33.85 degC | 2.17 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A6 | 12.0 kW | 29.92 degC | 32.35 degC | 34.56 degC | 2.43 K | above recommended (18.0-27.0 degC), still allowable for class A1 |

## Validation checks

- **FAIL** `mass_balance` -- inlet 3.552 m3/s vs outlet 3.411 m3/s (3.97% difference)
- **FAIL** `monotonic_heating` -- air warms from 26.80 to 31.80 degC along x (total non-physical cooling: 0.397 K)
- **FAIL** `residuals` -- worst final initial-residual 2.56e-03 (k)
- **PASS** `zone_A1_populated` -- cell zone 'A1': 150 cells
- **PASS** `zone_A2_populated` -- cell zone 'A2': 150 cells
- **PASS** `zone_A3_populated` -- cell zone 'A3': 150 cells
- **PASS** `zone_A4_populated` -- cell zone 'A4': 100 cells
- **PASS** `zone_A5_populated` -- cell zone 'A5': 150 cells
- **PASS** `zone_A6_populated` -- cell zone 'A6': 150 cells
- **FAIL** `ashrae_A1` -- A1 inlet 29.92 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A2` -- A2 inlet 29.39 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A3` -- A3 inlet 28.16 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A4` -- A4 inlet 28.4 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A5` -- A5 inlet 29.39 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A6` -- A6 inlet 29.92 degC -- above recommended (18.0-27.0 degC), still allowable for class A1

## Warnings

- The solver stopped at the iteration limit (1200 iterations) without meeting its residualControl tolerance; residuals were still 2.6e-03. Either raise endTime in system/controlDict so it can finish, or accept this tolerance deliberately -- right now the run stops on a counter rather than on physics.
