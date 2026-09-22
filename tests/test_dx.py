"""A direct-expansion CRAC runs, and its evaporator is modelled.

ADR-073 carried DX units in the library and checked their sheets, and
`equipment_for` refused to build a case from one. ADR-097 let the case run, at
the unit's RATED point, with the coil left unmodelled -- so the supply
temperature was held wherever the sheet put it, the coupled solve was skipped,
and the report told the reader to ask the manufacturer what the machine does at
the return the room gives it.

ADR-103 finished the job. A DX evaporator is the same finned bank as a
chilled-water coil with one side BOILING, and it is recovered from the
psychrometry of the same selection: the apparatus dew point its sensible/total
split implies. So the machine answers at any return, the room and the plant are
solved together, and `control: team` means something for a CRAC plant too.

What is still NOT modelled is the CONDENSING side: capacity follows the outdoor
air the condenser rejects into, and one selection cannot say how much. That is
said once, in the report's limitations -- not in the alerts card, which is for
what THIS plant fails to do (ADR-098).
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
    spec = support.spec("pod-raised-floor")
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

    def test_the_build_does_NOT_put_it_in_the_alerts(self):
        """It is a limitation of the MODEL, not a design criterion the plant
        misses, and it does not change from run to run. Arriving in the
        alerts card on every run was three paragraphs of boilerplate in the
        place meant for what this plant fails to do (ADR-098)."""
        _unit, built = self.built()
        self.assertNotIn("RATED", " ".join(built.alerts))
        self.assertNotIn("refrigerant", " ".join(built.alerts))

    def test_the_report_names_the_condenser_as_what_is_left(self):
        """The evaporator is modelled and the limitation says so, so that a
        reader knows which half of the machine the number rests on."""
        from aicfd import report

        class Stub:
            payload = {
                "model": {"floor_height": 1.0},
                "kpis": {"unit_model": "X1", "coil_model": {
                    "kind": "dx", "adp_c": 10.5, "rated_ambient_c": 37.6,
                    "assumptions": [],
                }},
            }

        said = " ".join(report._model_limits(Stub()))
        self.assertIn("X1", said)
        self.assertIn("EVAPORATOR is modelled", said)
        self.assertIn("CONDENSING side is not", said)
        self.assertIn("37.6", said)
        self.assertNotIn("ask the manufacturer", said)

    def test_every_assumption_the_fit_made_is_listed_too(self):
        from aicfd import report

        class Stub:
            payload = {
                "model": {"floor_height": 1.0},
                "kpis": {"unit_model": "X1", "coil_model": {
                    "kind": "dx", "adp_c": 11.5, "rated_ambient_c": 38.8,
                    "assumptions": ["the coil is dry"],
                }},
            }

        self.assertIn("assumed that the coil is dry",
                      " ".join(report._model_limits(Stub())))

    def test_a_unit_whose_coil_could_not_be_fitted_still_says_so(self):
        """The old sentence, kept for the case it is now true of: a file that
        does not carry what the fit needs."""
        from aicfd import report

        class Stub:
            payload = {
                "model": {"floor_height": 1.0},
                "kpis": {"unit_model": "X1", "rated_return_c": 30.0,
                         "rated_nscc_kw": 100.5,
                         "coil_problem": "X1 does not say how humid the air was"},
            }

        said = " ".join(report._model_limits(Stub()))
        self.assertIn("RATED", said)
        self.assertIn("does not say how humid", said)

    def test_a_chilled_water_report_carries_none_of_it(self):
        from aicfd import report

        class Stub:
            payload = {"model": {"floor_height": 1.0},
                       "kpis": {"unit_model": "Y",
                                "coil_model": {"kind": "chilled_water"}}}

        self.assertNotIn("direct-expansion", " ".join(report._model_limits(Stub())))

    def test_the_limitation_is_read_off_the_coil_and_not_written_twice(self):
        """It used to be a `dx_limitation` function in the model AND a
        sentence in the report, both naming the refrigerant circuit and both
        to keep in step. The coil describes itself now, and that is the only
        source (ADR-103)."""
        self.assertFalse(hasattr(m, "dx_limitation"))
        described = equipment.load("P3100DA").coil.describe()
        self.assertEqual(described["kind"], "dx")
        self.assertIn("rated_ambient_c", described)


class RatingAgainstTheRoomTest(unittest.TestCase):
    """The plate figure holds at one return, and the report says what the
    machine does at the return the room actually produced (ADR-103)."""

    def alerts(self, actual: float, rated: float = 30.0,
               available: float = 41.7) -> list[str]:
        return post._coil_alerts({
            "unit_model": "X",
            "coil_model": {"kind": "dx"},
            "fans": [{"name": "fan1"}],
            "available_kw": available,
            "rated_return_c": rated,
            "rated_nscc_kw": 51.7,
            "return_temp_c": actual,
        })

    def test_a_room_below_the_rating_is_told_what_it_really_gets(self):
        said = " ".join(self.alerts(21.8))
        self.assertIn("8.2 K", said)
        self.assertIn("below", said)
        self.assertIn("41.7 kW", said, "the capacity the coil actually gives")
        self.assertIn("81%", said.replace(" %", "%"))
        self.assertNotIn("ask the manufacturer", said)

    def test_a_room_above_the_rating_is_said_the_other_way(self):
        self.assertIn("above", " ".join(self.alerts(34.0, available=60.0)))

    def test_a_room_at_the_rating_says_nothing_at_all(self):
        self.assertEqual(self.alerts(30.5, available=52.0), [],
                         "within 2 K of the plate there is nothing to add, "
                         "and the limitation itself is in the report")

    def test_a_unit_with_no_coil_falls_back_to_the_old_sentence(self):
        said = " ".join(post._coil_alerts({
            "unit_model": "X",
            "coil_problem": "X does not state its gross capacities",
            "rated_return_c": 30.0,
            "rated_nscc_kw": 51.7,
            "return_temp_c": 21.8,
        }))
        self.assertIn("plate figure", said)
        self.assertIn("does not carry", said)

    def test_a_chilled_water_result_never_gets_this_alert(self):
        self.assertFalse(post._coil_alerts({
            "unit_model": "CA80NPVG6", "return_temp_c": 34.0,
            "coil_model": {"kind": "chilled_water"},
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
        self.assertEqual(built.supply_temp_c, 20.0, "it runs off its own sheet")


if __name__ == "__main__":
    unittest.main()
