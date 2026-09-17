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

**Decision.** `room.yaml` is committed. The generated case directory is gitignored and
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

**Decision.** The viewer is plain ES modules with a vendored copy of Three.js, served
as static files. No npm, no bundler, no framework.

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

**Decision.** `aicfd/podcase.py` builds a fan-wall POD with `blockMesh` →
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
`podcase.check_fan_orientation` refuses to solve if `fanSupply` is not facing
the data hall.

The intake's temperature is written as `inletOutlet` even though nothing can
enter through it, because `zeroGradient` stores no value on the patch and the
temperature of the air crossing there is exactly what the energy balance is
built from (ADR-018). Its inlet value is the supply temperature — inert, and
if it ever were used it would bring back cold air rather than fabricate heat.

---

## ADR-018 — A steady run is judged by its energy balance, not its residuals

**Decision.** `aicfd/podpost.py` computes, from the values on the fan intake
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

So a run is judged on two things. A closed balance says the field is
*consistent*; the places holding still between samples says it is *settled*.
`drift()` reports the largest move any instrumented place made since the
previous sample, and `settled` fails above 0,25 K. Neither check alone is a
verdict.

**Consequence.** Every quantity in this module is read from patch values, never
from the nearest cell centres. Approximating a face flux from cell-centre
velocity is wrong by tens of percent across a porous zone or a grille jet: it
is what first made a perfectly sealed POD look like it was losing 37% of its
air. The cost is that the generator must write a value on every patch the
balance reads, which is why the intake is `inletOutlet` rather than
`zeroGradient`.
