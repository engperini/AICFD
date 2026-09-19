"""The equipment library, and the number it exists to produce.

A fan wall's datasheet prints one capacity, true at the one return air
temperature the unit was selected for. A room almost never returns air at that
temperature, so judging a plant against the catalogue figure judges it against
something it will not do. These defend the table that replaces it (ADR-036).
"""

from __future__ import annotations

import copy
import unittest

import yaml

from aicfd import equipment, post
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
        for broken, why in (
            ({"capacity": RAW["capacity"][:1]}, "one row cannot be interpolated"),
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
    """The headline the table produces: what the plant uses of what it has."""

    def setUp(self):
        self.model = build_model(copy.deepcopy(SPEC))

    def _fans(self, *pairs):
        return [
            {"name": f"fan{i + 1}", "return_temp_c": t, "heat_kw": q}
            for i, (t, q) in enumerate(pairs)
        ]

    def test_each_unit_is_judged_at_its_own_return_temperature(self):
        fans = self._fans((36.0, 400.0), (40.0, 600.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 1000.0})
        self.assertAlmostEqual(fans[0]["available_kw"], 545.0)
        self.assertAlmostEqual(fans[1]["available_kw"], 697.6)
        self.assertAlmostEqual(fans[0]["of_available_pct"], 73.4, places=1)
        self.assertAlmostEqual(out["available_kw"], 1242.6)
        self.assertAlmostEqual(out["utilisation_pct"], 80.5, places=1)
        self.assertEqual(out["units_over_capacity"], 0)

    def test_a_unit_beyond_its_own_coil_is_counted(self):
        fans = self._fans((35.0, 600.0), (35.0, 100.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 700.0})
        self.assertEqual(out["units_over_capacity"], 1)
        self.assertGreater(fans[0]["of_available_pct"], 100)

    def test_the_catalogue_figure_is_reported_separately_not_instead(self):
        """Both numbers, named. A reader who has only seen the catalogue one
        will otherwise read this as it and conclude the opposite."""
        fans = self._fans((38.0, 500.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 500.0})
        self.assertAlmostEqual(out["available_kw"], 622.7)
        self.assertAlmostEqual(out["catalogue_kw"], 622.7)  # design point here
        self.assertEqual(out["unit_model"], "CA80NPVG6")

    def test_a_return_outside_the_selections_is_left_out_and_said(self):
        fans = self._fans((33.0, 400.0), (38.0, 500.0))
        out = post.coil_capacity(self.model, fans, {"recovered_kw": 900.0})
        self.assertEqual(out["coil_outside_table_c"], [33.0])
        self.assertNotIn("available_kw", fans[0])
        self.assertAlmostEqual(out["available_kw"], 622.7)

    def test_a_case_with_no_equipment_says_nothing_at_all(self):
        spec = copy.deepcopy(SPEC)
        spec["fanwall"] = {"count": 4, "airflow_m3h": 90000, "width": 3.9,
                           "height": 3.75, "supply_temp_c": 21.0}
        plain = build_model(spec)
        self.assertEqual(post.coil_capacity(plain, self._fans((38.0, 500.0)), {}), {})


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
