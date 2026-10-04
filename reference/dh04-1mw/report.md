Case 'dh04-1mw' at iteration 4800

  Room unit       385,000 m3/h (127.901 kg/s) at 22.5 degC
  Return air      30.3 degC (dT 7.8 K)
  Heat carried    995.70 kW of 999.09 kW installed (100%)
  Peak air        53.6 degC, 4.07 m/s
  Still moving    0.01 K since the previous sample
  Return path     30.2 -> 30.3 degC, 0.08 K apart (aisle 26.0-43.6)

  Room unit rise  126.1 Pa of the 50 Pa on the datasheet (252%) on the most loaded of 14 units (121.9 on the least)
  Rack row        95.6 Pa across the row, in the field
  Return grilles  0.00 Pa in the field, 0.00 Pa from K
  Floor plates    4.25 Pa in the field, 3.12 Pa from K, 1.25 m/s on the face
  HVAC sizing     capacity   14 x 100.5 kW = 1,407 kW for 999 kW of IT (141%)
  HVAC sizing     airflow    385,000 m3/h for 268,198 m3/h at 158 CFM/kW (144%)

  Station              Temp        Range        Flow     Speed
  Supply                22.5     22.0-26.7     385,000      3.54
  Rack intake           23.0     22.1-28.1     385,000      0.44
  Aisle exit            30.2     26.0-43.6     385,000      1.25
  Unit return           30.3     27.4-37.8     385,000      3.54

  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW  Available kW   of avail
  fan1                9.136     125.7         37.79      101.6         101.4       100%
  fan2                9.136     123.7         35.79      101.7         101.4       100%
  fan3                9.136     121.9         30.97       82.4         101.4        81%
  fan4                9.136     122.4         28.80       62.5          96.1        65%
  fan5                9.136     122.3         28.38       58.6          93.7        62%
  fan6                9.136     124.5         28.05       55.6          91.8        61%
  fan7                9.136     123.3         27.64       51.8          89.5        58%
  fan8                9.136     126.1         33.18      101.1         101.4       100%
  fan9                9.136     126.1         31.97       91.6         101.4        90%
  fan10               9.136     123.7         30.04       73.8         101.4        73%
  fan11               9.136     123.2         28.44       59.1          94.1        63%
  fan12               9.136     123.3         27.94       54.6          91.2        60%
  fan13               9.136     124.2         27.56       51.0          89.0        57%
  fan14               9.136     124.2         27.45       50.0          88.4        57%

  Coils (P3100DA): the plant removes 996 kW of the 1,342 kW its coils can transfer at the air they are receiving (74.2%); the catalogue figure at the selection point is 1,407 kW. Its evaporator is fitted to the design selection, with the coil surface at 10.52 degC and 63.0% of the air reaching it.

  171 racks: inlet at the top 22.2 to 27.3 degC, 4 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  B01              20.0 kW     28.0     27.3     45.9    17.9 K   !
  A01              20.0 kW     28.1     27.2     49.5    21.4 K   !
  B02              10.0 kW     27.3     27.1     40.2    12.9 K   !
  A02              10.0 kW     27.6     27.1     41.2    13.6 K   !
  A03              10.0 kW     27.3     27.0     39.3    12.1 K   ok
  A04              10.0 kW     27.1     26.9     39.0    11.9 K   ok
  B03              10.0 kW     27.0     26.9     39.0    11.9 K   ok
  A05              10.0 kW     26.7     26.8     38.6    11.9 K   ok
  B04              10.0 kW     26.8     26.7     38.6    11.8 K   ok
  B05              10.0 kW     26.1     26.2     38.0    11.9 K   ok

  Validation
    PASS  mass_balance         fan supplies 127.90120 kg/s and draws 127.90120 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (14 units), 0.0% of the 127.901 kg/s they move
    PASS  energy_closure       the return air carries 995.70 kW of the 999.09 kW installed (100%)
    PASS  return_path          the path from the containment to the fan intake is adiabatic, so these have to agree: the aisles pass 30.2 degC through the ceiling and the units draw 30.3 degC (0.08 K apart); the air crossing the ceiling spans 26.0 to 43.6 degC, which is the room being uneven; the path holds
    PASS  rack_resistance      the field drops 95.6 Pa across the rows where the rack curve at 385,000 m3/h asks for 104.1 Pa (92%) (rows 91.4 to 100.8 Pa) -- 5 of 171 positions carry no load; blanked, they resist like the rest, so the row is still one resistance and only the heat is missing
    PASS  floor_resistance     the field drops 4.25 Pa across the 238 floor plates where their K at 385,000 m3/h asks for 3.12 Pa; the air reaches it 1.4 times harder than the rated face velocity assumes, so its own K asks 4.29 Pa of this field (99%); they run at 1.25 m/s on the face
    PASS  fan_capacity         the room outside the cabinets costs the most loaded unit (fan8) 30.5 Pa -- 126.1 Pa of loop less the 95.6 Pa the cabinets' own fans carry -- and the unit's datasheet offers 50 Pa at 27,500 m3/h per unit (61%)
    PASS  coil_closure         every unit delivers the air its coil makes at the return it receives (0.04 K apart at worst, on fan2)
    PASS  settled              the stations moved at most 0.01 K since the previous sample (settled below 0.25 K)
    FAIL  ashrae_inlet         warmest rack inlet 27.3 degC at the top of B01 -- above recommended (18.0-27.0 degC), still allowable for class A1; 4 of 171 racks above recommended
    PASS  plausible_velocity   peak 4.07 m/s against a plausible 17.68 m/s (peak air 53.6 degC)
