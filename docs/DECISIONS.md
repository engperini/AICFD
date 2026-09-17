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
