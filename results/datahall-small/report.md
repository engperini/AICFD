# datahall-small -- results at t=2000

**Verdict: FAIL** (6/15 checks passed)

## Operating point

| Quantity | Value |
|---|---|
| IT load | 48.0 kW |
| Supply air | 11,999 m3/h (7,062 CFM) at 20.0 degC |
| Bulk temperature rise | 12.04 K |
| Air temperature range | 23.3 to 37.25 degC |
| Peak air speed | 29.668 m/s |
| Mesh | 120,000 cells |
| Solver | 2000 iterations in 426.21 s |

## Racks

| Rack | Load | Inlet | Mean | Peak | Rise | ASHRAE |
|---|---|---|---|---|---|---|
| A1 | 6.0 kW | 32.29 degC | 34.04 degC | 36.41 degC | 1.74 K | above recommended (18.0-27.0 degC), still allowable for class A2 |
| A2 | 8.0 kW | 31.87 degC | 34.27 degC | 37.25 degC | 2.39 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A3 | 8.0 kW | 31.78 degC | 34.16 degC | 37.06 degC | 2.38 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A4 | 8.0 kW | 31.75 degC | 33.92 degC | 36.71 degC | 2.17 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A5 | 6.0 kW | 31.46 degC | 33.24 degC | 35.99 degC | 1.78 K | above recommended (18.0-27.0 degC), still allowable for class A1 |
| A6 | 12.0 kW | 30.82 degC | 32.68 degC | 36.85 degC | 1.87 K | above recommended (18.0-27.0 degC), still allowable for class A1 |

## Validation checks

- **FAIL** `mass_balance` -- inlet 3.142 m3/s vs outlet 3.342 m3/s (6.38% difference)
- **FAIL** `monotonic_heating` -- air warms from 29.92 to 31.49 degC along x (total non-physical cooling: 1.831 K)
- **FAIL** `residuals` -- worst final initial-residual 7.26e-03 (p_rgh)
- **PASS** `zone_A1_populated` -- cell zone 'A1': 1200 cells
- **PASS** `zone_A2_populated` -- cell zone 'A2': 1200 cells
- **PASS** `zone_A3_populated` -- cell zone 'A3': 1200 cells
- **PASS** `zone_A4_populated` -- cell zone 'A4': 1200 cells
- **PASS** `zone_A5_populated` -- cell zone 'A5': 1200 cells
- **PASS** `zone_A6_populated` -- cell zone 'A6': 1200 cells
- **FAIL** `ashrae_A1` -- A1 inlet 32.29 degC -- above recommended (18.0-27.0 degC), still allowable for class A2
- **FAIL** `ashrae_A2` -- A2 inlet 31.87 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A3` -- A3 inlet 31.78 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A4` -- A4 inlet 31.75 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A5` -- A5 inlet 31.46 degC -- above recommended (18.0-27.0 degC), still allowable for class A1
- **FAIL** `ashrae_A6` -- A6 inlet 30.82 degC -- above recommended (18.0-27.0 degC), still allowable for class A1

## Warnings

- The solver stopped at the iteration limit (2000 iterations) without meeting its residualControl tolerance; residuals were still 7.3e-03. Either raise endTime in system/controlDict so it can finish, or accept this tolerance deliberately -- right now the run stops on a counter rather than on physics.
