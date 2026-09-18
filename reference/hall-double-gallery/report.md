Case 'hall-double-gallery' at iteration 3000

  Fan wall        1,458,533 m3/h (443.128 kg/s) at 21.9 degC
  Return air      33.4 degC (dT 11.4 K)
  Heat carried    5099.20 kW of 5099.60 kW installed (100%)
  Peak air        38.3 degC, 5.50 m/s
  Still moving    0.00 K since the previous sample
  Return path     33.5 -> 33.2 -> 33.3 degC, 0.29 K apart

  Fan wall rise   56.3 Pa of the 131 Pa on the datasheet (43%) on the most loaded of 14 units (52.8 on the least)
  Rack row        29.4 Pa across the row, in the field
  Return grilles  8.99 Pa in the field, 8.59 Pa from K
  HVAC sizing     capacity   14 x 432.6 kW = 6,056 kW for 5,100 kW of IT (119%)
  HVAC sizing     airflow    1,458,534 m3/h for 1,368,956 m3/h at 158 CFM/kW (106%)

  Place                  Temp    dP vs intake    Speed
  Cold aisle              22.0            53.9     1.35
  Hot aisle               33.5            24.6     1.63
  Ceiling plenum          33.2            10.7     2.32
  Behind the fan wall     33.3            -1.2     2.84

  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW
  fan1               31.652      55.6         33.32      363.3
  fan2               31.652      53.3         33.34      363.9
  fan3               31.652      55.8         33.38      365.1
  fan4               31.652      52.8         33.41      366.0
  fan5               31.652      56.3         33.37      365.0
  fan6               31.652      53.3         33.34      363.8
  fan7               31.652      55.8         33.31      362.9
  fan8               31.652      54.2         33.34      363.8
  fan9               31.652      53.1         33.34      363.8
  fan10              31.652      53.7         33.36      364.5
  fan11              31.652      54.7         33.40      365.7
  fan12              31.652      54.9         33.37      365.0
  fan13              31.652      53.0         33.33      363.7
  fan14              31.652      55.7         33.30      362.7

  440 racks: inlet at the top 22.0 to 22.4 degC, 0 above ASHRAE recommended. The ten warmest:

  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE
  F6B2-03          11.6 kW     23.1     22.4     33.7    10.6 K   ok
  F5B2-05          11.6 kW     23.2     22.4     33.8    10.6 K   ok
  F5B1-20          11.6 kW     22.9     22.4     33.7    10.8 K   ok
  F3B2-03          11.6 kW     23.1     22.4     33.7    10.6 K   ok
  F3B2-04          11.6 kW     23.1     22.4     33.7    10.6 K   ok
  F6B2-02          11.6 kW     22.9     22.4     33.7    10.8 K   ok
  F5B1-19          11.6 kW     23.1     22.4     33.7    10.6 K   ok
  F5B2-04          11.6 kW     23.1     22.4     33.7    10.6 K   ok
  F5B2-06          11.6 kW     23.2     22.4     33.8    10.6 K   ok
  F6B2-04          11.6 kW     23.1     22.4     33.7    10.6 K   ok

  Validation
    PASS  mass_balance         fan supplies 443.12802 kg/s and draws 443.12803 kg/s (0.000% apart)
    PASS  sealed_envelope      every wall and baffle carries zero flow
    PASS  no_backflow          0.00000 kg/s reverses through the fan intakes (14 units), 0.0% of the 443.128 kg/s they move
    PASS  energy_closure       the return air carries 5099.20 kW of the 5099.60 kW installed (100%)
    PASS  return_path          nothing heats or cools the air between the rack outlet and the fan intake, so these have to agree: hot_aisle 33.5, plenum 33.2, fan_back 33.3 degC (0.29 K apart)
    PASS  rack_resistance      the field drops 29.4 Pa across the rows where the rack curve at 1,458,534 m3/h asks for 28.4 Pa (104%) (rows 29.2 to 30.0 Pa)
    PASS  grille_resistance    the field drops 8.99 Pa across the return grilles where their K at 1,458,534 m3/h asks for 8.59 Pa (105%)
    PASS  fan_capacity         the most loaded unit (fan5) costs 56.3 Pa, the least 52.8 Pa, and the unit's P-Q curve offers 131 Pa at 104,181 m3/h per unit (43%); uncontrolled at full speed it would run at 128,785 m3/h and 86.0 Pa
    PASS  settled              the places moved at most 0.00 K since the previous sample (settled below 0.25 K)
    PASS  ashrae_inlet         warmest rack inlet 22.4 degC at the top of F6B2-03 -- within ASHRAE recommended (18.0-27.0 degC)
    PASS  plausible_velocity   peak 5.50 m/s against a plausible 11.99 m/s (peak air 38.3 degC)
