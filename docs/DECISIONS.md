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
