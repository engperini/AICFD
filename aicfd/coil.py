"""The coil model: what a fan wall does at any condition the room presents.

A manufacturer issues a *selection*: the machine at one stated duty. A room
runs at conditions of its own, and the question every result turns on -- how
much heat does this unit move, and at what supply air temperature, given the
air reaching it -- is answered by modelling the machine (ADR-039).

**A chilled-water coil is a counterflow heat exchanger.** What it transfers is

    Q = epsilon * C_air * (T_return - T_water_in)

where `epsilon` follows from the coil's conductance and the two flows.
Everything here follows from that identity.

**The model is recovered from the design selection.** Return and supply air,
airflow, net sensible capacity and the water the unit was selected at are
enough: they fix the effectiveness at that point, and the effectiveness fixes
the conductance. The unit then answers anywhere.

**The water flow behind a selection is inferred from its water temperatures.**
A selection states the entering and leaving water and sizes the flow to them,
so the flow is the capacity over the temperature rise. Read that way the
recovered conductance splits about 78 % air and 22 % water, which is what a
finned chilled-water coil is.

**Reference selections refine and check the fit.** A unit carrying more of the
manufacturer's selections uses them to set how the resistance divides between
the two sides, and the model then reproduces them to a stated error. A unit
carrying only its design selection uses DEFAULT_AIR_SPLIT, or its own
`coil.air_split` where the manufacturer states it.

**Counterflow, not crossflow.** The arrangement is geometrically crossflow,
and a deep multi-row coil with counter-circuited tubes is thermally
counterflow. Against a selection held out of the fit the counterflow relation
lands within 0,03 K where crossflow-unmixed misses by 0,06 K, and crossflow
fits a set of selections only by putting 99 % of the resistance on the air
side.

**The water limit is the design selection's own flow**, which is what the
pump, the balancing valve and the pipe were sized for. Whether the installed
hydraulics deliver it is a piping question; where it matters, it is an input.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from aicfd.model import CP_AIR, air_density, site_pressure

#: Water, near enough over the 6-15 degC a coil works across.
CP_WATER = 4180.0

#: How the two film coefficients scale with flow. The textbook exponents for
#: forced convection -- turbulent inside the tubes, a staggered finned bank
#: outside -- held rather than fitted, so the assumption stays visible instead
#: of hiding inside a number.
AIR_EXPONENT = 0.6
WATER_EXPONENT = 0.8

#: How the resistance divides at the design point for a unit described by its
#: design selection alone. A chilled-water coil is air-side dominated; this is
#: the value a full set of manufacturer selections of one recovers. A unit
#: whose own split is known states it as `coil.air_split`.
DEFAULT_AIR_SPLIT = 0.78


class CannotFit(ValueError):
    """The selection does not describe a coil this model can recover."""


def effectiveness(ntu: float, ratio: float) -> float:
    """Counterflow effectiveness, referred to the smaller capacity rate."""
    if ntu <= 0:
        return 0.0
    if abs(1.0 - ratio) < 1e-9:
        return ntu / (1.0 + ntu)
    exponent = -ntu * (1.0 - ratio)
    # Guard the large-NTU limit, where exp() underflows and the answer is 1.
    if exponent < -50:
        return 1.0
    x = math.exp(exponent)
    return (1.0 - x) / (1.0 - ratio * x)


def _solve(f, low: float, high: float, steps: int = 200) -> float | None:
    """Bisection. Deliberately not Newton: the bracket is known and a
    derivative that goes wrong near the limits would fail silently."""
    f_low = f(low)
    if f_low * f(high) > 0:
        return None
    for _ in range(steps):
        mid = (low + high) / 2
        if f_low * f(mid) <= 0:
            high = mid
        else:
            low, f_low = mid, f(mid)
    return (low + high) / 2


@dataclass(frozen=True)
class Operating:
    """One coil at one condition: what it does, and how hard it is working."""

    return_c: float
    supply_c: float
    capacity_kw: float
    """The heat it moves here, at the supply temperature above."""
    ceiling_kw: float
    """The most it could move at this return, with the valve wide open."""
    effectiveness: float
    valve: float
    """Water flow as a fraction of the coil's limit. 1.0 means wide open."""
    saturated: bool
    """True when the valve is wide open and the supply is no longer held."""
    water_m3h: float

    @property
    def of_ceiling_pct(self) -> float:
        return self.capacity_kw / self.ceiling_kw * 100 if self.ceiling_kw else 0.0


@dataclass(frozen=True)
class Coil:
    """A fan wall's cooling coil, recovered from its design selection."""

    water_c: float
    """Entering chilled water, the temperature every capacity is measured from."""
    water_rise_k: float
    k_air: float
    k_water: float
    """1/UA = k_air / C_air**0.6 + k_water / C_water**0.8, in kW/K."""
    water_max: float
    """The coil's water-side limit, as a capacity rate in kW/K."""
    air_fitted: float
    """The air capacity rate of the design selection, in kW/K."""
    design_return_c: float
    air_split: float
    """Fraction of the resistance on the air side, at the design selection."""
    reference_returns: tuple[float, ...] = ()
    """Return temperatures of any further manufacturer selections the unit
    carries. They set the split and then check the fit."""
    reference_error_k: float | None = None
    """RMS error against those reference selections, in K of supply air."""
    rejected_references: tuple[str, ...] = ()
    """Reference rows whose own numbers do not agree with each other. They
    take no part in the fit, and saying which is the point of keeping them."""

    # --- the machine ----------------------------------------------------------

    def ua(self, air: float, water: float) -> float:
        """Conductance at these two capacity rates, in kW/K."""
        return 1.0 / (self.k_air / air**AIR_EXPONENT
                      + self.k_water / water**WATER_EXPONENT)

    def epsilon(self, air: float, water: float) -> float:
        """Effectiveness referred to the AIR, which is the side we measure."""
        low, high = min(air, water), max(air, water)
        return effectiveness(self.ua(air, water) / low, low / high) * low / air

    def supply(self, return_c: float, air: float, water: float) -> float:
        return return_c - self.epsilon(air, water) * (return_c - self.water_c)

    def operate_shared(self, seen_c: float, return_c: float, air: float,
                       setpoint_c: float | None = None) -> Operating:
        """What this unit does when its control reads ``seen_c``, not its own
        return.

        Fan walls on a network run to the worst condition any of them sees.
        What is shared is the reading, and what the reading buys is the valve:
        a unit whose own air is cool is told to open as far as the worst-case
        unit has to, and then delivers what ITS coil gives at ITS own return.
        That is how a unit far from the load stops idling at the setpoint and
        starts taking a share of it (ADR-064).

        Reported against the air this unit really receives -- the capacity is
        `air x (own return - what it delivers)`. Working it out on the shared
        return would quote heat the unit never moved.

        A chilled-water coil still has no heating mode (ADR-062): told to open
        against air colder than its water, it delivers its own return and
        moves nothing. A real unit's own supply sensor shuts the valve there.
        """
        if seen_c <= return_c:
            return self.operate(return_c, air, setpoint_c)
        ordered = self.operate(seen_c, air, setpoint_c)
        water = ordered.valve * self.water_max
        if water <= 0:
            return self.operate(return_c, air, setpoint_c)
        supply = min(self.supply(return_c, air, water), return_c)
        coldest = self.supply(return_c, air, self.water_max)
        return Operating(
            return_c=return_c,
            supply_c=supply,
            capacity_kw=air * (return_c - supply),
            ceiling_kw=max(0.0, air * (return_c - coldest)),
            effectiveness=self.epsilon(air, water),
            valve=ordered.valve,
            saturated=ordered.saturated,
            water_m3h=ordered.water_m3h,
        )

    def leaving_water_c(self, capacity_kw: float) -> float:
        """What the water leaves at, carrying this heat at the design flow.

        The other half of the heat balance, and the one that says whether a
        capacity is reachable by the plant rather than only by the coil. A
        unit at a return far past its selection transfers far more than the
        catalogue figure -- that is the heat exchanger and not a mistake --
        but it asks the water to leave much warmer, and the chiller, the pump
        and the valve were sized for the selection's rise (ADR-063).
        """
        rate = self.water_max / 4.18 * 3.6 * 1000 / 3600 * CP_WATER / 1000
        return self.water_c + capacity_kw / rate if rate else self.water_c

    # --- the machine under control -------------------------------------------

    def operate(self, return_c: float, air: float,
                setpoint_c: float | None = None) -> Operating:
        """What the unit does at this return, with the valve doing its job.

        A fan wall on supply-air control modulates its water valve to hold the
        setpoint for as long as it has authority. Once the valve is wide open
        the supply air follows the return, which is why a supply temperature
        is solved rather than imposed.

        With no setpoint the valve is taken wide open: the plant's capacity
        ceiling at this condition.
        """
        coldest = self.supply(return_c, air, self.water_max)
        # What it could REMOVE, which is nothing when the water is no colder
        # than the air. A negative ceiling is a coil asked to heat.
        ceiling = max(0.0, air * (return_c - coldest))
        if setpoint_c is not None and return_c <= setpoint_c:
            # Air already at or below what the unit is asked to deliver. The
            # valve shuts and the unit ventilates: supply is the return, and
            # it moves no heat.
            #
            # It used to pin the supply at the setpoint here, which claimed a
            # chilled-water coil could WARM the air -- 15 degC in, 21,9 out,
            # and a capacity of minus 214 kW. Worse than a wrong number in a
            # report: that supply is written back onto the fan patch, so the
            # unit went on to inject heat into the room it was cooling. A
            # coil has no heating mode, and the case that reaches this is the
            # ordinary one of a zone that has been over-cooled (ADR-062).
            return Operating(
                return_c=return_c,
                supply_c=return_c,
                capacity_kw=0.0,
                ceiling_kw=ceiling,
                effectiveness=0.0,
                valve=0.0,
                saturated=False,
                water_m3h=0.0,
            )
        if setpoint_c is None or setpoint_c <= coldest:
            water, supply, saturated = self.water_max, coldest, setpoint_c is not None
        else:
            found = _solve(
                lambda w: self.supply(return_c, air, w) - setpoint_c,
                self.water_max * 1e-3, self.water_max,
            )
            if found is None:
                # A trickle already overshoots: the smallest flow the valve
                # can pass takes the air past the setpoint. It delivers what
                # that trickle gives, not the setpoint it cannot hold from
                # above.
                water = self.water_max * 1e-3
                supply, saturated = self.supply(return_c, air, water), False
            else:
                water, supply, saturated = found, setpoint_c, False
        return Operating(
            return_c=return_c,
            supply_c=supply,
            capacity_kw=air * (return_c - supply),
            ceiling_kw=ceiling,
            effectiveness=self.epsilon(air, water),
            valve=water / self.water_max,
            saturated=saturated,
            water_m3h=water / CP_WATER * 1000 * 3600 / 1000,
        )


def air_capacity_rate(airflow_m3h: float, temp_c: float,
                      altitude_m: float) -> float:
    """The air's capacity rate in kW/K, at the density it really has.

    Not a detail: the same volume at 750 m carries 9 % less mass than at the
    coast, and every capacity here is a mass flow times a temperature
    difference.
    """
    rho = air_density(site_pressure(altitude_m), temp_c)
    return airflow_m3h / 3600 * rho * CP_AIR / 1000


def fit(unit) -> Coil:
    """Recover the coil from a unit's design selection.

    One selection describes the machine: its return and supply air, its
    airflow, its net sensible capacity and the water it was selected at. From
    those the conductance follows, and from the conductance the unit answers
    at any condition.

    Where the unit also carries reference selections, they set how the
    resistance divides between the two sides and then serve as a check on the
    fit. Where it carries none, `DEFAULT_AIR_SPLIT` applies and the unit's own
    `coil.air_split` overrides it.

    Raises CannotFit when the selection does not describe a coil, which leaves
    the unit usable and simply not askable off its design point.
    """
    selection = unit.selection or {}
    design = unit.design or {}
    water_in = selection.get("entering_water_c")
    water_out = selection.get("leaving_water_c")
    if water_in is None or water_out is None:
        raise CannotFit(
            f"{unit.model} does not say what water it was selected at "
            f"(entering_water_c and leaving_water_c), so its capacity cannot "
            f"be separated from its conditions"
        )
    rise = float(water_out) - float(water_in)
    if rise <= 0:
        raise CannotFit(f"{unit.model} leaves the water no warmer than it enters")
    altitude = float(selection.get("elevation_m") or 0.0)
    missing = [k for k in ("return_c", "supply_c", "airflow_m3h", "nscc_kw")
               if design.get(k) is None]
    if missing:
        raise CannotFit(
            f"{unit.model} has no design selection to characterise it: "
            f"design.{', design.'.join(missing)} missing"
        )

    anchor = _point(unit, design, water_in, rise, altitude)
    air0, water0, eps0, _ = anchor
    ua0 = _solve(
        lambda ua: effectiveness(ua / min(air0, water0),
                                 min(air0, water0) / max(air0, water0))
        * min(air0, water0) / air0 - eps0,
        0.5, 20000.0,
    )
    if ua0 is None:
        raise CannotFit(
            f"{unit.model}: no conductance reproduces its design selection"
        )

    # A reference row that does not close is a bad row, not a bad unit. It is
    # optional evidence (ADR-039), so it is set aside and named rather than
    # blocking the coil the design selection already describes (ADR-067).
    reference, rejected = [], []
    for row in unit.capacity:
        try:
            reference.append(_point(unit, row, water_in, rise, altitude))
        except CannotFit as why:
            rejected.append(str(why).replace("selection at", "reference row at", 1))
    stated = (unit.coil_split if getattr(unit, "coil_split", None) else None)
    if stated is not None:
        split = float(stated)
    elif len(reference) >= 2:
        # The one free parameter, and the only thing extra selections buy:
        # how the resistance divides at the design point. Everything else is
        # fixed by the exponents above.
        split = min(
            (i / 200 for i in range(5, 200)),
            key=lambda s: _misfit(s, ua0, air0, water0, reference, water_in),
        )
    else:
        split = DEFAULT_AIR_SPLIT
    k_air = split / ua0 * air0**AIR_EXPONENT
    k_water = (1 - split) / ua0 * water0**WATER_EXPONENT

    fitted = Coil(
        water_c=float(water_in),
        water_rise_k=rise,
        k_air=k_air,
        k_water=k_water,
        # The water the plant was bought to circulate to this unit: the branch,
        # the valve and the pump were sized on the design selection.
        water_max=water0,
        air_fitted=air0,
        design_return_c=float(design["return_c"]),
        air_split=split,
        reference_returns=tuple(p[3] for p in reference),
        rejected_references=tuple(rejected),
    )
    if len(reference) < 2:
        return fitted
    return replace(fitted, reference_error_k=math.sqrt(
        _misfit(split, ua0, air0, water0, reference, water_in) / len(reference)
    ))


def _point(unit, row: dict, water_in: float, rise: float, altitude: float):
    """One selection as (air capacity rate, water capacity rate, effectiveness,
    return temperature), with its own numbers checked against each other."""
    ret, sup = float(row["return_c"]), float(row["supply_c"])
    if ret <= sup or ret <= water_in:
        raise CannotFit(
            f"{unit.model} selection at {ret} degC does not describe cooling: "
            f"supply {sup} degC, water {water_in} degC"
        )
    air = air_capacity_rate(float(row["airflow_m3h"]), ret, altitude)
    heat = air * (ret - sup)
    # A selection's capacity must agree with its own temperatures and airflow.
    # Where it does not, no model fitted to it would mean anything.
    stated = float(row["nscc_kw"])
    if abs(heat - stated) > max(0.03 * stated, 5.0):
        raise CannotFit(
            f"{unit.model} selection at {ret} degC is not self-consistent: "
            f"{row['airflow_m3h']:,.0f} m3/h from {ret} to {sup} degC carries "
            f"{heat:,.0f} kW, but the selection says {stated:,.0f} kW"
            + _what_would_close(row, ret, sup, stated, altitude)
        )
    # THE INFERENCE: the selection holds the water temperatures and sizes the
    # flow to them. See this module's docstring.
    return air, heat / rise, (ret - sup) / (ret - water_in), ret


def _what_would_close(row, ret: float, sup: float, stated: float,
                      altitude: float) -> str:
    """The numbers that would make this selection agree with itself.

    Three candidates, and the first is not a guess. A CWA datasheet prints
    `Gross Sensible Cooling Capacity` directly above `NSCC`, and they differ by
    exactly the fan power the array returns to the air -- which this row
    already carries. Where the stated figure less that power is what the
    temperatures and airflow do carry, the gross figure was copied, and the
    arithmetic says so rather than supposing it (ADR-068).

    Otherwise: the site elevation, and the supply air at the stated elevation.
    Both, unranked. Which of them is the transcription is the engineer's to
    know -- the same miss reads as a copied elevation on one unit and a
    rounded supply on the next -- and guessing would send half the readers to
    the wrong field with the tool's confidence behind it.
    """
    air = air_capacity_rate(float(row["airflow_m3h"]), ret, altitude)
    heat = air * (ret - sup)
    power = row.get("power_kw")
    if power and abs(heat - (stated - float(power))) <= max(0.03 * stated, 5.0):
        return (f". {stated:,.1f} less this row's {float(power):,.1f} kW of fan "
                f"power is {stated - float(power):,.1f} kW, which is what these "
                f"temperatures and airflow do carry: the GROSS figure was "
                f"taken. Every capacity here is net sensible (NSCC)")
    wants = f"{stated:,.0f} kW wants a supply of {ret - stated / air:.1f} degC"
    found = _solve(
        lambda alt: air_capacity_rate(float(row["airflow_m3h"]), ret, alt)
        * (ret - sup) - stated,
        0.0, 5000.0,
    )
    if found is None:
        return f". At {altitude:,.0f} m, {wants}"
    return (f". Two things would close it: a site elevation of {found:,.0f} m "
            f"(this unit says {altitude:,.0f} m -- that field is on the "
            f"`Selected at` card), or, at {altitude:,.0f} m, {wants}")


def _misfit(split, ua0, air0, water0, points, water_in) -> float:
    """Squared error of a candidate split against the reference selections,
    weighed in kelvin of supply air."""
    k_air = split / ua0 * air0**AIR_EXPONENT
    k_water = (1 - split) / ua0 * water0**WATER_EXPONENT
    total = 0.0
    for air, water, eps, ret in points:
        ua = 1.0 / (k_air / air**AIR_EXPONENT + k_water / water**WATER_EXPONENT)
        low, high = min(air, water), max(air, water)
        total += ((effectiveness(ua / low, low / high) * low / air - eps)
                  * (ret - water_in)) ** 2
    return total
