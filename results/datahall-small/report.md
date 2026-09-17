# datahall-small -- results at t=2000

**Verdict: FAIL** (14/17 checks passed)

## Operating point

| Quantity | Value |
|---|---|
| IT load | 48.0 kW |
| Supply air | 11,999 m3/h (7,062 CFM) at 20.0 degC |
| Bulk temperature rise | 12.04 K |
| Air temperature range | 20.0 to 62.29 degC |
| Peak air speed | 1.489 m/s |
| Mesh | 120,000 cells |
| Solver | 2000 iterations in 434.93 s |

## Racks

| Rack | Load | Inlet | Mean | Peak | Rise | Air drawn | ASHRAE |
|---|---|---|---|---|---|---|---|
| A1 | 6.0 kW | 23.14 degC | 32.69 degC | 46.43 degC | 9.54 K | 337 of 1,642 m3/h (20%) | within ASHRAE recommended (18.0-27.0 degC) |
| A2 | 8.0 kW | 24.37 degC | 38.87 degC | 57.75 degC | 14.5 K | 488 of 2,189 m3/h (22%) | within ASHRAE recommended (18.0-27.0 degC) |
| A3 | 8.0 kW | 24.63 degC | 40.32 degC | 58.58 degC | 15.69 K | 482 of 2,189 m3/h (22%) | within ASHRAE recommended (18.0-27.0 degC) |
| A4 | 8.0 kW | 24.72 degC | 40.47 degC | 59.02 degC | 15.75 K | 472 of 2,189 m3/h (22%) | within ASHRAE recommended (18.0-27.0 degC) |
| A5 | 6.0 kW | 24.59 degC | 39.49 degC | 55.02 degC | 14.9 K | 344 of 1,642 m3/h (21%) | within ASHRAE recommended (18.0-27.0 degC) |
| A6 | 12.0 kW | 23.42 degC | 39.38 degC | 62.29 degC | 15.97 K | 675 of 3,284 m3/h (20%) | within ASHRAE recommended (18.0-27.0 degC) |

## Validation checks

- **FAIL** `mass_balance` -- inlet 3.328 m3/s vs outlet 3.455 m3/s (3.81% difference)
- **PASS** `no_air_below_supply` -- coldest air 20.00 degC against a 20.00 degC supply; cross-section average runs 20.00 to 34.04 degC along the flow
- **FAIL** `residuals` -- worst final initial-residual 8.08e-03 (p_rgh)
- **PASS** `zone_A1_populated` -- cell zone 'A1': 1200 cells
- **PASS** `zone_A2_populated` -- cell zone 'A2': 1200 cells
- **PASS** `zone_A3_populated` -- cell zone 'A3': 1200 cells
- **PASS** `zone_A4_populated` -- cell zone 'A4': 1200 cells
- **PASS** `zone_A5_populated` -- cell zone 'A5': 1200 cells
- **PASS** `zone_A6_populated` -- cell zone 'A6': 1200 cells
- **FAIL** `rack_throughflow` -- A1 draws 337 m3/h of the 1,642 m3/h its 6 kW needs (20%), A2 draws 488 m3/h of the 2,189 m3/h its 8 kW needs (22%), A3 draws 482 m3/h of the 2,189 m3/h its 8 kW needs (22%), A4 draws 472 m3/h of the 2,189 m3/h its 8 kW needs (22%), A5 draws 344 m3/h of the 1,642 m3/h its 6 kW needs (21%), A6 draws 675 m3/h of the 3,284 m3/h its 12 kW needs (20%)
- **PASS** `ashrae_A1` -- A1 inlet 23.14 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `ashrae_A2` -- A2 inlet 24.37 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `ashrae_A3` -- A3 inlet 24.63 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `ashrae_A4` -- A4 inlet 24.72 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `ashrae_A5` -- A5 inlet 24.59 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `ashrae_A6` -- A6 inlet 23.42 degC -- within ASHRAE recommended (18.0-27.0 degC)
- **PASS** `plausible_velocity` -- peak air speed 1.49 m/s against a plausible ceiling of 14.57 m/s (supply 0.22 m/s, buoyancy over a 42.3 K spread 2.91 m/s)

## Warnings

- The solver stopped at the iteration limit (2000 iterations) without meeting its residualControl tolerance; residuals were still 8.1e-03. Either raise endTime in system/controlDict so it can finish, or accept this tolerance deliberately -- right now the run stops on a counter rather than on physics.
- 6 rack(s) are drawing a fraction of the air their load needs, because AICFD models a rack as a flow resistance rather than a fan (see ADR-011): air that finds an easier path around the rack takes it. A real rack's own fans would pull their rated airflow regardless. The rack temperatures above are therefore far higher than reality -- read this as 'the layout lets air bypass the racks', not as a predicted temperature.
