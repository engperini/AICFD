# 2026-09-20 — A check that invented a fault: the return path on a half-empty row

**Question.** An engineer builds a typical row with some cabinets at 32 kW and
some at 0 kW, runs the POD, and `return_path` fails by nearly nine kelvin:

```
FAIL  return_path
nothing heats or cools the air between the rack outlet and the fan intake, so
these have to agree: hot_aisle 28.1, plenum 27.4, fan_back 36.3 degC
(8.93 K apart) -- still filling
```

Is the field still filling, is the containment leaking, or is the check wrong?

**Answer.** The check was wrong, and the message's "still filling" was not a
measurement — it was text appended to every failure. See ADR-078.

## Reproducing it

`cases/pod-uneven-row.yaml`: the worked POD with a typical row of six
positions — 0, 32, 0, 32, 0, 32 kW — 96 kW into 26 000 m³/h, 75 600 cells,
800 iterations, about three minutes. The first run of it, before the fix:

```
PASS  mass_balance      fan supplies 8.69641 kg/s and draws 8.69641 kg/s (0.000% apart)
PASS  sealed_envelope   every wall and baffle carries zero flow
PASS  no_backflow       0.00000 kg/s reverses through the fan intake
PASS  energy_closure    the return air carries 95.77 kW of the 96.00 kW installed (100%)
FAIL  return_path       hot_aisle 30.3, plenum 31.9, fan_back 31.1 degC (1.54 K apart) -- still filling
PASS  settled           the places moved at most 0.20 K since the previous sample
```

Every balance closed, the field had stopped moving, and one check still said
the loop was broken. Smaller than the engineer's 8,93 K, but the same fault.

## Measuring the same field properly

The statement `return_path` makes is about a *stream*: between the rack outlet
and the fan intake nothing adds or removes heat, so the air at each station is
the same air. Measured as streams, on that same step:

| | |
|---|---|
| leaving the containment through the ceiling (mixing cup) | 30,94 °C |
| arriving at the fan intakes (mixing cup, already in the KPIs) | 30,97 °C |
| **apart** | **0,02 K** |

Nothing was filling. Nothing was leaking.

## Why three probes could not see it

The cell layer under the false ceiling, across the hot aisle, ran from
**24,5 °C to 37,3 °C** — 12,8 K apart along the length of one aisle. That is
not a fault: a blanked cabinet passes the cold air it is given straight
through (ADR-054) and the 32 kW cabinet beside it discharges at 36 °C. It is
what a half-populated row *is*.

Three point probes in a 12,8 K field estimate its mean the way three coins
estimate a coin's bias. Two couplings turn that from bad luck into a bias:

* `hot_aisle` and `plenum` are the **same three columns** at two heights, so
  they land on the same cabinets and miss together;
* the typical row is **replicated down the hall** (ADR-074), so a column that
  lands on a cold cabinet lands on a cold one in every row.

That is the signature in the engineer's numbers. `hot_aisle` 28,1 and `plenum`
27,4 agree with each other because they share the mistake; `fan_back` 36,3 is
the odd one out because it is the only probe downstream of the mixing — it was
the one telling the truth.

## Two stored results carry the same fingerprint

| result | verdict |
|---|---|
| `hall-hotrow-independent` | FAIL, 1,83 K |
| `hall-hotrow-team` | PASS, 1,46 K |

A pass at 1,46 K and a fail at 1,83 K on the same tolerance is not two
different rooms; it is one measurement with a scatter of about that size.

## What the run says now

```
PASS  return_path   nothing heats or cools the air between the containment and the
                    fan intake, so these have to agree: the aisles pass 31.0 degC
                    through the ceiling and the units draw 31.0 degC (0.01 K apart)
```

The probes are still sampled and still in the places table, where their
disagreement is the finding it always was — the room is uneven, and an
engineer with a half-populated row wants to know by how much.

## The part worth remembering

The failing message said `-- still filling` every single time it failed,
because the string was concatenated onto every failure. It cost an engineer a
longer run on a field that had already converged, and it did it in the voice
of a measurement. A diagnosis printed beside a number is read as part of the
number. Either measure it or do not say it.
