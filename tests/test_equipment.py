"""The equipment library, and the number it exists to produce.

A fan wall's datasheet prints one capacity, true at the one return air
temperature the unit was selected for. A room almost never returns air at that
temperature, so judging a plant against the catalogue figure judges it against
something it will not do. These defend the table that replaces it (ADR-036).
"""

from __future__ import annotations

import copy
import inspect
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
        """The alert carries the problem in its own words and says what the
        capacity beside it therefore is.

        It used to open "This unit cannot be modelled from what its file
        says", which is right for an unfinished file and wrong for a DX unit
        -- whose file is complete and whose coil is simply not a thing this
        software models (ADR-073, ADR-097). The wrapper now states the
        consequence and lets the problem speak for the cause.
        """
        from aicfd import post

        out = post._coil_alerts({"coil_problem": "X has no design selection"})
        self.assertTrue(out)
        self.assertIn("X has no design selection", out[0])
        self.assertIn("catalogue figure", out[0])
        self.assertIn("the return air the unit was selected for", out[0])


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


class WhatWouldCloseTest(unittest.TestCase):
    """A selection that does not close names the fields that would close it.

    The eight real Vertiv selections shipped here agree with their own
    temperatures and airflow to within 0.6%, so a miss of several percent is a
    transcription and the message's job is to say which field to look at.
    """

    def unit(self, elevation, **design):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec["selection"] = dict(spec["selection"], elevation_m=elevation)
        spec["design"] = design
        spec.pop("capacity")
        return equipment.parse(spec)

    def test_the_shipped_selections_close_on_themselves(self):
        """The calibration the tolerance rests on. If this drifts, the check
        is measuring the model's physics rather than the manufacturer's data."""
        from aicfd.coil import air_capacity_rate

        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        altitude = spec["selection"]["elevation_m"]
        for row in [*spec["capacity"], spec["design"]]:
            with self.subTest(return_c=row["return_c"]):
                air = air_capacity_rate(row["airflow_m3h"], row["return_c"], altitude)
                heat = air * (row["return_c"] - row["supply_c"])
                self.assertLess(abs(heat - row["nscc_kw"]) / row["nscc_kw"], 0.01)

    def test_it_names_the_elevation_that_would_close_it(self):
        """The reported case: a CA40 selection read at 750 m instead of ~330."""
        # 2.0 kW of fan power: far too little to explain the 14 kW gap, so
        # the gross/net branch does not fire and these two candidates do.
        unit = self.unit(750, return_c=37.0, supply_c=21.8, airflow_m3h=60504.0,
                         nscc_kw=281.0, power_kw=2.0)
        why = unit.coil_problem
        self.assertIn("site elevation of 328 m", why)
        self.assertIn("this unit says 750 m", why)
        self.assertIn("Selected at", why)

    def test_it_also_names_the_supply_that_would_close_it(self):
        unit = self.unit(750, return_c=37.0, supply_c=21.8, airflow_m3h=60504.0,
                         nscc_kw=281.0, power_kw=2.0)
        self.assertIn("wants a supply of 21.0 degC", unit.coil_problem)

    def test_at_the_elevation_it_names_the_selection_fits(self):
        """Not just plausible -- the number is the answer."""
        unit = self.unit(328, return_c=37.0, supply_c=21.8, airflow_m3h=60504.0,
                         nscc_kw=281.0, power_kw=2.0)
        self.assertIsNone(unit.coil_problem)
        self.assertIsNotNone(unit.coil)

    def test_neither_is_ranked_over_the_other(self):
        """Which field is the transcription is the engineer's to know."""
        unit = self.unit(750, return_c=38.0, supply_c=24.0, airflow_m3h=108986.0,
                         nscc_kw=390.0, power_kw=26.2)
        why = unit.coil_problem
        self.assertIn("Two things would close it", why)
        self.assertIn("1,771 m", why)
        self.assertIn("25.6 degC", why)


class GrossForNetTest(unittest.TestCase):
    """The CA40NPVGT datasheet, as a test.

    Vertiv CWA CA40NPVGT, 661 m, 37.0 degC / 30% RH entering air, 18/28 degC
    water, 100 Pa ESP. The sheet prints `Gross Sensible Cooling Capacity`
    280.7 kW directly above `NSCC` 270.1 kW, and they differ by exactly the
    10.6 kW of fan power. Taking the wrong one of the two is the transcription
    this product family invites, and the row itself carries the evidence.
    """

    SHEET = {"return_c": 37.0, "supply_c": 21.8, "airflow_m3h": 60504.0,
             "power_kw": 10.6}
    NSCC = 270.1
    GROSS = 280.7

    def unit(self, elevation=661.0, **design):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec["model"] = "CA40NPVGT"
        spec["selection"] = dict(spec["selection"], elevation_m=elevation)
        merged = {**self.SHEET, **design}
        spec["design"] = {k: v for k, v in merged.items() if v is not None}
        spec.pop("capacity")
        return equipment.parse(spec)

    def test_the_model_reproduces_the_manufacturers_nscc(self):
        """0.03% against the printed figure. This is the calibration that
        makes the whole consistency check worth making."""
        from aicfd.coil import air_capacity_rate

        air = air_capacity_rate(self.SHEET["airflow_m3h"], 37.0, 661.0)
        self.assertAlmostEqual(air * (37.0 - 21.8), self.NSCC, delta=0.5)

    def test_the_sheet_as_printed_fits(self):
        self.assertIsNone(self.unit(nscc_kw=self.NSCC).coil_problem)

    def test_the_gross_figure_is_identified_by_name(self):
        why = self.unit(nscc_kw=self.GROSS).coil_problem
        self.assertIn("GROSS figure was taken", why)
        self.assertIn("270.1 kW", why)
        self.assertIn("net sensible (NSCC)", why)

    def test_it_is_identified_even_at_the_wrong_elevation(self):
        """Both fields wrong at once, which is how it was really reported."""
        why = self.unit(elevation=750.0, nscc_kw=self.GROSS).coil_problem
        self.assertIn("GROSS figure was taken", why)

    def test_a_row_with_no_power_falls_back_to_the_other_candidates(self):
        why = self.unit(nscc_kw=self.GROSS, power_kw=None).coil_problem
        self.assertNotIn("GROSS", why)
        self.assertIn("site elevation of", why)


class ShippedUniflairTest(unittest.TestCase):
    """The Uniflair in the repository is real data; a typo in it is a wrong
    report. One selection, so this is also the shipped proof that one is
    enough (ADR-065)."""

    SHEET = dict(elevation=661.0, return_c=38.0, supply_c=24.6,
                 airflow_m3h=115500.0, nscc_kw=453.7, gross_kw=481.4,
                 power_kw=27.7, water_in=20.0, water_out=30.0)

    def setUp(self):
        self.unit = equipment.load("FWCV36L2F")

    def test_it_parses_with_one_selection_and_no_table(self):
        self.assertEqual(self.unit.capacity, ())
        self.assertEqual(self.unit.span, (38.0, 38.0))
        self.assertIsNotNone(self.unit.coil, self.unit.coil_problem)

    def test_every_number_is_the_sheets(self):
        s = self.SHEET
        self.assertEqual(self.unit.selection["elevation_m"], s["elevation"])
        self.assertEqual(self.unit.selection["entering_water_c"], s["water_in"])
        self.assertEqual(self.unit.selection["leaving_water_c"], s["water_out"])
        for key in ("return_c", "supply_c", "airflow_m3h", "nscc_kw", "power_kw"):
            self.assertAlmostEqual(float(self.unit.design[key]), s[key], msg=key)

    def test_the_net_figure_was_taken_not_the_gross(self):
        s = self.SHEET
        self.assertAlmostEqual(s["gross_kw"] - s["power_kw"], s["nscc_kw"], places=1)
        self.assertAlmostEqual(float(self.unit.design["nscc_kw"]), s["nscc_kw"])

    def test_the_selection_closes_on_its_own_numbers(self):
        from aicfd.coil import air_capacity_rate

        s = self.SHEET
        air = air_capacity_rate(s["airflow_m3h"], s["return_c"], s["elevation"])
        self.assertAlmostEqual(air * (s["return_c"] - s["supply_c"]),
                               s["nscc_kw"], delta=1.0)

    def test_the_dimensions_are_the_sheets(self):
        self.assertEqual(list(self.unit.size), [3.60, 1.60, 4.00])
        self.assertEqual(self.unit.weight_kg, 4000)
        self.assertEqual(self.unit.fans["count"], 8)

    def test_the_curve_passes_through_the_rated_flow(self):
        points = dict(self.unit.curve["points"])
        self.assertEqual(points[115500], 155)
        self.assertIs(self.unit.curve["measured"], False)


class WaterCarriesTheGrossDutyTest(unittest.TestCase):
    """The water side, against the sheets that state it.

    Two errors used to cancel: the design water flow was inferred by dividing
    the NET capacity by the water's rise, and `leaving_water_c` was then fed
    the net capacity too. The product was right at the design point and the
    parts were both wrong, so neither could be checked against a datasheet
    (ADR-069).
    """

    def coil(self, model):
        return equipment.load(model).coil

    def test_the_inference_matches_a_stated_flow(self):
        """Vertiv CA40NPVGT prints 402.76 l/min against 280.7 kW gross over a
        10 K rise. The two agree to 0.04%, which is what makes the inference
        usable on the units that do not print it."""
        stated = 402.76 * 60 / 3600 * 4.18          # l/min -> kW/K
        inferred = 280.7 / 10.0                     # gross / rise
        self.assertAlmostEqual(stated, inferred, delta=0.05)

    def test_a_stated_flow_is_used_where_it_is_given(self):
        unit = equipment.load("FWCV36L2F")
        self.assertEqual(unit.selection["water_flow_lh"], 41430)
        self.assertAlmostEqual(unit.coil.water_max, 41430 / 3600 * 4.18, delta=0.05)

    def test_the_leaving_water_reproduces_the_sheet(self):
        """Both units print what the water leaves at. The model has to agree
        at the design point or its water side means nothing."""
        for model, net, sheet in (("FWCV36L2F", 453.7, 30.0),
                                  ("CA80NPVG6", 432.6, 28.0)):
            with self.subTest(model=model):
                self.assertAlmostEqual(
                    self.coil(model).leaving_water_c(net), sheet, delta=0.1)

    def test_the_fans_heat_the_water_even_with_no_cooling_load(self):
        """The array runs whatever the room asks, so the water carries its
        power. Zero used to read as water leaving at the temperature it
        entered, which is a unit that is not running."""
        coil = self.coil("CA80NPVG6")
        self.assertGreater(coil.leaving_water_c(0.0), coil.water_c)

    def test_no_existing_capacity_moves(self):
        """The change is to the water side and the parts that were wrong
        cancelled, so every capacity this tool has ever reported stands."""
        coil = self.coil("CA80NPVG6")
        for return_c, expected in ((30.0, 323.5), (34.0, 431.4),
                                   (38.0, 539.2), (42.0, 647.0)):
            with self.subTest(return_c=return_c):
                self.assertAlmostEqual(
                    coil.operate(return_c, coil.air_fitted).ceiling_kw,
                    expected, delta=0.5)


class ShippedSpringerTest(unittest.TestCase):
    """Springer Tech 396FWA500, and the supply-air convention it exposed.

    This sheet's "Supply air Dry condition" is the air off the COIL, not off
    the unit: 116,000 m3/h from 38.0 to 24.4 degC carries the sheet's GROSS
    462 kW. Taken at face value it would put air 0.76 K colder than the unit
    really delivers onto the fan patch, and quote a capacity the room never
    receives. What this file carries is the unit's discharge.
    """

    def setUp(self):
        self.unit = equipment.load("396FWA500")

    def test_it_carries_the_units_discharge_not_the_coils(self):
        from aicfd.coil import air_capacity_rate

        air = air_capacity_rate(116000.0, 38.0, 661.0)
        off_coil = 24.4                                   # what the sheet prints
        self.assertAlmostEqual(air * (38.0 - off_coil), 462.0, delta=1.0)
        self.assertAlmostEqual(float(self.unit.design["supply_c"]),
                               off_coil + 25.8 / air, delta=0.02)

    def test_the_selection_closes_on_the_net_figure(self):
        from aicfd.coil import air_capacity_rate

        air = air_capacity_rate(116000.0, 38.0, 661.0)
        self.assertAlmostEqual(air * (38.0 - 25.15), 436.2, delta=1.0)

    def test_the_water_side_agrees_independently(self):
        """39.6 m3/h over a 10 K rise is 460 kW: the gross, a third time."""
        self.assertAlmostEqual(self.unit.coil.water_max, 39600 / 3600 * 4.18,
                               delta=0.05)
        self.assertAlmostEqual(self.unit.coil.leaving_water_c(436.2), 28.0,
                               delta=0.1)

    def test_every_number_is_the_sheets(self):
        self.assertEqual(list(self.unit.size), [3.40, 1.50, 4.00])
        self.assertEqual(self.unit.weight_kg, 4000)
        self.assertEqual(self.unit.fans["count"], 10)
        self.assertEqual(self.unit.selection["elevation_m"], 661)
        self.assertEqual(self.unit.selection["esp_pa"], 150)
        self.assertAlmostEqual(float(self.unit.design["nscc_kw"]), 436.2)
        self.assertAlmostEqual(float(self.unit.design["power_kw"]), 25.8)


class EveryShippedUnitTest(unittest.TestCase):
    """The admission rule: only sheets that close get into the library.

    Whatever is here is real data behind somebody's report, and a sheet that
    does not agree with itself cannot be made to by entering it anyway. Four
    have been turned away on these checks -- a CM500W with its total and
    sensible capacities transposed, a Delta coil selection that is not a unit,
    an FA126HC whose air side misses by 4.4%, and a CA40NPVGT read at the
    wrong elevation -- and each was sent back to its vendor instead (ADR-071).
    """

    def test_every_shipped_unit_fits_a_coil(self):
        """Both kinds. A direct-expansion evaporator is the same exchanger
        with one side boiling, and it is fitted from the psychrometry of its
        own selection (ADR-103) -- it used to be carried and never asked."""
        for model in equipment.SHIPPED:
            unit = equipment.load(model)
            with self.subTest(model=model, cooling=unit.cooling):
                self.assertIsNotNone(unit.coil, unit.coil_problem)

    def test_every_coil_reproduces_the_selection_it_was_fitted_to(self):
        """The one thing a fit must do. A coil that does not give back its own
        design point answers nothing else credibly either."""
        from aicfd.coil import air_capacity_rate

        for model in equipment.SHIPPED:
            unit = equipment.load(model)
            design = unit.design
            air = air_capacity_rate(float(design["airflow_m3h"]),
                                    float(design["return_c"]),
                                    float(unit.selection["elevation_m"]))
            point = unit.coil.operate(float(design["return_c"]), air)
            with self.subTest(model=model):
                self.assertAlmostEqual(point.ceiling_kw, float(design["nscc_kw"]),
                                       delta=0.02 * float(design["nscc_kw"]))
                self.assertAlmostEqual(point.supply_c, float(design["supply_c"]),
                                       delta=0.3)

    def test_their_air_side_closes_on_the_net_figure(self):
        """Airflow x density x cp x dT is the net sensible capacity. Every
        manufacturer whose sheet is admitted here agrees within 1%."""
        from aicfd.coil import air_capacity_rate

        for model in equipment.SHIPPED:
            with self.subTest(model=model):
                unit = equipment.load(model)
                design, altitude = unit.design, unit.selection["elevation_m"]
                air = air_capacity_rate(float(design["airflow_m3h"]),
                                        float(design["return_c"]), float(altitude))
                heat = air * (float(design["return_c"]) - float(design["supply_c"]))
                stated = float(design["nscc_kw"])
                self.assertLess(abs(heat - stated) / stated, 0.01)

    def test_a_stated_water_flow_carries_the_gross_duty(self):
        """Where the sheet gives its flow, it has to agree with net plus fan
        power over the water's rise -- the third independent check, and the
        one that catches a supply temperature read off the wrong side of the
        fans (ADR-069, ADR-070)."""
        for model in equipment.SHIPPED:
            unit = equipment.load(model)
            flow = (unit.selection or {}).get("water_flow_lh")
            if not flow:
                continue
            with self.subTest(model=model):
                rise = (float(unit.selection["leaving_water_c"])
                        - float(unit.selection["entering_water_c"]))
                gross = (float(unit.design["nscc_kw"])
                         + float(unit.design["power_kw"]))
                self.assertAlmostEqual(float(flow) / 3600 * 4.18 * rise, gross,
                                       delta=0.01 * gross)

    def test_every_unit_states_what_a_report_will_quote(self):
        for model in equipment.SHIPPED:
            with self.subTest(model=model):
                unit = equipment.load(model)
                for field in ("return_c", "supply_c", "airflow_m3h",
                              "nscc_kw", "power_kw"):
                    self.assertIsNotNone(unit.design.get(field), field)
                for field in ("elevation_m", "esp_pa"):
                    self.assertIsNotNone(unit.selection.get(field), field)
                # What the heat leaves into: water temperatures for a coil,
                # the outdoor air for a condenser. One or the other, always.
                needed = (("entering_water_c", "leaving_water_c")
                          if unit.cooling == "chilled_water"
                          else ("outside_air_c",))
                for field in needed:
                    self.assertIsNotNone(unit.selection.get(field), field)
                self.assertEqual(len(unit.size), 3)
                self.assertTrue(all(v > 0 for v in unit.size))

    def test_they_all_reproduce_their_own_leaving_water(self):
        for model in equipment.SHIPPED:
            unit = equipment.load(model)
            if unit.cooling != "chilled_water":
                continue
            with self.subTest(model=model):
                net = float(unit.design["nscc_kw"])
                self.assertAlmostEqual(
                    unit.coil.leaving_water_c(net),
                    float(unit.selection["leaving_water_c"]), delta=0.15)


class GrossDetectorDoesNotOverreachTest(unittest.TestCase):
    """The gross/net branch has to EXPLAIN the gap, not land near it.

    Shipped too loose: it fired whenever the stated figure less the fan power
    came within the same 3% tolerance, which on a unit whose fans are 7% of
    its capacity is satisfied by almost any near miss. It then reported a
    confident, wrong diagnosis on the FA126HC -- a sheet whose net figure is
    right (its water side and its own gross-less-power both say so) and whose
    airflow is not.
    """

    def unit(self, model, selection, design):
        spec = yaml.safe_load((equipment.LIBRARY / "CA80NPVG6.yaml").read_text())
        spec["model"] = model
        spec["selection"] = selection
        spec["design"] = design
        spec.pop("capacity")
        return equipment.parse(spec)

    FA126HC = (
        dict(elevation_m=661, esp_pa=100, entering_water_c=20, leaving_water_c=30,
             entering_air_rh=25, water_flow_lh=44640),
        dict(return_c=36.3, supply_c=23.9, airflow_m3h=126224.0,
             nscc_kw=481.7, power_kw=34.89),
    )

    def test_it_does_not_fire_on_a_sheet_whose_net_figure_is_right(self):
        why = self.unit("FA126HC", *self.FA126HC).coil_problem
        self.assertNotIn("GROSS", why)
        self.assertIn("Two things would close it", why)

    def test_that_sheets_water_side_confirms_its_net_figure(self):
        """12.4 L/s over a 10 K rise is 518 kW, its gross; less the 34.89 kW
        of fans that is its net. The air side is the odd one out."""
        self.assertAlmostEqual(12.4 * 4.18 * 10.0, 516.6, delta=2.0)
        self.assertAlmostEqual(516.6 - 34.89, 481.7, places=1)

    def test_it_still_fires_where_the_fan_power_really_explains_the_gap(self):
        water = dict(entering_water_c=18, leaving_water_c=28,
                     entering_air_rh=30, esp_pa=100)
        design = dict(return_c=37.0, supply_c=21.8, airflow_m3h=60504.0,
                      nscc_kw=280.7, power_kw=10.6)
        for elevation in (661, 750):
            with self.subTest(elevation=elevation):
                why = self.unit("CA40NPVGT", dict(water, elevation_m=elevation),
                                design).coil_problem
                self.assertIn("GROSS figure was taken", why)

    def test_it_never_fires_when_the_stated_figure_is_below_the_heat(self):
        """Taking the gross for the net always overstates. A selection that
        understates is a different mistake and must not be labelled this one."""
        why = self.unit(
            "UNDER",
            dict(elevation_m=750, esp_pa=100, entering_water_c=18,
                 leaving_water_c=28, entering_air_rh=30),
            dict(return_c=38.0, supply_c=24.0, airflow_m3h=108986.0,
                 nscc_kw=390.0, power_kw=26.2),
        ).coil_problem
        self.assertNotIn("GROSS", why)


class DerivedFieldsAreDeclaredTest(unittest.TestCase):
    """A unit read out of an ambiguous sheet is weaker evidence than one that
    closed on its own, and a report quoting them side by side has to say so
    (ADR-071)."""

    def test_the_trane_declares_its_derived_supply(self):
        unit = equipment.load("DFWA5560")
        self.assertEqual(unit.derived_fields, ("supply_c",))
        self.assertIn("derived", unit.to_dict())

    def test_the_sheets_that_close_declare_nothing(self):
        for model in equipment.SHIPPED:
            if model == "DFWA5560":
                continue
            with self.subTest(model=model):
                self.assertEqual(equipment.load(model).derived_fields, ())

    def test_a_derived_field_must_name_a_real_design_field(self):
        for model in equipment.SHIPPED:
            unit = equipment.load(model)
            for name in unit.derived_fields:
                with self.subTest(model=model, field=name):
                    self.assertIsNotNone(unit.design.get(name))

    def test_the_declared_field_is_what_makes_that_sheet_close(self):
        """The supply as printed, 24 degC, misses by 3.5%; what is carried
        closes. Naming it is the whole difference between the two."""
        from aicfd.coil import air_capacity_rate

        unit = equipment.load("DFWA5560")
        air = air_capacity_rate(126224.0, 36.3, 661.0)
        self.assertGreater(abs(air * (36.3 - 24.0) - 473.6) / 473.6, 0.03)
        self.assertAlmostEqual(
            air * (36.3 - float(unit.design["supply_c"])), 473.6, delta=1.0)

    def test_a_report_says_so_where_a_unit_has_one(self):
        line = post._coil_provenance(
            {"coil_model": {"air_split_pct": 62, "reference_selections": 0,
                            "reference_error_k": None,
                            "derived_fields": ["supply_c"]}})
        self.assertIn("did not state supply_c", line)
        self.assertIn("read out of the sheet", line)

    def test_and_says_nothing_where_it_has_none(self):
        line = post._coil_provenance(
            {"coil_model": {"air_split_pct": 62, "reference_selections": 0,
                            "reference_error_k": None, "derived_fields": []}})
        self.assertNotIn("read out of the sheet", line)


class ArrangementTest(unittest.TestCase):
    """A downflow unit's coil is right and its geometry is not modelled.

    The library carries room units because their coils are the same physics.
    The geometry side builds a wall of fans in a mechanical gallery, so naming
    one as a case's fanwall.model would give the right coil in the wrong shape
    of room -- a wrong answer that looks like a right one (ADR-072).
    """

    DOWNFLOW = ("HDCV5300F-HT", "HXCV5000F-HT", "39CRA150", "IDAV1911F")

    def test_the_room_units_declare_themselves(self):
        for model in self.DOWNFLOW:
            with self.subTest(model=model):
                self.assertEqual(equipment.load(model).arrangement, "downflow")

    def test_everything_else_is_a_fan_wall_by_default(self):
        for model in equipment.SHIPPED:
            if model in self.DOWNFLOW:
                continue
            with self.subTest(model=model):
                self.assertEqual(equipment.load(model).arrangement, "fanwall")

    def test_a_case_cannot_build_a_fan_wall_from_one(self):
        from aicfd.model import equipment_for

        for model in self.DOWNFLOW:
            with self.subTest(model=model):
                with self.assertRaises(ValueError) as caught:
                    equipment_for({"fanwall": {"model": model}})
                said = str(caught.exception)
                # A DX room unit is refused on the coil before the geometry;
                # either refusal names what is right about the file as well as
                # what is wrong, so nobody deletes a good one.
                self.assertTrue("discharges downward" in said
                                or "chilled-water one" in said, said)
                self.assertIn("in the library", said)

    def test_a_room_unit_is_what_a_raised_floor_wants(self):
        """The same file that is wrong for a gallery wall is right for an
        access floor, and the refusal names which room it needs (ADR-076)."""
        from aicfd.model import equipment_for

        spec = {"fanwall": {"model": "39CRA150"},
                "floor": {"enabled": True, "height": 1.0}}
        self.assertEqual(equipment_for(spec).arrangement, "downflow")
        with self.assertRaises(ValueError) as caught:
            equipment_for({"fanwall": {"model": "CA80NPVG6"},
                           "floor": {"enabled": True, "height": 1.0}})
        said = str(caught.exception)
        self.assertIn("wall of fans", said)
        self.assertIn("this case has one", said)

    def test_a_fan_wall_still_builds(self):
        from aicfd.model import equipment_for

        unit = equipment_for({"fanwall": {"model": "CA80NPVG6"}})
        self.assertEqual(unit.arrangement, "fanwall")

    def test_the_room_units_still_pass_every_admission_check(self):
        """Being unplaceable is not being unverified: they are in the library
        because their sheets close (ADR-071)."""
        from aicfd.coil import air_capacity_rate

        for model in self.DOWNFLOW:
            with self.subTest(model=model):
                unit = equipment.load(model)
                design = unit.design
                air = air_capacity_rate(float(design["airflow_m3h"]),
                                        float(design["return_c"]),
                                        float(unit.selection["elevation_m"]))
                heat = air * (float(design["return_c"]) - float(design["supply_c"]))
                self.assertLess(abs(heat - float(design["nscc_kw"]))
                                / float(design["nscc_kw"]), 0.01)


class DirectExpansionTest(unittest.TestCase):
    """A CRAC is modelled, at every return, and says what it assumed.

    Its evaporator is the same heat exchanger as a chilled-water coil with one
    side BOILING: the refrigerant holds its temperature while it changes
    phase, so the counterflow relation collapses to `1 - exp(-NTU)` measured
    from the coil's apparatus dew point. What that does NOT model is the
    condensing side, which follows the outdoor air (ADR-103).

    It used to be refused outright, which left every DX case answered at its
    plate figure, uncoupled from the room, and told to ask the manufacturer.
    """

    def test_the_shipped_crac_declares_itself(self):
        unit = equipment.load("IDAV1911F")
        self.assertEqual(unit.cooling, "dx")
        self.assertEqual(unit.arrangement, "downflow")
        self.assertIsNotNone(unit.coil, unit.coil_problem)

    def test_everything_else_is_chilled_water(self):
        for model in equipment.SHIPPED:
            if model == "IDAV1911F":
                continue
            with self.subTest(model=model):
                self.assertEqual(equipment.load(model).cooling, "chilled_water")

    def test_its_selection_still_closes_on_the_air_side(self):
        """0.07%. Being unmodellable is not being unverified."""
        from aicfd.coil import air_capacity_rate

        air = air_capacity_rate(14215.0, 30.0, 661.0)
        self.assertAlmostEqual(air * (30.0 - 17.9), 51.7, delta=0.5)

    def test_the_fans_net_the_capacity_not_the_compressor(self):
        """53.5 gross less 1.8 kW of FANS is the 51.7 net. The compressor's
        17.6 kW and the condenser's 0.9 leave through the refrigerant and
        never reach this air, so netting with the 19.3 kW the unit absorbs
        would understate it by a third."""
        unit = equipment.load("IDAV1911F")
        self.assertAlmostEqual(float(unit.design["power_kw"]), 1.8)
        self.assertAlmostEqual(53.5 - 1.8, float(unit.design["nscc_kw"]), places=1)

    def test_it_states_the_outdoor_air_its_capacity_depends_on(self):
        unit = equipment.load("IDAV1911F")
        self.assertEqual(unit.selection["outside_air_c"], 38.8)
        self.assertNotIn("entering_water_c", unit.selection)

    def test_a_case_cannot_build_a_fan_wall_from_it(self):
        """Still refused, and now for the reason that is actually true of it.

        It used to be refused for being DX, which was too wide: what is not
        modelled is its COIL, and a room needs none of that (ADR-097). What
        still stops it here is its ARRANGEMENT -- it stands in the room and
        discharges downward, so it needs a raised floor (ADR-072).
        """
        from aicfd.model import equipment_for

        with self.assertRaises(ValueError) as caught:
            equipment_for({"fanwall": {"model": "IDAV1911F"}})
        self.assertIn("raised floor", str(caught.exception))
        self.assertNotIn("chilled-water one", str(caught.exception),
                         "being DX is no longer the reason")


class ShippedListsAreHonestTest(unittest.TestCase):
    """What the repository ships is declared, not discovered (ADR-077).

    The library-wide guards used to walk the FOLDER, which on a working
    installation holds the engineer's own drafts as well. A unit somebody was
    halfway through entering then failed the build -- the third time a test
    has read a user's files and broken on them (ADR-056, ADR-061).
    """

    def libraries(self):
        from aicfd import components, racklib

        return ((equipment, "equipment"), (racklib, "racks"),
                (components, "components"))

    def test_every_declared_id_has_a_file(self):
        for module, what in self.libraries():
            for name in module.SHIPPED:
                with self.subTest(library=what, id=name):
                    self.assertTrue((module.LIBRARY / f"{name}.yaml").is_file())

    def test_the_declarations_are_sorted_and_unique(self):
        """So a reader can see at a glance what is there, and a second copy of
        an id cannot hide in the middle."""
        for module, what in self.libraries():
            with self.subTest(library=what):
                self.assertEqual(list(module.SHIPPED), sorted(set(module.SHIPPED)))

    def test_a_users_own_file_does_not_enter_the_guards(self):
        """The whole point. A draft in the folder is the engineer's business;
        the page tells them what does not close, the build does not."""
        draft = equipment.LIBRARY / "ZZ-USER-DRAFT.yaml"
        draft.write_text("model: ZZ-USER-DRAFT\nfamily: Draft\n"
                         "size: [1, 1, 1]\nselection: {}\ndesign: {}\n")
        self.addCleanup(lambda: draft.exists() and draft.unlink())
        self.assertIn("ZZ-USER-DRAFT", equipment.available())
        self.assertNotIn("ZZ-USER-DRAFT", equipment.SHIPPED)


class UnitPickerTest(unittest.TestCase):
    """Which unit a case uses is a choice on the page, and the page's verdict
    on each option is the generator's.

    The group showed the unit's name and linked to its datasheet, and that was
    everything: `fanwall.model` was not an editable field, so a reader who
    opened the equipment page, found their CRAH and selected it there changed
    the library page in front of them and nothing about their case. The model
    page came back with the same unit and no way to change it (ADR-092).

    The risk in fixing it is a second opinion. The picker says beside each
    option whether it suits this room; the generator refuses the ones that do
    not. Two statements of one rule drift, and the drift a reader meets is a
    picker offering a machine the Apply then rejects -- or, worse, one it
    marks unusable that would have worked. So the rule is
    `model.equipment_mismatch`, both read it, and this test holds the pair
    together against every unit in the library and every shipped case.
    """

    def specs(self):
        from tests import support

        for name in support.SHIPPED_CASES:
            yield name, yaml.safe_load(
                (support.REPO / "cases" / f"{name}.yaml").read_text())

    def test_the_picker_offers_every_unit_in_the_library(self):
        from aicfd.model import equipment_in_use

        for name, spec in self.specs():
            with self.subTest(case=name):
                offered = {o["model"] for o in equipment_in_use(spec)["options"]}
                self.assertEqual(
                    offered, set(equipment.available()),
                    "the picker hides a unit. Somebody looking for their CRAH "
                    "learns only that the software has never heard of it",
                )

    def test_the_picker_and_the_generator_agree_on_every_unit(self):
        from aicfd.model import equipment_in_use

        for name, spec in self.specs():
            for option in equipment_in_use(spec)["options"]:
                with self.subTest(case=name, unit=option["model"]):
                    trial = copy.deepcopy(spec)
                    trial.setdefault("fanwall", {})["model"] = option["model"]
                    try:
                        equipment_for(trial)
                    except ValueError as refused:
                        accepted, why = False, str(refused)
                    else:
                        accepted, why = True, None
                    self.assertEqual(
                        option["suits"], accepted,
                        f"the picker says suits={option['suits']} and the "
                        f"generator says {accepted}: {option['why'] or why}",
                    )
                    if not accepted:
                        self.assertEqual(
                            option["why"], why,
                            "the picker gives a different reason than the "
                            "refusal the reader will get from Apply",
                        )

    def test_the_case_its_own_unit_names_is_one_that_suits_it(self):
        from aicfd.model import equipment_in_use

        for name, spec in self.specs():
            chosen = (spec.get("fanwall") or {}).get("model")
            if not chosen:
                continue
            with self.subTest(case=name):
                option = next(o for o in equipment_in_use(spec)["options"]
                              if o["model"] == chosen)
                self.assertTrue(
                    option["suits"],
                    f"cases/{name}.yaml names {chosen}, which the picker "
                    f"marks as unusable here: {option['why']}",
                )

    def test_the_field_accepts_a_unit_in_the_library_and_refuses_one_that_is_not(self):
        spec = dict(yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "hall-double-gallery.yaml").read_text()))
        other = next(m for m in equipment.available()
                     if m != spec["fanwall"]["model"]
                     and equipment.load(m).arrangement == "fanwall"
                     and equipment.load(m).cooling == "chilled_water")

        changed, rejected = server.apply_changes(copy.deepcopy(spec),
                                                 {"fan_model": other})
        self.assertEqual(rejected, [])
        self.assertEqual(changed["fanwall"]["model"], other)

        changed, rejected = server.apply_changes(copy.deepcopy(spec),
                                                 {"fan_model": "no-such-unit"})
        self.assertTrue(rejected, "a unit that is not in the library was taken")
        self.assertIn("no-such-unit", rejected[0])
        self.assertEqual(changed["fanwall"]["model"], spec["fanwall"]["model"],
                         "a refused name must leave the case's unit alone")

    def test_naming_no_unit_is_a_real_answer(self):
        """A case describes its plant by the numbers typed into it until it
        names a unit, which is how a case starts. The empty option has to get
        back to that rather than be ignored."""
        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "hall-double-gallery.yaml").read_text())
        changed, rejected = server.apply_changes(copy.deepcopy(spec),
                                                 {"fan_model": ""})
        self.assertEqual(rejected, [])
        self.assertIsNone(changed["fanwall"]["model"])
        self.assertIsNone(equipment_for(changed))


class ChangingTheUnitTest(unittest.TestCase):
    """Naming another machine brings its numbers with it.

    `equipment_for` fills only the keys a case leaves blank: the library is a
    default, not a lock, and a value typed over it is a decision that stays
    visible (ADR-036). That is right for a case somebody wrote by hand, and
    wrong the moment the unit can be CHANGED from the page -- every key is
    already filled with the previous machine's figures, so picking another one
    changed the name and nothing else.

    What that produced, in a real hall: `model: 39CRA150` carrying the fan
    wall's 432.6 kW, 121,545 m3/h, 3.96 m and 21.4 kW against the CRAH's
    actual 145 kW, 33,700 m3/h, 2.73 m and 6.0 kW. Four machines rated at
    three times what they are, under the right name, and twelve green checks
    on the answer (ADR-094).
    """

    DATASHEET = ("airflow_m3h", "capacity_kw", "power_kw", "supply_temp_c",
                 "width", "depth", "height", "static_pressure_pa", "curve")

    def hall(self) -> dict:
        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "hall-double-gallery.yaml").read_text())
        spec["floor"] = {"enabled": True, "height": 1.0, "tiles_per_rack": 1}
        spec.pop("plenum", None)
        return spec

    def test_the_mapping_has_one_statement(self):
        """`equipment_defaults` has three readers now: the fill, the page and
        the forget. Three copies would put three machines in front of one
        reader."""
        from aicfd import model as model_module

        source = inspect.getsource(model_module)
        self.assertEqual(
            source.count('"capacity_kw": point["nscc_kw"]'), 1,
            "the unit-to-spec mapping is written more than once",
        )

    def test_changing_the_unit_replaces_the_previous_one_s_numbers(self):
        spec = self.hall()
        was = dict(spec["fanwall"])
        changed, rejected = server.apply_changes(copy.deepcopy(spec),
                                                 {"fan_model": "39CRA150"})
        self.assertEqual(rejected, [])
        build_model(changed)  # the build is what refills them
        fresh = equipment.load("39CRA150")
        from aicfd.model import equipment_defaults

        wanted = equipment_defaults(fresh)
        for key in self.DATASHEET:
            with self.subTest(key=key):
                self.assertEqual(
                    changed["fanwall"].get(key), wanted[key],
                    f"{key} is still the previous unit's "
                    f"({was.get(key)!r}) after naming another machine",
                )

    def test_the_curve_changes_too(self):
        """It has no field on the page, so only the server can replace it --
        and a stale P-Q curve is the previous machine's fan."""
        spec = self.hall()
        changed, _ = server.apply_changes(copy.deepcopy(spec),
                                          {"fan_model": "39CRA150"})
        build_model(changed)
        self.assertNotEqual(changed["fanwall"]["curve"], spec["fanwall"]["curve"])
        self.assertEqual(
            [list(p) for p in changed["fanwall"]["curve"]],
            [list(p) for p in equipment.load("39CRA150").curve["points"]],
        )

    def test_re_applying_the_same_unit_keeps_what_was_typed_over_it(self):
        """The form sends `fan_model` on EVERY Apply, so clearing on every
        send would wipe an override the first time anything else was saved."""
        spec = self.hall()
        spec["fanwall"]["airflow_m3h"] = 99_000
        changed, _ = server.apply_changes(
            copy.deepcopy(spec),
            {"fan_model": spec["fanwall"]["model"], "fan_count": 6})
        self.assertEqual(changed["fanwall"]["airflow_m3h"], 99_000)
        self.assertEqual(changed["fanwall"]["count"], 6)

    def test_a_value_sent_with_the_change_wins_over_the_datasheet(self):
        """Switching unit and typing a figure in the same Apply is one edit,
        and the typed one is the statement."""
        changed, _ = server.apply_changes(
            self.hall(), {"fan_model": "39CRA150", "airflow_m3h": 28_000})
        build_model(changed)
        self.assertEqual(changed["fanwall"]["airflow_m3h"], 28_000)
        self.assertEqual(changed["fanwall"]["capacity_kw"],
                         equipment.load("39CRA150").design_point(None)["nscc_kw"])

    def test_every_option_carries_the_numbers_the_page_shows(self):
        from aicfd.model import equipment_defaults, equipment_in_use

        for option in equipment_in_use(self.hall())["options"]:
            with self.subTest(unit=option["model"]):
                wanted = {k: v for k, v in
                          equipment_defaults(equipment.load(option["model"])).items()
                          if v is not None}
                self.assertEqual(option["defaults"], wanted)
