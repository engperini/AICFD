POD 'pod-fanwall' at iteration 800

  Fan wall        5,000 m3/h (1.672 kg/s) at 20.0 degC
  Return air      30.6 degC (dT 10.6 K)
  Heat carried    17.87 kW of 18.00 kW installed (99%)
  Peak air        31.1 degC, 2.09 m/s
  Still moving    0.04 K since the previous sample
  Return path     30.7 -> 30.7 -> 30.6 degC, 0.04 K apart

  Fan wall rise   29.0 Pa of the 100 Pa on the datasheet (29%)
  Rack row        26.6 Pa across the row, in the field
  Return grilles  open holes (no free area given)

  Place                  Temp    dP vs intake    Speed
  Corredor frio           20.0            29.0     0.20
  Corredor quente         30.7             2.0     0.43
  Plenum do forro         30.7            -1.0     0.39
  Costas do fan wall      30.6             0.0     0.23

  Rack              Load    Inlet   Outlet    Rise   ASHRAE
  R1                6.0 kW     20.5     30.7    10.2 K   ok
  R2                6.0 kW     20.8     30.6     9.8 K   ok
  R3                6.0 kW     20.6     30.8    10.2 K   ok

  Validation
    PASS  mass_balance         fan supplies 1.67239 kg/s and draws 1.67239 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through fanIntake, 0.0% of the 1.672 kg/s it moves
    PASS  energy_closure       the return air carries 17.87 kW of the 18.00 kW installed (99%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 30.7, plenum 30.7, fan_back 30.6 degC (0.04 K apart)
    PASS  rack_resistance      the field drops 26.6 Pa across the row where the rack curve at 5,000 m3/h asks for 25.8 Pa (103%)
    PASS  fan_capacity         the POD costs 29.0 Pa and the fan wall's datasheet offers 100 Pa at 5,000 m3/h (29%)
    PASS  settled              the places moved at most 0.04 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 20.8 degC -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 2.09 m/s against a plausible 12.05 m/s (peak air 31.1 degC)
