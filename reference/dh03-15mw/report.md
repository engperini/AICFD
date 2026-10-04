Case 'dh03-15mw' at iteration 2300

  Fan wall        805,800 m3/h (265.894 kg/s) at 24.0 degC
  Return air      27.3 degC (dT 3.4 K)
  Heat carried    899.75 kW of 894.40 kW installed (101%)
  Peak air        34.0 degC, 7.04 m/s
  Still moving    0.00 K since the previous sample
  Return path     27.2 -> 27.3 degC, 0.16 K apart (aisle 23.9-30.0)

  Fan wall rise   79.7 Pa of the 240 Pa on the datasheet (33%) on the most loaded of 8 units (74.2 on the least)
  Rack row        33.4 Pa across the row, in the field
  Return grilles  0.00 Pa in the field, 0.00 Pa from K
  HVAC sizing     capacity   8 x 450.0 kW = 3,600 kW for 894 kW of IT (403%)
  HVAC sizing     airflow    805,800 m3/h for 240,096 m3/h at 158 CFM/kW (336%)

  Station              Temp        Range        Flow     Speed
  Supply                24.0     23.9-24.0     805,800      1.85
  Rack intake           24.5     24.1-24.8     805,800      0.64
  Aisle exit            27.2     23.9-30.0     805,800      1.06
  Unit return           27.3     27.2-27.5     805,800      1.85

  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW  Available kW   of avail
  fan1               33.237      74.2         27.33      113.5         202.5        56%
  fan2               33.237      76.4         27.41      115.0         204.7        56%
  fan3               33.237      76.8         27.41      114.9         204.7        56%
  fan4               33.237      75.3         27.48      116.1         206.6        56%
  fan5               33.237      76.8         27.32      110.8         202.2        55%
  fan6               33.237      79.7         27.28      110.3         201.1        55%
  fan7               33.237      79.7         27.18      108.4         198.3        55%
  fan8               33.237      77.5         27.30      110.4         201.6        55%

  Coils (CA80NEVGT): the plant removes 900 kW of the 1,622 kW its coils can transfer at the air they are receiving (55.5%); the catalogue figure at the selection point is 3,600 kW. Its coil is fitted to the design selection alone, with the air/water split assumed at 78% -- the one number here nobody measured.

  218 racks: inlet at the top 24.0 to 24.7 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  F4-01             4.8 kW     24.8     24.7     27.9     3.1 K   ok
  F4-02             4.8 kW     24.8     24.7     27.7     2.9 K   ok
  F6-01             4.8 kW     24.8     24.6     27.9     3.1 K   ok
  F8-01             4.8 kW     24.7     24.6     27.9     3.2 K   ok
  F8-02             4.8 kW     24.8     24.6     27.8     3.0 K   ok
  F6-02             4.8 kW     24.8     24.6     27.7     2.9 K   ok
  F4-03             4.8 kW     24.7     24.6     27.5     2.8 K   ok
  F10-01            4.8 kW     24.7     24.6     27.8     3.1 K   ok
  F13-13            4.8 kW     24.5     24.6     27.7     3.1 K   ok
  F14-01            4.8 kW     24.7     24.6     27.8     3.1 K   ok

  Validation
    PASS  mass_balance         fan supplies 265.89363 kg/s and draws 265.89361 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (8 units), 0.0% of the 265.894 kg/s they move
    PASS  energy_closure       the return air carries 899.75 kW of the 894.40 kW installed (101%)
    PASS  return_path          the path from the containment to the fan intake is adiabatic, so these have to agree: the aisles pass 27.2 degC through the ceiling and the units draw 27.3 degC (0.16 K apart); the air crossing the ceiling spans 23.9 to 30.0 degC, which is the room being uneven; the path holds
    PASS  rack_resistance      the field drops 33.4 Pa across the rows where the rack curve at 805,800 m3/h asks for 32.9 Pa (101%) (rows 32.7 to 35.0 Pa) -- 94 of 218 positions carry no load; blanked, they resist like the rest, so the row is still one resistance and only the heat is missing
    PASS  plenum_resistance    the field drops 8.79 Pa across the supply grilles where their K at 805,800 m3/h asks for 8.61 Pa (102%); they run at 3.6 m/s on the face
    PASS  fan_capacity         the room outside the cabinets costs the most loaded unit (fan7) 46.3 Pa -- 79.7 Pa of loop less the 33.4 Pa the cabinets' own fans carry -- and the unit's P-Q curve offers 240 Pa at 100,725 m3/h per unit (19%); uncontrolled at full speed it would run at 139,185 m3/h and 152.2 Pa
    PASS  coil_closure         every unit delivers the air its coil makes at the return it receives (0.01 K apart at worst, on fan1)
    PASS  settled              the stations moved at most 0.00 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 24.7 degC at the top of F4-01 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 7.04 m/s against a plausible 9.25 m/s (peak air 34.1 degC)
