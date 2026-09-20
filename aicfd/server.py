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
from aicfd import yamledit

REPO_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = REPO_ROOT / "cases"
RUNS_DIR = REPO_ROOT / "runs"
RESULTS_DIR = REPO_ROOT / "results"
REFERENCE_DIR = REPO_ROOT / "reference"
REPORTS_DIR = REPO_ROOT / "reports"

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
    # Drawn, not meshed: how far the unit reaches back into the mechanical
    # gallery (ADR-046). Defaults to the named unit's datasheet.
    "fan_depth": (("fanwall", "depth"), float, (0.1, 10.0)),
    "supply_temp_c": (("fanwall", "supply_temp_c"), float, (-10.0, 40.0)),
    # --- supply plenum ------------------------------------------------------
    # The wall into the hall doubled, the cavity between its leaves
    # pressurised, and grilles in the inner leaf deciding where the air
    # leaves (ADR-058). Off is a single wall, the unit blowing through it.
    "plenum": (("plenum", "enabled"), bool, None),
    "plenum_depth": (("plenum", "depth"), float, (0.3, 6.0)),
    "plenum_grille_width": (("plenum", "grille", "width"), float, (0.3, 12.0)),
    "plenum_grille_height": (("plenum", "grille", "height"), float, (0.3, 6.0)),
    # The same wall treatment the other way: the 13 x 13 mm mesh across the
    # opening and no plenum, for a hall already built that cannot grow
    # (ADR-060).
    "plenum_as_mesh": (("plenum", "as_mesh"), bool, None),
    "supply_grille": (("components", "supply_grille"), "component", "supply_grille"),
    "fan_static_pa": (("fanwall", "static_pressure_pa"), float, (0.0, 2_000.0)),
    # --- return grilles -----------------------------------------------------
    "grille_size": (("grilles", "size"), float, (0.1, 3.0)),
    "grille_count": (("grilles", "count"), int, (1, 200)),
    "grille_coverage": (("grilles", "coverage"), float, (0.05, 1.0)),
    # Which component fills each role. Validated against the library rather
    # than a range: a name that is not in it is not a value out of bounds, it
    # is a case pointing at nothing (ADR-048).
    "ceiling_return": (("components", "ceiling_return"), "component", "ceiling_return"),
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
    """idle | meshing | solving | exporting | done | failed"""
    step: str = ""
    message: str = ""
    case: str | None = None
    stopping: bool = False
    """A stop has been asked for and the solver has not reached it yet."""
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
                "stopping": self.stopping,
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


def case_path(name: str) -> str:
    """Where this case lives, said the shortest way a person can act on.

    Relative to the repository when it is inside it, which is what a reader
    can paste into `git restore`; absolute otherwise, because a path relative
    to somewhere they are not is worse than a long one -- and because
    `relative_to` raises rather than declines, which turned an error message
    into a second error (ADR-056).
    """
    path = CASES_DIR / f"{name}.yaml"
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


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
        if caster == "component":
            from aicfd import components as library

            choice = str(raw or "").strip()
            if choice and choice not in library.for_role(limits):
                rejected.append(
                    f"{key}: the library has no {limits} component called {choice!r}"
                )
                continue
            _place(spec, path, choice or None)
            continue
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
    return payload_for(name, load_spec(name))


def payload_for(name: str, spec: dict) -> dict:
    """Everything the page draws, from a spec that need not be on disk yet.

    Taking the spec rather than reading it is what lets a change be built
    before it is saved: a spec the generator refuses is never written, so it
    cannot take the page down with it (ADR-055).
    """
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


def solved_after(name: str, export: Path) -> str | None:
    """Whether a solve has written fields this export does not know about.

    A run that is killed rather than stopped -- Ctrl+C on the container, a
    crash, a laptop going to sleep -- leaves solved time directories behind
    and no export, so `results/` still holds the run before it. Everything
    else about that export is right, including its spec, so nothing else
    here can tell that it is out of date.

    Comparing what is on disk can: if a solved time directory is newer than
    the export, a solve happened that nobody read. This is a file-mtime
    comparison, so a clock that jumps backwards makes it say nothing; that is
    the correct failure -- it is an extra warning, not a guarantee.
    """
    run = RUNS_DIR / name
    if not run.is_dir() or not export.is_file():
        return None
    written = export.stat().st_mtime
    newer = [
        entry.name
        for entry in run.iterdir()
        if entry.is_dir() and (entry / "phi").is_file()
        and (entry / "phi").stat().st_mtime > written + 1
    ]
    if not newer:
        return None
    latest = max(newer, key=float)
    return (
        f"a solve reached iteration {latest} after this was exported, and was "
        f"never read -- a run that is killed rather than stopped leaves no "
        f"export. Run 'aicfd post {name}' to read it."
    )


def results_state(name: str, spec: dict) -> dict:
    """What the exported result is, relative to the spec now on screen.

    The page offers a link to the result, and a link that opens a *superseded*
    run under the current case's name is worse than no link: it shows numbers
    that belong to inputs the reader is no longer looking at, and says nothing
    about it. So the comparison is made here and the page is told what it has
    (ADR-030).
    """
    own = RESULTS_DIR / name / "viewer.json"
    shipped = REFERENCE_DIR / name / "viewer.json"
    # Yours shadows the one in the repository, the same way the page resolves
    # it (ADR-032).
    path = own if own.exists() else shipped
    if not path.exists():
        return {"exists": False, "matches": False, "note": "nothing exported yet"}
    try:
        exported = json.loads(path.read_text())["model"].get("spec") or {}
    except (OSError, ValueError, KeyError):
        return {"exists": True, "matches": False,
                "note": "the exported result could not be read"}
    where = "" if path == own else "the result shipped with the repository, "
    if not exported:
        return {"exists": True, "matches": False,
                "note": f"{where}exported before AICFD recorded its inputs"}
    changed = spec_differences(exported, spec)
    if not changed:
        behind = solved_after(name, path)
        if behind:
            return {"exists": True, "matches": False, "note": behind}
        return {"exists": True, "matches": True, "note": where.rstrip(", ")}
    listed = ", ".join(changed[:3]) + (f" and {len(changed) - 3} more"
                                       if len(changed) > 3 else "")
    return {"exists": True, "matches": False,
            "note": f"{where}from different inputs: {listed}"}


def read_equipment(model: str | None) -> dict:
    """One unit out of the library, or the list of them.

    The page shows the capacity table as a curve and lets it be edited. It is
    not day-to-day work -- a unit is characterised once, from the
    manufacturer's selections -- but it has to be possible, and it has to be
    visible: the table decides every capacity number in a report, and a
    number that decides a conclusion should not be buried in a file nobody
    opens (ADR-036).
    """
    from aicfd import equipment as library

    models = library.available()
    if not model:
        return {"models": models}
    unit = library.load(model)
    return {"models": models, "unit": unit.to_dict()}


def read_racks(name: str) -> dict:
    """Every rack position in this case, with the load it actually carries.

    A hall is specified by one load per rack because that is how a hall is
    bought. No hall is filled that way: positions are reserved, staged, or
    left for growth, and where the gaps sit decides how evenly the units load
    (ADR-054). So the standard is here, and beside it every position with its
    own figure.
    """
    from aicfd import model as m

    case = name
    spec = load_spec(case)
    model = m.build_model(spec)
    standard = float(spec["racks"]["load_kw"])
    stated = m.rack_loads(spec)
    rows = []
    for row in model.rows:
        for position, rack in enumerate(row.racks, start=1):
            rows.append({
                "id": rack.id,
                "row": row.id,
                "position": position,
                "load_kw": rack.load_kw,
                "stated": rack.id in stated,
                "airflow_m3h": round(rack.rated_airflow_m3h),
            })
    loaded = [r for r in rows if r["load_kw"] > 0]
    return {
        "case": case,
        "standard": {
            "load_kw": standard,
            "size": list(spec["racks"]["size"]),
            "cfm_per_kw": spec["racks"].get("airflow_cfm_per_kw"),
        },
        "racks": rows,
        "totals": {
            "positions": len(rows),
            "loaded": len(loaded),
            "unloaded": len(rows) - len(loaded),
            "load_kw": round(sum(r["load_kw"] for r in rows), 1),
            "nominal_kw": round(standard * len(rows), 1),
            "airflow_m3h": round(sum(r["airflow_m3h"] for r in rows)),
        },
    }


def write_racks(name: str, body: dict) -> dict:
    """Write the standard and the per-position loads back to the case.

    A position set to the standard is removed from the list rather than
    written as a value equal to it: a case should say what differs, so that
    changing the standard later moves every position that never disagreed
    with it.
    """
    from aicfd import model as m

    case = name
    spec = load_spec(case)
    rejected: list[str] = []

    standard = body.get("load_kw")
    if standard is not None:
        try:
            value = float(standard)
        except (TypeError, ValueError):
            rejected.append(f"load_kw: {standard!r} is not a number")
        else:
            if 0.1 <= value <= 200:
                spec["racks"]["load_kw"] = value
            else:
                rejected.append(f"load_kw: {value:g} is outside 0.1-200")

    if "loads" in body:
        known = {rack.id for rack in m.build_model(spec).racks}
        loads: dict[str, float] = {}
        for rack_id, raw in (body.get("loads") or {}).items():
            if rack_id not in known:
                rejected.append(f"{rack_id}: no such rack position")
                continue
            if raw is None or raw == "":
                continue  # back to the standard
            try:
                value = float(raw)
            except (TypeError, ValueError):
                rejected.append(f"{rack_id}: {raw!r} is not a number")
                continue
            if not 0 <= value <= 200:
                rejected.append(f"{rack_id}: {value:g} kW is outside 0-200")
                continue
            if value != float(spec["racks"]["load_kw"]):
                loads[rack_id] = value
        if loads:
            spec["racks"]["loads"] = loads
        else:
            spec["racks"].pop("loads", None)

    # Built before it is written, the same order the model page uses: nothing
    # writes a case it has not built (ADR-055).
    m.build_model(spec)
    _save_case_racks(case, spec)
    out = read_racks(case)
    out["rejected"] = rejected
    return out


def _save_case_racks(name: str, spec: dict) -> None:
    """Write the rack block back, leaving the rest of the case untouched.

    A case file is hand-written: the comment beside `airflow_cfm_per_kw` is
    what tells the next person that the number sizes the resistance. Re-dumping
    the parsed document would save the right values and lose all of that, so
    only the two lines this page owns are rewritten -- the standard, and the
    block of positions that disagree with it (ADR-048, ADR-054).
    """
    path = CASES_DIR / f"{name}.yaml"
    lines = path.read_text().split("\n")
    on_disk = yaml.safe_load("\n".join(lines)) or {}
    # Only where it actually moved: rendering `6.0` back as `6` is the same
    # number and a diff on a file nobody changed.
    if float(on_disk.get("racks", {}).get("load_kw", 0)) != float(spec["racks"]["load_kw"]):
        yamledit.set_scalar(lines, ["racks", "load_kw"], spec["racks"]["load_kw"])
    yamledit.set_map(lines, ["racks", "loads"], spec["racks"].get("loads") or {})
    text = "\n".join(lines)
    # Parsed back before it is written, for the same reason a component save
    # is: a save path that can corrupt a file corrupts it before anything
    # reads it (ADR-048).
    written = yaml.safe_load(text) or {}
    if (written.get("racks") or {}) != (spec.get("racks") or {}):
        raise ValueError("the rack block did not survive the edit; nothing was written")
    path.write_text(text)


def read_component(chosen: str | None) -> dict:
    """One component out of the library, or the list of them by role.

    The surfaces the air passes through -- ceiling return grilles, the mesh
    closing the plenum into a gallery, raised floor plates, the leakage a
    containment has -- are standards rather than per-case choices, so they
    live in a library the way a fan wall does (ADR-048).
    """
    from aicfd import components as library

    every = [library.load(name).to_dict() for name in library.available()]
    listing = {"components": every,
               "roles": [{"role": r, "label": label} for r, label in library.ROLES.items()]}
    if not chosen:
        return listing
    return {**listing, "component": library.load(chosen).to_dict()}


def write_component(chosen: str, body: dict) -> dict:
    """Write the page's draft back over one component.

    A component marked `fixed` refuses: it is the building rather than a
    choice, and the library says so rather than the page hiding the field.
    """
    from aicfd import components as library

    library.save(chosen, body)
    return read_component(chosen)


def write_equipment(model: str, body: dict) -> dict:
    """Write the page's draft back, over this unit or into a new one.

    A `save_as` name means a copy: renaming a unit in place would break every
    case file pointing at the old name without saying so, and both units
    usually have to exist anyway -- one for the studies already run, one for
    the new work.
    """
    from aicfd import equipment as library

    changes = {k: v for k, v in body.items() if k != "save_as"}
    fresh = (body.get("save_as") or "").strip()
    if fresh and fresh != model:
        library.save_as(fresh, model, changes)
        return read_equipment(fresh)
    library.save(model, changes)
    return read_equipment(model)


def results_dir_for(name: str) -> Path:
    """Where this case's result is, yours before the shipped one.

    Same rule as the results page: a result you produced shadows the worked
    one the repository carries, which is what keeps `results/` out of version
    control and re-running a worked case from colliding with the copy in the
    repository (ADR-032).
    """
    mine = RESULTS_DIR / name
    if (mine / "viewer.json").is_file():
        return mine
    shipped = REFERENCE_DIR / name
    if (shipped / "viewer.json").is_file():
        return shipped
    raise FileNotFoundError(
        f"no exported result for {name!r}. Solve it first, or run "
        f"`aicfd post {name}` if the solve is already on disk."
    )


def commands_for(name: str) -> dict:
    """Which of the result page's two commands this case can actually run.

    Asked before the buttons are shown, so a button that cannot work is not
    offered at all. A button that appears and then explains why it failed
    teaches the reader to distrust the others.
    """
    solved = (RUNS_DIR / name).is_dir()
    return {
        "report": (RESULTS_DIR / name / "viewer.json").is_file()
        or (REFERENCE_DIR / name / "viewer.json").is_file(),
        "reread": solved,
        "reread_note": (
            "Post-process the solved run again without solving it again. The "
            "analysis changes more often than the fields do, so a result on "
            "disk can be carrying an answer computed by older code."
            if solved
            else f"Nothing to re-read: runs/{name}/ is not on this machine. "
                 f"Only a case solved here can be post-processed again."
        ),
    }


def reread_run(name: str) -> dict:
    """Post-process the solved case again, without solving it again.

    The reason this is a button and not only a command: the analysis changes
    more often than the fields do. A check gets added, a capacity table gets
    corrected, a KPI gets a better definition -- and every solved run on disk
    is then carrying an answer computed by older code. Re-reading costs
    seconds against the hours the solve cost, and the alternative is a page
    quietly showing conclusions nobody can reproduce.
    """
    spec = load_spec(name)
    model = model_module.build_model(spec)
    solved = RUNS_DIR / name
    if not solved.is_dir():
        raise FileNotFoundError(
            f"runs/{name}/ is not here: the solved case it would be re-read "
            f"from was removed or never ran on this machine."
        )
    from aicfd import post

    results = post.export(model, solved, RESULTS_DIR / name, spec=spec)
    failed = [c.name for c in results.checks if not c.passed]
    return {
        "time": results.time,
        "valid": results.valid,
        "checks": len(results.checks),
        "failed": failed,
        "note": (
            f"Re-read iteration {results.time}: all {len(results.checks)} "
            f"checks pass."
            if not failed
            else f"Re-read iteration {results.time}, and these checks FAILED: "
                 f"{', '.join(failed)}."
        ),
    }


def write_report(name: str, body: dict) -> Path:
    """Build the Word report for this case's exported result.

    Written into `reports/`, beside its figures, and never into the export it
    was read from: writing there would have producing a document modify a
    result -- and, once the worked results moved to `reference/`, modify a
    file the repository tracks (ADR-032).
    """
    from aicfd.report import build, title_of

    out = REPORTS_DIR / name / f"{name}-cfd-report.docx"
    out.parent.mkdir(parents=True, exist_ok=True)
    return build(
        results_dir_for(name),
        out,
        client=(body.get("client") or "").strip() or None,
        author=(body.get("author") or "").strip() or None,
        title=(body.get("title") or "").strip() or title_of(name),
    )


def stop_run(name: str) -> dict:
    """Ask the running solve to stop cleanly, OpenFOAM's own way.

    Not a kill: the solver finishes its iteration, writes the field and exits,
    and the worker then exports as it would have at the iteration cap. What
    comes out is a result, judged by the same eleven checks -- so a run cut
    short before it settled reports that, rather than passing quietly.
    """
    from aicfd.run import request_stop

    snapshot = STATE.snapshot()
    if snapshot["stage"] != "solving":
        return {"run": snapshot,
                "blocked": "there is no solve running to stop"}
    if not request_stop(RUNS_DIR / name):
        return {"run": snapshot,
                "blocked": "this run has no controlDict to stop"}
    STATE.set(stopping=True)
    return {"run": STATE.snapshot()}


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
            stopped = STATE.snapshot()["stopping"]
            how = "stopped early" if stopped else "solved"
            STATE.set(
                stage="done",
                step="",
                stopping=False,
                message=(
                    f"{how}, all {len(results.checks)} checks passed"
                    if not failed
                    else f"{how}, and these checks FAILED: {', '.join(failed)}"
                    + (
                        " -- expected of a run cut short before it settled"
                        if stopped
                        else ""
                    )
                ),
            )
        except FoamCommandFailed as error:
            STATE.set(stage="failed", message=str(error))
        except Exception as error:  # surfaced verbatim on the page
            STATE.set(stage="failed", message=f"{type(error).__name__}: {error}")

    if STATE.snapshot()["stage"] in {"meshing", "solving", "exporting"}:
        return
    STATE.set(stage="meshing", step="", message="", case=name, stopping=False)
    threading.Thread(target=worker, daemon=True).start()


class Handler(SimpleHTTPRequestHandler):
    case_name = "pod-fanwall"

    #: Set while a response has already declared its own caching, so the
    #: default below does not send a second, contradictory header.
    _cache_control_sent = False

    def send_header(self, keyword, value):
        if keyword.lower() == "cache-control":
            self._cache_control_sent = True
        super().send_header(keyword, value)

    def end_headers(self):
        """Never let the browser keep a page or a stylesheet across a pull.

        The clone is the application (ADR-034): `git pull` has to take effect
        on the next reload, and a browser holding yesterday's `drawing.css`
        while the server hands it today's HTML breaks the page in a way that
        looks like the change being wrong rather than absent -- the exact
        failure that ADR exists to remove, one layer further out.

        `no-cache` is revalidate, not re-download: the conditional request
        still answers 304 from `Last-Modified`, so nothing is fetched twice.
        """
        if not self._cache_control_sent:
            super().send_header("Cache-Control", "no-cache")
        self._cache_control_sent = False
        super().end_headers()

    def _query(self, key: str) -> str | None:
        from urllib.parse import parse_qs, urlparse

        return (parse_qs(urlparse(self.path).query).get(key) or [None])[0]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(REPO_ROOT), **kwargs)

    def log_message(self, *args):  # keep the console for the run, not requests
        pass

    # --- routing --------------------------------------------------------------

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/model"):
            payload = self._safely(build_payload, self.case_name)
            if "error" in payload:
                # A case on disk the generator refuses leaves the page with no
                # form to fix it in, so the error has to say where the file is
                # and what to edit. The page can no longer put a case into
                # this state; one edited by hand still can (ADR-055).
                payload["case"] = self.case_name
                payload["file"] = case_path(self.case_name)
            return self._json(payload)
        if self.path.startswith("/api/racks"):
            return self._json(self._safely(read_racks, self._query("case") or self.case_name))
        if self.path.startswith("/api/components"):
            return self._json(self._safely(read_component, self._query("id")))
        if self.path.startswith("/api/equipment"):
            return self._json(self._safely(read_equipment, self._query("model")))
        if self.path.startswith("/api/commands"):
            return self._json(self._safely(commands_for, self._query("case")
                                           or self.case_name))

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
                # Built before it is saved. A value can be inside its range
                # and still describe a room that cannot exist -- seven 4 m fan
                # walls along a 26 m wall -- and the generator refuses it.
                # Saving first meant that refusal was already on disk: every
                # reload afterwards failed on the same error, so the form that
                # could undo it never loaded again, and the page was gone for
                # good (ADR-055).
                try:
                    payload = payload_for(self.case_name, spec)
                except ValueError as refused:
                    payload = build_payload(self.case_name)
                    payload["rejected"] = rejected + [str(refused)]
                    return payload
                save_spec(self.case_name, spec)
                payload["rejected"] = rejected
                return payload

            return self._json(self._safely(update))

        if self.path.startswith("/api/racks"):
            return self._json(
                self._safely(write_racks, self._query("case") or self.case_name, body)
            )
        if self.path.startswith("/api/components"):
            return self._json(
                self._safely(write_component, self._query("id"), body)
            )
        if self.path.startswith("/api/equipment"):
            return self._json(
                self._safely(write_equipment, self._query("model"), body)
            )

        if self.path.startswith("/api/post"):
            return self._json(self._safely(reread_run, self._query("case")
                                           or self.case_name))

        if self.path.startswith("/api/report"):
            return self._report(self._query("case") or self.case_name, body)

        if self.path.startswith("/api/stop"):
            return self._json(self._safely(stop_run, self.case_name))

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

    def _report(self, name: str, body: dict) -> None:
        """The document itself, as the response.

        A download rather than a path, because the page has no business
        knowing where on the filesystem the server put the file -- and because
        a path would not work at all once the page is opened from anywhere
        other than the machine that built it.
        """
        try:
            out = write_report(name, body)
            data = out.read_bytes()
        except Exception as error:
            return self._json({"error": f"{type(error).__name__}: {error}"})
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        self.send_header("Content-Disposition", f'attachment; filename="{out.name}"')
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

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
