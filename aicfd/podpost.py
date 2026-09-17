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

    return PodResults(
        case_name=model.name, time=time, kpis=kpis, checks=_checks(model, step, kpis, grid)
    )


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


# --- report -------------------------------------------------------------------


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
        "",
        "  Rack              Load    Inlet   Outlet    Rise   ASHRAE",
    ]
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
