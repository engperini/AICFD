"""A direct-expansion CRAC is usable, at its rated point, and says so.

ADR-073 carried DX units in the library and checked their sheets, and
`equipment_for` refused to build a case from one. The reason was sound and
too wide: what this software cannot model is a DX unit's COIL -- capacity
against return air, which follows the refrigerant circuit, the compressors'
staging and the outdoor air the condenser rejects into. Everything the ROOM
needs is on the sheet and as well defined as any chilled-water unit's: the
airflow, the supply temperature, the dimensions, the sensible capacity at the
rated return.

So a DX unit runs (ADR-097). The coupled re-solve is skipped because there is
nothing to re-ask, and the result is the room at the plant's RATED duty -- a
real answer to a real question, and not the question a chilled-water case
answers. What keeps that honest is saying it three times over, in the places
a reader actually looks:

  before the solve   an alert off the build, so it is read before the run is
                     paid for
  in the export      the coil problem in its own words
  after the solve    the return the room PRODUCED against the return the unit
                     was RATED at. Measured on the first DX case run here: a
                     unit rated 51,7 kW at 30,0 degC, in a room that returns
                     21,8 -- 8,2 K below the plate, where a DX circuit does
                     markedly less sensible work than its plate says
"""

from __future__ import annotations

import copy
import unittest

import yaml

from aicfd import equipment, model as m, post
from tests import support


def dx_units():
    return [n for n in equipment.SHIPPED
            if equipment.load(n).cooling != "chilled_water"]


def raised_floor_case(unit: str) -> dict:
    spec = yaml.safe_load(
        (support.REPO / "cases" / "pod-raised-floor.yaml").read_text())
    spec["fanwall"] = {"model": unit, "count": spec["fanwall"].get("count", 1)}
    return spec


class UsableTest(unittest.TestCase):
    def test_the_library_ships_one_to_test_with(self):
        self.assertTrue(dx_units(), "no DX unit in equipment.SHIPPED")

    def test_a_dx_unit_builds_a_case(self):
        for unit in dx_units():
            with self.subTest(unit=unit):
                if equipment.load(unit).arrangement != "downflow":
                    continue
                built = m.build_model(raised_floor_case(unit))
                self.assertEqual(built.equipment.model, unit)

    def test_it_takes_its_numbers_from_the_sheet_like_any_other_unit(self):
        for unit in dx_units():
            loaded = equipment.load(unit)
            if loaded.arrangement != "downflow":
                continue
            with self.subTest(unit=unit):
                built = m.build_model(raised_floor_case(unit))
                point = loaded.design_point(None)
                self.assertAlmostEqual(built.supply_temp_c, point["supply_c"])
                self.assertAlmostEqual(built.unit_capacity_kw, point["nscc_kw"])
                self.assertAlmostEqual(
                    built.airflow_m3h,
                    point["airflow_m3h"] * len(built.fans), places=3)

    def test_the_arrangement_rule_still_applies(self):
        """Usable is not the same as placeable anywhere: a downflow unit still
        needs a raised floor (ADR-072)."""
        for unit in dx_units():
            if equipment.load(unit).arrangement != "downflow":
                continue
            spec = raised_floor_case(unit)
            spec["floor"] = dict(spec["floor"], enabled=False)
            with self.subTest(unit=unit), self.assertRaises(ValueError) as caught:
                m.build_model(spec)
            self.assertIn("raised floor", str(caught.exception))


class SaysWhatIsNotAnsweredTest(unittest.TestCase):
    def built(self):
        unit = next(n for n in dx_units()
                    if equipment.load(n).arrangement == "downflow")
        return unit, m.build_model(raised_floor_case(unit))

    def test_the_build_alerts_before_the_solve(self):
        unit, built = self.built()
        said = " ".join(built.alerts)
        self.assertIn(unit, said)
        self.assertIn("RATED", said,
                      "the alert has to say the result is the rated point")
        self.assertIn("not modelled", said)

    def test_a_chilled_water_case_says_none_of_it(self):
        spec = yaml.safe_load(
            (support.REPO / "cases" / "pod-fanwall.yaml").read_text())
        built = m.build_model(spec)
        self.assertNotIn("RATED", " ".join(built.alerts))

    def test_the_limitation_is_one_function_with_one_reader_per_place(self):
        """`dx_limitation` is what the build says; `equipment.coil_problem` is
        what the export carries. Both name the refrigerant circuit, and
        neither is a copy of the other's wording to keep in step."""
        unit, built = self.built()
        self.assertIsNone(m.dx_limitation(None))
        self.assertIsNotNone(m.dx_limitation(built.equipment))
        chilled = equipment.load("CA80NPVG6")
        self.assertIsNone(m.dx_limitation(chilled))


class RatingAgainstTheRoomTest(unittest.TestCase):
    """The plate figure holds at one return, and a reader should not have to
    find the two numbers and subtract them."""

    def alerts(self, actual: float, rated: float = 30.0) -> list[str]:
        return post._coil_alerts({
            "coil_problem": "X is a dx unit: ... which this software does not model",
            "unit_model": "X",
            "rated_return_c": rated,
            "rated_nscc_kw": 51.7,
            "return_temp_c": actual,
        })

    def test_a_room_far_below_the_rating_is_said_so(self):
        said = " ".join(self.alerts(21.8))
        self.assertIn("8.2 K", said)
        self.assertIn("below", said)
        self.assertIn("21.8", said, "the return to ask the manufacturer about")

    def test_a_room_above_the_rating_is_said_the_other_way(self):
        self.assertIn("above", " ".join(self.alerts(34.0)))

    def test_a_room_at_the_rating_says_nothing_extra(self):
        self.assertEqual(len(self.alerts(30.5)), 1,
                         "within 2 K of the plate there is nothing to add")

    def test_a_chilled_water_result_never_gets_this_alert(self):
        self.assertFalse(post._coil_alerts({
            "unit_model": "CA80NPVG6", "return_temp_c": 34.0,
            "coil_water_out_c": None, "coil_water_out_design_c": None,
        }))


class ShippedProjectUnitTest(unittest.TestCase):
    """`equipment/` is the engineer's folder, so nothing walks it (ADR-077).

    What IS held here is the rule that let a project unit in: a unit not in
    `SHIPPED` is not held to the report-field checks, and the reason has to
    be a real one. P3100DA is carried for the Fortaleza project and is not
    declared, because its data sheet states no external static pressure.
    """

    def test_an_undeclared_unit_is_still_loadable_and_usable(self):
        draft = equipment.LIBRARY / "ZZ-DX-DRAFT.yaml"
        draft.write_text(
            "model: ZZ-DX-DRAFT\nfamily: Draft\narrangement: downflow\n"
            "cooling: dx\nsize: [2.0, 0.9, 2.0]\n"
            "selection: {elevation_m: 0, outside_air_c: 35}\n"
            "design: {return_c: 30.0, supply_c: 20.0, airflow_m3h: 20000,"
            " nscc_kw: 66.9, power_kw: 5.0}\n")
        self.addCleanup(lambda: draft.exists() and draft.unlink())
        self.assertNotIn("ZZ-DX-DRAFT", equipment.SHIPPED)
        built = m.build_model(raised_floor_case("ZZ-DX-DRAFT"))
        self.assertEqual(built.equipment.model, "ZZ-DX-DRAFT")
        self.assertIn("RATED", " ".join(built.alerts))


if __name__ == "__main__":
    unittest.main()
