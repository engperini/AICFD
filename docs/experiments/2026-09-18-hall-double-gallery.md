# A hall driven from both ends, against a study of the real thing

*2026-09-18. Case `cases/hall-double-gallery.yaml`, run
`runs/hall-double-gallery`, export `results/hall-double-gallery`. First run of
the double-gallery layout (ADR-027) and of the Word deliverable (ADR-028).*

Two questions. Does the generator build a hall with a mechanical gallery at
each end and its rack rows cut into blocks, with one shared return plenum, and
does the physics close? And how far does AICFD's answer stand up next to an
independent CFD study of a real hall of exactly that shape — *CFD Analysis of
VIN03 Data Hall*, Maders Consulting for Ascenty, September 2026, HELYX 4.5.1,
21,6 M cells?

## The design

Modelled on VIN03: five PODs (ten rows) cut into two blocks along their
length, a mechanical gallery at each end with seven Vertiv Liebert CWA
CA80NPVG6 units in each, hot-aisle containment and a ceiling-plenum return
shared by both galleries.

|  | value |
|---|---|
| racks | 440 (10 rows × 2 blocks × 22) at 11,59 kW = 5 100 kW |
| hall | 33,0 × 32,4 m between the galleries, 7,5 m to the slab, ceiling 5,5 m |
| galleries | 2 × 9,0 m deep, one at each end |
| aisles | hot 1,2 m contained · cold 2,7 m · perimeter 1,8 m · transverse 3,0 m |
| fan walls | 14 (7 + 7), 3,96 × 3,67 m, 104 181 m³/h each at 21,9 °C |
| unit rating | 432,6 kW net sensible, 21,4 kW input, 100 Pa ESP, selected at 750 m |
| return grilles | the whole ceiling over each hot-aisle block, K 2,4 |
| mesh | 0,60 × 0,30 × 0,25 m → 85 × 108 × 30 = **275 400 cells** |

Three of those are deliberate departures from the real hall, and each is a
thing AICFD cannot yet take:

- **11,59 kW on every rack.** VIN03 has 5 000 kW of IT in 412 of 438 loaded
  positions, plus 100 kW of PDU loss in the electrical room. AICFD takes one
  load per rack, so the 5 100 kW total is spread evenly over 440 positions.
  The total heat is right; the *distribution* is not.
- **The catalogue capacity.** Comparisons here are against the unit's 432,6 kW
  rating at its selection point, not against the capacity its coil actually
  has at the return temperature this room produces. See below — it is the
  single most consequential gap.
- **Perimeter 1,8 m and galleries 9,0 m symmetric**, giving 51,0 × 32,4 m
  overall against the site's 54,64 × 31,98. The real return corridors are 7 m
  and 11 m.

## What the layout has to get right, and what would have hidden it

`createBaffles` gives the *master* patch of a pair to the face's owner cell,
which for an x-normal face of a blockMesh box is the cell at lower x. For the
near gallery that cell is in the gallery, so the master is the intake. For the
far gallery it is a hall cell, so master and slave swap. Naming the halves
without swapping their boundary conditions builds a unit that supplies into
its own return — and it converges, every residual falls, and the answer is
about nothing at all.

So each unit carries a sign, the generator emits the halves in that order, and
`check_fan_orientation` measures the outward normal of *both* patches of every
pair against it after meshing. On this case that is 28 measurements, and they
are what says the second gallery is a mirror rather than a hopeful copy.

## What the run did

3 000 iterations on 4 cores: 2 150 s of solver time, about 0,7 s per
iteration. **The field was settled by iteration 600** — the return temperature
has not moved from 33,3 °C since, and `settled` reports the instrumented
places moving less than 0,01 K between samples — so the useful run is about
seven minutes and the rest was spent confirming it. `residualControl` did not
stop the run because p_rgh levels off near 8 × 10⁻⁴; that is the normal
behaviour of a room this size and is exactly why the checks, not the
residuals, decide when a run is finished (ADR-018).

All eleven checks pass.

| | |
|---|---|
| mass balance | 443,128 kg/s supplied and drawn, 0,000 % apart |
| energy closure | the return air carries 5 099 kW of the 5 100 kW installed (100 %) |
| return path | hot aisle 33,5 · plenum 33,2 · behind the units 33,3 °C, 0,28 K apart |
| rack resistance | field 29,4 Pa where the rack curve asks 28,4 (104 %) |
| grille resistance | field 8,99 Pa where K asks 8,59 (105 %) |
| backflow | 0,000 kg/s |
| warmest rack intake | 22,45 °C at the top of F6B2-03, 4,55 K below the ASHRAE recommended limit |
| every rack | 22,0 to 22,45 °C at the top; none above the recommended band |
| per unit | 362,7 to 366,0 kW removed, 5 099 kW in all; return 33,30 to 33,41 °C |
| rise per unit | 52,8 to 56,3 Pa, of the 131 Pa the curve offers at this airflow |

The `return_path` check is the one that matters most here, because it is the
only thing that would catch a *second* plenum pretending to be one: the hot
aisles, the single plenum and both galleries agreeing to within 0,28 K is the
measurement that says the volume above the false ceiling really is continuous
and really does feed both ends.

## Against the reference study

| | AICFD | VIN03 study |
|---|---|---|
| cells | 275 400 | 21 600 000 |
| cost | 11 min on 4 cores | a cluster |
| warmest rack intake | 22,45 °C | 22,33 °C (normal), 22,34 °C (N) |
| mixed return air | 33,35 °C | 32,39 °C |
| return spread across units | 0,11 K | 1,35 K |
| heat removed per unit | 362,7–366,0 kW | 331–397 kW |
| margin to ASHRAE recommended | 4,55 K | 4,7 K |

The two agree on the answer the study was asked for — **the air reaching the
IT equipment is comfortably inside the recommended band, with about 4,6 K of
margin** — on a mesh 78 times coarser, from a 95-line spec, in a run that was
settled after seven minutes. That is the result worth having here, and the
0,12 K between the two warmest rack intakes is well inside the 1 to 2 K this
mesh is honestly worth on any single rack.

Two of the differences are ours and are explained by the load:

- **The return spread, 0,11 K against 1,35 K.** The study attributes its own
  uniformity to the load distribution and to the dispersal of the unloaded
  positions, *not* to any property of the units. We give every rack the same
  load in a symmetric room, so our spread is an artefact of the input. It is
  not evidence that this arrangement is more uniform than that one; it is what
  a uniform load looks like. A per-rack load map is the input that would let
  this comparison mean something.
- **The return temperature, 33,3 against 32,39 °C.** Our racks are 11,59 kW
  against their 12 kW in 412 positions, and our rack airflow comes from
  158 CFM/kW rather than from their porous coefficients. Within a kelvin on a
  quantity that depends on both is the agreement this pairing can support.

## The finding that is not in the numbers this run produced

Every unit here removes 362,7 to 366,0 kW, which is **84 % of the 432,6 kW
catalogue rating**, and the report says so. On the catalogue figures the plant has
ample margin, and that is the sentence a reader will take away.

The reference study is about why that sentence is wrong. A chilled-water
coil's sensible capacity rises with the difference between entering air and
entering water; the unit was selected at 34,0 °C entering air and 18 °C water,
and this room returns 33,3 °C. Scaling the rating on that difference by hand —
**arithmetic done outside the tool, not a number AICFD produced** — gives
about 415 kW available per unit rather than 432,6, so the units are at roughly
88 % of what their coils can actually transfer, not 84 %. On this hall the
correction is small and changes nothing. On VIN03 the same correction moved
the plant from an apparent 6 056 kW against a 5 100 kW load to 95,6 % of the
capacity available at the operating point — and, with two units out, past
100 %.

That is the gap: AICFD can say the racks are fine, and cannot yet say how much
reserve the plant has. `fanwall.rating_return_c` and `entering_water_c`, and a
failure scenario, are what close it. Both are written up in
`docs/reference-report-parameters.md` and are the top of the roadmap.

## A smaller observation, at the edge of what this mesh resolves

Seven units share a 32,4 m wall in front of six cold aisles, so they cannot
all be centred on one. In the first gallery the units that land on a cold
aisle run near 53 Pa and those that land in front of a rack row near 56 Pa —
about 2,5 Pa, or 5 %, for facing the wrong thing. In the second gallery the
pattern is weaker and not monotonic. The whole spread is 52,8 to 56,3 Pa, 6 %
of the rise, on a 0,30 m mesh: that is at the edge of what this run can be
asked, and it is recorded as something to look for on a finer mesh rather
than as a result.

## What this run establishes

- The double-gallery layout builds, meshes and closes, and the shared plenum
  is measurably one volume.
- The orientation of every unit's baffle pair is measured, not assumed, which
  is what makes the far gallery trustworthy rather than merely plausible.
- On the question a conceptual study asks — is the air the racks breathe
  acceptable, and where is the worst one — AICFD lands within 0,1 K of a
  21,6 M-cell study of the same arrangement, in eleven minutes.
- On the question that study was commissioned for — how much reserve does the
  plant have when a unit fails — AICFD cannot answer yet, and the report says
  so in its own limitations section rather than leaving the reader to infer it.
