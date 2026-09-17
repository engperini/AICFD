"""Turn a solved case into engineering KPIs, a validation verdict and a web payload.

This is the layer that answers the questions a facility engineer actually asks --
"is any rack inlet out of the ASHRAE envelope?", "does the mass balance close?",
"did it really converge?" -- rather than the questions a CFD tool answers.

Outputs, written to ``results/<case>/``:

* ``viewer.json``  geometry, KPIs, checks and residual history
* ``fields.bin``   float32 T, Ux, Uy, Uz on the structured grid
* ``report.md``    the same verdict, readable without a browser
"""

from __future__ import annotations

import json
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from aicfd.foam import solverlog
from aicfd.foam.casedict import Box, CaseGeometry, read_case
from aicfd.foam.fields import cell_centres, read_field, to_grid

KELVIN = 273.15

# Air at ~20 C, 1 atm. Good to ~2% over any data hall temperature range.
RHO_AIR = 1.19  # kg/m3
CP_AIR = 1005.0  # J/(kg K)

M3H_PER_CFM = 1.69901
"""1 CFM = 1.69901 m3/h."""

# ASHRAE TC 9.9 rack *inlet* envelopes, degrees C dry bulb.
ASHRAE_RECOMMENDED = (18.0, 27.0)
ASHRAE_ALLOWABLE = {
    "A1": (15.0, 32.0),
    "A2": (10.0, 35.0),
    "A3": (5.0, 40.0),
    "A4": (5.0, 45.0),
}


@dataclass
class Check:
    """One physical-validation check, with the number that produced the verdict."""

    name: str
    passed: bool
    detail: str

    @property
    def status(self) -> str:
        return "PASS" if self.passed else "FAIL"


@dataclass
class Results:
    case_name: str
    time: str
    kpis: dict
    checks: list[Check]
    warnings: list[str]

    @property
    def valid(self) -> bool:
        return all(check.passed for check in self.checks)


def latest_time(case_dir: Path) -> str:
    """The highest-numbered time directory in a case."""
    times = [
        entry.name
        for entry in case_dir.iterdir()
        if entry.is_dir() and _is_number(entry.name) and float(entry.name) > 0
    ]
    if not times:
        raise FileNotFoundError(
            f"{case_dir}: no result time directories. Has the solver run?"
        )
    return max(times, key=float)


def analyse(case_dir: str | Path, time: str | None = None) -> tuple[
    Results, CaseGeometry, dict[str, np.ndarray]
]:
    """Load a solved case and compute KPIs plus the validation verdict."""
    case = Path(case_dir)
    geometry = read_case(case)
    time = time or latest_time(case)

    n = geometry.n_cells
    grids = {
        "T": to_grid(read_field(case / time / "T", n), geometry.divisions),
        "U": to_grid(read_field(case / time / "U", n), geometry.divisions),
    }

    log_path = _find_log(case)
    log = solverlog.parse(log_path) if log_path else solverlog.SolverLog()

    kpis, checks, warnings = _evaluate(geometry, grids, log)
    results = Results(
        case_name=case.name,
        time=time,
        kpis=kpis,
        checks=checks,
        warnings=warnings,
    )
    return results, geometry, {**grids, "log": log}  # type: ignore[dict-item]


def _evaluate(
    geometry: CaseGeometry,
    grids: dict[str, np.ndarray],
    log: solverlog.SolverLog,
) -> tuple[dict, list[Check], list[str]]:
    temperature = grids["T"]
    velocity = grids["U"]
    checks: list[Check] = []
    warnings: list[str] = []

    room_size = geometry.room.size
    inlet_area = room_size[1] * room_size[2]  # the fanwall spans the yz face
    inlet_speed = abs(geometry.inlet_velocity[0]) if geometry.inlet_velocity else 0.0
    flow_m3s = inlet_speed * inlet_area
    load_w = geometry.total_load_w

    supply_c = (
        geometry.inlet_temperature_k - KELVIN
        if geometry.inlet_temperature_k
        else float(temperature[:, :, 0].mean() - KELVIN)
    )

    # --- airflow / thermal balance -------------------------------------------
    bulk_dt = load_w / (flow_m3s * RHO_AIR * CP_AIR) if flow_m3s > 0 else float("nan")
    required_m3s_at_10k = load_w / (10.0 * RHO_AIR * CP_AIR) if load_w else float("nan")
    over_ventilation = flow_m3s / required_m3s_at_10k if required_m3s_at_10k else None

    # --- mass balance ---------------------------------------------------------
    inlet_flux = float(velocity[:, :, 0, 0].mean()) * inlet_area
    outlet_flux = float(velocity[:, :, -1, 0].mean()) * inlet_area
    balance_error = (
        abs(outlet_flux - inlet_flux) / abs(inlet_flux) if inlet_flux else float("inf")
    )
    checks.append(
        Check(
            "mass_balance",
            balance_error < 0.02,
            f"inlet {inlet_flux:.3f} m3/s vs outlet {outlet_flux:.3f} m3/s "
            f"({balance_error * 100:.2f}% difference)",
        )
    )

    # --- monotonic heating along the flow path --------------------------------
    profile = temperature.mean(axis=(0, 1)) - KELVIN
    drops = float(np.minimum(np.diff(profile), 0).sum())
    checks.append(
        Check(
            "monotonic_heating",
            drops > -0.05,
            f"air warms from {profile[0]:.2f} to {profile[-1]:.2f} degC along x "
            f"(total non-physical cooling: {abs(drops):.3f} K)",
        )
    )

    # --- convergence ----------------------------------------------------------
    final = log.final_residuals()
    worst = max(final.values()) if final else float("nan")
    checks.append(
        Check(
            "residuals",
            bool(final) and worst < 1e-3,
            f"worst final initial-residual {worst:.2e}"
            + (f" ({max(final, key=final.get)})" if final else " (no solver log found)"),
        )
    )
    if log.stopped_on_iteration_limit:
        warnings.append(
            f"The solver stopped at the iteration limit ({log.completed_iterations} "
            "iterations), not at a convergence tolerance. Residuals were still "
            f"{worst:.1e}. Set residualControl in system/fvSolution so the run "
            "stops on physics rather than on a counter."
        )

    # --- per-zone temperatures ------------------------------------------------
    x, y, z = cell_centres(geometry.room.lo, room_size, geometry.divisions)
    zones = []
    for name, box in geometry.zones.items():
        mask = _zone_mask(box, x, y, z)
        cells = int(mask.sum())
        if cells == 0:
            checks.append(
                Check(
                    f"zone_{name}_populated",
                    False,
                    f"cell zone '{name}' contains no cells -- its heat load is "
                    "not in the simulation at all",
                )
            )
            continue
        checks.append(
            Check(f"zone_{name}_populated", True, f"cell zone '{name}': {cells} cells")
        )
        zone_t = temperature[mask] - KELVIN
        inlet_t = _zone_inlet_temperature(temperature, box, x, y, z) - KELVIN
        watts = sum(s.watts for s in geometry.heat_sources if s.zone == name)
        zones.append(
            {
                "name": name,
                "load_w": watts,
                "cells": cells,
                "inlet_temp_c": round(float(inlet_t), 2),
                "mean_temp_c": round(float(zone_t.mean()), 2),
                "peak_temp_c": round(float(zone_t.max()), 2),
                "rise_k": round(float(zone_t.mean() - inlet_t), 2),
                "ashrae": _ashrae_verdict(float(inlet_t)),
            }
        )

    for zone in zones:
        # Asymmetric on purpose: air above the envelope is an equipment risk and
        # fails the run. Air below it is only wasted energy -- no rack is harmed
        # by cold supply -- so it is a warning, not a failure.
        ashrae = zone["ashrae"]
        checks.append(
            Check(
                f"ashrae_{zone['name']}",
                not ashrae["above_recommended"],
                f"{zone['name']} inlet {zone['inlet_temp_c']} degC -- "
                f"{ashrae['verdict']}",
            )
        )
        if ashrae["below_recommended"]:
            warnings.append(
                f"Rack '{zone['name']}' is fed at {zone['inlet_temp_c']} degC, below "
                f"the ASHRAE recommended minimum of {ASHRAE_RECOMMENDED[0]} degC. "
                "No equipment risk, but raising the supply setpoint would cut "
                "chiller energy at no thermal cost."
            )

    speed = np.linalg.norm(velocity, axis=-1)
    kpis = {
        "room_size_m": [round(v, 3) for v in room_size],
        "cells": geometry.n_cells,
        "total_load_w": load_w,
        "supply_temp_c": round(supply_c, 2),
        "supply_velocity_ms": round(inlet_speed, 3),
        "supply_flow_m3s": round(flow_m3s, 3),
        "supply_flow_m3h": round(flow_m3s * 3600, 1),
        "supply_flow_cfm": round(flow_m3s * 3600 / M3H_PER_CFM, 1),
        "bulk_delta_t_k": round(bulk_dt, 3),
        "required_flow_m3h_at_10k": round(required_m3s_at_10k * 3600, 1),
        "over_ventilation_factor": (
            round(over_ventilation, 1) if over_ventilation else None
        ),
        "temp_min_c": round(float(temperature.min() - KELVIN), 2),
        "temp_max_c": round(float(temperature.max() - KELVIN), 2),
        "temp_mean_c": round(float(temperature.mean() - KELVIN), 2),
        "speed_max_ms": round(float(speed.max()), 3),
        "zones": zones,
        "iterations": log.completed_iterations,
        "runtime_s": log.execution_time_s,
        "converged_at": log.converged_at,
    }

    if over_ventilation and over_ventilation > 3:
        warnings.append(
            f"Supply airflow is {over_ventilation:.0f}x what this load needs for a "
            f"10 K rise ({flow_m3s * 3600:,.0f} m3/h supplied vs "
            f"{required_m3s_at_10k * 3600:,.0f} m3/h required). The room-level "
            "temperature rise is therefore near zero and the result says little "
            "about real cooling performance."
        )

    return kpis, checks, warnings


def _zone_mask(box: Box, x, y, z) -> np.ndarray:
    mask_x = (x >= box.lo[0]) & (x <= box.hi[0])
    mask_y = (y >= box.lo[1]) & (y <= box.hi[1])
    mask_z = (z >= box.lo[2]) & (z <= box.hi[2])
    return mask_z[:, None, None] & mask_y[None, :, None] & mask_x[None, None, :]


def _zone_inlet_temperature(temperature, box: Box, x, y, z) -> float:
    """Mean temperature on the column of cells immediately upstream of a zone.

    This is the number that matters for ASHRAE compliance -- the air a rack
    actually breathes, not the room average.
    """
    upstream = np.searchsorted(x, box.lo[0]) - 1
    upstream = max(upstream, 0)
    mask_y = (y >= box.lo[1]) & (y <= box.hi[1])
    mask_z = (z >= box.lo[2]) & (z <= box.hi[2])
    face = temperature[np.ix_(np.where(mask_z)[0], np.where(mask_y)[0], [upstream])]
    return float(face.mean())


def _ashrae_verdict(inlet_c: float) -> dict:
    low, high = ASHRAE_RECOMMENDED
    below, above = inlet_c < low, inlet_c > high
    classes = [
        name
        for name, (lo, hi) in ASHRAE_ALLOWABLE.items()
        if lo <= inlet_c <= hi
    ]
    if not (below or above):
        verdict = f"within ASHRAE recommended ({low}-{high} degC)"
    elif classes:
        side = "below" if below else "above"
        verdict = (
            f"{side} recommended ({low}-{high} degC), still allowable for "
            f"class {classes[0]}"
        )
    else:
        verdict = "outside every ASHRAE allowable envelope"
    return {
        "within_recommended": not (below or above),
        "below_recommended": below,
        "above_recommended": above,
        "allowable_classes": classes,
        "verdict": verdict,
    }


def export(
    case_dir: str | Path,
    out_dir: str | Path,
    time: str | None = None,
) -> Results:
    """Analyse a case and write the viewer payload plus a human-readable report."""
    results, geometry, data = analyse(case_dir, time)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    temperature = data["T"]
    velocity = data["U"]
    log: solverlog.SolverLog = data["log"]  # type: ignore[assignment]

    # Pack fields back-to-back as float32, C-order, indexed [k, j, i].
    layers = {
        "T": temperature - KELVIN,
        "Ux": velocity[..., 0],
        "Uy": velocity[..., 1],
        "Uz": velocity[..., 2],
    }
    descriptors = []
    offset = 0
    with (out / "fields.bin").open("wb") as handle:
        for name, array in layers.items():
            flat = np.ascontiguousarray(array, dtype=np.float32).ravel()
            handle.write(flat.tobytes())
            descriptors.append(
                {
                    "name": name,
                    "offset": offset,
                    "count": int(flat.size),
                    "min": round(float(flat.min()), 4),
                    "max": round(float(flat.max()), 4),
                    "units": "degC" if name == "T" else "m/s",
                }
            )
            offset += flat.size * 4

    nx, ny, nz = geometry.divisions
    payload = {
        "case": results.case_name,
        "time": results.time,
        "valid": results.valid,
        "grid": {
            "divisions": [nx, ny, nz],
            "origin": list(geometry.room.lo),
            "size": list(geometry.room.size),
            "order": "kji",
        },
        "geometry": {
            "room": {"lo": list(geometry.room.lo), "hi": list(geometry.room.hi)},
            "zones": [
                {
                    "name": name,
                    "lo": list(box.lo),
                    "hi": list(box.hi),
                    "load_w": sum(
                        s.watts for s in geometry.heat_sources if s.zone == name
                    ),
                }
                for name, box in geometry.zones.items()
            ],
            "patches": geometry.patches,
            "inlet_patch": geometry.inlet_patch,
        },
        "fields": descriptors,
        "kpis": results.kpis,
        "checks": [asdict(c) | {"status": c.status} for c in results.checks],
        "warnings": results.warnings,
        "residuals": {
            "iterations": log.iterations,
            "series": {
                name: [None if np.isnan(v) else v for v in values]
                for name, values in log.residuals.items()
            },
            "continuity": log.continuity,
        },
    }
    (out / "viewer.json").write_text(json.dumps(payload, indent=1))
    (out / "report.md").write_text(_report(results))
    return results


def _report(results: Results) -> str:
    kpis = results.kpis
    lines = [
        f"# {results.case_name} -- results at t={results.time}",
        "",
        f"**Verdict: {'PASS' if results.valid else 'FAIL'}** "
        f"({sum(c.passed for c in results.checks)}/{len(results.checks)} checks passed)",
        "",
        "## Operating point",
        "",
        "| Quantity | Value |",
        "|---|---|",
        f"| IT load | {kpis['total_load_w'] / 1000:.1f} kW |",
        f"| Supply air | {kpis['supply_flow_m3h']:,.0f} m3/h "
        f"({kpis['supply_flow_cfm']:,.0f} CFM) at {kpis['supply_temp_c']} degC |",
        f"| Bulk temperature rise | {kpis['bulk_delta_t_k']:.2f} K |",
        f"| Air temperature range | {kpis['temp_min_c']} to {kpis['temp_max_c']} degC |",
        f"| Peak air speed | {kpis['speed_max_ms']} m/s |",
        f"| Mesh | {kpis['cells']:,} cells |",
        f"| Solver | {kpis['iterations']} iterations in {kpis['runtime_s']} s |",
        "",
    ]

    if kpis["zones"]:
        lines += [
            "## Racks",
            "",
            "| Rack | Load | Inlet | Mean | Peak | Rise | ASHRAE |",
            "|---|---|---|---|---|---|---|",
        ]
        for zone in kpis["zones"]:
            lines.append(
                f"| {zone['name']} | {zone['load_w'] / 1000:.1f} kW | "
                f"{zone['inlet_temp_c']} degC | {zone['mean_temp_c']} degC | "
                f"{zone['peak_temp_c']} degC | {zone['rise_k']} K | "
                f"{zone['ashrae']['verdict']} |"
            )
        lines.append("")

    lines += ["## Validation checks", ""]
    for check in results.checks:
        lines.append(f"- **{check.status}** `{check.name}` -- {check.detail}")

    if results.warnings:
        lines += ["", "## Warnings", ""]
        lines += [f"- {w}" for w in results.warnings]

    return "\n".join(lines) + "\n"


#: Solvers whose logs carry a residual history. blockMesh, topoSet and checkMesh
#: also write `log.*` files, and picking one of those by accident silently
#: reports "no convergence data" for a run that converged fine.
SOLVERS = ("buoyantSimpleFoam", "buoyantPimpleFoam", "simpleFoam", "pimpleFoam")


def _find_log(case: Path) -> Path | None:
    for solver in SOLVERS:
        candidate = case / f"log.{solver}"
        if candidate.exists():
            return candidate
    # Unknown solver: fall back to whichever log actually has iterations in it.
    logs = [
        (path, path.read_text(errors="replace").count("\nTime = "))
        for path in sorted(case.glob("log.*"))
    ]
    best = max(logs, key=lambda entry: entry[1], default=(None, 0))
    return best[0] if best[1] > 0 else None


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True
