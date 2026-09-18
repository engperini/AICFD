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
*not* need OpenFOAM — the 208 tests, the page over the tracked results, and the
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
   came out 74 px tall. The height is now a generous cap that width beats, and
   the results page gives each drawing a full-width row.
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
