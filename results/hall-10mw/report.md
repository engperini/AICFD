Case 'hall-10mw' at iteration 500

  Fan wall        2,682,749 m3/h (709.467 kg/s) at 22.1 degC
  Return air      36.1 degC (dT 14.0 K)
  Heat carried    9982.97 kW of 9984.00 kW installed (100%)
  Peak air        55.2 degC, 9.45 m/s
  Still moving    0.03 K since the previous sample
  Return path     36.2 -> 35.9 -> 36.0 degC, 0.31 K apart

  Fan wall rise   73.2 Pa of the 100 Pa on the datasheet (73%) on the most loaded of 35 units (67.5 on the least)
  Rack row        26.8 Pa across the row, in the field
  Return grilles  9.20 Pa in the field, 8.30 Pa from K
  HVAC sizing     capacity   35 x 290.9 kW = 10,182 kW for 9,984 kW of IT (102%)
  HVAC sizing     airflow    2,682,750 m3/h for 2,680,142 m3/h at 158 CFM/kW (100%)

  Place                  Temp    dP vs intake    Speed
  Corredor frio           22.4            68.4     2.20
  Corredor quente         36.2            40.9     1.76
  Plenum do forro         35.9            28.4     2.55
  Costas do fan wall      36.0             1.8     3.81

  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW
  fan1               20.270      67.8         35.83      279.6
  fan2               20.270      70.3         36.00      283.1
  fan3               20.270      69.9         36.03      283.8
  fan4               20.270      70.5         36.16      286.4
  fan5               20.270      69.9         36.08      284.9
  fan6               20.270      71.5         36.15      286.1
  fan7               20.270      71.9         36.07      284.7
  fan8               20.270      72.0         36.10      285.1
  fan9               20.270      71.1         36.05      284.2
  fan10              20.270      70.9         36.09      285.0
  fan11              20.270      68.0         36.11      285.4
  fan12              20.270      70.3         36.14      286.1
  fan13              20.270      67.5         36.15      286.2
  fan14              20.270      72.1         36.14      286.0
  fan15              20.270      67.5         36.16      286.3
  fan16              20.270      72.5         36.14      286.0
  fan17              20.270      68.5         36.19      287.1
  fan18              20.270      70.8         36.15      286.2
  fan19              20.270      68.9         36.21      287.4
  fan20              20.270      71.8         36.18      286.7
  fan21              20.270      68.2         36.18      286.8
  fan22              20.270      72.5         36.15      286.2
  fan23              20.270      69.2         36.12      285.7
  fan24              20.270      73.1         36.11      285.5
  fan25              20.270      69.3         36.07      284.7
  fan26              20.270      73.2         36.12      285.6
  fan27              20.270      71.2         36.10      285.2
  fan28              20.270      71.8         36.16      286.4
  fan29              20.270      72.5         36.10      285.3
  fan30              20.270      71.5         36.16      286.4
  fan31              20.270      70.2         36.10      285.2
  fan32              20.270      70.6         36.17      286.5
  fan33              20.270      69.8         36.04      283.9
  fan34              20.270      70.2         36.00      283.2
  fan35              20.270      67.8         35.83      279.8

  768 racks: inlet at the top 22.2 to 22.8 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  F1-05            13.0 kW     23.1     22.8     35.3    12.2 K   ok
  F1-06            13.0 kW     23.2     22.8     35.3    12.1 K   ok
  F32-05           13.0 kW     23.1     22.8     35.3    12.2 K   ok
  F32-06           13.0 kW     23.2     22.8     35.3    12.1 K   ok
  F2-21            13.0 kW     24.1     22.8     36.9    12.8 K   ok
  F1-07            13.0 kW     23.2     22.7     35.3    12.0 K   ok
  F31-21           13.0 kW     24.1     22.7     36.9    12.8 K   ok
  F32-07           13.0 kW     23.2     22.7     35.3    12.0 K   ok
  F1-08            13.0 kW     23.3     22.7     35.3    12.0 K   ok
  F32-08           13.0 kW     23.3     22.7     35.3    12.0 K   ok

  Validation
    PASS  mass_balance         fan supplies 709.46749 kg/s and draws 709.46749 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (35 units), 0.0% of the 709.467 kg/s they move
    PASS  energy_closure       the return air carries 9982.97 kW of the 9984.00 kW installed (100%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 36.2, plenum 35.9, fan_back 36.0 degC (0.31 K apart)
    PASS  rack_resistance      the field drops 26.8 Pa across the rows where the rack curve at 2,682,750 m3/h asks for 25.0 Pa (107%) (rows 26.5 to 28.0 Pa)
    PASS  grille_resistance    the field drops 9.20 Pa across the return grilles where their K at 2,682,750 m3/h asks for 8.30 Pa (111%)
    PASS  fan_capacity         the most loaded unit (fan26) costs 73.2 Pa, the least 67.5 Pa, and the fan wall's datasheet offers 100 Pa at 76,650 m3/h per unit (73%); uncontrolled at full speed it would run at 82,530 m3/h and 84.9 Pa
    PASS  settled              the places moved at most 0.03 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 22.8 degC at the top of F1-05 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 9.45 m/s against a plausible 13.69 m/s (peak air 55.2 degC)
