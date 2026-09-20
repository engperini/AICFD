"""The equipment library, and the number it exists to produce.

A fan wall's datasheet prints one capacity, true at the one return air
temperature the unit was selected for. A room almost never returns air at that
temperature, so judging a plant against the catalogue figure judges it against
something it will not do. These defend the table that replaces it (ADR-036).
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

import yaml

from aicfd import equipment, post, server
from aicfd.model import build_model, equipment_for

RAW = yaml.safe_load(
    """
model: TEST-UNIT
family: Test
size: [3.96, 1.48, 3.67]
selection: {elevation_m: 750, esp_pa: 100, entering_water_c: 18}
capacity:
  - {return_c: 35.0, nscc_kw: 500.0, airflow_m3h: 132000, power_kw: 26.0, supply_c: 22.0}
  - {return_c: 37.0, nscc_kw: 580.0, airflow_m3h: 131800, power_kw: 25.8, supply_c: 21.9}
  - {return_c: 41.0, nscc_kw: 730.0, airflow_m3h: 131400, power_kw: 25.4, supply_c: 21.7}
curve: {measured: false, points: [[0, 260], [132000, 100], [185000, 0]]}
"""
)


class TableTest(unittest.TestCase):
    def setUp(self):
        self.unit = equipment.parse(copy.deepcopy(RAW))

    def test_the_manufacturers_own_points_come_back_exactly(self):
        """A study run at a selection point must quote the selection, not an
        approximation of it."""
        for row in RAW["capacity"]:
            self.assertAlmostEqual(
                self.unit.available_kw(row["return_c"]), row["nscc_kw"], places=9
            )

    def test_between_two_rows_it_interpolates(self):
        self.assertAlmostEqual(self.unit.available_kw(36.0), 540.0)
        self.assertAlmostEqual(self.unit.at(39.0, "nscc_kw"), 655.0)
        self.assertAlmostEqual(self.unit.at(36.0, "airflow_m3h"), 131900.0)

    def test_outside_the_table_it_refuses_rather_than_guesses(self):
        """A coil curve is not a straight line and the ends are where
        extrapolating is worst. Refusing is the whole point."""
        for outside in (34.9, 41.1, 0.0):
            with self.assertRaises(equipment.OutsideTheTable):
                self.unit.available_kw(outside)
        self.assertFalse(self.unit.covers(34.9))
        self.assertTrue(self.unit.covers(35.0))

    def test_rows_are_sorted_and_checked(self):
        shuffled = copy.deepcopy(RAW)
        shuffled["capacity"].reverse()
        self.assertEqual(
            [r["return_c"] for r in equipment.parse(shuffled).capacity],
            [35.0, 37.0, 41.0],
        )
        # A single reference row is legitimate (ADR-065): the design selection
        # is what describes the unit, and the table below it is optional.
        self.assertEqual(
            len(equipment.parse({**copy.deepcopy(RAW),
                                 "capacity": RAW["capacity"][:1]}).capacity),
            1,
        )
        for broken, why in (
            ({"capacity": [{"return_c": 35.0}]}, "a row missing its columns"),
            ({"capacity": [dict(RAW["capacity"][0]), dict(RAW["capacity"][0])]},
             "two rows at the same temperature"),
        ):
            with self.assertRaises(ValueError, msg=why):
                equipment.parse({**copy.deepcopy(RAW), **broken})

    def test_the_design_point_defaults_to_the_warmest_selection(self):
        point = self.unit.design_point()
        self.assertEqual(point["return_c"], 41.0)
        self.assertEqual(point["nscc_kw"], 730.0)


class ShippedUnitTest(unittest.TestCase):
    """The unit in the repository is real data; a typo in it is a wrong report."""

    def test_the_ca80_parses_and_spans_the_selections(self):
        unit = equipment.load("CA80NPVG6")
        self.assertEqual(unit.span, (35.0, 41.0))
        self.assertEqual(len(unit.capacity), 7)
        self.assertAlmostEqual(unit.available_kw(35.0), 504.9)
        self.assertAlmostEqual(unit.available_kw(41.0), 734.3)

    def test_capacity_rises_with_the_air_it_receives(self):
        """The property the whole table exists for. If this ever fails, the
        numbers were transcribed wrong."""
        unit = equipment.load("CA80NPVG6")
        values = [row["nscc_kw"] for row in unit.capacity]
        self.assertEqual(values, sorted(values))
        self.assertGreater(values[-1] / values[0], 1.4)

    def test_an_unknown_model_names_what_there_is(self):
        with self.assertRaises(equipment.UnknownModel) as caught:
            equipment.load("no-such-unit")
        self.assertIn("CA80NPVG6", str(caught.exception))


SPEC = yaml.safe_load(
    """
name: e
gallery: {depth: 9.0, sides: 2}
hall: {height: 7.5, ceiling: 5.5}
pods: 2
aisles: {cold: 2.7, hot: 1.2, perimeter: 1.8, transverse: 3.0}
racks: {per_row: 6, blocks: 2, load_kw: 11.6, size: [0.6, 1.2, 2.25]}
fanwall: {model: CA80NPVG6, count: 4, design_return_c: 38}
grilles: {size: 0.6, coverage: 1.0, free_area: 0.8, loss_coefficient: 2.4}
containment: {enabled: true}
mesh: {cell_size: [0.6, 0.3, 0.25]}
"""
)


class SpecTest(unittest.TestCase):
    def test_naming_a_model_is_enough_to_describe_the_machine(self):
        spec = copy.deepcopy(SPEC)
        model = build_model(spec)
        self.assertEqual(model.equipment.model, "CA80NPVG6")
        self.assertAlmostEqual(model.unit_airflow_m3h, 132397.0)
        self.assertAlmostEqual(model.unit_capacity_kw, 622.7)
        self.assertAlmostEqual(model.unit_power_kw, 25.9)
        self.assertAlmostEqual(model.supply_temp_c, 21.8)
        self.assertAlmostEqual(spec["fanwall"]["width"], 3.96)
        self.assertAlmostEqual(spec["fanwall"]["height"], 3.67)
        self.assertEqual(spec["fanwall"]["static_pressure_pa"], 100)

    def test_the_site_follows_the_selection_unless_stated(self):
        """A unit selected at 750 m and run at sea level moves different air.
        Inheriting it is right; inheriting it silently over a stated value is
        not."""
        spec = copy.deepcopy(SPEC)
        build_model(spec)
        self.assertEqual(spec["site"]["altitude_m"], 750)

        stated = copy.deepcopy(SPEC)
        stated["site"] = {"altitude_m": 0}
        build_model(stated)
        self.assertEqual(stated["site"]["altitude_m"], 0)

    def test_the_spec_always_wins_over_the_library(self):
        spec = copy.deepcopy(SPEC)
        spec["fanwall"]["airflow_m3h"] = 90000
        spec["fanwall"]["supply_temp_c"] = 19.0
        model = build_model(spec)
        self.assertAlmostEqual(model.unit_airflow_m3h, 90000.0)
        self.assertAlmostEqual(model.supply_temp_c, 19.0)

    def test_no_model_named_means_no_equipment(self):
        spec = copy.deepcopy(SPEC)
        spec["fanwall"] = {"count": 4, "airflow_m3h": 90000, "width": 3.9,
                           "height": 3.75, "supply_temp_c": 21.0}
        self.assertIsNone(equipment_for(spec))
        self.assertIsNone(build_model(spec).equipment)


class CoilCapacityTest(unittest.TestCase):
    """The headline the coil produces: what the plant uses of what it has.

    Every number here moves with the return air temperature, which is the
    whole point: a heat exchanger's capacity is epsilon x C_air x (return -
    water), and holding it fixed while the return climbs asserts that a coil
    moves the same heat across a bigger temperature difference (ADR-039).
    """

    def setUp(self):
        self.model = build_model(copy.deepcopy(SPEC))

    def _fans(self, *pairs):
        """Units as a solved run reports them: the mass each one moved, and
        the heat that mass carried. Consistent by construction, because a
        fixture where they disagree tests nothing real."""
        out = []
        for i, (temperature, kg_s) in enumerate(pairs):
            out.append({
                "name": f"fan{i + 1}",
                "return_temp_c": temperature,
                "intake_kg_s": kg_s,
                "heat_kw": kg_s * 1.005 * (temperature - self.model.supply_temp_c),
            })
        return out

    def test_capacity_rises_with_the_return_air_it_receives(self):
        """The finding the whole model exists to produce."""
        out = {}
        fans = self._fans((35.0, 31.0), (38.0, 31.0), (41.0, 31.0))
        post.coil_capacity(self.model, fans, out)
        self.assertLess(fans[0]["available_kw"], fans[1]["available_kw"])
        self.assertLess(fans[1]["available_kw"], fans[2]["available_kw"])
        # and by a lot: the same machine, the same air, warmer air reaching it
        self.assertGreater(fans[2]["available_kw"] / fans[0]["available_kw"], 1.25)

    def test_each_unit_is_judged_at_its_own_return_temperature(self):
        fans = self._fans((36.0, 31.0), (40.0, 31.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 1000.0})
        self.assertNotAlmostEqual(fans[0]["available_kw"], fans[1]["available_kw"])
        self.assertAlmostEqual(
            out["available_kw"], fans[0]["available_kw"] + fans[1]["available_kw"], 1
        )
        removed = sum(f["heat_kw"] for f in fans)
        self.assertAlmostEqual(
            out["utilisation_pct"], removed / out["available_kw"] * 100, places=1
        )

    def test_a_unit_beyond_its_own_coil_is_counted(self):
        fans = self._fans((35.0, 95.0), (35.0, 8.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 1000.0})
        self.assertEqual(out["units_over_capacity"], 1)
        self.assertGreater(fans[0]["of_available_pct"], 100)

    def test_the_catalogue_figure_is_reported_separately_not_instead(self):
        """Both numbers, named. A reader who has only seen the catalogue one
        will otherwise read this as it and conclude the opposite."""
        fans = self._fans((38.0, 31.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 500.0})
        self.assertEqual(out["unit_model"], "CA80NPVG6")
        self.assertAlmostEqual(out["catalogue_kw"], 622.7)
        self.assertNotAlmostEqual(out["available_kw"], out["catalogue_kw"], places=1)

    def test_a_capacity_is_given_at_any_return_the_room_produces(self):
        """The unit is a machine, not a table: every condition a room presents
        has an answer, whether or not a selection was taken at it."""
        fans = self._fans((28.0, 31.0), (33.0, 31.0), (38.0, 31.0), (46.0, 31.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 900.0})
        capacities = [f["available_kw"] for f in fans]
        self.assertEqual(len(capacities), 4)
        self.assertEqual(capacities, sorted(capacities), "warmer air, more heat")
        self.assertNotIn("coil_outside_table_c", out)

    def test_the_coil_is_reported_by_what_it_is(self):
        fans = self._fans((33.0, 31.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 400.0})
        coil = out["coil_model"]
        self.assertIn("design_return_c", coil)
        self.assertIn("air_split_pct", coil)
        self.assertIn("water_max_m3h", coil)

    def test_how_much_authority_each_unit_has_left_is_reported(self):
        """The number that says whether a unit can still hold its supply
        temperature. The water flow itself is a hydraulic question this tool
        does not answer."""
        fans = self._fans((36.0, 31.0))
        post.coil_capacity(self.model, fans, {"recovered_kw": 400.0})
        self.assertGreater(fans[0]["coil_valve_pct"], 0)
        self.assertLessEqual(fans[0]["coil_valve_pct"], 100)
        self.assertNotIn("coil_water_m3h", fans[0])

    def test_a_supply_the_coil_cannot_hold_is_reported_not_hidden(self):
        """The reason a fixed supply air temperature is an assumption and not
        a fact: once the valve is wide open the machine stops holding it."""
        spec = copy.deepcopy(SPEC)
        spec["fanwall"]["supply_temp_c"] = 18.5  # below what any valve can reach
        model = build_model(spec)
        fans = self._fans((36.0, 31.0))
        out = post.coil_capacity(model, fans, {"recovered_kw": 400.0})
        self.assertEqual(out["coil_saturated_units"], 1)
        self.assertGreater(out["coil_supply_needed_c"], out["coil_supply_setpoint_c"])
        self.assertEqual(fans[0]["coil_valve_pct"], 100)

    def test_a_case_with_no_equipment_says_nothing_at_all(self):
        spec = copy.deepcopy(SPEC)
        spec["fanwall"] = {"count": 4, "airflow_m3h": 90000, "width": 3.9,
                           "height": 3.75, "supply_temp_c": 21.0}
        plain = build_model(spec)
        self.assertEqual(post.coil_capacity(plain, self._fans((38.0, 31.0)), {}), {})


class UnfittableUnitTest(unittest.TestCase):
    """One model for every unit. A unit that cannot be fitted is a file that
    is not finished, and it says which field is missing (ADR-063)."""

    def unit(self):
        import tempfile

        import yaml

        from aicfd import equipment as e

        spec = yaml.safe_load((e.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec["selection"].pop("leaving_water_c")
        spec["model"] = "INCOMPLETE"
        tmp = Path(tempfile.mkdtemp())
        (tmp / "X.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
        saved, e.LIBRARY = e.LIBRARY, tmp
        self.addCleanup(lambda: setattr(e, "LIBRARY", saved))
        return e.load("X")

    def test_it_names_the_field_that_is_missing(self):
        unit = self.unit()
        self.assertIsNone(unit.coil)
        self.assertIn("leaving_water_c", unit.coil_problem)

    def test_there_is_no_second_way_of_answering(self):
        """A capacity table used to be read instead, which made two classes
        of unit with different answers and a refusal off the table that a
        fitted unit never hit."""
        from aicfd import post

        self.assertFalse(hasattr(post, "_coil_from_table"))
        source = Path(post.__file__).read_text()
        self.assertNotIn("coil_outside_table_c", source)

    def test_the_result_says_so_rather_than_falling_silent(self):
        from aicfd import post

        out = post._coil_alerts({"coil_problem": "X has no design selection"})
        self.assertTrue(out)
        self.assertIn("cannot be modelled", out[0])


class LibraryCopy(unittest.TestCase):
    """A scratch library holding the real shipped unit, restored after each
    test, so writing tests never touch the repository's own file."""

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.saved = equipment.LIBRARY
        equipment.LIBRARY = Path(self.tmp.name)
        shutil.copy(self.saved / "CA80NPVG6.yaml", equipment.LIBRARY)
        self.path = equipment.LIBRARY / "CA80NPVG6.yaml"
        self.before = self.path.read_text()

    def tearDown(self):
        equipment.LIBRARY = self.saved
        self.tmp.cleanup()

    def _rows(self):
        return [dict(row) for row in equipment.load("CA80NPVG6").capacity]


class SaveTest(LibraryCopy):
    """Editing a unit must not cost the file its provenance.

    An equipment file's comments say which selections the numbers came from,
    who issued them and at what conditions. `yaml.safe_dump` would rewrite
    the file and throw all of that away, and a table of numbers nobody can
    trace is worth less than no table.
    """

    def test_an_edit_keeps_every_comment(self):
        rows = self._rows()
        rows[0]["nscc_kw"] = 499.5
        equipment.save("CA80NPVG6", {"capacity": rows})
        after = self.path.read_text()
        self.assertEqual(after.count("#"), self.before.count("#"))
        self.assertIn("Ascenty SP06", after)
        self.assertAlmostEqual(equipment.load("CA80NPVG6").available_kw(35.0), 499.5)

    def test_saving_the_same_table_changes_nothing_at_all(self):
        equipment.save("CA80NPVG6", {"capacity": self._rows()})
        self.assertEqual(self.path.read_text(), self.before)

    def test_a_table_that_does_not_parse_never_reaches_the_file(self):
        rows = self._rows()
        rows[1]["return_c"] = rows[0]["return_c"]
        with self.assertRaises(ValueError):
            equipment.save("CA80NPVG6", {"capacity": rows})
        self.assertEqual(self.path.read_text(), self.before)

    def test_rows_can_be_added_and_removed(self):
        rows = self._rows()
        rows.append({**rows[-1], "return_c": 42.0, "nscc_kw": 770.0})
        equipment.save("CA80NPVG6", {"capacity": rows})
        self.assertEqual(equipment.load("CA80NPVG6").span, (35.0, 42.0))
        self.assertIn("Ascenty SP06", self.path.read_text())

        equipment.save("CA80NPVG6", {"capacity": rows[:3]})
        self.assertEqual(len(equipment.load("CA80NPVG6").capacity), 3)
        self.assertIn("Ascenty SP06", self.path.read_text())

    def test_the_identity_fields_are_editable(self):
        """The same coil is very often sold under two names. Changing the one
        on the label must not mean retyping the selections."""
        unit = equipment.load("CA80NPVG6").to_dict()
        equipment.save(
            "CA80NPVG6",
            {**unit, "family": "Ascenty CoolWall", "weight_kg": 3100,
             "fans": {**unit["fans"], "count": 10, "type": "EC plug fan"}},
        )
        after = equipment.load("CA80NPVG6")
        self.assertEqual(after.family, "Ascenty CoolWall")
        self.assertEqual(after.weight_kg, 3100)
        self.assertEqual(after.fans["count"], 10)
        self.assertEqual(after.fans["type"], "EC plug fan")
        self.assertEqual(after.fans["module"], unit["fans"]["module"])  # untouched
        self.assertIn("Ascenty SP06", self.path.read_text())

    def test_the_selection_conditions_are_editable(self):
        unit = equipment.load("CA80NPVG6").to_dict()
        equipment.save(
            "CA80NPVG6",
            {**unit, "selection": {**unit["selection"], "esp_pa": 150}},
        )
        self.assertEqual(equipment.load("CA80NPVG6").selection["esp_pa"], 150)
        self.assertEqual(
            self.path.read_text().count("#"), self.before.count("#")
        )

    def test_the_curve_is_editable(self):
        unit = equipment.load("CA80NPVG6").to_dict()
        points = [[0, 280], [130000, 110], [180000, 0]]
        equipment.save(
            "CA80NPVG6", {**unit, "curve": {"measured": True, "points": points}}
        )
        after = equipment.load("CA80NPVG6")
        self.assertTrue(after.curve["measured"])
        self.assertEqual([list(p) for p in after.curve["points"]], points)
        self.assertIn("Ascenty SP06", self.path.read_text())

    def test_saving_a_unit_unchanged_changes_nothing_at_all(self):
        """Not one byte, comment alignment included: a page that reads a unit
        and writes it straight back must leave the file alone."""
        equipment.save("CA80NPVG6", equipment.load("CA80NPVG6").to_dict())
        self.assertEqual(self.path.read_text(), self.before)

    def test_an_edit_and_its_undo_leave_the_file_as_it_was(self):
        unit = equipment.load("CA80NPVG6").to_dict()
        equipment.save(
            "CA80NPVG6", {**unit, "selection": {**unit["selection"], "esp_pa": 120}}
        )
        self.assertNotEqual(self.path.read_text(), self.before)
        equipment.save("CA80NPVG6", unit)
        self.assertEqual(self.path.read_text(), self.before)


class SaveAsTest(LibraryCopy):
    """A new name is a new unit.

    Renaming in place would break every case file pointing at the old name
    without saying so, and both units normally have to exist anyway: one for
    the studies already run, one for the new work.
    """

    def test_it_copies_the_unit_under_the_new_name(self):
        unit = equipment.load("CA80NPVG6").to_dict()
        made = equipment.save_as("CW80-A", "CA80NPVG6", {**unit, "family": "Reseller"})
        self.assertEqual(made.model, "CW80-A")
        self.assertEqual(made.family, "Reseller")
        self.assertEqual(
            [dict(r) for r in made.capacity],
            [dict(r) for r in equipment.load("CA80NPVG6").capacity],
        )
        self.assertIn("CW80-A", equipment.available())

    def test_the_source_unit_is_left_exactly_as_it_was(self):
        equipment.save_as(
            "CW80-A", "CA80NPVG6",
            {**equipment.load("CA80NPVG6").to_dict(), "family": "Reseller"},
        )
        self.assertEqual(self.path.read_text(), self.before)

    def test_the_copy_keeps_the_provenance_and_says_where_it_came_from(self):
        equipment.save_as("CW80-A", "CA80NPVG6", equipment.load("CA80NPVG6").to_dict())
        text = (equipment.LIBRARY / "CW80-A.yaml").read_text()
        self.assertIn("Ascenty SP06", text)
        self.assertIn("Copied from CA80NPVG6", text)

    def test_it_refuses_a_name_the_library_already_has(self):
        with self.assertRaises(ValueError):
            equipment.save_as(
                "CA80NPVG6", "CA80NPVG6", equipment.load("CA80NPVG6").to_dict()
            )
        self.assertEqual(self.path.read_text(), self.before)

    def test_it_refuses_a_name_that_is_a_path(self):
        for name in ("", "../escape", "sub/unit"):
            with self.assertRaises(ValueError):
                equipment.save_as(
                    name, "CA80NPVG6", equipment.load("CA80NPVG6").to_dict()
                )

    def test_a_copy_that_does_not_parse_is_not_left_behind(self):
        rows = [dict(r) for r in equipment.load("CA80NPVG6").capacity]
        rows[1]["return_c"] = rows[0]["return_c"]
        with self.assertRaises(ValueError):
            equipment.save_as("CW80-A", "CA80NPVG6", {"capacity": rows})
        self.assertNotIn("CW80-A", equipment.available())


class EndpointTest(LibraryCopy):
    """What the equipment page posts, and what comes back to it."""

    def test_a_save_writes_the_unit_and_returns_it(self):
        from aicfd.server import read_equipment, write_equipment

        draft = read_equipment("CA80NPVG6")["unit"]
        payload = write_equipment(
            "CA80NPVG6", {**draft, "family": "Reseller", "save_as": ""}
        )
        self.assertEqual(payload["unit"]["model"], "CA80NPVG6")
        self.assertEqual(payload["unit"]["family"], "Reseller")
        self.assertEqual(equipment.load("CA80NPVG6").family, "Reseller")

    def test_a_save_as_makes_a_new_unit_and_returns_that_one(self):
        from aicfd.server import read_equipment, write_equipment

        draft = read_equipment("CA80NPVG6")["unit"]
        payload = write_equipment(
            "CA80NPVG6", {**draft, "family": "Reseller", "save_as": "CW80-A"}
        )
        self.assertEqual(payload["unit"]["model"], "CW80-A")
        self.assertIn("CW80-A", payload["models"])
        self.assertEqual(equipment.load("CA80NPVG6").family, draft["family"])

    def test_save_as_under_the_same_name_is_an_ordinary_save(self):
        """Not an error: the name box simply still holds this unit's name."""
        from aicfd.server import read_equipment, write_equipment

        draft = read_equipment("CA80NPVG6")["unit"]
        payload = write_equipment(
            "CA80NPVG6", {**draft, "weight_kg": 3100, "save_as": "CA80NPVG6"}
        )
        self.assertEqual(payload["unit"]["weight_kg"], 3100)

    def test_the_draft_the_page_reads_writes_back_byte_for_byte(self):
        """The page's round trip, in full: whatever `read_equipment` hands the
        page must come back through `write_equipment` without disturbing the
        file — otherwise opening a unit and pressing Save would rewrite it."""
        from aicfd.server import read_equipment, write_equipment

        write_equipment("CA80NPVG6", {**read_equipment("CA80NPVG6")["unit"],
                                      "save_as": ""})
        self.assertEqual(self.path.read_text(), self.before)


class ColdReturnTest(unittest.TestCase):
    """A chilled-water coil has no heating mode (ADR-062)."""

    def coil(self):
        from aicfd import equipment as e

        return e.load("CA80NPVG6").coil

    def air(self):
        from aicfd.coil import air_capacity_rate

        return air_capacity_rate(104181.0, 30.0, 750.0)

    def test_air_colder_than_the_setpoint_shuts_the_valve(self):
        """It used to pin the supply at the setpoint: 15 degC in, 21,9 out,
        and minus 214 kW of capacity. That supply is written back onto the fan
        patch, so the unit went on to inject heat into the room it was
        cooling."""
        point = self.coil().operate(15.0, self.air(), 21.9)
        self.assertEqual(point.valve, 0.0)
        self.assertAlmostEqual(point.supply_c, 15.0, places=6)
        self.assertEqual(point.capacity_kw, 0.0)

    def test_nothing_it_reports_at_a_cold_return_is_negative(self):
        for ret in (5.0, 12.0, 18.0, 21.9):
            with self.subTest(return_c=ret):
                point = self.coil().operate(ret, self.air(), 21.9)
                self.assertGreaterEqual(point.capacity_kw, 0.0)
                self.assertGreaterEqual(point.ceiling_kw, 0.0)
                self.assertLessEqual(point.supply_c, ret + 1e-9)

    def test_it_still_cools_the_moment_there_is_something_to_cool(self):
        point = self.coil().operate(22.5, self.air(), 21.9)
        self.assertGreater(point.valve, 0.0)
        self.assertAlmostEqual(point.supply_c, 21.9, places=2)
        self.assertGreater(point.capacity_kw, 0.0)

    def test_a_warm_return_is_unchanged(self):
        """The fix touches the cold end only: above the setpoint the unit
        holds it until the valve is wide open, and then the supply floats."""
        held = self.coil().operate(35.0, self.air(), 21.9)
        self.assertAlmostEqual(held.supply_c, 21.9, places=2)
        self.assertFalse(held.saturated)
        floating = self.coil().operate(55.0, self.air(), 21.9)
        self.assertTrue(floating.saturated)
        self.assertGreater(floating.supply_c, 21.9)


class ProvenanceTest(unittest.TestCase):
    """The model answers at any return with the same confidence for a unit
    fitted to seven manufacturer selections and for one fitted to a single
    one. A reader has to be able to tell which (ADR-063)."""

    def test_a_unit_checked_against_more_says_how_closely(self):
        said = post._coil_provenance(
            {"coil_model": {"reference_selections": 7, "reference_error_k": 0.026,
                            "air_split_pct": 78}})
        self.assertIn("7 more", said)
        self.assertIn("0.026 K", said)

    def test_a_unit_on_one_selection_says_what_was_assumed(self):
        said = post._coil_provenance(
            {"coil_model": {"reference_selections": 0, "air_split_pct": 78}})
        self.assertIn("design selection alone", said)
        self.assertIn("78%", said)
        self.assertIn("nobody measured", said)

    def test_one_selection_is_enough_to_model_a_unit(self):
        """What every new unit will carry. The seven rows the worked unit
        happens to have only refine the air/water split."""
        import tempfile

        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        minimal = {k: v for k, v in spec.items()
                   if k in ("model", "family", "size", "selection", "design", "fans")}
        minimal["model"] = "ONE-SELECTION"
        tmp = Path(tempfile.mkdtemp())
        (tmp / "X.yaml").write_text(yaml.safe_dump(minimal, sort_keys=False))
        worked = equipment.load("CA80NPVG6").coil
        saved, equipment.LIBRARY = equipment.LIBRARY, tmp
        self.addCleanup(lambda: setattr(equipment, "LIBRARY", saved))
        unit = equipment.load("X")
        self.assertIsNotNone(unit.coil)
        self.assertEqual(unit.coil.reference_returns, ())
        from aicfd.coil import air_capacity_rate

        air = air_capacity_rate(104181.0, 30.0, 750.0)
        # and it answers well past the selection, as the worked unit does
        self.assertAlmostEqual(unit.coil.operate(55.0, air, 21.9).supply_c,
                               worked.operate(55.0, air, 21.9).supply_c,
                               delta=0.1)


class OneSelectionOnThePageTest(unittest.TestCase):
    """A reader with one selection in hand must not be told the unit needs
    two (ADR-065)."""

    def page(self) -> str:
        from aicfd import server

        return (server.REPO_ROOT / "web" / "equipment.js").read_text()

    def test_the_design_selection_is_on_the_page(self):
        """It was editable through the API and absent from the page, so a
        reader typed their one selection into the reference table instead."""
        js = self.page()
        for field in ("design.return_c", "design.supply_c",
                      "design.airflow_m3h", "design.nscc_kw"):
            with self.subTest(field=field):
                self.assertIn(field, js)

    def test_the_reference_table_can_be_emptied(self):
        """It refused to go below two rows, which is where the impression
        came from."""
        self.assertNotIn("draft.capacity.length <= 2", self.page())

    def test_it_is_not_called_the_selections(self):
        js = self.page()
        self.assertIn("The design selection", js)
        self.assertIn("Reference selections", js)

    def test_a_unit_with_no_reference_rows_parses(self):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        one = {k: v for k, v in spec.items() if k != "capacity"}
        one["model"] = "ONE"
        unit = equipment.parse(one)
        self.assertEqual(unit.capacity, ())
        self.assertIsNotNone(unit.coil)

    def test_a_unit_with_no_design_selection_says_what_is_missing(self):
        """It loads -- a half-written unit has to be openable to be finished
        -- and the coil says which field is absent."""
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        without = {k: v for k, v in spec.items() if k != "design"}
        unit = equipment.parse(without)
        self.assertIsNone(unit.coil)
        self.assertIn("design.return_c", unit.coil_problem)
        self.assertNotIn("at least two", unit.coil_problem)

    def test_nothing_anywhere_asks_for_two_selections(self):
        from aicfd import server

        for path in (equipment.__file__, server.REPO_ROOT / "web" / "equipment.js"):
            with self.subTest(file=Path(path).name):
                self.assertNotIn("at least two", Path(path).read_text())


class HalfWrittenUnitTest(unittest.TestCase):
    """A unit somebody started and has not finished.

    Every one of these failed before ADR-066, and the visible symptom was the
    worst kind: the page accepted what was typed, said "Saved", and the next
    read showed the fields empty again.
    """

    STARTED = """# A unit somebody started.
model: ZZHALF
family: Teste

size: [3.96, 1.48, 3.67]      # width x depth x height, metres
weight_kg: 3000

fans:
  count: 8

selection:
  elevation_m: 750
  esp_pa: 100                 # external static pressure
  entering_water_c: 18
  leaving_water_c: 28
  entering_air_rh: 30         # %
"""

    DESIGN = {"return_c": 34.0, "supply_c": 21.9, "airflow_m3h": 121545.0,
              "nscc_kw": 432.6, "power_kw": 21.4}

    def setUp(self):
        self.path = equipment.LIBRARY / "ZZHALF.yaml"
        self.path.write_text(self.STARTED)
        self.addCleanup(lambda: self.path.exists() and self.path.unlink())

    def test_it_loads_and_has_no_span(self):
        unit = equipment.load("ZZHALF")
        self.assertIsNone(unit.span)
        self.assertFalse(unit.covers(34.0))
        self.assertIsNone(unit.to_dict()["span"])

    def test_the_design_selection_survives_a_save(self):
        """The bug, exactly: the block is absent, so the value was dropped."""
        raw = equipment.load("ZZHALF").to_dict()
        raw["design"] = dict(self.DESIGN)
        equipment.save("ZZHALF", raw)
        back = equipment.load("ZZHALF")
        self.assertEqual({k: float(v) for k, v in back.design.items()}, self.DESIGN)
        self.assertIsNotNone(back.coil, back.coil_problem)
        self.assertEqual(back.span, (34.0, 34.0))

    def test_saving_keeps_the_comments_it_did_not_write(self):
        raw = equipment.load("ZZHALF").to_dict()
        raw["design"] = dict(self.DESIGN)
        equipment.save("ZZHALF", raw)
        text = self.path.read_text()
        self.assertIn("# A unit somebody started.", text)
        self.assertIn("# external static pressure", text)

    def test_reference_rows_can_be_added_to_a_file_with_no_table(self):
        raw = equipment.load("ZZHALF").to_dict()
        raw["design"] = dict(self.DESIGN)
        raw["capacity"] = [{"return_c": 35.0, "nscc_kw": 504.9,
                            "airflow_m3h": 121545.0, "power_kw": 21.4,
                            "supply_c": 22.0}]
        equipment.save("ZZHALF", raw)
        self.assertEqual(len(equipment.load("ZZHALF").capacity), 1)

    def test_an_empty_table_stays_absent_rather_than_being_invented(self):
        raw = equipment.load("ZZHALF").to_dict()
        raw["design"] = dict(self.DESIGN)
        raw["capacity"] = []
        equipment.save("ZZHALF", raw)
        self.assertNotIn("capacity:", self.path.read_text())


class DesignSelectionReachesTheDraftTest(unittest.TestCase):
    """The page's working copy has to carry every field the page shows.

    `clone` in web/equipment.js builds the draft key by key. A card was added
    to the page and its block was not added here, so typing into it threw
    inside an input handler -- invisibly -- and Save posted a draft with no
    design. The page said "Saved" and the fields came back empty (ADR-067).
    """

    def test_clone_carries_every_editable_block(self):
        source = (server.REPO_ROOT / "web" / "equipment.js").read_text()
        body = source[source.index("function clone(source)"):]
        body = body[: body.index("\n}")]
        for block in sorted({p.split(".")[0] for p in equipment.EDITABLE if "." in p}):
            with self.subTest(block=block):
                self.assertIn(f"{block}:", body)

    def test_plant_creates_a_missing_block_instead_of_throwing(self):
        source = (server.REPO_ROOT / "web" / "equipment.js").read_text()
        body = source[source.index("function plant(target, path, value)"):]
        self.assertIn("??=", body[: body.index("\n}")])


class BadReferenceRowTest(unittest.TestCase):
    """A reference row is optional evidence, so a bad one is a bad row."""

    def raw(self, **design):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec["capacity"] = [
            # Consistent: 121,545 m3/h from 35.0 to 20.79 degC really is 504.9 kW.
            {"return_c": 35.0, "nscc_kw": 504.9, "airflow_m3h": 121545.0,
             "power_kw": 21.4, "supply_c": 20.79},
            # Nonsense: the temperatures and airflow do not carry this.
            {"return_c": 38.0, "nscc_kw": 390.0, "airflow_m3h": 108986.0,
             "power_kw": 26.2, "supply_c": 24.0},
        ]
        return spec

    def test_it_does_not_block_the_coil(self):
        unit = equipment.parse(self.raw())
        self.assertIsNotNone(unit.coil, unit.coil_problem)
        self.assertIsNone(unit.coil_problem)

    def test_it_is_named_rather_than_dropped_in_silence(self):
        unit = equipment.parse(self.raw())
        rejected = unit.coil.rejected_references
        self.assertEqual(len(rejected), 1)
        self.assertIn("reference row at 38.0", rejected[0])
        self.assertNotIn(38.0, unit.coil.reference_returns)

    def test_the_design_selection_itself_still_has_to_close(self):
        """That one IS the model, so a wrong number there is not evidence."""
        spec = self.raw()
        spec["design"] = dict(spec["design"], nscc_kw=999.9)
        unit = equipment.parse(spec)
        self.assertIsNone(unit.coil)
        self.assertIn("not self-consistent", unit.coil_problem)


class CoilCurveForThePageTest(unittest.TestCase):
    """What the capacity chart draws, and what it says when it cannot."""

    def test_a_unit_with_one_selection_gets_a_curve(self):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec.pop("capacity")
        curve = server.coil_curve(equipment.parse(spec))
        self.assertIsNone(curve["problem"])
        self.assertGreater(len(curve["points"]), 10)
        warmer = [p["nscc_kw"] for p in curve["points"]]
        self.assertEqual(warmer, sorted(warmer), "capacity rises with return air")
        self.assertAlmostEqual(curve["design_return_c"], 34.0)

    def test_it_never_reads_below_the_water(self):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        curve = server.coil_curve(equipment.parse(spec))
        water = spec["selection"]["entering_water_c"]
        self.assertGreaterEqual(min(p["return_c"] for p in curve["points"]), water)
        self.assertTrue(all(p["nscc_kw"] >= 0 for p in curve["points"]))

    def test_with_no_design_it_carries_the_reason_instead(self):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec.pop("design")
        curve = server.coil_curve(equipment.parse(spec))
        self.assertEqual(curve["points"], [])
        self.assertIn("design.return_c", curve["problem"])
