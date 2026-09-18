POD 'hall-10mw' at iteration 400

  Fan wall        2,720,002 m3/h (909.779 kg/s) at 20.0 degC
  Return air      30.9 degC (dT 10.9 K)
  Heat carried    9979.55 kW of 9984.00 kW installed (100%)
  Peak air        61.8 degC, 9.48 m/s
  Still moving    0.04 K since the previous sample
  Return path     31.0 -> 30.7 -> 30.8 degC, 0.33 K apart

  Fan wall rise   83.5 Pa of the 100 Pa on the datasheet (83%) on the most loaded of 17 units (77.6 on the least)
  Rack row        27.1 Pa across the row, in the field
  Return grilles  11.86 Pa in the field, 10.66 Pa from K

  Place                  Temp    dP vs intake    Speed
  Corredor frio           20.2            78.4     2.16
  Corredor quente         31.0            50.8     1.78
  Plenum do forro         30.7            34.4     2.57
  Costas do fan wall      30.8             3.1     3.27

  Fan wall     Supply kg/s   Rise Pa
  fan1               53.516      81.2
  fan2               53.516      82.2
  fan3               53.516      83.1
  fan4               53.516      79.8
  fan5               53.516      78.5
  fan6               53.516      77.7
  fan7               53.516      77.6
  fan8               53.516      78.3
  fan9               53.516      79.7
  fan10              53.516      79.1
  fan11              53.516      78.1
  fan12              53.516      78.2
  fan13              53.516      79.0
  fan14              53.516      80.6
  fan15              53.516      83.5
  fan16              53.516      82.7
  fan17              53.516      81.8

  768 racks: inlet at the top 20.1 to 20.5 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  F1-06            13.0 kW     20.8     20.5     30.1     9.3 K   ok
  F1-05            13.0 kW     20.8     20.5     30.2     9.4 K   ok
  F1-07            13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F32-06           13.0 kW     20.8     20.5     30.1     9.2 K   ok
  F32-05           13.0 kW     20.8     20.5     30.1     9.3 K   ok
  F32-07           13.0 kW     20.9     20.5     30.1     9.2 K   ok
  F1-08            13.0 kW     20.9     20.5     30.1     9.3 K   ok
  F2-04            13.0 kW     21.0     20.4     31.0    10.0 K   ok
  F2-05            13.0 kW     21.1     20.4     30.8     9.7 K   ok
  F31-05           13.0 kW     21.1     20.4     30.8     9.7 K   ok

  Validation
    PASS  mass_balance         fan supplies 909.77881 kg/s and draws 909.77878 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (17 units), 0.0% of the 909.779 kg/s they move
    PASS  energy_closure       the return air carries 9979.55 kW of the 9984.00 kW installed (100%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 31.0, plenum 30.7, fan_back 30.8 degC (0.33 K apart)
    PASS  rack_resistance      the field drops 27.1 Pa across the rows where the rack curve at 2,720,000 m3/h asks for 24.8 Pa (110%) (rows 26.7 to 29.3 Pa)
    PASS  grille_resistance    the field drops 11.86 Pa across the return grilles where their K at 2,720,000 m3/h asks for 10.66 Pa (111%)
    PASS  fan_capacity         the most loaded unit (fan15) costs 83.5 Pa, the least 77.6 Pa, and the fan wall's datasheet offers 100 Pa at 160,000 m3/h per unit (83%); uncontrolled at full speed it would run at 167,125 m3/h and 91.1 Pa
    PASS  settled              the places moved at most 0.04 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 20.5 degC at the top of F1-06 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 9.48 m/s against a plausible 12.35 m/s (peak air 61.8 degC)
