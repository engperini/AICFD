"""Command line interface.

    aicfd new <name>                 write a starter room spec into cases/
    aicfd build <spec.yaml>          generate an OpenFOAM case from a room spec
    aicfd doctor                     check that OpenFOAM is usable here
    aicfd run <case|spec.yaml>       generate if needed, then solve into runs/
    aicfd post <name>                turn a solved run into results/NAME
    aicfd view [--port 8000]         serve the viewer

The CLI is the whole tool. The AI assistant drives these same commands rather
than a separate code path, so anything the assistant can do is reproducible by
hand -- and anything that breaks can be debugged without it.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import webbrowser
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = REPO_ROOT / "runs"
RESULTS_DIR = REPO_ROOT / "results"
CASES_DIR = REPO_ROOT / "cases"
REFERENCE_CASE = REPO_ROOT / ".claude/skills/datacenter-cfd/reference-case"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="aicfd", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    new_parser = sub.add_parser("new", help="write a starter room spec")
    new_parser.add_argument("name", help="case name; becomes cases/<name>.yaml")

    build_parser = sub.add_parser("build", help="generate a case from a room spec")
    build_parser.add_argument("spec", help="path to a room spec YAML")
    build_parser.add_argument("--out", help="output case directory (default: runs/<name>/case)")

    sub.add_parser("doctor", help="check the OpenFOAM installation")

    run_parser = sub.add_parser("run", help="solve a case")
    run_parser.add_argument(
        "case",
        nargs="?",
        default=str(REFERENCE_CASE),
        help="a room spec YAML, or a case directory "
        "(default: the bundled reference case)",
    )
    run_parser.add_argument("--name", help="run name (default: the case's folder name)")
    run_parser.add_argument(
        "--no-post", action="store_true", help="skip post-processing afterwards"
    )

    post_parser = sub.add_parser("post", help="post-process a solved run")
    post_parser.add_argument("name", help="run name under runs/")
    post_parser.add_argument("--time", help="time directory (default: the latest)")

    view_parser = sub.add_parser("view", help="serve the viewer")
    view_parser.add_argument("--port", type=int, default=8000)
    view_parser.add_argument("--case", help="case to open (default: the newest result)")
    view_parser.add_argument("--no-browser", action="store_true")

    args = parser.parse_args(argv)
    return globals()[f"_{args.command}"](args)


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
    print(f"\nSolver environment (isolated, see ADR-002):\n  " +
          "\n  ".join(f"{k}={v}" for k, v in FOAM_ENV.items()))
    return 0


STARTER_SPEC = """# Room spec for AICFD. Everything here is in engineering units -- the
# OpenFOAM side (patch velocities, porosity, heat sources, mesh) is derived.
name: {name}

room:
  size: [8.0, 5.0, 3.0]        # x (along the airflow), y (across), z (height), m

racks:
  # position is the x, y of the rack's lower corner; size is depth, width, height.
  - {{id: A1, position: [3.0, 1.0], size: [1.0, 0.6, 2.0], load_kw: 6.0}}
  - {{id: A2, position: [3.0, 1.7], size: [1.0, 0.6, 2.0], load_kw: 6.0}}

cracs:
  # airflow_m3h is required: it is what sets the room's temperature rise.
  - {{id: CRAC01, airflow_m3h: 3600, supply_temp_c: 20.0}}

mesh:
  cell_size: 0.10              # m; halving this multiplies cells by 8

solver:
  max_iterations: 2000
  residual_tolerance: 1.0e-4
"""


def _new(args) -> int:
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    path = CASES_DIR / f"{args.name}.yaml"
    if path.exists():
        print(f"error: {path} already exists", file=sys.stderr)
        return 1
    path.write_text(STARTER_SPEC.format(name=args.name))
    print(f"Wrote {path}\nEdit it, then:  aicfd run {path}")
    return 0


def _build(args) -> int:
    from aicfd import case as case_builder
    from aicfd.spec import SpecError, load

    try:
        spec = load(args.spec)
    except SpecError as error:
        print(f"error in {args.spec}: {error}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else RUNS_DIR / spec.name / "case"
    case_builder.build(spec, out)
    print(case_builder.summary(spec))
    print(f"Generated {out}")
    return 0


def _run(args) -> int:
    from aicfd.run import FoamCommandFailed, FoamNotInstalled, prepare, solve

    case = Path(args.case)
    if not case.exists():
        print(f"error: no such case or spec: {case}", file=sys.stderr)
        return 1

    # A .yaml argument is a room spec: generate the case first (ADR-004).
    if case.is_file():
        from aicfd import case as case_builder
        from aicfd.spec import SpecError, load

        try:
            spec = load(case)
        except SpecError as error:
            print(f"error in {case}: {error}", file=sys.stderr)
            return 1
        print(case_builder.summary(spec))
        name = args.name or spec.name
        target = RUNS_DIR / name
        case_builder.build(spec, target)
    else:
        name = args.name or case.name
        target = RUNS_DIR / name
        print(f"Preparing {case} -> {target}")
        prepare(case, target)

    try:
        steps = solve(target, on_step=lambda c: print(f"  {c} ...", flush=True))
    except FoamNotInstalled as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except FoamCommandFailed as error:
        print(f"error: {error}", file=sys.stderr)
        _tail(error.log)
        return 1

    total = sum(step.seconds for step in steps)
    print(f"Solved in {total:.1f}s")
    if args.no_post:
        return 0
    return _post(argparse.Namespace(name=name, time=None))


def _post(args) -> int:
    from aicfd.post import export

    case = RUNS_DIR / args.name
    if not case.exists():
        print(f"error: no run named '{args.name}' under {RUNS_DIR}", file=sys.stderr)
        return 1

    out = RESULTS_DIR / args.name
    results = export(case, out, args.time)
    print(f"\n{(out / 'report.md').read_text()}")
    print(f"Wrote {out}/viewer.json, fields.bin, report.md")
    print(f"View with:  aicfd view --case {args.name}")
    return 0 if results.valid else 2


def _view(args) -> int:
    import http.server
    import socketserver

    case = args.case or _newest_result()
    if case is None:
        print(
            f"error: nothing in {RESULTS_DIR}. Run 'aicfd run' first.", file=sys.stderr
        )
        return 1

    url = f"http://localhost:{args.port}/web/?case={case}"

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **kw):
            super().__init__(*a, directory=str(REPO_ROOT), **kw)

        def log_message(self, *a):  # keep the console readable
            pass

    with socketserver.TCPServer(("", args.port), Handler) as server:
        print(f"Serving {url}\nPress Ctrl+C to stop.")
        if not args.no_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


def _newest_result() -> str | None:
    if not RESULTS_DIR.exists():
        return None
    candidates = [d for d in RESULTS_DIR.iterdir() if (d / "viewer.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.stat().st_mtime).name


def _tail(path: Path, lines: int = 20) -> None:
    if not path.exists():
        return
    print(f"\n--- last {lines} lines of {path.name} ---", file=sys.stderr)
    for line in path.read_text(errors="replace").splitlines()[-lines:]:
        print(line, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
