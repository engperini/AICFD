"""The coil model: what a fan wall does at a return temperature nobody selected.

A manufacturer issues *selections*: the machine at a handful of stated
conditions. A room does not run at any of them. The question every result
turns on -- how much heat does this unit move, and at what supply air
temperature, when the air reaching it is not the air it was selected for --
therefore cannot be answered by reading a table. It needs a model of the
machine (ADR-039).

**A chilled-water coil is a counterflow heat exchanger.** Its capacity is not
a property of the machine; it is

    Q = epsilon * C_air * (T_return - T_water_in)

where `epsilon` depends only on the two flows and the coil's UA, never on the
temperatures. Everything here follows from that one identity. Holding a
catalogue capacity fixed while the return temperature moves asserts that a
coil transfers the same heat across a bigger temperature difference, which no
heat exchanger does.

**The model is fitted from the selections themselves.** Nothing extra has to
be asked of the manufacturer, which matters because the selections are
usually all anyone gets. The fit reads a set of selections and recovers UA;
from UA it can then answer at any condition.

**One inference makes it work, and it is worth stating plainly.** In a set of
selections at a fixed air flow the effectiveness is *not* constant -- for the
worked CA80NPVG6 it climbs from 0,765 at 35 degC return to 0,839 at 41 degC.
The effectiveness of a heat exchanger with both flows fixed cannot change. So
a flow changed, and since the air flow is stated fixed it must be the water:
the selection program re-sizes the water flow at every row to hold the stated
entering and leaving water temperatures. Reading the table that way recovers
a UA that rises 5 % for 45 % more water -- textbook for a finned coil, whose
water-side film coefficient goes as the flow to the 0,8 -- and the fitted
split of the resistance comes out 78 % air, 22 % water, which is what a
chilled-water coil is.

The reading is testable, and it was tested: fitted on seven selections at one
air flow, the model predicts an eighth selection it never saw -- a different
air flow *and* a different return temperature -- to 0,03 K of supply air
temperature and 0,4 % of capacity.

**Counterflow, not crossflow.** The arrangement is geometrically crossflow,
but a deep multi-row coil with counter-circuited tubes is thermally
counterflow, and the numbers agree: against the blind selection the
counterflow relation lands within 0,03 K where crossflow-unmixed misses by
0,06 K, and crossflow can only fit the selections by putting 99 % of the
resistance on the air side, which is not a coil.

**What the model still cannot know.** How much water the branch can actually
pass. The coil's own limit is taken as the largest flow the manufacturer
selected it at -- they issued that selection for this machine, so the machine
can take it -- but the pump, the balancing valve and the available differential
pressure are a piping decision this model has no sight of. Where that limit
matters, it is an input, not a guess.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from aicfd.model import CP_AIR, air_density, site_pressure

#: Water, near enough over the 6-15 degC a coil works across.
CP_WATER = 4180.0

#: How the two film coefficients scale with flow. Both are the textbook
#: exponents for forced convection -- turbulent inside the tubes, and a
#: staggered finned bank outside -- and both are held rather than fitted:
#: seven selections at one air flow cannot identify the air-side exponent,
#: and pretending otherwise would hide the assumption inside a number.
AIR_EXPONENT = 0.6
WATER_EXPONENT = 0.8

#: How far outside the fitted flows the model will go before it says so.
#: The blind validation held at 92 % of the fitted air flow; past this the
#: extrapolation is no longer backed by anything measured.
TRUSTED_FLOW_RANGE = (0.70, 1.15)


class CannotFit(ValueError):
    """The selections do not describe a coil this model can recover."""


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
    extrapolated: str | None
    """Why this condition is outside what the selections back, or None."""

    @property
    def of_ceiling_pct(self) -> float:
        return self.capacity_kw / self.ceiling_kw * 100 if self.ceiling_kw else 0.0


@dataclass(frozen=True)
class Coil:
    """A fan wall's cooling coil, recovered from its manufacturer selections."""

    water_c: float
    """Entering chilled water, the temperature every capacity is measured from."""
    water_rise_k: float
    k_air: float
    k_water: float
    """1/UA = k_air / C_air**0.6 + k_water / C_water**0.8, in kW/K."""
    water_max: float
    """The coil's own water-side limit, as a capacity rate in kW/K."""
    air_fitted: float
    """The air capacity rate the selections were taken at, in kW/K."""
    returns_fitted: tuple[float, ...]
    residual_k: float
    """RMS error of the fit against the selections, in K of supply air."""
    air_split: float
    """Fraction of the resistance on the air side, at the design selection."""

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

    # --- the machine under control -------------------------------------------

    def operate(self, return_c: float, air: float,
                setpoint_c: float | None = None) -> Operating:
        """What the unit does at this return, with the valve doing its job.

        A fan wall on supply-air control modulates its water valve to hold the
        setpoint, and holds it for as long as it has authority. The moment the
        valve is wide open the setpoint stops being a boundary condition and
        the supply temperature floats with the return -- which is the whole
        reason a fixed supply temperature is an assumption rather than a fact.

        With no setpoint the valve is taken wide open, which is the plant's
        worst case and its capacity ceiling.
        """
        coldest = self.supply(return_c, air, self.water_max)
        ceiling = air * (return_c - coldest)
        if setpoint_c is None or setpoint_c <= coldest:
            water, supply, saturated = self.water_max, coldest, setpoint_c is not None
        else:
            found = _solve(
                lambda w: self.supply(return_c, air, w) - setpoint_c,
                self.water_max * 1e-3, self.water_max,
            )
            if found is None:  # even a trickle overshoots: the room is colder
                water, supply, saturated = self.water_max * 1e-3, setpoint_c, False
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
            extrapolated=self.outside(return_c, air),
        )

    def outside(self, return_c: float, air: float) -> str | None:
        """Why this condition is beyond what the selections back, or None.

        Said rather than refused. A coil model extrapolates on physics where a
        table extrapolates on a straight line, so it may be used past the
        selections -- but a reader has to know when they are being shown one
        and when the other (ADR-039).
        """
        reasons = []
        low, high = min(self.returns_fitted), max(self.returns_fitted)
        # Phrased without this unit's own numbers, so fourteen units in the
        # same condition raise one finding rather than fourteen.
        if return_c < low - 1e-9:
            reasons.append(f"the return air is below the coldest selection ({low:.0f} degC)")
        elif return_c > high + 1e-9:
            reasons.append(f"the return air is above the warmest selection ({high:.0f} degC)")
        share = air / self.air_fitted
        if share < TRUSTED_FLOW_RANGE[0]:
            reasons.append("the air flow is well below the one the selections were taken at")
        elif share > TRUSTED_FLOW_RANGE[1]:
            reasons.append("the air flow is well above the one the selections were taken at")
        return "; ".join(reasons) or None


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
    """Recover the coil from an Equipment's selections.

    Raises CannotFit when the selections do not support it, which is not a
    failure: a unit described by a capacity table alone is still usable, it
    just cannot be asked what it does off the table.
    """
    selection = unit.selection or {}
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
    rows = list(unit.capacity)
    if len(rows) < 2:
        raise CannotFit(f"{unit.model} has too few selections to fit a coil")

    points = []
    for row in rows:
        ret, sup = float(row["return_c"]), float(row["supply_c"])
        if ret <= sup or ret <= water_in:
            raise CannotFit(
                f"{unit.model} selection at {ret} degC does not describe "
                f"cooling: supply {sup} degC, water {water_in} degC"
            )
        air = air_capacity_rate(float(row["airflow_m3h"]), ret, altitude)
        heat = air * (ret - sup)
        # The selections' own capacity must agree with their own temperatures
        # and airflow. Where it does not, the rows are not one consistent set
        # and no model fitted to them would mean anything.
        stated = float(row["nscc_kw"])
        if abs(heat - stated) > max(0.03 * stated, 5.0):
            raise CannotFit(
                f"{unit.model} selection at {ret} degC is not self-consistent: "
                f"{row['airflow_m3h']:,.0f} m3/h from {ret} to {sup} degC "
                f"carries {heat:,.0f} kW, but the row says {stated:,.0f} kW"
            )
        # THE INFERENCE: the selection program held the water temperatures and
        # re-sized the flow. See this module's docstring for why it is the only
        # reading the numbers support.
        water = heat / rise
        points.append((air, water, (ret - sup) / (ret - water_in), ret))

    air0, water0, eps0, _ = points[0]
    ua0 = _solve(
        lambda ua: effectiveness(ua / min(air0, water0),
                                 min(air0, water0) / max(air0, water0))
        * min(air0, water0) / air0 - eps0,
        0.5, 20000.0,
    )
    if ua0 is None:
        raise CannotFit(f"{unit.model}: no conductance reproduces its first selection")

    # One free parameter: how the resistance divides between the two sides at
    # the design selection. Everything else is fixed by the exponents above.
    best = None
    for step in range(5, 200):
        split = step / 200
        k_air = split / ua0 * air0**AIR_EXPONENT
        k_water = (1 - split) / ua0 * water0**WATER_EXPONENT
        error = 0.0
        for air, water, eps, ret in points:
            ua = 1.0 / (k_air / air**AIR_EXPONENT + k_water / water**WATER_EXPONENT)
            low, high = min(air, water), max(air, water)
            predicted = effectiveness(ua / low, low / high) * low / air
            error += ((predicted - eps) * (ret - water_in)) ** 2  # weigh in K
        if best is None or error < best[0]:
            best = (error, split, k_air, k_water)
    error, split, k_air, k_water = best
    return Coil(
        water_c=float(water_in),
        water_rise_k=rise,
        k_air=k_air,
        k_water=k_water,
        water_max=max(p[1] for p in points),
        air_fitted=sum(p[0] for p in points) / len(points),
        returns_fitted=tuple(p[3] for p in points),
        residual_k=math.sqrt(error / len(points)),
        air_split=split,
    )
