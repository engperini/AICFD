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

import contextlib
import json
import re
import shutil
import threading
import time as time_module

try:  # POSIX only; on anything else the thread lock is what there is
    import fcntl
except ImportError:  # pragma: no cover - not a platform this tool runs on
    fcntl = None
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from aicfd.case import FAN_INTAKE, FAN_SUPPLY, KELVIN
from aicfd.foam.fields import patch_names, read_field, read_patch_field, to_grid
from aicfd.model import CP_AIR, Model, num

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

#: Surfaces the air is MEANT to cross, by name prefix. Everything else that
#: carries flow is a leak, which is what `sealed_envelope` is for.
#:
#: A ceiling return grille and the woven mesh closing the plenum into a
#: mechanical gallery are both holes with a pressure jump rather than walls
#: (ADR-048); so are a supply plenum's grilles (ADR-058), a raised floor's
#: perforated plates, and the mesh below its deck (ADR-076). Leaving one out
#: makes the check fail on a hall that is sealed, which is how this list came
#: to be a named constant instead of a literal buried in the check.
#:
PASSES_FLOW = ("grille", "plenum_opening", "supply", "floor_opening", "tile_")

#: A CAGE IS BOTH, under one name. Built in mesh it is a porous surface and
#: the air is meant to cross it; built in drywall it is a wall, and flow
#: through it is exactly the leak this check exists to find. The two are told
#: apart by how `createBaffles` names their patches -- a porous pair ends
#: `_below`/`_above`, a wall pair `_master`/`_slave` -- so the prefix list
#: above cannot express it and this one does (ADR-096).
PASSES_FLOW_PAIRS = (("cage_", ("_below", "_above")),)


def _passes_flow(name: str) -> bool:
    """Is this patch one the air is MEANT to cross?"""
    if name.startswith(PASSES_FLOW):
        return True
    return any(name.startswith(head) and name.endswith(tails)
               for head, tails in PASSES_FLOW_PAIRS)

#: How far apart the two ends of the return path may read before something is
#: wrong, in kelvin. Both are mixing-cup means over the whole stream, so this
#: is tighter than it looks: a settled, sealed loop closes them to hundredths,
#: and a tenth of a kelvin is already a leak or a volume still filling.
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

#: Below this, in pascals, a resistance check compares absolute pressures
#: rather than their ratio.
#:
#: A ratio is the right test while there is something to divide. A surface
#: open enough to cost nothing -- a full-width mesh leaf, a ceiling return
#: with no grille in it -- asks for hundredths of a pascal and the field
#: delivers hundredths of a pascal, and the quotient of two numbers that are
#: both nearly zero is noise. The mesh leaf failed at "0.00 Pa against 0.00
#: Pa (0%)", which is not a disagreement about anything (ADR-060).
NEGLIGIBLE_PRESSURE_PA = 0.5

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
    # Under the reconstruction lock for its whole length: this is the reader a
    # torn field would mislead, and it is routinely run against a case that is
    # still solving.
    with reconstruction_lock(case_dir):
        return _analyse(model, case_dir, time)


def _analyse(model: Model, case_dir: str | Path, time: str | None = None) -> PodResults:
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
    kpis["aisle_exit_c"] = aisle_exit_temperature(model, grid)
    kpis["peak_speed_ms"] = round(float(np.linalg.norm(grid["U"], axis=0).max()), 3)
    kpis["peak_air_temp_c"] = round(float(grid["T"].max()), 2)
    kpis["racks"] = rack_temperatures(model, grid)

    kpis["rack_drop_pa"], kpis["rows"] = rack_pressure_drop(model, grid)
    kpis["grille_drop_pa"] = grille_pressure_drop(step)
    kpis["grille_drop_asked_pa"] = round(model.grille_pressure_drop_pa, 3)
    kpis["mesh_drop_pa"] = grille_pressure_drop(step, "plenum_opening")
    kpis["supply_drop_pa"] = grille_pressure_drop(step, "supply")
    kpis["grille_spread"] = flow_spread(step, "grille")
    kpis["supply_spread"] = flow_spread(step, "supply")
    kpis["floor_spread"] = flow_spread(step, "tile_")
    kpis["grille_reverse"] = reverse_fraction(step, "grille")
    kpis["supply_reverse"] = reverse_fraction(step, "supply")
    kpis["floor_reverse"] = reverse_fraction(step, "tile_")
    kpis["supply_drop_asked_pa"] = (
        round(model.plenum_pressure_drop_pa, 3)
        if model.plenum_pressure_drop_pa is not None else None
    )
    kpis["supply_face_velocity_ms"] = (
        round(model.plenum_face_velocity_ms, 3)
        if model.plenum_face_velocity_ms is not None else None
    )
    kpis["mesh_drop_asked_pa"] = round(model.mesh_pressure_drop_pa, 3) \
        if model.mesh_pressure_drop_pa is not None else None
    # A raised floor's plates are a perforated surface like any other, and
    # every cubic metre the units move crosses them once (ADR-076).
    kpis["floor_drop_pa"] = grille_pressure_drop(step, "tile_")
    kpis["floor_drop_asked_pa"] = (
        round(model.floor_pressure_drop_pa, 3)
        if model.floor_pressure_drop_pa is not None else None
    )
    kpis["floor_face_velocity_ms"] = (
        round(model.floor_face_velocity_ms, 3)
        if model.floor_face_velocity_ms is not None else None
    )
    kpis["floor_tiles"] = len(model.floor_tiles) or None
    # Which architecture this run is, for everything that reads the export
    # rather than the model: the report's wording, the page, the document.
    kpis["floor_height"] = model.floor_height
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
    kpis["stations_now"] = history[-1]["stations"] if history else []
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
        # No second way of answering. Every unit is modelled the same way --
        # one selection is enough to recover the coil, and that is what every
        # unit carries -- so a unit that cannot be fitted is a unit whose file
        # is incomplete, and it says which field is missing rather than
        # switching to a different way of answering. Reading a capacity table
        # instead used to be the fallback, and it meant two classes of unit
        # with different answers and a refusal off the table that the fitted
        # ones never hit (ADR-063).
        # A DX unit also carries the return its rating was taken at, because
        # the alert below compares the two and a rating is only a rating at
        # its own return (ADR-097).
        return {"unit_model": unit.model, "coil_problem": unit.coil_problem,
                "rated_return_c": (unit.design or {}).get("return_c"),
                "rated_nscc_kw": (unit.design or {}).get("nscc_kw")}

    from aicfd.coil import air_capacity_rate

    # The model's own per-unit airflow, not the total divided by however many
    # fans happened to be measured: those are the same number in a solved run
    # and very different ones anywhere else.
    per_unit = model.unit_airflow_m3h
    setpoint = model.supply_temp_c
    available = removed = 0.0
    saturated, warmest_supply, seen, air_seen = 0, setpoint, [], 0.0
    # The worst return each gallery's network is reading. Read once, so the
    # capacity reported here is worked out the same way the coupled loop
    # worked out the supply it imposed (ADR-064).
    by_name = {f["name"]: f.get("return_temp_c") for f in fans}
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
        worst = max((by_name[peer] for peer in model.team_of(fan["name"])
                     if by_name.get(peer) is not None), default=temperature)
        point = coil.operate_shared(worst, temperature, air, setpoint)
        fan["available_kw"] = round(point.ceiling_kw, 1)
        fan["coil_supply_c"] = round(point.supply_c, 2)
        # How much of its water-side authority the unit is using: the number
        # that says whether it can still hold its supply temperature. The flow
        # itself is a hydraulic question this tool does not answer.
        fan["coil_valve_pct"] = round(point.valve * 100, 0)
        water_out = coil.leaving_water_c(point.capacity_kw)
        if water_out is not None:
            fan["coil_water_out_c"] = round(water_out, 2)
        available += point.ceiling_kw
        if fan.get("heat_kw") is not None:
            removed += fan["heat_kw"]
            fan["of_available_pct"] = round(fan["heat_kw"] / point.ceiling_kw * 100, 1)
        if point.saturated:
            saturated += 1
            warmest_supply = max(warmest_supply, point.supply_c)
        # WHAT IS HOLDING THIS UNIT: its coil, or its compressors (ADR-118).
        at_limit = getattr(coil, "at_compressor_limit", None)
        fan["coil_at_limit"] = bool(at_limit(temperature, air)) if at_limit else False
    if not available:
        return {}
    return {
        "unit_model": unit.model,
        # The plate figure and the return it holds at. A rating is only a
        # rating at its own return, and the alert below says how far the room
        # ran from it (ADR-097, ADR-103).
        "rated_return_c": (unit.design or {}).get("return_c"),
        "rated_nscc_kw": (unit.design or {}).get("nscc_kw"),
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
        # The coil describes ITSELF -- a chilled-water one in water
        # temperatures and a valve, a direct-expansion one in its apparatus
        # dew point and its compressors -- so nothing here has to know which
        # kind it is holding (ADR-103).
        "coil_model": {
            **coil.describe(),
            # Fields the datasheet did not print, which somebody read out of
            # it. Carried so a report never quotes such a unit as though it
            # were admitted on its own arithmetic (ADR-071).
            "derived_fields": list(unit.derived_fields) if unit else [],
        },
        # The whole point of modelling the machine: the supply temperature is
        # an output, and where the valve runs out it stops matching the one
        # the run imposed. Then every temperature in the result is optimistic.
        "coil_saturated_units": saturated,
        # How many units the COMPRESSORS are holding back rather than the
        # coil: past that return the evaporator would transfer more and the
        # machine cannot lift it (ADR-118).
        "coil_at_limit_units": sum(1 for f in fans if f.get("coil_at_limit")),
        "coil_supply_setpoint_c": round(setpoint, 2),
        "coil_supply_needed_c": round(warmest_supply, 2),
        "coil_return_span_c": [round(min(seen), 2), round(max(seen), 2)] if seen else None,
        # What the water would have to do for the heat above. A coil at a
        # return far past its selection transfers far more than the catalogue
        # figure -- that is the heat exchanger, not a mistake -- and this is
        # the number that says whether the PLANT could ever give it that
        # (ADR-063).
        "coil_water_out_c": (round(max(
            (f["coil_water_out_c"] for f in fans if f.get("coil_water_out_c")),
            default=coil.water_c), 2)
            if getattr(coil, "water_c", None) is not None else None),
        "coil_water_out_design_c": unit.design_leaving_water_c,
        # On the mass the solve actually moved, the same basis every other
        # number here uses. Deriving it from the nominal volume flow instead
        # would put a few per cent of density between two figures a reader
        # would reasonably expect to agree.
        "coil_air_share_pct": round(air_seen / len(seen) / coil.air_fitted * 100),
    }


def _coil_provenance(kpis: dict) -> str:
    """Whose numbers the coil is answering on.

    The model answers at any return, above the selection and below it, with
    the same confidence for a unit fitted to a manufacturer's own set and for
    one fitted to a single selection with the air/water split assumed. The
    reader has to be able to tell which, because the second is theirs to
    validate and the first is not (ADR-063).
    """
    coil = kpis.get("coil_model") or {}
    if not coil:
        return ""
    if coil.get("kind") == "dx":
        # A direct-expansion coil is recovered from the psychrometry of one
        # selection: the surface temperature its own sensible/total split
        # implies (ADR-103). What it assumed, where the sheet was thin, is
        # said in the same breath as the capacity it decides.
        line = (f". Its evaporator is fitted to the design selection, with the "
                f"coil surface at {coil.get('adp_c')} degC and "
                f"{coil.get('contact_factor_pct')}% of the air reaching it")
        for assumption in coil.get("assumptions") or []:
            line += f". Assumed: {assumption}"
        return line
    checked = coil.get("reference_selections") or 0
    error = coil.get("reference_error_k")
    if checked >= 2 and error is not None:
        line = (f". Its coil is fitted to the design selection and reproduces "
                f"{checked} more of the manufacturer's to {error:.3g} K")
    else:
        line = (f". Its coil is fitted to the design selection alone, with the "
                f"air/water split assumed at {coil.get('air_split_pct')}% -- the "
                f"one number here nobody measured")
    derived = coil.get("derived_fields") or []
    if derived:
        # Said outright, and in the same breath as the capacity it decides.
        # A unit read out of an ambiguous sheet is weaker evidence than one
        # that closed on its own, and only its own file knows that.
        line += (f". Its selection did not state {', '.join(sorted(derived))}"
                 f" -- that was read out of the sheet, and the capacity here "
                 f"follows from it")
    return line


def _coil_alerts(kpis: dict) -> list[str]:
    """What the coil model found, said where a reader will see it.

    An alert, not a failed check, because none of this makes the field wrong
    on its own terms -- the solve did what it was told. What it says is that
    what it was told may not be what the machine would do, and that is a
    judgement for the engineer reading it (ADR-023, ADR-039).
    """
    out = []
    problem = kpis.get("coil_problem")
    # THE SAME PARAGRAPH THREE TIMES IS NOT THREE WARNINGS. A DX unit's
    # limitation used to arrive here, off the build, and again off the coil --
    # one long block of boilerplate on every run, in the card meant for design
    # criteria the plant MISSES. It is not a criterion and it does not change
    # from run to run, so it belongs in the report's limitations, where a
    # reader meets it once (ADR-098). What stays here is the one line that IS
    # about this run: how far the room's return is from the rating.
    if problem and not kpis.get("rated_return_c"):
        # An unfinished file, not a DX unit: the capacity beside it came from
        # nowhere a reader can check, and that IS worth saying every time.
        out.append(
            f"Capacity here is the catalogue figure alone, which holds only "
            f"at the return air the unit was selected for: {problem}."
        )
    # HOW FAR OFF ITS RATING THE ROOM ACTUALLY RUNS. The sentence above says
    # the rating holds at one return; this says what return the room produced,
    # and a reader should not have to find the two numbers and subtract them.
    # Measured on the first DX case run here: a unit rated 51,7 kW at 30,0 degC
    # return, in a room that returns 21,8 -- 8,2 K below the rating, where a
    # DX circuit does markedly less sensible work than its plate says
    # (ADR-097).
    rated_at = kpis.get("rated_return_c")
    actual = kpis.get("return_temp_c")
    rated = kpis.get("rated_nscc_kw")
    if problem and rated_at and actual is not None and abs(actual - rated_at) > 2.0:
        out.append(
            f"The room returns {num(actual, 1)} degC and "
            f"{kpis.get('unit_model')} is rated"
            + (f" {num(rated, 1)} kW" if rated else "")
            + f" at {num(rated_at, 1)} degC -- "
            f"{num(abs(actual - rated_at), 1)} K "
            + ("below" if actual < rated_at else "above")
            + " it. The capacity quoted above is the plate figure, and this "
            "unit's file does not carry what its coil would need to answer "
            "at the return the room gives it."
        )
    # A UNIT WITH A COIL ANSWERS FOR ITSELF. It used to say "ask the
    # manufacturer for its capacity at 26,4 degC", which is the one thing a
    # model of the machine exists to avoid -- so it says what the machine
    # does there, and how that compares with the plate (ADR-103).
    per_unit = (kpis.get("available_kw") or 0) / max(1, len(kpis.get("fans") or []))
    if (not problem and rated and rated_at and actual is not None
            and per_unit and abs(actual - rated_at) > 2.0):
        out.append(
            f"The room returns {num(actual, 1)} degC and {kpis.get('unit_model')} "
            f"is rated {num(rated, 1)} kW at {num(rated_at, 1)} degC -- "
            f"{num(abs(actual - rated_at), 1)} K "
            + ("below" if actual < rated_at else "above")
            + f" it. At the return this run produced its coil gives "
            f"{num(per_unit, 1)} kW per unit, "
            f"{num(per_unit / rated * 100, 0)}% of the plate figure. The plate "
            f"is not the capacity this room has; the number above is."
        )
    water, design = kpis.get("coil_water_out_c"), kpis.get("coil_water_out_design_c")
    if water and design and water > design + 0.5:
        out.append(
            f"The coils are transferring more than the water side was sized "
            f"for: at the air they are receiving they move "
            f"{kpis['available_kw']:,.0f} kW, which would take the water out "
            f"at {water:.1f} degC against the {design:g} degC of the "
            f"selection. The heat exchanger really does that at this return; "
            f"whether the chiller, the pump and the valve can hold "
            f"{kpis['coil_model']['water_max_m3h']:g} m3/h at that rise is a "
            f"question this tool does not answer."
        )
    at_limit = kpis.get("coil_at_limit_units") or 0
    coil_model = kpis.get("coil_model") or {}
    if at_limit and coil_model.get("capacity_ceiling_kw"):
        net = coil_model["capacity_ceiling_kw"] - (
            kpis.get("coil_fan_power_kw") or 0.0)
        out.append(
            f"{at_limit} unit(s) are at the COMPRESSOR limit, not the coil's: "
            f"at the return they receive their evaporator would transfer more "
            f"than the machine can lift, so the capacity is held at the "
            f"{coil_model['capacity_ceiling_kw']:,.1f} kW gross the selection "
            f"itself was taken at"
            + (f", at {coil_model['rated_ambient_c']:g} degC outdoor air"
               if coil_model.get("rated_ambient_c") else "")
            + ". A warmer day lowers it further, which this study does not "
              "model."
        )
    saturated = kpis.get("coil_saturated_units") or 0
    if saturated:
        out.append(
            f"{saturated} unit(s) cannot hold the "
            f"{kpis['coil_supply_setpoint_c']:.1f} degC supply air this run "
            f"imposed: with the "
            f"{(kpis.get('coil_model') or {}).get('duty_label', 'water valve')} "
            f"wide open their coil delivers "
            f"{kpis['coil_supply_needed_c']:.1f} degC at the return they "
            f"receive. Every temperature in this result is that much "
            f"optimistic -- re-run at the higher supply temperature."
        )
    return out


def grille_pressure_drop(step: str | Path, prefix: str = "grille") -> float | None:
    """The jump the field shows across one kind of perforated surface, in Pa.

    Each is a cyclic pair; the drop is the mean p_rgh on the upstream face
    minus the mean on the downstream one, flow-weighted across the surfaces of
    that kind.

    UPSTREAM, not `_below`. `createBaffles` hands the master patch to the face's
    owner cell, which for an x-normal face is the cell at lower x -- so on a
    hall with a gallery at each end the two supply meshes face opposite ways:
    air leaves the first gallery towards higher x and the second towards lower
    x. Taking `_below` as upstream on both made one drop come out negative,
    and the flow-weighted mean of +0,4 and -0,4 Pa is nothing at all. The check
    then reported -133% of what the mesh asks on a hall whose meshes were both
    working (ADR-080). The sign comes from phi, which knows which way the air
    is going.

    ``prefix`` matters. Every cyclic pair in the case ends `_below`, so taking
    them all mixed the ceiling return grilles with the woven mesh closing the
    plenum into a mechanical gallery -- two surfaces with different open areas
    -- into one weighted mean, which then matched neither one's K. Each is
    measured against its own (ADR-048).
    """
    phi_path = Path(step) / "phi"
    names = [n for n in patch_names(phi_path)
             if n.endswith("_below") and n.startswith(prefix)]
    if not names:
        return None
    total_flow, weighted = 0.0, 0.0
    for below in names:
        above = below[: -len("_below")] + "_above"
        net = float(np.sum(read_patch_field(phi_path, below)))
        drop = float(
            np.mean(read_patch_field(Path(step) / "p_rgh", below))
            - np.mean(read_patch_field(Path(step) / "p_rgh", above))
        )
        # phi is positive out of the owner cell, which is the `_below` side.
        # Positive net flow means the air runs below -> above and `_below` is
        # upstream; negative means the surface is being crossed the other way
        # and the same physical drop reads with the opposite sign.
        if net < 0:
            drop = -drop
        total_flow += abs(net)
        weighted += abs(net) * drop
    return round(weighted / total_flow, 3) if total_flow else None


def flow_spread(step: str | Path, prefix: str = "grille") -> float:
    """How much more a quadratic resistance costs than its RATED face velocity
    says, because of how the air actually reaches it. Dimensionless.

    A perforated surface costs ``K rho u^2 / 2`` FACE BY FACE, so what it
    really costs follows ``mean(u^2)``. Its rating is taken at the face
    velocity on the drawing -- the flow the surface passes, over its area --
    which is ``net / area``. This is the ratio between the two, and it is 1,0
    exactly when the air arrives evenly and all of it in one direction.

    THE DENOMINATOR IS THE NET, NOT THE MEAN OF MAGNITUDES. Those are the same
    number only while nothing crosses backwards, and the surface this was
    written for is crossed backwards: 9,7% of the mass that passes the supply
    mesh passes it INTO the plenum, because a 1,2 m cavity fed by three
    discrete fan walls jets through opposite each unit and draws back in
    between them. Dividing by the mean magnitude quietly credited the surface
    for that return flow and left 38% of the measured drop unexplained
    (ADR-082).

    Two effects, one number, and both belong to the plant rather than to the
    surface: the air is not spread, and some of it is going round in circles.
    """
    phi_path = Path(step) / "phi"
    names = [n for n in patch_names(phi_path)
             if n.endswith("_below") and n.startswith(prefix)]
    if not names:
        return 1.0
    # ONE SURFACE, NOT 286 OF THEM. This used to measure each plate against
    # its OWN rated velocity and average the results, which is the variation
    # INSIDE a plate -- a couple of cells, so 1,09 on a raised floor. What
    # costs the pressure is the variation BETWEEN the plates: on a 1 MW hall
    # they run from 0,08 to 1,14 m/s, because a plate over the CRAC's
    # discharge and a plate at the end of the plenum are not the same plate.
    # Every face of the kind is one population, against the face velocity the
    # drawing rates them all at (ADR-119).
    fluxes = [read_patch_field(phi_path, below) for below in names]
    flux = np.concatenate(fluxes) if fluxes else np.array([])
    net, gross = float(flux.sum()), float(np.abs(flux).sum())
    # A surface with no net flow has no rated face velocity to be measured
    # against -- the ratio would be a division by nearly zero, and what it
    # would be describing is a surface that is passing nothing.
    if flux.size < 2 or not gross or abs(net) < 0.01 * gross:
        return 1.0
    # WEIGHTED THE WAY THE DROP IS. `grille_pressure_drop` reports the
    # flow-weighted mean of the jump, so the closed form it is judged against
    # has to be the flow-weighted mean of `K rho u^2 / 2` over the same faces.
    # Judged against the second moment instead, an evenly-built floor read 20%
    # out; against this, 0,5% (ADR-119).
    #
    # The faces of one kind of surface all have the same area in a structured
    # hexahedral block, which is the only mesh this tool builds, so the flux
    # per face is the face velocity in another unit and the ratio is the same.
    rated = net / flux.size
    weight = np.abs(flux)
    return round(float((weight * flux**2).sum() / weight.sum() / rated**2), 3)


def reverse_fraction(step: str | Path, prefix: str = "grille") -> float:
    """How much of the mass crossing a family of surfaces crosses it the wrong
    way, as a fraction of the net.

    Named separately from `flow_spread` because it is a different fault with a
    different remedy: uneven flow wants a deeper plenum or a diffuser, air
    going round in circles wants the units aimed or spaced differently. The
    one number they share is the drop, which is why the drop alone was never
    going to say which it was.
    """
    phi_path = Path(step) / "phi"
    names = [n for n in patch_names(phi_path)
             if n.endswith("_below") and n.startswith(prefix)]
    net_total, wrong = 0.0, 0.0
    for below in names:
        flux = read_patch_field(phi_path, below)
        net = float(flux.sum())
        if not net:
            continue
        back = np.clip(flux, None, 0) if net > 0 else np.clip(flux, 0, None)
        net_total += abs(net)
        wrong += float(np.abs(back).sum())
    return round(wrong / net_total, 4) if net_total else 0.0


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
    # THE RACK'S OWN BAND, not everything below its top. The two are the same
    # room when the cabinets stand on the slab, which is why this read right
    # for years. Put the room on an access floor and everything below the rack
    # top includes the supply plenum, whose pressure is the one driving the
    # whole loop -- averaged into both planes it dragged the measured drop to
    # 70% of what the rack curve asks, and the check called the field wrong
    # when it was the sampling (ADR-076).
    rack_low = model.racks[0].box.lo[2]
    rack_top = model.racks[0].box.hi[2]

    def plane(position: float, span: tuple[float, float]) -> float:
        picks = [
            np.where((coords[0] >= span[0]) & (coords[0] <= span[1]))[0],
            [int(np.argmin(abs(coords[1] - position)))],
            np.where((coords[2] >= rack_low) & (coords[2] <= rack_top))[0],
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
    before = {
        station["name"]: station["temp_c"]
        for station in previous["stations"]
        if station["temp_c"] is not None
    }
    moves = [
        abs(station["temp_c"] - before[station["name"]])
        for station in last["stations"]
        if station["temp_c"] is not None and station["name"] in before
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


def aisle_exit_temperature(model: Model, grid: dict) -> float | None:
    """Mixing-cup temperature of the air leaving the contained aisles, in degC.

    The companion to `return_temperature`, at the other end of the return path:
    that one is the mixed mean of the air arriving at the fan intakes, this one
    the mixed mean of the air leaving the containment through the ceiling. Two
    streams, no probes, so a row of uneven load cannot move either.

    Measured on the cell layer immediately below the false ceiling, over the
    hot aisles and inside the hall, weighted by the mass flux LEAVING through
    it (rho u, upward only). The weighting selects the grilles without being
    told where they are: a cell under solid ceiling carries no upward flux and
    contributes nothing, and a cell under a grille contributes in proportion to
    what it passes. Cells outside the hall are excluded because a gallery with
    no false ceiling has room air at this height and it is not on this path.
    """
    if not model.hot_aisles:
        return None
    x, y, z = grid["x"], grid["y"], grid["z"]
    layer = int(np.argmin(abs(z - (model.ceiling_z - model.cell_size[2] / 2))))
    aisle = np.concatenate(
        [np.where((y >= lo) & (y <= hi))[0] for lo, hi in model.hot_aisles]
    )
    inside = np.where((x >= model.hall.lo[0]) & (x <= model.hall.hi[0]))[0]
    if aisle.size == 0 or inside.size == 0:
        return None
    picks = np.ix_(aisle, inside)
    temperature = grid["T"][layer][picks]
    # rho u, not u: the mixing cup weights by mass, and at 20 kelvin of spread
    # the hot cells are 6% lighter than the cold ones.
    flux = np.clip(grid["U"][2][layer][picks], 0, None) / (temperature + KELVIN)
    total = float(flux.sum())
    if total <= 0:
        return None
    return round(float((flux * temperature).sum() / total), 2)


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


def _resistance_verdict(delivered: float, asked: float, spread: float = 1.0,
                        reverse: float = 0.0) -> tuple[bool, str]:
    """Whether a surface's field drop agrees with its closed form, and why.

    Two regimes, one rule: a ratio while the pressures are worth dividing, an
    absolute difference once both are too small to tell apart.

    ``spread`` is what the surface costs under the flow it actually gets,
    over what it would cost with that flow spread evenly (`flow_spread`). The
    verdict is taken against ``asked * spread``, because that is the closed
    form for THIS field; ``asked`` alone is the design figure and stays in the
    sentence, where an engineer can see how far the plant is from it (ADR-082).
    """
    expected = asked * spread
    if abs(delivered - expected) <= NEGLIGIBLE_PRESSURE_PA and expected <= NEGLIGIBLE_PRESSURE_PA:
        return True, (
            f" -- both under {NEGLIGIBLE_PRESSURE_PA:g} Pa, so this surface is "
            f"too open for the ratio to mean anything"
        )
    # A RATIO ON A PASCAL IS NOISE -- in ONE direction. 1,38 Pa where the
    # closed form asks 1,05 is 31 % out and three tenths of a pascal, below
    # what a conceptual mesh and a mixing-cup average tell apart, and it
    # failed a 1 MW hall whose plates are too open to cost anything (ADR-115).
    #
    # The forgiveness is only for a surface costing MORE than its K asks. What
    # this check is for is the opposite: a zone not delivering the resistance
    # it was given makes the fan pressure read off the field too low, and a
    # machine sized on that is undersized. Half the drop is still half the
    # drop, however few pascals it is (ADR-082).
    passed = (abs(delivered / expected - 1.0) <= RESISTANCE_TOLERANCE
              or 0.0 <= delivered - expected <= NEGLIGIBLE_PRESSURE_PA)
    if spread < 1.05:
        return passed, ""
    why = (
        f"; the air reaches it {spread:.1f} times harder than the rated face "
        f"velocity assumes, so its own K asks {expected:.2f} Pa of this field"
    )
    if reverse >= 0.02:
        why += (
            f", and {reverse * 100:.0f}% of the mass crossing it is going back "
            f"the other way"
        )
    return passed, why


def return_path_check(kpis: dict) -> "Check | None":
    """Does the air arriving at the units weigh the same heat as the air that
    left the aisles?

    Both ends are mixing-cup means over the whole stream -- `aisle_exit_c`
    through the ceiling, `return_temp_c` at the intakes -- so this measures the
    path and nothing else. Between them every wall is adiabatic and every
    opening is meant to pass this air, so a difference is air joining the path
    from somewhere else, or a volume that has not finished filling.

    It used to compare three point probes instead, and a row of uneven load
    broke it: a probe measures the cell it sits in, `hot_aisle` and `plenum`
    shared their columns, and a typical row repeated down the hall repeated
    whatever those columns landed on. A settled, sealed POD closing its streams
    to 0,02 K failed this at 1,54 K, and a hall of 0 kW and 32 kW cabinets
    failed it by nearly 9 K, with nothing wrong in either (ADR-078).

    The unevenness those probes were groping at is now measured properly, as
    the range of the air actually leaving through the ceiling, and said here
    where it explains why one number stands for a stream that spans twelve
    kelvin.
    """
    leaving, arriving = kpis.get("aisle_exit_c"), kpis.get("return_temp_c")
    if leaving is None or arriving is None:
        return None
    spread = abs(arriving - leaving)
    exit_station = next(
        (s for s in kpis.get("stations_now", []) if s["name"] == "aisle_exit"), None
    )
    low = exit_station and exit_station.get("low_c")
    high = exit_station and exit_station.get("high_c")
    uneven = (
        f"; the air crossing the ceiling spans {low:.1f} to {high:.1f} degC, "
        "which is the room being uneven, not the path leaking"
        if low is not None and high is not None
        and high - low > RETURN_PATH_TOLERANCE
        else ""
    )
    return Check(
        "return_path",
        spread <= RETURN_PATH_TOLERANCE,
        "nothing heats or cools the air between the containment and the fan "
        f"intake, so these have to agree: the aisles pass {leaving:.1f} degC "
        f"through the ceiling and the units draw {arriving:.1f} degC "
        f"({spread:.2f} K apart)"
        + (uneven if spread <= RETURN_PATH_TOLERANCE else
           " -- air is joining the return path, or the field has not filled; "
           "energy_closure and settled say which"),
    )


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
        and not _passes_flow(name)
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

    path_check = return_path_check(kpis)
    if path_check is not None:
        checks.append(path_check)

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
                + (f" -- {model.unloaded_racks} of {len(model.racks)} positions "
                   f"carry no load; blanked, they resist like the rest, so the "
                   f"row is still one resistance and only the heat is missing"
                   if model.unloaded_racks else "")
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
        passed, why = _resistance_verdict(
            grille_drop, grille_asked, kpis.get("grille_spread", 1.0),
            kpis.get("grille_reverse", 0.0))
        checks.append(
            Check(
                "grille_resistance",
                passed,
                f"the field drops {grille_drop:.2f} Pa across the return grilles "
                f"where their K at {model.airflow_m3h:,.0f} m3/h asks for "
                f"{grille_asked:.2f} Pa ({ratio * 100:.0f}%)" + why,
            )
        )

    supply_drop, supply_asked = (
        kpis.get("supply_drop_pa"), kpis.get("supply_drop_asked_pa"))
    if supply_drop is not None and supply_asked:
        ratio = supply_drop / supply_asked
        velocity = kpis.get("supply_face_velocity_ms")
        passed, why = _resistance_verdict(
            supply_drop, supply_asked, kpis.get("supply_spread", 1.0),
            kpis.get("supply_reverse", 0.0))
        checks.append(
            Check(
                "plenum_resistance",
                passed,
                f"the field drops {supply_drop:.2f} Pa across the supply grilles "
                f"where their K at {model.airflow_m3h:,.0f} m3/h asks for "
                f"{supply_asked:.2f} Pa ({ratio * 100:.0f}%)"
                # The velocity, with no verdict attached: what is high for
                # one hall is ordinary in another, and the reader knows which
                # they have (ADR-059).
                + (f"; they run at {velocity:.1f} m/s on the face"
                   if velocity is not None else "")
                + why,
            )
        )

    floor_drop, floor_asked = (
        kpis.get("floor_drop_pa"), kpis.get("floor_drop_asked_pa"))
    if floor_drop is not None and floor_asked:
        ratio = floor_drop / floor_asked
        velocity = kpis.get("floor_face_velocity_ms")
        passed, why = _resistance_verdict(
            floor_drop, floor_asked, kpis.get("floor_spread", 1.0),
            kpis.get("floor_reverse", 0.0))
        checks.append(
            Check(
                "floor_resistance",
                passed,
                f"the field drops {floor_drop:.2f} Pa across the "
                f"{kpis.get('floor_tiles')} floor plates where their K at "
                f"{model.airflow_m3h:,.0f} m3/h asks for {floor_asked:.2f} Pa "
                f"({ratio * 100:.0f}%)"
                # The velocity, with no verdict attached, for the same reason
                # the plenum's carries none (ADR-059).
                + (f"; they run at {velocity:.2f} m/s on the face"
                   if velocity is not None else "")
                + why,
            )
        )

    rise = kpis.get("fan_rise_pa")
    available = model.fan_available_pa()
    if rise is not None and available:
        fans = kpis.get("fans") or []
        loaded = max(fans, key=lambda f: f["rise_pa"])["name"] if len(fans) > 1 else None
        # WHOSE WORK IS THIS? The fan rise the field shows is the whole loop,
        # because this model has no rack fans: the units drive the air through
        # the cabinets as well (ADR-013). A real cabinet's own fans do that
        # part, in series, and a unit's EXTERNAL STATIC PRESSURE is what it
        # offers the room OUTSIDE itself -- plenum, plates, aisles, grilles,
        # gallery. Charging it for the servers' fans too failed a 1 MW hall at
        # 219 % where the room outside the cabinets costs a quarter of that
        # (ADR-115).
        cabinets = kpis.get("rack_drop_pa") or 0.0
        room = max(rise - cabinets, 0.0)
        checks.append(
            Check(
                "fan_capacity",
                room <= available,
                (
                    f"the room outside the cabinets costs the most loaded unit "
                    f"({loaded}) {room:.1f} Pa "
                    if loaded
                    else f"the room outside the cabinets costs {room:.1f} Pa "
                )
                + (
                    f"-- {rise:.1f} Pa of loop less the {cabinets:.1f} Pa the "
                    f"cabinets' own fans carry -- "
                    if cabinets
                    else ""
                )
                + (
                    f"and the unit's P-Q curve offers {available:.0f} Pa at "
                    if model.fan_curve
                    else f"and the unit's datasheet offers {available:.0f} Pa at "
                )
                + f"{model.unit_airflow_m3h:,.0f} m3/h per unit ({room / available * 100:.0f}%)"
                + (
                    f"; uncontrolled at full speed it would run at "
                    f"{kpis['fan_operating_m3h']:,.0f} m3/h and {kpis['fan_operating_pa']:.1f} Pa"
                    if kpis.get("fan_operating_m3h")
                    else ""
                )
                + ("" if room <= available else " -- the unit cannot deliver this airflow"),
            )
        )

    moved = kpis.get("drift_k")
    checks.append(
        Check(
            "settled",
            moved is not None and moved <= STEADY_TOLERANCE,
            "not enough samples to tell whether anything is still moving"
            if moved is None
            else f"the stations moved at most {moved:.2f} K since the previous "
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

#: Where a running solve records what its stations are reading. Kept
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


#: Between threads. The cross-process half is a lock file beside the case,
#: because the readers that matter are other *programs*: `aicfd post` or the
#: results page opened while a solve is still running.
_RECONSTRUCT_GUARD = threading.RLock()
_RECONSTRUCT_DEPTH = threading.local()

#: What the lock file is called. Beside the case rather than in it, so a
#: `runs/<case>/` that is copied or cleaned does not carry a stale lock.
LOCK_NAME = ".aicfd-reconstruct.lock"


@contextlib.contextmanager
def reconstruction_lock(case_dir: str | Path, timeout: float = 180.0):
    """Exclusive access to a case's reconstructed time directories.

    Held by whoever rebuilds one and by whoever reads one. Two reconstructions
    of the same time write the same files at the same moment and a reader
    between them sees half a field -- which is how a coupled run died on
    `300/phi: no boundaryField`, with the sampler and the coupling loop both
    rebuilding time 300 (ADR-040).

    Across processes as well as threads, because the readers that matter are
    other programs: `aicfd post` on a case that is still solving, or the
    results page refreshed mid-run. A thread lock alone leaves exactly those
    unprotected.

    Re-entrant, since analysing a result reads and samples and the sampling
    reconstructs. Advisory: after `timeout` it proceeds anyway rather than
    fail a solve that has already cost half an hour, and says nothing -- a
    reader that waited three minutes for a reconstruction has a worse problem
    than a torn field.
    """
    depth = getattr(_RECONSTRUCT_DEPTH, "value", 0)
    if depth:
        _RECONSTRUCT_DEPTH.value = depth + 1
        try:
            yield
        finally:
            _RECONSTRUCT_DEPTH.value -= 1
        return

    _RECONSTRUCT_GUARD.acquire()
    handle = None
    try:
        if fcntl is not None:
            path = Path(case_dir) / LOCK_NAME
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                handle = path.open("w")
            except (OSError, ValueError):
                # A read-only checkout, or a path the filesystem will not take.
                # The thread lock is then what there is; refusing to lock is
                # not a reason to refuse to post-process.
                handle = None
        if handle is not None:
            deadline = time_module.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time_module.monotonic() >= deadline:
                        break
                    time_module.sleep(0.2)
        _RECONSTRUCT_DEPTH.value = 1
        yield
    finally:
        _RECONSTRUCT_DEPTH.value = 0
        if handle is not None:
            try:
                fcntl.flock(handle, fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
        _RECONSTRUCT_GUARD.release()


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
    with reconstruction_lock(case):
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


def station_readings(model: Model, step: str | Path, grid: dict,
                     fans: list[dict]) -> list[dict]:
    """What each station of the air loop reads, at one written time.

    Four streams, in the order the air passes them, each reported the way an
    air-side test report reports a duct: the mixing-cup temperature of the air
    crossing it, the range that air spans, the volume it carries and the mean
    velocity through the surface it crosses.

    The mixing cup is the only temperature that means anything here. A room
    with cabinets of different load has no single temperature at any station --
    on a half-populated row the aisle runs twelve kelvin along its own length
    -- so an average taken over area, or over three probes, describes air that
    never existed. Weighted by what each part of the surface actually carries,
    it describes the air the next component receives, which is the number the
    rest of the loop is built on (ADR-078).

    The range beside it is not error. It is the finding: intakes within a
    kelvin of each other say the containment is holding; an aisle exit spanning
    twelve says the row is half empty, which is true and worth reporting.
    """
    from aicfd.model import stations

    step = Path(step)
    racks = rack_temperatures(model, grid)
    area = sum(fan.area for fan in model.fans) or None
    flow_m3h = round(sum(f["supply_kg_s"] for f in fans) / _density(model) * 3600, 0)

    supplies = [f["supply_temp_c"] for f in fans if f.get("supply_temp_c") is not None]
    returns = [f["return_temp_c"] for f in fans if f.get("return_temp_c") is not None]
    inlets = [r["inlet_c"] for r in racks]

    # Rack intake is weighted by each cabinet's own airflow, which is its
    # resistance and not its load: a blanked cabinet breathes (ADR-054) and the
    # air it passes is part of what the row draws.
    grille_area = sum(
        panel.area for panel in model.panels if panel.name.startswith("grille")
    )
    weights = [rack.resistance_airflow_m3s for rack in model.racks] or [1.0]
    drawn = sum(weights) or 1.0
    exit_low, exit_high = _aisle_exit_range(model, grid)

    reading = {
        "supply": {
            "temp_c": supply_temperature(step, model.supply_temp_c),
            "low_c": round(min(supplies), 2) if supplies else None,
            "high_c": round(max(supplies), 2) if supplies else None,
            "flow_m3h": flow_m3h,
            "speed_ms": round(flow_m3h / 3600 / area, 3) if area else None,
        },
        "rack_intake": {
            # Weighted by each cabinet's own airflow, which is its resistance
            # and not its load: a blanked cabinet breathes (ADR-054) and the
            # air it passes is part of what the row draws.
            "temp_c": round(
                sum(t * w for t, w in zip(inlets, weights)) / drawn, 2
            ) if inlets else None,
            "low_c": round(min(inlets), 2) if inlets else None,
            "high_c": round(max(inlets), 2) if inlets else None,
            # The row passes what the loop passes. The cabinets' RATED flow is
            # a different number -- it sizes their resistance and, on a row
            # bought for more load than it carries, it is far larger. Reporting
            # it here would say the row moves twice what the units deliver.
            "flow_m3h": flow_m3h,
            "speed_ms": round(
                flow_m3h / 3600 / sum(rack.face_area for rack in model.racks), 3
            ) if model.racks else None,
        },
        "aisle_exit": {
            "temp_c": aisle_exit_temperature(model, grid),
            "low_c": exit_low,
            "high_c": exit_high,
            "flow_m3h": flow_m3h,
            "speed_ms": round(model.airflow_m3s / grille_area, 3)
            if grille_area else None,
        },
        "unit_return": {
            "temp_c": round(return_temperature(step) - KELVIN, 2),
            "low_c": round(min(returns), 2) if returns else None,
            "high_c": round(max(returns), 2) if returns else None,
            "flow_m3h": round(
                sum(f["intake_kg_s"] for f in fans) / _density(model) * 3600, 0
            ),
            "speed_ms": round(flow_m3h / 3600 / area, 3) if area else None,
        },
    }
    return [
        {"name": s.name, "label": s.label, "note": s.note, **reading[s.name]}
        for s in stations(model)
    ]


def _aisle_exit_range(model: Model, grid: dict) -> tuple[float | None, float | None]:
    """The coldest and warmest air actually leaving through the ceiling.

    Only cells that carry flow upward count. A cell under solid ceiling can sit
    at any temperature it likes; it is not leaving, so it is not in the range.
    """
    if not model.hot_aisles:
        return None, None
    x, y, z = grid["x"], grid["y"], grid["z"]
    layer = int(np.argmin(abs(z - (model.ceiling_z - model.cell_size[2] / 2))))
    aisle = np.concatenate(
        [np.where((y >= lo) & (y <= hi))[0] for lo, hi in model.hot_aisles]
    )
    inside = np.where((x >= model.hall.lo[0]) & (x <= model.hall.hi[0]))[0]
    if aisle.size == 0 or inside.size == 0:
        return None, None
    picks = np.ix_(aisle, inside)
    leaving = grid["U"][2][layer][picks] > 0
    if not leaving.any():
        return None, None
    values = grid["T"][layer][picks][leaving]
    return round(float(values.min()), 2), round(float(values.max()), 2)


def measure(model: Model, step: str | Path, iteration: int) -> dict:
    """One reading: every instrumented place, plus the two balances.

    The stations come from the streams that cross them; the balances come from
    the patch values, because what crosses a patch is phi on that patch.
    """
    from aicfd.model import stations

    step = Path(step)
    grid = read_grid(model, step)
    flows = patch_flows(step)
    fans = fan_flows(step, flows, model.supply_temp_c)
    supply = sum(f["supply_kg_s"] for f in fans)
    intake = sum(f["intake_kg_s"] for f in fans)
    recovered = recovered_load_w(step, model)

    readings = station_readings(model, step, grid, fans)

    return {
        "iteration": iteration,
        "stations": readings,
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
    """The recorded history, shaped for a chart: one series per station.

    Every written time is kept, so the chart shows the loop filling rather than
    only where it ended up: that partial record is the one thing a solve can
    show an engineer while it is still running.
    """
    from aicfd.model import stations

    history = sample(model, case_dir)
    if not history:
        return {"iterations": [], "groups": [], "balance": []}

    series = []
    for station in stations(model):
        readings = [
            next((s for s in row["stations"] if s["name"] == station.name), None)
            for row in history
        ]
        series.append(
            {
                "name": station.name,
                "label": station.label,
                "note": station.note,
                "temp_c": [r["temp_c"] if r else None for r in readings],
                "low_c": [r["low_c"] if r else None for r in readings],
                "high_c": [r["high_c"] if r else None for r in readings],
                "flow_m3h": [r["flow_m3h"] if r else None for r in readings],
                "speed_ms": [r["speed_ms"] if r else None for r in readings],
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

    readings = []
    for place in ends[0]["stations"]:
        other = next(
            (p for p in ends[1]["stations"] if p["name"] == place["name"]), None
        )
        if other is None or place["temp_c"] is None or other["temp_c"] is None:
            continue
        readings.append(
            {
                "name": place["name"],
                "label": place["label"],
                "first_c": place["temp_c"],
                "second_c": other["temp_c"],
                "gap_k": round(abs(place["temp_c"] - other["temp_c"]), 3),
            }
        )
    worst = max((p["gap_k"] for p in readings), default=None)
    return {
        "iterations": [ends[0]["iteration"], ends[1]["iteration"]],
        "stations": readings,
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
    """The two ends of the return path, and the range the aisle air spans.

    The ends are mixing-cup means of the whole stream and are what the check
    judges; the range beside them is the room's own unevenness, which is
    information and not a fault (ADR-078).
    """
    leaving, arriving = kpis.get("aisle_exit_c"), kpis.get("return_temp_c")
    if leaving is None or arriving is None:
        return "- (no samples yet)"
    station = next(
        (s for s in kpis.get("stations_now", []) if s["name"] == "aisle_exit"), None
    )
    low = station and station.get("low_c")
    high = station and station.get("high_c")
    span = f" (aisle {low:.1f}-{high:.1f})" if low is not None and high is not None else ""
    return (
        f"{leaving:.1f} -> {arriving:.1f} degC, "
        f"{abs(arriving - leaving):.2f} K apart{span}"
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
    line += _coil_provenance(kpis)
    if over:
        line += f". {over} unit(s) are above their own coil's capacity"
    water, design = (kpis.get("coil_water_out_c"),
                     kpis.get("coil_water_out_design_c"))
    if water and design and water > design + 0.5:
        line += (
            f"; that would take the water out at {water:.1f} degC against the "
            f"{design:g} degC of the selection"
        )
    return line + "."


def _drift_line(kpis: dict) -> str:
    moved = kpis.get("drift_k")
    if moved is None:
        return "- (only one sample so far)"
    return f"{moved:.2f} K since the previous sample"


def report(results: PodResults) -> str:
    k = results.kpis
    # A downflow unit is not a fan wall and the report should not call it one:
    # the reader is checking a drawing against this, and the word is how they
    # know which drawing (ADR-076).
    unit = "Room unit" if k.get("floor_height") else "Fan wall"
    lines = [
        f"Case '{results.case_name}' at iteration {results.time}",
        "",
        f"  {unit:<15} {k['supply_m3h']:,.0f} m3/h "
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
        f"  {unit + ' rise':<15} {_fan_rise_line(k)}",
        f"  Rack row        {_resistance_line(k, results)}",
        f"  Return grilles  "
        + (
            f"{k['grille_drop_pa']:.2f} Pa in the field, {k['grille_drop_asked_pa']:.2f} Pa from K"
            if k.get("grille_drop_pa") is not None
            else "open holes (no free area given)"
        ),
        *(
            [f"  Floor plates    {k['floor_drop_pa']:.2f} Pa in the field, "
             f"{k['floor_drop_asked_pa']:.2f} Pa from K"
             + (f", {k['floor_face_velocity_ms']:.2f} m/s on the face"
                if k.get("floor_face_velocity_ms") is not None else "")]
            if k.get("floor_drop_pa") is not None else []
        ),
        *(
            [f"  HVAC sizing     {line}" for line in k.get("hvac_lines", [])]
        ),
        "",
        "  Station              Temp        Range        Flow     Speed",
    ]
    for station in k.get("stations_now", []):
        temp, low, high = station["temp_c"], station["low_c"], station["high_c"]
        flow, speed = station.get("flow_m3h"), station.get("speed_ms")
        lines.append(
            f"  {station['label']:<20}"
            + (f"{temp:>6.1f}" if temp is not None else f"{'-':>6}")
            + (f"{low:>9.1f}-{high:<6.1f}"
               if low is not None and high is not None else f"{'-':>16}")
            + (f"{flow:>10,.0f}" if flow is not None else f"{'-':>10}")
            + (f"{speed:>10.2f}" if speed is not None else f"{'-':>10}")
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
    from aicfd.model import stations

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
                    # Whose room this cabinet is in. A hall with a customer
                    # cage is two rooms, and every table that totals a load
                    # has to be able to say which (ADR-102).
                    **({"in_cage": rack.id in set(model.cage_racks)}
                       if model.cage_racks else {}),
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
                    # Where a downflow unit's return face is, so the report's
                    # sections can draw the machine and not just its footprint
                    # (ADR-111).
                    "return_z": panel.return_z,
                }
                for panel in model.panels
            ],
            "stations": [
                {"name": s.name, "label": s.label, "note": s.note}
                for s in stations(model)
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
        "aisle_exit_c": k.get("aisle_exit_c"),
        "stations": k.get("stations_now", []),
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
        "coil_model": k.get("coil_model"),
        "coil_saturated_units": k.get("coil_saturated_units"),
        "coil_supply_setpoint_c": k.get("coil_supply_setpoint_c"),
        "coil_supply_needed_c": k.get("coil_supply_needed_c"),
        "coil_return_span_c": k.get("coil_return_span_c"),
        "coil_problem": k.get("coil_problem"),
        "coil_water_out_c": k.get("coil_water_out_c"),
        "coil_water_out_design_c": k.get("coil_water_out_design_c"),
        "coil_air_share_pct": k.get("coil_air_share_pct"),
        # Which units returned air the selections do not cover. Carried because
        # it is the reason a capacity is missing, and a missing number without
        # its reason reads as an oversight rather than a refusal (ADR-036).
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
