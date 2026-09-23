# Architecture Decision Records

Short entries. Each one records a choice that a future contributor would otherwise
have to re-derive, and the reason it was made.

---

## ADR-001 — OpenFOAM as the solver, not a custom one

**Decision.** Use OpenFOAM (`buoyantSimpleFoam`) as the numerical engine. AICFD is a
modeling and interface layer, not a solver project.

**Why.** Data hall cooling is steady-state buoyant convection with porous media — a
solved problem with a mature, validated, GPL implementation. Writing a solver would
mean re-validating fluid dynamics that OpenFOAM validated decades ago, and would add
nothing to the actual problem, which is that the existing solver is unusable by a
facility engineer without a week of training.

**Cost accepted.** OpenFOAM's environment handling is fragile (see ADR-002), its
dictionary format is verbose, and version drift between distributions is real. All
three are contained by generating dictionaries and pinning the version in Docker.

---

## ADR-002 — Run every OpenFOAM command in an isolated environment

**Decision.** Never invoke OpenFOAM binaries from an inherited shell. Always run
through `env -i` with an explicit, minimal variable set.

**Why.** The Ubuntu `openfoam` package (v1912) installs the solvers into `/usr/bin`
but omits the shell helpers (`foamEtcFile`, `foamCleanPath`) that `etc/bashrc`
expects. A partially-executed `bashrc` leaves `WM_PROJECT_USER_DIR` and friends in a
poisoned state, and every solver then fails with:

```
FOAM FATAL ERROR: Could not find mandatory etc entry (mode=ugo) 'controlDict'
```

The failure mode is confusing because it names `controlDict`, which exists and is
valid — the resolution path is what is broken, not the file.

**Consequence.** `aicfd/run.py` owns this. No other module shells out to OpenFOAM.
When the Docker image pins a different OpenFOAM build, this is the single place that
changes.

---

## ADR-003 — Racks as porous zones, not resolved geometry

**Decision.** A rack is a `cellZone` with a Darcy-Forchheimer resistance tensor plus a
volumetric enthalpy source, not modeled server hardware.

**Why.** This is what makes the simulation tractable — minutes instead of days — and
it is exactly what the commercial tools do internally. Resolving the geometry of a
server chassis would add no accuracy at the scale that matters (room-level airflow and
inlet temperatures) while multiplying cell count by orders of magnitude.

The anisotropic resistance is the whole trick: a high coefficient on the lateral axes
forces air through the rack front-to-back, as a real rack does, and a moderate
coefficient on the flow axis reproduces the equipment pressure drop.

**Consequence.** AICFD cannot answer questions *inside* a rack (component-level
temperatures, fan curves per server). It answers room-level questions. That boundary
should stay explicit in the docs so nobody over-trusts the output.

---

## ADR-004 — One YAML file is the case; the OpenFOAM directory is a build artifact

**Decision.** The case spec (`cases/<name>.yaml`) is committed. The generated case directory is gitignored and
regenerated on demand.

**Why.** An OpenFOAM case is ~20 files of coupled dictionaries where a single
inconsistent value silently changes the physics. Treating it as source invites manual
edits that drift from the spec and make results irreproducible. Treating it as a build
output means the spec and the result can never disagree.

**Consequence.** Hand-tuning a generated dictionary is not supported. If a case needs
something the spec cannot express, the spec grows — that pressure is intentional and
keeps the tool honest.

---

## ADR-005 — Static web viewer, no build step

*Amended by ADR-025: the vendored Three.js and the 3-D scene were removed. The
decision below stands; the page is now plain ES modules, SVG and canvas only.*

**Decision.** The viewer is plain ES modules, served as static files. No npm, no
bundler, no framework.

**Why.** The target user is an electrical engineer running `docker compose up` on a
corporate Windows laptop. Every build step is a failure mode they cannot debug, and a
Node toolchain in the runtime image is dead weight next to a 1 GB OpenFOAM install.
The viewer's job — draw boxes, draw a colored slice, draw a line chart — does not need
a framework.

**Consequence.** Vendored dependencies must be updated deliberately, and the viewer
code stays deliberately small. If it ever needs a build step, that is a signal the
viewer is doing too much.

---

## ADR-006 — The AI assistant is a repo skill, not an embedded chatbot

**Decision.** The assistant layer lives in `.claude/skills/` and runs inside Claude
Code. The web UI has no chat box and the runtime needs no API key.

**Why.** Three reasons. It costs the user nothing per use and requires no key
management, which matters for adoption of an open-source tool. It keeps the LLM out
of the runtime dependency graph, so the CLI stays fully usable offline and in CI. And
it puts the assistant where the user already has file access, so it can read a DCIM
spreadsheet or an IFC export directly instead of through an upload endpoint.

**Cost accepted.** Users who do not use Claude Code get the CLI without the natural
language layer. That is an acceptable degradation — the CLI is designed to stand
alone (see the component table in ROADMAP.md).

**Revisit if.** Enough users ask for a browser-native assistant that the tool-calling
layer is worth building twice. The CLI is the natural tool surface for both, so this
is additive, not a rewrite.

---

## ADR-007 — Validation runs on every solve, and can fail a converged run

*Superseded in detail by ADR-018, which replaced these checks with the eleven
the closed-loop model needs. The principle below is why they exist at all.*

**Decision.** `aicfd post` emits a validation verdict independent of the solver's exit
code: mesh quality, mass balance in vs. out, monotonic temperature rise along the
flow path, final residual thresholds.

**Why.** `buoyantSimpleFoam` exiting 0 means the iteration loop finished, not that the
answer is physical. A case with an empty `cellZone` (a common failure — `topoSet` not
run, or a box that misses the mesh) converges beautifully to a room with no heat load
in it. Without an automated check, that result looks fine and is completely wrong.

The intended user is not a CFD engineer and will not catch this by eye. The tool has
to catch it.

---

## ADR-008 — Read fields off the structured grid, not through VTK

**Decision.** Post-processing parses OpenFOAM's ASCII field files directly and
reshapes them into a numpy grid, instead of converting to VTK and reading that.

**Why.** AICFD's primary meshing path is a single `blockMesh` hex block over an
axis-aligned room (ADR-003 keeps racks as porous zones, so there is no complex
geometry to snap to). On such a mesh the cell ordering is fully determined — x
varies fastest, then y, then z — so a field file *is* a 3D array already.

The payoff is out of proportion to the effort: numpy becomes the only runtime
dependency, the Docker image drops the ~200 MB VTK stack, post-processing takes
milliseconds, and the web viewer receives a regular grid it can slice in the
browser rather than a pre-rendered image per view.

**Consequence.** This breaks the moment the mesh is not a single uniform block —
`snappyHexMesh` (M6), graded blocks, or multi-block rooms. When that lands it
needs a real unstructured reader alongside this one, selected by mesh type, not
a rewrite of it. The reader raises rather than guesses if the cell count does
not match the declared divisions, so the failure will be loud.

---

## ADR-009 — Two ramps for temperature, chosen by the question being asked

**Decision.** The viewer offers air temperature on a *diverging* blue↔red ramp
anchored on the middle of the ASHRAE recommended band, and temperature rise
above supply on a *sequential* single-hue ramp. Air speed uses the sequential
ramp.

**Why.** "Is this rack inlet acceptable?" is a polarity question — too cold
wastes chiller energy, too hot risks equipment, on-target is neither — and
polarity is what a diverging ramp with a neutral midpoint encodes. "How much
heat has this air picked up?" is a magnitude question from zero, which is what a
sequential ramp encodes. Using one ramp for both would misstate one of them.

The rainbow/jet colormap that CFD tools default to is rejected: it manufactures
visual boundaries where the data is smooth, and it is unreadable to a
colorblind viewer.

**Consequence.** Captions in the viewer must not name a color. The sequential
ramp inverts between the light and dark surfaces (near-zero always recedes
toward the surface), so "dark means fast" is true in one mode and false in the
other. The colorbar's numeric ticks carry the direction instead.

---

## ADR-010 — Hand-written validation, generated dictionaries, no template engine

**Decision.** The room spec is validated by hand-written checks over plain
dataclasses, not by a schema library. OpenFOAM dictionaries are generated by
Python code, not rendered from a template engine. Runtime dependencies stay at
`numpy` and `pyyaml`.

**Why.** The audience is an engineer who typed a number wrong, and the
difference between a good and a bad tool here is the error message. Compare:

```
racks.0.size: Input should be a valid number [type=float_parsing]
```

against what AICFD says:

```
rack 'A1' is thinner than two cells on x (0.40 m / 0.500 m cells).
Reduce mesh.cell_size to at most 0.200 m, or model the row as one larger block.
```

The second one names the item, the measurement, and the fix. Producing it needs
domain knowledge the validator has and a schema library does not, so the library
would only handle the trivial half and leave the interesting half here anyway.

Templating was rejected for a narrower reason: OpenFOAM dictionaries use both
`{}` and `$`, which collide with Python's format syntax and with Jinja2's and
`string.Template`'s delimiters respectively. Generating from code sidesteps the
escaping entirely and makes a multi-rack `fvOptions` an ordinary loop.

**Cost accepted.** More lines of validation code than a declarative schema, and
those checks need their own tests. That is the right trade for a tool whose
users cannot read a traceback.

---

## ADR-011 — A rack is a resistance, not a fan

**Decision.** `airflow_m3h` on a rack sizes its Darcy-Forchheimer resistance
(via the pressure drop at that flow) but does not force that much air through
it. The air that actually passes through a rack is an outcome of the room's
pressure field.

**Why.** The porous-zone model (ADR-003) has no momentum source, so the only
thing the spec can set is how hard the rack is to push air through. Forcing the
rated flow would need a fan model, which is real work and belongs with the other
rack-level features in M5.

This is not only a simplification — it is also the more honest default for the
question AICFD is asked. "Does this layout deliver enough air to each rack?" is
answered badly by a model that guarantees each rack gets its rated airflow no
matter what the room does. Letting the flow fall out of the solution is what
makes recirculation and starvation visible at all.

**Consequence.** Per-rack airflow is a *result*, not an input, and the docs must
not imply otherwise. The spec validator compensates by comparing total rack
demand against total CRAC supply up front and warning on a shortfall, which
catches the gross case before a 10-minute solve rather than after it.

**Measured cost, and a revision.** The first realistic case built on this model
gave every rack 21% of the air its load needs: 77% of the supply went over the
top of the rack row instead of through it, and the zones reached 46-62 degC.
Those temperatures are an artefact of the missing fan, not a prediction — real
rack fans pull their rated CFM whatever the room does.

So the cost is larger than it looked when this was written. AICFD systematically
over-predicts rack temperatures in any layout where bypass is easier than
passing through, which is most of them. Two responses: `rack_throughflow` now
fails a run where this is happening and says so in those words (ADR-013's
sibling), and rack fans as momentum sources move ahead of the rest of M5.

Note this does not reverse the decision. A model that forces each rack's rated
airflow could not show starvation at all; the fan model has to *add* a driving
pressure to the existing resistance, not replace it.

---

## ADR-012 — The outlet fixes p_rgh, not the static pressure

*Superseded by ADR-017: there is no outlet patch any more -- the air loop closes
inside the box and is cut only at the fan wall. Kept because the reasoning about
`p_rgh` applies to every patch that ever gets added.*

**Decision.** The return patch uses `fixedValue` on `p_rgh`. Never
`prghPressure`.

**Why.** `buoyantSimpleFoam` solves for the modified pressure
`p_rgh = p - rho*(g.x) = p + rho*g*z`. In a still room the static pressure `p`
carries a hydrostatic gradient, which is exactly the term `p_rgh` removes — so
`p_rgh` is the quantity that is uniform, and `fixedValue` says so.

`prghPressure` takes a *static* pressure and converts it, so a uniform `p`
asserts that the static pressure is identical at floor and ceiling. Over a 3 m
room that contradicts the hydrostatic column by `rho*g*H ≈ 35 Pa`, and 35 Pa
across the outlet drives about 7 m/s. The first generated multi-rack case
recirculated at 29.7 m/s in a room whose buoyant ceiling was 1.7 m/s, entirely
because of this.

`prghPressure` is correct where the static pressure at a boundary is genuinely
known — an opening to outdoor air at a stated height. A return grille inside the
room being modelled is not such a boundary.

**Consequence.** Any future patch type (downflow CRAC returns, plenum
openings, leakage paths in M5) inherits this question, and the answer is the
same unless that patch really does connect to a separately-known pressure. Both
the generator and a test carry the reasoning inline, because the wrong choice
produces a case that runs, converges and lies.

---

## ADR-013 — Validate that the flow itself is attainable

**Decision.** `aicfd post` checks the peak air speed against five times the
larger of the supply face velocity and the buoyant velocity scale
`sqrt(2*g*(dT/T0)*H)`, and fails the run if it exceeds it.

**Why.** The validation layer as first written (ADR-007) checks that individual
quantities are self-consistent: mass in versus mass out, temperature rising
along the flow path, residuals small, cell zones populated. Every one of those
passed on a run whose velocity field was seventeen times above anything the
physics could drive, because each quantity was unremarkable *in isolation*.

"Could this flow exist at all?" is a different question from "is this number
consistent with that one", and it needs its own check. It is also the cheapest
possible diagnostic for the largest class of setup error — a wrong boundary
condition, a porosity coefficient off by orders of magnitude, a steady solver
chasing an unsteady flow — all of which show up first as a field that moves too
fast.

**Consequence.** The margin (5x) is deliberately loose. A jet through a narrow
gap between racks legitimately runs several times the mean, so this is sized to
catch an order-of-magnitude artefact, not to police accuracy. When it fires, it
also warns that the temperatures are unreliable, since they come from the same
field — a reader who sees only "velocity check failed" might otherwise keep
trusting the ASHRAE column.

---

## ADR-014 — One geometry module, consumed by both the drawing and the mesh

**Decision.** `aicfd/model.py` turns a spec into the complete derived geometry —
domain, gallery, hall, racks, and every internal wall and opening as a `Panel` —
and applies mesh snapping *before* returning. The web page draws from that
object's JSON and nothing else; the case generator meshes from the same object.

**Why.** The drawings exist so an engineer can catch a wrong dimension before
paying for a solve. A drawing produced from the spec while the mesh is built
from something slightly different would defeat that entirely, and the
divergence would be invisible: both would look right.

Snapping is the specific trap. A 0,61 m grille on a 0,10 m mesh is built at
0,60 m. If the page quotes the nominal 0,61 m and the summary computes a face
velocity from it, the number on screen is not the number being solved. So
`build_model` snaps first and records what it changed, and everything
downstream — drawing, summary table, velocities, warnings — reads the snapped
geometry. The page reports the adjustment rather than hiding it.

**Consequence.** `Panel` has to be expressive enough for the mesh surgery it
drives (`topoSet` + `createPatch` for boundary patches, `createBaffles` for
internal ones), not just for drawing rectangles. That is why a panel carries an
axis, a position and an in-plane extent rather than a polygon: those are
exactly the arguments `boxToFace` and `normalToFace` take.

---

## ADR-015 — The page is the interface before the run, not a report after it

**Decision.** `aicfd view` serves a page that draws the model, takes parameter
edits against a whitelist, starts the solve, and streams convergence — and it
is meant to be opened *before* solving. Results rendering (M1's viewer) is a
second job, not the primary one.

**Why.** A mistake found after a twenty-minute solve has already cost the
twenty minutes, and the mistakes that matter here are geometric: an aisle on
the wrong side, a grille over the racks instead of the hot aisle, a fan wall
opening into the wrong volume. Those are invisible in a YAML file and obvious
in a section drawing. Two sections and a plan, drawn to one shared scale, catch
them in seconds.

The parameter form is deliberately a gate rather than a text editor. `EDITABLE`
in `aicfd/server.py` lists the numbers an engineer iterates on, each with a
range; anything else needs an edit to the YAML. A form that could put the spec
into a state the generator has never seen would trade one silent failure for
another.

**Consequence.** The server is stdlib-only and holds one run at a time, which
is the right size for a tool run on an engineer's own machine. When the case
generator for a geometry is missing, the page says so and disables the button
rather than offering a Run that dies on an import.

---

## ADR-016 — A POD is meshed as one box and then operated on

**Decision.** `aicfd/case.py` builds a fan-wall POD with `blockMesh` →
`topoSet` → `createBaffles -overwrite` → `checkMesh` → `buoyantSimpleFoam`.
Every internal surface — the gallery/hall dividing wall, the false ceiling, the
hot-aisle containment — is a two-sided baffle patch created from a face zone.
The openings are not built; they are holes *left* in those face zones.

**Why.** The M2 generator (ADR-004's `aicfd/case.py`) describes a room whose
supply and return are faces of the domain. A POD's air loop is closed inside
one box, so none of its surfaces are on the boundary and `blockMesh` alone can
express none of them. Multi-block meshing could carve the volumes, but every
opening would become a block boundary and the block count would track the
geometry rather than the physics.

Building the holes as absences is what makes the geometry cheap to change. A
grille is not a thing to mesh; it is three `topoSet` lines removing faces from
the ceiling's face zone. Moving it costs nothing.

Two traps are load-bearing here and both are enforced in code:

* **`boxToFace` selects by face centre, whatever the face's orientation.** A
  thin box around a plane also catches every face running perpendicular
  through it — 2218 faces where 540 were wanted, the first time. Every
  selection is narrowed with `normalToFace`.
* **`createBaffles` without `-overwrite` writes the modified mesh into a new
  time directory** and leaves `constant/polyMesh` untouched. `checkMesh` and
  the solver then both read the mesh that was supposed to be replaced, and
  neither complains: the run succeeds, on a mesh with no internal walls at all.

**Consequence.** The mesh must be orthogonal for this to stay cheap, which
means every plane in the geometry has to land on a cell face — which is what
ADR-014's snapping guarantees. The two decisions only work together.

---

## ADR-017 — The fan wall is where the air loop is cut

**Decision.** The fan wall becomes a pair of patches on the same internal
faces: `fanIntake` on the gallery side, where air leaves the domain, and
`fanSupply` on the hall side, where it re-enters at the supply temperature.
Both sides are set by **mass** flow — `flowRateOutletVelocity` and
`flowRateInletVelocity`, the same `massFlowRate` — and the pressure level,
which no patch now fixes, is pinned with `pRefCell`/`pRefValue`.

**Why.** The POD recirculates: nothing enters or leaves. A genuinely closed
domain has no pressure reference and needs the fan modelled as a momentum
source or a pressure jump, both of which need tuning against a fan curve
nobody has. Cutting the loop at the fan turns the problem back into the one
the solver is good at — an inlet at a known temperature and flow, an outlet at
a known pressure — and does it at the one surface where the physical machine
also adds energy. What is lost is the fan's own curve; what is gained is a
case that sets up from a nameplate airflow.

Mass rather than volume, because the air leaving is warmer and thinner than
the air arriving — about 1% at a 10 K rise — and a closed loop has nowhere to
put the difference. And prescribed rather than pressure-driven, because the
first version used `pressureInletOutletVelocity` on the intake and let **3.9
kg/s blow backwards into the gallery against a net of 1.7**: the intake is a
7,2 m² patch at the end of a 100 m³ gallery, and at a 0,19 m/s mean face
velocity local eddies reverse it freely. No fan does that. Worse, on every
reversed face the `inletOutlet` temperature was pinned to the seeded return
value, so the boundary was *inventing* heat the racks never produced and the
energy balance could never close. A fan moves its duty regardless of what the
gallery is doing, and saying so removes both problems at once.

**Consequence.** Which side of the pair `createBaffles` calls master follows
the mesh's face winding, not anything AICFD writes. Getting it backwards
builds a POD that supplies cold air into its own return, and it would converge
and report plausible numbers. So it is measured rather than assumed:
`aicfd/foam/polymesh.py` reads one face and three points, and
`case.check_fan_orientation` refuses to solve if `fanSupply` is not facing
the data hall.

The intake's temperature is written as `inletOutlet` even though nothing can
enter through it, because `zeroGradient` stores no value on the patch and the
temperature of the air crossing there is exactly what the energy balance is
built from (ADR-018). Its inlet value is the supply temperature — inert, and
if it ever were used it would bring back cold air rather than fabricate heat.

---

## ADR-018 — A steady run is judged by its energy balance, not its residuals

**Decision.** `aicfd/post.py` computes, from the values on the fan intake
patch,

    Q = cp * sum over intake faces of  phi_i * (T_i - T_supply)

and fails the run when that misses the installed IT load by more than 10%.
`convergence()` reports the same number at every written time.

**Why.** Residuals measure how much the last iteration changed the field. They
say nothing about whether the field means anything. The run that prompted this
had residuals falling steadily for 300 iterations and was returning air at
20,5 °C against a design return of 30,8 — the thermal field had simply not
filled the domain yet, and nothing in the solver log said so. Reading the
residual plot, the case looked like it was converging nicely.

Every watt installed has to leave as warmer air. That identity cannot be
satisfied by accident, it needs no reference solution, and a reader who is not
a CFD engineer can check it: *the racks make 18 kW, the air is carrying 17,6,
so I am looking at the answer.* Watched across written times it also separates
the two failure modes — a merely unconverged run climbs towards 100%, a wrong
one settles somewhere else.

**And it is necessary but not sufficient.** That correction cost a run. The
balance works as a verdict from a *cold* start, where it climbs from zero as
the heat works its way round the loop. Seed the field warm — which is the
right thing to do, the mechanical gallery is a third of the domain — and the
balance reads 101% at iteration 100 because the seed put it there, while the
contained hot aisle is still swinging 27,5 → 25,1 → 26,5 °C between samples.

**And stillness is not enough either**, for the opposite reason: it measures
*speed*, not *distance remaining*. A seeded run passed `settled` at 0,103 K
while its return plenum sat 3,2 K below the contained hot aisle feeding it,
closing that gap at 0,1 K per hundred iterations — three thousand iterations
from its answer, and perfectly still by that test.

What catches it is a physical identity rather than a numerical one. Between the
rack outlet and the fan intake nothing adds or removes heat: every wall is
adiabatic and the containment is sealed. So the contained hot aisle, the return
plenum and the back of the fan wall have to read the *same temperature* at
steady state, and any spread between them is air that has not finished
arriving. `return_path` fails above 1,5 K — loose enough for real stratification
in a 1,5 m plenum, tight enough to catch a volume still filling.

So a run is judged on three things, and none of them is a verdict alone:

| check | what it says | how it fails on its own |
|---|---|---|
| `energy_closure` | the field is *consistent* | a warm seed satisfies it from iteration one |
| `settled` | the field is *still* | still is not the same as arrived |
| `return_path` | the field is *arrived* | says nothing about a field that is drifting as a whole |

The pattern is worth naming, because it has now repeated three times: each
check was proposed as sufficient and turned out to be necessary. Convergence in
a recirculating room is not one number.

**Consequence.** Every quantity in this module is read from patch values, never
from the nearest cell centres. Approximating a face flux from cell-centre
velocity is wrong by tens of percent across a porous zone or a grille jet: it
is what first made a perfectly sealed POD look like it was losing 37% of its
air. The cost is that the generator must write a value on every patch the
balance reads, which is why the intake is `inletOutlet` rather than
`zeroGradient`.

---

## ADR-019 — Seed the initial field, then prove the seed did not choose the answer

**Decision.** `0/T` is written non-uniform: supply temperature upstream of the
racks, supply plus the design rise everywhere the air has already passed
through them — the contained hot aisle, the return plenum, and the mechanical
gallery. `solver.warm_start: false` converges the same case from a uniform
field, and `post.compare()` checks the two land in the same place.

**Why seed at all.** A steady solve's initial condition does not appear in its
converged solution; it only decides how much work getting there costs. Leaving
the whole box at supply temperature is therefore a free choice made badly. The
gallery alone is 100 m³ of a 302 m³ domain and sits in the return path, so the
solver spends thousands of iterations carrying heat round the loop before the
energy balance can begin to close. Measured: from a uniform field, at iteration
1100 the contained hot aisle had settled at 31,2 °C while the gallery was still
at the 20,0 °C it started from.

This is ordinary practice, not a shortcut. OpenFOAM ships `setFields` to do
exactly this; commercial codes call it patching a region, hybrid or FMG
initialisation, or mapping from a previous solution. What AICFD does here is
`setFields` written directly, because the regions are already known — they are
the model's own volumes.

**Why it still has to be checked.** The argument above assumes the steady
problem has one solution. A buoyancy-driven room need not: recirculating flows
can admit more than one steady branch, and then the initial condition selects
between them. That is the one thing a seed can legitimately change about an
answer, so it is tested rather than asserted. The two runs must agree on every
instrumented place to within 1 K; if they do not, the seed is not a shortcut,
it is a choice, and the disagreement is the finding.

**Consequence.** A seeded run cannot be judged by its energy balance alone —
the seed satisfies it from iteration one (see ADR-018's second half). The
`settled` check and the cold/warm comparison are what carry the verdict
instead. Seeding also makes `aicfd run` twice as expensive when the comparison
is being done honestly, which is the right price for the claim.

---

## ADR-020 — Datasheet numbers enter as physics, and come back out as checks

**Decision.** The return grilles are cyclic baffle pairs carrying a
`porousBafflePressure` jump on `p_rgh`, with `D = 0`, `length = 1` and `I`
equal to the grille's loss coefficient K referred to the face velocity over the
gross opening. K comes from `grilles.loss_coefficient` when the datasheet gives
a pressure-drop point, and otherwise from `grilles.free_area` through
Idelchik's thin-plate correlation. The fan wall takes `fanwall.curve`, its P–Q
points from the datasheet, in addition to the rated static pressure. Neither
number changes how the solver drives the flow; both become checks on the solved
field and a reading of the machine's margin.

**Why the grille is a jump and not a hole.** Until now the grilles were absences
— faces removed from the false ceiling's zone. A real return grille has a frame
and vanes: a Koolair series 20.2 ceiling return loses 4,1 Pa at 2,1 m/s
effective velocity with a 0,81 ceiling factor, which is K ≈ 2,4 on the gross
face velocity. Idelchik's thin-plate formula at 80% free area gives 0,54, so the
correlation is a fallback that under-reads a vaned grille by 4×; the datasheet
point wins whenever it exists, and the spec says so. At this POD's 1,3 m/s face
velocity the grilles cost about 2,4 Pa — a tenth of the racks, but it is real
resistance the fan has to produce, and the pressure budget could not close
against a datasheet with it missing.

`porousBafflePressure` sits on a cyclic base, so every other field passes
through untouched and only the pressure sees the grille — which is what a
grille does to air. The cyclic pairs are excluded from `sealed_envelope`,
because carrying the return flow is their job.

**Why the fan keeps its fixed mass flow.** Continental Fan's application guide
is explicit that EC fan arrays run closed-loop, either constant airflow or
constant pressure. A unit under constant-airflow control holds its rated flow
whatever the POD's resistance turns out to be — which is exactly what the
supply/intake pair already imposes. The curve therefore does not drive the
solve. It answers two questions the fixed-flow model cannot: how much static
pressure is available at the rated flow (`fan_capacity`), and where the unit
would run if the control were absent — the crossing of its curve with the
POD's own resistance scaled as flow squared (`fan_operating_point`). On the
worked case: 29 Pa needed of 100 available, and uncontrolled the fan would
overshoot to ~6 300 m³/h.

**Consequence.** Both numbers close the loop between datasheet and field.
`grille_resistance` compares the jump the solver delivers against K at the
measured flow; `rack_resistance` already did the same for the racks; and the
sum of the drops has to equal the fan rise the field shows. When a vendor
sheet is in hand, the spec takes it verbatim; when it is not, the fallback is
named as a fallback in the summary table rather than dressed as data.

---

## ADR-021 — Cells are sized per axis

**Decision.** `mesh.cell_size` takes one number or three. The worked case runs
at 0,20 × 0,20 × 0,10 m.

**Why.** A fan-wall POD's geometry is anisotropic in what it asks of the mesh.
In plan, a 0,6 m rack and a 0,6 m grille are resolved by three 0,2 m cells,
and the aisles are metres wide. In height, the false ceiling at 6,5 m and the
rack tops at 2,2 m are planes that must land on cell faces or the surgery
leaks silently (ADR-016), and 6,5 is not a multiple of 0,2. A uniform 0,1 m
mesh honours the planes at 302 400 cells; a uniform 0,2 m mesh runs in six
minutes but builds the ceiling 10 cm low. Per-axis cells give both: 75 600
cells, every plane exact, and the coarse-versus-fine comparison already showed
the answer holds to 0,2 K and 3% across that range.

**Consequence.** Everything that turned a coordinate into a cell index —
snapping, alignment warnings, the face-selection box thickness, the seeded
field, the readers — now asks for the cell along its own axis. The page's
cell-size field accepts "0,2" or "0,2, 0,2, 0,1".

---

## ADR-022 — A data hall is a POD's parts in lists

**Decision.** The geometry model holds lists where a POD had one thing: rows,
cold aisles, hot aisles, fan walls. A spec with `pods:` lays out that many
row–HAC–row pairs across y, a cold aisle between pairs and a perimeter aisle
round the edge, and one fan wall in the dividing wall in front of every cold
aisle. The POD spec builds the same model with lists of length one, and
nothing downstream distinguishes the two.

**Why.** The user's conceptual work is a whole hall of 10 MW, judged by the
inlet of its worst rack. Everything the POD taught — the closed loop, the
sealed rack boxes, the grille jump, the fan pair set by mass flow, the
energy and return-path checks — carries over unchanged, and the only new
physics is that there are more of each. Writing a second generator would
have duplicated ~2 000 lines and, worse, let the two drift.

**Consequences.**

- *Fan walls.* The spec's `airflow_m3h` is per unit, as datasheets speak;
  each unit is its own baffle pair `fan{k}Intake`/`fan{k}Supply` moving its
  own mass flow, and the datasheet pressure is compared against the most
  loaded unit. Units are centred on their aisle as far as the corners allow
  and never overlap; the placement is a small greedy sweep, refused when
  they cannot fit.
- *Rows.* Each row knows which way its racks breathe (`front_sign`), so the
  two rows of a POD face each other across the contained aisle and the rack
  readers take the inlet on the correct side. Every rack is still its own
  porous zone and heat source, so loads can differ per rack later.
- *Zones.* Walls of a kind share one face zone (`rack_top`, `rack_end`,
  `containment_wall`, `containment_door`): 195 panels become 6 wall patches
  plus one pair per fan and per grille strip. The leak check still names the
  kind of surface that leaks.
- *Grilles.* A hall's return grilles are one strip per hot aisle covering its
  ceiling (`grilles.coverage` shortens it), one cyclic pair each.
- *Parallel.* `solver.processors` above one adds `decomposePar` (slabs across
  the longest axis; the packaged scotch is a stub) and runs the solver under
  `mpirun`, logging as `log.buoyantSimpleFoam` so nothing that reads a solver
  log changes. The sampler reconstructs each processor write as it lands,
  so a parallel run is watched the same way as a serial one. Measured on
  the POD: 3,6× on 4 cores.
- *Per-rack inlet.* The rack readers report the face mean and the top layer
  of the inlet face, and the ASHRAE verdict is taken at the top — where
  recirculating or leaking hot air arrives first. The results page paints
  every rack by that number over the plan and lists the warmest.
- *Drawings.* A hall is many times wider than deep, so its plan turns to run
  across the page and each view takes a full row. Aisle tints, captions and
  levels are placed in room coordinates and mapped through the view.

What did not change: the checks. A hall passes or fails on the same eleven
identities as a POD, now summed over units and rows.

---

## ADR-023 — The plant is sized by the design office's rules before any CFD, and the air weighs what it weighs on site

**Decision.** The spec carries the fan wall as the datasheet gives it — units,
airflow per unit, net sensible capacity per unit, electrical input — and the
rack's airflow as the design rule the office uses, 158 CFM/kW. The model
checks installed capacity against IT load and installed airflow against the
racks' demand, reports both, and *alerts* when either falls short. It never
blocks a run. The case also carries the site altitude, and the solver runs at
that pressure.

**Why.** Two of the numbers the previous hall ran on were invented to make the
loop close: 160 000 m³/h a unit and an 11 K rack rise. A real selection
(Vertiv CA40NPVG6 for QR2) says 76 650 m³/h, 290,9 kW, 22,1 °C supply, and it
was selected at 1 880 m — where a cubic metre of air is 24% lighter than at
the coast, which the sheet states as 56 475 m³/h of standard air. Sizing a
fan wall count by aisles (17) instead of by capacity (35) was the single
largest error in the concept, and it is caught by arithmetic, not by CFD. The
rule-of-thumb checks belong in front of the solve where they cost nothing.

**Consequences.**

- `fanwall.count` decouples the number of units from the number of cold
  aisles; units are spread evenly along the gallery wall, snapped to the mesh
  and never overlapping. Left out, the count is one per cold aisle as before.
- `fanwall.capacity_kw` and `fanwall.power_kw` are shown, summed and compared
  on the model page, in the CLI summary and in the run report. A shortfall is
  an alert in a card of its own; a conceptual study wants to see what an
  undersized plant does, so the button stays live.
- `racks.airflow_cfm_per_kw` replaces the fixed 11 K rise in sizing the
  racks' resistance. At sea level 158 CFM/kW *is* 11 K; at 1 880 m it is 14 K,
  and the return-air temperature the model predicts now matches the
  datasheet's 37 °C selection rather than the coast's 31.
- `site.altitude_m` sets the operating pressure of every field, boundary
  value and reference (standard atmosphere). Densities, mass flows and the
  rack and grille coefficients all follow from it; nothing is hard-coded at
  101 325 Pa any more.
- The alerts are not validation checks. The eleven checks say whether the
  *field* is to be believed; the alerts say whether the *plant* is big
  enough. A run can pass all eleven with an undersized plant — it will simply
  show a hot return — and that is the correct behaviour.

---

## ADR-024 — Field maps are contour legends: fixed band for temperature, round bands everywhere, thresholds drawn on the bar

**Decision.** The field maps are presented the way a post-processor an
engineer already trusts presents them:

- **Discrete contour bands, not a smooth wash.** Temperature runs in 2,5 K
  bands, and speed and pressure in a round step chosen near sixteen bands
  (1 m/s and 10 Pa on the worked hall). A colour can be read back as a number
  off the legend without hunting for a cursor.
- **A fixed band for temperature**, 10 to 40 °C, whatever the run. Air outside
  it saturates at the end of the ramp; the legend marks the saturating end and
  states what the field actually spans.
- **The judgement thresholds are drawn on the bar**: ASHRAE's recommended
  18 and 27 °C and allowable A1 32 °C for the air a rack breathes in.
- **Round edges.** Band edges are 1, 2, 2,5 or 5 times a power of ten, never
  the 9,74 that fitting to a field's extremes produces.

**Why.** A scale fitted to each field answers a different question every time
it is drawn. In the 10 MW hall one rack starved at the mouth of an aisle
reached 55 °C, and colouring against that left a 22 °C cold aisle and a 36 °C
hot aisle both washed out to nearly the same pale tone: the reader could not
see the 14 K the whole design is about. Fixed limits also make two runs
comparable, which is what a conceptual study is for.

The bands matter for a second reason. A continuous ramp cannot be read
quantitatively: nobody tells 31 °C from 34 °C by matching two shades of red
against a gradient. Bands turn the picture back into numbers, and putting the
standard's own limits on the bar puts the judgement where the reading
happens.

**Consequence.** `Scale` gained an optional `step`: with one, a value takes
its band's colour, and `bands()` gives the legend its blocks. `niceStep`
picks the round width. The midpoint of the temperature ramp now means nothing
in particular, which is how a thermometer legend should read -- the earlier
version anchored the neutral on the middle of the ASHRAE band and then had to
explain what that meant, which is a legend explaining itself rather than the
data.

The per-rack inlet map keeps fitted limits: its job is to rank 768 racks whose
inlets differ by less than a kelvin, and the fixed 30 K band would paint all
of them one colour. It is banded on a round step all the same (0,1 K on the
worked hall), so it reads as numbers too.

---

## ADR-025 — One method, kept auditable: the room generator, the 3-D scene and their data are deleted

**Decision.** The repository carries exactly one way to model a data hall. The
M2 room generator (`room:` + `cracs:`, supply and return as faces of the
domain) is deleted, with its spec validator, its post-processor, its case
reader, its tests, its worked case and its exported results. The 3-D scene and
its vendored Three.js are deleted. The remaining modules are named for what
they do: `aicfd/case.py` generates, `aicfd/post.py` analyses. `aicfd verify`
is added as the single command that proves a checkout works.

**Why.** Two generators meant two answers to every question -- which spec shape
is current, which post-processor a result came from, which of two colourbars to
trust -- and the second answer was always the obsolete one. That is a cost paid
by every new reader: another engineer opening the repo, or an AI agent in a
fresh sandbox with no memory of which one won. The M2 shape cannot express what
a real hall does (an air loop that closes inside the box, ADR-016), so it was
never going to come back.

The same applies to the 3-D scene. It showed one slice of the same field the
plan and sections now show in contour bands with the ASHRAE limits on the
legend (ADR-024), it could not be read quantitatively, and it carried 752 kB of
vendored library. A second, weaker view of the same data is not an option for
the reader; it is a question they have to answer before they can start.

**Consequence.**

- ~2 000 lines of Python, 434 lines of JavaScript and 752 kB of vendored
  library are gone; the test suite went from 219 to 186 tests, all of which now
  test code that is used.
- `aicfd/post.py` absorbed the ASHRAE envelopes, the `Check` record and the
  verdict helper, which were the only things the deleted post-processor still
  provided.
- The CLI has one path: every spec is a POD or a hall, both built by the same
  model. `is_pod_spec` and the branching it fed are gone.
- `aicfd verify` runs the unit tests, the install check, a full mesh of every
  worked case, and optionally a short solve held to the eleven checks. It is
  what a new sandbox runs first and what a reviewer runs to disbelieve a
  result.
- Deleted *decisions* are not deleted. Superseded ADRs stay in this file with a
  status line, because the reasoning is the asset -- it is why the current
  shape is what it is, and it is what stops the next contributor from
  re-deriving a dead end.

---

## ADR-026 — The software is in English, end to end

**Decision.** Every string the software produces or shows is English: the
page, the case specs and their comments, the reports, the CLI, the code, the
docs and the commit messages. Numbers are written the English way
(`1,234.56`). Conversation with the user stays in whatever language they
write in; that is not part of the software.

**Why.** The page had grown into two languages at once -- Portuguese chrome
around English check details that come straight from the analysis and are also
what `report.md` carries. Half-translating is worse than either choice: a
reader has to hold two vocabularies for one screen, and "Corredor frio" on the
page against `cold_aisle` in the export is a translation step in the reader's
head every time.

English is the choice because everything the tool is made of is already in it:
OpenFOAM's own vocabulary, ASHRAE's, the datasheets, the code, and the next
engineer or agent who picks the repository up. A Brazilian data-centre
engineer reads these terms in English daily; a tool that renames them buys
nothing and costs the mapping.

**Consequence.** `aicfd.model.num` formats one way, and the page's
`toLocaleString` calls use `en-US`. The tests that asserted on Portuguese
strings assert on English ones. Anyone who wants a localised interface later
adds a translation layer over a single vocabulary rather than untangling two.

---

## ADR-027 — A gallery at each end, rows in blocks, and one plenum

**Decision.** The hall layout takes two new inputs, both optional and both
defaulting to what the tool did before:

- `gallery.sides: 2` puts a mechanical gallery at **each end** of the hall,
  each with its own dividing wall and its own share of `fanwall.count`, facing
  the opposite ends of the same cold aisles;
- `racks.blocks: n` cuts every rack row into `n` blocks along its length,
  separated by `aisles.transverse`, each block a contained volume of its own.

The **return plenum stays single**. The false ceiling covers the hall and stops
at each dividing wall, so the volume above it is one continuous space that
collects from every hot aisle and opens into every gallery. Two supplies, one
return.

**Why.** A 50 m hall driven from one end asks one gallery to push air the whole
length, and the measured consequence in `docs/experiments/2026-09-18-hall-10mw.md`
was already visible at 88 m: the plenum was the bottleneck, taking 23 to 30 Pa
of the unit's budget. Real halls of this size are built with a gallery at each
end and the rows cut in two, so each block is fed from the end nearest to it and
no unit reaches further than half the room.

Keeping the return shared is not a simplification, it is the arrangement: a
single plenum lets a block whose own gallery is short of capacity be drawn on by
the other one, which is exactly the redundancy the layout is bought for. Two
separate plenums would be two rooms in one box.

**What it changes in the mesh.** One thing, and it is the thing that would have
been silently wrong. `createBaffles` gives the *master* patch of a pair to the
face's owner cell, and for an x-normal face of a blockMesh box the owner is the
cell at lower x. For the near gallery that cell is in the gallery, so the master
is the intake. For the far gallery the owner is a hall cell, so master and slave
swap. Naming the two halves without swapping their boundary conditions builds a
unit that supplies into its own return — and it converges, and every residual
falls, and the answer is about nothing. So each fan wall carries a `sign` (+1
when its gallery is at lower x), the generator emits the halves in that order,
and `check_fan_orientation` measures the normal of both patches of every pair
against it after the mesh is built (ADR-013's rule: orientation is measured, not
assumed).

**Alternatives rejected.**

- *A second domain box for the far gallery, coupled by a mapped boundary.* Two
  solutions to reconcile, and the shared plenum — the whole point — would become
  a boundary condition instead of a volume.
- *A separate plenum per gallery.* Simpler to mesh, and wrong: it removes the
  cross-feed that the arrangement exists to provide.
- *Blocks as separate rows in the spec.* A row cut in two is still one row of
  the hall; making the user list it twice would let the two halves disagree.

**Consequence.** `Model.gallery` became `Model.galleries` (a list; the POD keeps
`gallery` as the first of them), and `Model.blocks` holds each block's x span,
so the chimney area, the warm start, the drawing's section plane and the grille
strips all count blocks rather than assuming one run of racks. The worked case
is `cases/hall-double-gallery.yaml`, written against a real 5 MW hall of this
shape (Ascenty VIN03) so the tool's answer can be held against an independent
study of the same room.

---

## ADR-028 — The deliverable is a Word document, built from the export

**Decision.** `aicfd report <name>` writes a Word (.docx) technical report for
an exported result: cover, contents, summary with the basis of design and the
headline numbers, methodology with the geometry, the mesh and the boundary
conditions, results with the checks, the convergence, the field maps, the
per-rack table and the per-unit table, then conclusions and limitations.

It reads `results/<name>/viewer.json` and `fields.bin` and nothing else — the
same two files the page reads — so a figure in the document and a view on the
screen are two renderings of one export. Figures are rendered with matplotlib;
the document is assembled with python-docx. Both are listed in
`requirements.txt` under a comment saying they are for the report alone, both
are imported late, and the model, the case generator, the solve and the eleven
checks import neither.

**Why a document at all.** A study is finished when someone who was not in the
room can read it, disagree with it and check it. `report.md` and the page are
for the person driving the tool; the document is what gets attached to an
email, marked up by a reviewer, and filed against a design decision. Every
consultancy study of this kind arrives as one, and a tool whose output cannot
be circulated that way is a tool whose results stay with whoever ran it.

**Why not HTML, or the page printed.** The page is interactive — sliders,
hover readouts, a live run — and printing it produces a worse version of
itself. And a reviewer marks up a document, not a web page.

**Two rules the writer holds to, and they are what make it worth trusting.**

1. *Every number comes from the export.* Nothing in `aicfd/report.py` computes
   physics. Where a quantity is not in the export, the document says the tool
   does not produce it — as it does for the coil capacity available at the
   return temperature the room actually reaches, which is exactly the number a
   reader coming from a consultancy report will look for first.
2. *The limits sit with the result, not in a footnote.* Section 5 states, in
   the document itself, that this is one scenario, one load per rack, the
   catalogue capacity rather than the available one, perfect containment and a
   conceptual-design mesh carrying 1 to 2 K on any single rack. A coarse model
   that ranks racks correctly is a useful instrument or a misleading one
   depending entirely on whether the reader was told which it is.

**Two colour rules are carried over from the page rather than re-invented.**
`aicfd/palette.py` is a port of `web/colormaps.js`, and `tests/test_palette.py`
runs the JavaScript under node and compares every step of both ramps, so the
two cannot drift. The field maps use the fixed 10–40 °C band with the ASHRAE
limits on the bar (ADR-024); the per-rack map uses a *fitted* scale and says so
in its caption, because on the fixed band every rack in a healthy hall falls
inside one 2,5 K step — true, and useless for ranking them.

**Consequence.** `aicfd post` now points at `aicfd report`. `fan_flows` gained
the per-unit return temperature and heat removed, which the document's unit
table needs and the page can now show too — an addition the reference study in
`docs/reference-report-parameters.md` made obviously necessary: a hall uniform
in the mean can still have one unit at the end of a row moving half again the
flow of its neighbours, and only the per-unit table shows it.

---

## ADR-029 — The image's build is its test, not its install

**Decision.** The Docker build runs, after installing, four things inside the
image it has just made: `aicfd doctor`, `aicfd build` on a worked spec,
`aicfd report` on a worked result, and the full unit-test suite. Any of them
failing fails the build. `results/` is copied into the image, so the page has
something to show the moment a container starts.

**Why.** The image installed `python3-numpy` and not `python3-yaml`. The build
succeeded — its only check was `aicfd doctor`, which reads no spec and imports
no YAML — and the first real command a user typed died on
`ModuleNotFoundError: No module named 'yaml'`. The image was green and the tool
was unusable, which is the worst arrangement available: it moves the failure
from the person who can fix it to the person who cannot.

An import check would have caught that one and would not catch the next. What
the build now runs is four *paths*, end to end: a spec read and an OpenFOAM
case written, a document assembled with every figure rendered, and the tests.
It costs about a minute of build time.

**On the versions the image actually has.** Ubuntu 24.04 ships matplotlib 3.6.3
and python-docx 1.1.0; development was on 3.11 and 1.2. Running the report
inside the build is what says those versions work, rather than a floor in
`requirements.txt` asserting it. The floors there are now the distribution's,
because that is what is exercised.

**Why this matters more than it looks.** The tool's claim is that a result can
be reproduced and disbelieved by someone else (ADR-004, ADR-025). Someone else
starts by cloning and building. A build that succeeds and then does not run
breaks that claim before any physics is involved, and it breaks it for
precisely the reader the project is for — an engineer who will reasonably
conclude the tool is broken rather than that one apt package is missing.

**Consequence.** On macOS, where OpenFOAM v1912 has no native build, Docker is
the only path and the README says so first rather than last. `openfoam` v1912
is published for arm64, so Apple Silicon runs the container natively. What does
*not* need OpenFOAM — the unit tests, the page over the tracked results, and the
Word report — is listed separately, because it is what a reader can run in the
first minute after a clone.

---

## ADR-030 — A result belongs to the inputs that produced it, and says so

**Decision.** Three things, one rule:

1. **The solve on the page exports.** `aicfd view`'s Run button used to mesh,
   solve and stop. Nothing wrote `results/<name>/`, so the page's own "See
   results" opened whatever was there from before — for ever, until someone
   ran `aicfd post` from a terminal. The worker now exports when the solve
   ends and reports the checks in its status line.
2. **The export carries its spec.** `to_dict(model, {})` threw the inputs away.
   It now carries them, so anything downstream can ask whether an export still
   belongs to the spec in front of the reader.
3. **Both pages say what they are showing.** The model page's link is hidden
   when nothing has been exported, disabled while a run is in flight, and reads
   *See previous result* — naming the fields that changed — when the export
   came from different inputs. The results page asks the server whether a run
   is in flight for its case and, if so, carries a badge saying what is on
   screen is the previous result.

**Why.** Found by a user on their first real run: they changed the rack load,
pressed Run, and clicked "See results" while it solved. They got the previous
run's numbers, under the current case's name, with nothing to say so. Their
own words — *"não acho correto"*.

They were right, and the defect was bigger than the button. Because the page
never exported at all, that stale result was not a window of a few minutes; it
was permanent. Every number on it — the warmest rack, the plant utilisation,
the eleven checks — belonged to a different design, and the page presented it
as this one's.

This is the failure the whole tool is built against. The checks exist so a
result cannot be quoted unless the physics closes (ADR-018); the generated case
is never hand-edited so the mesh matches the spec (ADR-004); the model snaps to
the mesh before anyone reads it so the drawing and the solve describe the same
room. All of that is worth nothing if the *result on screen* can silently
belong to another set of inputs.

**Why not simply disable the link.** Looking at the previous run on purpose is
legitimate — it is how you see what a change did. What is not legitimate is
doing it without knowing. So the link stays, and it tells the truth: which
state it is in, and which fields differ.

**Why the results page asks the server.** That page is static by design and has
to work from a plain file server, from disk, or from a copied `results/`
directory. It asks `/api/progress` and ignores every failure: where a server
exists the hole is closed, and where one does not there is no run to be
overtaken by.

**Consequence.** `post.export` takes the spec; `aicfd post` and the page both
pass it. A run that ends before its first write — an iteration cap below
`solver.sensor_interval`, or `residualControl` converging first — used to fail
with `FileNotFoundError: .../0/phi`, because `0/` holds the initial conditions
and has no flux field. It now says what happened and what to change. The three
worked results were re-exported so they carry their specs.

---

## ADR-031 — Stop is a word in a file, not a signal

**Decision.** `aicfd stop <name>`, and a Stop button on the page while the
solver is iterating, write `stopAt writeNow;` into the running case's
`system/controlDict`. The solver finishes the iteration it is on, writes the
field and exits 0. The run then reconstructs, exports and is judged by the same
eleven checks it would have faced at its iteration cap.

**Why not kill the process.** A killed solver leaves a half-written time
directory and no export: nothing to look at, and the next thing anyone does is
delete it. The point of stopping is to *keep* what has been computed.

**Why this works at all.** The generator has always written
`runTimeModifiable true`, so OpenFOAM re-reads its controlDict every iteration.
The mechanism was there from the start; what was missing was a way to ask. This
is also exactly what every commercial tool's Stop button does — finish the
iteration, keep the solution, let the user look at it.

**The result of a stopped run is a real result, and says how far it got.** A run
cut short before it settled fails `settled`, and may fail `energy_closure` and
`return_path` too. That is not a fault to be worked around; it is the checks
doing their job (ADR-018), and the page says so in those words rather than
reporting a bare failure. A run stopped *after* it settled passes all eleven —
which is what happened the first time this was used: the worked POD was stopped
at iteration 1 329 of 2 000 and every check passed, so `results/pod-fanwall` is
now that run.

**Atomically, because the solver is reading that file.** The new text is written
to a temporary in the same directory and `os.replace`d over the original, so a
reader sees either the whole old file or the whole new one. A plain in-place
rewrite can be read half-done, and the failure mode is a solver crash on a
malformed dictionary — at exactly the moment the user asked for a clean stop.

**Nothing is left behind.** `aicfd run` regenerates `system/controlDict`, so the
flag cannot survive into the next run. This is asserted in `tests/test_stop.py`,
because it is what makes it safe to leave the file as it is afterwards.

**Not done here: resuming.** OpenFOAM restarts with `startFrom latestTime`, so
"pause" is this plus that. It touches the generator rather than a running
case's dictionary, and it is left for its own change.

---

## ADR-032 — Evidence in `reference/`, artifacts in `results/`

**Decision.** The three worked results are tracked under `reference/`. `results/`
joins `runs/` in `.gitignore`. The page, the Word report and the server all
resolve a case to `results/<name>/` first and `reference/<name>/` second, and
nothing ever writes into `reference/`.

**Why.** A user cloned the repository, ran `aicfd report pod-fanwall` — the
command the README tells them to run — and their next `git pull` aborted:

```
error: Your local changes to the following files would be overwritten by merge:
        results/pod-fanwall/figures/ashrae.png
```

They had done nothing wrong. Using the tool as documented put the repository
into a state where it could not be updated.

The cause was an inconsistency the project had been carrying in writing.
`.gitignore` said, of `runs/`: *build artifacts, regenerated by `aicfd run`
(ADR-004)*. `results/` is equally an artifact — and it was tracked, at the same
path the tool writes to. So the first thing a sceptical reader does, re-run a
worked case to see whether the numbers hold, was also the thing that broke
their clone.

**Why not simply untrack them.** Because they are not only artifacts. The
README quotes them, the ADRs cite them, `tests/test_report.py` builds a
document from one, and they are why a fresh container has something to show
before anything is solved. They are *evidence*. The mistake was not tracking
them; it was tracking them where the tool writes.

**Shadowing, rather than a second name.** Keeping the same case name in both
places and preferring yours means nothing has to be renamed, explained or
chosen: a fresh clone shows the worked result, you re-run the case, and from
then on the page shows yours. `results/pod-fanwall` and `reference/pod-fanwall`
are the same study, one of them checked in.

**Consequence.** `aicfd report` writes its figures beside the document rather
than beside the export it read — otherwise producing a report would modify a
tracked file, which is the same bug one layer down. The Dockerfile copies
`reference/` and drops any `results/` its build produced. The page says
*shipped with the repository* on the results it did not solve.

---

## ADR-033 — The page makes the expensive input legible

**Decision.** Three things on the page, each aimed at a number a reader would
otherwise have to guess at.

1. **The drawings are as large as the column allows.** They shared a scale — a
   metre is the same length in all three, which is what lets a reader compare
   across them — but their heights were fixed at 320 and 420 px, so the most
   constrained view set the size of all of them. A 51 × 32 m hall had to fit
   its plan into 320 px, so everything was drawn at 10 px/m and the sections
   came out 74 px tall. Each drawing now gets a full-width row, and the height
   cap comes from the window rather than a constant: 80% of it, which is what
   the scroller allows, so a drawing labelled `fit` always fits. A constant
   served two rooms badly at once — it held a 51 × 32 m hall's plan to 19 px
   per metre where the page had room for 20,5, and bound a 9 × 4,2 × 8 m POD
   far harder, whose sections are nearly square and whose height the window
   had to spare. Raising the constant far enough for the POD would have made
   `fit` a drawing that still needed scrolling.

   **A drawing narrower than the row is centred under the others.** The card
   centres the scroller, and the scroller is full width, so a transverse
   section — narrow because the room is — sat 450 px off from the plan and
   the longitudinal section it exists to be compared against. Magnified, the
   auto margins resolve to zero and it scrolls from the left as before.
2. **The cell size offers presets, and says what they cost.** It is the one
   input that decides the price of a run, it is three numbers, and the
   consequence — a cell count — is not something anyone computes in their head.
   A select offers the three the worked cases use, named by what they resolve;
   the box still takes anything typed; and a line underneath turns whichever it
   is into `85 × 108 × 30 = 275,400 cells · roughly 7 min on 4 cores`.
3. **A run that was killed rather than stopped is detectable.** Ctrl+C on the
   container leaves solved time directories and no export. Everything about the
   export left behind is still right, *including its spec*, so the comparison
   in ADR-030 cannot see it. Comparing file times can: a solved time directory
   newer than the export means a solve happened that nobody read, and the page
   says so and names the command.

**On the cost estimate.** It is anchored on one measurement — 275 400 cells
settled in about seven minutes on four cores — and scaled with an exponent
above one, because a finer mesh also needs more iterations. It is deliberately
vague in its wording (*a couple of minutes*, *roughly 40 min*, *hours*): it is
an order of magnitude, and a reader who is told a precise number will believe
it. The honest version of this input is one that makes the difference between
minutes and hours obvious before the button is pressed, not one that predicts
the clock.

---

## ADR-034 — The image is the dependencies; the clone is the application

**Decision.** `docker compose` mounts `aicfd/`, `web/`, `tests/`, `cases/`,
`runs/`, `results/`, `equipment/`, `reports/` and (read-only) `reference/` from
the working copy. The image carries OpenFOAM, Python and the libraries. `git
pull && docker compose up` therefore runs the code that was just pulled, with
no rebuild.

**Why.** A user pulled a fix to the results page, ran `docker compose up`, and
saw the old page. Nothing was wrong with the fix, the pull or the command —
`web/` was baked into the image, and compose reuses an existing image unless
told to rebuild. They reported the fix as not working, which is exactly how
this failure presents: **the symptom is indistinguishable from the change not
having been made.** That is worse than an error, because it sends the person
looking in the wrong place, and it sends whoever wrote the fix looking there
too.

The remedy is not to document `--build`. A ten-minute rebuild to see a
one-line CSS change is not a workflow anyone will follow, and a workflow nobody
follows is not a remedy.

**It also makes a stated principle true.** ROADMAP's sixth design principle is
*"No build step. The page is static files; the tests need no OpenFOAM."* That
was true of the repository and false of the only way most people will run it.
It is now true of both.

**What it costs.** Adding a Python dependency needs a rebuild, and until then
the container fails with `ModuleNotFoundError`. That is a loud, specific,
correctly-pointed failure — the opposite of the silent one it replaces — and
the README says which changes need a rebuild.

**Everything a page writes is mounted.** The rule is not "mount the source":
it is that no directory the software writes to may exist only inside the
container. The equipment page saves a unit, the components page a free area,
the results page a .docx, so `equipment/`, `components/` and `reports/` are
mounted alongside `runs/` and `results/`. Miss one and the write succeeds, the
page confirms it, and the file is gone at the next `docker compose down` — the
same silent class of failure this ADR exists to remove, arriving a day later
instead of immediately.

The test that guards it lists the directories by name, so a library added
later is covered only once its name is added too. `components/` went a commit
without a mount for exactly that reason. A second test now compares the list
against the libraries the code actually defines, so the next one is found
rather than waited for.

**The browser is the last copy that can go stale.** Moving the drawings'
layout into `drawing.css` broke the results page for anyone whose browser
still held the previous copy of that file: new HTML, old stylesheet, and the
rules that had moved between them belonged to neither. The page came apart in
a way that reads as the change being wrong rather than absent — this ADR's own
failure, one layer further out, and the pull was not even at fault. The
viewer now sends `Cache-Control: no-cache` on everything it serves that does
not already say otherwise, so a reload revalidates. It is revalidation, not
re-download: the conditional request still answers 304 from `Last-Modified`.

**Why `reference/` is mounted read-only.** So that "the tool never writes to
the reference results" (ADR-032) is enforced by the filesystem rather than
promised by the code.

---

## ADR-035 — Zoom, because fitting is not seeing

**Decision.** Each drawing carries its own magnification: `−` and `+` step it
through 1×, 1,5×, 2×, 3×, 4×, 6×, 8×, the drawing overflows into a scroller,
and the label says `fit` or `4×` so a magnified drawing never passes for the
fitted one. At `fit` the three still share a scale, so a metre is the same
length in all of them.

**Why layout could not fix this.** The drawings were made full width and given a
generous height cap (ADR-033), and the sections were still too small to analyse.
They always will be: a data hall in section is 51 m long and 7,5 m tall. Fitted
to any page at any width, that is a strip about 160 px high. The aspect ratio is
the aspect ratio, and no column, cap or grid changes it.

So the question is not how to fit more in. It is that **fitting and examining
are different jobs**, and a page that only fits can only ever answer the first.
Fitted, you see the arrangement — five chimneys, two galleries, one plenum.
Magnified, you see the thing you came for: the gradient across a single rack,
the air between two rows, where a plume actually reaches. Every post-processor
an engineer already uses works this way, and it is why they all scroll.

**Per drawing, not per page.** The reader magnifies the section they are
studying and leaves the plan fitted beside it. A single page-wide zoom would
make that impossible, and the shared scale — worth keeping, since it is what
lets a length be compared between drawings — survives at `fit`, where the
comparison is actually made.

**On both pages, from one place.** The model page was left out of this and of
the full-width row in ADR-033, so a 51 m hall was checked before the solve at
10 px per metre and read after it at 19 — the same lines, half the size, on
the page where a wrong dimension is still cheap to fix. Where a fan wall edge
lands on the mesh grid is exactly the close look this control exists for, and
it is worth more before the run than after. The layout, the head, the zoom and
the scroller now live in `drawing.css`, which both pages already load for the
drawings themselves: that file exists so the two cannot drift, and two copies
of these rules is how they would.

**The zoom holds the centre.** Magnifying to a corner loses whatever the reader
was looking at, which makes the control useless for following something across
scales.

**Two things it broke, and they were both real.** A grid item's default
`min-width: auto` let a magnified drawing widen its card instead of scrolling
inside it, so the whole page scrolled sideways. And rack labels were drawn
whenever the rack cleared 26 px, which at 3× is true of a 0,6 m rack whose own
id needs 50 — so the ids ran into each other along the row. The threshold is now
the width of the text itself.

---

## ADR-036 — A unit is a table, not a rating

**Decision.** `equipment/<model>.yaml` describes a fan wall by several
manufacturer selections of the same machine at the same conditions, differing
only in the return air temperature. `fanwall.model: CA80NPVG6` in a case spec
is then enough to describe the machine — size, airflow, capacity, power and
supply temperature all come from the table, at `fanwall.design_return_c`.
After a solve, **every unit is judged against the capacity its coil has at the
return air it actually receives**, and the report says that number alongside
the catalogue one rather than instead of it. `web/equipment.html`, reached
from the fan wall section of the model page, draws the curve and lets the
table be edited.

**Why.** A datasheet prints one capacity. It is true at one return air
temperature — the one the unit was selected for — and a real room almost never
returns air at it. On the unit this was built from, the Vertiv CA80NPVG6:

| return air | net sensible |
|---|---|
| 35 °C | 504,9 kW |
| 38 °C | 622,7 kW |
| 41 °C | 734,3 kW |

**45 % more capacity for 6 K of warmer air**, same machine, same water, same
static pressure, same fan speed. A study that compares a hall's heat against a
single figure is not comparing it against anything the plant will do, and the
error is not conservative: quote the warm selection and an undersized plant
looks fine.

This is the gap `docs/reference-report-parameters.md` put at the top of the
list, and the independent study of VIN03 is what it looks like when it bites —
14 units whose catalogue sum was 6 056 kW against a 5 100 kW load, apparently
19 % of margin, were at 95,6 % of the capacity available at the temperature the
room really produced, and past 100 % with two units out.

**Interpolated between the rows, refused outside them.** A coil curve is not a
straight line — the slope of this one falls from 40,1 to 36,7 kW/K across the
table — and the ends are exactly where extrapolating is worst. Outside the
selections AICFD says so and names the fix: ask for a selection at that
condition. A number that would decide whether a plant has reserve is not worth
guessing.

**The library is a default, never a lock.** Anything written in the spec wins,
and stays visible there, which is where a reader looks for what was assumed. A
study of the same unit at a different fan speed or external static pressure is
legitimate; silently inheriting a table that no longer applies is not.

**Editing keeps the file's provenance.** Saving from the page swaps the rows
textually instead of re-dumping the YAML, because the comments are what say
which selections these numbers came from, who issued them and on what date.
A table of numbers nobody can trace is worth less than no table.

**What it immediately found.** The CA80NPVG6's selections start at 35 °C. A
hall designed at the office's 158 CFM/kW returns air about 12 K above supply,
which lands near 34 °C — *below the table*. The design rule and the unit's
characterisation are inconsistent by about 8 %, and the tool will refuse to
extrapolate into the gap rather than paper over it. The fix is two more
selections, at 33 and 34 °C.

## ADR-037 — A unit's identity is editable; a new name is a new unit

*2026-09-19*

The equipment page started out able to edit one thing: the capacity table.
Everything else about a unit — who makes it, what it is called, how big it is,
how many fans it has, what conditions it was selected at, what its P–Q curve
is — could only be changed by opening the YAML file. That was the wrong line to
draw. The same coil is routinely sold under two names, and a study that adopts
a manufacturer's selections while calling the machine by a different part
number is an ordinary, honest thing to want. All of it is editable now.

**The model name is the exception, and it is not editable — it is
duplicable.** A case file names its unit by model. Renaming a unit in place
would quietly break every spec pointing at the old name, and the breakage would
surface as a missing-file error in some later run rather than at the moment the
decision was made. So the page offers *Save as new unit*: the file is copied
under the new name, the edits land on the copy, and the original is left exactly
as it was. Both units then exist, which is what is actually wanted — one for the
studies already run, one for the new work.

**The copy says where it came from, in a comment, not a field.** A table whose
numbers were selected for another machine has to admit that somewhere. A field
would invite the software to reason about it; a comment puts it in front of the
person who opens the file, which is who the warning is for.

**Writing is surgical, down to the comment column.** Every field is swapped
into the file textually — indentation and trailing comment preserved, the
comment left at the column it was hand-aligned to — so reading a unit and
saving it straight back leaves the file byte for byte as it was. This is
tested. It matters because the alternative is a page that silently reformats a
provenance-carrying file every time someone opens it and presses Save, and a
diff nobody can read is a diff nobody checks.

**An emptied number means absent, not zero.** A weight nobody filled in stays
absent rather than becoming 0 kg.

## ADR-038 — The report names the machine; the results page keeps the commands

*2026-09-19*

Two changes that belong together: the equipment library now decides what the
document says, and the two commands a reader actually needs after a run are
buttons rather than things to type.

**The report is about a named machine.** The cover says which unit the plant
is made of. Section 1 names it and gives the chilled-water and air conditions
its selections were taken at. Section 2 gains a subsection for the unit itself
— its dimensions, its fans, the conditions, the whole selection table as
issued, and a figure of capacity against return air with this hall's own units
marked on it. Section 3.5 gains two columns beside "Of the rating": what the
coil actually has at the return air it received, and what fraction of *that*
each unit is using. Section 4 states the plant's utilisation of available
capacity rather than of the catalogue sum, and says plainly that the second
number is the one that says what happens when a unit is lost.

**Where there is a table, the catalogue limitation is false and comes out.**
Section 5 used to say every capacity comparison was against the catalogue
figure and therefore optimistic. Once the report has read the real capacity
that is no longer true, so the bullet is replaced by the two limitations that
are: the table is valid only between its ends, and it holds only at the
conditions it was selected at. Where no unit is named, the old bullet stays
exactly as it was.

**Refusing is reported, not omitted.** This hall returns air at 33,3 °C and the
CA80NPVG6 was characterised from 35 °C up. AICFD will not extrapolate a coil
curve, so it cannot state the plant's margin — and the report says which units,
over what range, and that the fix is a manufacturer selection at that
condition. The capacity figure shades the region the selections do not cover
and draws every unit's return inside it. A missing number with its reason
attached is a finding; a missing number on its own reads as an oversight.

**Two buttons, and only two.** *Word report* asks for client, author and title,
remembers them in the browser, and downloads the document. *Re-read this run*
re-runs the post-processing without re-solving, because the analysis changes
more often than the fields do and a result on disk can be carrying an answer
computed by older code. Both appear only when the page is served by `aicfd
view`, and only for what that case can do — the results page is a static page
over two files and has to stay one, so an export copied onto a laptop with no
Python on it still reads, with no buttons at all.

**`new`, `build`, `doctor` and `verify` stay commands.** They make or check a
case. None of them belongs on a page about a finished result.

**Reports move to `reports/`.** They used to be written into the export they
were read from, which meant producing a document modified a result. Same place
now whether the document came from the command or the button.

## ADR-039 — The fan wall is modelled, not looked up

*2026-09-19*

A capacity table answers "what did the manufacturer select?". Every result
turns on a different question: "what does this machine do at the air my room
actually gives it?" Those coincide at seven points and nowhere else, and the
gap is not small — reading the CA80NPVG6's table as though it were an
operating curve overstates the plant by 940 kW at 41 °C.

**A chilled-water coil is a counterflow heat exchanger.** Its capacity is

    Q = ε · C_air · (T_return − T_water_in)

and only ε belongs to the machine: it depends on the two flows and the coil's
UA, never on the temperatures. Holding a catalogue capacity fixed while the
return air climbs asserts that a coil transfers the same heat across a bigger
temperature difference, which no heat exchanger does. `aicfd/coil.py` fits UA
from the selections and answers at any condition.

**The model is fitted from the selections and nothing else.** That constraint
is the design: selections are what a manufacturer sends, and a tool needing
more would be a tool nobody could feed.

**One inference makes it work, and it is stated rather than buried.** Across a
set of selections at fixed air flow the effectiveness is *not* constant — it
climbs from 0,765 to 0,839. The effectiveness of a heat exchanger with both
flows fixed cannot change, so a flow changed; the air is stated fixed, so it
is the water. The selection program re-sizes the water flow at each row to
hold the stated entering and leaving water temperatures. Read that way, the
recovered UA rises 5 % for 45 % more water — textbook for a finned coil — and
the resistance splits 78 % air, 22 % water, which is what a chilled-water coil
is. Read any other way it does not.

**Tested by prediction, not by fit quality.** Fitted on seven selections at
one air flow, the model reproduces an eighth — a different air flow *and* a
different return temperature — to 0,014 K of supply air and 0,4 % of
capacity. Counterflow was chosen over crossflow the same way: it lands within
0,03 K where crossflow-unmixed misses by 0,06 K, and crossflow can only fit
the selections by putting 99 % of the resistance on the air side.

**The tool stops refusing.** Outside the table AICFD used to say it could not
tell, which left the plant's margin unstated for exactly the halls that needed
it — the worked hall returns 33,3 °C against a table starting at 35 °C. The
model answers there, and says it is extrapolating: physics rather than a
straight line, but nothing measured behind it. Where no coil can be fitted —
a unit whose selections do not say what water they were taken at — the table
is read as before and refused outside, unchanged.

**The supply air temperature is an assumption, and the model says when it
breaks.** A fan wall holds its setpoint only while its water valve has
authority. Once wide open the supply floats with the return, and every
temperature in the run is optimistic by the difference. That is reported as an
alert with the temperature the coil would really deliver — not as a failed
check, because the solve did what it was told (ADR-023). Making the solver
itself follow the coil per unit is the next step and is not done here.

**The capacity quoted is the coil's ceiling, at valve wide open.** Real for
one unit; every unit at its ceiling at once is a chilled water plant nobody
sized. So the water each unit draws is reported beside it, and the hydraulic
side is named in the limitations as something this model does not see.

**The exponents are held, not fitted.** Air-side 0,6, water-side 0,8. Seven
selections at one air flow cannot identify the air-side exponent, and fitting
it would hide the assumption inside a number.

## ADR-040 — The supply air temperature is solved, not imposed

*2026-09-19*

A fan wall does not decide what air it delivers. Its coil does, from the air
the room gives it. Imposing a supply temperature and solving once answers a
question about a machine whose delivered temperature is independent of the
room feeding it — which is no machine. The room and the machine are solved
together now.

**How.** The room is solved in segments. After each, every unit's own return
temperature is read off the solved field, put through that unit's coil, and
written back as that unit's supply temperature; the next segment continues
from the field already there. It stops when no unit's supply moves more than
0,02 K. Coupling is an *addition* to the run, never a reduction: the first
segment is the iteration cap the spec asks for, unchanged, and the passes are
what it costs to make the supply a result.

**Why it converges in two or three passes.** The room fixes the temperature
rise across itself — load over mass flow — so a change in supply moves the
return one for one, and the coil passes back only (1 − ε) of it. With ε near
0,8 the loop gain is about 0,2 and each pass cuts the error five-fold. The
loop does not rely on that: it measures what actually moved and stops on it.
Measured on the worked hall: two passes, 308 s, converged to 0,000 K.

**Why segments and not a self-computing boundary condition.** OpenFOAM's
`codedFixedValue` would do this inside the solve, and that is how it is
usually written up. It compiles C++ at run time, which needs a toolchain the
*packaged* OpenFOAM installs do not ship — this container has `g++` but no
`wmake`. A clone that runs everywhere cannot depend on it. Segments need
nothing but the solver and reach the same fixed point from outside.

**Each unit gets its own temperature.** Not the plant's average. A unit at the
end of a row returning warmer air delivers warmer air, and giving every unit
the mean would smear away exactly the imbalance a hall is simulated to find.

**What it found on the worked hall: nothing, and that is the result.** All
fourteen valves have authority, so the coil delivers the 21,9 °C the run
imposed and the coupled answer equals the uncoupled one. The difference is
that this is now *shown* rather than assumed. Change the chilled water to
20 °C and the valves run out: the supply floats to 22,8 °C and every
temperature in the hall goes with it.

**One selection describes the unit; the manufacturer's others are reference.**
A unit now carries a `design` block — the duty the plant was bought on — and
every default and the coil's water limit come from it. The other rows stay as
reference and as the data the fit is made from, never as an operating curve:
the selection program was free to ask for more water at each of them.

**That split buys a standing test.** The design selection is deliberately kept
*out* of the fit, so every unit carries a live blind check of its own coil
model. For the CA80NPVG6 it is 0,014 K. A number that drifts says the
selections have stopped describing one machine.

**The lock is across processes, not just threads.** The first version used a
thread lock, which fixed the sampler racing the coupling loop inside one run
and left untouched the readers that actually matter: `aicfd post` on a case
that is still solving, or the results page refreshed mid-run. Those are other
*programs*. It is now an advisory lock file beside the case, taken by whoever
rebuilds a time directory and by whoever reads one — `analyse` included, for
its whole length. Re-entrant, because analysing a result samples it and
sampling reconstructs. After three minutes it proceeds anyway: a reader that
has waited that long has a worse problem than a torn field, and failing a
solve that already cost half an hour to protect it would be the wrong trade.

**The first coupling pass says it is the first.** It used to report
`moved 99.000 K` — a sentinel that reads as a measurement, and a reader who
took it for one would think the loop had diverged when it had not started.
It is None now, and prints as such.

**Chilled water temperature is an input.** `fanwall.entering_water_c`. It is
the one condition a plant changes without changing the machine, and the one
the supply follows at about 0,8 K per K once the valve is open — by far the
dominant sensitivity. It moves only the water the plant circulates: the fit
stays anchored to the selections as issued, because re-reading them at
another water temperature would change the flow inferred behind every row and
quietly turn the machine into a different machine.

## ADR-041 — The report states its method before its numbers

*2026-09-19*

Section 1 is an Introduction, fixed in every report AICFD writes, and it is
the template's opening rather than a preface that can be skipped. A reader who
disagrees with a conclusion has to be able to see what was solved, with what,
and under what assumptions, without being sent to a manual that may not travel
with the document.

It carries six things and stops: scope, the solver, the room, the cooling
unit and what the engineer enters, the coupled solve, acceptance.

**It is fixed text and takes no result.** `_introduction(doc)` has nothing to
read a study from, which is the guarantee rather than a convention, and a test
asserts the signature. Section 1 describes how AICFD models a data hall; the
study begins at section 2. A number from the run leaking into the method would
make the method look like a finding.

**Section 1.4 is the engineer's path, in five steps.** The selection is
entered as the manufacturer issued it; the software checks it against itself —
the stated air flow between the stated temperatures has to carry the stated
capacity — and refuses one whose own numbers disagree; the coil is built from
it; the curve it produces is reviewed, and kept or replaced with the
manufacturer's own; then the model runs. Section 1.5 is the coupled solve.

**Sections 1 to 5 are written in the affirmative, and tests enforce it.** A
method stated by negation reads as a defence, and a reader of a consultancy
report is not asking what the tool declines to do. What the study does not
cover is section 6's subject; how the method came to be is this record's;
sections 1 to 5 say what is solved, what was found, and what follows. No
justification of a tool choice appears there either — naming the solver and
delivering the case with the report is the whole of what a reader needs.

Three tests hold the line: section 1 carries no negated construction, sections
2 to 5 carry none of the negations that were there, and the whole document
carries none of the rhetorical contrasts — "rather than", "would have",
"instead of", "which is not" — that set a claim against something the reader
never proposed. Mesh-snapping notes now say a dimension falls between grid
lines and what the mesh uses; the capacity conclusion states the available
figure and then the catalogue one; the coil's physics is stated once, in
section 1.4, and section 3 gives this unit's numbers.

**OpenFOAM is specified.** v1912, steady buoyantSimpleFoam, SIMPLE
pressure–velocity coupling on p_rgh so buoyancy survives the hydrostatic
column, energy as enthalpy with density following temperature, k-epsilon RANS
with buoyancy terms active.

**The coil is introduced as physics.** A chilled-water coil is a counterflow
heat exchanger; its effectiveness belongs to the machine and to the two
flows; the supply air temperature is solved from it. The five steps of the
coupled solve are a table — solve, read each unit, ask each coil, write back,
close when nothing moves — so the loop is followable without the source.

**Everything renumbers by one.** Summary 2, Methodology 3, Results 4,
Conclusions 5, Limitations 6. A test now asserts that every `section N` the
document cites is a section the document has, because renumbering a template
is exactly where stale cross-references breed.

## ADR-042 — One selection describes a unit

*2026-09-19*

The coil is recovered from the unit's **design selection**: return and supply
air, airflow, net sensible capacity, and the water it was selected at. Those
fix the effectiveness at that point, the effectiveness fixes the conductance,
and the unit then answers at any condition a room presents. That is the whole
input an engineer provides, and it is what a manufacturer sends.

A unit may also carry further manufacturer selections. They set how the
resistance divides between air and water, and the model then reproduces them
to a stated error — 0,026 K on the worked CA80NPVG6. A unit carrying its
design selection alone uses `DEFAULT_AIR_SPLIT`, 0,78, which is the value a
full set recovers for a finned chilled-water coil, and which a manufacturer's
own figure overrides through `coil.air_split`. The two paths agree to 0,01 %
across the range: the extra selections refine a second-order term.

**The report carries one selection.** It gives the design selection, the coil
recovered from it, and the coil's properties. Every sentence that counted
selections, reported that a return fell outside them, or noted a point missing
from a curve has gone, and so has the table of the further ones: those belong
to the work that produced the method, and this record is where that lives. A
study runs on the selection the engineer was issued — printing seven invites
the reader to go and find six more for the next machine. The unit's file still
holds them and the fit still uses them; the document does not.

A test asserts the word *selections* appears nowhere in the document, because
that plural is how the theme creeps back.

**The capacity figure shows two curves and no table.** The coil at the air
flow it was selected for, with the design selection sitting on it, and the
same coil at the air flow this hall gives it, with every unit marked where it
ran. A coil's capacity depends on the air flow through it as much as on the
air's temperature, and drawing selections taken at a third air flow on either
line would mislead.

**The user judges the curve.** The design selection is an input; the curve
that follows is the machine. Where a manufacturer issues the unit's own
capacity curve, it replaces the recovered one.


## ADR-043 — Limitations are where the model differs from the room

*2026-09-19*

Section 6 lists where AICFD's representation of a room departs from the room,
and nothing else. It carries five items: one load per rack, ancillary losses
outside the racks, containment modelled as perfect, a conceptual-design mesh,
and no comparison against measurement. Where a run has no coil to read a
capacity from, a sixth says so and names the input that fixes it.

**What came out, and why.** Numerical uncertainty, steady state, and a single
modelled scenario are properties of CFD and of the run that was asked for.
Every CFD study carries them, no reader is asking, and listing them reads as a
disclaimer rather than a finding. Likewise the coil model's own provenance:
section 1.4 says how a unit is described and section 3 gives this unit's
numbers, so repeating it under Limitations argued with a method the document
had already set out.

**The chilled water flow came out with them.** AICFD does not judge whether a
branch is balanced or whether the pumps deliver what a coil could draw, so
reporting the flow each unit takes invited a reading the tool does not support.
What remains is the valve position — how much authority a unit has left, which
is what decides whether it can still hold its supply air temperature — and the
entering water temperature, which is an input and the temperature every
capacity is measured from.


## ADR-044 — The report is read on its own

*2026-09-19*

Section 4.4 used to end "the complete table is exported as CSV from the
results page", which sends a reader of a circulated document to a tool they
may not have, on a machine they may not have, for data the document was
already carrying. A report is read on its own: anything it cites belongs in
it.

**The full rack table is Annex A.** Every position, ordered by row, with the
same seven columns section 4.4 uses for the twelve warmest — so the ranking
and the lookup read the same way. It appears only where the hall has more
than twelve positions; below that, section 4.4 already carries them all.

A test asserts the document never mentions a CSV, the results page or a
download, because that kind of pointer creeps back in whenever a table looks
long.

---

## ADR-045 — The rack's own faces are the rack, not a wall

**Decision.** `rack_top` and `rack_end` carry `of_rack`, and the drawings skip
them. The solver does not read the flag: every face is meshed and blocked
exactly as before.

**Why they exist.** A rack is a porous box that has to breathe front to back.
Leave its lid or its ends open and the cold aisle gets into the porous zone
sideways, along a path that is almost free — a few cells of blocked axis
against 1,2 m of the flow axis. Measured as each was closed, the share of the
fan's duty entering through the rack fronts went 22%, then 57%, then the rest.
So they are real surfaces, and they stay.

**Why drawing them was wrong.** They lie exactly on the rack box, which the
drawings already show. Drawn again as walls they took the containment colour,
and in the transverse section the lid came out as a heavy green line across
the top of the rack, meeting the chimney wall in an L. That reads as a
containment duct hanging above the rack with a horizontal roof — a wall where
the room has a rack, in the one drawing whose job is to make the containment
unambiguous. In the plan the lids were worse: twenty dashed rectangles lying
exactly over the racks, saying nothing the rack outline did not.

The chimney now comes down and lands on the rack, which is what it does.

**Why a flag and not the name.** `name.startswith('rack_')` would have worked
today and broken the first time a surface was renamed, silently and in the
drawing only. The model says what a face belongs to; the drawing asks.

**What it cost to find.** The flag came back false on every panel: the
mesh-snapping pass rebuilt each `Panel` field by field and stopped one short,
so a field added later arrived as its default. That pass now uses
`dataclasses.replace`, which cannot drop a field it has not been told about.

---

## ADR-046 — The fan wall is drawn with its depth and meshed without it

**Decision.** The unit's depth joins its width and height on the spec, filled
from the named unit's datasheet, and the drawings set the machine's envelope
back into the mechanical gallery. The mesh does not change: a fan wall is
still the zero-thickness baffle pair the solver has always seen.

**Why draw what is not modelled.** The baffle pair is the right physics — a
fan wall is a boundary condition, air leaving one face at a temperature and a
rate — and giving it a volume would buy nothing the momentum source does not
already give. But it left a machine 3,96 m wide, 3,67 m tall and 1,48 m deep
as a line on the page. A reader asking the question the drawing exists to
answer — does the gallery hold these units, with room to pull a fan module —
had nothing to measure. The depth is stated on the datasheet; not drawing it
was losing information the case already had.

**Where the depth comes from.** The spec, like the width and the height
beside it. Naming a unit fills all three; a typed value wins. A case that
states none leaves the fan wall as the plane the solver sees, because
inventing a depth would put a machine on the drawing that nothing in the case
describes.

**Which side.** `sign` already says which side of the panel the gallery is
on — it is what tells a fan wall which half of its baffle pair is the intake
(ADR-027) — so the body sets back the way the machine actually faces, and a
hall with a gallery at each end gets both right without a special case.

**The envelope is quiet.** A light fill under the heavy fan line, and an
outline rather than a fill over a field map, where a translucent rectangle
would tint the temperatures beneath it. It is the machine's extent, not a
surface anything was solved across, and it should not read as one.

**The report's figures draw it too.** They are matplotlib rather than SVG, so
the envelope had to be written twice — the plan and the section along the hall
each set it back behind the fan's face. A result exported before the depth
reached the payload falls back to the library, which is where the number came
from, so a report on an older run draws the machine rather than the plane the
solver saw.

---

## ADR-047 — A hot aisle stops where the racks do

**Decision.** The hot aisle tint is drawn per rack block. The cold aisle
keeps the hall's length.

**Why.** It was painted from one end of the hall to the other, so on the plan
it ran past the end of every row and straight across the transverse aisle
between two blocks — three strips of bare floor per aisle, tinted as though
the containment reached them. On a hall with five aisles and two blocks that
is fifteen claims the geometry does not make, in the drawing whose job is to
show where the containment is.

**Why the cold aisle is different.** A hot aisle is a pocket between two rack
rows and ends with them. A cold aisle is fed by the fan wall and opens onto
the room; its air is the room's air, and the tint says which volume is which
rather than where a wall stands.

**A section along a hot aisle shows the containment it is inside.** The plane
passes BETWEEN the two walls that contain the aisle, so neither is cut and
both project onto one rectangle — rack top to ceiling, over the length of the
block. That rectangle is the contained volume, and it was drawn at the weight
everything off the plane shares: a thin dash whose every edge landed on
something already on the page, the ceiling line along its top, the end doors
down its sides, the rack tops along its bottom. It showed nothing. The one
view that has no aisle tints — the only one where y is off screen — was also
the one view with no sign of the containment in it.

It now carries the hot aisle's own tint inside the containment's green, which
is what the plan shows in the same place, drawn after the room's own lines so
it is not buried under them. Only in that view: where the aisle bands of the
step above are on screen they already say which volume is which, and a second
wash over them says it twice. Over a field map the wash goes and the outline
stays, because a wash would tint the temperatures underneath.

The report's figures are matplotlib and had no containment in a section at
all. They now draw the same two things: the contained rectangle looking along
the hall, dashed; and looking across it, the chimney beside each rack row,
solid, because there the plane cuts the wall along its length.

---

## ADR-048 — The surfaces the air passes through are a library, not a case field

**Decision.** A component library beside the equipment one, `components/`,
holding the perforated surfaces: the grilles in the false ceiling, the woven
mesh that closes the return plenum where it opens into a mechanical gallery,
the plates of a raised floor, and the leakage a containment has. Each is one
number — open area over gross area — and the loss coefficient follows from it.
A case names a component; the component says what it is. They have their own
page, and saving one changes every element of that kind in every case that
names it.

**Why not a case field.** Because they are standards. A house specification
fixes the ceiling return grilles at 65 %, the floor plates at 54 % and the
containment leakage at 5 %, and every hall built to it uses those numbers. Left
as a field per case, the third hall gets 0,8 because that is what the template
said, and its fan duty is then not comparable with the first two and nobody
notices. One place to state a standard is what makes a set of studies a set.

**Why the gallery mesh is fixed.** The opening is closed with a woven security
mesh in every hall built this way — it is the building, not a choice — so the
library marks it `fixed`, the page shows it without editing it, and the save
path refuses it rather than the page merely hiding the field. What it is not
is free: every cubic metre the plant moves passes through it once, and until
now the model let it through for nothing. It now carries its loss coefficient
like any other surface.

**What that cost to wire.** Three things, each a real consequence rather than
a detail:

1. The porous baffle was selected by the name `grille`. It is now selected by
   *having a resistance*, because a hole in a wall with a pressure jump across
   it is one thing whatever it is called.
2. `sealed_envelope` reads every surface that is not a fan and not a grille as
   a leak. The mesh carries the whole return by design, so it failed a hall
   that was sealed. The check now excludes the perforated surfaces as a class.
3. The measured grille drop took every cyclic pair in the case, and every pair
   ends `_below`. With the mesh added it averaged two surfaces of different
   open areas together and then matched neither one's K — `grille_resistance`
   read 53 %. Each surface is now measured against its own.

The second and third were found by solving, not by reading. A resistance added
to a model and not verified against the field it produces is a number nobody
has checked.

**A free area is typed, a K is shown.** The page takes the open area and shows
the loss coefficient live beside it, because the consequence of the change is
what the engineer is choosing and it should be visible before a solve rather
than after one. Where a datasheet states K directly it wins: a measurement
beats a correlation.

**Fully open is a component.** `ceiling-return-600-open` is a 600 × 600 hole
with nothing in it. It is not a product; it is the control case. Run a hall
against it and against the specified grille, and the difference is what the
grilles cost.

**The save is checked before it lands.** The editor rewrites lines in place to
keep the comments, and a rule that mishandles one shape of value damages the
*file* rather than the value: a folded note whose continuation lines were left
behind swallowed the keys under it and the library stopped loading. That
happened, on the save path, where the damage is written before anything reads
it back. A save is now parsed back into a component before the file is
written, and refused if it cannot be.

---

## ADR-049 — A drawn aperture, and a number the solver does not read yet

**Two decisions on the components page, both about not lying to the reader.**

**The aperture is drawn from the component's own numbers.** Each component
carries a `pattern` — woven, egg-crate, perforated, slotted, joint — and the
page generates it at the open fraction the component states. A 13 mm weave on
3 mm wire is drawn as that weave; a 51 % perforated face gets holes whose area
is 51 % of the plate.

A photograph from a catalogue would look more like the product and say less
about the only quantity that decides anything, and it can disagree with the
number beside it — a picture of a 65 % grille sitting above a field edited to
50 % is a drawing that is wrong. Generated from the same number, it cannot be.
It also keeps a manufacturer's photograph out of a repository that is meant to
be cloned and redistributed.

**A number the solver does not read yet says so, on the page.** The library now
holds three figures the specification states and the solver ignores: the 5 %
permeability of a containment, the 54 % of a raised floor plate, and the 2 % of
the IT load that power distribution dissipates. Each carries `applied: false`,
and the page marks it and says what a run answers instead — as though the
containment were sealed, as though the dissipation were zero.

The alternative was to leave them out until the modelling caught up. That is
worse: the engineer types 5 %, runs the hall, gets the same answer as before,
and has no way to tell whether the leak does not matter or the leak was never
read. A stated intention with a stated gap is honest; a silent one is a trap.

**Why a dissipation is in a library of surfaces.** Because the page is the
house specification rather than a catalogue of grilles: the numbers a set of
studies has to share for its results to be comparable. A free area and a share
of the IT load are both that. The component carries a `kind`, and each is
shown as what it is — the load has no free area, no loss coefficient, and no
aperture drawn, because it has no face for air to cross.

---

## ADR-050 — A section draws each thing once, and an aisle has no edge

**Three faults, one page, all of them the drawing claiming something the room
does not have.**

**What lies along the view axis piles up.** A section looks down an axis, so
everything along it projects onto the same rectangle. The worked hall's 440
racks land on ten in the transverse section and forty-four in the longitudinal
one. Drawn one per rack that is forty-four copies of the same outline, and an
outline at 45 % opacity stacked forty-four deep is opaque: a rack the section
does not cut came out looking exactly like one it did, which is the single
distinction these drawings exist to make. Each rectangle is now painted once
per class, and the racks the plane cuts go down first so solid beats dashed
where both would land — the drawing's own rule, decided by order.

**An aisle is a tint, not a surface.** Over a field map the volume fills become
outlines, which is right for the hall and the gallery and wrong for an aisle: it
drew a black line the length of the hall along each side of every aisle, an edge
the room does not have and one the reader has to decide to ignore. They are not
drawn there at all now. The temperatures underneath say where the aisles are,
which is what the map is for.

**The floor between two blocks is room.** The aisle bands stop where the rack
rows stop, so on a plan the transverse aisle and the clearance at each end came
out as bare paper: a white gap across every row that read as something missing
from the drawing. It is open floor on the cold side of the racks — a person
walks down it — so it takes the cold tint, at full hall width, while the bands
take only the blocks. The two are complementary by construction, which is what
stops a strip being tinted twice and coming out as a darker square.

Painted only where x is on the page. Looking along x the strip has no width
there, and painting it covered the whole transverse section in cold and washed
the hot aisles out of it — found by looking at the drawing after the fix, not
before.

---

## ADR-051 — The card names the choice; the page owns the numbers

**Decision.** The model page's group for return air carries, per role, which
component this case uses and what it costs — a select where the library offers
more than one, a line where it offers one. The two fields that used to state a
free area and a loss coefficient by hand are gone.

**Why they had to go.** They said the same thing as the component the case
uses, in a second place, and could disagree with it: a card reading 80 % free
above a drawing solved at 65 % is a page arguing with itself in front of the
reader, and neither number says which one the solver read. One place to state
a thing is what makes it checkable.

**Why the choice stays.** Which grille a hall is built with is a per-case
decision — it is what a study is often about. What that grille IS, the open
area and what it costs, is a house standard. So the card names the machine and
the components page describes it, which is the split the fan wall already has:
`CA80NPVG6 ▸` on the model page, the selections and the curve behind it.

**The cost is shown beside the name, and updates on the select.** What a
choice costs is the reason for making it. Seeing it only after Apply and a
round trip makes the control feel like it did nothing, and sends the reader to
another page to answer the question they were already looking at.

**A role with one option is a line, not a select.** There is nothing to choose,
and a select with one entry invites a reader to look for a second. The line
still carries the cost, because the mesh closing the plenum is fixed and still
paid for.

**What is not applied says so here too.** The containment's leakage, the floor
plates and the distribution losses carry the same tag the components page
gives them. A reader deciding whether a case is set up correctly should not
have to open another page to learn that one of its numbers is inert.

---

## ADR-052 — The room is cold, and the captions are set on a halo

**One rule for the volumes.** The hall below the false ceiling is cold-side
air. That is what the fan wall fills it with and what a person standing in it
breathes, so it is the room's default state and the contained hot aisles are
the exception painted over it. The return plenum above the ceiling is the hot
side: it carries the air the racks have just heated, on its way to the units.

**What that replaces.** A list of tinted bands — a strip per cold aisle, a
strip per hot aisle — with everything else left as bare paper. The space above
a rack was white. The transverse aisle between two blocks was white. The whole
longitudinal section was white, because that view has no y axis on screen and
the bands were keyed to y. White reads as a gap in the drawing rather than as
the room, and the special case added to patch the plan's gaps then had to be
switched off in the section where it had no width. The hall's own fill covers
all of it, and the special case is gone.

**The hot tint is painted over the cold room now**, not over paper, so it had
to carry against it: at the weight it had, a chimney came out a muddy lilac
that read as neither side of the loop.

**Every caption is set on a halo** of the page's own background, painted under
the glyphs so the letters keep their shape. The drawings are dense exactly
where the captions have to sit — `chimney` on the chimney, `cold aisle` in the
aisle — and a label that has to be deciphered against the thing it labels is
not doing its job.

**A panel's caption sits near the top of its rectangle**, not in the middle.
The middle of a fan wall in section is where the racks and the sensor markers
are, so `fan wall` was set across a rack with a sensor drawn through it. The
top of the same rectangle is clear. A rack's own id stays centred: the
rectangle is the rack, and there is nothing else in it.

---

## ADR-053 — A direct label is a mark and a name, and the palette is measured

**The chart.** Four places are monitored while a run settles, and three of
them sit within a kelvin of each other at steady state. Their end labels were
stacked apart so they would not overlap, which left three names beside four
lines and nothing saying which was which.

**Each label now carries a mark in its series colour and its name in ink**,
and a label pushed off its own line keeps a thin leader back to where that
line ends. Colouring the text was the original intent and it never worked: the
class sets `fill`, and a CSS rule beats an SVG presentation attribute, so every
name came out the same grey. A mark is the better answer anyway — it survives
print, and it survives a reader who cannot separate the hues, which coloured
text does not.

**The palette was measured, not judged.** Two of the four series failed the
normal-vision floor: `#eda100` against `#eb6834` at ΔE 13,7 where 15 is the
floor — "hard to tell apart even with full colour vision". Those were two of
the three bunched lines, so the complaint and the measurement were the same
fault. The fourth slot is re-stepped to a magenta, `#a3166b` on the light
surface and `#cf62b0` on the dark, chosen by searching the colour space for a
step that passes every pair in both themes rather than by eye. Dark is its own
step from the same family, not the light one flipped: the obvious purple
candidate passed on white and collapsed against the dark theme's blue at
ΔE 1,9 under protanopia.

**A sensor marker is not a series.** It had been drawn in `--series-4`, so
re-stepping that slot for a chart would have repainted every sensor in every
drawing. It has its own token now, holding exactly the colour it already had,
and the drawings are untouched.

---

## ADR-054 — A hall is specified by one load and filled by a list

**Decision.** `racks.load_kw` stays the hall's standard, and `racks.loads` is
a map of position to its own load, zero included. A page of its own carries
both: the standard at the top, every position below it. The case file stores
only the positions that disagree, so raising the standard later moves every
rack that never had one of its own.

**Why it is not a detail.** A hall is bought at one load per rack and no hall
is ever filled that way — positions are reserved, staged, or left for growth.
Where the gaps sit decides how evenly the units load, and a study that assumes
every cabinet is full answers a question about a hall nobody has yet.

**What a zero is.** A cabinet that exists and dissipates nothing. It keeps its
cell zone and its porous block, and loses its heat source: writing `h (0 0)`
would be a source injecting nothing while claiming there is IT in the cabinet.
It is *not* a hole through the row. An empty position is blanked — that is
what blanking plates are for, and a hall that leaves them off has a problem
worth its own study, not a default. So the cabinet resists like the ones
either side of it and only the heat is missing.

**Resistance is a property of the cabinet, not of its load.** `Rack` carries
`resistance_kw` beside `load_kw`: the load its flow resistance is calibrated
at, where that differs from what it dissipates. Row building sets it to the
hall's standard for every position, so a zeroed cabinet keeps the row's
coefficient. Calibrating it at its own zero instead made its own fans draw
nothing, its inertial term zero, and the position an open path: in the worked
POD, one of three positions emptied that way dropped the measured drop across
the row to 1,7 Pa against the 26,8 Pa the curve asks for — 6% — and
`rack_resistance` rightly failed. The first attempt answered that by narrowing
the closed form to the loaded positions, which is the wrong fix: it makes the
check agree with a model that is wrong about the hardware.

**What the check says now.** The row is still one resistance, so the closed
form still takes one coefficient and the whole face, and it holds with gaps
anywhere in the row. It quotes how many positions carry no load beside its
verdict, so a hall below its nominal load is never read as one at it.

**A field added in the middle of a dataclass, again.** `resistance_kw` went in
before `rho`, and `snap_rack` rebuilt every rack positionally — so each rack
came back calibrated at 1,2 kW, the air density. `f` read 7 567 instead of
301, the closed form asked 672 Pa instead of 26,8, and nothing raised a thing.
This is ADR-045 for the second time. Every rebuild of a model dataclass is
`dataclasses.replace` now; a positional one in this codebase is a bug waiting
for the next field.

**Why the table sends every position and not the edits.** The server drops the
ones equal to the standard. A position edited back to the standard therefore
stops being stated and follows it again, which is what "back to the standard"
has to mean for the file to stay a statement of what differs.

**The case keeps its comments.** A case file is hand-written, and the comment
beside `airflow_cfm_per_kw` is what tells the next person that the number
sizes the rack's resistance. Saving from this page rewrites the two lines it
owns — the standard, and the block of positions — through the same textual
editor the libraries use (ADR-048), and leaves every other byte alone. The
block is written the first time a position disagrees and removed when the
last one stops, so clearing the table gives back the file that was there
before. The save is parsed and compared against what was asked before it is
written; a rack block that did not survive the edit is refused rather than
saved.

---

## ADR-055 — A change is built before it is saved

**Decision.** The page's Apply builds the model from the edited spec first and
writes the case only if the model builds. A spec the generator refuses is
reported as a rejection, exactly like a value outside its range, and the file
on disk is left as it was.

**What it fixes.** Apply saved first and built second. Every field was inside
its own range — seven fan walls, four metres each — and what they described
together was a 26,1 m wall with 28 m of plant on it, which `place_fans`
rightly refuses. By then the refusal was the case on disk: the next request
for the model failed with the same error, so did the one after a reload, and
the page that could have undone the change never drew its form again. One
press of a button and the case was unreachable from the interface that wrote
it.

**Why a range check was never going to catch it.** `EDITABLE` holds each field
to its own limits, and that is the right job for it: it is what stops a typed
`4O` or a negative aisle. It cannot know that a count and a width are only
wrong *together*, or wrong only in a hall of this length. The generator knows,
because building the model is what finds out. So the generator is the check,
and the only thing that had to change is the order.

**A case that cannot be built says where it lives.** The page draws itself
from the model, so when there is no model there is no form — nothing to edit
and no way back. The error panel now names the case file and what the
generator said, which is the shortest way to a file a person can open. The
page can no longer put a case into that state; one edited by hand still can,
and now it says so.

**The general rule.** Nothing writes a case it has not built. The rack page
follows it too, for the same reason and in the same order.

---

## ADR-056 — The tests own their cases, and `cases/` is not a fixture

**Decision.** `tests/cases/` holds the cases the suite reads and writes.
Nothing in the suite saves into `cases/`, and a test that reads a worked case
from there does it through `support.spec`, which skips — naming the file —
when that case cannot be used. Whether the shipped cases still build is one
test of its own, `test_cases.py`.

**What went wrong.** `cases/` is the engineer's working folder: the page
writes to it, and a case edited through the page is the software doing its
job. The tests read those same files as fixtures and asserted on their
contents — that no component had been chosen, that the comments were still
there. So a legitimate edit failed the suite, and because `docker build` runs
the suite over the copied working tree, the image stopped building for a
reason that had nothing to do with the image. One press of Apply, before
ADR-055, left a case the generator refuses; the build came back with thirty
errors and two failures, and not one of them named a file.

**Why the answer is not just "restore the file".** That fixes the instance.
The shape stays: every case a person edits is a fixture some test asserts on,
so the next real edit breaks the build again. A fixture the tests own cannot
be edited by the person using the software, which is the whole point.

**A broken case now says so once.** The guard test fails with the file, the
generator's sentence and the command that puts it back. Everything whose
fixture that case was is skipped rather than errored: a missing fixture is
not the code being wrong, and thirty tests saying so is thirty ways of not
saying which file.

**The image still builds the working folder.** `COPY . /app` carries the
cases as they are, and the guard test runs inside the image, so a case that
cannot be built does stop the build. That is right — an image whose cases do
not build is not an image that works — and now it says which one and why.

**What the sandbox caught on its way in.** Pointing `CASES_DIR` at a
directory outside the repository broke the error panel itself:
`Path.relative_to` raises rather than declines, so the code that was there to
name a broken case's file raised a second error instead. `case_path` answers
relative where it can and absolute where it cannot.

---

## ADR-057 — A page's script and a shared module never share a file

**Decision.** `web/` holds two kinds of file: the script a page loads, and
the module other files import. No file is both. The rack-inlet card of the
results page is `rack-inlets.js`, named for what it exports; `racks.js` is
the page that configures the racks, named for the page that loads it. A test
reads the import graph and fails when a name is imported that its file does
not export, and when a page's entry script is also imported by another page.

**What happened.** `web/racks.js` was the results page's rack-inlet card. The
new rack page followed the convention every other page here uses —
`equipment.html` loads `equipment.js`, `components.html` loads
`components.js` — and was written straight over it. `results.js` then
imported `RackInlets` from a file that no longer exported it, which in an ES
module is a *parse* error: the whole module fails, so the whole results page
fails. Not the rack card. The page, stuck on "loading…", after a solve that
had worked perfectly.

**Why nothing caught it.** There is no build step (ADR-005), which is a
deliberate choice and a good one, but it means nothing reads the import graph
until a browser does. The suite checked the contents of these files —
several tests assert on what `app.js` and `drawing.js` contain — and never
checked that they still fit together. A page can only be broken this way by
something that is not visible in either file alone.

**Why the convention is kept and the collision moved.** Page-named scripts
are worth having: a reader looking for what `components.html` runs should
find `components.js`. So the names pages want are reserved for pages, and a
shared module is named for what it exports. `racks.js` was a poor name for a
rack-inlet chart anyway — it said what the file was about rather than what it
was.

---

## ADR-058 — A supply plenum is the hall wall built twice

**Decision.** `plenum.enabled` doubles the wall between the hall and the
mechanical gallery. The leaf the units are mounted in is the one that was
always there and does not move; a second leaf stands `plenum.depth` (1,2 m by
default) into the hall; the cavity between them is pressurised; and the inner
leaf carries a supply grille in front of each cold aisle, as tall as a rack
and 2 m wide by default. The grilles are a component role of their own, so
their free area is a house standard, and any of them can be shut.

**What it is for.** Without a plenum the unit blows through the wall straight
into the cold aisle it faces: where the unit aims is where the air goes, and
a cold aisle with no unit in front of it is fed by whatever spills sideways.
With a plenum the room is fed by the grilles instead. The cavity evens out
what the units do, and the grille decides the direction — which is what makes
the supply uniform along the wall and aimed at the aisles.

**The hall grows; the clearances do not.** The plenum is added to the
building, not carved out of the room. `aisles.perimeter` and `racks.offset_x`
are the clearance between the racks and the wall the hall actually has, and
with a plenum that wall is the inner leaf — so the rows start past the plenum
and the hall is longer by its depth, at each gallery that has one. The worked
hall goes from 51,0 m to 53,4 m for a plenum at both ends, and not one rack
moves relative to the wall it faces.

**The cavity needs no lid.** The false ceiling already covers the hall, this
strip included, so the plenum is closed at that level and the return passes
over it above the ceiling exactly as before. The whole arrangement adds two
kinds of surface to the case — the inner leaf, and the grilles in it — and
changes nothing else about the air loop.

**What the first attempt got wrong, and why it mattered.** It read "1,2 m
between the face of the unit and the wall" as the unit moving back into the
gallery, with a partition of its own to be mounted in and a lid to keep the
return out of the supply. That is a different building: it changes how the
unit meets the wall, which is the one thing that was not to change. The
symptom on the drawing was the fan wall coming away from the wall it lives
in. The lesson is the cheaper one: when a change is described as a change to
*one* element, the parts that were not named stay where they are.

**Drawn like what it is.** The cavity takes the cold aisle's own tint,
because it is the same air on its way to the same place. The grilles are
drawn in the grille red every other grille uses. The inner leaf is drawn as
building fabric rather than in the containment green every `wall` wears --
otherwise a green line across the end of the hall says that end is contained,
which is the misreading ADR-045 answered over the racks.

---

## ADR-059 — A plenum is sized before it is solved, and the velocity is
## reported rather than judged

**Decision.** Two questions are answered from the specification alone and
raise a design alert: whether the units have the pressure for what the loop
costs, and — for a mesh leaf — whether there is room left to work in front of
the racks. The face velocity through the supply grilles is **reported** among
the derived numbers, beside the fan wall's own, and nothing passes judgement
on it. After a run, `plenum_resistance` checks the field's drop across the
supply grilles against the closed form, exactly as `grille_resistance` does
for the return, and quotes the velocity without a verdict.

**The velocity criterion was removed, and it was right to remove it.** It was
3 m/s, taken from how a supply grille into an occupied room is usually sized.
A real hall ran at 3,3 m/s — ordinary for a data hall — and the alert fired
on a sound design, told the engineer his grilles were "small for the duty",
and attributed the 3 m/s to *his case* when the number was the tool's own.
Three faults in one sentence: a threshold that does not generalise, a verdict
on someone else's specification, and a misattribution. An alert has to earn
its interruption; this one spent attention and returned nothing.

**What replaces it is the number.** 7,5 m/s and 35 Pa, beside the fan wall's
1,98 m/s, is what an engineer needs to judge whether the opening is right.
The tool is better at measuring than at having opinions, and the reader knows
his own hall.

**The velocity is a consequence, so it is never an input.** It is what the
airflow and the size of the opening come to. It was briefly offered as a form
field, which said the opposite — that it was a number to type in — and it sat
among depth, width and height, which really are choices.

**What stays, because it is not an opinion.** `loop_pressure_drop_pa` adds up
the closed forms already in the model — racks, return grilles, the mesh into
the gallery, the supply grilles — and compares them against the unit's own
P-Q curve. That is the machine's own datasheet answering, not a rule of
thumb, and the alert says the total is a lower bound because the aisles and
the turns are not in it.

**Pressure and velocity are the same fact**, which is why removing the
velocity verdict loses nothing. A perforated surface costs `K rho v^2 / 2`
and `v` is the flow over its area: a velocity high enough to matter *is* a
pressure, and pressure has a reference to be judged against where velocity
has only a rule of thumb.

**So the alert names where the pressure goes, and how much face would fix
it.** A total says the plant is short; the breakdown says what to change. And
because the cost falls with the *square* of the area, the face that gives
back exactly the pascals the unit is missing is arithmetic — no threshold in
it: "3,6 m2 per plenum would have to be 17,4 m2 to give back the 1 903 Pa the
unit is short". When even a free opening would not close the gap the sentence
is left out, because the pressure is somewhere else and widening these would
be the wrong repair.

**Why before the run at all.** Both answers are arithmetic. Neither needs a
solve, and finding out after one is finding out late — the run costs minutes
and the conclusion was available the moment the geometry was typed.

**Gross area, not free area.** The face velocity is the flow over the whole
opening, which is what a grille catalogue quotes and what its pressure table
is fitted to. The free area is already inside the loss coefficient; using it
twice would double-count the perforation.

**The card shows the standard rather than nothing.** A field a case says
nothing about renders the house default it would get — 1,2 m, 2 m, the rack
height — so a reader takes it or changes it instead of guessing what an empty
box means. And a checkbox renders whether or not the case mentions it:
filtering the form on what a case states dropped both switches and left the
plenum as number boxes with no way to turn the thing on.

---

## ADR-060 — The plenum's hall side, as a mesh, for a hall already built

**Decision.** One wall, three arrangements, and the page offers them as one,
the other, or neither — never both. **Include Plenum with grilles** doubles
the wall and puts grilles in the inner leaf. **No Plenum, only Mesh** doubles
it the same way and closes the hall side with the 13 x 13 mm woven mesh, open
over its whole face. Neither is the wall as it was, and that is the default.
The cavity, its depth and the units are the same in both; what differs is the
leaf. `plenum.as_mesh` is an arrangement in its own right and does not need
`plenum.enabled` set as well, because the two are offered as alternatives and
ticking one clears the other.

**Either leaf is a wall as far as clearance is concerned.** A mesh screen is
as much an obstruction to a person and a cabinet door as a panel is, so the
building grows by the cavity whichever one closes it and `aisles.perimeter`
and `racks.offset_x` are the clearance to the leaf either way. Everything
here is based on the clearance, and only a case with no plenum at all is the
shorter room — it has no cavity in front of it to make room for.

An earlier version had the mesh leaf take its cavity out of the room instead,
on the reasoning that a hall already built cannot grow. That treated a mesh
as if it were air: it is not, a person still cannot walk through it, and the
arrangement it produced was a row of cabinets 0,8 m from a screen.

**A barrier, not a distributor.** The mesh is open everywhere, so it aims
nothing: where each unit points is still where its air goes, and the cavity
only evens out what the units do by being a cavity. That is the trade against
grilles, which cost more and decide direction.

**It IS a surface.** The first attempt modelled it as a loss coefficient
added to `fan_capacity`, on the reasoning that a mesh over the units' own
opening cannot redirect anything. It is not over the opening: it closes the
plenum's hall side, a metre away, and it is as much a surface as a ceiling
grille. The solver builds it as a cyclic pair like any other porous surface,
`plenum_resistance` checks the field against its K, and the duty must not
count it twice.

**One product, one number.** The mesh leaf reads the same `gallery_mesh`
component as the return side. A house that changes its mesh changes it on
both sides of the loop, which is what a house standard means (ADR-048), and a
second component holding the same 13 x 13 mm would be the two of them
drifting apart.

**What the summary was hiding.** The list of surfaces `createBaffles` builds
came from the wall plan, so a porous surface that is nobody's hole was built
and never listed. A grille punched through a wall is still counted on that
wall's line — "less 3 opening(s)", which is the readable way round — and
anything standing on its own is now named.

---

## ADR-061 — Apply edits the lines it owns, and the tests never read the
## engineer's cases

**Decision.** Saving from the model page rewrites only the values that
changed and leaves every other byte of the case alone. And no test reads
`cases/` except the one that asks whether what is shipped still builds:
everything else reads a fixture under `tests/cases/` that the suite owns.

**What happened.** An engineer set a few rack loads and changed a dimension
through the page — the software doing exactly its job — and `docker build`
stopped working. Four tests failed, and all four were asserting on the
*contents* of his working case: that this hall states no per-rack overrides,
that it is 49,2 m long, that its comments are still there. One of them told
him to `git restore` the work he had just done.

**ADR-056 fixed half of this and I said it was fixed.** It decoupled the
tests from a case edited into something that would not *build*, and left them
coupled to a case edited into something that builds perfectly well. The
second is the commoner failure and the more confusing one, because nothing is
wrong anywhere: the case is valid, the code is right, and the image will not
build. A fixture the user cannot edit is the only version of this that holds.

**Why the comments were gone in the first place.** Apply re-dumped the parsed
document — `yaml.safe_dump` — so one press saved the right values and threw
away every comment in the file. The machinery to avoid that had existed since
ADR-047 and was being used by the component library and the rack page, and
not by the page that writes cases. `yamledit.rewrite` now does for a case
what `set_scalar` already did for a component: find the line, change the
value, keep the trailing comment in its column, and touch nothing else. A
change that changes nothing leaves the file byte for byte as it was.

**The dump is still the fallback, and that is deliberate.** Where the edit
cannot be made faithfully — a key removed, a shape the line editor cannot
express — `rewrite` returns None and the dump runs. A file that is correct
and bare beats a file that is pretty and wrong. The result is parsed back and
compared against the spec that was asked for before it is written, so the
comment-keeping path can never save something different from what the dump
would have saved.

**The test that punished the user is gone.** "The shipped cases keep their
comments" asserted on files the software is built to edit. With Apply no
longer destroying them the assertion is nearly always true, and it was still
the wrong test: whether a save keeps a file readable belongs on the save
path, against a sandbox, which is where it now is.

---

## ADR-062 — A chilled-water coil has no heating mode

**Decision.** Where the air reaching a unit is already at or below the supply
setpoint, the valve shuts: the supply temperature is the return, the capacity
is zero, and the ceiling is what the coil could remove, never less than zero.

**What it did before.** It pinned the supply at the setpoint. On the worked
unit that reads 15 °C of return delivering 21,9 °C of supply and a capacity of
**minus 214 kW** — the coil warming the air by seven kelvin with 18 °C water.
The negative number in the report is the smaller half of it. That supply is
written back onto the fan wall's own patch by the coupled loop, so the unit
went on to *inject heat into the room it was cooling*, and nothing downstream
would have said so.

**It is not an exotic condition.** A zone that has been over-cooled returns
air below the setpoint, and so does any unit whose neighbours are doing the
work. Networked control makes it ordinary rather than rare: a unit told to
open its valve because another unit's return is hot will over-cool its own
zone and land here on the next pass. Fixing this first is what makes that
feature safe to build on.

**Where the water is warmer than the air, the ceiling is zero.** `ceiling_kw`
is the most a coil could *remove*. Computed as `air x (return - coldest)` it
goes negative the moment the return falls below the water, and every
percentage taken against it inverts. Clamped at zero, a unit with nothing to
cool reads as a unit with nothing to cool.

**The trickle case is answered by the trickle.** Where even the smallest flow
the valve can pass takes the air past the setpoint, the unit delivers what
that flow gives rather than the setpoint it cannot hold from above. Same
principle: report what the machine does, not what it was asked for.

---

## ADR-063 — One coil model for every unit, and it says whose numbers it is

**Decision.** Every fan wall is modelled the same way: the coil is fitted from
the unit's design selection and answers at any return temperature. There is
no second path. The capacity table read between its rows and refused outside
them is gone, and with it the two classes of unit that had different answers
to the same question. A unit that cannot be fitted is a file that is not
finished, and it says which field is missing.

**One selection is enough, and one selection is what a unit carries.** The
fit needs the design selection — return and supply air, airflow, net sensible
capacity — and the water it was selected at. Nothing else. The worked unit
happens to carry seven of the manufacturer's selections, and all they do is
refine how the resistance divides between air and water: the same unit fitted
to its design selection alone answers within 0,1 K of it at 55 °C of return.
That is no longer presented as the normal case, because it is not: a unit
arrives with one selection.

**So the model says whose numbers it is answering on.** It extrapolates with
the same confidence either way, and the reader has to be able to tell the
difference, because one of the two is theirs to validate. Fitted to a single
selection it says so and names the assumption — "the air/water split assumed
at 78 %, the one number here nobody measured". Checked against more it says
how many and how closely — "reproduces 7 more of the manufacturer's to
0,026 K". Refusing to answer was the old way of handling the uncertainty;
naming it is the better one.

**Capacity above the nameplate is the heat exchanger, not a mistake.** A
430 kW unit at 55 °C of return really does transfer 916 kW: `Q = epsilon x
C_air x (T_return - T_water)` and the return is 20 K past the selection. What
was sized for 430 kW is everything around the coil. So the result now says
what the water would have to do — "that would take the water out at 39,2 °C
against the 28 °C of the selection" — and alerts when it exceeds the design
rise. Capping the capacity at the nameplate would have been the wrong repair:
the supply air temperature comes from the same model, so a capped capacity and
an uncapped temperature describe a machine that does not exist.

**What the model still does not know** is worth saying beside that number:
condensation, whether the chiller and the pump can hold the design flow at
that rise, and an air flow far from the one the fit was anchored at.

---

## ADR-064 — Units on a network run to the worst return their gallery sees

**Decision.** `fanwall.control: team` puts the units of one mechanical
gallery on a network: each runs to the worst return any of them sees, rather
than to its own. `independent` is the default and is what a unit with no
network does. The group is the gallery — a hall with a gallery at each end
has two networks that know nothing of each other — and it is read off the
geometry rather than from a list somebody has to keep in step with the
layout.

**What the shared reading buys is the valve.** A unit told that the room is
at 34 °C opens as far as a unit really at 34 °C has to, and then delivers
what *its* coil gives at *its* own return. On the worked unit a fan wall
seeing 24 °C of return goes from 26 % valve and 65 kW, holding the setpoint,
to 83 % and 140 kW delivering 19,5 °C. That is a unit that had stopped
working taking a share of the load, which is the whole point of the network.

**Capacity is reported on the air the unit really receives.** `air x (own
return - what it delivers)`. Working it out on the shared return would quote
heat the unit never moved, and the number would not tie back to the heat the
field shows it carrying. The coupled loop and the results page apply the same
chain, so the capacity reported matches the supply temperature imposed.

**A coil told to open against cold air still does not heat.** ADR-062 holds
here, and this is the arrangement that reaches it: a unit ordered to open by
a hot neighbour over-cools its own zone and, on the next pass, sees a return
below its setpoint. It delivers its own return and moves nothing, which is
what its own supply sensor would make it do.

**What it does not change is the airflow.** Networked control often stages or
equalises fan speed as well; this is the thermal setpoint only, and saying so
is cheaper than a reader assuming the fans moved too.

**The risk, named.** `max` is not smooth. The coupled loop converges because
the coil passes back only what its effectiveness leaves — a gain near 0,12 —
but a shared maximum couples every unit to one, and two units swapping places
as the worst between passes could chatter instead of settling. Nothing is
damped for now: the loop already measures what moved and warns when it does
not settle within its passes. A real case that chatters is the evidence that
would justify damping, and inventing it first would hide the very behaviour
worth seeing.

## ADR-065 — One selection is the whole requirement, and the page says so

**Decision.** A unit is described by its design selection. The reference
table under it is optional — zero rows, one row, or seven, all load — and
nothing in the parser, the page, or a message asks for a second selection.

**The rule had already changed; three places had not.** ADR-063 made the
coil recoverable from one selection and proved it: a unit carrying only
`selection` + `design` answers within 0,1 K of the seven-row CA80 at 55 °C.
What stayed behind was a completeness gate in `equipment.parse` that
demanded a design selection or else a pair of capacity rows, an equipment
page that titled the reference table "The selections" and locked its remove
button once two rows were left, and a P–Q chart that draws nothing below two
points. A user with one selection in hand read those three and concluded the
tool wanted two. Nothing was broken; the tool was saying the wrong thing,
which for an input form is the same defect.

**Completeness is the coil fit's business, not the parser's.** The parser
validates what is present — a row missing a column, two rows at the same
`return_c` — and refuses those, because a bad number reaches a report. What
is *absent* is reported where it can be named usefully: `CannotFit` says
which field it wanted, `coil_problem` carries that to the result, and the
unit still loads. A half-written unit has to be openable, or it cannot be
finished; refusing to parse it means the page that would let you fill in the
missing field will not open either.

**The page now leads with the design selection.** Its own card, five
editable fields, above a table renamed "Reference selections (optional)"
whose rows delete down to none. Adding a row starts from the design point
rather than from a row that may not exist.

**A single reference row is legitimate and is not interpolated.** The old
error — "one row cannot be interpolated" — described a table that was the
model. It no longer is: the coil is the model, and the table is evidence
against it. One row of evidence is less than seven, not invalid.

**What the user owns.** A new unit entered with one selection gets a coil
extrapolated from that point by the ε-NTU fit, and the report says so
(ADR-063). Validating that extrapolation is the engineer's, and saying it
plainly is cheaper than a gate that pretends the tool could check it.

## ADR-066 — A save that cannot write the field says so, and the page can add it

**Decision.** The equipment save path creates a key, and the block that holds
it, when the file does not have one. `set_scalar` reports `False` for a key it
cannot find; ignoring that return value was the defect. `Equipment.span` is
`None` for a unit that states no selection at all, so such a unit loads and
serves rather than raising `KeyError` out of the API.

**The symptom was the worst kind there is.** A user typed a design selection
into the page, clicked Save, was told "Saved", and on the next read the fields
were empty. Nothing errored. The file was never touched, because the surgical
YAML editing that keeps these files' comments intact can only replace a line
that exists, and a half-written unit is precisely the file whose lines do not.
The comment in `yamledit` even said so — "will simply not be found, and the
value will be left alone" — which is right for an editor and wrong for a save.

**ADR-065 was half done.** It made `parse` accept a unit with one selection or
none, on the grounds that a half-written unit has to be openable to be
finished. It did not check that such a unit could be *served* or *saved*. Three
paths still assumed a complete file: `span` indexed `design["return_c"]`,
`set_scalar` silently dropped every `design.*` field, and `replace_list` raised
`no capacity block to replace`. A rule is not adopted until the paths that
carry it are, and the way to find out is to drive the page as a user would.

**`set_or_add` writes at the end of the block**, after the keys already there
and the comments that belong to them, and falls back to `set_map` when the
block itself is absent. Nothing already in the file moves, which is the
property these files are edited this way to keep.

**An empty list stays absent.** Saving a unit with no reference rows does not
write an empty `capacity:` — there is still nothing to say, and an invented
empty block reads as a table somebody emptied on purpose.

**What is still not automatic, on purpose.** The case's `fanwall` block is not
filled from the unit. Naming a model adds the coil; every number typed in the
case still wins (ADR-036). A unit is a machine and a case is a project, and one
project runs a machine below its catalogue point often enough — N+2 sharing,
a de-rated selection — that copying the datasheet into the case would quietly
overwrite the number the engineer meant.

## ADR-067 — What the page edits, and what the capacity card draws

**Decision.** The equipment page's working copy carries every block the page
shows; `plant` creates a missing one rather than throwing. The capacity card
draws the **fitted coil**, not the reference table, and says why where there is
no coil. A reference row whose own numbers disagree is set aside by name and
does not block the fit.

**Three defects, one cause: the page was changed and its edges were not.**
ADR-065 put the design selection on the page. `clone` — which builds the draft
the page edits, key by key — was not given `design`. Typing into the new card
threw inside an input handler, where nothing shows it, so the value never
reached the draft, Save posted a draft with no design, the server had nothing
to write, and the page reported "Saved". The user typed five numbers, was told
they were saved, and watched them come back empty. Twice, because ADR-066 fixed
the server side of the same symptom and the browser side was still broken.

**`plant` now creates what is missing.** `keys.reduce((n, k) => (n[k] ??= {}))`.
The old form threw on the first absent block, and an exception in an input
handler is the quietest failure this codebase has: the page looks like it took
the value, and the value is nowhere. A field added to the form should at worst
be ignored, never take the form down in silence.

**The chart was the same mistake, from the same commit.** The card promises
"net sensible capacity against the air the unit receives" and plotted
`capacity`, bailing below two rows. Under ADR-065 that is exactly backwards:
the coil answers that question, the table is optional evidence, and most units
have none — so the card was blank for precisely the unit the page exists to
enter. It now plots the coil across ±10 K of its design selection, floored at
the entering water, with the design point marked and any reference rows on top.

**A blank card is the worst possible answer**, because it reads as a broken
page rather than as missing input. Where the coil cannot be fitted, the card
carries the reason — which field is absent, or which number does not close.

**A bad reference row is a bad row, not a bad unit.** One inconsistent row used
to raise out of `fit` and cost the unit its coil entirely. Reference rows are
evidence (ADR-039); they set the air/water split and then check the fit. A row
that does not agree with itself is excluded, named in full, and drawn hollow so
the picture and the message say the same thing. The design selection is held to
the old standard: that one *is* the model, and a wrong number there is not
evidence but a wrong model.

**What this found in real data.** The unit that produced the bug report states
108,986 m³/h from 38 to 24 °C at 390 kW. Those numbers carry 442 kW; 390 kW
wants a supply of 25.6 °C. Something in that row is a transcription — and the
page says so now instead of drawing nothing.

## ADR-068 — A selection that does not close names the field to look at

**Decision.** When a selection's capacity disagrees with its own airflow and
temperatures, the message gives the two numbers that would close it: the site
elevation, and the supply air at the stated elevation. Neither is ranked over
the other.

**The 3% tolerance is calibrated, not chosen.** The eight Vertiv selections
shipped in `equipment/CA80NPVG6.yaml` — seven reference rows and the design
point — agree with `airflow x density x cp x dT` to within **0.55%**, worst
case. A test asserts 1%. That is what makes a miss of several percent
meaningful: it is the manufacturer's own arithmetic that closes this tightly,
so a row that does not is a transcription, not the model's physics being
approximate.

**The field at fault is usually not the one the message named.** The first
real report of this was a CA40 selection: 60,504 m³/h from 37.0 to 21.8 °C,
stated 281 kW, computed 267. Every number in that sentence was right. What was
wrong was the *site elevation*, which is on a different card, was copied from
another unit, and never appears in the message. Those numbers close at 328 m;
the unit said 750. A message that names three correct fields and not the wrong
one sends the reader to re-check the datasheet they already read correctly.

**Where the arithmetic identifies the field, it says so.** A CWA datasheet
prints `Gross Sensible Cooling Capacity` directly above `NSCC`, and the two
differ by exactly the fan power the array returns to the air -- which the row
already carries. Where the stated figure less that power is what the
temperatures and airflow do carry, the gross figure was copied, and the message
says that instead of offering candidates. That is not a guess: it is the row
proving it against itself. The CA40NPVGT that prompted this had both fields
wrong at once -- 750 m for 661, and 280.7 kW for 270.1 -- and the gross/net
identification survives the elevation being wrong too, because the fan power
explains the gap at either.

**It has to explain the gap, not land near it.** As first shipped the branch
fired whenever the stated figure less the fan power came within the same 3%
tolerance -- which, on a unit whose fans are 7% of its capacity, almost any
near miss satisfies. It then told a reader that a sheet's net figure was its
gross when the net was right and the airflow was not. The test is now a
residual small in its own right (under 2% of the stated figure) AND much
smaller than the gap it accounts for (under a quarter of it). A wrong
diagnosis delivered confidently is worse than no diagnosis: it sends somebody
to change a number that was correct.

**Otherwise both candidates are given, unranked.** Solving for the elevation is
one bisection, and solving for the supply is arithmetic. Which of the two is the
transcription is the engineer's to know — the same miss reads as a copied
elevation on one unit and a rounded supply temperature on the next, and
guessing would send half of the readers to the wrong field with the tool's
confidence behind it.

**Where no elevation in 0–5,000 m closes it**, only the supply is offered. A
selection that far out is not a units or altitude question.

**The calibration, restated on a second unit.** The CA40NPVGT datasheet, at its
own 661 m: 60,504 m³/h from 37.0 to 21.8 °C computes 270.0 kW against a printed
NSCC of 270.1 -- **0.03%**. Two units, two independent selections, sub-percent
both times. That is the ground the 3% tolerance stands on, and both sheets are
now tests.

## ADR-069 — The water carries the gross duty

**Decision.** The design water flow is inferred by dividing the **gross** duty
— the net sensible capacity plus the fan power — by the water's rise, not the
net. `leaving_water_c` adds the fan power for the same reason. A selection that
states its water flow (`selection.water_flow_lh`) uses that number instead of
the inference.

**Two errors that cancelled.** The flow was inferred from the net, which
under-reads it by the fan power's share, and `leaving_water_c` was then fed the
net capacity, which under-reads the duty by exactly the same amount. Their
product was right at the design point, so nothing ever looked wrong. Both parts
were wrong, and neither could be checked against a datasheet — which is how a
pair of errors like this survives.

**What exposed it was a sheet that prints its water flow.** The Uniflair
FWCV36L2F gives 41,430 l/h against 20/30 °C water: 48.10 kW/K. The inference
from the net gave 45.29, 6% low. From the gross it gives 48.14. The Vertiv
CA40NPVGT settles it independently — it prints 402.76 l/min, which is 28.06
kW/K, against a gross-over-rise of 28.07. **0.04% apart.** That is what makes
the inference trustworthy on the units that do not print their flow, and the
stated field a refinement rather than a necessity.

**Physically it was never in doubt.** The coil hands its whole duty to the
water. The fan array then puts its power back into the air *downstream of the
coil*, which is why the net sensible figure a datasheet quotes is smaller than
what the water carried. Every sheet in this repository prints the two and their
difference is the fan power, to the decimal (ADR-068).

**No result moves.** Because the two errors cancelled exactly at the design
point, and every capacity is computed there, the CA80's capacities are
identical before and after — 323.5 / 431.4 / 539.2 / 647.0 kW at 30 / 34 / 38 /
42 °C. A test pins those four numbers. Off the design point the leaving water
now reads about 0.1 K lower at a heavily loaded unit, and — the part that was
plainly wrong — a unit carrying no cooling load no longer returns water at the
temperature it entered, because the fans are still running.

**Both sheets that print a leaving water temperature are now reproduced**:
30.01 °C against the Uniflair's 30.0, 28.03 against the CA80's 28.0.

## ADR-070 — Which "supply air" a sheet means, and how to tell

**Decision.** A unit's `design.supply_c` is the air leaving the **unit**, after
the fan array. Where a sheet prints the air off the coil instead, the fan heat
is added before the number is entered, and the file says so at the top.

**The two conventions look identical on paper.** Vertiv and Uniflair print
"Discharge air temperature off unit" and mean it: their airflow times that
temperature difference gives the NET capacity, and both close to 0.2%.
Springer Tech prints "Supply air Dry condition" and means the air off the
coil: 116,000 m³/h from 38.0 to 24.4 °C carries 461.7 kW, which is that
sheet's **gross** 462, not its net 436.2. Nothing on either sheet says which
one it is. The arithmetic does.

**The test is free and unambiguous.** Multiply the airflow by the temperature
difference. If it lands on the net figure, the number is the unit's discharge.
If it lands on the gross, it is the coil's, and the fan heat — the sheet's own
power input divided by the air's capacity rate, 0.76 K here — has to be added.
Where a sheet prints its water flow, that is a third independent check: 39.6
m³/h over a 10 K rise is 460 kW, the gross again (ADR-069).

**Taking it at face value is not a rounding error.** It would put air 0.76 K
colder than the unit really delivers onto the fan patch — the boundary
condition the whole solve hangs from — and quote a capacity of 462 kW where
the room receives 436. On a 14-unit hall that is 360 kW of cooling that does
not exist.

**Every shipped unit now reproduces its own leaving water temperature**, and a
test walks the whole library asserting it within 0.15 K. That check catches
both this and ADR-069's: a supply temperature read off the wrong side of the
fans, or a water flow taken from the net, both show up there.

## ADR-071 — Only sheets that close get into the library

**Decision.** A unit is admitted to `equipment/` only when its own numbers
agree with each other. Four checks, all enforced by a test that walks the whole
library:

1. **The air side closes on the net figure**, within 1%: airflow × density ×
   cp × ΔT is the net sensible capacity, at the sheet's own site elevation.
2. **A stated water flow carries the gross duty**, within 1%: flow × 4.18 ×
   the water's rise is the net plus the fan power (ADR-069).
3. **The unit reproduces its own leaving water temperature**, within 0.15 K.
4. **Every field a report will quote is present**: the five of the design
   selection, the four of the conditions, and three dimensions.

**A sheet that does not agree with itself cannot be made to by entering it
anyway.** Whatever is in this directory is real data behind somebody's report,
and a study is only as defensible as the numbers under it. Four sheets have
been turned away:

- **CM500W** — "Sensible 464" printed above "Total 432", which is impossible;
  the chilled water supply and return transposed; no fan power stated; and the
  air side 8.6% out even on the kindest reading of the two capacities.
- **Delta PAHV800** — a coil selection, not a unit: no fans, no external static
  pressure, no casing. Taken at elevation 0 m for a 661 m site, and its airflow
  is standard air, which is the 4% by which its air side misses while its water
  side closes to 0.05%.
- **FA126HC** — its net figure is right and provable twice over, and its air
  side still misses by 4.4%. Its airflow, its supply temperature or its
  elevation is wrong, and only its vendor knows which.
- **CA40NPVGT** — a good sheet entered at the wrong elevation, which is the
  same failure seen from the other side: 661 m makes it close to 0.03%.
- **Trane DFWA 5560 R3** — two revisions of the same model at identical stated
  conditions quote different capacities: R2 500.0/473.6 kW, R3 480.8/465.5.
  Both cannot be right. R3's total less its net is 15.3 kW against its own fan
  power of 25.2 or 28.8, where R2's 26.4 is at least near its 26.9. The
  engineer chose R2; R3 stays out.

**One unit is admitted on an interpretation, and declares it.** The Trane R2
sheet prints "Supply Air Temperature 24 °C" — the only temperature on it
without a decimal — and at face value its airflow and that temperature carry
456.9 kW against a stated net of 473.6, 3.5% out. Three readings were
possible:

1. the printed airflow and capacity stand and the supply was rounded, at
   23.55 °C;
2. the sheet's standard-air figure is the real mass flow (114,064 × 1.2 =
   38.02 kg/s), which closes to 0.8% but makes the true volume 129,853 m³/h;
3. the printed volume stands and the capacity is 456.9 kW.

Between (1) and (2) lay 184 kW across the project's 14 units, so it was not a
choice to make quietly. The engineer chose (1), on the evidence that vendors'
standard-air conversions are the least reliable field on these sheets: Vertiv
prints one on two sheets here implying densities of 1.03 and 0.94 kg/m³ for
air at 661 m, where it is 1.05.

**So `design.derived` names the fields a sheet did not print.** The air side
of such a unit closes by construction rather than by check, and
`_coil_provenance` says so in the same sentence as the capacity it decides:
*"Its selection did not state supply_c — that was read out of the sheet, and
the capacity here follows from it."* Without that, a report would quote it
beside five units that closed on their own arithmetic as though the evidence
were equal. A test asserts that every other shipped unit declares nothing.

**Its water flow is still not carried.** The sheet's 12.3 l/s is 2.2% above
what its own duty needs, where the four other sheets that state a flow agree
to within 0.5%. The inference from the gross stands instead (ADR-069), and the
question — a generously sized circuit, or a capacity 2% optimistic — goes back
to the vendor.

**What a rejected sheet gets instead is a question for its vendor**, naming the
field. That is the whole point of the consistency check (ADR-068): it is cheap
to ask, and a plant sized on a sheet that does not close is not.

**This is not a claim that the admitted units are right** — only that they are
self-consistent and that what the tool reports about them traces to a
manufacturer's own arithmetic. A coil fitted from one selection still carries
an extrapolation the engineer owns (ADR-063).

## ADR-072 — Room units are carried, and refused as fan walls

**Decision.** `equipment/` carries downflow CRAHs alongside fan walls, because
a coil is a coil and the model that recovers it from one selection does not
care how the box is installed. `arrangement: downflow` marks them, and
`equipment_for` refuses to build a fan wall from one.

**The geometry is the part that does not generalise.** This model builds a wall
of fans across a mechanical gallery, with a return plenum above a false ceiling
and a contained hot aisle. A downflow CRAH stands in the room and discharges
under a raised floor. Everything the library knows about such a unit — its
capacity against the air it receives, its water side, its P–Q curve — is
correct and useful; the one thing missing is where to put it.

**So the failure is made loud rather than left to be noticed.** Naming a room
unit as `fanwall.model` used to give a fan wall 3.11 m wide and 2.61 m tall
with a CRAH's coil in it: every number plausible, the room wrong, and nothing
saying so. That is the worst kind of wrong answer. It now raises, and the
message says what is right about the entry as well as what is missing, so
nobody deletes a good file thinking it is broken.

**Room units are admitted on the same evidence as everything else** (ADR-071).
Three are carried and all three close on their sheets' own arithmetic. Being
unplaceable is not being unverified, and when the downflow layout is built they
are ready.

**The conventions do not travel with the vendor.** Springer Tech's fan-wall
sheet (396FWA500) and its CRAH sheet (39CRA150) use the same template and the
same words — "Supply air Dry condition" — for two different things: on the fan
wall it is the air off the coil, and on the CRAH it is the unit's discharge.
The arithmetic says which every time (ADR-070) and nothing else does. Never
infer a convention from the manufacturer's name; multiply and check.

## ADR-073 — Direct-expansion units are carried, checked, and not modelled

**Decision.** `cooling: dx` marks a direct-expansion unit. Its selection is
carried in the library and held to the checks that apply to it; no coil is
fitted, and `equipment_for` refuses to build a fan wall from it.

**The coil this software fits is a chilled-water one** — an ε-NTU counterflow
exchanger recovered from a water flow and an entering water temperature
(ADR-063). A DX unit has neither. Its capacity follows the refrigerant
circuit, the compressor's speed and the outdoor air its condenser rejects
into; the sheet here states 38.8 °C of outdoor air for exactly that reason,
where no chilled-water sheet in this library states any. Fitting the
chilled-water model to it anyway would produce a capacity-against-return curve
with nothing behind it, and a report would quote it beside real ones.

**So the message says what is not modelled rather than what is missing.**
"Does not say what water it was selected at" would send somebody looking for a
field that cannot exist on a DX sheet. It now reads: *its capacity follows the
refrigerant circuit and the outdoor air its condenser rejects into, which this
software does not model. Its selection is carried and checked; what it does at
any other return air is not answered.*

**The admission rule splits by what removes the heat** (ADR-071). Every unit's
air side must close within 1% and every field a report quotes must be present.
A chilled-water unit must additionally fit a coil, reproduce its own leaving
water, and — where it states a flow — carry the gross duty. A DX unit must
state the outdoor air instead of the water temperatures.

**Which power nets the capacity is not obvious, and getting it wrong costs a
third.** The shipped CRAC absorbs 19.3 kW, of which 17.6 is the compressor and
1.8 the fans, plus 0.9 at the condenser outdoors. Only the **fans'** 1.8 kW
reaches the room's air; the rest leaves through the refrigerant. Netting the
53.5 kW gross with 19.3 would give 34.2 instead of the sheet's 51.7. Every
chilled-water sheet here nets with the whole unit input because on those the
whole input *is* fan power.

**Three of four CRAC sheets were turned away, and the fourth is the control
that proves it.** The Uniflair IDAV1911F and the Vertiv P1060DA quote the same
airflow (14,215 m³/h) at the same return (30.0 °C). The Uniflair says 17.9 °C
of supply and 51.7 kW, and closes to 0.07%. The Vertiv says 18.1 °C — *warmer*
— and 53.5 kW, which is more heat from less temperature difference at the same
mass flow. The three Vertiv PEX4 sheets miss by 4.65%, 5.02% and 5.32%, all in
the same direction, against sheets that themselves declare "performance
tolerance ±5%". That is systematic, and it is the vendor's, not this model's.

## ADR-074 — A row is a pattern of positions, not a count of cabinets

**Decision.** `racks.row` states a **typical row**: a list of positions, each a
cabinet or a blanking panel, each free to carry its own width and load. Every
row of the hall is built from it, and its length is the row's count. Beside it,
`racks.widths` and `racks.blanks` are where a single position disagrees — the
same division the load has always had between `racks.load_kw` and
`racks.loads` (ADR-054).

**A blanking panel is not a cabinet at zero load.** The zero-load cabinet is a
box that still breathes and still resists like the cabinets either side of it;
that is what ADR-054 established and it is still right. A blanking panel is a
**plate** where no cabinet stands, and no air crosses it at all. Both are real
and they say different things: the zero is a position nobody has racked yet,
the plate is the metres a row does not fill. The page says so in as many
words, because using one for the other is a wrong answer in either direction —
a plate where a zero belongs closes a path the real room leaves open, and a
zero where a plate belongs opens one the real room closes.

**The plate closes the cold-aisle face only.** Sealing both faces would leave a
pocket with no path to anywhere, and an isolated cell region is a pressure
solve with no reference in it. Open to the hot aisle it is dead air, which is
what the space behind a blanking panel is.

**The row is as long as its parts.** `count × size[0]` was right while every
position was the same width and wrong the moment one was not: the containment
and the row-end walls ran past the row's end, and the hall was sized from a
length no row had. Both layouts now accumulate the widths.

**Widths are snapped, not faces.** The general mesh snapper moves every face to
the nearest grid line. On a row of one width that is exact; on a row of several
it is not. Two identical 0.8 m cabinets on a 0.6 m cell came out **0.60 m and
1.20 m** — their edges fell on opposite sides of the same line and the error
walked down the row. Rounding the *width* instead makes every cabinet of a
width the same width, and lays the row out on grid lines by construction.

**And the warning is per width, not per cabinet.** A hall of 400 positions
built from one typical row repeated the same sentence forty times and buried
everything else. It is said once, with the x cell that would carry the width
exactly — a 25% loss on a cabinet's width is not a rounding a reader should
have to work out the cure for.

**Every position is a position, including the plates.** The page and the API
list and validate them all. Validating an edit against `model.racks` — which
holds only the cabinets — meant a position the case had just blanked read as
an unknown id, and un-blanking it from the page was impossible. That is the
kind of one-way door a form must never have.

## ADR-075 — A rack type is a product, and lives out of the case

**Decision.** `racks/<id>.yaml` holds one cabinet type — its width, depth,
height, its U count, what its document says it dissipates, and the document
itself. A case names one as its standard (`racks.type`) or per position
(`racks.row[i].type`). Anything the case states still wins, as everywhere else
(ADR-036).

**Because a cabinet is the same cabinet in every project that buys it.** Typing
600 × 1200 × 2200 into each case is how two halls of the same rack end up 5 mm
apart with nobody able to say which is right — and this project has exactly
that trap in it, documented: the Type-E RFP notes that the **605 mm** marked on
its drawings is 600 mm of rack body plus 5 mm of engineering tolerance. The
catalogue carries 605, because a row is laid out on the pitch and not on the
box, and says so in the file.

**Every type quotes its source.** A test asserts it. What a cabinet is has to
be traceable to the document it came from, or the next engineer has no way to
tell a measured number from a remembered one.

**A dimension the source does not state is left `null`.** The Vinhedo 03 layout
marks the 32 kW liquid network rack's depth and height "further confirmation",
so `liquid-network-800` states its 800 mm width and nothing else, and a case
naming it is refused by name. A plausible number in that field is a row that
does not fit with nothing saying so (ADR-071).

**A LIQUID CABINET'S RATED DUTY IS NOT ITS AIR LOAD.** The Type-E rack is
225 kW and leaves about 96% of it in the coolant, so what the room has to
remove is the other 4% — **9.0 kW, not 225**. Taking the rated figure would
make the hall look 25 times worse than it is; taking the hall's air standard
would be a number meant for a different machine.

**So the type carries the fraction, and the air load follows from it.** An
air-cooled type brings its `load_kw` as it stands; a liquid-cooled one brings
`load_kw × (1 − liquid_fraction)`. That is the source document's own
arithmetic, not an assumption, which is why it is a default rather than a
question — a position that really runs otherwise still states its own load and
that still wins. Only a liquid type stating **no** fraction is refused, because
there the gap between the two readings is 9 kW and 225 and nothing in the file
chooses between them.

**What the catalogue holds after this project's documents**: the three
air-cooled cabinets the Type-E RFP standardises (600×1200 45U, 800×1200 46U,
800×1200 48U at 12–16 kW), the Type-E liquid rack, and the four types the
Vinhedo 03 15+15 MW schedule adds — shuffle-box, ODF, the shallow 300 mm ODF
special, and the liquid network rack that is still missing two dimensions.

**The lease's Exhibit A-2 describes cabinets too, and they are types.** Its
maxima are the envelope of a real rack, not a clearance: the SUM3 high-density
GPU cabinet at 1800 × 1800 × 2600 and the low-density one at 1200 × 1800 ×
2600, both deployed modularly — the first as one 1800 mm unit or up to four
600 mm racks connected in place, the second as up to two. Three types carry
them, split by the duties the same exhibit gives: GPU 1,100–2,200 kW, low
density compute 34–54 kW, low density storage and network 14–34 kW. Each
carries the TOP of its range, because a cooling study sized on the bottom of a
range is the wrong error to make, and each says the range in its note.

**Low density is 100% air, high density is not, and the difference is in the
lease.** Low-density rows are provided with infrastructure for 100% air
cooling, so those cabinets' whole duty is what the room removes. High-density
rows carry both, and the lease gives the split **per colo** — up to 100% liquid
and 55% air on the first, 35% after — never per rack. So `sum3-high-density`
states no `liquid_fraction` and a position naming it is refused until it says
what reaches the air. At 2,200 kW that refusal is worth megawatts.

What is NOT in the catalogue is the exhibit's clearances — cold aisle 1800 mm
low density and 2438 mm high, hot aisle 2134 mm, perimeter 2438 mm. Those are a
case's aisles, and a rack type has nothing to say about them.

**A row has one depth and one height.** Only the width is a position's own: a
row is a single band across the hall and mixing depths inside it is geometry
this model does not build. A type named at a position contributes its width
and, where its other two differ from the row's, says so — silently dropping
them is how an 1800 mm cabinet ends up drawn 1200 deep. Named as `racks.type`
it sizes the whole row instead.

## ADR-076 — The raised floor, and the downflow unit that feeds it

**Decision.** `floor.enabled` puts the room on an access floor. Everything
already placed moves **up** by the floor's depth and the plenum is built
beneath it, so the room is the room it always was — same aisles, same rack
heights, same false ceiling, same return plenum — and the **building** is
taller. The fan wall becomes a downflow unit in the same place on the same
wall, and the air reaches the cold aisle through perforated plates in the
floor instead of through the wall.

**The loop, end to end.** Return plenum → the woven mesh above the false
ceiling → the mechanical gallery → **the unit's top face** → *(not modelled:
the machine)* → **the unit's bottom face** → the plenum under the gallery →
**the woven mesh below the deck** → the plenum under the hall → the plates →
the cold aisle → the cabinets → the hot aisle → the ceiling grilles → back.

**The opening below is the twin of the one above.** The return plenum already
opens into each gallery across the full width of the hall, closed with a woven
13 mm mesh (ADR-048). The supply opening is the same hole in the same wall at
the other end, closed the same way, and it carries the same component. The
plant pays for that mesh twice — once going up and once coming down — and
that is what the building really does.

**A plate is as wide as the cabinet in front of it and as deep as the floor
grid.** The count is what a case sets, not an area, because the floor is built
in plates and the engineer thinks in plates: two fills a 1.2 m aisle, three an
1.8 m one, one leaves the far half solid. `racks.tiles` is where a position
disagrees with the standard, exactly as `racks.loads` is for its load
(ADR-054). Zero is a cabinet fed only by what reaches it sideways, which is a
real thing to study and the reason the field exists.

**A downflow unit is two baffles, not one.** The fan wall is a single plane
doing both jobs: the air leaves the gallery through one side and arrives in
the hall through the other. Here the two faces are a storey apart, so each
plane carries one condition and a wall behind it — the top draws from the
gallery above and is a wall underneath, the bottom delivers into the plenum
below and is a wall on top. **Give both sides of both planes a condition and
the unit runs twice.** The same mass leaves the top as enters the bottom;
volume would not do, for the reason it never does.

**The body between the two faces is left open to the gallery.** Walling it
would seal a region with no path to anywhere and no pressure reference in it,
which is a solve that does not converge rather than a detail. The unit is
drawn and not meshed, exactly as the fan wall's depth already is (ADR-046).

**The orientation check is the same test turned on its side.** For a fan wall
it measures that the intake's outward normal points at the gallery along x.
For a downflow unit the faces are normal to z: the return's cells are the
gallery above it so its normal points down, the supply's are the plenum below
so its normal points up. Reversed, the unit draws from the plenum it is
filling — and it would still read as converged, which is why this is measured
and not assumed.

**The rack drop is measured across the RACK, not across everything under it.**
`rack_pressure_drop` sampled the whole column from the domain floor to the
rack top. On the slab those are the same room, which is why it read right for
years; on an access floor everything below the rack top includes the supply
plenum, whose pressure drives the entire loop. Averaged into both planes it
dragged the measured drop to **70%** of what the rack curve asks, and the
check reported the field as wrong when it was the sampling. Narrowed to the
cabinet's own z band it reads **101%**, and the cases on the slab are
unchanged to the decimal because there the two windows are identical.

**The plates are a grille like every other, and are checked like one.**
`floor_resistance` measures what the field drops across them against what
their K asks at design flow, and prints their face velocity beside it — the
same form as `grille_resistance` and `plenum_resistance`, from the same
`grille_pressure_drop` reading its own prefix. No limit is put on the
velocity, for the reason no limit was put on the plenum's: what is high for
one hall is ordinary in another (ADR-059). They enter the pressure budget too,
and on a raised floor **the mesh is counted twice** — the same opening exists
at both ends of the dividing wall, once above the false ceiling for the return
and once below the deck for the supply, and the air crosses both.

**The report says which architecture it is.** A downflow unit is not a fan
wall, and the reader is checking a drawing against the document: `report.md`
calls it a room unit and prints the plates beside the return grilles, the
sensor is "at the unit's return" rather than "behind the fan wall", and the
Word report's Geometry section describes the floor, the mesh below the deck
and the plates before anyone reaches a number. Calling it a fan wall would
send them to the wrong drawing.

**The two arrangements are exclusive, and so are the units.** A supply plenum
at the gallery wall (ADR-058, ADR-060) and a raised floor are two ways of
getting the same air from the same machines into the same aisles; a case
asking for both has not chosen, and is refused. Likewise the unit: a case with
a raised floor needs a `downflow` type and one without needs a `fanwall` one,
and naming the other is refused with what is right about the file as well as
what is wrong (ADR-072). This is where the three downflow room units already
in the library stop being unplaceable.

## ADR-077 — What the repository ships is declared, not discovered

**Decision.** `equipment.SHIPPED`, `racklib.SHIPPED` and `components.SHIPPED`
name the entries this repository ships. The library-wide guards walk those
lists; `available()` keeps listing whatever is in the folder, because that is
what a user's page is for.

**Because the two are not the same thing, and only one is ours to promise.**
The admission rule (ADR-071) is a statement about what is committed here: the
sheets behind it close, and a study quoting them traces to a manufacturer's
own arithmetic. A unit an engineer is halfway through entering on their own
machine is not that, and was never meant to be held to it.

**It broke a build, which is how it was found.** `docker compose build` runs
the suite, and `COPY . /app` brings the working copy's `equipment/` with it.
Two drafts in that folder — one 1.1% out on the air side, one 1.6% and 0.15 K
on its water — failed three library-wide guards and stopped the image. The
tool was telling an engineer that their own draft was not allowed to exist.

**THIS IS THE THIRD TIME A TEST HAS READ A USER'S FILES.** ADR-056 was the
cases, ADR-061 was their contents, and this is the libraries. The pattern each
time: a guard written to protect the repository's own data, pointed at a
directory the user also writes to. The lesson is not "add fixtures again" — it
is that **a guard must name what it guards**. A list of ids is one line per
entry, it is visible in review, and it cannot quietly grow to cover somebody
else's work.

**A test asserts every declared id has a file**, which is the half that can be
checked automatically. The other half — adding the id when adding the unit —
is one line in the same commit. And a test creates a deliberately broken draft
in the live folder and asserts the guards ignore it, so the next person to
point a guard at `available()` finds out immediately.

**What a user's own entry still gets is the page.** A unit that does not close
shows its problem where the engineer is working on it, with the field named
and the arithmetic shown (ADR-068). That is the right place for it: a draft is
supposed to be incomplete while it is being written, and the build is not the
thing that should say so.

## ADR-078 — The return path is judged on streams, not on probes

**Decision.** `return_path` compares two mixing-cup means: `aisle_exit_c`, the
air leaving the containment through the ceiling, and `return_temp_c`, the air
arriving at the fan intakes. The three point probes on the path — `hot_aisle`,
`plenum`, `fan_back` — are still sampled, still reported and still in the
places table; they no longer decide a verdict.

**Because a probe measures the cell it sits in, and a room of uneven load is
not one temperature.** Between the rack outlet and the fan intake nothing adds
or removes heat, so the *air* at every station is the same air. That statement
is about a stream. Three points estimate a stream only where the stream is
uniform. Put a 0 kW cabinet beside a 32 kW one and the aisle is twelve kelvin
apart across its own length, and which of those the probe lands in is an
accident of the layout.

**Two couplings made it worse than bad luck.** `hot_aisle` and `plenum` are the
SAME three columns at two heights, so they miss together; and a typical row is
replicated down the hall (ADR-074), so a column that lands on a cold cabinet
lands on a cold one in every row. The two places that agree with each other are
the two that share the mistake, and the odd one out is `fan_back` — the only
probe that sees air which has already mixed.

**It was found by a run that was right.** A POD of three 0 kW and three 32 kW
cabinets, mass balance 0.000%, energy closure 100%, settled at 0.20 K: every
other check passed and `return_path` failed at 1.54 K. The streams at the two
ends of that same field were 30.94 and 30.97 degC — 0.02 K apart. Nothing was
filling and nothing was leaking. Two stored results carry the same fingerprint:
`hall-hotrow-independent` failed at 1.83 K and `hall-hotrow-team` scraped a
pass at 1.46 K.

**The message said "-- still filling" whenever it failed.** That text was
appended unconditionally to every failure, so it never carried information: it
was a guess printed in the voice of a measurement, and it sent an engineer off
to run more iterations on a field that had already converged. The failing
message now names the two things it could be and points at the two checks that
tell them apart — `energy_closure` for a leak, `settled` for a volume still
filling — and neither claim is made without evidence.

**The measurement takes nothing on faith about where the grilles are.**
`aisle_exit_temperature` reads the cell layer immediately below the false
ceiling, over the hot aisles, inside the hall, weighted by the mass flux
leaving through it. A cell under solid ceiling carries no upward flux and
weighs nothing; a cell under a grille weighs what it passes. The hall bound
matters: a mechanical gallery has no false ceiling, so that height is room air
there and counting it would mix the supply side into the return.

**What the probes are for now is what they were always good at.** Their scatter
is the room's own unevenness — a real finding, and the one an engineer with a
half-populated row wants to see. Where they disagree by more than the check's
own tolerance the passing message says so in a clause, so a reader comparing
the places table against the verdict is told why the two differ instead of
concluding that one of them is broken.

## ADR-079 — Four stations, measured as streams

**Decision.** The four point-probe places — `cold_aisle`, `hot_aisle`,
`plenum`, `fan_back`, three probes each — are gone. In their place are four
STATIONS on the air loop, in the order the air passes them: `supply`,
`rack_intake`, `aisle_exit`, `unit_return`. A station names a surface the whole
airflow crosses and reports the mixing-cup temperature of the air crossing it,
the range that air spans, the volume it carries and the velocity through it.
Every written time is still recorded, so a running solve still shows the loop
filling.

**Because a place was never a temperature.** ADR-078 removed the probes from
the one check that judged them; this removes the instrument. A probe measures
the cell it sits in, and that is a fine thing to know and a poor thing to
average: on a row of 0 kW and 32 kW cabinets the air under the ceiling runs
from 20 to 37 degC, and a mean of three points in it describes air that never
existed. What the next component of the loop receives is the mixing cup, and
the mixing cup is what the rest of the model is built on.

**The range is the finding, not the error bar.** Rack intakes 20,1 to 20,6 degC
say the containment is holding. An aisle exit spanning 20,0 to 36,7 says the
row is half empty — which it is, and which is exactly what an engineer laying
out a half-populated hall wants to see. It is on the report table, on the page
and shaded behind each line on both charts.

**One flow through every station.** The first version reported the cabinets'
RATED airflow at `rack_intake` and the row appeared to move 51 541 m3/h against
26 000 delivered. The rated figure is real -- it sizes the resistance -- but it
is not what the row passes, and a station table whose columns disagree is worse
than no table. A test asserts every station reports the same flow.

**Three things kept the probes honest and are kept.** They travelled with the
geometry, they never sat inside a rack, and they were drawn on the section so
the placement could be argued about before the run. None of that is needed by a
station: it has no coordinates to get wrong. A test asserts a `Station` carries
none, because the temptation to add "where" to a measurement that does not need
it is exactly how this started.

**`settled` is now measured on the streams**, which are far steadier than three
probes in a recirculating room -- the same field that drifted 0,19 K on the
probes drifts 0,01 K on the stations. `STEADY_TOLERANCE` is unchanged at
0,25 K, which is therefore now conservative rather than tight. Re-calibrating
it means re-running the worked cases and is not done here.

## ADR-080 — A pressure jump has the sign the air gives it

**Decision.** `grille_pressure_drop` takes the direction of flow from `phi` and
reports the drop from the upstream face to the downstream one. It no longer
assumes the `_below` patch of a cyclic pair is upstream.

**Because on a hall with a gallery at each end, half the surfaces face the
other way.** `createBaffles` hands the master patch to the face's owner cell,
which for an x-normal face is the cell at lower x. The first gallery discharges
towards higher x and the second towards lower x, so `_below` is upstream on one
supply mesh and downstream on the other. Read the same way, one drop came out
positive and the other negative, and the flow-weighted mean of +0,4 and -0,4 Pa
is nothing at all.

**It reported -133% on a hall whose meshes were both working.** `-0.41 Pa across
the supply grilles where their K asks for 0.31 Pa` is not a plant fault and not
a field that has not filled; it is two correct measurements added with the wrong
sign. A single-gallery POD never showed it, because there is only one of each
surface and it always faces the same way.

**Negative is still reported where it is real.** The sign now follows the air,
not the patch name, so a surface the air crosses against its own pressure rise
-- which is a finding -- still reads negative. What is no longer possible is a
surface reading negative because of which side of it OpenFOAM called the owner.

## ADR-081 — A blanking panel is a wall in the mesh, not only on the drawing

**Decision.** `blank` joins `WALL_GROUPS`, so `createBaffles` builds a blanking
panel as the two-sided internal wall the model always said it was.

**Because it was in the model, on the drawing and in the summary, and it was
not in the mesh.** ADR-074 introduced the blanking panel as "a solid plate that
passes no air". `model.panels` carried it, `drawing.js` drew it, the row
summary counted it -- and `wall_plan` never listed it, so `topoSet` built no
face zone and `createBaffles` built no baffle. The solver saw an open hole
exactly where the plate is.

**Measured, on the user's hall.** One blank in every fifteen positions, sixteen
rows. At the blank's own column the field ran **3,83 m3/s out into the hot aisle
at 2,84 m/s** -- 28% of what the whole row of fourteen cabinets passed, through
one position of fifteen. The row then delivered **11,4 Pa where its curve asks
21,1 (54%)**, because an open path in parallel with the cabinets halves the
row's resistance. Take the blanks out of the same hall and the same check reads
105%. The user found it by doing exactly that.

**What a failing check is for.** `rack_resistance` did its job: it said the row
was not delivering the resistance it was given, and it said any fan pressure
taken from that field is wrong. It was believed to be a measurement artefact
for one session, on the strength of the row being half empty, and it was not --
it was the geometry. A resistance check that disagrees with the curve by half
is not a sampling question until the mesh has been looked at.

**The space behind the plate belongs to the hot aisle.** The panel is one wall
on the cabinets' own face, so the volume behind it is a dead pocket open to the
contained aisle -- which is what it is in a real row. With the cold side sealed
there is no pressure driving anything through it: the lateral flow through the
neighbouring cabinets measures 0,06 m3/s, against the 3,83 that used to come
straight through.

**A test asserts every wall panel the model builds is in a face zone**, so the
next surface added to the model cannot be drawn without being meshed.

**And it walks a declared list, not the folder.** The first version globbed
`cases/*.yaml` -- which is the engineer's working folder, where they keep the
rooms they are actually studying. That is the FOURTH time a guard written to
protect this repository's own data was pointed at a directory the user also
writes to (ADR-056, ADR-061, ADR-077, this). It was caught here before it
shipped, by noticing a stray case in `git status`; the previous three were
caught by a broken `docker build`. `support.SHIPPED_CASES` names the nine
cases this repository ships, a test asserts each has a file, and a stranger's
case sitting in `cases/` is now none of the suite's business.

## ADR-082 — A resistance is judged against the flow it actually gets

**Decision.** `flow_spread` measures `mean(u^2) / mean(u)^2` across a family of
perforated surfaces, and `_resistance_verdict` judges the field drop against
`asked * spread`. The rated figure stays in the sentence; the verdict is taken
against the closed form for this field.

**Because a quadratic resistance costs what the LOCAL velocity says.** A grille
costs `K rho u^2 / 2` face by face, so what it really costs is proportional to
`mean(u^2)`, which is never below `mean(u)^2`. The spec sizes it on the second,
because at the drawing board the air is assumed spread. Both numbers are right;
dividing one by the other measures the distribution, not the resistance.

**It read 283% and looked like an instrument fault.** A 1,2 m supply plenum fed
by three discrete fan walls delivers 2,9 times the mean flux opposite a unit and
a fraction of it in between; `mean(u^2)/mean(u)^2` came to 2,06 and the mesh
cost twice its rated face velocity. The mesh was doing exactly what its K says.
The PLENUM was not spreading the air, which is a finding about the plant --
deepen it, or give it a diffuser -- and it now appears in that sentence instead
of as a percentage that reads like a bug.

**The denominator is the NET, not the mean of magnitudes.** Those are the same
number only while nothing crosses backwards, and the surface this was written
for is crossed backwards: **9,7% of the mass that passes the supply mesh passes
it INTO the plenum**, because a 1,2 m cavity fed by three discrete fan walls
jets through opposite each unit and draws back in between them. Dividing by the
mean magnitude quietly credited the surface for its own return flow and left
38% of the measured drop unexplained -- 0,88 Pa against 0,64. With the net, the
mesh's own K asks 0,90 Pa of that field and the measurement is 2% under it.

**Two faults, reported apart.** `flow_spread` says the air does not arrive
evenly and `reverse_fraction` says some of it is going round in circles, and
they want different remedies -- a deeper plenum or a diffuser for the first,
the units aimed or spaced differently for the second. The drop alone was never
going to say which, which is why it read 284% of a rating and looked like an
instrument fault.

**It cannot excuse a surface that is not delivering.** The correction is for
the flow, not for the resistance: half the drop under the same distribution is
still half the drop, and a test asserts it fails. Nor does it loosen a surface
that is fed properly -- on the same hall the ceiling grilles and the plenum
opening measure 1,06 and 1,02 either way.

## ADR-083 — The rack page does not fight the engineer

**Three faults, all in the way the page handled being typed into.**

**A number no longer redraws the page.** Every keystroke in the typical row
called `render()`, which rewrites the whole layout -- so the field being typed
into was destroyed and rebuilt under the caret and the page jumped to the top.
Typing "13,2" was four fights with the scrollbar. Only a change of KIND changes
which cells exist (a plate has no load cell), so only that redraws; a figure
updates the draft and the read-outs that follow from it.

**The positions table answers with the pattern.** `current`, `widthOf` and
`isBlank` fell back to the standard cabinet the moment a position stopped
stating its own figure. So `Make every row exactly this` dropped the
exceptions and then showed every position at the standard, and what the button
had actually done only appeared after a save and a reload. They fall back to
the typical row now, so the table follows the pattern live.

**And the save no longer freezes the old pattern.** Every position was sent
with the figure the server last computed, which for anything the pattern had
set was not the standard -- so it was written back as an explicit exception,
and editing the typical row changed nothing for those positions. An untouched
position is sent as null now, "whatever the typical row says", unless the case
stated it as an exception in the first place. `blank_stated` joins the payload
for the same reason: a panel the pattern places is not a panel the position
asked for.

**Both separators are accepted, a dot is written.** The fields were
`type="number"`, and a browser set to a comma locale reads `0,8` in one as the
empty string -- so the figure an engineer typed was dropped on the keystroke.
They are text fields with a decimal keypad now, and `web/decimal.js` turns what
was typed into a number. Tolerant coming in, a dot going out.

**Every form goes through it.** The same trap was in `equipment.js` -- the
design-selection fields and the P-Q curve, where an engineer copies a
datasheet -- and in `components.js`, where a free area typed as `62,5` left
the K read-out at "--" because `Number("62,5")` is NaN. Leaving one page
tolerant and another silently swallowing the number would have been worse than
either. Three tests hold it: no form carries a `type="number"`, every decimal
field asks for a decimal keypad, and every form imports the one helper. When
the separator is settled across the tool it changes in one file.

## ADR-084 — `applied` is settled by the solver, not by a list of names

**Decision.** Whether a component's numbers reach the solver is asserted
against the MODEL: build a case that uses the role, and check that the
component's own K is the K the panels carry. The list of names in
`test_what_the_solver_does_not_read_yet_says_so` stays as a cheap sanity check
and is no longer the guard.

**Because the list went stale and then defended the staleness.**
`components/floor-tile-600.yaml` said `applied: false # the raised floor
itself is not modelled yet`, and the page therefore told an engineer looking
at a raised-floor case that their floor plates were **NOT IN THE SOLVER**.
They were: ADR-076 shipped the deck, the plates as `tile_*` porous baffles,
the mesh below the deck and a `floor_resistance` check measuring them. The
model's `floor_tile_k` is 3,350 and the component's `k` is 3,350 -- the same
number, because one comes from the other.

**The test that should have caught it was the reason it survived.** It read
`for name in ("containment-panel", "floor-tile-600", "pdu-distribution-loss"):
assertFalse(applied)`. A list of names asserts what somebody believed when they
wrote it. Shipping the feature did not touch that line, so the suite went on
certifying the old world, and the only symptom was a sentence on a page that
nobody re-read. **A flag that describes the code must be checked against the
code.**

**The other two are still true, and the tests say why.** Containment panels
are `kind="wall"` in the mesh -- solid -- so the panel's free area is genuinely
unread. Nothing but the racks heats the room, so the distribution loss share is
unread too. Both are asserted from the model as well, which is what stops the
guard being satisfied by marking everything applied.

**What the engineer saw.** A card reading `Raised floor plates · 54 % free ·
K 3,35 · NOT IN THE SOLVER`, on a case whose solver was using exactly that
3,35. The number and the label were arguing in front of the reader, and the
label was the one that was wrong.

## ADR-085 — A row starts on a cell face, and its widths are then left alone

**Decision.** `_make_row` snaps the row's ORIGIN to the grid before laying the
cabinets out. Every width is already a whole number of cells (ADR-075), so
from a snapped origin every face lands on a cell face by construction and
`snap_to_mesh` has nothing left to move.

**Because snapping face by face redistributes the widths.** `snap_to_mesh`
moves each box face to the nearest cell face, one face at a time. A row is
built by accumulating widths from its block's origin, so if that origin sits
half a cell off the grid, EVERY cumulative face sits on a rounding tie and
each one falls whichever way the floating-point arithmetic takes it.

**Measured, on a hall an engineer was looking at.** The first block began at
12,5 m on a 0,2 m grid -- 12,5 / 0,2 = 62,5 exactly, a tie at every step. Its
typical row of nine 0,60 m cabinets and six 0,80 m ones came out as

    0,80 · 0,40 · 0,60 ×6 · 0,80 ×7     = 10,40 m

with a **0,40 m cabinet no specification ever asked for**, and ten centimetres
too long. The second block began at 25,0 m, landed on the grid, and came out
exactly right:

    0,60 ×9 · 0,80 ×6                   = 10,20 m

One hall, one typical row, two different rows on the two sides of it. The
engineer saw it on the plan and asked whether the mesh had done that. It had,
and the drawing was telling the truth.

**This is ADR-075 finished.** That decision moved the snapping from the faces
to the WIDTH, so two identical cabinets could not come out 0,60 and 1,20. It
did not touch `snap_to_mesh`, which went on snapping every face afterwards and
undoing the guarantee wherever the row did not start on the grid. Snapping a
width is only half of it: a row also has to START somewhere the grid agrees
with.

**The blocks stay consistent with each other.** `_make_row` returns its end,
and the next block is placed from it, so snapping the origin carries down the
hall instead of being re-rounded at every block.

**A test builds a hall of mixed widths whose first block is deliberately off
the grid** and asserts one distinct row across all of them, no cabinet at a
width nobody asked for, every face on a cell face, and the row the length its
pattern says. Without the fix it fails seven times.

**The same fault was in the hall's WIDTH, and it was worse.** The width is a
chain too -- perimeter, row, hot aisle, row, cold aisle, row, ... -- and it was
laid out from unsnapped parts and rounded afterwards, one boundary at a time.
On a 0,3 m grid a 2,2 m hot aisle cannot be exact, so it has to move; what it
must not do is move DIFFERENTLY in different pods. It did: three pods of the
same hall came out at 2,1 m and the fourth at 2,4 m, so one contained aisle had
**14% more chimney cross-section** than its neighbours, and nothing in the
output said so. Each band is now snapped once, before the chain is laid out, so
a figure the grid cannot carry is carried the same way everywhere. A band the
grid CAN carry is left alone -- 2,4 m is eight cells of 0,3 and stays 2,4.

**And a block is now what its rows turned out to be.** `model.blocks` was
snapped on its own, so after the rows were fixed the two still disagreed: the
rows measured 10,20 m and the block went on saying 10,40. The plan dimensioned
a row two hundred millimetres longer than the row, and `chimney_area` measured
a contained aisle that long. Every row in a block shares a span, so the rows
are the answer.

**One rule, three places it was broken:** snap the quantity, then accumulate.
Never accumulate, then snap.

## ADR-086 — The plan is dimensioned like a layout drawing

**Decision.** The plan carries band chains on both axes: along x the gallery,
the supply plenum, the row and the cross aisle; along y the row depth, the hot
aisle, the cold aisle and the fan wall. Each distinct part is dimensioned ONCE,
where it first occurs. Every cabinet carries its name and its load, written
inside it. The two sections are untouched, and have their own margins so this
cannot move them.

**Because a hall repeats.** Four pods, sixteen rows, the same aisle between
each pair: dimensioning every instance would put seventeen figures down one
margin and say nothing the first four do not. Measuring each part where it
first appears and letting the reader carry it along the repeat is how a layout
drawing has always been read, and it is what keeps the sheet legible.

**The cabinet labels are sized from the drawn rectangle**, not fixed, so they
fit whatever the sheet scale turned out to be -- small on a hall of 224
cabinets, comfortable on a POD of three -- and below 1,4 px they are dropped,
because a name too small to read is a smudge that hides the rectangle under it.
An engineer who wants to read them zooms, which is what they asked for.

**The chains found the geometry faults of ADR-085.** The engineer saw two
different rows on the two sides of one hall, on the plan, and asked. A drawing
that dimensions what it draws is a test that runs every time somebody looks.

## ADR-087 — The page says which case it is looking at

**Decision.** Every call the page makes carries `?case=`, taken from its own
address, and every route prefers it over the case the server was started with.
The top bar carries a case menu: open one, start one from the starter or from
a copy, paste one in, copy this one out. Switching reloads.

**Because `aicfd view --case X` said which case the page OPENS on, and the
server then answered every request with X whatever the page asked.** So
`?case=other` changed the address bar and nothing else, and the only way to
look at a second room was to stop the server and start it again. Opening,
starting and importing a case were terminal-only -- `aicfd new`, or a query
typed by hand -- and the page is the interface (ADR-005).

**Switching reloads, deliberately.** Every card's state comes from the server
already, so rebuilding it in place would be a second way of doing what a
reload does correctly, and a second way is a second thing to keep right.

**A pasted case is built before it is written (ADR-055).** Saving first and
validating afterwards is how the page once lost itself: a spec the generator
refuses is a spec every reload fails on, so the form that could have undone it
never loaded again. A paste that cannot be drawn is refused in the menu, and
the message says that nothing was saved -- because the engineer's next
question is whether they now have a file to go and delete.

**The name is checked, not trusted.** It reaches the filesystem, so it is
letters, digits, `-` and `_`, and nothing that could walk out of `cases/`.

## ADR-088 — A key's value may be a list at the key's own indent

**Decision.** `set_map` and `replace_list` treat a list sitting at its key's
own indentation as part of that key's block, and an item that continues onto
further lines as one item.

**Because YAML allows it and `yaml.safe_dump` writes it:**

    racks:
      row:
      - width: 0.6
      - width: 0.6
        load_kw: 0

The editor took "the block under a key" to mean "the lines indented deeper
than the key", which those items are not. Removing the block therefore took
the key and **left the list**, and the orphans read as a list item where a key
was expected. `_write_row_block` rewrites the typical row by removing it and
writing it again, so the removal was half of every save -- and **no case with
a typical row could be saved at all**. The rack page answered

    ParserError: ... line 17: per_row: 15 ^ expected <block end>,
    but found '-' ... line 202: - width: 0.6

**Nothing was corrupted, and that is not luck.** `_save_case_racks` parses the
text it built before it writes it and refuses when the rack block did not
survive the edit (ADR-048). The save failed loudly and the file on disk stayed
exactly as it was. A save path that can corrupt a file corrupts it before
anything reads it back, which is why that guard exists; this is the second
time it has earned its place.

**`find` was made to accept a bare key too.** `  row:` ends at the colon, and
the guard only accepted a space or a tab after it -- so a caller that split the
file with `splitlines(True)` got a newline there and was told the key was
absent. The server splits on `"\\n"` and never hit it, so this was latent
rather than live; it is fixed because the next caller would have.

## ADR-089 — The report describes this run, and is emitted for every result

**Decision.** What the report says about the model is computed from the model,
not written down once. The mesh verdict, the limitations, the perforated
surfaces, the control mode and the rack distribution all follow the case. And
a report is produced for every result that exists, including one exported by
an earlier version of the tool.

**Because a sentence written once goes stale in place, and a report is read as
a measurement.** Three of them had:

* **"One load per rack. Unloaded positions and a real per-rack load map are
  not represented."** ADR-054 shipped the load map, ADR-054 the zero-load
  cabinet and ADR-074 the blanking panel. The report told an engineer their
  layout was not represented while the solver was using it. Gone, and the
  modelling section now says what a blanking panel and a zero-load cabinet
  each are, because those are two different things and the case says which
  each position is.
* **"One rack per cell in plan … 1 to 2 K of uncertainty."** Printed for every
  mesh. A hall run at 0,10 × 0,20 m has **six cells across a 0,6 m cabinet and
  six through its depth**, and the sentence told its reader the opposite. The
  cells per cabinet are counted now and the verdict follows them.
* **"No comparison against measurement … it cannot promise the built room
  behaves this way."** True of every CFD study ever written, which makes it a
  property of the method and not a finding of this one. Deleted.

**A limitation is asked of the code.** The PDU losses and the containment
leakage are printed while `components` says the solver does not read them, and
would go when it does -- which is checked against the library rather than
against a memory.

**Three things the report was silent about and an engineer has to have.** The
perforated surfaces it modelled, with the K that decides each; how the units
are controlled, because a networked plant and eight independent ones give
different per-unit capacity from the same room (ADR-064); and the rack
distribution -- how many positions, how many carry nothing, how many are
plates, what widths -- beside the plant it is cooled by.

**The physics is stated, not named.** Each surface in the boundary-condition
table now carries the relation it imposes: the Darcy-Forchheimer sink with the
whole of a cabinet's drop in the inertial term, and the cyclic pair's
`Δp = ½·K·ρ·u_n²` applied face by face -- which is why a surface the air
reaches unevenly costs more than its rated face velocity says.

**Figures A, B and C are the room, dimensioned**, and a detail of C names every
cabinet and its load. Each distinct part is dimensioned once where it first
occurs, because a hall repeats (ADR-086). They sit in the basis of design,
where a reader checks the study against a layout drawing.

**Two stored halls could not produce a report at all.** Their coil payload
predates `design_return_c` and the section indexed it rather than asking for
it. A result is written once and read for years; a generator that raises on a
field an older export lacks cannot produce the document for a study somebody
is holding, which is the one thing it must never do. A test now walks every
tracked result and builds it. That costs about eighty seconds of the suite,
and it is the guarantee the deliverable needs: whatever the case used -- a
raised floor, a supply plenum, a mesh leaf, networked units, a typical row
with blanks -- the document comes out.

---

## ADR-090 — A case is authored from documents, and the manual for it is tested

**Decision.** `docs/CASE-AUTHORING.md` is the procedure for turning a real
project — drawings, a mechanical specification, a fan wall selection, a rack
schedule — into one `cases/<name>.yaml`. It is written for an agent doing that
work, and `tests/test_case_authoring.py` holds it to the code: every key it
documents is one the software reads, every key the software offers is
documented, the ranges match, the check names match, its YAML fragments parse
and are accepted by the readers they demonstrate, and every library file it
names exists.

**Why the manual.** The schema is small and the translation is not. A case
whose numbers are all plausible, all in range and all from the wrong document
builds a clean mesh, solves, passes twelve checks and answers a question about
a building that does not exist. Nothing downstream can catch that, because
every stage after the spec is faithful to the spec. The only place it can be
caught is where the number is read off the document, so that is where the
guidance has to be — and it has to say which document, not just which key.

Three things the manual carries that no amount of reading the code supplies:

- **what is derived rather than stated.** A hall's length and width are a chain
  of clearances, rack widths and pod counts; `hall.size` on a case with `pods:`
  is silently ignored. An author who does not know that types a size, sees a
  hall of another size, and has no way to connect the two. The manual gives the
  arithmetic in both directions, because reading it backwards is how a measured
  building becomes inputs.
- **the three kinds of number.** Measured, selected, or assumed — and the
  comment says which. This is the repository's existing practice, visible in
  every shipped case, and it was nowhere written down.
- **what a FAIL says about the inputs.** The checks test the model, so a red
  `rack_resistance` is usually a surface that was never built rather than a bad
  solve. An author who reads it as a solver problem tunes the solver.

**Why it is tested rather than maintained by hand.** A human reading a stale
manual notices: the key is not in the file they are editing, the range does not
match the error they just got, and they go and look. An agent has nothing to
check it against. It writes what the manual said, the build refuses the case,
and the repository has instructed it to do the wrong thing. That is worse than
shipping no manual, and it is the failure mode of every agent-facing document
that is not executable.

So the manual's reference tables are machine-readable — a row is a backticked
dotted path, a type, a range and a source — and the test parses them. A key
added to `EDITABLE` fails the suite until it is documented; a key retired from
the software fails it until the row goes. The prose is not parsed, so the test
is about facts and never about wording.

**Consequence.** The list of keys the page does not offer — rack types, the
per-position overrides, the fan curve, the coupling controls — lives in the
test as `BEYOND_THE_FORM`, and that list can rot like any other (ADR-056,
ADR-084). A further check reads `aicfd/` and fails when a name on it is no
longer read anywhere, so the escape hatch cannot quietly become the stale part.

---

## ADR-091 — The case travels with the reader, on every hop

**Decision.** Which case a page is looking at is kept on every link out of it,
every query string it rewrites and every call whose answer depends on it. One
module, `web/case.js`, does all three, and every page imports it.

**What was wrong.** ADR-087 made the API calls carry the case, so the server
stopped answering with whichever one `aicfd view` was started on. It did not
make the NAVIGATION carry it. The case lives in `location.search`, and three
separate things threw it away:

- **a link between pages.** `href="./"` on the four back links, and
  `href="./racks.html"` and `href="./components.html"` out of the model page.
- **a page rewriting its own query string.** `location.search = "?model=X"` on
  the equipment page replaces the whole search, case included.
- **a fetch that never said which case.** `web/racks.js` called `/api/racks`
  bare, for both the read and the write.

**Why the third one is the serious one.** With the server on `pod-fanwall` and
`hall-10mw` open on the model page, clicking through to the racks page loaded
`pod-fanwall`'s racks; changing the standard load to 13.75 kW and pressing Save
wrote it into `cases/pod-fanwall.yaml` and left `hall-10mw.yaml` untouched.
Measured, both ways, on a copy of the repository. The page did exactly what it
said it was doing; the only clue was a case name in a header nobody reads
twice. A page that SHOWS the wrong room is a nuisance. A page that SAVES to it
is a corruption that announces nothing.

The case menu had the same fault more visibly: `/api/cases` marks which case is
open, so the menu ticked the server's start case while the page was on another.

**Why one module rather than five fixes.** A back link is not a special case of
this — it is the same hop the other way, and so is a group link, and so is the
page that rewrites its own address. Patching the four back links would have
left the racks page still saving to the wrong file. `case.js` states the rule
once: a link to a page of this tool carries the case unless it names one of its
own. It rewrites the anchors present at load and listens in the capture phase
for the ones a template builds later, and it rewrites `href` rather than
intercepting the navigation, so ctrl-click, middle-click and "copy link" all
carry the case too. `decimal.js` was made for the same reason, one separator
ago (ADR-083).

**Consequence.** The guard reads the case-scoped endpoints out of the router
rather than listing them: a route that starts using `self._case()` is covered
the day it does. Three checks — every page imports `case.js`, no page replaces
its query string without the case, every case-scoped fetch names one — and each
was shown to fail on the code as it was.

---

## ADR-092 — Which machine cools the room is a choice on the page

**Decision.** `fanwall.model` is an editable field, offered as a picker in the
Fan walls group, listing every unit in `equipment/` — including the ones that
do not suit this room, each saying why. One function, `equipment_mismatch`,
decides whether a unit fits; the generator refuses on it and the picker quotes
it.

**What was wrong.** The group showed the unit's name and linked to its
datasheet, and that was all. `fanwall.model` was not in `EDITABLE`, so it could
not be set from the page and a hand-made request for it was rejected as an
unknown field. The only way to put another machine in a case was to edit the
YAML.

The path a reader actually takes makes it worse than a missing field. The
equipment page has a model picker, so somebody who wants a CRAH goes there,
finds `39CRA150`, selects it — and that picker changes which unit's DATASHEET
is on screen, not which unit the case uses. They go back to the model page and
find the same fan wall, unchanged and unchangeable. Everything worked; nothing
they wanted happened.

**Why the unusable units are listed rather than hidden.** Somebody looking for
their CRAH on a hall with no raised floor learns, from a picker it is missing
from, only that the software has never heard of it. Listed with the reason,
they learn that the model exists, that it is a downflow machine, and that the
room needs a raised floor to take it — which is the answer to the question they
were really asking.

**Why they are not disabled either.** Ticking `floor.enabled` and choosing a
downflow unit is one edit made of two fields. The Apply carries both, and the
generator judges the pair after every field has landed (ADR-055) — so the
combined edit is accepted, and it is the only way the change can be made at
all. A picker that greys out the CRAH until the floor is saved would force two
Applies, the first of which is a hall with a raised floor and a fan wall in it.

**Why one rule with two readers.** The picker states a verdict and the
generator enforces one. Two copies drift, and the drift a reader meets is a
picker offering a machine the Apply then refuses — or, worse, marking one
unusable that would have worked. The first version of the picker worked the
reason out for itself from `arrangement` and got it backwards, telling the
reader a downflow CRAH "needs no raised floor". So `equipment_mismatch` is the
rule, `equipment_for` raises what it returns, and the page shows the same
string. A test puts every unit in the library against every shipped case — 90
pairs, 51 suitable and 39 refused — and fails when the two disagree about any
of them, or give different reasons.

**Consequence.** A comment that restates the value beside it goes stale the
moment the value is editable: `model: CA80NPVG6  # the unit, as
equipment/CA80NPVG6.yaml holds it` became a line naming a file the case no
longer used. The shipped comments say `its file in equipment/` instead. The
general lesson holds for any field the page can write.

---

## ADR-093 — A skill that leaves this repository carries what it needs

**Decision.** `.claude/skills/case-authoring/` is the case-authoring manual
shaped as a skill: `SKILL.md` is the procedure and the triggers,
`reference/CASE-AUTHORING.md` is a copy of `docs/CASE-AUTHORING.md`, and a
test holds the copy byte for byte against the original.

**Why a copy rather than a reference.** A skill is uploaded. Once it is, it has
no repository behind it: a `SKILL.md` that sends the reader to `docs/` sends
them nowhere, and the failure is silent — the agent writes a case from the
procedure alone, without the key reference, the ranges or the traps. So the
skill ships the manual.

**Why the copy is tested rather than trusted.** `docs/CASE-AUTHORING.md` is
held to the code (ADR-090); the copy inside the skill is not, and cannot be —
it is read by something that has never seen this repository. The only thing
that can keep it honest is being the same file, so that is what is checked, and
the failure message gives the one command that fixes it.

**Why `SKILL.md` is not just the manual again.** A skill is a procedure with
triggers; a manual is a reference. `SKILL.md` carries what changes the shape of
the work — the order to extract in, the derived-geometry trap that sends an
author to the wrong rack count, and the questions a drawing cannot answer that
have to be ASKED rather than assumed — and points into the reference for
everything else. Restating the manual there would be a third copy to keep.

**What this found.** `datacenter-cfd`'s frontmatter did not parse: `Trigger on:
"CFD do datacenter", ...` is a YAML mapping where a string was meant, so a
strict loader rejects the whole block and the skill never triggers. Nothing in
this repository reads a skill, so nothing failed — the symptom was a skill that
quietly did not exist.

**And what the upload found that the guard had not.** `case-authoring`'s first
description said it would write `cases/<name>.yaml`, and the upload was refused
outright: *SKILL.md description cannot contain XML tags*. The placeholder
convention every shell uses is markup to a validator that is looking for
markup. A guard only catches what it checks, which is the whole argument for
adding to it every time something gets past it: the description is now held to
carrying no angle brackets at all, rather than to a list of the ones that are
allowed, because they have no other job in a sentence. The file uses `NAME`
throughout, as the `aicfd` skill beside it already did.

---

## ADR-094 — A change of unit brings that unit's numbers with it

**Decision.** Picking a unit on the model page fills the fan wall fields with
that machine's datasheet at once, and a change of `fanwall.model` drops the
previous unit's figures so the build refills them from the new one. The
mapping from a unit to those fields is stated once, in `equipment_defaults`.

**What was wrong.** `equipment_for` fills only the keys a case leaves blank —
the library is a default, not a lock, and a value typed over it is a decision
that stays visible (ADR-036). That is right for a case somebody wrote by hand.
It became wrong the moment the unit could be CHANGED from the page (ADR-092),
because by then every key is already filled with the previous machine's
figures: naming another unit changed the name and nothing else.

Measured on a real hall, `FOR-META`, after its author picked the CRAH:

| | the case said | the 39CRA150 is | where it came from |
|---|---|---|---|
| airflow | 121,545 m³/h | 33,700 | CA80NPVG6 |
| capacity | 432.6 kW | 145.0 | CA80NPVG6 |
| power | 21.4 kW | 6.0 | CA80NPVG6 |
| width | 3.96 m | 2.73 | CA80NPVG6 |
| supply | 21.9 °C | 23.2 | CA80NPVG6 |
| ESP | 100 Pa | 150 | CA80NPVG6 |

Four machines rated at three times what they are, under the right name, and
the summary reporting 211% of the load covered where the truth is 71%. Every
check would have passed on it. This is the exact failure the project exists to
prevent — plausible numbers, in range, from the wrong document — arriving
through a feature added to help.

**Why both halves.** The page fills the fields so the reader SEES the machine
they picked before pressing Apply; that is what was asked for, and it means
the form then sends the right numbers. The server drops the stale keys anyway,
because the page is not the only client and because `curve` has no field —
a stale P-Q curve is the previous machine's fan, invisibly.

**What is kept.** A key the request states wins: switching unit and typing a
figure in the same Apply is one edit and the typed figure is the statement.
And re-applying the SAME unit keeps everything, which is not a nicety — the
form sends `fan_model` on every Apply, so clearing on every send would wipe an
override the first time anything else was saved.

---

## ADR-095 — Two planes that nothing checked: the plate and the return

**Decision.** Floor plates that would be laid twice over the same floor are
refused, by name, with the two ways out. And `snap_to_mesh` snaps a downflow
unit's `return_z` like every other plane.

**How they were found.** The same hall would not mesh, twice over.

**The plates.** `floor.tiles_per_rack` lays N plates outward from each
cabinet's face. Two rows face the same cold aisle from opposite sides, so an
aisle of width W holds W/depth rows of plate BETWEEN them — not that many for
each of them. A 1,2 m aisle with two 0,6 m plates a rack is fully floored by
either row alone, and 90 of the hall's 240 plates were built on top of each
other. Nothing in the geometry said so: they were built, counted, drawn, and
the failure arrived four steps later out of OpenFOAM as

```
createBaffles exited 1 ... Face 39400 already in faceZone 55
```

a mesh face index and a zone number, about a hall somebody had just spent an
hour describing. It is refused now where the two rows and the width of the
aisle they share are still in hand, and the message gives both fixes — a
plate count that fits, and the aisle width that would take the count asked
for. A test takes each of those two suggestions and builds it, because a
refusal naming a fix nobody can take is half a refusal.

**The check runs AFTER snapping**, which is not a detail: 60 footprints
collided before the snap and 90 after it, so the same check run on the
unsnapped geometry would have passed thirty of them straight through.

**The return plane.** `snap_to_mesh` moved every panel's `position` and
`extent` and left `return_z` — the second plane a downflow unit has, its top
face a storey above its bottom one — exactly where the spec put it. A 2,87 m
unit on a 1,0 m floor returns at 3,87 m, which is not on a 0,25 m grid.
`topoSet` takes the faces inside a box a quarter of a cell thick around each
plane, found none, and every `fanNIntake` patch was built with `nFaces 0`.
`createBaffles` was happy. The orientation check then read a face one past the
end of the mesh and raised `IndexError: no face 433534`.

That check is the only reason this was ever seen. Without it the solve would
have run a plant that returns nothing at all.

The panel-snapping code carries a comment warning about exactly this — that
rebuilding a Panel field by field loses the fields added later, which is why
it uses `replace`. `replace` carried `return_z` across faithfully, and
unsnapped. So the guard is no longer the two planes somebody remembered: every
coordinate a panel carries is checked against the grid, by name, on every
shipped case.

---

## ADR-096 — A customer cage is mesh or drywall, and the two are different rooms

**Decision.** `cage:` encloses the rows in a boundary inside the hall, built
one of two ways. `mesh` is a porous surface carrying the woven mesh's loss
coefficient; `drywall` is a wall. A drywall cage closed at the top is refused.

**Why it is a field and not a drawing note.** A plan shows the same rectangle
whichever way the cage is built, and the answer is not the same. The air
crossing a mesh cage pays K twice — once entering on the cold side, once
leaving on the hot one. The air meeting a drywall cage does not cross it at
all and goes over the top instead. Measured on the shipped POD with a cage
round its row, 400 iterations, everything else identical:

| | mesh | drywall, 2,6 m, open above |
|---|---|---|
| fan rise | 31,9 Pa | 33,0 Pa (+3,5%) |
| peak speed | 1,79 m/s | 1,98 m/s (+10,4%) |

Both pass all eleven checks. Neither is a rounding, and nothing about the
drawing tells them apart.

**Why a drywall cage closed at the top is refused.** Its walls are solid and
the supply is outside them, while the ceiling return grilles over its own hot
aisles still let air OUT into the plenum. That is a volume with an exit and no
entry: there is no steady solution, and the solver does not say so politely —
four ranks died on a floating point exception about nine minutes in, having
meshed and decomposed perfectly. The refusal names the two ways a real one is
built: below the ceiling with the top open, so the air passes over, or as
mesh. A drywall cage with a door or a duct through it is real and is not
modelled yet, and the message says that too.

**One name, two kinds of surface.** A cage panel is `cage_*` either way, and
that made two older lessons bite at once:

- `wall_plan` grouped every panel matching a prefix, so a mesh cage went into
  a wall zone AND into `porous()` — the same face in two zones, which is
  exactly what `createBaffles` refuses (ADR-095). The group now excludes
  anything that resists the air, which is the invariant rather than a
  special case.
- a zone shares a normal, and a cage has two — two walls across the rows and
  two along them. One zone for all four failed an assertion deep in the
  generator. Each drywall wall is its own zone now, the way each fan wall is.
- `sealed_envelope` tells them apart by the patch pair `createBaffles` makes:
  a porous surface is `_below`/`_above` and a wall is `_master`/`_slave`.
  Excusing the `cage_` prefix would have excused a drywall leak, which is the
  one thing that check exists to find.

**Consequence.** `cage.clearance` has to be less than `aisles.perimeter`, or
the cage wall lands on the hall wall and the cage is the room rather than a
boundary in it. That is refused with the gap on each side and the clearance
that would fit — the smaller of the two gaps, not half the leftover, because
rows are rarely centred in a room.

---

## ADR-097 — A direct-expansion unit runs, at its rated point

**Decision.** `equipment_for` no longer refuses a DX unit. The case builds, the
coupled re-solve is skipped because there is nothing to re-ask, and the result
is the room at the plant's RATED duty. Three places say so: an alert off the
build, the coil problem in the export, and — after the solve — the return the
room produced against the return the unit was rated at.

**What was too wide about ADR-073.** It said a DX unit is "carried, checked,
and not modelled", and refused to build a case from one. The reasoning was
about the COIL, and it is still right: capacity against return air follows the
refrigerant circuit, the compressors' staging and the outdoor air the
condenser rejects into, and the ε-NTU chilled-water exchanger this software
fits has no equivalent for it. But a ROOM needs none of that. It needs the
airflow, the supply temperature, the dimensions and the sensible capacity at
the rated return — all of which a DX sheet states as plainly as any other.

Refusing the whole unit for the sake of one property left entire sites
unmodellable. Ascenty Fortaleza is direct expansion throughout: six data halls
of Emerson P3100DA and Stulz ASD 1112 AU, self-contained, downflow into an
inter-floor plenum. Nothing about that room is beyond this software except the
one curve nobody asked it for.

**Everything downstream already handled it.** `supply_temperatures` returns
empty for a unit with no coil; `solve_coupled` falls back to a plain solve;
`coil_capacity` returns the problem instead of a fit; the report prints it.
The change is one branch removed and the consequences made loud.

**Loud, because a rated point is not a duty.** The first DX case run here
rates 51.7 kW at 30.0 °C return, and the room returns 21.8 °C — 8.2 K below
the plate, where a DX circuit does markedly less sensible work than its plate
says. A reader should not have to find those two numbers and subtract them, so
the result does it and names the number to ask the manufacturer about.

**A project unit is not a shipped one.** `equipment/P3100DA.yaml` carries the
Fortaleza CRAC from its data sheet and is deliberately **not** in
`equipment.SHIPPED`: the sheet states no external static pressure, and every
unit this repository guarantees must state what a report will quote (ADR-071).
It is usable in a case today and declarable the moment Emerson supplies the
figure. That is what `equipment/` being the engineer's folder is for (ADR-077),
and the file says so at the top rather than leaving the omission to be found.

**One reporting fault fell out of the same case.** The build summary printed
`normal x ... y ...` for every unit, including a downflow one whose faces are
horizontal — so it showed the x range of a z-normal face under the label y,
and five units correctly spread down two galleries read as three stacked in
one place and two in another. A reader checking the placement was shown a room
that does not exist. It now prints a downflow unit as what it is: horizontal,
with the height it supplies at, the height it returns at, and its footprint.

---

## ADR-098 — A cage goes where the drawing puts it, and the DX note is a limitation

**Two decisions from one review of a real plan.**

**The cage is placeable.** `cage.pods` says which PODs are inside — a
contiguous run, counted from 1 along the hall — and `cage.sides` says which
walls to build. Everything else follows:

| | keys | |
|---|---|---|
| middle of the hall | *(neither)* | four walls round every rack |
| over some rows only | `pods: [1, 2]` | four walls; the rest of the hall is outside |
| against the wall, dividing the hall | `pods: [1, 2]`, `sides: [right]` | one wall spanning the room |

The first version enclosed every rack and refused any clearance that reached
the hall wall. That is one arrangement of three, and not the common one: the
Fortaleza plan shows a cage occupying the bottom of the data hall with more
racks above it, closed by the room on three sides and by one wall of its own.

**A side the room closes is not built, and the rectangle is CLIPPED to the
room.** The clipping is the half that matters. Without it the wall that IS the
cage stops at the enclosed racks plus the clearance, so a case asking for a
partition across a hall gets one with a gap at each end that nothing asked
for — and the mesh builds it, and the drawing shows it, and it reads as a
detail rather than a mistake. A dropped side is said in a warning, because a
wall nobody knows is missing is worse than one refused.

**A cage wall may not cut a cabinet.** With `pods` the wall stands in the aisle
beside the pods it encloses, and too big a clearance walks it into the next
pod's rows. That meshes, and models a partition through the middle of
somebody's cabinets. Refused, naming the cabinet.

**The direct-expansion note moved out of the alerts.** It arrived there twice
over on every DX run — off the build and again off the coil — as three
paragraphs in the card headed *design criteria; these warn, they do not block
a run*. It is not a design criterion, the plant does not fail it, and it does
not change from run to run: it is a **limitation of the model**, so it is read
once, in the report's limitations, where every other one is (ADR-089).

What stays in the alerts is the one line that IS about this run: the return the
room produced against the return the unit was rated at, subtracted. On the
Fortaleza hall that reads *the room returns 27.5 °C and P3100DA is rated
100.5 kW at 30.0 °C — 2.5 K below it*, which is a fact about this result and
not boilerplate. An unfinished unit file still warns every time, because there
the capacity beside it came from somewhere a reader cannot check.

**Consequence.** `cage.pods` and `cage.sides` are lists, and the page's field
casters take scalars, so placement is a YAML edit until the cage group grows a
control for it. The manual says so rather than leaving a reader to find out.

---

## ADR-099 — A cage wall stands in an aisle, and that aisle gets a width of its own

**Decision.** A cage boundary that runs along the hall stands in a cold aisle,
and that aisle is set by `cage.aisle`, not by `aisles.cold`. `cage.clearance`
says where in it the wall sits, measured from the enclosed rows' faces. Only
the one or two pod boundaries a cage wall occupies take `cage.aisle`; every
other boundary keeps the aisle the hall was drawn with.

**Why.** The wall was put on the boundary and the aisle was left as drawn, so
it halved it. Measured on `hall-cage-1mw` as it first built: **0,60 m from the
wall to F4 and 0,60 m to F5**, inside a 1,20 m aisle. Both rows breathe from
that aisle — the one inside the cage and the one outside — and 0,60 m in front
of a row of cabinets is a different room from 1,20 m. Nothing said so: the case
built, meshed and solved, and the first thing to notice was somebody looking at
the drawing. The hall grows by the extra width instead, which is what a real
project does.

**Two keys, because they answer different questions.** `cage.aisle` is how much
room there is; `cage.clearance` is who gets it. 2,40 m of aisle with 1,20 m of
clearance centres the wall and leaves both rows their full 1,20 m; raising the
clearance gives the cage more and the hall less, without changing the total.

**What the run says.** Where the wall leaves a row less cold aisle than
`aisles.cold` draws, the summary names the aisle, both gaps, both rows and the
two keys that fix it — and says nothing at all when the aisle is wide enough to
hold the wall, because a paragraph on every run is the fault this repository
has already had to undo once (ADR-098). A clearance that puts the wall inside a
cabinet is refused outright, naming the rack and how much clearance to lose.

---

## ADR-100 — A cold aisle is closed with a lid, not a chimney

**Decision.** `containment.aisle` is `hot` (the default, and what this tool has
always built) or `cold`. They are different rooms and different geometry:

| | what is built | the return grille | needs |
|---|---|---|---|
| `hot` | walls from the rack tops to the false ceiling, doors at each end — a chimney | at the top of the chimney | nothing |
| `cold` | a lid over the cold aisle at rack height, doors up to it | over the **hot** aisle, now open to the room | a raised floor |

**Why not the same shape.** Building the cold one as a chimney would join the
cold aisle to the return plenum, which is the opposite of containing it. A
cold aisle is capped: the cold air stays in it, the racks discharge into the
room, and the ROOM is the hot volume — so the ceiling return grille belongs
over the hot aisle, which is where this generator already put it. The one key
carries the whole arrangement.

**Why `cold` needs a raised floor.** A contained cold aisle is sealed by the
rack rows, a lid and two doors. Its only way in is the floor. A supply blown
into the room outside it cannot reach it, and the racks would draw from a
closed box — so `cold` without `floor.enabled` is refused, naming both the
reason and the two ways out.

**THE LID WAS BUILT AND NOT MESHED, and that is the fourth time.** It was in
the model, on the drawing, in the summary, and absent from `WALL_GROUPS`, so
`createBaffles` never made it. Measured on the first run: the rows delivered
**4%** of their rated resistance and the warmest rack inlet read **47.0 °C,
outside every ASHRAE allowable envelope**. With the lid meshed, the same case
gives 98% and 20.6 °C, inside the recommended envelope. The same fault took
3.83 m³/s out of one blanking panel two months ago (ADR-081).

Every time, the model was right and the generator's list of prefixes had not
been told. A list is what goes stale, so the fifth name is not the fix:
`tests/test_case_files.py` now checks the two against each other. Every panel
the model builds must end up in a wall zone, in `porous()`, as a hole in a
wall, or as a fan — over every shipped case, in both containment
arrangements. Shown to fail on the lid with `WALL_GROUPS` as it was.

**Consequence.** The summary row said "Hot aisle chimney" whatever the case
closed, and quoted the ceiling height for a lid that stops at the racks. It
follows the arrangement now, and `chimney_area` with it — for a lid the
number is the thing's own footprint, because the air does not rise through a
lid, it leaves sideways through the racks.

---

## ADR-101 — The spectrum is offered, and it is not the default

**Decision.** Every field map, the per-rack map and the report figures can be
painted in a blue–cyan–green–yellow–orange–red spectrum. It is a picker on the
results page (`Colours`), `--colours spectrum` on `aicfd report`, and a `ramp`
sent with the report request so the document matches the screen it was asked
for from. The ramp each field asks for stays the default: diverging for
temperature and pressure, sequential for speed (ADR-009).

**Why offer it at all.** That sequence is what every commercial post-processor
prints, so it is what a client sets this study beside. A result nobody can
compare with the consultant's report is worth less than one they can, and the
comparison happens in whatever colours the other document is in. This is the
reader's decision, not the tool's.

**What it costs, plainly.** The hue sequence is not perceptually even: the
cyan/green edge reads as a step the data does not have, and a smooth wash of it
invents structure. For a red–green colour-blind reader — roughly one man in
twelve — the middle of the ramp collapses. Both are real, and both are why it
is not the default.

**Why the cost is bearable here.** Every scale in this tool is banded
(ADR-024), and a contour scale is read off the bar rather than judged by eye: a
colour is looked up, not estimated. The bands, the 10–40 °C domain, the pinned
centre and the ASHRAE marks are the field's whatever ramp is painted over them.

**The invariant.** Picking a ramp changes WHICH COLOURS, never what they
encode. `temperature_scale(ramp)` and `fitted_scale(..., ramp=...)` change only
the `colours` list; `min`, `max`, `step`, `center`, `edges` and `marks` come
out identical, and `tests/test_palette.py` asserts exactly that. So two readers
holding the same plot in different colours are holding the same numbers.

**One choice, everywhere.** The key lives in `web/colormaps.js` and a `window`
event carries a change to the other cards, because the field maps in the
spectrum beside a per-rack map still in blue is worse than either alone — and
the results page sends it with the report cover, so the Word document does not
disagree with the screen.

**Not repainted.** The categorical series colours (`palette.SERIES`) and the
convergence chart: those name things, and identity is not a magnitude.

---

## ADR-102 — A drawing calls the machines what they are, and shows the cage

**Decision.** `model.unit_naming` decides what this room's machines are called
— `fan wall`, `CRAC` or `CRAH` — from the unit's arrangement and how it makes
cold. It travels in the viewer payload, and everything that speaks to a reader
takes the word from there: the page's drawings and KPI tiles, the report's
figures, tables and prose, and the model summary. Each machine carries a tag,
`CRAC-01` onwards, and the same tag appears on every figure. The customer cage
is drawn — violet, dashed for mesh and solid for drywall — on the page and in
the report. Each rack row is named on the plan with the cabinets it carries.
The three dimensioned drawings (Figures A, B and C) are removed.

**Why the name.** A hall cooled by five direct-expansion room units had `fan
wall` printed on every drawing, on the KPI tile, in the model summary and
through the report's prose. It is wrong in the one place a reader checks the
model against the room they know, and it was wrong because each drawing
decided the word for itself — the noun was a literal in eight files. A CRAC
and a CRAH differ by more than a letter, too: one makes its own cold with a
refrigerant circuit, the other is fed chilled water, and the report says
different things about the two.

**Why the cage was invisible.** `cage_*` panels were in the model, meshed,
solved and named in the summary, and no figure drew them: the report's panel
lists named the containment and the plenum and stopped there, and the page
drew them in the containment green, where they read as an aisle wall. A reader
checking a study against a layout drawing looked for the room's most visible
feature and did not find it.

**Why the rows are named.** A hall plan is rows of identical grey boxes.
Without a name on each one a reader cannot say which row a hot spot is in or
count what is in it — the two questions that figure is looked at for. Naming
every CABINET is the detail figure's job; at hall scale those labels come out
smaller than the lines of the drawing.

**Why Figures A, B and C are gone.** They were a section, a section and a plan,
each with a chain of dimensions down two margins. Equal aspect and rotated
chain labels put the drawing in one corner with the figures scattered across
the empty two thirds, nowhere near what they measured; on a 23 × 15 m hall the
room took a third of the frame and the dimension text overlapped the title. A
drawing nobody can measure is not a layout drawing. What replaced them is
nothing: the model page draws the same room to scale, with the cut where the
reader put it, and the report keeps the detail figure — every cabinet, its name
and its load — which is the part that was working.

**Cost accepted.** The report no longer carries a dimensioned drawing at all.
That is a real loss for a reader with only the document, and it is smaller than
the loss of four pages of drawings that cannot be read. A dimensioned figure
worth printing is a separate piece of work.

---

## ADR-103 — A direct-expansion coil is modelled, from the psychrometry of its selection

**Decision.** A DX unit's evaporator is fitted like any other coil and asked
what it does at the return the room produces. The refrigerant boils, so it
holds its temperature and the counterflow relation collapses to

    epsilon = 1 - exp(-NTU),   Q = epsilon · C_air · (T_return - ADP)

measured from the coil's **apparatus dew point** instead of an entering water
temperature. The compressors take the valve's place: they modulate to hold the
supply setpoint until there is nothing left to give. DX cases therefore join
the coupled solve, and `fanwall.control: team` changes their answer as it does
for a chilled-water plant (ADR-064). ADR-073 and the coil half of ADR-097 are
superseded.

**Why it was refused before, and why that was wrong.** The old position was
that a DX unit's capacity "follows the refrigerant circuit and the outdoor air
its condenser rejects into, which is not modelled" — so every DX case ran at
its plate figure, uncoupled, and the report told the reader to *ask the
manufacturer for its capacity at 26,4 °C*. That is the one thing a model of the
machine exists to avoid. And it conflated two different sides of the machine:
the air side of a DX evaporator is the same finned bank as a chilled-water
coil's, and it is exactly as knowable from a selection.

**Where the surface temperature comes from.** The selection's own split between
sensible and total capacity. The process line from the air entering to the air
leaving the coil, extended to saturation, is the ADP. On the P3100DA's sheet:
110,3 kW total against 109,4 kW sensible — 0,8 % latent — puts the ADP at
**10,5 °C**, just under the 10,6 °C dew point of the air entering. That is the
physical statement a nearly-dry coil makes, and it is what a precision unit
selected on sensible heat is.

**What it answers.** The same unit, rated 100,5 kW at 30 °C return:

| return air | net sensible | supply air |
|---|---|---|
| 30,0 °C (its rating) | 100,5 kW | 18,7 °C |
| 28,0 °C | 89,9 kW | 18,0 °C |
| 26,4 °C | 81,1 kW | 17,4 °C |
| 24,0 °C | 68,0 kW | 16,5 °C |

A room returning 26,4 °C gets **81 %** of the plate figure out of the machine.
The alert says that now, with the number, instead of naming a phone call.

**What is still not modelled, and is now said precisely.** The CONDENSING
side. Capacity follows the outdoor air the condenser rejects into, and one
selection cannot say how much; the result holds it at the selection's own
ambient (37,6 °C here), which is the design day and therefore the conservative
end. The compressors are taken as modulating continuously — a two-scroll
machine cycles about this — and the ADP is held where the selection put it,
while a real fixed-capacity compressor drops its suction pressure at part load
and gives slightly more. Both approximations make the ceiling conservative,
which is the right direction for a capacity a plant is sized on.

**What a thin sheet costs.** Where a selection prints no total beside its
sensible capacity, the coil is taken as dry; on the one sheet here that prints
both, that assumption is worth 0,12 K of coil surface and 0,6 % of capacity.
Where it prints no gross capacity, gross is net plus the stated fan power. Each
assumption is carried on the coil, listed in the report's limitations, and
named in the summary line beside the capacity it decides (ADR-071).

**Consequence for the team control.** A DX plant could not be run as a network
before, because nothing re-solved its supply: `control: team` changed the case
file and not the answer. It does now.

**Consequence for `saturated`.** A unit told to work harder for a worse-placed
peer delivers air COLDER than the setpoint; it was being counted as one that
cannot hold it, so a whole team read as failing and the report said every
temperature was optimistic where they were pessimistic. Saturation is now the
unit's own: its supply above the setpoint it was given.

---

## ADR-104 — The cage wall costs a clearance on both sides, and divides what it crosses

**Decision.** Three things a reader found on one drawing, all of them the cage
wall not being treated as a wall with two sides:

1. **`cage.clearance` is a gap on BOTH sides.** The aisle a cage boundary
   stands in is `max(aisles.cold, 2 × clearance)` by default, so the row
   inside the cage and the row outside it each keep the clearance.
   `cage.aisle` still overrides it for an asymmetric split.
2. **A cage wall may not jump a row.** Landing inside a cabinet was already
   refused; a wall that clears the next row's cabinets and stops in the aisle
   beyond them now is too, naming the row it passed.
3. **A contained COLD aisle the wall stands in is two aisles.** The lid and
   both doors are cut at the wall, each side gets its own, and the cage wall
   stays what the case says it is.

**Why the clearance.** `cage.clearance` is the distance from a cabinet's face
to the cage wall, and the cabinets outside the cage have faces too. It was
applied to the enclosed rows only: the wall was placed at the clearance from
them, and whatever was left of a 1,20 m aisle — nothing — went to the hall's
row. The drawing showed the hall's cabinets hard against the partition. The
hall grows by what the wall costs, which is what a real project does.

**Why the jump.** A stated `cage.aisle` narrower than the clearance asks for
walks the wall past the next row entirely: 3,50 m of clearance in a 1,20 m
aisle put it two rows away, with a row of somebody else's cabinets inside the
cage rectangle and no wall between. The only complaint was a snapping note.

**Why the division.** With the hot aisle contained this never came up: a cage
boundary stands in a COLD aisle, and a cold aisle was open. Contain the cold
aisle (ADR-100) and the two meet — one lid and one pair of doors were built
over the whole aisle, straight across the cage wall, so the model held a
single contained volume spanning a security boundary that the drawing showed
dividing it. For a mesh cage that volume is not even closed: the air crosses
the wall and pays K for it, twice (ADR-096). A cage wall is not an aisle
closure. Each half is now its own contained aisle — its row, its lid, its two
doors and the cage wall — and the summary says so.

**And the rows' own ends.** The same drawing showed a row dimensioned 10,40 m
over cabinets that stopped at 10,00. A case that overrides one position's
width (`racks.widths`) makes that row a different length from its neighbours,
because a 300 mm frame on a 0,40 m grid is not 300 mm of row. The pair's ends
were taken from whichever row was built last, so a row's own end wall was
built at its neighbour's end, and the ceiling grille and the containment over
the aisle were sized from one row for two. Each row now carries its own end
wall at its own end; what the pair SHARES covers the longer of the two,
because a lid that stops where the shorter row stops leaves the other row's
last cabinet outside the containment; and the build says which two rows differ
and by how much.

---

## ADR-105 — The plan dimensions the data hall, and measures the cabinets once

**Decision.** On the plan, the overall dimension is the **data hall** —
`hall.lo` to `hall.hi` on the axis being measured — and both axes carry it in
the band chain as well. The galleries keep their own bands. The `row` band is
gone: what is dimensioned along the hall is `racks`, the span the cabinets
actually occupy, once per distinct length. Sections still dimension the
domain, which is what they are sections of.

**Why.** The overall was the modelled DOMAIN: gallery plus hall plus the
gallery at the other end, one figure that is neither room. A reader checking
the model against a layout drawing has the hall's length and width on that
drawing and could not find either here.

**Why the row band went.** `model.blocks` is what the rows turned out to be
(ADR-085), so a hall whose rows differ in length carries one span each —
`10,40 row` and `10,00 row` printed down the same margin, with the cabinets
visibly stopping before the longer one. The racks are drawn; the chain now
measures them rather than the band they were asked to fill, which is the same
number when the rows agree and the truth when they do not (ADR-104).

---

## ADR-106 — The ceiling is counted in its own modules, like the floor

**Decision.** A case can count the 600 mm return grilles over each contained
aisle: `grilles.across` modules across it and `grilles.along` modules along the
row (hall) or `grilles.count` along it (POD). Counting a direction switches it
from the coverage strip to the module grid; leaving both out keeps the strip
exactly as it was. The floor gains the matching key, `floor.tiles_across` — the
plate rows **across** the cold aisle, counting both sides of it.

**Why.** `grilles.coverage` is a fraction of the row length: the right input
when a reader is sizing an area, the wrong one when they are counting the
grilles a ceiling grid holds. And there was no way at all to say *three 600 ×
600 across this aisle*, though the floor has had a count since the raised floor
existed. A reflected ceiling plan is a count, and a ceiling is ordered in
modules.

**Why the floor needed a second key.** `floor.tiles_per_rack` is laid outward
from each cabinet's face, and two rows face the same aisle — so the aisle holds
an EVEN number: one each side is two, two each is four. An 1,80 m aisle takes
three, and three was not sayable. `tiles_across` counts the aisle's plate rows
and the two rows split them, the odd one going to the row nearer `y = 0`, which
is how a floor grid runs. The double-laying guard (ADR-095) still applies and
catches a count wider than the aisle.

**A count the mesh cannot hold is not a count.** A 600 mm module on a 0,40 m
cell is a module and a half: the opening's edges would be snapped back to the
cell and the summary would claim seventeen grilles over an opening seventeen and
a third long. So a counted direction whose cell does not divide the module is
refused, naming the cell sizes that do. The uncounted direction is unaffected,
which is why the two are independent.

**Where the modules sit.** Centred on the aisle, rounded onto the cell and held
inside it. Centred and left there, a single 600 mm module on a 1,20 m aisle came
out 400 mm wide after snapping.

**Cost accepted.** A hall's opening is still ONE panel per aisle per block, not
one per module: a hall of fourteen pods at 3 × 17 modules is 714 faceZones for a
rectangle the solver treats as one surface. A POD keeps discrete modules,
because there are a handful of them and discrete is what a ceiling grid is.

---

## ADR-107 — The room stands on the deck, and the section says so

**Decision.** On a raised-floor case the drawing paints the room's air from
the **deck**, not from the slab: the cold wash over the hall and the hot-aisle
bands both start at `floor_height`. The volume below it keeps its own wash and
its own caption, in the transverse section as well as the longitudinal one.

**Why.** Both washes started at `domain.lo[2]`, so every hot aisle ran
straight down through the under-floor supply plenum — the transverse section
showed a contained hot aisle a metre below the floor anybody stands on, with
the plenum striped pink where the aisles crossed it. The washes are
translucent, so the plenum's own blue did not hide it; it mixed with it.

**And the caption with it.** `cold aisle` was placed 0,45 m above absolute
zero, which on a 1,0 m plenum is inside the plenum: the drawing named the
supply plenum `cold aisle`, one line below where the cold aisle is. It is
placed from the deck now, and section A names the plenum the way section B
always did.

**The rule this is an instance of.** A drawing of a room with a raised floor
is two rooms stacked, and every coordinate in it has to say which one it
belongs to. Absolute zero is the slab and it is not where anything in the
data hall happens.

---

## ADR-108 — A case of your own never stops the build, and a default never refuses

**Decision.** Three things, all of them the same mistake in different places:
the software treating the engineer's own folder, and its own defaults, as
though they were the repository's.

1. **`cases/` is theirs.** The suite checks that the ten cases the repository
   SHIPS build — a failure there stops a release. Anything else in that folder
   is somebody's own work: a draft that does not build is reported once, as a
   skip that names the file, and the suite goes on.
2. **A default never refuses.** `floor.tiles_per_rack` defaults to 2, and two
   rows facing a 1,20 m cold aisle at 2 plates each ask for four rows of plate
   in an aisle that holds two. What the aisle holds is laid, and a note says
   what was dropped and how to state it.
3. **A cage wall flush against a cabinet is refused**, not warned.

**Why the folder.** `docker build` runs the suite (ADR-003), and a draft of
somebody's next project — a cold aisle they had not finished thinking about —
stopped the image, the tests and the release. The failure named their own
aisle, so the message was right and the consequence was absurd. ADR-056 said
tests must not read `cases/` as a fixture; this is the same rule from the
other side: what is in that folder cannot be allowed to decide whether the
software is fit to ship.

**And the tests that were still doing it.** Three tests added the same week
read `cases/hall-cage-1mw.yaml` directly. An engineer with their own copy of
that case — a different hot aisle, a row of their own — had three failures
about a case they had every right to edit. Those tests read
`tests/cases/hall-cage.yaml` now, a snapshot the suite owns, like every other
test (ADR-056).

**Why the default.** `tiles_per_rack: 2` was a house standard that the
commonest aisle in the industry cannot hold, and the page WRITES the value it
shows — so a case nobody typed a number into arrived with 2 and was refused by
name. The guard it tripped is a real one (ADR-095: 90 plates laid twice, and
`createBaffles` failing four steps later about a mesh face index), so the
clamp is what changed, not the guard: the plates are laid to fit and the note
says `floor.tiles_across: 2` if you want to state it.

**Why the refusal.** `cage.clearance` is the gap on both sides of the wall
(ADR-104). A stated `cage.aisle` as wide as the clearance leaves the row on
the other side nothing: the partition lands on its cabinet faces, which seals
them for a drywall cage and is not a room anybody builds either way. That was
a warning, and a reader found it on the drawing instead of in the summary --
which is the definition of a warning that should have been a refusal.

---

## ADR-109 — `cage.clearance` is a minimum on both faces, and nothing overrides it

**Decision.** The aisle a cage wall stands in is `max(aisles.cold,
2 × cage.clearance)`. `cage.aisle` can only WIDEN it beyond that: a case that
states an aisle too narrow to hold the clearance on both sides is widened to
fit, with a note saying by how much the hall grew. The aisle is also a field
on the model page now, beside the clearance.

**Why, for the third time.** The same wall, the same mistake from a third
direction:

| | what happened |
|---|---|
| ADR-104 | the aisle stayed `aisles.cold` and the clearance was measured from the cage, so the hall's row stood hard against the partition |
| ADR-108 | a stated aisle as wide as the clearance left the far row zero; refused |
| this | a stated aisle WIDER than the clearance but narrower than two of them quietly split the difference — 2,40 m of aisle with 1,80 m of clearance left the row outside the cage 0,60 m |

The third one is the shipped example plus the page: `hall-cage-1mw` declares
`cage.aisle: 2.40`, the page offers `cage.clearance` and had no field for the
aisle, so the only thing a reader could do by raising the clearance was take
the difference out of the row on the other side of the wall. Measured on that
exact configuration: 1,80 m inside the cage, 0,60 m outside. It was read off
the drawing, which was drawing exactly what it was given.

**The rule, stated once.** A cage wall is a wall with two faces. Both of them
face somebody's cabinets, and the clearance is what each of them gets — mesh
or drywall, because a security boundary is a security boundary and a mesh one
is also a pressure the air pays twice (ADR-096). The hall grows by what the
wall costs. A case that wants more room on one side asks for a wider aisle,
which gives the extra to the hall side; it cannot ask for less than the
clearance anywhere.

**Consequence.** The two refusals that stood behind this — a wall inside a
cabinet, a wall past a row (ADR-104) — can no longer be reached by any case,
because the aisle is sized before the wall is placed. They stay, and they are
exercised directly, because they are what stands between a future change to
the layout and a partition through the middle of somebody's cabinets.

---

## ADR-110 — A contained cold aisle closes with a side of its own, never on the cage wall

**Decision.** Where a cage wall stands in a CONTAINED cold aisle, the
containment does not reach it. Each half keeps the width the hall was drawn
with (`aisles.cold`), measured from its own row's cabinet faces, and closes
with a `containment_side` panel of its own: a vertical panel the length of the
lid, from the floor deck to the lid. What is left of `cage.clearance` between
that side and the cage wall is open floor — the walkway the clearance exists
for. A case whose `cage.clearance` is not wider than `aisles.cold` is refused
by name, because there is nowhere for the side to stand.

With the HOT aisle contained nothing is divided at all. A contained hot aisle
is a chimney between two rows: it ends on the rows, and a cage wall crossing
one is a partition inside a volume that is already closed, which is exactly
what the panel is.

**Why, for the third time on the same wall.** The three attempts, in order:

| | what was built | what is wrong with it |
|---|---|---|
| first | one lid and one pair of doors over the whole aisle, straight across the wall | a single contained volume spanning a security boundary |
| ADR-104 | the lid and doors cut AT the wall, each side its own aisle | the cage wall became the fourth side of two enclosures |
| this | each side stops short and closes with its own panel, walkway to the wall | — |

The second is the subtle one and it is the one a reader caught on the drawing.
Cutting at the wall is only sound if the wall closes the aisle. For a mesh cage
it does not close anything: the air crosses it and pays K twice (ADR-096), so
the "contained" aisle was open on its fourth side. For a drywall cage it closes
it, but then the containment's own doors are somebody else's security wall,
which is not how either is built or maintained.

**The rule, stated once.** A contained aisle ends on one of three things: a
wall of the room, a row of cabinets, or a side of the containment. A cage wall
is none of them. It is a security boundary standing in the room, and what a
room keeps around one is a walkway.

**Consequence.** `cage.clearance` now has to be wider than `aisles.cold`
wherever the cold aisle is contained — on the shipped example, 1.80 m of
clearance against a 1.20 m aisle, leaving 0.60 m of walkway on each face of the
wall. The hall derivation in `docs/CASE-AUTHORING.md` §2 gains that boundary,
and `case.wall_plan` gains the `containment_side` group so the new panels are
claimed like every other (an unclaimed panel is a face `createBaffles` refuses,
and the orphan guard in `tests/test_case_files.py` is what says so).

---

## ADR-111 — A machine is drawn as the machine, in every view

**Decision.** One function, `figures.fan_body_box`, says where a cooling unit's
body is, and the plan, the sections, the report's geometry windows and the page
all draw it from that:

* a **fan wall** is normal to x — the panel is the plane the solver sees, and
  the machine stands `fanwall.depth` behind it, on the side `sign` says the
  gallery is;
* a **downflow unit** is normal to z — its footprint is already the panel, the
  depth is spent there, and what stands above it is its HEIGHT: the supply face
  on the deck, the return face a storey up at `return_z`, which the panel now
  carries into both payloads.

A panel is drawn by what the view sees of it, not by its corners: a line where
it is edge-on, the rectangle where it is face-on. This applies to the cage and
the containment as much as the plant.

**Why.** Four readings of the same report and the same page, all of them
geometry the drawings got wrong rather than results:

| what a reader saw | what it was |
|---|---|
| "the CRAC is tiny on the model page" | the page extruded the unit's DEPTH (0,873 m) along the panel's own normal, which for a downflow unit is z: a 0,87 m tall box hanging under the deck, where the room holds a 1,97 m machine |
| "some CRACs are not in both galleries" | the report's plan drew each unit as a line at `panel["position"]`, which for a downflow unit is a HEIGHT: all fourteen machines came out at x = 1,0 m, one gallery, stacked on each other |
| "there are diagonal dashes in some figures" | the cage's row-end wall, and the containment's end doors, are face-on in a transverse section; drawn corner to corner they came out as a dashed diagonal across the room |
| — | the report's geometry window drew a downflow unit as a line on the deck |

The reading a drawing gets is the one it earns. None of these changed a number,
and every one of them changed what a reader believed the room was.

**Consequence.** `return_z` is part of both payloads, so an export written
before this carries none and its units fall back to the footprint alone — the
drawing is poorer, never wrong. The page keeps drawing a fan wall exactly as it
did.

---

## ADR-112 — The report describes the plant it has, not the plant it used to have

**Decision.** Where the method section speaks of the coil, it names both: a
chilled-water unit's counterflow exchanger against the entering water, and a
direct-expansion unit's evaporator measured from its apparatus dew point
(ADR-103). Where a results section speaks of what the control moves, it says
the water side for a chilled-water plant and the compressors for a DX one. The
coil table's note no longer calls every finned coil a chilled-water one.

**Why.** Section 1.4 is the FIXED introduction — printed identically in every
report, before any result, and it takes no export by design (ADR-036). When the
software gained a second coil model, that section kept describing the first,
so a report on fourteen direct-expansion CRACs told its reader about "the
chilled water the unit was selected at". The section is the METHOD, and the
method now has two coil models: it describes both and says that section 2 names
which one this plant is. Nothing there depends on the result, so the guard that
keeps the introduction result-free still holds.

---

## ADR-113 — The cores are cut where it costs least, and a failure to start says why

**Decision.** `case.decomposition` picks the grid `(nx, ny, nz)` that shares
the fewest faces between processors, out of every factorisation of the core
count, never cutting an axis more finely than it has cells. `decomposeParDict`
is written from it. And where a run fails, the log is read for the phrases that
say what to do about it — `FoamCommandFailed` carries the meaning, not just the
exit code.

**Why.** Both came from the same afternoon on a 1 MW hall.

The mesh was slabbed across its longest axis, because for a box slabs share
fewest faces — which is true of ONE cut and false of many. Every extra slab is
another plane of halo exchanged each iteration, while the work on each core
falls:

| cores | slabbed `(1 N 1)` | best grid | |
|---|---|---|---|
| 9 | 28.512 faces | `(3 3 1)` | 18.612 (−35 %) |
| 16 | 53.460 | `(4 4 1)` | 27.918 (−48 %) |
| 24 | 81.972 | `(4 6 1)` | 35.046 (−57 %) |
| 32 | 110.484 | `(4 8 1)` | 42.174 (−62 %) |

which is why 16 cores ran no faster than 9: nearly twice the communication for
half the work each. On this mesh (108 × 174 × 33) the cut is never in z, and
the search says so rather than a rule of thumb — on a smaller hall
(15 × 60 × 32) the cheapest cut of four cores IS `(1 2 2)`.

Then 24 cores would not start at all, and the tool said `mpirun exited 1`.
Open MPI had written the reason into the log: it counts physical cores as
slots, not threads, and refuses to put two ranks on one core. A tool that
holds the log and prints only the exit code is asking its user to guess.

**Consequence.** The decomposition changes no result — it changes what the run
pays to exchange. A case decomposed before this and re-run now must be built
again, which is one of the failures the log reader names.

---

## ADR-114 — The derivation ends where the mesh ends

**Decision.** The hall derivation in `docs/CASE-AUTHORING.md` §2 finishes with
the grid: `total_x` and `total_y` are the sum of the parts SNAPPED to the cell,
because that is the last thing the software does to them. The test that mirrors
the formula snaps them too, and a guard now drives it with gallery depths that
do not land on the cell.

**Why.** A build failed on `hall-cage-1mw` with `22.9 != 22.8`: the manual's
formula said 22,90 m, the software built 22,80 m, and neither was wrong. The
room is meshed, and the overall size is rounded to the nearest cell face — a
gallery of 4,45 m on 0,20 m cells moves the total by half a cell. Every shipped
hall until now was drawn on its own grid, so the derivation agreed with the
software for the wrong reason: nothing in any of them was ever rounded, and the
one case a person edited on the page found the gap the same afternoon.

The software already said so — `domain length: 21.700 m falls between 0.20 m
grid lines; the mesh uses 21.80 m (+100 mm)` is in the summary, by name
(ADR-074). What was missing was the manual agreeing with it, so an engineer
checking a drawing against the derivation could tell a rounding from an error.

**Consequence.** A case whose parts are all multiples of the cell derives
exactly as before. One whose parts are not now derives what the mesh builds,
and the difference — up to half a cell — is named in the summary rather than
found by a failing test.

---

## ADR-115 — A check compares like with like

**Decision.** Two of the eleven physical checks were comparing quantities that
are not the same quantity:

* **`fan_capacity`** now judges the unit against **the room outside the
  cabinets**: the fan rise the field shows, less the drop across one rack row.
  The sentence names all three numbers, so the reader sees the loop, the
  cabinets and the difference.
* **`_resistance_verdict`** forgives a surface that costs up to
  `NEGLIGIBLE_PRESSURE_PA` **more** than its closed form asks. The forgiveness
  is one-directional: a surface delivering LESS than its K is the failure this
  check exists for (ADR-082), however few pascals it is.

**Why.** A 1 MW hall of fourteen direct-expansion CRACs failed both, and
neither failure was about the room.

`fan_capacity` compared the fan's rise across itself — which in this model is
the WHOLE loop, because it has no rack fans (ADR-013): the units drive the air
through the cabinets as well. A real cabinet's own fans do that part, in
series, and a unit's external static pressure is what it offers the room
outside itself: plenum, plates, aisles, grilles, gallery. On this hall the loop
costs 109,5 Pa of which the cabinets are 85,1 — so the check read 219 % of a
50 Pa machine, and said "the unit cannot deliver this airflow" about a room
that costs it 24,4 Pa.

`floor_resistance` failed at 142 %: 1,38 Pa where the plates' own K asks 1,05.
Three tenths of a pascal, on a 0,30 m mesh, through a mixing-cup average. The
existing escape — both numbers under half a pascal — did not reach it, because
the EXPECTED figure was 1,05. A ratio is only a test while there is something
to divide.

**What this does not excuse.** The missing rack fans are still missing, and
this hall shows why they matter: the plant moves 385.000 m³/h where the
cabinets would draw 268.000, and with the cold aisles contained the excess is
forced through the cabinets at 1,4 times their rated flow — which is what makes
their drop 85 Pa in the first place. The check now measures the right thing;
the model underneath is still the one ADR-013 describes.

---

## ADR-116 — What a report shows: the room in plan, and the other two fields

**Decision.** Three changes to what the deliverable draws:

* the **basis of design** carries the whole data hall in plan — the drawing the
  model page shows — instead of one pod at rack height. Past `NAMES_FIT`
  cabinets in the window, the rows are named and the cabinets are not;
* the results gain **air speed and static pressure**, in plan and in section,
  through the same `plan`/`section` figures with a `field` argument;
* the plan's band chain leaves out the aisle a **cage wall** stands in.

**Why.** Three readings of the same report.

The basis of design is where a reader checks the LAYOUT against their drawing,
and it showed them five metres of one pod. The answer to "does this match my
plan" is the plan.

A study is read for three fields and only the first was ever drawn. The speed
maps answer "is the aisle fed"; the pressure maps show the plenum, the drop
across the cabinets and what the units have to produce — the same 85 Pa
`rack_resistance` checks, visible as a gradient instead of a number in a table.

Both new fields are fitted to the run at the 1st and 99th percentile, because
the peak speed is inside a unit's own discharge and the peak pressure is under
the deck: a scale stretched to reach them paints the room in one pale band. A
pressure field with one sign gets a sequential ramp — a diverging one wastes
half its bar and puts the neutral band where nothing is — and the ASHRAE marks
belong to a temperature scale, so they are off the others.

The cage's aisle is `cage.aisle` wide because the clearance is a gap on both
faces of the wall (ADR-109). It is neither the hall's cold aisle nor a distance
anybody sets out from a drawing, and dimensioned as one it printed a lone
6,00 m across the middle of the plan.

---

## ADR-117 — A CRAC network shares a setpoint; a CRAH network shares its valves

**Decision.** `fanwall.control: team` means two different things, because the
two plants are built two different ways:

* **chilled water (CRAH).** What is shared is the VALVE. Every unit on the
  loop opens as far as the worst-placed one has to, and then delivers what its
  own coil gives at its own return. Unchanged (ADR-064).
* **direct expansion (CRAC).** What is shared is the SUPPLY SETPOINT. The
  plant delivers the coldest air its worst-placed unit can still make, and
  every other unit holds that same temperature with its own compressors,
  unloading as far as it needs to.

**Why.** A CRAC is controlled on its supply air and a network of them holds one
setpoint between them; a CRAH array on a common water loop is throttled by the
valves the loop's control opens. Modelling the DX plant the chilled-water way
drove the well-placed units to 15,9 °C of supply against an 18,8 °C setpoint —
they were told to run at the worst unit's compressor duty and had nothing to do
with it but overcool their own air. It also read as a plant at 100 % of its
available capacity, because every unit was at its ceiling by construction.

**Consequence.** For a DX plant, `team` and `independent` now agree wherever
the worst-placed unit can hold the setpoint, and part company exactly when it
cannot — which is the finding the control mode exists to show. A unit on the
network is never colder than the same unit left alone.

---

## ADR-118 — The compressors are a ceiling, and the drawing says where

**Decision.** A `DXCoil` carries `capacity_ceiling_kw` — the gross
refrigeration the compressors can lift at the condensing condition the
selection names, taken as that selection's own gross total. The coil's gross
duty is held there, the capacity curve flattens at it, the figure draws the
line and names it, and the alert says how many units are held by their
compressors rather than by their coil. A selection that prints no total gets no
ceiling and answers unbounded, as before.

**Why.** An ε-NTU evaporator grows without limit as the return warms: at
30,6 °C this machine's coil asked for 114 kW where its own selection is
110,3 kW gross, and the report read it as a unit at 112 % of its plate. The
coil really would transfer that. The compressors would not lift it, and holding
capacity at the manufacturer's table is what every commercial tool does.

**What is still not modelled.** The CONDENSING side. The ceiling is the one the
selection was taken at — 37,6 °C of outdoor air on the sheet in hand — and a
warmer day lowers it. One selection point is one point on a capacity table, and
this tool holds it there and says so, in the limitations and beside the alert.

---

## ADR-119 — A surface is judged on the field it is in, face by face

**Decision.** `flow_spread` is the FLOW-WEIGHTED second moment of the flux over
every face of that kind of surface, against the rated face velocity — the same
weighting `grille_pressure_drop` uses to report the drop. It was the variation
inside a single patch, averaged over the patches.

**Why.** A 510 kW study, fully converged — `settled` at 0,01 K, `energy_closure`
at 100 %, every other check green — failed `floor_resistance` at 140 %: the
field dropped 3,22 Pa across 208 plates where their K at the rated velocity
asks 2,30. Measured on the tracked 1 MW run, plate by plate:

| | |
|---|---|
| each plate's drop against `K rho u²/2` at ITS OWN velocity | **1,015** |
| the same field against the K at the rated velocity | 1,44 |
| face velocities across the plates | 0,076 to 1,144 m/s |

The plates deliver what they were given to one and a half per cent. What the
check was doing was comparing a flow-weighted measurement of an unevenly fed
surface against a closed form evaluated at one uniform velocity, and correcting
it by the variation INSIDE one plate — two cells, so 1,09, where the variation
BETWEEN the plates is 1,44.

A plate over a CRAC's discharge and a plate at the far end of the plenum are
not the same plate. That is the oldest fact about a raised floor, it is exactly
what a CFD study is run to see, and the check was treating it as a modelling
error.

**The rule, stated once.** Measure the field one way and compute the
expectation the same way. Whatever weighting the reported number uses — and
flow-weighted is the right one, because it is the pressure the average kilogram
of air pays and therefore what the fan has to produce — the closed form is
evaluated with that weighting over the same faces.

**Consequence.** On the tracked run the floor goes from 145 % to 100,7 %, and
the sentence now says why the ratio against the rated velocity is 145 %: *the
air reaches it 1,4 times harder than the rated face velocity assumes, so its
own K asks 1,40 Pa of this field*. The ceiling grilles, the supply grilles and
the plenum mesh are measured the same way and were always nearly even, so
nothing there moves. The reverse-flow denominator of ADR-082 is unchanged: the
rated velocity is still the NET over the faces, so recirculation still counts
against the surface.

---

## ADR-120 — The page runs the same solve the command line runs

**Decision.** `server.start_run` calls `solve_coupled`, with the same
`solver.couple`, `solver.coupling_segment` and `solver.coupling_passes` the CLI
reads, and puts each pass's summary on the page's progress state. It called
`solve`.

**Why.** A run started from the page never coupled the coil to the room. The
supply air stayed at the temperature the case states, from the first iteration
to the last; no pass ever read a return or asked a coil anything; and
`fanwall.control` — `team` or `independent` — changed nothing whatsoever,
because the control only exists inside a pass that never ran.

An engineer testing the two control modes on the page found exactly that, said
so, and was told the coil model was the thing to fix. The coil model did need
fixing (ADR-103, ADR-117), and none of it reached a run started from the page.

The evidence in the end was one line on a cover sheet: *solved to iteration
2000*, where 2000 is the case's `max_iterations`. A coupled run ends at
`max_iterations + passes × segment` and never at the cap itself. Every unit in
that run delivered the same 22,0 °C the case asked for, while the coil's own
answer at the returns the room produced was 23,3 °C — which is how a report
came to show two units removing more heat than their machine can make.

**Consequence.** A page run is now the run the report describes. It takes the
coupling passes' extra iterations, which is what ADR-040 already says a coupled
solve costs, and the page shows them as they happen. A case with
`solver.couple: false` gets the plain solve, on the page as on the command
line.

---

## ADR-121 — A row of machines is dimensioned off the wall, not off the aisles

**Decision.** Two fields say where the units stand: `fanwall.offset`, from the
start of the gallery wall to the face of the first unit, and `fanwall.pitch`,
centre to centre. Both are optional; with either stated, `row_of_units` lays
the row where the drawing puts it. Failing that, the ARRANGEMENT decides:

* a **fan wall** keeps one unit per cold aisle, centred on the aisle it faces;
* a **downflow** unit gets an even row along the wall.

**Why.** "One unit centred on each cold aisle" is what a fan wall is for: it
blows horizontally into the aisle in front of it, so facing the aisle it feeds
is the whole point. A downflow unit feeds no aisle at all — it discharges
through the deck into the plenum, and the plates distribute — so aligning it
with an aisle was a fan wall's rule applied where it does not belong.

It showed. On a 1 MW hall whose cold aisles are not evenly spaced — 6 m where
the cage wall stands, 1,2 m at the perimeter — seven CRACs came out at gaps of
1,5 / 4,5 / 4,2 / 2,1 / 2,1 / 1,5 m, with the first flush against the wall
because a 2,7 m machine centred on a 1,2 m aisle starts outside the room. The
engineer checked the layout it was taken from: first unit 240 mm off the wall,
and one pitch from there.

Which is the point. What sets the offset and the pitch is the structure, the
pipework, the door swing and the space to pull a unit out — never the air. A
tool that derives them from the aisles is deriving them from the wrong thing,
and a tool that cannot be told them cannot reproduce a hall that exists.

**Consequence.** Any raised-floor hall whose unit count happened to equal its
aisle count moves its machines. That is the correction, not a side effect:
`hall-cage-1mw`'s seven units go from those uneven gaps to an even row, and
stating `offset` and `pitch` puts them where its drawing has them.

An offset finer than the cell cannot survive the mesh — 240 mm does not exist
on a 300 mm grid — and the alignment check says so by name (ADR-074), as it
does for every other plane.

---

## ADR-122 — The solver log is the whole run, and the page says where it is going

**Decision.** `run_command` takes `append`, and `solve_coupled` uses it for
every solver pass after the first: one log holds every iteration of the run.
The page's progress line names the pass, of how many, and the iteration the
next segment is solving to.

**Why.** A coupled solve runs the solver once per pass, and each pass opened
`log.buoyantSimpleFoam` with `"w"`. Two things followed, and an engineer
watching a run found both:

* **The log emptied and started again at every pass.** Tailing it, the
  iteration counter fell back and climbed again — which is exactly what a
  solver restarting from zero looks like. It was not restarting; the record of
  it was being thrown away.
* **The report's convergence figure showed 300 iterations of a 2.800-iteration
  run.** Section 4.2 is where a reader judges whether the residuals settled,
  and it was drawing the last segment as though it were the run.

Both are the same defect, and neither is about the solve: the field, the
checks and every number in section 4 were right. What was lost was the history
that says whether to believe them.

The second half is a consequence of the page coupling at all (ADR-120). A run
started from the page now goes past `max_iterations` — that is what a coupled
solve costs (ADR-040) — and the page said nothing about it. Asking for 1600
iterations and watching the run carry on to 2800 with no explanation is a tool
doing something behind its user. It now says which pass it is on, how many
there are, and where the next one ends. A case that wants the old behaviour
sets `solver.coupling_passes: 1`, or `solver.couple: false` to turn it off.

---

## ADR-123 — A result is only real if the units deliver the air their coils make

**Decision.** A twelfth physical check, `coil_closure`, compares the supply
temperature every unit **imposes on the field** with the supply temperature
its **coil produces** at the return it actually receives. More than
`COIL_CLOSURE_TOLERANCE_K` = 0,1 K apart on any unit and the report fails its
checks.

**Why.** This is the one thing the checks did not test, and it is the thing
that decides whether a result describes the room at all.

The field is solved with a supply temperature at each unit: the air leaves the
floor at that temperature and everything downstream of it — the inlet
temperatures, the ΔT, the recirculation, every KPI in section 4 — is measured
from it. The coil model is what says whether the machine can actually make
that air at the return it gets. When the two agree, the result is a room whose
plant can exist. When they do not, the field was solved with air the plant
does not produce, and **every temperature in the result is wrong by the
difference** — not approximately, not conservatively, in one direction:
optimistic.

Two things can put them apart:

* A coupled solve that ran out of passes. The loop was still moving when
  `coupling_passes` ended, so the field carries pass four's supply and the coil
  has moved on to pass five's.
* A run solved with `couple: false` on a plant that cannot hold the stated
  `supply_temp_c` — the field simply asserts a temperature and nothing ever
  checks it.

`hall-cage-1mw` was the second kind and then the first: 23,25 °C in the field
against 24,37 °C from the coil at the 35,09 °C return that unit sees — 1,12 K.
The report said so, in a paragraph, as an **alert**, under a cover page that
said the checks had passed. A document cannot say "these results are sound"
and "every temperature here is a kelvin optimistic" on the same page. That is
what made it a check rather than a warning.

**Why 0,1 K.** Measured, not chosen. Across every tracked result, a coupled
solve that closed agrees to 0,00–0,01 K, and the one that stopped at its pass
cap was 1,12 K out. A tenth of a kelvin is ten times the first and a tenth of
the second: nothing sound fails and nothing broken passes.

**What it is not.** It is not "a unit is saturated". A unit at full duty whose
supply follows its return is a real machine doing its best, and a perfectly
good result can be full of them — every unit of `hall-double-gallery-water21`
is saturated and it closes to 0,01 K. Saturation is an engineering finding and
stays an alert (ADR-118). Disagreement between the field and the machine is an
arithmetic contradiction, and that is a failure.

The remedy the check names is the remedy: raise `solver.coupling_passes` until
the loop closes, or state a `fanwall.supply_temp_c` the plant can hold.

### What the same read found in the report

The check above came out of reading the document end to end and asking, of
every page, whether an experienced engineer would have written it that way.
Six other things did not survive that reading, and all six are the same fault:
the document stating one quantity two ways.

* **The headline pressure contradicted the conclusion.** Section 2 charged the
  most loaded unit for the WHOLE loop — 113,6 Pa against the 50 Pa of external
  static it offers, which reads 227 % — four pages above a section 5 that said
  54 %. The subtraction that separates the cabinets' own drop from the room's
  (ADR-115) lived in two places and had only been fixed in one. It is one
  exported number now, `room_static_pa`, and everything that quotes it reads
  that.
* **Two supply temperatures under one heading.** Section 2's table, introduced
  as "that machine's own manufacturer selection", printed the SOLVED supply —
  23,2 °C — while section 3's selection table printed 18,8 °C for the same
  machine. Both are real; neither was labelled. They are now two rows.
* **Two elevations, likewise**: 0 m for the hall and 25 m for the selection,
  with nothing saying they were different things.
* **The method promised what the run had not delivered.** Section 1.6 said
  "all of them pass before a temperature in this document is quoted" on the
  front of a report whose cover said CHECKS FAILED, and counted "eleven"
  identities above a table of thirteen. Section 1 is fixed text and cannot
  know either, so it states the RULE; the verdict now leads section 4.1 and
  section 5, where the numbers are.
* **`PASS … (148 %)`.** The resistance checks quoted the ratio against the
  DESIGN figure and then explained, three clauses later, that the field's own
  closed form asks something else (ADR-119). A reader cannot be asked to find
  the correction and redo the division: the percentage is now the one the
  verdict was taken on, and the design figure stays in the sentence.
* **Thirty-three bullets, ten of them distinct.** Every containment lid, row
  end and door that landed on the same 3,200 m plane got its own paragraph.
  Identical snaps are collapsed to one line naming every plane that took them.

Two more were not contradictions but a report written by a program rather than
by a person: `2 unit(s)`, and a pressure column headed `Rise` in pascals beside
four temperature columns.

The one thing the reading found MISSING rather than wrong is the coupling
record. Whether the loop closed is what decides everything above, and it was
returned to the caller and written down nowhere — so the only way to know was
to have watched the run, and a reader of the report was not there. The solve
now leaves `coupling.json` in the case, and section 4.2 says how many passes
ran, how far the last one moved, and whether that closed.

---

## ADR-124 — The coupled loop runs until it closes; how many passes that takes is not a setting

**Decision.** `solver.coupling_passes` and `solver.coupling_segment` are gone
from the case, the page and the manual. The coupled solve runs passes until
no unit's supply air moves more than 0,02 K, and stops early only at a safety
limit of thirty, which a run reaching has **not closed** — the record says so
and `coil_closure` fails on it. A case that still carries the old keys is told
by name that they were ignored.

**Why.** An engineer asked the right question: *do the commercial tools ask the
user to handle the coupling? Shouldn't it be part of every iteration? How
would I choose the coupling for another plant?*

They do not, it should, and you would not. In 6SigmaDCX, in FloTHERM, in every
tool of that kind, the cooling unit is re-evaluated inside the iteration loop
and the user chooses the **machine** (its selection), its **control** (supply
setpoint, return setpoint, capacity-limited) and its **limits**. Nobody
chooses how many times the unit and the room talk to each other, because that
is convergence, and convergence is the solver's job.

Here the coupling was built as *solve a segment → stop → read → write back →
restart* (ADR-040), and the pass count and the segment length leaked out of
that implementation into the case file as though they were design decisions.
A 1 MW hall then stopped at its default of five passes with the units still
moving 1,12 K, and the remedy the software offered — "raise
`solver.coupling_passes`" — asked the engineer to tune a convergence
parameter they had no way to judge. That is the software handing its own job
to its user.

**What is the engineer's and what is not:**

| decision | whose |
|---|---|
| the machine, its setpoint, `team` or `independent`, its ceilings | the engineer's — it stays in the case |
| how often the coil is re-read, how many times, when it has converged | nobody's — it is numerics and it is gone |

**The safety limit is not a target.** Every tracked run closed in two to five
passes. A loop that has not closed in thirty is a plant oscillating between
two answers — a `team` plant flipping which unit is worst, a saturated unit
handing its return back and forth — and the field carries one of them. That
is a finding about the plant, the record and section 4.2 of the report say
so, and no number of further passes would have made it a result.

**What this does not do, and why.** The right implementation re-evaluates the
coil *inside* the solver, every N iterations, with no stop and no restart — a
function object on the supply patches. OpenFOAM's `coded*` mechanisms do that
through run-time compilation, and the Debian `openfoam` package this image
installs ships no `wmake` and no headers, so they are not available without
rebuilding the image around a development install. Until then the loop is
outside the solver and the record of it is written down; the interface the
engineer sees is already the one the commercial tools have.

### Found on the way: the report described `team` as a CRAC whatever the plant

Putting every rule on a CRAH and a fan wall as well as a CRAC (the two new
cases) turned up one sentence that was only ever true of one of them. Section
3's control paragraph said a networked plant "controls every unit to the
warmest return, so they deliver the same supply temperature" — which is the
shared **setpoint** a DX plant runs to (ADR-118), and not what a chilled-water
plant does: that one shares the **valve position**, and each unit then
delivers what its own coil gives at its own return (ADR-117). On a CRAH or a
fan-wall hall the report described a control that was not running. It now
says which of the two the plant in front of it has.

The same read added to the chilled-water report the half the DX report already
had: the DX plant's limitations say the condenser is not modelled and the
capacity is held at the selection's outdoor air; the chilled-water plant's
now say the chiller, the pumps and the distribution are not, and the capacity
is held at the selection's entering water and flow.

### The CRAH twin, read end to end

`hall-cage-1mw-crah` solved and closed its loop on its own in six passes,
every check passing — which is what ADR-124 was for. Reading its report the
way the DX one was read found eleven more places where the document was
written for one machine, one arrangement, or by a program:

* the cover said `Hall Cage 1mw Crah`; acronyms and units survive the slug now;
* the geometry table said "Contained hot aisles" and three figure captions
  said "contained hot aisle" on a hall whose COLD aisles are the contained
  ones — one helper reads which aisle was built with a lid, and every sentence
  asks it;
* "Static pressure available … 604 Pa" sat beside "external static … 50 Pa"
  with nothing saying the first is the fan curve at FULL SPEED and the second
  the selection at 67 %;
* "Cabinet widths 0,9 m" in the basis of design, of an 800 mm cabinet the
  mesh carries as 900 — it says both now;
* an airflow table whose step 3 is step 1 × step 2 printed 441.699 for
  14 × 31.550;
* "4 units are drawing more than the coil can give" under a table showing all
  fourteen at 100 % — the noise of a mixing-cup mean above a strict `> 100`;
* the saturation alert said the run was "solved at 22,69 °C", the mixed
  figure, on a shared-valve plant whose units deliver 21,98 to 24,05 — no unit
  produces 22,69;
* the water alert quoted the unit's flow as though it were the plant's;
* the pressure-curve note said "anchored to the selected external static"
  where this sheet anchors it on the maximum;
* the static-pressure caption named "the supply plenum" on halls that have
  none.

### The fan-wall twin, read end to end

`hall-cage-1mw-fanwall` closed in four passes, holds its 23,0 °C setpoint with
reserve, and passes every check. Its report, read the same way, found the
document describing a room it does not have and missing the one rack-level
conclusion a CFD study exists to make:

* the surfaces table listed "Raised floor plates" and "Plenum supply grilles"
  in a hall with neither — the library's `applied` flag says a case MAY use a
  component, and the table now also asks whether this arrangement built it;
* the face-velocity column read "—" on every row of every report: the plate
  and grille velocities were measured and never exported, and the ceiling
  grilles' is the aisle-exit station's;
* "the unit's airflow, from the datasheet: 75.000 m³/h" of a unit whose sheet
  says 140.165 — it is the operated airflow, and it says so, by volume and by
  mass, because the coil sees mass and this hall's sea-level air is denser
  than the sheet's at 661 m;
* the mesh prose claimed the unit top "lands on a cell face" three lines above
  a list saying it was moved 80 mm;
* the geometry section described the raised-floor air path or nothing — the
  fan-wall path has its own paragraph now;
* the plate alert blamed the return temperature alone for a coil at 48 % of
  plate that is also moving 61 % of the selection's air.

**The finding that was missing.** The racks have no fans (ADR-013): a cabinet
draws what the pressure across its row gives it, so a 20 kW cabinet between
4,7 kW neighbours gets much the same air they do and rises by two or three
times its design ΔT — 27,5 K on F1-01 here, 35 K elsewhere. The report printed
that rise in an annex column and concluded nothing. Section 5 now counts the
cabinets rising by more than twice the design ΔT, names the worst, and says
what the number means in the model and in the room. It is what the commercial
tools report as an airflow deficit, and it is the cabinet to look at first.

---

## ADR-125 — A DX network holds the setpoint it was given; a loop whose step does not shrink is stopped

**Decision.** `DXCoil.operate_shared` runs every unit to the **fixed** supply
setpoint the case states. A unit whose return is too warm for its compressors
delivers what it can and its supply follows its return; the rest hold the
setpoint. The "plant delivers what its worst unit can make" law of ADR-117 is
withdrawn. The coupled loop now also stops when the supply movement has failed
to shrink for four passes running, records `diverged`, and `coil_closure`
names a runaway as a runaway.

**Why.** Re-running the 1 MW DX hall with the loop free to run until it closed
(ADR-124) showed it never would:

```
pass  1: warmest supply 19,83 degC
pass  2: 20,89   moved 1,06 K
pass  3: 21,97   moved 1,08 K
  …
pass 25: 48,04   moved 1,24 K
```

Not an oscillation — a **runaway**, 1,1 to 1,2 K a pass without bound. The
law behind it: every unit was set to the supply the worst-placed unit could
still make. That unit is at its compressor ceiling, so its supply is its
return less a constant. Raise the plant's supply by Δ and the room's return
rises by Δ; the worst unit's achievable supply rises by Δ; the setpoint
follows it by Δ. A fixed-point iteration with unit gain has no fixed point,
and the 1,12 K at pass five that `coil_closure` first caught (ADR-123) was the
fifth step of this, read as a loop that needed longer.

The physics of the room was right throughout; the control law was invented.
No BMS raises a common setpoint to what its weakest machine can reach. A
Liebert iCOM "teamwork" network of supply-controlled units holds the setpoint
the operator typed on every unit; the units that cannot reach it saturate,
the others hold, and the network's work — staging compressors, coordinating
fan speed — is dynamic and invisible to a steady field. So in steady state
`team` and `independent` give the same answer for a supply-controlled DX
plant, and the report says so instead of describing a control that does not
exist. ADR-117's chilled-water half — a CRAH network shares the **valve
position** and each unit delivers what its own coil gives at its own return
(ADR-064) — stands: that IS how a common water loop behaves, and the CRAH and
fan-wall twins closed in six and four passes on it.

**The divergence stop.** The safety limit of thirty passes (ADR-124) would
have taken this run to 10.300 iterations — two hours — to fail a check that
pass five could already have failed. A converging fixed-point iteration takes
smaller steps each time; one whose step holds or grows for four passes in a
row is running away, and the loop now stops there, writes `diverged` on the
record, and the remedy says "the supply air kept moving by about 1,2 K every
pass instead of settling: the plant's control is chasing its own return"
rather than sending the reader to look for an oscillation.

---

## ADR-126 — The report says what things are

**Decision.** Every sentence in the report and in the alerts that said what
something is *not* now says what it is. "The range is part of the reading and
not an error bar" became "the range is that spread"; "the room is derived from
the case specification, not drawn" lost its second half; "NOT a blanking
panel" became "the same porous zone as its neighbours, with no heat source";
"the plate is not the capacity this room has" became "the capacity to count on
in this room"; "THESE ARE NOT RESULTS" became "THIS SECTION IS DIAGNOSTIC
MATERIAL"; "the condensing side is not modelled" became "capacity is held at
the outdoor air above, which is the whole of the condensing side in this
model". Forty-odd sentences, in `report.py` and `post.py`.

**Why.** The engineer reading the reports put it exactly: a machine has the
habit of writing what things are not, and nobody opens a report to learn what
something is not. Every one of those negations was written to head off a
misreading the author imagined; the reader had not imagined it, and the
sentence made them wonder who had. Where a sentence's whole content was a
negation — an alert whose point was "the plate is not the capacity" — the
question to ask was what it was FOR: that alert exists to say which capacity
to count on, and now it says that. The limitations section keeps stating what
the model leaves out, because that is what a limitations section is for; it
does so as "lies outside the model" and "enters through these two numbers"
rather than as a list of nots.
