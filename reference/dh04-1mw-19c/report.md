Case 'dh04-1mw-19c' at iteration 8401

  Room unit       381,087 m3/h (127.901 kg/s) at 19.5 degC
  Return air      27.3 degC (dT 7.8 K)
  Heat carried    997.38 kW of 999.09 kW installed (100%)
  Peak air        50.1 degC, 4.01 m/s
  Still moving    0.02 K since the previous sample
  Return path     27.2 -> 27.3 degC, 0.06 K apart (aisle 22.8-40.2)

  Room unit rise  124.0 Pa of the 50 Pa on the datasheet (248%) on the most loaded of 14 units (118.6 on the least)
  Rack row        94.7 Pa across the row, in the field
  Return grilles  0.00 Pa in the field, 0.00 Pa from K
  Floor plates    4.21 Pa in the field, 3.15 Pa from K, 1.25 m/s on the face
  HVAC sizing     capacity   14 x 100.5 kW = 1,407 kW for 999 kW of IT (141%)
  HVAC sizing     airflow    385,000 m3/h for 268,198 m3/h at 158 CFM/kW (144%)

  Station              Temp        Range        Flow     Speed
  Supply                19.5     19.0-23.2     381,087      3.50
  Rack intake           20.0     19.1-24.7     381,087      0.43
  Aisle exit            27.2     22.8-40.2     381,087      1.25
  Unit return           27.3     24.4-34.2     381,087      3.50

  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW  Available kW   of avail
  fan1                9.136     122.1         34.21      101.4         101.4       100%
  fan2                9.136     121.0         32.76      101.4         101.4       100%
  fan3                9.136     119.5         28.00       82.7          91.5        90%
  fan4                9.136     119.2         25.89       63.2          79.4        80%
  fan5                9.136     118.6         25.40       58.8          76.6        77%
  fan6                9.136     120.1         25.16       56.6          75.2        75%
  fan7                9.136     120.7         24.64       51.8          72.2        72%
  fan8                9.136     124.0         30.22      101.4         101.4       100%
  fan9                9.136     123.7         28.96       91.4          97.0        94%
  fan10               9.136     121.4         27.00       73.5          85.8        86%
  fan11               9.136     120.6         25.44       59.1          76.8        77%
  fan12               9.136     120.8         24.94       54.5          73.9        74%
  fan13               9.136     121.6         24.56       51.1          71.8        71%
  fan14               9.136     121.9         24.45       50.0          71.1        70%

  Coils (P3100DA): the plant removes 997 kW of the 1,176 kW its coils can transfer at the air they are receiving (84.8%); the catalogue figure at the selection point is 1,407 kW. Its evaporator is fitted to the design selection, with the coil surface at 10.52 degC and 63.0% of the air reaching it.

  171 racks: inlet at the top 19.2 to 23.8 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  B01              20.0 kW     24.6     23.8     42.5    17.9 K   ok
  A01              20.0 kW     24.7     23.7     46.1    21.4 K   ok
  B02              10.0 kW     23.9     23.6     36.8    12.9 K   ok
  A02              10.0 kW     24.2     23.6     37.7    13.6 K   ok
  A03              10.0 kW     23.8     23.5     35.9    12.1 K   ok
  B03              10.0 kW     23.6     23.5     35.6    12.0 K   ok
  A04              10.0 kW     23.6     23.4     35.6    12.0 K   ok
  B04              10.0 kW     23.4     23.4     35.4    11.9 K   ok
  A05              10.0 kW     23.4     23.4     35.4    12.0 K   ok
  A06              10.0 kW     23.2     23.1     35.3    12.1 K   ok

  Validation
    PASS  mass_balance         fan supplies 127.90119 kg/s and draws 127.90120 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (14 units), 0.0% of the 127.901 kg/s they move
    PASS  energy_closure       the return air carries 997.38 kW of the 999.09 kW installed (100%)
    PASS  return_path          the path from the containment to the fan intake is adiabatic, so these have to agree: the aisles pass 27.2 degC through the ceiling and the units draw 27.3 degC (0.06 K apart); the air crossing the ceiling spans 22.8 to 40.2 degC, which is the room being uneven; the path holds
    PASS  rack_resistance      the field drops 94.7 Pa across the rows where the rack curve at 385,000 m3/h asks for 104.1 Pa (91%) (rows 90.8 to 99.9 Pa) -- 5 of 171 positions carry no load; blanked, they resist like the rest, so the row is still one resistance and only the heat is missing
    PASS  floor_resistance     the field drops 4.21 Pa across the 238 floor plates where their K at 385,000 m3/h asks for 3.15 Pa; the air reaches it 1.4 times harder than the rated face velocity assumes, so its own K asks 4.35 Pa of this field (97%); they run at 1.25 m/s on the face
    PASS  fan_capacity         the room outside the cabinets costs the most loaded unit (fan8) 29.3 Pa -- 124.0 Pa of loop less the 94.7 Pa the cabinets' own fans carry -- and the unit's datasheet offers 50 Pa at 27,500 m3/h per unit (59%)
    PASS  coil_closure         every unit delivers the air its coil makes at the return it receives (0.00 K apart at worst, on fan1)
    PASS  settled              the stations moved at most 0.02 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 23.8 degC at the top of B01 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 4.01 m/s against a plausible 17.68 m/s (peak air 50.1 degC)
