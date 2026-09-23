"""The direct-expansion coil: the same exchanger with one side boiling.

A DX unit used to be carried and never asked -- every case ran at its plate
figure, nothing coupled it to the room, and the report told the reader to ask
the manufacturer what the machine does at the return the room gives it
(ADR-073, ADR-097). It is fitted now, from the psychrometry of its own
selection (ADR-103), and what these check is the two things that makes true:
the fit reproduces the sheet, and the machine answers away from it.
"""

from __future__ import annotations

import unittest

from aicfd import coil as coil_module
from aicfd import equipment as library
from aicfd.coil import air_capacity_rate


class PsychrometryTest(unittest.TestCase):
    """The three relations the fit rests on, against published values."""

    def test_saturation_pressure_matches_the_steam_tables(self):
        for temp, pascals in ((0.0, 611.2), (10.0, 1228.0), (20.0, 2339.0),
                              (30.0, 4247.0), (40.0, 7385.0)):
            with self.subTest(temp=temp):
                self.assertAlmostEqual(
                    coil_module.saturation_pressure_pa(temp), pascals,
                    delta=0.002 * pascals)

    def test_wet_bulb_and_relative_humidity_agree_at_one_state(self):
        """30 degC dry bulb and 18 degC wet bulb IS about 30% RH, which is
        what the P3100DA's sheet prints on the same line."""
        pressure = 101325.0
        by_wb = coil_module.humidity_ratio(30.0, pressure, wetbulb_c=18.0)
        by_rh = coil_module.humidity_ratio(30.0, pressure, rh_pct=30.0)
        self.assertAlmostEqual(by_wb, by_rh, delta=0.0005)

    def test_the_dew_point_of_the_air_is_where_it_saturates(self):
        pressure = 101325.0
        w = coil_module.humidity_ratio(30.0, pressure, rh_pct=30.0)
        dew = coil_module.dew_point_c(w, pressure)
        self.assertAlmostEqual(dew, 10.5, delta=0.3)
        self.assertAlmostEqual(
            coil_module.saturated_humidity_ratio(dew, pressure), w, delta=1e-5)

    def test_a_dry_coil_puts_its_surface_at_the_entering_dew_point(self):
        """The physical statement a sensible-only selection makes: the coil is
        barely cold enough to wet."""
        pressure = 101325.0
        w = coil_module.humidity_ratio(30.0, pressure, rh_pct=30.0)
        adp = coil_module.apparatus_dew_point_c(30.0, w, 17.7, w, pressure)
        self.assertAlmostEqual(adp, coil_module.dew_point_c(w, pressure), delta=0.05)

    def test_taking_moisture_out_puts_the_surface_below_it(self):
        """A coil that dehumidifies is colder than one that does not. 40% RH
        at 30 degC, because a dry coil cannot leave air at 17,7 degC out of
        50% RH air -- its dew point is already above that."""
        pressure = 101325.0
        w = coil_module.humidity_ratio(30.0, pressure, rh_pct=40.0)
        dry = coil_module.apparatus_dew_point_c(30.0, w, 17.7, w, pressure)
        wet = coil_module.apparatus_dew_point_c(30.0, w, 17.7, w - 0.001, pressure)
        self.assertLess(wet, dry)

    def test_air_that_would_have_to_condense_is_refused_not_guessed(self):
        """30 degC at 50% RH cannot leave a coil at 17,7 degC without losing
        moisture: its dew point is 18,4. A fit that answered anyway would be
        inventing a surface."""
        pressure = 101325.0
        w = coil_module.humidity_ratio(30.0, pressure, rh_pct=50.0)
        with self.assertRaises(coil_module.CannotFit):
            coil_module.apparatus_dew_point_c(30.0, w, 17.7, w, pressure)


class FitTest(unittest.TestCase):
    """The P3100DA, whose sheet prints the whole performance table."""

    def setUp(self):
        self.unit = library.load("P3100DA")
        self.coil = self.unit.coil
        self.air = air_capacity_rate(27500.0, 30.0, 25.0)

    def test_it_is_a_dx_coil_and_says_so(self):
        self.assertEqual(self.coil.describe()["kind"], "dx")

    def test_the_surface_sits_just_under_the_entering_dew_point(self):
        """110,3 kW total against 109,4 sensible is 0,8% latent, and that is
        a coil that barely wets (ADR-103)."""
        self.assertAlmostEqual(self.coil.adp_c, 10.5, delta=0.3)

    def test_it_gives_back_the_selection_it_was_fitted_to(self):
        point = self.coil.operate(30.0, self.air)
        self.assertAlmostEqual(point.ceiling_kw, 100.5, delta=1.0)
        self.assertAlmostEqual(point.supply_c, 18.8, delta=0.2)

    def test_a_cooler_room_gets_less_out_of_the_machine(self):
        """The whole point. The alert used to say "ask the manufacturer for
        its capacity at 26,4 degC"; this is that number."""
        air = air_capacity_rate(27500.0, 26.4, 25.0)
        point = self.coil.operate(26.4, air)
        self.assertAlmostEqual(point.ceiling_kw, 81.0, delta=2.0)
        self.assertLess(point.ceiling_kw, 0.9 * 100.5)
        self.assertLess(point.supply_c, 18.8)

    def test_capacity_rises_with_the_return_and_never_jumps(self):
        last = None
        for ret in (20.0, 22.0, 24.0, 26.0, 28.0, 30.0, 32.0):
            air = air_capacity_rate(27500.0, ret, 25.0)
            now = self.coil.operate(ret, air).ceiling_kw
            if last is not None:
                self.assertGreater(now, last)
                self.assertLess(now - last, 0.25 * 100.5)
            last = now

    def test_air_colder_than_the_coil_surface_asks_nothing_of_it(self):
        air = air_capacity_rate(27500.0, 9.0, 25.0)
        self.assertEqual(self.coil.operate(9.0, air).ceiling_kw, 0.0)

    def test_it_holds_a_setpoint_it_has_the_compressors_for(self):
        air = air_capacity_rate(27500.0, 28.0, 25.0)
        point = self.coil.operate(28.0, air, setpoint_c=22.0)
        self.assertAlmostEqual(point.supply_c, 22.0, places=2)
        self.assertLess(point.valve, 1.0, "the compressors are not unloading")
        self.assertFalse(point.saturated)

    def test_it_floats_above_a_setpoint_it_has_not(self):
        air = air_capacity_rate(27500.0, 34.0, 25.0)
        point = self.coil.operate(34.0, air, setpoint_c=14.0)
        self.assertGreater(point.supply_c, 14.0)
        self.assertTrue(point.saturated)
        self.assertAlmostEqual(point.valve, 1.0)

    def test_air_already_colder_than_the_setpoint_stops_the_compressors(self):
        """A coil has no heating mode, here as anywhere (ADR-062)."""
        air = air_capacity_rate(27500.0, 16.0, 25.0)
        point = self.coil.operate(16.0, air, setpoint_c=18.0)
        self.assertEqual(point.capacity_kw, 0.0)
        self.assertEqual(point.supply_c, 16.0)
        self.assertEqual(point.valve, 0.0)

    def test_there_is_no_water_to_leave(self):
        self.assertIsNone(self.coil.leaving_water_c(80.0))

    def test_it_carries_what_the_report_has_to_say_about_it(self):
        described = self.coil.describe()
        self.assertEqual(described["rated_ambient_c"], 37.6)
        self.assertEqual(described["refrigerant"], "R410A")
        self.assertEqual(described["duty_label"], "compressor duty")
        self.assertEqual(described["assumptions"], [])


class ThinSheetTest(unittest.TestCase):
    """A sheet that prints less still fits, and says what was assumed."""

    def setUp(self):
        self.unit = library.load("IDAV1911F")
        self.coil = self.unit.coil

    def test_it_fits_and_names_every_assumption(self):
        self.assertIsNotNone(self.coil, self.unit.coil_problem)
        self.assertEqual(len(self.coil.assumptions), 2)
        joined = " ".join(self.coil.assumptions)
        self.assertIn("gross", joined)
        self.assertIn("dry", joined)

    def test_it_still_reproduces_its_own_selection(self):
        air = air_capacity_rate(14215.0, 30.0, 661.0)
        point = self.coil.operate(30.0, air)
        self.assertAlmostEqual(point.ceiling_kw, 51.7, delta=0.6)
        self.assertAlmostEqual(point.supply_c, 17.9, delta=0.2)

    def test_the_dry_assumption_is_worth_a_fraction_of_a_kelvin(self):
        """Measured on the sheet that prints both: carrying the real latent
        duty moves the surface by about a tenth of a degree (ADR-103)."""
        import copy

        from aicfd.coil import fit_dx

        full = library.load("P3100DA")
        thin = copy.deepcopy(full.design)
        thin.pop("gross_total_kw")

        class _Thin:
            model, cooling = full.model, full.cooling
            design, selection = thin, full.selection

        self.assertAlmostEqual(fit_dx(_Thin()).adp_c, full.coil.adp_c, delta=0.3)


class CouplingTest(unittest.TestCase):
    """A DX plant is solved WITH the room now, which is what makes the
    networked control mean anything for it (ADR-064, ADR-103)."""

    def model(self, control: str):
        import copy

        import yaml

        from aicfd.model import build_model
        from tests import support

        spec = copy.deepcopy(support.spec("hall-cage"))
        spec.setdefault("fanwall", {})["control"] = control
        return build_model(spec)

    def supplies(self, control: str) -> dict:
        from aicfd import coupled, post

        built = self.model(control)
        temperatures = [28.0, 30.0, 34.0, 27.0, 26.0, 29.0, 31.0] * 4
        fans = [{"name": p.name, "return_temp_c": t, "intake_kg_s": 8.8}
                for p, t in zip(built.fans, temperatures)]
        original = post.fan_flows
        post.fan_flows = lambda step, **kw: fans
        try:
            return coupled.supply_temperatures(built, "unused")[0]
        finally:
            post.fan_flows = original

    def test_a_dx_case_couples_at_all(self):
        """It returned nothing at all before: no coil, no re-solve, and the
        supply air stayed at whatever the case imposed."""
        self.assertTrue(self.supplies("independent"))

    def test_a_networked_dx_plant_holds_the_setpoint_it_was_given(self):
        """A CRAC array is controlled on its SUPPLY AIR, and a network of
        them holds ONE FIXED setpoint -- the one the case states -- on every
        unit that can reach it. The units that cannot deliver what they can,
        so their supply is warmer (ADR-125).

        It used to set every unit to the supply the WORST unit could make,
        which has unit gain on the room and ran a 1 MW hall from 19,8 to
        48 degC in twenty-five passes (ADR-125). Before that it shared the
        compressor DUTY, which drove well-placed units to 15,9 degC.
        """
        team = self.supplies("team")
        self.assertTrue(team)
        setpoint = 18.8
        holding = [n for n, t in team.items() if abs(t - setpoint) < 1e-6]
        short = [n for n, t in team.items() if t > setpoint + 1e-6]
        self.assertTrue(holding, "no unit holds the setpoint it was given")
        self.assertTrue(short, "the fixture no longer has a unit at its ceiling")
        self.assertFalse([n for n, t in team.items() if t < setpoint - 1e-6],
                         "a unit on the network overcooled")

    def test_the_network_changes_nothing_in_a_steady_field(self):
        """Staging and fan coordination are what a DX network does, and a
        steady field does not see them: `team` and `independent` give the
        same supply on every unit of a supply-controlled DX plant, and the
        report says so rather than describing a control that does not exist
        (ADR-125)."""
        alone, team = self.supplies("independent"), self.supplies("team")
        self.assertEqual(set(alone), set(team))
        for name in alone:
            with self.subTest(unit=name):
                self.assertAlmostEqual(team[name], alone[name], places=6)

    def test_a_chilled_water_network_still_shares_its_valves(self):
        """The two plants are built differently and the model says so: a CRAH
        array on one water loop shares the valve position, a CRAC array shares
        the setpoint (ADR-064, ADR-117)."""
        from aicfd import equipment as library

        # A real chilled-water unit from the library, so this is the coil the
        # tool actually builds for a CRAH and not one invented here.
        unit = next(library.load(name) for name in library.SHIPPED
                    if library.load(name).cooling == "chilled_water"
                    and library.load(name).coil is not None)
        coil = unit.coil
        air = coil.air_fitted
        # A setpoint this unit can hold on its own return with the valve part
        # open: the case where sharing the valve has something to change.
        alone = coil.operate(24.0, air, 22.0)
        shared = coil.operate_shared(32.0, 24.0, air, 22.0)
        self.assertLess(alone.valve, 1.0, "this unit was already flat out")
        self.assertLess(shared.supply_c, alone.supply_c + 1e-6,
                        "a CRAH told to open for its worst peer did not")
        self.assertGreater(shared.capacity_kw, alone.capacity_kw,
                           "and it took no more of the load for doing it")


class TheCompressorsAreTheLimitTest(unittest.TestCase):
    """An evaporator's e-NTU answer grows without limit as the return warms.
    The machine does not: past some return the coil would transfer more than
    the compressors can lift, and every commercial tool holds it there from
    the manufacturer's capacity table (ADR-118).
    """

    def setUp(self):
        self.unit = library.load("P3100DA")
        self.coil = self.unit.coil

    def air(self, return_c: float) -> float:
        return air_capacity_rate(27500.0, return_c, 0.0)

    def test_the_ceiling_is_the_selection_s_own_gross_total(self):
        self.assertAlmostEqual(self.coil.capacity_ceiling_kw, 110.3, places=1)

    def test_it_still_reproduces_the_selection_it_was_fitted_to(self):
        """The cap must not move the point the fit was taken at."""
        point = self.coil.operate(30.0, self.air(30.0))
        self.assertAlmostEqual(point.ceiling_kw, 100.5, delta=1.0)
        self.assertAlmostEqual(point.supply_c, 18.8, delta=0.2)

    def test_a_warm_return_no_longer_buys_capacity_that_is_not_there(self):
        """34 degC of return asked the coil for 122 kW net, which read as a
        unit at 121 % of its plate."""
        point = self.coil.operate(34.0, self.air(34.0))
        self.assertLess(point.ceiling_kw, 102.0)
        self.assertTrue(self.coil.at_compressor_limit(34.0, self.air(34.0)))

    def test_below_the_limit_nothing_changed(self):
        for ret in (20.0, 24.0, 26.4):
            with self.subTest(return_c=ret):
                self.assertFalse(
                    self.coil.at_compressor_limit(ret, self.air(ret)))
        point = self.coil.operate(26.4, self.air(26.4))
        self.assertAlmostEqual(point.ceiling_kw, 81.0, delta=2.0)

    def test_the_curve_never_falls_as_the_return_warms(self):
        last = None
        for ret in range(18, 41, 2):
            now = self.coil.ceiling_kw(float(ret), self.air(float(ret)))
            if last is not None:
                self.assertGreaterEqual(now, last - 1e-6)
            last = now

    def test_a_unit_at_the_limit_still_holds_a_setpoint_it_can_reach(self):
        point = self.coil.operate(34.0, self.air(34.0), setpoint_c=28.0)
        self.assertAlmostEqual(point.supply_c, 28.0, places=2)
        self.assertFalse(point.saturated)

    def test_a_sheet_with_no_total_capacity_has_no_ceiling(self):
        """Nothing is invented: a selection that does not state the total says
        nothing about what the compressors can lift."""
        thin = library.load("IDAV1911F")
        self.assertIsNone(thin.coil.capacity_ceiling_kw)

    def test_the_report_can_see_it(self):
        described = self.coil.describe()
        self.assertAlmostEqual(described["capacity_ceiling_kw"], 110.3, places=1)


class ADXNetworkHoldsTheSetpointItWasGivenTest(unittest.TestCase):
    """A network of supply-controlled DX units holds ONE FIXED setpoint -- the
    one the engineer typed -- on every unit (ADR-125).

    It used to set every unit to the supply the worst-placed unit could still
    make. That law has unit gain on the room: the plant's supply rises, the
    room's return rises by the same amount, the worst unit's achievable
    supply rises again, and the coupled loop on a 1 MW hall climbed 1,2 K a
    pass from 19,8 to 48 degC with nothing to stop it.
    """

    def setUp(self):
        self.coil = library.load("P3100DA").coil
        self.air = self.coil.air_fitted

    def test_a_unit_that_can_hold_the_setpoint_holds_it_whatever_its_peers_see(self):
        alone = self.coil.operate(26.0, self.air, 18.8)
        shared = self.coil.operate_shared(35.0, 26.0, self.air, 18.8)
        self.assertAlmostEqual(shared.supply_c, 18.8, places=6)
        self.assertAlmostEqual(shared.supply_c, alone.supply_c, places=6)
        self.assertAlmostEqual(shared.capacity_kw, alone.capacity_kw, places=6)

    def test_a_unit_that_cannot_delivers_what_it_can_and_says_so(self):
        shared = self.coil.operate_shared(35.0, 35.0, self.air, 18.8)
        self.assertGreater(shared.supply_c, 18.8)
        self.assertTrue(shared.saturated)
        self.assertAlmostEqual(shared.valve, 1.0, places=6)

    def test_the_setpoint_is_never_raised_to_what_the_worst_unit_makes(self):
        """The runaway, in one line: the worst unit's supply is 24,4 degC and
        a well-placed unit must NOT be told to deliver 24,4."""
        worst = self.coil.operate(35.0, self.air, 18.8)
        self.assertGreater(worst.supply_c, 23.0, "the fixture no longer saturates")
        shared = self.coil.operate_shared(35.0, 26.0, self.air, 18.8)
        self.assertLess(shared.supply_c, worst.supply_c - 3.0)
