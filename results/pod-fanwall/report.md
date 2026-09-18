POD 'pod-fanwall' at iteration 2000

  Fan wall        5,000 m3/h (1.672 kg/s) at 20.0 degC
  Return air      30.7 degC (dT 10.7 K)
  Heat carried    17.99 kW of 18.00 kW installed (100%)
  Peak air        31.0 degC, 1.86 m/s
  Still moving    0.00 K since the previous sample
  Return path     30.7 -> 30.7 -> 30.7 degC, 0.02 K apart

  Fan wall rise   30.0 Pa of the 100 Pa on the datasheet (30%)
  Rack row        25.5 Pa across the row, in the field
  Return grilles  2.41 Pa in the field, 2.36 Pa from K

  Place                  Temp    dP vs intake    Speed
  Corredor frio           20.0            30.0     0.26
  Corredor quente         30.7             5.0     0.42
  Plenum do forro         30.7            -0.7     0.64
  Costas do fan wall      30.7            -0.0     0.26

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  R1                6.0 kW     20.4     20.2     30.7    10.3 K   ok
  R2                6.0 kW     20.6     20.2     30.6    10.0 K   ok
  R3                6.0 kW     20.5     20.2     30.8    10.3 K   ok

  Validation
    PASS  mass_balance         fan supplies 1.67239 kg/s and draws 1.67239 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intake, 0.0% of the 1.672 kg/s they move
    PASS  energy_closure       the return air carries 17.99 kW of the 18.00 kW installed (100%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 30.7, plenum 30.7, fan_back 30.7 degC (0.02 K apart)
    PASS  rack_resistance      the field drops 25.5 Pa across the row where the rack curve at 5,000 m3/h asks for 25.8 Pa (99%)
    PASS  grille_resistance    the field drops 2.41 Pa across the return grilles where their K at 5,000 m3/h asks for 2.36 Pa (102%)
    PASS  fan_capacity         the POD costs 30.0 Pa and the fan wall's datasheet offers 100 Pa at 5,000 m3/h per unit (30%); uncontrolled at full speed it would run at 6,307 m3/h and 47.7 Pa
    PASS  settled              the places moved at most 0.00 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 20.2 degC at the top of R2 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 1.86 m/s against a plausible 12.05 m/s (peak air 31.0 degC)
