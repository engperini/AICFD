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
    fan_power_kw: float = 0.0
    """What the array returns to the air, from the design selection. The water
    carries this on top of the heat the air loses."""
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
            # SATURATED IS ABOUT THIS UNIT, not about the network. A unit told
            # to open for a worse-placed peer delivers air COLDER than the
            # setpoint, and reporting it as one that cannot hold the setpoint
            # made every unit of a team read as failing -- and put "every
            # temperature in this result is optimistic" under a run where
            # they were pessimistic (ADR-064, ADR-103).
            saturated=ordered.saturated and (
                setpoint_c is not None and supply > setpoint_c + 1e-6),
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
        if not rate:
            return self.water_c
        # The water carries what the air lost PLUS what the fans put back.
        return self.water_c + (capacity_kw + self.fan_power_kw) / rate

    # --- what a report says about it ------------------------------------------

    def describe(self) -> dict:
        """The coil's own properties, for the KPIs and the report.

        Both kinds of coil answer this, so nothing downstream has to know
        which one it is holding -- it reads `kind` where the difference
        matters and the shared fields where it does not (ADR-103).
        """
        return {
            "kind": "chilled_water",
            "water_c": self.water_c,
            "design_return_c": self.design_return_c,
            "air_split_pct": round(self.air_split * 100),
            "water_max_m3h": round(self.water_max / 4.18 * 3.6, 1),
            "reference_selections": len(self.reference_returns),
            "reference_error_k": (round(self.reference_error_k, 3)
                                  if self.reference_error_k is not None else None),
            "duty_label": "water valve",
        }

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
    # A selection that states its water flow is measured where this otherwise
    # infers. The inference divides the NET capacity by the water's rise, but
    # the water carries the GROSS duty -- the fan power is added back to the
    # air downstream of the coil -- so it under-reads by that fraction, about
    # 4 to 6% on the sheets in hand. Where the flow is given it is used, and
    # the coil's water-side limit is the plant's real one (ADR-069).
    stated_flow = (selection or {}).get("water_flow_lh")
    if stated_flow:
        water0 = float(stated_flow) / 3600.0 * CP_WATER / 1000.0
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
        fan_power_kw=float(design.get("power_kw") or 0.0),
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
    #
    # The duty the WATER carries is the gross one. `heat` is the net effect on
    # the air across the whole unit, and the fan array adds its power back to
    # that air downstream of the coil, so the coil hands the water the sum of
    # the two. Dividing the net by the rise under-read the flow by the fan
    # power's share -- 4 to 6% on the sheets in hand -- and the Vertiv CA40,
    # which prints its flow, settles it: 402.76 l/min is 28.06 kW/K, and
    # gross/rise is 28.07 (ADR-069).
    gross = heat + float(row.get("power_kw") or 0.0)
    return air, gross / rise, (ret - sup) / (ret - water_in), ret


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
    # It has to EXPLAIN the gap, not merely land inside the same tolerance.
    # On a unit whose fan power is a few percent of its capacity, "within 3%
    # of the stated figure" is satisfied by almost any near miss, and the
    # branch then reports a confident, wrong diagnosis -- which it did, on a
    # sheet whose net figure was right and whose airflow was not. What
    # qualifies is a residual that is small in its own right AND much smaller
    # than the gap it accounts for.
    before = abs(heat - stated)
    after = abs(heat - (stated - float(power or 0.0))) if power else before
    if power and after < 0.25 * before and after < 0.02 * stated:
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


# --- direct expansion ---------------------------------------------------------
#
# A DX unit's evaporator is the same heat exchanger with one side BOILING.
# Refrigerant changing phase holds its temperature, so the cold side's capacity
# rate is effectively infinite and the counterflow relation collapses to
#
#     epsilon = 1 - exp(-UA / C_air)
#
# measured from the coil's APPARATUS DEW POINT rather than from an entering
# water temperature -- the surface the air is dragged towards. That is the
# bypass-factor model every psychrometric text prints, written as epsilon-NTU
# so a DX unit and a chilled-water unit are answered by one piece of code
# (ADR-103).
#
# WHAT IS ASSUMED, said once. The ADP is held at the value the manufacturer's
# selection implies. A real circuit moves it: a fixed-capacity compressor
# against a lighter load drops its suction pressure, so the coil runs colder
# and gives a little more than this says, and a unit with staged or inverter
# compressors unloads instead. Both make the number here a conservative
# ceiling, which is the right direction for a capacity a plant is sized on.
# The condensing side is the selection's own outdoor air; capacity moves with
# that too, and one selection cannot say how much (ADR-103).


def saturation_pressure_pa(temp_c: float) -> float:
    """Saturation vapour pressure over water, in Pa (ASHRAE Fundamentals)."""
    t = temp_c + 273.15
    return math.exp(
        -5.8002206e3 / t + 1.3914993 - 4.8640239e-2 * t + 4.1764768e-5 * t**2
        - 1.4452093e-8 * t**3 + 6.5459673 * math.log(t)
    )


def humidity_ratio(temp_c: float, pressure_pa: float, *, rh_pct: float | None = None,
                   wetbulb_c: float | None = None) -> float:
    """Mass of water per mass of dry air, from relative humidity or wet bulb.

    Wet bulb is preferred where a selection states it: it is what a selection
    program works in, and the two disagree by a few per cent on the sheets in
    hand.
    """
    if wetbulb_c is not None:
        w_sat = 0.621945 * saturation_pressure_pa(wetbulb_c) / (
            pressure_pa - saturation_pressure_pa(wetbulb_c))
        return ((2501 - 2.326 * wetbulb_c) * w_sat
                - 1.006 * (temp_c - wetbulb_c)) / (
                    2501 + 1.86 * temp_c - 4.186 * wetbulb_c)
    if rh_pct is None:
        raise CannotFit("neither a wet bulb nor a relative humidity was given")
    pv = rh_pct / 100.0 * saturation_pressure_pa(temp_c)
    return 0.621945 * pv / (pressure_pa - pv)


def saturated_humidity_ratio(temp_c: float, pressure_pa: float) -> float:
    ps = saturation_pressure_pa(temp_c)
    return 0.621945 * ps / (pressure_pa - ps)


def dew_point_c(humidity: float, pressure_pa: float) -> float:
    """The temperature this air would have to reach to start condensing."""
    return _solve(
        lambda t: saturated_humidity_ratio(t, pressure_pa) - humidity,
        -40.0, 90.0,
    ) or -40.0


def apparatus_dew_point_c(t_in: float, w_in: float, t_out: float, w_out: float,
                          pressure_pa: float) -> float:
    """Where the coil's own process line meets saturation, in degC.

    The line from the air entering to the air leaving the COIL, extended. For
    a coil that removes almost no moisture -- which is what a precision unit
    selected on sensible heat is -- it is nearly horizontal, and it meets
    saturation just below the entering air's dew point. That is the physical
    statement being made: the surface is barely cold enough to wet.
    """
    if abs(t_out - t_in) < 1e-9:
        raise CannotFit("the selection leaves the air at the temperature it entered")
    slope = (w_out - w_in) / (t_out - t_in)
    found = _solve(
        lambda t: saturated_humidity_ratio(t, pressure_pa)
        - (w_in + slope * (t - t_in)),
        -30.0, t_out,
    )
    if found is None:
        raise CannotFit(
            "the selection's own air states do not meet saturation: no coil "
            "surface produces them"
        )
    return found


@dataclass(frozen=True)
class DXCoil:
    """A direct-expansion evaporator, recovered from its design selection.

    The same interface as `Coil`, because everything downstream -- the coupled
    solve, the per-unit capacities, the capacity figure -- should not have to
    know which kind of machine it is holding.
    """

    adp_c: float
    """Apparatus dew point: the surface temperature the air is dragged towards."""
    ua: float
    """Conductance at the fitted air flow, in kW/K."""
    air_fitted: float
    design_return_c: float
    fan_power_kw: float
    """What the fans hand back to the air, downstream of the coil."""
    rated_capacity_kw: float
    rated_ambient_c: float | None
    """The outdoor air the condenser was selected against. Capacity moves with
    it and one selection cannot say how much, so the result holds it there."""
    compressor_kw: float | None = None
    condenser_kw: float | None = None
    capacity_ceiling_kw: float | None = None
    """The refrigeration the COMPRESSORS can move at the condensing condition
    this unit was selected at, gross, before the fans put their power back.

    An evaporator's e-NTU answer grows without limit as the return warms: at
    30,6 degC of return this machine's coil asks for 114 kW where its own
    selection is 110,3 kW gross, and the report read it as a unit at 112 % of
    its plate. The coil really would transfer that; the compressors would not
    lift it, which is the limit every commercial tool applies from the
    manufacturer's capacity table. One selection point is one point on that
    table, so what is held is the total refrigeration at the ambient the
    selection names -- and the result says it is held there (ADR-118)."""
    refrigerant: str | None = None
    assumptions: tuple[str, ...] = ()
    """What the fit had to assume because the selection did not print it. Said
    in the report, beside the capacity it decides (ADR-071)."""

    # --- the machine ----------------------------------------------------------

    def epsilon(self, air: float) -> float:
        """Contact factor at this air capacity rate.

        The conductance follows the air flow with the same exponent the
        chilled-water coil uses -- it is the same finned bank, and only the
        air side moves.
        """
        ua = self.ua * (air / self.air_fitted) ** AIR_EXPONENT
        return effectiveness(ua / air, 0.0)

    def gross_kw(self, return_c: float, air: float, duty: float = 1.0) -> float:
        """What the coil transfers, before the fans, at ``duty`` of cooling.

        The evaporator's own answer, held at what the compressors can lift
        (ADR-118).
        """
        gross = self.epsilon(air) * air * (return_c - self.adp_c) * duty
        if self.capacity_ceiling_kw is not None:
            gross = min(gross, self.capacity_ceiling_kw)
        return gross

    def at_compressor_limit(self, return_c: float, air: float) -> bool:
        """Whether the coil is asking for more than the compressors can lift."""
        if self.capacity_ceiling_kw is None:
            return False
        asked = self.epsilon(air) * air * (return_c - self.adp_c)
        return asked > self.capacity_ceiling_kw + 1e-9

    def supply(self, return_c: float, air: float, duty: float = 1.0) -> float:
        """Air leaving the unit, fans included, at ``duty`` of full cooling."""
        gross = self.gross_kw(return_c, air, duty)
        return return_c - max(0.0, gross - self.fan_power_kw) / air

    def ceiling_kw(self, return_c: float, air: float) -> float:
        """Net sensible capacity with the compressors at full, at this return."""
        return max(0.0, self.gross_kw(return_c, air) - self.fan_power_kw)

    def leaving_water_c(self, capacity_kw: float) -> None:
        """There is no water. Said as None rather than left off the class, so
        a caller holding either kind of coil does not have to ask which."""
        return None

    # --- the machine under control -------------------------------------------

    def operate(self, return_c: float, air: float,
                setpoint_c: float | None = None) -> Operating:
        """What the unit does at this return, with the compressors doing their
        job: unloading, staging and cycling to hold the supply setpoint until
        there is nothing left to give.

        The chilled-water coil's valve and this are the same control question
        answered with different hardware, so the answer has the same shape --
        including a unit told to cool air already colder than its setpoint,
        which shuts its compressors off and ventilates (ADR-062).
        """
        ceiling = self.ceiling_kw(return_c, air)
        if setpoint_c is not None and return_c <= setpoint_c:
            return Operating(
                return_c=return_c, supply_c=return_c, capacity_kw=0.0,
                ceiling_kw=ceiling, effectiveness=0.0, valve=0.0,
                saturated=False, water_m3h=0.0,
            )
        coldest = return_c - ceiling / air
        if setpoint_c is None or setpoint_c <= coldest:
            supply, duty = coldest, 1.0
            saturated = setpoint_c is not None
        else:
            wanted = air * (return_c - setpoint_c)
            duty = min(1.0, (wanted + self.fan_power_kw)
                       / max(1e-9, ceiling + self.fan_power_kw))
            # `duty` is a fraction of the coil's own answer; where the
            # compressors are the limit, that answer was held below it and a
            # fraction of the held value asks for too little (ADR-118).
            if self.at_compressor_limit(return_c, air):
                asked = self.epsilon(air) * air * (return_c - self.adp_c)
                duty *= (self.capacity_ceiling_kw or asked) / max(asked, 1e-9)
            supply, saturated = setpoint_c, False
        return Operating(
            return_c=return_c,
            supply_c=supply,
            capacity_kw=air * (return_c - supply),
            ceiling_kw=ceiling,
            effectiveness=self.epsilon(air),
            # How much of its refrigeration the unit is using: the compressor's
            # analogue of a valve position, and read the same way.
            valve=max(0.0, min(1.0, duty)),
            saturated=saturated,
            water_m3h=0.0,
        )

    def operate_shared(self, seen_c: float, return_c: float, air: float,
                       setpoint_c: float | None = None) -> Operating:
        """What this unit does on a network holding ONE supply temperature.

        A DX ROOM UNIT IS CONTROLLED ON ITS SUPPLY AIR, and a network of them
        holds one setpoint between them: the plant delivers the coldest air
        its WORST-placed unit can still make, and every other unit holds that
        same temperature with its own compressors, unloading as far as it
        needs to. A unit with cool return does LESS work, not more.

        That is the difference from a chilled-water network, which shares the
        valve position instead (`Coil.operate_shared`, ADR-064) -- and it is
        the arrangement each kind of plant is actually built with: a CRAH
        array on a common water loop, a CRAC array on a common supply
        setpoint (ADR-117).

        Shared DUTY is what this used to do, and on a 1 MW hall it drove the
        well-placed units to 15,9 degC of supply against an 18,8 degC
        setpoint: they were told to run at the worst unit's compressor duty
        and had nothing to do with it but overcool their own air.
        """
        if seen_c <= return_c:
            return self.operate(return_c, air, setpoint_c)
        # What the worst-placed unit can hold -- the plant's common supply.
        ordered = self.operate(seen_c, air, setpoint_c)
        return self.operate(return_c, air, ordered.supply_c)

    # --- what a report says about it ------------------------------------------

    def describe(self) -> dict:
        """The coil's own properties, for the KPIs and the report."""
        return {
            "kind": "dx",
            "design_return_c": self.design_return_c,
            "adp_c": round(self.adp_c, 2),
            "contact_factor_pct": round(self.epsilon(self.air_fitted) * 100, 1),
            "rated_ambient_c": self.rated_ambient_c,
            "rated_capacity_kw": self.rated_capacity_kw,
            "compressor_kw": self.compressor_kw,
            "condenser_kw": self.condenser_kw,
            "capacity_ceiling_kw": self.capacity_ceiling_kw,
            "refrigerant": self.refrigerant,
            "assumptions": list(self.assumptions),
            "duty_label": "compressor duty",
        }


def fit_dx(unit) -> DXCoil:
    """Recover a direct-expansion evaporator from its design selection.

    What it needs is the selection's psychrometry: the air in (dry bulb and
    wet bulb, or relative humidity), the air off the coil, and how much of the
    duty was sensible. The last is what says how cold the surface is -- a
    selection that removes no moisture is telling you the coil barely reaches
    the dew point, and one that removes a lot is telling you it is far below.

    Raises CannotFit, naming the field, where the sheet does not carry it.
    """
    design = unit.design or {}
    selection = unit.selection or {}
    missing = [k for k in ("return_c", "supply_c", "airflow_m3h", "nscc_kw")
               if design.get(k) is None]
    if missing:
        raise CannotFit(
            f"{unit.model} has no design selection to characterise it: "
            f"design.{', design.'.join(missing)} missing"
        )
    humidity = design.get("return_wb_c"), design.get("return_rh_pct")
    if humidity == (None, None):
        humidity = None, selection.get("entering_air_rh")
    if humidity == (None, None):
        raise CannotFit(
            f"{unit.model} does not say how humid the air it was selected on "
            f"was (design.return_wb_c, or design.return_rh_pct), so the coil "
            f"surface its capacity implies cannot be found. A direct-expansion "
            f"unit's sensible capacity is a psychrometric statement"
        )
    assumed: list[str] = []
    # THE GROSS SENSIBLE DUTY: what the COIL does, before the fans put their
    # own power back into the air downstream of it. A sheet that prints it is
    # used; one that does not has said the same thing in two numbers, because
    # net is gross less the fan power it states.
    gross = design.get("gross_sensible_kw")
    if gross is None:
        gross = float(design["nscc_kw"]) + float(design.get("power_kw") or 0.0)
        assumed.append(
            "the coil's gross duty is the net sensible plus the stated fan "
            "power, because the selection prints only the net figure"
        )
    # THE LATENT DUTY, which is what says how far below the entering dew point
    # the surface runs. A precision unit selected on sensible heat is all but
    # dry -- on the one sheet here that prints both, the latent is 0,8% of the
    # total, and carrying it moves the apparatus dew point by 0,12 K and the
    # capacity by 0,6%. So a sheet that prints no total is taken as dry, and
    # the report says so (ADR-103).
    total = design.get("gross_total_kw")
    if total is None:
        total = gross
        assumed.append(
            "the coil is dry -- no moisture removed -- because the selection "
            "prints no total capacity beside its sensible one. On a sheet "
            "that prints both, that is worth 0,1 K of coil surface"
        )
    return_c = float(design["return_c"])
    altitude = float(selection.get("elevation_m") or 0.0)
    pressure = site_pressure(altitude)
    air = air_capacity_rate(float(design["airflow_m3h"]), return_c, altitude)
    mass = air / (CP_AIR / 1000)
    # What the fans hand back to the air: the selection's own difference
    # between gross and net, which is what it applied. The nameplate fan power
    # is the fallback, and the two differ by a kilowatt or so on the sheets in
    # hand -- motor heat the selection counted and the plate does not.
    fan_power = float(gross) - float(design["nscc_kw"])
    if fan_power <= 0:
        fan_power = float(design.get("power_kw") or 0.0)
    off_coil = return_c - float(gross) / air
    w_in = humidity_ratio(return_c, pressure,
                          wetbulb_c=humidity[0], rh_pct=humidity[1])
    latent = max(0.0, float(total) - float(gross))
    # Latent heat of vaporisation near a coil surface, kJ/kg.
    w_out = max(0.0, w_in - latent / (mass * 2450.0))
    adp = apparatus_dew_point_c(return_c, w_in, off_coil, w_out, pressure)
    if adp >= off_coil:
        raise CannotFit(
            f"{unit.model}: the selection's own numbers put the coil surface "
            f"at {adp:.1f} degC and the air leaving it at {off_coil:.1f} degC, "
            f"which no coil does"
        )
    epsilon = (return_c - off_coil) / (return_c - adp)
    return DXCoil(
        assumptions=tuple(assumed),
        adp_c=adp,
        ua=-math.log(1.0 - epsilon) * air,
        air_fitted=air,
        design_return_c=return_c,
        fan_power_kw=fan_power,
        rated_capacity_kw=float(design["nscc_kw"]),
        rated_ambient_c=(float(selection["outside_air_c"])
                         if selection.get("outside_air_c") is not None else None),
        compressor_kw=(float(design["compressor_kw"])
                       if design.get("compressor_kw") is not None else None),
        condenser_kw=(float(design["condenser_kw"])
                      if design.get("condenser_kw") is not None else None),
        # WHAT THE COMPRESSORS CAN LIFT at the condensing condition this
        # selection names: its own gross total refrigeration. A sheet that
        # prints no total says nothing about the limit, and the coil answers
        # unbounded as it did before (ADR-118).
        capacity_ceiling_kw=(float(design["gross_total_kw"])
                             if design.get("gross_total_kw") is not None
                             else None),
        refrigerant=selection.get("refrigerant"),
    )
