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
import re
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aicfd.case import FAN_INTAKE, FAN_SUPPLY, KELVIN
from aicfd.foam.fields import patch_names, read_field, read_patch_field, to_grid
from aicfd.model import CP_AIR, Model

# ASHRAE TC 9.9 rack *inlet* envelopes, degrees C dry bulb. The recommended
# band is what a design is held to; the allowable classes say how far outside
# it equipment is still rated to run, which is what a verdict quotes when a
# rack sits above the recommendation.
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


def _ashrae_verdict(inlet_c: float) -> dict:
    low, high = ASHRAE_RECOMMENDED
    below, above = inlet_c < low, inlet_c > high
    classes = [name for name, (lo, hi) in ASHRAE_ALLOWABLE.items() if lo <= inlet_c <= hi]
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

#: The patches a fan wall pair is made of: ``fanIntake``/``fanSupply`` for a
#: POD's one unit, ``fan3Intake``/``fan3Supply`` for a hall's third.
FAN_PATCH = re.compile(r"^(fan\d*)(Intake|Supply)$")

#: How many racks the text report lists in full before it lists the warmest
#: few and sums up the rest.
REPORT_RACKS = 24

#: How far the energy balance may miss before the run is not to be believed.
#: Loose, because it is catching "the field never filled", not modelling error.
ENERGY_TOLERANCE = 0.05

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


def fan_pairs(step: str | Path) -> list[tuple[str, str]]:
    """(intake, supply) for every fan wall the solved step has, in unit order.

    Read off the field files rather than the model so that a result can be
    analysed with nothing but the run directory.
    """
    names = patch_names(Path(step) / "phi")
    intakes = [n for n in names if FAN_PATCH.match(n) and n.endswith("Intake")]
    intakes.sort(key=_fan_order)
    return [(n, n[: -len("Intake")] + "Supply") for n in intakes]


def _fan_order(name: str) -> int:
    digits = FAN_PATCH.match(name).group(1)[len("fan"):]
    return int(digits) if digits else 0


def _is_fan_patch(name: str) -> bool:
    return FAN_PATCH.match(name) is not None


def _intakes(step: str | Path, field: str) -> np.ndarray:
    """One array of the patch values across every fan intake."""
    step = Path(step)
    parts = [read_patch_field(step / field, intake) for intake, _ in fan_pairs(step)]
    return np.concatenate(parts) if parts else np.zeros(0)


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
    available = written_times(case)
    if time is None:
        if not available:
            # `0/` holds the initial conditions, not a solution: it has no phi,
            # so every flux, balance and check would fail on a missing file
            # rather than on the real problem. Reachable whenever the run ends
            # before its first write -- an iteration cap below
            # `solver.sensor_interval`, or residualControl converging first.
            raise ValueError(
                f"{case} has no solved time directory: the run ended before it "
                f"wrote a field. The solver writes every "
                f"solver.sensor_interval iterations, so either raise "
                f"solver.max_iterations above it or lower the interval, and "
                f"run again."
            )
        time = available[-1]
    step = case / time

    flows = patch_flows(step)
    fans = fan_flows(step, flows, model.supply_temp_c)
    supply = sum(f["supply_kg_s"] for f in fans)  # stated positive, into the hall
    intake = sum(f["intake_kg_s"] for f in fans)

    kpis = {
        "supply_kg_s": round(supply, 5),
        "intake_kg_s": round(intake, 5),
        "supply_m3h": round(supply / _density(model) * 3600, 0),
        "backflow_kg_s": round(sum(f["backflow_kg_s"] for f in fans), 5),
        # What the units delivered, not what they were told to deliver.
        "supply_temp_c": supply_temperature(step, model.supply_temp_c),
        "return_temp_c": round(return_temperature(step) - KELVIN, 2),
        "load_kw": round(model.total_load_w / 1000, 2),
        "fans": fans,
    }
    kpis["delta_t_k"] = round(kpis["return_temp_c"] - kpis["supply_temp_c"], 2)
    recovered = recovered_load_w(step, model)
    kpis["recovered_kw"] = round(recovered / 1000, 2)
    kpis["energy_closure"] = round(recovered / model.total_load_w, 4) if model.total_load_w else None

    grid = read_grid(model, step)
    kpis["peak_speed_ms"] = round(float(np.linalg.norm(grid["U"], axis=0).max()), 3)
    kpis["peak_air_temp_c"] = round(float(grid["T"].max()), 2)
    kpis["racks"] = rack_temperatures(model, grid)

    kpis["rack_drop_pa"], kpis["rows"] = rack_pressure_drop(model, grid)
    kpis["grille_drop_pa"] = grille_pressure_drop(step)
    kpis["grille_drop_asked_pa"] = round(model.grille_pressure_drop_pa, 3)
    # The rise that sizes the machine is the one the most loaded unit has to
    # produce; a hall's units do not all see the same resistance.
    rises = [f["rise_pa"] for f in fans]
    kpis["fan_rise_pa"] = round(max(rises), 3) if rises else None
    kpis["fan_rise_min_pa"] = round(min(rises), 3) if rises else None
    kpis["fan_rise_mean_pa"] = round(float(np.mean(rises)), 3) if rises else None
    operating = model.fan_operating_point(kpis["fan_rise_pa"] or 0.0)
    if operating:
        kpis["fan_operating_m3h"], kpis["fan_operating_pa"] = operating
    available = model.fan_available_pa()
    if available:
        kpis["fan_static_pa"] = available
        kpis["fan_margin"] = round(kpis["fan_rise_pa"] / available, 4)
    kpis.update(coil_capacity(model, fans, kpis))
    kpis["drift_k"] = drift(case)
    kpis["hvac"] = model.hvac()
    kpis["hvac_lines"] = [line.strip() for line in _hvac_summary(model)]
    kpis["alerts"] = list(model.alerts) + _coil_alerts(kpis)
    history = read_history(case)
    kpis["places_now"] = history[-1]["places"] if history else []
    return PodResults(
        case_name=model.name, time=time, kpis=kpis, checks=_checks(model, step, kpis, grid)
    )


def coil_capacity(model: Model, fans: list[dict], kpis: dict) -> dict:
    """What the coils really do at the air they are actually receiving.

    A datasheet's capacity is true at one return air temperature -- the one
    the unit was selected for -- and a real room almost never returns air at
    it. Holding that capacity fixed while the return moves asserts that a heat
    exchanger transfers the same heat across a larger temperature difference,
    which none does: capacity is `epsilon * C_air * (T_return - T_water)`, and
    only `epsilon` belongs to the machine (ADR-039).

    So the unit is modelled rather than looked up. `aicfd.coil` fits the
    manufacturer's own selections to a counterflow coil and can then answer at
    any condition -- above the selections, below them, and at an air flow none
    of them used, which is where every real hall sits. Where the fit is not
    possible the table is read as before, and where the table does not reach,
    the number is refused rather than extrapolated on a straight line.

    Adds to each unit: the ceiling its coil has at its own return (`available_kw`),
    what fraction of it the unit is using, the supply air temperature the coil
    would really deliver, and how far open its water valve has to be.
    """
    unit = getattr(model, "equipment", None)
    if unit is None:
        return {}
    coil = unit.coil
    if coil is None:
        return _coil_from_table(model, unit, fans)

    from aicfd.coil import air_capacity_rate

    # The model's own per-unit airflow, not the total divided by however many
    # fans happened to be measured: those are the same number in a solved run
    # and very different ones anywhere else.
    per_unit = model.unit_airflow_m3h
    setpoint = model.supply_temp_c
    available = removed = 0.0
    saturated, warmest_supply, notes, seen, air_seen = 0, setpoint, set(), [], 0.0
    for fan in fans:
        temperature = fan.get("return_temp_c")
        if temperature is None:
            continue
        seen.append(temperature)
        # The mass the solve actually moved through this unit, where it is
        # known, rather than the nominal volume flow converted at some
        # reference density. It matters because `available_kw` is compared
        # against `heat_kw`, and `heat_kw` was measured on that same mass:
        # judging one against a capacity rate derived from the other would
        # put a few per cent of nothing into the margin.
        measured = fan.get("intake_kg_s")
        air = (measured * CP_AIR / 1000 if measured
               else air_capacity_rate(per_unit, temperature, model.altitude_m))
        air_seen += air
        point = coil.operate(temperature, air, setpoint)
        fan["available_kw"] = round(point.ceiling_kw, 1)
        fan["coil_supply_c"] = round(point.supply_c, 2)
        fan["coil_valve_pct"] = round(point.valve * 100, 0)
        # The water this unit is drawing, so the hydraulic side stays
        # checkable: the coil's ceiling is real for one unit, but every unit
        # taking its ceiling at once is a chilled water plant nobody sized.
        fan["coil_water_m3h"] = round(point.water_m3h, 1)
        available += point.ceiling_kw
        if fan.get("heat_kw") is not None:
            removed += fan["heat_kw"]
            fan["of_available_pct"] = round(fan["heat_kw"] / point.ceiling_kw * 100, 1)
        if point.saturated:
            saturated += 1
            warmest_supply = max(warmest_supply, point.supply_c)
        if point.extrapolated:
            notes.add(point.extrapolated)
    if not available:
        return {}
    return {
        "unit_model": unit.model,
        "available_kw": round(available, 1),
        "utilisation_pct": round(removed / available * 100, 1),
        "units_over_capacity": sum(
            1 for f in fans if (f.get("of_available_pct") or 0) > 100
        ),
        # Named so nobody has to guess whether a number is the catalogue's or
        # the room's: the catalogue figure is the same machine at its selection
        # point, and quoting one for the other is the mistake this exists to
        # stop.
        "catalogue_kw": round((model.unit_capacity_kw or 0) * len(fans), 1) or None,
        "coil_table_span_c": [coil.returns_fitted[0], coil.returns_fitted[-1]],
        "coil_model": {
            "water_c": coil.water_c,
            "air_split_pct": round(coil.air_split * 100),
            "residual_k": round(coil.residual_k, 3),
            "water_max_m3h": round(coil.water_max / 4.18 * 3.6, 1),
            "fitted_returns_c": list(coil.returns_fitted),
        },
        # The whole point of modelling the machine: the supply temperature is
        # an output, and where the valve runs out it stops matching the one
        # the run imposed. Then every temperature in the result is optimistic.
        "coil_saturated_units": saturated,
        "coil_water_m3h": round(sum(f.get("coil_water_m3h") or 0 for f in fans), 1),
        "coil_supply_setpoint_c": round(setpoint, 2),
        "coil_supply_needed_c": round(warmest_supply, 2),
        "coil_extrapolated": sorted(notes) or None,
        "coil_return_span_c": [round(min(seen), 2), round(max(seen), 2)] if seen else None,
        # On the mass the solve actually moved, the same basis every other
        # number here uses. Deriving it from the nominal volume flow instead
        # would put a few per cent of density between two figures a reader
        # would reasonably expect to agree.
        "coil_air_share_pct": round(air_seen / len(seen) / coil.air_fitted * 100),
    }


def _coil_alerts(kpis: dict) -> list[str]:
    """What the coil model found, said where a reader will see it.

    An alert, not a failed check, because none of this makes the field wrong
    on its own terms -- the solve did what it was told. What it says is that
    what it was told may not be what the machine would do, and that is a
    judgement for the engineer reading it (ADR-023, ADR-039).
    """
    out = []
    saturated = kpis.get("coil_saturated_units") or 0
    if saturated:
        out.append(
            f"{saturated} unit(s) cannot hold the "
            f"{kpis['coil_supply_setpoint_c']:.1f} degC supply air this run "
            f"imposed: with the water valve wide open their coil delivers "
            f"{kpis['coil_supply_needed_c']:.1f} degC at the return they "
            f"receive. Every temperature in this result is that much "
            f"optimistic -- re-run at the higher supply temperature."
        )
    notes = kpis.get("coil_extrapolated") or []
    if notes:
        span = kpis.get("coil_return_span_c") or []
        where = (f" The units returned {span[0]:.1f} to {span[1]:.1f} degC at "
                 f"{kpis['coil_air_share_pct']:.0f}% of the selections' air flow."
                 if span else "")
        out.append(
            f"The coil model was used beyond the manufacturer's selections: "
            f"{'; '.join(notes)}.{where} It extrapolates on the heat "
            f"exchanger's physics rather than on a straight line, and it "
            f"reproduces every selection to {kpis['coil_model']['residual_k']:.02f} K "
            f"-- but nothing measured backs it there."
        )
    return out


def _coil_from_table(model: Model, unit, fans: list[dict]) -> dict:
    """The old reading, for a unit whose selections do not support a fit.

    Interpolated between the rows and refused outside them. Kept because a
    unit described by a capacity table alone is still worth judging against
    that table; it just cannot be asked what it does off it.
    """
    available, removed, outside = 0.0, 0.0, []
    for fan in fans:
        temperature = fan.get("return_temp_c")
        if temperature is None:
            continue
        if not unit.covers(temperature):
            outside.append(round(temperature, 2))
            continue
        capacity = unit.available_kw(temperature)
        fan["available_kw"] = round(capacity, 1)
        available += capacity
        if fan.get("heat_kw") is not None:
            removed += fan["heat_kw"]
            fan["of_available_pct"] = round(fan["heat_kw"] / capacity * 100, 1)
    low, high = unit.span
    catalogue = round((model.unit_capacity_kw or 0) * len(fans), 1) or None
    if not available:
        return (
            {
                "unit_model": unit.model,
                "coil_table_span_c": [low, high],
                "catalogue_kw": catalogue,
                "coil_outside_table_c": outside,
            }
            if outside
            else {}
        )
    return {
        "unit_model": unit.model,
        "available_kw": round(available, 1),
        "utilisation_pct": round(removed / available * 100, 1),
        "units_over_capacity": sum(
            1 for f in fans if (f.get("of_available_pct") or 0) > 100
        ),
        "coil_table_span_c": [low, high],
        "catalogue_kw": catalogue,
        "coil_outside_table_c": outside,
    }


def grille_pressure_drop(step: str | Path) -> float | None:
    """The jump the field shows across the return grilles, in Pa.

    Each grille is a cyclic pair; the drop is the mean p_rgh on its lower face
    minus the mean on its upper face, flow-weighted across grilles.
    """
    phi_path = Path(step) / "phi"
    names = [n for n in patch_names(phi_path) if n.endswith("_below")]
    if not names:
        return None
    total_flow, weighted = 0.0, 0.0
    for below in names:
        above = below[: -len("_below")] + "_above"
        flow = abs(float(np.sum(read_patch_field(phi_path, below))))
        drop = float(
            np.mean(read_patch_field(Path(step) / "p_rgh", below))
            - np.mean(read_patch_field(Path(step) / "p_rgh", above))
        )
        total_flow += flow
        weighted += flow * drop
    return round(weighted / total_flow, 3) if total_flow else None


def fan_flows(step: str | Path, flows: dict[str, float] | None = None,
              supply_temp_c: float | None = None) -> list[dict]:
    """What each fan wall moves, returns and has to push against.

    Per unit, because that is the level a plant is judged at: a hall whose
    units look uniform in the mean can still have one at the end of a row
    taking half again the flow of its neighbours, and only the per-unit table
    shows it. With ``supply_temp_c`` each unit also reports the mixed-mean
    temperature of the air it draws and the heat that air carries -- the same
    enthalpy balance the hall-wide `energy_closure` check uses, one unit at a
    time.
    """
    step = Path(step)
    flows = flows or patch_flows(step)
    units = []
    for intake, supply in fan_pairs(step):
        rise = float(
            np.mean(read_patch_field(step / "p_rgh", supply))
            - np.mean(read_patch_field(step / "p_rgh", intake))
        )
        units.append(
            {
                "name": intake[: -len("Intake")],
                "supply_kg_s": round(-flows.get(supply, 0.0), 5),
                "intake_kg_s": round(flows.get(intake, 0.0), 5),
                "backflow_kg_s": round(backflow(step, intake), 5),
                "rise_pa": round(rise, 3),
                **_unit_return(step, intake, _supply_of(step, supply, supply_temp_c)),
                "supply_temp_c": (lambda v: round(v, 2) if v is not None else None)(
                    _supply_of(step, supply, supply_temp_c)
                ),
            }
        )
    return units


def supply_temperature(step: str | Path, fallback: float | None = None) -> float | None:
    """The air the units are really delivering, read off their supply patches.

    Not the setpoint the spec asked for. Where the coil is solved with the room
    the two part company exactly when it matters -- a unit whose water valve
    has run out delivers warmer air than it was told to, and a KPI that kept
    reporting the setpoint would hide the finding under the number that names
    it (ADR-040).

    Flow-weighted across the units, so a plant whose units deliver different
    temperatures reports the air the hall actually receives.
    """
    step = Path(step)
    if not (step / "phi").is_file() or not (step / "T").is_file():
        return fallback  # nothing written yet, or not a solved step at all
    total = weighted = 0.0
    for _, supply in fan_pairs(step):
        temperature = read_patch_field(step / "T", supply)
        phi = read_patch_field(step / "phi", supply)
        if temperature.size == 0:
            continue
        mass = float(abs(phi).sum()) or 1.0
        weighted += float(np.mean(temperature)) * mass
        total += mass
    return round(weighted / total - KELVIN, 2) if total else fallback


def _supply_of(step: Path, supply: str, fallback: float | None) -> float | None:
    """One unit's own supply air temperature."""
    values = read_patch_field(step / "T", supply)
    return float(np.mean(values)) - KELVIN if values.size else fallback


def _unit_return(step: Path, intake: str, supply_temp_c: float | None) -> dict:
    """One unit's mixed-mean return temperature and the heat it removes.

    Flow-weighted over the intake's own faces, exactly as the hall-wide
    `return_temperature` is: a patch where half the area carries most of the
    flow has an area average that describes no air that ever existed.
    """
    if supply_temp_c is None:
        return {}
    phi = read_patch_field(step / "phi", intake)
    temperature = read_patch_field(step / "T", intake)
    if phi.size == 0 or temperature.size <= 1:
        return {}
    leaving = np.clip(phi, 0, None)
    if leaving.sum() <= 0:
        return {}
    mixed_k = float((leaving * temperature).sum() / leaving.sum())
    heat_w = float(CP_AIR * (phi * (temperature - (supply_temp_c + KELVIN))).sum())
    return {
        "return_temp_c": round(mixed_k - KELVIN, 2),
        "heat_kw": round(heat_w / 1000, 1),
    }


def rack_pressure_drop(model: Model, grid: dict) -> tuple[float | None, list[dict]]:
    """The drop the solved field shows across each rack row, in Pa.

    Sampled one cell either side of the row, outside the porous cells: the
    reconstructed velocity inside a porous zone is not trustworthy, but the
    pressure in free cells on either side of it is. Returns the mean over the
    rows and the rows themselves.
    """
    if not model.rows:
        return None, []
    coords = (grid["x"], grid["y"], grid["z"])
    rack_top = model.racks[0].box.hi[2]

    def plane(position: float, span: tuple[float, float]) -> float:
        picks = [
            np.where((coords[0] >= span[0]) & (coords[0] <= span[1]))[0],
            [int(np.argmin(abs(coords[1] - position)))],
            np.where(coords[2] <= rack_top)[0],
        ]
        return float(grid["p_rgh"][np.ix_(picks[2], picks[1], picks[0])].mean())

    half = model.cell(1) / 2
    rows = []
    for row in model.rows:
        front = plane(row.front_y - row.front_sign * half, row.span)
        back = plane(row.back_y + row.front_sign * half, row.span)
        rows.append({"id": row.id, "drop_pa": round(front - back, 3)})
    mean = float(np.mean([r["drop_pa"] for r in rows]))
    return round(mean, 3), rows


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
    """Mixed-mean temperature of the air leaving through the fan intakes, in K.

    Flow-weighted, not area-averaged: a patch where half the area carries most
    of the flow has an area average that describes no air that ever existed.
    """
    step = Path(step)
    phi = _intakes(step, "phi")
    temperature = _intakes(step, "T")
    if temperature.size == 0:
        raise ValueError(
            f"{step}/T carries no value on the fan intake. A zeroGradient "
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
    phi = _intakes(step, "phi")
    temperature = _intakes(step, "T")
    if phi.size == 0 or temperature.size <= 1:
        return 0.0  # a uniform patch value cannot resolve a mixed-mean rise
    supply_k = (supply_temperature(step, model.supply_temp_c) or
                model.supply_temp_c) + KELVIN
    return float(CP_AIR * (phi * (temperature - supply_k)).sum())


def read_grid(model: Model, step: str | Path) -> dict:
    """The cell fields as ``[k, j, i]`` grids, with the axis coordinates."""
    step = Path(step)
    n, divisions = model.n_cells, model.divisions
    cx, cy, cz = model.cell_size
    return {
        "T": to_grid(read_field(step / "T", n), divisions) - KELVIN,
        "p_rgh": to_grid(read_field(step / "p_rgh", n), divisions),
        "U": np.stack(
            [to_grid(read_field(step / "U", n)[:, axis], divisions) for axis in range(3)]
        ),
        "x": (np.arange(divisions[0]) + 0.5) * cx,
        "y": (np.arange(divisions[1]) + 0.5) * cy,
        "z": (np.arange(divisions[2]) + 0.5) * cz,
    }


def rack_temperatures(model: Model, grid: dict) -> list[dict]:
    """Inlet and outlet temperature for each rack, at the faces it breathes through.

    The inlet face is the row of cells just upstream of the rack, not the first
    row inside it: inside the porous zone the air has already started heating.
    """
    x, y, z = grid["x"], grid["y"], grid["z"]
    where = {
        rack.id: (row.id, position + 1)
        for row in model.rows
        for position, rack in enumerate(row.racks)
    }
    rows = []
    for rack in model.racks:
        axis = rack.airflow_axis
        half = model.cell(axis) / 2
        # Air enters at the low face when it travels +, at the high face when -.
        entry = rack.box.lo[axis] - half if rack.airflow_sign > 0 else rack.box.hi[axis] + half
        exit_ = rack.box.hi[axis] + half if rack.airflow_sign > 0 else rack.box.lo[axis] - half
        span = {
            other: (rack.box.lo[other], rack.box.hi[other])
            for other in range(3)
            if other != axis
        }

        def face(position: float) -> np.ndarray:
            """The face's cells as [k, j, i] -- z kept separate so the top
            of the rack can be read on its own."""
            picks = []
            for a, coords in ((0, x), (1, y), (2, z)):
                if a == axis:
                    picks.append([int(np.argmin(abs(coords - position)))])
                else:
                    lo, hi = span[a]
                    picks.append(np.where((coords >= lo) & (coords <= hi))[0])
            return grid["T"][np.ix_(picks[2], picks[1], picks[0])]

        inlet_face, outlet_face = face(entry), face(exit_)
        inlet = float(inlet_face.mean())
        # The top of the rack is where recirculating or leaking hot air
        # arrives first, so it is the worst point and the one to judge by.
        top = float(inlet_face[-1].mean())
        outlet = float(outlet_face.mean())
        row_id, position = where.get(rack.id, ("", 0))
        rows.append(
            {
                "id": rack.id,
                "row": row_id,
                "position": position,
                "load_kw": rack.load_kw,
                "inlet_c": round(inlet, 2),
                "inlet_top_c": round(top, 2),
                "outlet_c": round(outlet, 2),
                "rise_k": round(outlet - inlet, 2),
                "ashrae": _ashrae_verdict(top),
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
        if not _is_fan_patch(name)
        and not name.startswith("grille")
        and abs(flow) > MASS_TOLERANCE * supply
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
    units = len(kpis.get("fans", [])) or 1
    checks.append(
        Check(
            "no_backflow",
            reverse <= BACKFLOW_TOLERANCE * intake,
            f"{reverse:.5f} kg/s reverses through the fan intake"
            + (f"s ({units} units)" if units > 1 else "")
            + f", {reverse / intake * 100:.1f}% of the {intake:.3f} kg/s they move"
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
        rows = kpis.get("rows") or []
        spread = (
            f" (rows {min(r['drop_pa'] for r in rows):.1f} to "
            f"{max(r['drop_pa'] for r in rows):.1f} Pa)"
            if len(rows) > 1
            else ""
        )
        checks.append(
            Check(
                "rack_resistance",
                abs(ratio - 1.0) <= RESISTANCE_TOLERANCE,
                f"the field drops {delivered:.1f} Pa across the row{'s' if len(rows) > 1 else ''} "
                f"where the rack curve at {model.airflow_m3h:,.0f} m3/h asks for "
                f"{asked:.1f} Pa ({ratio * 100:.0f}%){spread}"
                + (
                    ""
                    if abs(ratio - 1.0) <= RESISTANCE_TOLERANCE
                    else " -- any fan pressure taken from this field is wrong"
                ),
            )
        )

    grille_drop, grille_asked = kpis.get("grille_drop_pa"), kpis.get("grille_drop_asked_pa")
    if grille_drop is not None and grille_asked:
        ratio = grille_drop / grille_asked
        checks.append(
            Check(
                "grille_resistance",
                abs(ratio - 1.0) <= RESISTANCE_TOLERANCE,
                f"the field drops {grille_drop:.2f} Pa across the return grilles "
                f"where their K at {model.airflow_m3h:,.0f} m3/h asks for "
                f"{grille_asked:.2f} Pa ({ratio * 100:.0f}%)",
            )
        )

    rise = kpis.get("fan_rise_pa")
    available = model.fan_available_pa()
    if rise is not None and available:
        fans = kpis.get("fans") or []
        loaded = max(fans, key=lambda f: f["rise_pa"])["name"] if len(fans) > 1 else None
        checks.append(
            Check(
                "fan_capacity",
                rise <= available,
                (
                    f"the most loaded unit ({loaded}) costs {rise:.1f} Pa, the least "
                    f"{kpis['fan_rise_min_pa']:.1f} Pa, "
                    if loaded
                    else f"the POD costs {rise:.1f} Pa "
                )
                + (
                    f"and the unit's P-Q curve offers {available:.0f} Pa at "
                    if model.fan_curve
                    else f"and the unit's datasheet offers {available:.0f} Pa at "
                )
                + f"{model.unit_airflow_m3h:,.0f} m3/h per unit ({rise / available * 100:.0f}%)"
                + (
                    f"; uncontrolled at full speed it would run at "
                    f"{kpis['fan_operating_m3h']:,.0f} m3/h and {kpis['fan_operating_pa']:.1f} Pa"
                    if kpis.get("fan_operating_m3h")
                    else ""
                )
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
    racks = kpis["racks"]
    worst_rack = max(racks, key=lambda r: r["inlet_top_c"]) if racks else None
    worst = worst_rack["inlet_top_c"] if worst_rack else model.supply_temp_c
    verdict = _ashrae_verdict(worst)
    over = sum(1 for r in racks if r["ashrae"]["above_recommended"])
    checks.append(
        Check(
            "ashrae_inlet",
            not verdict["above_recommended"],
            f"warmest rack inlet {worst:.1f} degC at the top of "
            f"{worst_rack['id'] if worst_rack else 'the racks'} -- {verdict['verdict']}"
            + (f"; {over} of {len(racks)} racks above recommended" if over else ""),
        )
    )

    limit = PLAUSIBLE_SPEED_MARGIN * max(
        model.fan_face_velocity_ms, _buoyant_velocity(model)
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


def _hvac_summary(model: Model) -> list[str]:
    from aicfd.case import _hvac_lines

    return [line.replace("HVAC capacity", "capacity").replace("HVAC airflow", "airflow")
            for line in _hvac_lines(model)]


def _buoyant_velocity(model: Model) -> float:
    rise = max(min(model.design_delta_t_k, 30.0), 1.0)
    return float((2 * 9.81 * rise / 293.0 * model.domain.size[2]) ** 0.5)


def _density(model: Model) -> float:
    from aicfd.case import supply_density

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
    reconstruct_new_times(case)
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


def reconstruct_new_times(case: Path, keep: int = 3) -> list[str]:
    """Stitch together any time a parallel run has finished writing.

    The processors write their own pieces under ``processor*/``; the sampler
    needs the whole field, so each complete write is run through
    reconstructPar as it lands. Older reconstructed times are dropped the way
    purgeWrite drops the pieces, keeping the last ``keep``.
    """
    processors = sorted(case.glob("processor[0-9]*"))
    if not processors:
        return []
    from aicfd.run import FoamCommandFailed, run_command

    done = []
    for entry in sorted(processors[0].iterdir(), key=lambda e: _sort_key(e.name)):
        if not entry.is_dir() or not _is_number(entry.name) or float(entry.name) == 0:
            continue
        if (case / entry.name / "phi").exists() and _complete(case / entry.name):
            continue
        if not all(_complete(p / entry.name) for p in processors):
            continue  # still being written
        try:
            run_command(case, "reconstructPar", args=["-time", entry.name])
        except FoamCommandFailed:
            continue
        done.append(entry.name)

    stitched = [t for t in written_times(case) if float(t) > 0]
    for old in stitched[:-keep]:
        shutil.rmtree(case / old, ignore_errors=True)
    return done


def _sort_key(name: str) -> float:
    try:
        return float(name)
    except ValueError:
        return float("inf")


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
    fans = fan_flows(step, flows, model.supply_temp_c)
    supply = sum(f["supply_kg_s"] for f in fans)
    intake = sum(f["intake_kg_s"] for f in fans)
    recovered = recovered_load_w(step, model)

    # Pressure is reported against the fan intake, so it reads as what a
    # manometer in the room would show: how many pascals above the machine's
    # suction each place sits. The absolute value is 101 325 Pa everywhere and
    # says nothing. p_rgh rather than p because it has the hydrostatic column
    # removed -- comparing a point at 1 m with one at 7 m on static pressure
    # alone would just measure the height difference.
    reference = float(np.mean(_intakes(step, "p_rgh")))

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
        "backflow_kg_s": round(sum(f["backflow_kg_s"] for f in fans), 5),
        "return_temp_c": round(return_temperature(step) - KELVIN, 2),
        "recovered_kw": round(recovered / 1000, 3),
        "closure": round(recovered / model.total_load_w, 4)
        if model.total_load_w
        else None,
        "peak_speed_ms": round(float(np.linalg.norm(grid["U"], axis=0).max()), 3),
        "peak_temp_c": round(float(grid["T"].max()), 2),
        # What the fan wall has to produce: the static rise across it. This is
        # the number that sizes the machine, and it is not something the user
        # gave -- it is what the POD's own resistance turned out to be. With
        # several units it is the most loaded one.
        "fan_rise_pa": round(max(f["rise_pa"] for f in fans), 3) if fans else None,
        "fans": [{"name": f["name"], "rise_pa": f["rise_pa"]} for f in fans],
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
    units = len(kpis.get("fans", []))
    span = (
        f" on the most loaded of {units} units ({kpis['fan_rise_min_pa']:.1f} on the least)"
        if units > 1
        else ""
    )
    if not kpis.get("fan_static_pa"):
        return f"{rise:.1f} Pa across the fan wall{span}"
    return (
        f"{rise:.1f} Pa of the {kpis['fan_static_pa']:.0f} Pa on the "
        f"datasheet ({kpis['fan_margin'] * 100:.0f}%){span}"
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


def _coil_line(kpis: dict) -> str:
    """The plant against what its coils can actually transfer.

    Separate from the catalogue comparison, and said in the same breath,
    because a reader who has only ever seen the catalogue figure will read
    this one as it and conclude the opposite of what it says.
    """
    used = kpis["utilisation_pct"]
    over = kpis.get("units_over_capacity") or 0
    catalogue = kpis.get("catalogue_kw")
    line = (
        f"Coils ({kpis['unit_model']}): the plant removes "
        f"{kpis['recovered_kw']:,.0f} kW of the {kpis['available_kw']:,.0f} kW "
        f"its coils can transfer at the air they are receiving ({used:.1f}%)"
    )
    if catalogue:
        line += f"; the catalogue figure at the selection point is {catalogue:,.0f} kW"
    if over:
        line += f". {over} unit(s) are above their own coil's capacity"
    outside = kpis.get("coil_outside_table_c") or []
    if outside:
        low, high = kpis["coil_table_span_c"]
        line += (
            f". {len(outside)} unit(s) return air outside the {low:g}-{high:g} degC "
            f"the selections cover and are not counted"
        )
    return line + "."


def _drift_line(kpis: dict) -> str:
    moved = kpis.get("drift_k")
    if moved is None:
        return "- (only one sample so far)"
    return f"{moved:.2f} K since the previous sample"


def report(results: PodResults) -> str:
    k = results.kpis
    lines = [
        f"Case '{results.case_name}' at iteration {results.time}",
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
        f"  Return grilles  "
        + (
            f"{k['grille_drop_pa']:.2f} Pa in the field, {k['grille_drop_asked_pa']:.2f} Pa from K"
            if k.get("grille_drop_pa") is not None
            else "open holes (no free area given)"
        ),
        *(
            [f"  HVAC sizing     {line}" for line in k.get("hvac_lines", [])]
        ),
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
    fans = k.get("fans", [])
    if len(fans) > 1:
        # Per unit, because that is the level the plant is judged at: a hall
        # uniform in the mean can still have one unit at the end of a row
        # taking half again its neighbours' flow, or carrying a share of the
        # load its coil cannot transfer at the air it receives.
        coil = any(f.get("available_kw") for f in fans)
        head = "  Fan wall     Supply kg/s   Rise Pa   Return degC    Heat kW"
        lines += ["", head + ("  Available kW   of avail" if coil else "")]
        for fan in fans:
            returned = fan.get("return_temp_c")
            heat = fan.get("heat_kw")
            lines.append(
                f"  {fan['name']:<12}{fan['supply_kg_s']:>13.3f}"
                f"{fan['rise_pa']:>10.1f}"
                + (f"{returned:>14.2f}" if returned is not None else f"{'-':>14}")
                + (f"{heat:>11.1f}" if heat is not None else f"{'-':>11}")
                + (
                    (f"{fan['available_kw']:>14.1f}"
                     f"{fan.get('of_available_pct', 0):>10.0f}%")
                    if fan.get("available_kw") else ""
                )
            )
        if k.get("available_kw"):
            lines += ["", "  " + _coil_line(k)]
    racks = k["racks"]
    listed = racks
    if len(racks) > REPORT_RACKS:
        listed = sorted(racks, key=lambda r: -r["inlet_top_c"])[:10]
        tops = [r["inlet_top_c"] for r in racks]
        over = sum(1 for r in racks if r["ashrae"]["above_recommended"])
        lines += [
            "",
            f"  {len(racks)} racks: inlet at the top {min(tops):.1f} to {max(tops):.1f} degC, "
            f"{over} above ASHRAE recommended. The ten warmest:",
        ]
    lines += ["", "  Rack              Load    Inlet      Top   Outlet    Rise   ASHRAE"]
    for rack in listed:
        inside = "ok" if rack["ashrae"]["within_recommended"] else "!"
        lines.append(
            f"  {rack['id']:<16}{rack['load_kw']:>5.1f} kW"
            f"{rack['inlet_c']:>9.1f}{rack['inlet_top_c']:>9.1f}{rack['outlet_c']:>9.1f}"
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


# --- viewer payload -----------------------------------------------------------


def export(
    model: Model,
    case_dir: str | Path,
    out_dir: str | Path,
    time: str | None = None,
    spec: dict | None = None,
) -> PodResults:
    """Write the 3-D viewer's payload for a solved POD.

    The same two files the M1 viewer already reads -- ``viewer.json`` for
    geometry, KPIs and checks, ``fields.bin`` for the fields themselves as
    float32 in [k, j, i] order -- plus the one thing a POD has that a plain
    room does not: its internal surfaces. Without them the viewer would show a
    slice through an empty box and leave the reader to imagine where the
    containment was, which is exactly the thing a picture is for.
    """
    from aicfd.foam import solverlog
    from aicfd.model import sensors

    case = Path(case_dir)
    results = analyse(model, case, time)
    step = case / results.time
    grid = read_grid(model, step)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Pressure ships relative to the fan intake, with the hydrostatic column
    # out of it: what a manometer would read against the machine's suction.
    reference = float(np.mean(_intakes(step, "p_rgh")))
    layers = {
        "T": grid["T"],
        "Ux": grid["U"][0],
        "Uy": grid["U"][1],
        "Uz": grid["U"][2],
        "P": grid["p_rgh"] - reference,
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
                    "units": {"T": "degC", "P": "Pa"}.get(name, "m/s"),
                }
            )
            offset += flat.size * 4

    log = _solver_log(case, solverlog)
    payload = {
        "case": results.case_name,
        "time": results.time,
        "valid": results.valid,
        "grid": {
            "divisions": list(model.divisions),
            "origin": list(model.domain.lo),
            "size": list(model.domain.size),
            "order": "kji",
        },
        "geometry": {
            "room": {"lo": list(model.domain.lo), "hi": list(model.domain.hi)},
            "zones": [
                {
                    "name": rack.id,
                    "lo": list(rack.box.lo),
                    "hi": list(rack.box.hi),
                    "load_w": rack.load_w,
                }
                for rack in model.racks
            ],
            # A POD is its internal surfaces. Handing them over lets the viewer
            # draw the containment, the false ceiling and the fan wall instead
            # of an empty box.
            "panels": [
                {
                    "name": panel.name,
                    "kind": panel.kind,
                    "axis": panel.axis,
                    "position": panel.position,
                    "sign": panel.sign,
                    "lo": list(panel.box().lo),
                    "hi": list(panel.box().hi),
                }
                for panel in model.panels
            ],
            "sensors": [
                {
                    "name": group.name,
                    "label": group.label,
                    "points": [list(point) for point in group.points],
                }
                for group in sensors(model)
            ],
            # Where to cut first. The middle of the box is a poor default for
            # a POD -- at 4 m it sits above the racks, in the one part of the
            # hall where nothing happens. Rack mid-height crosses the cold
            # aisle, the row and the contained hot aisle in a single plane.
            "default_slice": {
                "axis": 2,
                "index": int(
                    round(model.racks[0].box.hi[2] / 2 / model.cell(2))
                )
                if model.racks
                else model.divisions[2] // 2,
            },
            "patches": [name for pair in fan_pairs(step) for name in pair],
            "inlet_patch": None,  # the fan wall is internal, not a domain face
        },
        "fields": descriptors,
        "kpis": _viewer_kpis(model, results),
        "checks": [
            {"name": c.name, "passed": c.passed, "detail": c.detail, "status": c.status}
            for c in results.checks
        ],
        "warnings": list(model.warnings),
        "residuals": {
            "iterations": log.iterations if log else [],
            "series": {
                name: [None if v != v else v for v in values]
                for name, values in (log.residuals.items() if log else {})
            },
            "continuity": log.continuity if log else [],
        },
        "sensors": sensor_history(model, case),
    }
    # The page's own drawing code reads the model payload, so the plan and
    # sections that were checked before the run can be drawn again over the
    # solved field -- the same lines, now with a colour underneath.
    from aicfd.model import to_dict

    # The spec travels with the result. A result is only meaningful against
    # the inputs that produced it, and without them nothing downstream can
    # tell whether an export still belongs to the spec on screen -- which is
    # how a page ends up showing a superseded run under the current name
    # (ADR-030).
    payload["model"] = to_dict(model, spec or {})
    (out / "viewer.json").write_text(json.dumps(payload, indent=1))
    (out / "report.md").write_text(report(results))
    return results


def _viewer_kpis(model: Model, results: PodResults) -> dict:
    """The KPI names the M1 viewer already knows, filled from a POD."""
    k = results.kpis
    return {
        "cells": model.n_cells,
        "total_load_w": model.total_load_w,
        "supply_flow_m3h": k["supply_m3h"],
        "supply_temp_c": k["supply_temp_c"],
        "return_temp_c": k["return_temp_c"],
        "bulk_delta_t_k": k["delta_t_k"],
        "temp_max_c": k["peak_air_temp_c"],
        "speed_max_ms": k["peak_speed_ms"],
        "fan_rise_pa": k.get("fan_rise_pa"),
        "fan_static_pa": k.get("fan_static_pa"),
        "rack_drop_pa": k.get("rack_drop_pa"),
        "grille_drop_pa": k.get("grille_drop_pa"),
        "fan_operating_m3h": k.get("fan_operating_m3h"),
        "fan_operating_pa": k.get("fan_operating_pa"),
        "fan_rise_min_pa": k.get("fan_rise_min_pa"),
        "fans": k.get("fans", []),
        "rows": k.get("rows", []),
        "energy_closure": k["energy_closure"],
        # The heat the return air actually carries out. `energy_closure` is
        # this over the installed load, and a reader who has the ratio without
        # the kilowatts cannot check it or quote it.
        "recovered_kw": k.get("recovered_kw"),
        "unit_model": k.get("unit_model"),
        "available_kw": k.get("available_kw"),
        "utilisation_pct": k.get("utilisation_pct"),
        "units_over_capacity": k.get("units_over_capacity"),
        "catalogue_kw": k.get("catalogue_kw"),
        "coil_table_span_c": k.get("coil_table_span_c"),
        "coil_model": k.get("coil_model"),
        "coil_saturated_units": k.get("coil_saturated_units"),
        "coil_supply_setpoint_c": k.get("coil_supply_setpoint_c"),
        "coil_supply_needed_c": k.get("coil_supply_needed_c"),
        "coil_extrapolated": k.get("coil_extrapolated"),
        "coil_return_span_c": k.get("coil_return_span_c"),
        "coil_air_share_pct": k.get("coil_air_share_pct"),
        "coil_water_m3h": k.get("coil_water_m3h"),
        # Which units returned air the selections do not cover. Carried because
        # it is the reason a capacity is missing, and a missing number without
        # its reason reads as an oversight rather than a refusal (ADR-036).
        "coil_outside_table_c": k.get("coil_outside_table_c"),
        "hvac": k.get("hvac"),
        "alerts": k.get("alerts", []),
        "zones": [
            {
                "name": rack["id"],
                "row": rack["row"],
                "position": rack["position"],
                "load_w": rack["load_kw"] * 1000,
                "inlet_temp_c": rack["inlet_c"],
                "inlet_top_c": rack["inlet_top_c"],
                "peak_temp_c": rack["outlet_c"],
                # With the row closed every cubic metre the fan moves crosses
                # the racks, so there is no starvation ratio to report: it is
                # 100% by construction, and saying so would suggest the
                # simulation measured something it did not.
                "throughflow_ratio": None,
                "ashrae": rack["ashrae"],
            }
            for rack in k["racks"]
        ],
    }


def _solver_log(case: Path, solverlog):
    for name in ("log.buoyantSimpleFoam",):
        path = case / name
        if path.exists():
            try:
                return solverlog.parse(path)
            except Exception:
                return None
    return None
