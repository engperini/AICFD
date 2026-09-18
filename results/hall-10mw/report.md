POD 'hall-10mw' at iteration 600

  Fan wall        2,720,002 m3/h (909.779 kg/s) at 20.0 degC
  Return air      30.9 degC (dT 10.9 K)
  Heat carried    9978.14 kW of 9984.00 kW installed (100%)
  Peak air        62.5 degC, 9.47 m/s
  Still moving    0.04 K since the previous sample
  Return path     31.0 -> 30.6 -> 30.9 degC, 0.30 K apart

  Fan wall rise   81.0 Pa of the 100 Pa on the datasheet (81%) on the most loaded of 17 units (77.0 on the least)
  Rack row        27.1 Pa across the row, in the field
  Return grilles  11.83 Pa in the field, 10.66 Pa from K

  Place                  Temp    dP vs intake    Speed
  Corredor frio           20.1            78.4     2.44
  Corredor quente         31.0            50.1     1.78
  Plenum do forro         30.6            33.8     2.58
  Costas do fan wall      30.9             1.4     3.63

  Fan wall     Supply kg/s   Rise Pa
  fan1               53.516      79.6
  fan2               53.516      79.4
  fan3               53.516      79.5
  fan4               53.516      80.3
  fan5               53.516      80.8
  fan6               53.516      78.9
  fan7               53.516      77.9
  fan8               53.516      77.1
  fan9               53.516      80.1
  fan10              53.516      77.0
  fan11              53.516      78.6
  fan12              53.516      80.2
  fan13              53.516      81.0
  fan14              53.516      80.3
  fan15              53.516      80.8
  fan16              53.516      80.0
  fan17              53.516      80.3

  768 racks: inlet at the top 20.1 to 20.5 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  F1-06            13.0 kW     20.8     20.5     30.1     9.3 K   ok
  F32-06           13.0 kW     20.8     20.5     30.1     9.2 K   ok
  F1-05            13.0 kW     20.8     20.5     30.2     9.4 K   ok
  F1-07            13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F32-05           13.0 kW     20.8     20.5     30.1     9.3 K   ok
  F32-07           13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F1-08            13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F32-08           13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F1-09            13.0 kW     20.9     20.4     30.2     9.3 K   ok
  F4-21            13.0 kW     21.6     20.4     31.6    10.0 K   ok

  Validation
    PASS  mass_balance         fan supplies 909.77882 kg/s and draws 909.77879 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (17 units), 0.0% of the 909.779 kg/s they move
    PASS  energy_closure       the return air carries 9978.14 kW of the 9984.00 kW installed (100%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 31.0, plenum 30.6, fan_back 30.9 degC (0.30 K apart)
    PASS  rack_resistance      the field drops 27.1 Pa across the rows where the rack curve at 2,720,000 m3/h asks for 24.8 Pa (109%) (rows 26.6 to 29.4 Pa)
    PASS  grille_resistance    the field drops 11.83 Pa across the return grilles where their K at 2,720,000 m3/h asks for 10.66 Pa (111%)
    PASS  fan_capacity         the most loaded unit (fan13) costs 81.0 Pa, the least 77.0 Pa, and the fan wall's datasheet offers 100 Pa at 160,000 m3/h per unit (81%); uncontrolled at full speed it would run at 168,301 m3/h and 89.6 Pa
    PASS  settled              the places moved at most 0.04 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 20.5 degC at the top of F1-06 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 9.47 m/s against a plausible 12.35 m/s (peak air 62.5 degC)
