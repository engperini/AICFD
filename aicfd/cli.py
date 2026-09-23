"""Command line interface -- the whole tool, and the only way it is driven.

    aicfd new <name>                 write a starter case spec into cases/
    aicfd doctor                     check that OpenFOAM is usable here
    aicfd build <spec.yaml>          generate the OpenFOAM case, solve nothing
    aicfd run <spec.yaml>            generate, solve, sample, post-process
    aicfd post <name>                re-export a solved run into results/NAME
    aicfd view [--port 8000]         serve the page: check, edit, run, read
    aicfd verify [--solve]           run the audit: tests, install, both cases

The assistant drives these same commands rather than a separate code path, so
anything it can do is reproducible by hand -- and anything that breaks can be
debugged without it (ADR-003).

One spec shape, one generator, one analysis: a spec with ``pods:`` is a data
hall and one without is a single POD, and both are the same model underneath
(ADR-022). There is no second pipeline to choose between.
"""

from __future__ import annotations

import argparse
import re
import sys
import webbrowser
from pathlib import Path

# The one list of ramps a reader may ask for, shared with the server and the
# page, so `--colours` cannot offer a name the rest of the tool does not know.
from aicfd.palette import OPTIONAL_RAMPS

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"
RESULTS_DIR = REPO_ROOT / "results"
#: The three worked results, tracked because they are evidence rather than
#: artifacts. The tool never writes here: a result you produce goes to
#: `results/` and shadows the one shipped (ADR-032).
REFERENCE_DIR = REPO_ROOT / "reference"
#: Word deliverables and their figures. Separate from the exports so that
#: producing a document never modifies the result it was read from.
REPORTS_DIR = REPO_ROOT / "reports"


def find_result(name: str) -> Path | None:
    """Where this case's export is: yours first, the shipped one otherwise."""
    for base in (RESULTS_DIR, REFERENCE_DIR):
        if (base / name / "viewer.json").is_file():
            return base / name
    return None
CASES_DIR = REPO_ROOT / "cases"
TESTS_DIR = REPO_ROOT / "tests"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aicfd", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="write a starter case spec")
    new_parser.add_argument("name", help="case name; becomes cases/<name>.yaml")
    new_parser.add_argument(
        "--from",
        dest="template",
        help="start from an existing case (e.g. hall-10mw) instead of the blank POD",
    )

    sub.add_parser("doctor", help="check the OpenFOAM installation")

    build_parser = sub.add_parser("build", help="generate a case, solve nothing")
    build_parser.add_argument("spec", help="path to a case spec YAML")
    build_parser.add_argument(
        "--out", help="output case directory (default: runs/<name>/case)"
    )

    run_parser = sub.add_parser("run", help="generate, solve and post-process")
    run_parser.add_argument("spec", help="path to a case spec YAML")
    run_parser.add_argument("--name", help="run name (default: the spec's name)")
    run_parser.add_argument(
        "--no-post", action="store_true", help="skip post-processing afterwards"
    )

    stop_parser = sub.add_parser(
        "stop", help="ask a running solve to stop cleanly at its next iteration"
    )
    stop_parser.add_argument("name", help="run name under runs/")

    post_parser = sub.add_parser("post", help="post-process a solved run")
    post_parser.add_argument("name", help="run name under runs/")
    post_parser.add_argument("--time", help="time directory (default: the latest)")

    report_parser = sub.add_parser(
        "report", help="write the Word report for an exported result"
    )
    report_parser.add_argument("name", help="result name under results/")
    report_parser.add_argument(
        "--out", help="output .docx (default: results/<name>/<name>.docx)"
    )
    report_parser.add_argument("--client", help="who the study is for, on the cover")
    report_parser.add_argument(
        "--title", help="the room's name on the cover (default: the case name)"
    )
    report_parser.add_argument("--author", help="who ran it, on the cover")
    report_parser.add_argument(
        "--colours", choices=OPTIONAL_RAMPS, default=None,
        help="repaint the field figures in this ramp (default: the ramp each "
             "field asks for)",
    )

    view_parser = sub.add_parser("view", help="serve the page")
    view_parser.add_argument("--port", type=int, default=8000)
    view_parser.add_argument(
        "--case", help="case spec to open (default: the newest in cases/)"
    )
    view_parser.add_argument("--no-browser", action="store_true")

    verify_parser = sub.add_parser("verify", help="run the audit")
    verify_parser.add_argument(
        "--solve",
        action="store_true",
        help="also solve the POD case briefly and check the physics (a few minutes)",
    )
    verify_parser.add_argument(
        "--iterations", type=int, default=400, help="iterations for --solve"
    )

    args = parser.parse_args(argv)
    return globals()[f"_{args.command}"](args)


# --- the spec -----------------------------------------------------------------


def load_spec(path: str | Path):
    """Read a case spec and derive its model. The one entry point for both."""
    import yaml

    from aicfd.model import build_model

    spec = yaml.safe_load(Path(path).read_text())
    return build_model(spec), spec.get("solver", {})


STARTER_SPEC = """# AICFD case spec. Everything here is in engineering units; the OpenFOAM
# side (mesh, baffles, porosity, heat sources, boundary conditions) is derived
# from it and never edited by hand (ADR-004).
#
# This starter is a single POD: one row of racks, a contained hot aisle, a
# ceiling-plenum return and one fan wall. Add `pods: <n>` and swap
# `racks.count` for `racks.per_row` to make it a data hall of that many
# row-HAC-row pairs (see cases/hall-10mw.yaml).
name: {name}

site:
  altitude_m: 0               # the air weighs what it weighs here (ADR-023)

gallery:
  depth: 3.0                  # mechanical gallery, no false ceiling

hall:
  size: [6.0, 4.2, 8.0]       # length x, width y, floor to slab z
  ceiling: 6.5                # false ceiling; above it, the return plenum

aisles:
  cold: 1.8
  hot: 1.2                    # contained, a chimney up to the ceiling

racks:
  count: 3
  load_kw: 6.0
  size: [0.6, 1.2, 2.2]       # front(x) x depth(y) x height(z)
  offset_x: 2.0               # from the gallery wall
  airflow_cfm_per_kw: 158     # what a rack draws per kW (ADR-023)

fanwall:
  airflow_m3h: 5000           # PER UNIT, as a datasheet gives it
  supply_temp_c: 20.0
  width: 1.8
  height: 4.0
  static_pressure_pa: 100     # external static pressure at the rated flow
  # capacity_kw: 30.0         # net sensible per unit; add it and the sizing
  # power_kw: 2.0             # checks appear on the page and in the report

grilles:
  size: 0.60
  count: 3
  free_area: 0.80
  loss_coefficient: 2.4       # from the grille datasheet (ADR-020)

containment:
  enabled: true

mesh:
  cell_size: [0.20, 0.20, 0.10]   # per axis (ADR-021)

solver:
  max_iterations: 2000        # a cap, not a target
  residual_tolerance: 1.0e-4
  sensor_interval: 100        # how often the run reports
  warm_start: true            # seed the loop's topology (ADR-019)
  processors: 1               # above 1: decomposePar + mpirun
"""


def _new(args) -> int:
    """Write a new case spec: the commented starter, or a copy of a worked one.

    `--from hall-10mw` is how a real study starts: the worked case carries a
    datasheet selection, a mesh that is known to build and comments on every
    line, so the engineer edits numbers rather than inventing a file.
    """
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    path = CASES_DIR / f"{args.name}.yaml"
    if path.exists():
        print(f"error: {path} already exists", file=sys.stderr)
        return 1

    if args.template:
        source = CASES_DIR / f"{Path(args.template).stem}.yaml"
        if not source.exists():
            available = ", ".join(sorted(p.stem for p in CASES_DIR.glob("*.yaml")))
            print(
                f"error: no case named '{args.template}'. Available: {available}",
                file=sys.stderr,
            )
            return 1
        text = source.read_text()
        # Only the name changes; every comment and datasheet reference stays.
        text = re.sub(r"^name:.*$", f"name: {args.name}", text, count=1, flags=re.M)
        path.write_text(text)
        print(f"Wrote {path}, copied from {source.name}")
    else:
        path.write_text(STARTER_SPEC.format(name=args.name))
        print(f"Wrote {path}")
    print(f"Edit it, then:  aicfd view --case {args.name}    (or: aicfd run {path})")
    return 0


# --- the pipeline -------------------------------------------------------------


def _doctor(_args) -> int:
    from aicfd.run import FOAM_ENV, check_install

    print("OpenFOAM utilities:")
    found = check_install()
    for name, path in found.items():
        print(f"  {'OK ' if path else 'MISSING'}  {name:20} {path or ''}")
    if not all(found.values()):
        print(
            "\nInstall with:  apt-get install -y openfoam openfoam-examples\n"
            "Or use the Docker image, which has it baked in:  docker compose up"
        )
        return 1
    print(
        "\nSolver environment (isolated, see ADR-002):\n  "
        + "\n  ".join(f"{k}={v}" for k, v in FOAM_ENV.items())
    )
    return 0


def _build(args) -> int:
    from aicfd import case

    model, solver = load_spec(args.spec)
    out = Path(args.out) if args.out else RUNS_DIR / model.name / "case"
    case.build(model, out, **_build_options(solver))
    print(case.summary(model))
    _print_notes(model)
    print(f"Generated {out}")
    return 0


def _build_options(solver: dict) -> dict:
    return dict(
        max_iterations=int(solver.get("max_iterations", 2000)),
        residual_tolerance=float(solver.get("residual_tolerance", 1e-4)),
        sensor_interval=int(solver.get("sensor_interval", 100)),
        warm_start_field=bool(solver.get("warm_start", True)),
        processors=int(solver.get("processors", 1)),
    )


def _print_notes(model) -> None:
    """Mesh snapping first, then any HVAC criterion the plant misses.

    Both are warnings and neither stops a run: a conceptual study wants to see
    what an undersized plant does (ADR-023).
    """
    for warning in model.warnings:
        print(f"  ! {warning}")
    for alert in model.alerts:
        print(f"  !! {alert}")


def _run(args) -> int:
    from aicfd import case, post
    from aicfd import run as run_module
    from aicfd.run import FoamCommandFailed, FoamNotInstalled, solve

    spec_path = Path(args.spec)
    if not spec_path.is_file():
        print(f"error: no such spec: {spec_path}", file=sys.stderr)
        return 1

    import yaml

    model, solver = load_spec(spec_path)
    spec = yaml.safe_load(spec_path.read_text())
    name = args.name or model.name
    target = RUNS_DIR / name

    print(case.summary(model))
    _print_notes(model)
    options = _build_options(solver)
    case.build(model, target, **options)

    def step(command: str) -> None:
        # The orientation of a fan wall pair is only knowable once
        # createBaffles has run, and it is not worth a solve to find out
        # afterwards (ADR-017).
        if command == "checkMesh":
            problems = case.check_fan_orientation(target, model)
            if problems:
                raise RuntimeError(problems[0])
        print(f"  {command} ...", flush=True)

    # Reads each field write as it lands, so a long solve can be followed
    # rather than waited out (ADR-018).
    sampler = post.Sampler(model, target)
    sampler.start()
    try:
        def pass_done(record):
            print(f"  coil: {record.summary()}", flush=True)

        # No pass count and no segment length from the case: the loop runs
        # until the room and the machines agree, and how many passes that
        # takes is numerics, not a setting (ADR-124).
        steps, passes = run_module.solve_coupled(
            target, case.pipeline(options["processors"]), model,
            on_step=step,
            on_pass=pass_done if solver.get("couple", True) else None,
        ) if solver.get("couple", True) else (
            _solve_uncoupled(target, case.pipeline(options["processors"]), step), []
        )
    except (FoamNotInstalled, FoamCommandFailed) as error:
        print(f"error: {error}", file=sys.stderr)
        if isinstance(error, FoamCommandFailed):
            _tail(error.log)
        return 1
    finally:
        sampler.stop()

    print(f"Solved in {sum(s.seconds for s in steps):.1f}s")
    if passes:
        last = passes[-1]
        print(
            f"Coil coupling: {len(passes)} pass(es); the supply air temperature "
            f"is a result, not an input"
            + (f", settled to {last.moved_k:.3f} K" if last.converged else
               f", still moving {last.moved_k:.3f} K at the pass limit"
               if last.moved_k is not None else
               ", on a single pass with nothing to compare it against")
        )
    if args.no_post:
        return 0
    return _export(model, target, name, time=None, spec=spec)


def _stop(args) -> int:
    """Stop a solve the way a solver is meant to be stopped.

    Not a signal: OpenFOAM re-reads its controlDict every iteration, so this
    asks it to finish the iteration it is on, write the field and exit. The
    run then post-processes normally and what is left is a result -- partial,
    and held to the same eleven checks, so a run stopped before it settled
    says so rather than passing quietly.
    """
    from aicfd.run import request_stop, stop_requested

    run = RUNS_DIR / args.name
    if not run.exists():
        print(f"error: no run named '{args.name}' under {RUNS_DIR}", file=sys.stderr)
        return 1
    already = stop_requested(run)
    if not request_stop(run):
        print(
            f"error: {run}/system/controlDict is not a running case's "
            "controlDict, so there is nothing to stop.",
            file=sys.stderr,
        )
        return 1
    if already:
        print(f"'{args.name}' was already asked to stop.")
    else:
        print(f"Asked '{args.name}' to stop at its next iteration.")
    print(
        "The solver finishes the iteration it is on, writes the field and "
        "exits; the run then post-processes as usual.\n"
        "A run stopped before it settled will fail the 'settled' check, and "
        "may fail 'energy_closure' and 'return_path' too. That is the answer "
        "being honest about how far it got, not a fault.\n"
        "Nothing is left behind: 'aicfd run' rewrites controlDict."
    )
    return 0


def _post(args) -> int:
    run = RUNS_DIR / args.name
    if not run.exists():
        print(f"error: no run named '{args.name}' under {RUNS_DIR}", file=sys.stderr)
        return 1
    spec = CASES_DIR / f"{args.name}.yaml"
    if not spec.exists():
        print(f"error: no spec at {spec} to read the run with", file=sys.stderr)
        return 1
    import yaml

    model, _solver = load_spec(spec)
    return _export(model, run, args.name, args.time,
                   yaml.safe_load(spec.read_text()))


def _export(model, run: Path, name: str, time: str | None,
            spec: dict | None = None) -> int:
    from aicfd import post

    out = RESULTS_DIR / name
    try:
        results = post.export(model, run, out, time, spec)
    except ValueError as error:
        # A run with nothing solved in it is a normal mistake, not a crash:
        # say what to change rather than printing a traceback at someone.
        print(f"error: {error}", file=sys.stderr)
        return 1
    print()
    print(post.report(results))
    print(f"Wrote {out}/viewer.json, fields.bin, report.md")
    print(f"View with:  aicfd view --case {name}")
    print(f"Word report:  aicfd report {name}")
    return 0 if results.valid else 2


def _report(args) -> int:
    """The Word deliverable, built from an exported result and nothing else."""
    from aicfd.report import build

    source = find_result(args.name)
    if source is None:
        print(
            f"error: no exported result for '{args.name}' in {RESULTS_DIR} or "
            f"{REFERENCE_DIR}. Run 'aicfd post {args.name}' first.",
            file=sys.stderr,
        )
        return 1
    # Reports go to reports/, never into the export they were read from.
    # Writing beside the source would have producing a document modify a
    # result -- and, for a shipped result, modify a file the repository
    # tracks. Same place whether it came from this command or the button on
    # the results page (ADR-032).
    out = (Path(args.out) if args.out
           else REPORTS_DIR / args.name / f"{args.name}-cfd-report.docx")
    written = build(source, out, client=args.client, author=args.author,
                    title=args.title, ramp=args.colours)
    print(f"Wrote {written}")
    print(f"Figures in {written.parent / 'figures'}")
    return 0


def _view(args) -> int:
    """Serve the page: check the model, set parameters, run, read the result."""
    import time

    from aicfd.server import serve

    name = args.case or _newest_case()
    if name is None:
        print(
            f"error: no case specs in {CASES_DIR}. Write one with 'aicfd new'.",
            file=sys.stderr,
        )
        return 1

    url = serve(name, args.port)
    print(f"Serving {url}\nPress Ctrl+C to stop.")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print()
    return 0


# --- the audit ----------------------------------------------------------------


def _verify(args) -> int:
    """Prove this checkout reproduces, on this machine, from nothing.

    Four steps, each one a thing that can be wrong independently: the unit
    tests (the code does what it says), the OpenFOAM install (the solver is
    usable at all), the mesh of every worked case (the geometry survives the
    surgery -- ADR-016), and, with --solve, a short run of the POD whose
    eleven physical checks have to pass (ADR-018).

    This is what an engineer or an agent runs first in a fresh sandbox, and
    what a reviewer runs to disbelieve a result.
    """
    import unittest

    print("=" * 72)
    print("1/4  unit tests")
    print("=" * 72)
    suite = unittest.defaultTestLoader.discover(str(TESTS_DIR))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        print("\nVERIFY FAILED: unit tests", file=sys.stderr)
        return 1

    print()
    print("=" * 72)
    print("2/4  OpenFOAM install")
    print("=" * 72)
    if _doctor(args) != 0:
        print("\nVERIFY FAILED: OpenFOAM is not usable here", file=sys.stderr)
        return 1

    print()
    print("=" * 72)
    print("3/4  mesh every worked case")
    print("=" * 72)
    specs = sorted(CASES_DIR.glob("*.yaml"))
    if not specs:
        print(f"error: no case specs in {CASES_DIR}", file=sys.stderr)
        return 1
    for spec in specs:
        if _mesh_only(spec) != 0:
            print(f"\nVERIFY FAILED: meshing {spec.name}", file=sys.stderr)
            return 1

    print()
    print("=" * 72)
    print(f"4/4  solve the POD for {args.iterations} iterations" if args.solve
          else "4/4  solve  (skipped; pass --solve to include it)")
    print("=" * 72)
    if args.solve:
        if _short_solve(CASES_DIR / "pod-fanwall.yaml", args.iterations) != 0:
            print("\nVERIFY FAILED: the physical checks", file=sys.stderr)
            return 1

    print("\nVERIFY OK")
    return 0


def _mesh_only(spec: Path) -> int:
    """Build a case and run everything up to the solver, in a scratch copy."""
    import tempfile

    from aicfd import case
    from aicfd.run import FoamCommandFailed, FoamNotInstalled, solve

    model, solver = load_spec(spec)
    print(f"\n--- {spec.name}: {model.n_cells:,} cells, {len(model.racks)} racks, "
          f"{model.fan_count} fan wall(s)")
    _print_notes(model)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / model.name
        case.build(model, target, **_build_options(solver))
        try:
            steps = solve(target, case.MESH_PIPELINE, on_step=lambda c: print(f"  {c} ...", flush=True))
        except (FoamNotInstalled, FoamCommandFailed) as error:
            print(f"error: {error}", file=sys.stderr)
            if isinstance(error, FoamCommandFailed):
                _tail(error.log)
            return 1
        problems = case.check_fan_orientation(target, model)
        if problems:
            print(f"error: {problems[0]}", file=sys.stderr)
            return 1
        log = (target / "log.checkMesh").read_text()
        if "Mesh OK" not in log:
            print("error: checkMesh did not report 'Mesh OK'", file=sys.stderr)
            return 1
        print(f"  Mesh OK, fan wall orientation OK, "
              f"{sum(s.seconds for s in steps):.1f}s")
    return 0


def _short_solve(spec: Path, iterations: int) -> int:
    """Solve the worked POD briefly and hold it to its own physical checks.

    Short on purpose: warm-started, the POD's stations stop moving within a few
    hundred iterations, which is enough for every check to mean something. A
    converged run is `aicfd run`, not the audit.
    """
    from aicfd import case, post
    from aicfd.run import FoamCommandFailed, FoamNotInstalled, solve

    if not spec.exists():
        print(f"error: {spec} is missing", file=sys.stderr)
        return 1
    model, solver = load_spec(spec)
    options = dict(
        _build_options(solver),
        max_iterations=iterations,
        sensor_interval=max(50, iterations // 4),
    )
    target = RUNS_DIR / f"verify-{model.name}"
    case.build(model, target, **options)
    sampler = post.Sampler(model, target)
    sampler.start()
    try:
        solve(target, case.pipeline(options["processors"]),
              on_step=lambda c: print(f"  {c} ...", flush=True))
    except (FoamNotInstalled, FoamCommandFailed) as error:
        print(f"error: {error}", file=sys.stderr)
        if isinstance(error, FoamCommandFailed):
            _tail(error.log)
        return 1
    finally:
        sampler.stop()

    results = post.analyse(model, target)
    print(post.report(results))
    return 0 if results.valid else 1


# --- small helpers ------------------------------------------------------------


def _newest_case() -> str | None:
    if not CASES_DIR.exists():
        return None
    specs = sorted(CASES_DIR.glob("*.yaml"), key=lambda p: p.stat().st_mtime)
    return specs[-1].stem if specs else None


def _tail(path: Path, lines: int = 20) -> None:
    if not path.exists():
        return
    print(f"\n--- last {lines} lines of {path.name} ---", file=sys.stderr)
    for line in path.read_text(errors="replace").splitlines()[-lines:]:
        print(line, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())


def _solve_uncoupled(target, pipeline, on_step):
    """`solver.couple: false`: the plain solve, and a record saying the
    coupling was OFF -- so the report states the choice instead of guessing
    at the missing record (ADR-124)."""
    from aicfd.run import solve, write_coupling_off

    steps = solve(target, pipeline, on_step=on_step)
    write_coupling_off(target)
    return steps
