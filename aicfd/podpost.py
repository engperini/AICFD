"""Turn a solved POD into numbers an engineer can act on -- and can trust.

The checks here are deliberately not residuals. A steady solver's residuals say
how much the last iteration changed the field; they do not say whether the
field means anything. The 300-iteration run that motivated this module had
residuals falling steadily and was returning air at 20,5 degC against a design
return of 30,8 -- it had converged on nothing in particular, and nothing in the
solver log said so.

What does say so is the energy balance. Every watt of IT load has to leave
through the fan intake as warmer air:

    Q = cp * sum over the intake faces of  phi_i * (T_i - T_supply)

If that does not come back as the load, the case is either unconverged or
wrong, and the two are distinguished by watching it across written times. It
is the one number that cannot be satisfied by accident.

Everything here reads patch values, never the nearest cell centres. What
crosses a patch is ``phi`` on that patch; approximating it from cell-centre
velocity is wrong by tens of percent on a porous zone or a grille jet, which
is how a perfectly sealed POD first looked like it was leaking 37% of its air.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aicfd.foam.fields import patch_names, read_field, read_patch_field, to_grid
from aicfd.model import CP_AIR, Model
from aicfd.podcase import FAN_INTAKE, FAN_SUPPLY, KELVIN
from aicfd.post import ASHRAE_RECOMMENDED, Check, _ashrae_verdict

#: How far the energy balance may miss before the run is not to be believed.
#: Loose, because it is catching "the field never filled", not modelling error.
ENERGY_TOLERANCE = 0.10

#: Mass has nowhere to go in a closed loop, so this is tight.
MASS_TOLERANCE = 1.0e-3

#: Reverse flow through a patch the fan is supposed to control. Anything above
#: this means the boundary condition, not the room, is deciding the flow.
BACKFLOW_TOLERANCE = 0.02

#: Peak speed is checked against this many times the larger of the supply face
#: velocity and the buoyant velocity scale -- see ADR-013.
PLAUSIBLE_SPEED_MARGIN = 5.0

#: Places on the return path, in the order the air passes through them.
#: Between the rack outlet and the fan intake nothing adds or removes heat --
#: every wall is adiabatic and the containment is sealed -- so at steady state
#: they must all read the same temperature. Any spread between them is air that
#: has not finished arriving.
RETURN_PATH = ("hot_aisle", "plenum", "fan_back")

#: How far apart the return path may read before the run is not settled, in
#: kelvin. Loose enough for real stratification in a 1,5 m plenum, tight enough
#: to catch a volume still filling.
RETURN_PATH_TOLERANCE = 1.5

#: How far the pressure drop the solver delivers across the rack row may sit
#: from the one the spec asked for, as a fraction.
#:
#: This is not a convergence check -- it is a check that the *model* is
#: delivering the resistance it was given. It exists because it currently
#: fails: the field shows 5,3 Pa across the row where the rack's own curve, at
#: the airflow the fan is measurably moving, demands 25,8 Pa. An isolated duct
#: with the same porous coefficients reproduces the analytic drop to 0,2%, so
#: the coefficients are right and something about the zone in situ is not.
#: Until that is understood, a fan-pressure number read off this field is too
#: low and must not be used to size a machine.
RESISTANCE_TOLERANCE = 0.25

#: How much an instrumented place may still be moving between samples before
#: the run counts as settled, in kelvin.
#:
#: Energy closure alone is not enough, and finding that out cost a run. It
#: works as a verdict from a *cold* start, where it climbs from zero as the
#: heat works its way round the loop. Seed the field warm -- which is the right
#: thing to do, the gallery is a third of the domain -- and the balance reads
#: 101% at iteration 100 because the seed put it there, while the contained hot
#: aisle is still swinging 27,5 -> 25,1 -> 26,5 degC between samples. A closed
#: balance says the field is *consistent*; only stillness says it is *settled*.
#:
#: And stillness is not enough on its own either, for the opposite reason: it
#: measures *speed*, not *distance remaining*. A seeded run passed this at
#: 0,103 K while its return plenum sat 3,2 K below the hot aisle feeding it and
#: was closing that gap at 0,1 K per hundred iterations -- three thousand
#: iterations from its answer, and perfectly still by this test. That is what
#: RETURN_PATH_TOLERANCE catches.
STEADY_TOLERANCE = 0.25


@dataclass
class PodResults:
    case_name: str
    time: str
    kpis: dict
    checks: list[Check]

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)


def written_times(case_dir: str | Path) -> list[str]:
    """Time directories that hold a solved field, oldest first."""
    case = Path(case_dir)
    times = [
        entry.name
        for entry in case.iterdir()
        if entry.is_dir() and _is_number(entry.name) and (entry / "phi").exists()
    ]
    return sorted(times, key=float)


def analyse(model: Model, case_dir: str | Path, time: str | None = None) -> PodResults:
    case = Path(case_dir)
    sample(model, case)  # make sure the latest write has been read
    time = time or (written_times(case)[-1] if written_times(case) else "0")
    step = case / time

    flows = patch_flows(step)
    supply = -flows[FAN_SUPPLY]  # negative into the domain; state it as positive
    intake = flows[FAN_INTAKE]

    kpis = {
        "supply_kg_s": round(supply, 5),
        "intake_kg_s": round(intake, 5),
        "supply_m3h": round(supply / _density(model) * 3600, 0),
        "backflow_kg_s": round(backflow(step, FAN_INTAKE), 5),
        "supply_temp_c": model.supply_temp_c,
        "return_temp_c": round(return_temperature(step) - KELVIN, 2),
        "load_kw": round(model.total_load_w / 1000, 2),
    }
    kpis["delta_t_k"] = round(kpis["return_temp_c"] - kpis["supply_temp_c"], 2)
    recovered = recovered_load_w(step, model)
    kpis["recovered_kw"] = round(recovered / 1000, 2)
    kpis["energy_closure"] = round(recovered / model.total_load_w, 4) if model.total_load_w else None

    grid = read_grid(model, step)
    kpis["peak_speed_ms"] = round(float(np.linalg.norm(grid["U"], axis=0).max()), 3)
    kpis["peak_air_temp_c"] = round(float(grid["T"].max()), 2)
    kpis["racks"] = rack_temperatures(model, grid)

    kpis["rack_drop_pa"] = rack_pressure_drop(model, grid)
    kpis["fan_rise_pa"] = round(
        float(
            np.mean(read_patch_field(step / "p_rgh", FAN_SUPPLY))
            - np.mean(read_patch_field(step / "p_rgh", FAN_INTAKE))
        ),
        3,
    )
    if model.fan_static_pa:
        kpis["fan_static_pa"] = model.fan_static_pa
        kpis["fan_margin"] = round(kpis["fan_rise_pa"] / model.fan_static_pa, 4)
    kpis["drift_k"] = drift(case)
    history = read_history(case)
    kpis["places_now"] = history[-1]["places"] if history else []
    return PodResults(
        case_name=model.name, time=time, kpis=kpis, checks=_checks(model, step, kpis, grid)
    )


def rack_pressure_drop(model: Model, grid: dict) -> float | None:
    """The drop the solved field actually shows across the rack row, in Pa.

    Sampled one cell either side of the row, outside the porous cells: the
    reconstructed velocity inside a porous zone is not trustworthy, but the
    pressure in free cells on either side of it is.
    """
    if not model.racks:
        return None
    axis = model.racks[0].airflow_axis
    lo = model.racks[0].box.lo[axis] - model.cell_size / 2
    hi = model.racks[0].box.hi[axis] + model.cell_size / 2
    row = model.rack_span()
    coords = (grid["x"], grid["y"], grid["z"])

    def plane(position: float) -> float:
        picks = []
        for a in range(3):
            if a == axis:
                picks.append([int(np.argmin(abs(coords[a] - position)))])
            elif a == 0:
                picks.append(
                    np.where((coords[0] >= row[0]) & (coords[0] <= row[1]))[0]
                )
            else:
                picks.append(
                    np.where(coords[a] <= model.racks[0].box.hi[a])[0]
                )
        return float(grid["p_rgh"][np.ix_(picks[2], picks[1], picks[0])].mean())

    return round(plane(lo) - plane(hi), 3)


def drift(case_dir: str | Path) -> float | None:
    """The largest move any instrumented place made between the last two samples.

    None when there is nothing to compare yet. This is what distinguishes a
    settled field from one that merely satisfies its balances -- see
    STEADY_TOLERANCE.
    """
    history = read_history(case_dir)
    if len(history) < 2:
        return None
    last, previous = history[-1], history[-2]
    before = {place["name"]: place["temp_c"] for place in previous["places"]}
    moves = [
        abs(place["temp_c"] - before[place["name"]])
        for place in last["places"]
        if place["name"] in before
    ]
    moves.append(abs(last["return_temp_c"] - previous["return_temp_c"]))
    return round(max(moves), 3) if moves else None


# --- the quantities -----------------------------------------------------------


def patch_flows(step: str | Path) -> dict[str, float]:
    """Net mass flow through every patch, in kg/s, positive leaving the domain."""
    phi = Path(step) / "phi"
    return {
        name: float(np.sum(read_patch_field(phi, name))) for name in patch_names(phi)
    }


def backflow(step: str | Path, patch: str) -> float:
    """Mass flowing the wrong way through a patch, in kg/s."""
    phi = read_patch_field(Path(step) / "phi", patch)
    if phi.size == 0:
        return 0.0
    net = float(phi.sum())
    wrong_way = np.clip(phi, None, 0) if net > 0 else np.clip(phi, 0, None)
    return float(abs(wrong_way.sum()))


def return_temperature(step: str | Path) -> float:
    """Mixed-mean temperature of the air leaving through the fan intake, in K.

    Flow-weighted, not area-averaged: a patch where half the area carries most
    of the flow has an area average that describes no air that ever existed.
    """
    step = Path(step)
    phi = read_patch_field(step / "phi", FAN_INTAKE)
    temperature = read_patch_field(step / "T", FAN_INTAKE)
    if temperature.size == 0:
        raise ValueError(
            f"{step}/T carries no value on '{FAN_INTAKE}'. A zeroGradient "
            "outlet stores nothing, and the temperature of the air crossing "
            "that patch is what the energy balance is built from -- the "
            "generator has to write it as inletOutlet."
        )
    if phi.size == 0:
        return float("nan")
    if temperature.size == 1:
        return float(temperature[0])
    leaving = np.clip(phi, 0, None)
    return float((leaving * temperature).sum() / leaving.sum())


def recovered_load_w(step: str | Path, model: Model) -> float:
    """The IT load the air is actually carrying out, in watts."""
    step = Path(step)
    phi = read_patch_field(step / "phi", FAN_INTAKE)
    temperature = read_patch_field(step / "T", FAN_INTAKE)
    if phi.size == 0 or temperature.size <= 1:
        return 0.0  # a uniform patch value cannot resolve a mixed-mean rise
    supply_k = model.supply_temp_c + KELVIN
    return float(CP_AIR * (phi * (temperature - supply_k)).sum())


def read_grid(model: Model, step: str | Path) -> dict:
    """The cell fields as ``[k, j, i]`` grids, with the axis coordinates."""
    step = Path(step)
    n, divisions, cell = model.n_cells, model.divisions, model.cell_size
    return {
        "T": to_grid(read_field(step / "T", n), divisions) - KELVIN,
        "p_rgh": to_grid(read_field(step / "p_rgh", n), divisions),
        "U": np.stack(
            [to_grid(read_field(step / "U", n)[:, axis], divisions) for axis in range(3)]
        ),
        "x": (np.arange(divisions[0]) + 0.5) * cell,
        "y": (np.arange(divisions[1]) + 0.5) * cell,
        "z": (np.arange(divisions[2]) + 0.5) * cell,
    }


def rack_temperatures(model: Model, grid: dict) -> list[dict]:
    """Inlet and outlet temperature for each rack, at the faces it breathes through.

    The inlet face is the row of cells just upstream of the rack, not the first
    row inside it: inside the porous zone the air has already started heating.
    """
    x, y, z = grid["x"], grid["y"], grid["z"]
    cell = model.cell_size
    rows = []
    for rack in model.racks:
        axis = rack.airflow_axis
        before = rack.box.lo[axis] - cell / 2
        after = rack.box.hi[axis] + cell / 2
        span = {
            other: (rack.box.lo[other], rack.box.hi[other])
            for other in range(3)
            if other != axis
        }

        def face(position: float) -> float:
            picks = []
            for a, coords in ((0, x), (1, y), (2, z)):
                if a == axis:
                    picks.append([int(np.argmin(abs(coords - position)))])
                else:
                    lo, hi = span[a]
                    picks.append(np.where((coords >= lo) & (coords <= hi))[0])
            return float(grid["T"][np.ix_(picks[2], picks[1], picks[0])].mean())

        inlet, outlet = face(before), face(after)
        rows.append(
            {
                "id": rack.id,
                "load_kw": rack.load_kw,
                "inlet_c": round(inlet, 2),
                "outlet_c": round(outlet, 2),
                "rise_k": round(outlet - inlet, 2),
                "ashrae": _ashrae_verdict(inlet),
            }
        )
    return rows


# --- the checks ---------------------------------------------------------------


def _checks(model: Model, step: Path, kpis: dict, grid: dict) -> list[Check]:
    checks: list[Check] = []
    flows = patch_flows(step)

    supply, intake = kpis["supply_kg_s"], kpis["intake_kg_s"]
    error = abs(supply - intake) / supply if supply else 1.0
    checks.append(
        Check(
            "mass_balance",
            error < MASS_TOLERANCE,
            f"fan supplies {supply:.5f} kg/s and draws {intake:.5f} kg/s "
            f"({error * 100:.3f}% apart)",
        )
    )

    leaks = {
        name: flow
        for name, flow in flows.items()
        if name not in {FAN_INTAKE, FAN_SUPPLY} and abs(flow) > MASS_TOLERANCE * supply
    }
    checks.append(
        Check(
            "sealed_envelope",
            not leaks,
            "every wall and baffle carries zero flow"
            if not leaks
            else "flow through surfaces that should be solid: "
            + ", ".join(f"{n} {v:+.4g} kg/s" for n, v in leaks.items()),
        )
    )

    reverse = kpis["backflow_kg_s"]
    checks.append(
        Check(
            "no_backflow",
            reverse <= BACKFLOW_TOLERANCE * intake,
            f"{reverse:.5f} kg/s reverses through {FAN_INTAKE}, "
            f"{reverse / intake * 100:.1f}% of the {intake:.3f} kg/s it moves"
            if intake
            else "no flow through the fan",
        )
    )

    closure = kpis["energy_closure"]
    if closure is None:
        detail = "no IT load to account for"
    else:
        detail = (
            f"the return air carries {kpis['recovered_kw']:.2f} kW of the "
            f"{kpis['load_kw']:.2f} kW installed ({closure * 100:.0f}%)"
        )
        if closure < 1 - ENERGY_TOLERANCE:
            detail += " -- the field has not filled; run more iterations"
    checks.append(
        Check(
            "energy_closure",
            closure is not None and abs(closure - 1.0) <= ENERGY_TOLERANCE,
            detail,
        )
    )

    path = {
        place["name"]: place["temp_c"]
        for place in kpis.get("places_now", [])
        if place["name"] in RETURN_PATH
    }
    if len(path) == len(RETURN_PATH):
        spread = max(path.values()) - min(path.values())
        checks.append(
            Check(
                "return_path",
                spread <= RETURN_PATH_TOLERANCE,
                "nothing heats or cools the air between the rack outlet and the "
                "fan intake, so these have to agree: "
                + ", ".join(f"{name} {path[name]:.1f}" for name in RETURN_PATH)
                + f" degC ({spread:.2f} K apart)"
                + ("" if spread <= RETURN_PATH_TOLERANCE else " -- still filling"),
            )
        )

    delivered = kpis.get("rack_drop_pa")
    asked = model.rack_pressure_drop_pa
    if delivered is not None and asked > 0:
        ratio = delivered / asked
        checks.append(
            Check(
                "rack_resistance",
                abs(ratio - 1.0) <= RESISTANCE_TOLERANCE,
                f"the field drops {delivered:.1f} Pa across the row where the "
                f"rack curve at {model.airflow_m3h:,.0f} m3/h asks for "
                f"{asked:.1f} Pa ({ratio * 100:.0f}%)"
                + (
                    ""
                    if abs(ratio - 1.0) <= RESISTANCE_TOLERANCE
                    else " -- any fan pressure taken from this field is wrong"
                ),
            )
        )

    rise = kpis.get("fan_rise_pa")
    available = model.fan_static_pa
    if rise is not None and available:
        checks.append(
            Check(
                "fan_capacity",
                rise <= available,
                f"the POD costs {rise:.1f} Pa and the fan wall's datasheet "
                f"offers {available:.0f} Pa ({rise / available * 100:.0f}%)"
                + ("" if rise <= available else " -- the unit cannot deliver this airflow"),
            )
        )

    moved = kpis.get("drift_k")
    checks.append(
        Check(
            "settled",
            moved is not None and moved <= STEADY_TOLERANCE,
            "not enough samples to tell whether anything is still moving"
            if moved is None
            else f"the places moved at most {moved:.2f} K since the previous "
            f"sample (settled below {STEADY_TOLERANCE:.2f} K)",
        )
    )

    hottest = max(grid["T"].max(), 0)
    inlets = [rack["inlet_c"] for rack in kpis["racks"]]
    worst = max(inlets) if inlets else model.supply_temp_c
    verdict = _ashrae_verdict(worst)
    checks.append(
        Check(
            "ashrae_inlet",
            not verdict["above_recommended"],
            f"warmest rack inlet {worst:.1f} degC -- {verdict['verdict']}",
        )
    )

    limit = PLAUSIBLE_SPEED_MARGIN * max(
        model.face_velocity("fan"), _buoyant_velocity(model)
    )
    checks.append(
        Check(
            "plausible_velocity",
            kpis["peak_speed_ms"] <= limit,
            f"peak {kpis['peak_speed_ms']:.2f} m/s against a plausible "
            f"{limit:.2f} m/s (peak air {hottest:.1f} degC)",
        )
    )
    return checks


def _buoyant_velocity(model: Model) -> float:
    rise = max(min(model.design_delta_t_k, 30.0), 1.0)
    return float((2 * 9.81 * rise / 293.0 * model.domain.size[2]) ** 0.5)


def _density(model: Model) -> float:
    from aicfd.podcase import supply_density

    return supply_density(model)


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


# --- sensors ------------------------------------------------------------------

#: Where a running solve records what its instrumented places are doing. Kept
#: beside the case rather than in memory so a sample survives the page being
#: closed, the server restarting, and purgeWrite deleting the fields it came
#: from.
SENSOR_FILE = "sensors.json"

#: A time directory is only sampled once it is complete. OpenFOAM closes every
#: field file with its own footer, so this is a reliable end-of-write marker --
#: sampling a half-written field would record numbers that never existed.
FOOTER = "// *****"


def sample(model: Model, case_dir: str | Path) -> list[dict]:
    """Record any time directories that have appeared since the last call.

    Returns the whole history, oldest first. Idempotent: a time already
    recorded is not read again, which is what makes this safe to call from a
    poller as often as it likes.
    """
    case = Path(case_dir)
    history = read_history(case)
    seen = {row["iteration"] for row in history}

    for time in written_times(case):
        iteration = int(float(time))
        if iteration in seen or not _complete(case / time):
            continue
        try:
            history.append(measure(model, case / time, iteration))
        except (OSError, ValueError):
            continue  # the solver was still writing; it will be caught next time

    history.sort(key=lambda row: row["iteration"])
    (case / SENSOR_FILE).write_text(json.dumps(history))
    return history


def read_history(case_dir: str | Path) -> list[dict]:
    path = Path(case_dir) / SENSOR_FILE
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError:
        return []


def measure(model: Model, step: str | Path, iteration: int) -> dict:
    """One reading: every instrumented place, plus the two balances.

    The places come from the cells the probes sit in; the balances come from
    the patch values, because what crosses a patch is phi on that patch.
    """
    from aicfd.model import sensors

    step = Path(step)
    grid = read_grid(model, step)
    flows = patch_flows(step)
    supply = -flows.get(FAN_SUPPLY, 0.0)
    intake = flows.get(FAN_INTAKE, 0.0)
    recovered = recovered_load_w(step, model)

    # Pressure is reported against the fan intake, so it reads as what a
    # manometer in the room would show: how many pascals above the machine's
    # suction each place sits. The absolute value is 101 325 Pa everywhere and
    # says nothing. p_rgh rather than p because it has the hydrostatic column
    # removed -- comparing a point at 1 m with one at 7 m on static pressure
    # alone would just measure the height difference.
    reference = float(np.mean(read_patch_field(step / "p_rgh", FAN_INTAKE)))

    places = []
    for group in sensors(model):
        temperatures = [_at(grid, "T", point) for point in group.points]
        speeds = [float(np.linalg.norm(_at(grid, "U", point))) for point in group.points]
        pressures = [_at(grid, "p_rgh", point) - reference for point in group.points]
        places.append(
            {
                "name": group.name,
                "label": group.label,
                "note": group.note,
                "temp_c": round(float(np.mean(temperatures)), 3),
                "pressure_pa": round(float(np.mean(pressures)), 3),
                "pressure_spread_pa": round(
                    float(max(pressures) - min(pressures)), 3
                ),
                "speed_spread_ms": round(float(max(speeds) - min(speeds)), 4),
                # How much the three points disagree. A place whose probes are
                # kelvin apart is not one place, and its mean should not be
                # read as if it were.
                "spread_k": round(float(max(temperatures) - min(temperatures)), 3),
                "speed_ms": round(float(np.mean(speeds)), 4),
                "points_c": [round(t, 2) for t in temperatures],
            }
        )

    return {
        "iteration": iteration,
        "places": places,
        "supply_kg_s": round(supply, 5),
        "intake_kg_s": round(intake, 5),
        "backflow_kg_s": round(backflow(step, FAN_INTAKE), 5),
        "return_temp_c": round(return_temperature(step) - KELVIN, 2),
        "recovered_kw": round(recovered / 1000, 3),
        "closure": round(recovered / model.total_load_w, 4)
        if model.total_load_w
        else None,
        "peak_speed_ms": round(float(np.linalg.norm(grid["U"], axis=0).max()), 3),
        "peak_temp_c": round(float(grid["T"].max()), 2),
        # What the fan wall has to produce: the static rise across it. This is
        # the number that sizes the machine, and it is not something the user
        # gave -- it is what the POD's own resistance turned out to be.
        "fan_rise_pa": round(
            float(
                np.mean(read_patch_field(step / "p_rgh", FAN_SUPPLY))
                - np.mean(read_patch_field(step / "p_rgh", FAN_INTAKE))
            ),
            3,
        ),
    }


def _at(grid: dict, field: str, point) -> float:
    """The field value in the cell containing ``point``."""
    i = int(np.argmin(abs(grid["x"] - point[0])))
    j = int(np.argmin(abs(grid["y"] - point[1])))
    k = int(np.argmin(abs(grid["z"] - point[2])))
    values = grid[field]
    return values[..., k, j, i] if values.ndim == 4 else values[k, j, i]


def _complete(step: Path) -> bool:
    for name in ("T", "U", "phi", "p_rgh"):
        path = step / name
        if not path.exists():
            return False
        try:
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 200))
                if FOOTER.encode() not in handle.read():
                    return False
        except OSError:
            return False
    return True


class Sampler(threading.Thread):
    """Reads each new time directory while the solver is still running.

    A steady run that takes half an hour is not worth watching through its
    residuals -- they say how much the last iteration moved, not whether the
    cold aisle is cold. This turns every field write into a reading, and
    because the reading is stored, purgeWrite is free to delete the fields
    behind it.
    """

    def __init__(self, model: Model, case_dir: str | Path, every: float = 5.0):
        super().__init__(daemon=True)
        self.model = model
        self.case = Path(case_dir)
        self.every = every
        self._stop = threading.Event()

    def run(self) -> None:
        while not self._stop.wait(self.every):
            try:
                sample(self.model, self.case)
            except Exception:  # a sampler must never take the run down with it
                pass

    def stop(self) -> None:
        """Stop, after one last pass so the final write is not lost."""
        self._stop.set()
        try:
            sample(self.model, self.case)
        except Exception:
            pass


def sensor_history(model: Model, case_dir: str | Path) -> dict:
    """The recorded history, shaped for a chart: one series per place."""
    from aicfd.model import sensors

    history = sample(model, case_dir)
    groups = sensors(model)
    if not history:
        return {"iterations": [], "groups": [], "balance": []}

    series = []
    for group in groups:
        readings = [
            next((p for p in row["places"] if p["name"] == group.name), None)
            for row in history
        ]
        series.append(
            {
                "name": group.name,
                "label": group.label,
                "note": group.note,
                "points": [list(point) for point in group.points],
                "temp_c": [r["temp_c"] if r else None for r in readings],
                "spread_k": [r["spread_k"] if r else None for r in readings],
                "speed_ms": [r["speed_ms"] if r else None for r in readings],
                "pressure_pa": [
                    r.get("pressure_pa") if r else None for r in readings
                ],
            }
        )

    return {
        "iterations": [row["iteration"] for row in history],
        "groups": series,
        "balance": [
            {
                "iteration": row["iteration"],
                "closure": row["closure"],
                "return_temp_c": row["return_temp_c"],
                "recovered_kw": row["recovered_kw"],
                "backflow_kg_s": row["backflow_kg_s"],
                "peak_speed_ms": row["peak_speed_ms"],
                "fan_rise_pa": row.get("fan_rise_pa"),
            }
            for row in history
        ],
    }


def compare(first: str | Path, second: str | Path) -> dict:
    """Do two runs of the same case agree on where they ended up?

    This is what licenses seeding the initial field. A steady problem with a
    unique solution converges to the same place from anywhere, but a
    buoyancy-driven room need not have a unique solution, and an initial
    condition can in principle select between branches. Converging the same
    case from a seeded field and from a uniform one, and finding they land
    together, is the evidence that it did not (ADR-019).
    """
    ends = []
    for case in (first, second):
        history = read_history(case)
        if not history:
            return {"agree": None, "reason": f"no samples in {case}"}
        ends.append(history[-1])

    places = []
    for place in ends[0]["places"]:
        other = next(
            (p for p in ends[1]["places"] if p["name"] == place["name"]), None
        )
        if other is None:
            continue
        places.append(
            {
                "name": place["name"],
                "label": place["label"],
                "first_c": place["temp_c"],
                "second_c": other["temp_c"],
                "gap_k": round(abs(place["temp_c"] - other["temp_c"]), 3),
            }
        )
    worst = max((p["gap_k"] for p in places), default=None)
    return {
        "iterations": [ends[0]["iteration"], ends[1]["iteration"]],
        "places": places,
        "return_gap_k": round(
            abs(ends[0]["return_temp_c"] - ends[1]["return_temp_c"]), 3
        ),
        "worst_gap_k": worst,
        # Loose: two runs stopped at different iterations are being compared,
        # so this asks whether they are describing the same room, not whether
        # they agree to the last digit.
        "agree": worst is not None and worst <= 1.0,
    }


# --- report -------------------------------------------------------------------


def _resistance_line(kpis: dict, results: "PodResults") -> str:
    drop = kpis.get("rack_drop_pa")
    return "-" if drop is None else f"{drop:.1f} Pa across the row, in the field"


def _fan_rise_line(kpis: dict) -> str:
    rise = kpis.get("fan_rise_pa")
    if rise is None:
        return "-"
    if not kpis.get("fan_static_pa"):
        return f"{rise:.1f} Pa across the fan wall"
    return (
        f"{rise:.1f} Pa of the {kpis['fan_static_pa']:.0f} Pa on the "
        f"datasheet ({kpis['fan_margin'] * 100:.0f}%)"
    )


def _path_line(kpis: dict) -> str:
    path = {
        place["name"]: place["temp_c"]
        for place in kpis.get("places_now", [])
        if place["name"] in RETURN_PATH
    }
    if len(path) < len(RETURN_PATH):
        return "- (no samples yet)"
    spread = max(path.values()) - min(path.values())
    return (
        " -> ".join(f"{path[name]:.1f}" for name in RETURN_PATH)
        + f" degC, {spread:.2f} K apart"
    )


def _drift_line(kpis: dict) -> str:
    moved = kpis.get("drift_k")
    if moved is None:
        return "- (only one sample so far)"
    return f"{moved:.2f} K since the previous sample"


def report(results: PodResults) -> str:
    k = results.kpis
    lines = [
        f"POD '{results.case_name}' at iteration {results.time}",
        "",
        f"  Fan wall        {k['supply_m3h']:,.0f} m3/h "
        f"({k['supply_kg_s']:.3f} kg/s) at {k['supply_temp_c']:.1f} degC",
        f"  Return air      {k['return_temp_c']:.1f} degC "
        f"(dT {k['delta_t_k']:.1f} K)",
        f"  Heat carried    {k['recovered_kw']:.2f} kW of {k['load_kw']:.2f} kW "
        f"installed ({(k['energy_closure'] or 0) * 100:.0f}%)",
        f"  Peak air        {k['peak_air_temp_c']:.1f} degC, "
        f"{k['peak_speed_ms']:.2f} m/s",
        f"  Still moving    {_drift_line(k)}",
        f"  Return path     {_path_line(k)}",
        "",
        f"  Fan wall rise   {_fan_rise_line(k)}",
        f"  Rack row        {_resistance_line(k, results)}",
        "",
        "  Place                  Temp    dP vs intake    Speed",
    ]
    for place in k.get("places_now", []):
        pressure = place.get("pressure_pa")
        lines.append(
            f"  {place['label']:<22}{place['temp_c']:>6.1f}"
            + (f"{pressure:>16.1f}" if pressure is not None else f"{'-':>16}")
            + f"{place['speed_ms']:>9.2f}"
        )
    lines += ["", "  Rack              Load    Inlet   Outlet    Rise   ASHRAE"]
    for rack in k["racks"]:
        inside = "ok" if rack["ashrae"]["within_recommended"] else "!"
        lines.append(
            f"  {rack['id']:<16}{rack['load_kw']:>5.1f} kW"
            f"{rack['inlet_c']:>9.1f}{rack['outlet_c']:>9.1f}"
            f"{rack['rise_k']:>8.1f} K   {inside}"
        )
    lines += ["", "  Validation"]
    for check in results.checks:
        lines.append(f"    {check.status:<5} {check.name:<20} {check.detail}")
    return "\n".join(lines) + "\n"


def convergence(model: Model, case_dir: str | Path) -> list[dict]:
    """The energy balance at every written time.

    A steady run that is merely unconverged shows this climbing towards 100%.
    One that is wrong shows it settling somewhere else, and the difference is
    not visible in a residual plot.
    """
    rows = []
    for time in written_times(case_dir):
        step = Path(case_dir) / time
        recovered = recovered_load_w(step, model)
        rows.append(
            {
                "time": time,
                "return_temp_c": round(return_temperature(step) - KELVIN, 2),
                "recovered_kw": round(recovered / 1000, 3),
                "closure": round(recovered / model.total_load_w, 4)
                if model.total_load_w
                else None,
            }
        )
    return rows
