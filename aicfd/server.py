"""The small HTTP server behind `aicfd view`.

The page is the interface: you open it to check the geometry *before* solving,
change the parameters that matter, start the run, and watch it converge. That
is the whole reason this exists -- a static file server would be enough to look
at results after the fact, but by then a wrong dimension has already cost you
the run.

Deliberately stdlib-only (ADR-005): no framework, no build step, nothing to
install beyond what the solver already needs.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from aicfd import model as model_module

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "cases"
RUNS_DIR = REPO_ROOT / "runs"
RESULTS_DIR = REPO_ROOT / "results"

#: Every parameter the page may change, as `key -> (path, caster, limits)`.
#:
#: The page is the template an engineer fills in, so this list is deliberately
#: the whole spec rather than a chosen few: dimensions, clearances, equipment,
#: grilles, airflows, mesh and solver. What it is not is a general YAML editor
#: -- each entry names one field, casts it, and holds it to a range, so the
#: form cannot put the spec into a state the generator has never seen.
#:
#: `vector3` takes "0.6, 1.2, 2.2"; `cell_size` takes one value or three.
EDITABLE = {
    # --- site ---------------------------------------------------------------
    "altitude_m": (("site", "altitude_m"), float, (0.0, 5_000.0)),
    # --- the room -----------------------------------------------------------
    "pods": (("pods",), int, (1, 60)),
    "hall_size": (("hall", "size"), "vector3", (0.5, 500.0)),
    "hall_height": (("hall", "height"), float, (2.5, 30.0)),
    "ceiling": (("hall", "ceiling"), float, (2.0, 20.0)),
    "gallery_depth": (("gallery", "depth"), float, (0.5, 20.0)),
    "gallery_sides": (("gallery", "sides"), int, (1, 2)),
    # --- aisles and clearances ---------------------------------------------
    "cold_aisle": (("aisles", "cold"), float, (0.6, 10.0)),
    "hot_aisle": (("aisles", "hot"), float, (0.6, 10.0)),
    "perimeter": (("aisles", "perimeter"), float, (0.6, 20.0)),
    "transverse": (("aisles", "transverse"), float, (0.6, 20.0)),
    # --- racks --------------------------------------------------------------
    "rack_count": (("racks", "count"), int, (1, 60)),
    "racks_per_row": (("racks", "per_row"), int, (1, 100)),
    "rack_blocks": (("racks", "blocks"), int, (1, 6)),
    "rack_load_kw": (("racks", "load_kw"), float, (0.1, 200.0)),
    "rack_size": (("racks", "size"), "vector3", (0.1, 3.0)),
    "rack_offset_x": (("racks", "offset_x"), float, (0.0, 50.0)),
    "rack_cfm_per_kw": (("racks", "airflow_cfm_per_kw"), float, (20.0, 400.0)),
    # --- fan walls ----------------------------------------------------------
    "fan_count": (("fanwall", "count"), int, (1, 200)),
    "airflow_m3h": (("fanwall", "airflow_m3h"), float, (100.0, 500_000.0)),
    "fan_capacity_kw": (("fanwall", "capacity_kw"), float, (0.1, 5_000.0)),
    "fan_power_kw": (("fanwall", "power_kw"), float, (0.0, 500.0)),
    "fan_width": (("fanwall", "width"), float, (0.3, 20.0)),
    "fan_height": (("fanwall", "height"), float, (0.5, 20.0)),
    "supply_temp_c": (("fanwall", "supply_temp_c"), float, (-10.0, 40.0)),
    "fan_static_pa": (("fanwall", "static_pressure_pa"), float, (0.0, 2_000.0)),
    # --- return grilles -----------------------------------------------------
    "grille_size": (("grilles", "size"), float, (0.1, 3.0)),
    "grille_count": (("grilles", "count"), int, (1, 200)),
    "grille_coverage": (("grilles", "coverage"), float, (0.05, 1.0)),
    "grille_free_area": (("grilles", "free_area"), float, (0.05, 1.0)),
    "grille_k": (("grilles", "loss_coefficient"), float, (0.0, 100.0)),
    "containment": (("containment", "enabled"), bool, None),
    # --- mesh and solver ----------------------------------------------------
    "cell_size": (("mesh", "cell_size"), "cell_size", (0.02, 1.0)),
    "max_iterations": (("solver", "max_iterations"), int, (10, 20_000)),
    "sensor_interval": (("solver", "sensor_interval"), int, (10, 5_000)),
    "warm_start": (("solver", "warm_start"), bool, None),
    "processors": (("solver", "processors"), int, (1, 64)),
}


@dataclass
class RunState:
    """What the page needs to show while a solve is in flight."""

    stage: str = "idle"
    """idle | meshing | solving | done | failed"""
    step: str = ""
    message: str = ""
    case: str | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, **kwargs) -> None:
        with self.lock:
            for key, value in kwargs.items():
                setattr(self, key, value)

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "stage": self.stage,
                "step": self.step,
                "message": self.message,
                "case": self.case,
            }


STATE = RunState()

_TIME = re.compile(r"^Time = (\d+)", re.MULTILINE)
_RESIDUAL = re.compile(
    r"Solving for (\w+), Initial residual = ([\d.eE+-]+)"
)


def read_progress(case_name: str, max_points: int = 400) -> dict:
    """Residuals so far, straight off the solver log while it is still running."""
    log = RUNS_DIR / case_name / "log.buoyantSimpleFoam"
    if not log.exists():
        return {"iterations": [], "series": {}}

    text = log.read_text(errors="replace")
    marks = list(_TIME.finditer(text))
    iterations: list[int] = []
    series: dict[str, list[float]] = {}
    # Thin the history so a long run does not ship tens of thousands of points
    # to a chart that is a few hundred pixels wide.
    stride = max(1, len(marks) // max_points)
    for index in range(0, len(marks), stride):
        mark = marks[index]
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        iterations.append(int(mark.group(1)))
        seen: set[str] = set()
        for name, value in _RESIDUAL.findall(text[mark.end() : end]):
            if name in seen:
                continue
            seen.add(name)
            series.setdefault(name, []).append(float(value))
    for values in series.values():
        while len(values) < len(iterations):
            values.insert(0, float("nan"))
    return {
        "iterations": iterations,
        "series": {
            k: [None if v != v else v for v in values] for k, values in series.items()
        },
        "total": len(marks),
    }


def read_sensors(case_name: str) -> dict:
    """What each instrumented place is doing, straight off the running solve."""
    from aicfd import post

    case = RUNS_DIR / case_name
    if not case.exists():
        return {"iterations": [], "groups": []}
    model = model_module.build_model(load_spec(case_name))
    return post.sensor_history(model, case)


def load_spec(name: str) -> dict:
    path = CASES_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no case spec at {path}")
    return yaml.safe_load(path.read_text())


def save_spec(name: str, spec: dict) -> None:
    (CASES_DIR / f"{name}.yaml").write_text(
        yaml.safe_dump(spec, sort_keys=False, allow_unicode=True)
    )


def apply_changes(spec: dict, changes: dict) -> tuple[dict, list[str]]:
    """Apply edits from the page, rejecting anything outside its declared range."""
    rejected: list[str] = []
    for key, raw in changes.items():
        if key not in EDITABLE:
            rejected.append(f"{key}: not an editable parameter")
            continue
        path, caster, limits = EDITABLE[key]
        if caster in ("cell_size", "vector3"):
            # "0.2" or "0.2, 0.2, 0.1" -- one value or three (x, y, z).
            try:
                parts = [
                    float(v) for v in str(raw).replace(";", ",").split(",") if v.strip()
                ]
            except ValueError:
                rejected.append(f"{key}: {raw!r} is not a number or three numbers")
                continue
            allowed = (1, 3) if caster == "cell_size" else (3,)
            if len(parts) not in allowed:
                rejected.append(
                    f"{key}: give {'one value or three' if caster == 'cell_size' else 'three values'} (x, y, z)"
                )
                continue
            if limits and not all(limits[0] <= v <= limits[1] for v in parts):
                rejected.append(f"{key}: outside {limits[0]:g}-{limits[1]:g}")
                continue
            _place(spec, path, parts[0] if len(parts) == 1 else parts)
            continue
        try:
            value = caster(raw)
        except (TypeError, ValueError):
            rejected.append(f"{key}: {raw!r} is not a {caster.__name__}")
            continue
        if limits and not (limits[0] <= value <= limits[1]):
            rejected.append(f"{key}: {value:g} is outside {limits[0]:g}-{limits[1]:g}")
            continue
        _place(spec, path, value)
    return spec, rejected


def _place(spec: dict, path: tuple[str, ...], value) -> None:
    """Write ``value`` at ``path``, creating the sections it needs."""
    node = spec
    for step in path[:-1]:
        node = node.setdefault(step, {})
    node[path[-1]] = value


def _processors(name: str | None) -> int:
    if not name:
        return 1
    try:
        return int(load_spec(name).get("solver", {}).get("processors", 1))
    except Exception:
        return 1


def solver_available(name: str | None = None) -> str | None:
    """Why this model cannot be solved here, or None if it can.

    The page asks before offering the button. A Run that dies on a missing
    binary tells the engineer nothing; a button that says what is missing
    tells them exactly where they stand.
    """
    from aicfd.run import check_install

    from aicfd import case

    from aicfd.run import commands

    needed = commands(case.pipeline(_processors(name)))
    found = check_install()
    missing = [name for name in needed if not found.get(name, _which(name))]
    if missing:
        return f"OpenFOAM não está completo aqui (falta {', '.join(missing)})"
    return None


def _which(name: str) -> str | None:
    import shutil

    from aicfd.run import FOAM_ENV

    return shutil.which(name, path=FOAM_ENV["PATH"])


def build_payload(name: str) -> dict:
    spec = load_spec(name)
    model = model_module.build_model(spec)
    payload = model_module.to_dict(model, spec)
    # The page needs the path of every editable field, so it can read the
    # current value out of the spec without a second copy of this table.
    payload["editable"] = {key: list(path) for key, (path, _c, _l) in EDITABLE.items()}
    payload["run"] = STATE.snapshot()
    payload["results"] = results_state(name, spec)
    payload["blocked"] = solver_available(name)
    return payload


def spec_differences(before: dict, after: dict, prefix: str = "") -> list[str]:
    """The dotted keys whose values differ between two specs."""
    changed = []
    for key in sorted(set(before) | set(after)):
        path = f"{prefix}{key}"
        a, b = before.get(key), after.get(key)
        if isinstance(a, dict) and isinstance(b, dict):
            changed += spec_differences(a, b, f"{path}.")
        elif a != b:
            changed.append(path)
    return changed


def results_state(name: str, spec: dict) -> dict:
    """What the exported result is, relative to the spec now on screen.

    The page offers a link to the result, and a link that opens a *superseded*
    run under the current case's name is worse than no link: it shows numbers
    that belong to inputs the reader is no longer looking at, and says nothing
    about it. So the comparison is made here and the page is told what it has
    (ADR-030).
    """
    path = RESULTS_DIR / name / "viewer.json"
    if not path.exists():
        return {"exists": False, "matches": False, "note": "nothing exported yet"}
    try:
        exported = json.loads(path.read_text())["model"].get("spec") or {}
    except (OSError, ValueError, KeyError):
        return {"exists": True, "matches": False,
                "note": "the exported result could not be read"}
    if not exported:
        return {"exists": True, "matches": False,
                "note": "exported before AICFD recorded the inputs with a result"}
    changed = spec_differences(exported, spec)
    if not changed:
        return {"exists": True, "matches": True, "note": ""}
    listed = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more"
                                       if len(changed) > 3 else "")
    return {"exists": True, "matches": False,
            "note": f"from different inputs: {listed}"}


def start_run(name: str) -> None:
    """Solve in a worker thread so the page stays responsive."""
    from aicfd import case, post
    from aicfd.run import FoamCommandFailed, solve

    def worker() -> None:
        try:
            spec = load_spec(name)
            model = model_module.build_model(spec)
            solver = spec.get("solver", {})
            STATE.set(stage="meshing", step="build", message="", case=name)
            target = RUNS_DIR / name
            processors = int(solver.get("processors", 1))
            case.build(
                model,
                target,
                max_iterations=int(solver.get("max_iterations", 400)),
                residual_tolerance=float(solver.get("residual_tolerance", 1e-4)),
                sensor_interval=int(solver.get("sensor_interval", 100)),
                warm_start_field=bool(solver.get("warm_start", True)),
                processors=processors,
            )

            def step(command: str) -> None:
                # The orientation of the fan wall pair is only knowable once
                # createBaffles has run, and it is not worth a solve to find
                # out afterwards.
                if command == "checkMesh":
                    problems = case.check_fan_orientation(target, model)
                    if problems:
                        raise RuntimeError(problems[0])
                STATE.set(
                    stage="solving" if command.endswith("Foam") else "meshing",
                    step=command,
                )

            # Read each field write as it lands, so the run can be watched
            # rather than waited out -- and so purgeWrite is free to delete
            # the fields once they have been measured.
            sampler = post.Sampler(model, target)
            sampler.start()
            try:
                solve(target, case.pipeline(processors), on_step=step)
            finally:
                sampler.stop()
            # Export here, not later and not by hand. A solve that leaves no
            # export leaves results/<name>/ holding the PREVIOUS run, under
            # this run's name -- so the page's own "See results" opened a
            # superseded answer and said nothing about it (ADR-030).
            STATE.set(stage="exporting", step="post", message="")
            results = post.export(model, target, RESULTS_DIR / name, spec=spec)
            failed = [c.name for c in results.checks if not c.passed]
            STATE.set(
                stage="done",
                step="",
                message=(
                    f"solved, all {len(results.checks)} checks passed"
                    if not failed
                    else f"solved, but these checks FAILED: {', '.join(failed)}"
                ),
            )
        except FoamCommandFailed as error:
            STATE.set(stage="failed", message=str(error))
        except Exception as error:  # surfaced verbatim on the page
            STATE.set(stage="failed", message=f"{type(error).__name__}: {error}")

    if STATE.snapshot()["stage"] in {"meshing", "solving"}:
        return
    STATE.set(stage="meshing", step="", message="", case=name)
    threading.Thread(target=worker, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    case_name = "pod-fanwall"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def log_message(self, *args):  # keep the console for the run, not requests
        pass

    # --- routing --------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/model"):
            return self._json(self._safely(build_payload, self.case_name))
        if self.path.startswith("/api/progress"):
            return self._json(
                {
                    "run": STATE.snapshot(),
                    "residuals": read_progress(self.case_name),
                    "sensors": self._safely(read_sensors, self.case_name),
                }
            )
        return super().do_GET()

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")

        if self.path.startswith("/api/model"):
            def update():
                spec, rejected = apply_changes(load_spec(self.case_name), body)
                save_spec(self.case_name, spec)
                payload = build_payload(self.case_name)
                payload["rejected"] = rejected
                return payload

            return self._json(self._safely(update))

        if self.path.startswith("/api/run"):
            blocked = solver_available()
            if blocked:
                return self._json({"run": STATE.snapshot(), "blocked": blocked})
            start_run(self.case_name)
            return self._json({"run": STATE.snapshot()})

        self.send_error(404)

    # --- helpers --------------------------------------------------------------

    def _safely(self, fn, *args):
        try:
            return fn(*args)
        except Exception as error:
            return {"error": f"{type(error).__name__}: {error}"}

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def serve(case_name: str, port: int = 8000) -> str:
    Handler.case_name = case_name
    server = ThreadingHTTPServer(("", port), Handler)
    url = f"http://localhost:{port}/web/?case={case_name}"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return url
