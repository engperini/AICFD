"""Solving the room and the machine together.

A fan wall does not decide what air it delivers -- its coil does, from the air
the room gives it. Imposing a supply temperature and solving once answers a
question about a machine whose delivered temperature is independent of the
room feeding it, which is no machine at all (ADR-040).

What these defend is mostly the unglamorous half: writing a temperature back
onto a boundary field without corrupting it. A botched boundary file costs a
whole solve and does not announce itself -- OpenFOAM either refuses to start,
an hour after the mesh was built, or reads something plausible and wrong.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from aicfd import coupled

#: A boundary field shaped like the ones a solved run leaves behind: a patch
#: whose value is a long parenthesised list, another whose value is uniform,
#: an inletOutlet carrying both, and an empty patch on a processor that owns
#: none of its faces.
FIELD = """FoamFile
{
    version     2.0;
    format      ascii;
    class       volScalarField;
    object      T;
}

internalField   uniform 300;

boundaryField
{
    floor
    {
        type            zeroGradient;
    }
    fan1Intake
    {
        type            inletOutlet;
        inletValue      uniform 295.05;
        value           nonuniform List<scalar>
4
(
306.472
306.468
306.401
306.399
)
;
    }
    fan1Supply
    {
        type            fixedValue;
        value           uniform 295.05;
    }
    fan2Supply
    {
        type            fixedValue;
        value           nonuniform 0();
    }
    fan2Intake
    {
        type            inletOutlet;
        inletValue      uniform 295.05;
        value           uniform 306.4;
    }
}
"""


class BoundaryEditTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name)
        for where in ("3000", "processor0/3000", "processor1/3000"):
            path = self.case / where / "T"
            path.parent.mkdir(parents=True)
            path.write_text(FIELD)

    def tearDown(self):
        self.tmp.cleanup()

    def _written(self, where="3000"):
        return (self.case / where / "T").read_text()

    def test_it_writes_every_copy_the_solver_might_read(self):
        """A parallel run continues from the decomposed fields. Updating only
        the reconstructed one would leave the solve carrying the old boundary
        while every reader downstream saw the new one."""
        written = coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        self.assertEqual(written, 3)
        for where in ("3000", "processor0/3000", "processor1/3000"):
            self.assertIn("uniform 298.1500", self._written(where))

    def test_the_supply_value_is_replaced(self):
        coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        block = self._block("fan1Supply")
        self.assertIn("value           uniform 298.1500;", block)
        self.assertIn("fixedValue", block)

    def test_the_intake_inlet_value_follows_it(self):
        """The same physical quantity: what would come back if flow ever
        reversed. Left behind, it would inject the old supply temperature on
        the one iteration it mattered."""
        coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        block = self._block("fan1Intake")
        self.assertIn("inletValue      uniform 298.1500;", block)

    def test_a_long_list_after_the_edited_entry_survives_intact(self):
        """The reason this is not a regex over the whole entry: a boundary
        value is a parenthesised list spanning thousands of lines, and the
        semicolon that closes the entry is the one after the list closes."""
        coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        text = self._written()
        for value in ("306.472", "306.468", "306.401", "306.399"):
            self.assertIn(value, text)
        self.assertEqual(text.count("("), FIELD.count("("))
        self.assertEqual(text.count(")"), FIELD.count(")"))
        self.assertEqual(text.count("{"), FIELD.count("{"))
        self.assertEqual(text.count("}"), FIELD.count("}"))

    def test_an_empty_patch_on_this_processor_is_still_set(self):
        """`nonuniform 0()` is a patch whose faces live on another processor.
        Setting it is harmless and keeps every copy saying the same thing."""
        coupled.apply_supplies(self.case, "3000", {"fan2": 24.0})
        self.assertIn("value           uniform 297.1500;", self._block("fan2Supply"))

    def test_only_the_named_units_move(self):
        coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        self.assertIn("uniform 295.05", self._block("fan2Intake"))
        self.assertIn("zeroGradient", self._block("floor"))

    def test_a_unit_the_field_does_not_have_is_ignored(self):
        """Not an error: a spec may name more units than a decomposed field
        carries on any one processor."""
        coupled.apply_supplies(self.case, "3000", {"fan9": 25.0})
        self.assertEqual(self._written(), FIELD)

    def test_nothing_else_in_the_file_changes(self):
        coupled.apply_supplies(self.case, "3000", {"fan1": 25.0})
        before = FIELD.splitlines()
        after = self._written().splitlines()
        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(differing), 2, "one value and one inletValue")

    def _block(self, patch):
        import re

        text = self._written()
        match = re.search(rf"^\s*{patch}\s*$", text, re.MULTILINE)
        start = text.find("{", match.end())
        return text[start: coupled._closing_brace(text, start) + 1]


class ControlDictTest(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name)
        (self.case / "system").mkdir()
        (self.case / "system" / "controlDict").write_text(
            "application     buoyantSimpleFoam;\n"
            "startFrom       startTime;\n"
            "startTime       0;\n"
            "stopAt          endTime;\n"
            "endTime         3000;\n"
            "writeInterval   100;\n"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_next_segment_continues_from_what_is_there(self):
        coupled.set_end_time(self.case, 3300, latest=True)
        text = (self.case / "system" / "controlDict").read_text()
        self.assertIn("startFrom       latestTime;", text)
        self.assertIn("endTime         3300;", text)
        self.assertIn("startTime       0;", text, "only the keyword, not the value")
        self.assertIn("writeInterval   100;", text)

    def test_the_latest_written_time_is_found(self):
        for name in ("0", "2900", "3000", "constant"):
            (self.case / name).mkdir()
        self.assertEqual(coupled.latest_time(self.case), "3000")

    def test_a_decomposed_run_is_read_from_its_processors(self):
        (self.case / "processor0" / "3300").mkdir(parents=True)
        (self.case / "3000").mkdir()
        self.assertEqual(coupled.latest_time(self.case), "3300")

    def test_zero_alone_is_not_a_result(self):
        (self.case / "0").mkdir()
        self.assertIsNone(coupled.latest_time(self.case))


class PipelineSplitTest(unittest.TestCase):
    def test_the_solver_is_found_whether_it_runs_bare_or_under_mpirun(self):
        from aicfd.case import pipeline
        from aicfd.run import _split_at_solver

        for processors in (1, 4):
            before, solver, after = _split_at_solver(pipeline(processors))
            name = solver if isinstance(solver, str) else (solver[2:] or solver[:1])[0]
            self.assertTrue(str(name).endswith("Foam"), processors)
            self.assertIn("blockMesh", [e if isinstance(e, str) else e[0]
                                        for e in before])

    def test_a_pipeline_with_no_solver_says_so(self):
        from aicfd.run import _split_at_solver

        with self.assertRaises(ValueError):
            _split_at_solver(("blockMesh", "checkMesh"))


class SupplyTemperatureTest(unittest.TestCase):
    """Each unit's supply temperature, from its own return through its coil."""

    def setUp(self):
        import copy

        import yaml

        from aicfd.model import build_model

        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "hall-double-gallery.yaml").read_text()
        )
        self.model = build_model(copy.deepcopy(spec))

    def _with_fans(self, fans):
        from aicfd import post

        original = post.fan_flows
        post.fan_flows = lambda step, **kw: fans
        try:
            return coupled.supply_temperatures(self.model, "unused")
        finally:
            post.fan_flows = original

    def test_each_unit_gets_its_own_temperature(self):
        """Not the plant's average. A unit at the end of a row returning
        warmer air delivers warmer air, and a model that gave every unit the
        mean would smear away the imbalance a hall is simulated to find."""
        supplies, returns, _ = self._with_fans([
            {"name": "fan1", "return_temp_c": 33.0, "intake_kg_s": 31.0},
            {"name": "fan2", "return_temp_c": 45.0, "intake_kg_s": 31.0},
        ])
        self.assertEqual(set(supplies), {"fan1", "fan2"})
        self.assertEqual(returns["fan2"], 45.0)
        self.assertLess(supplies["fan1"], supplies["fan2"],
                        "the warmer return must deliver the warmer supply")

    def test_a_unit_with_authority_holds_its_setpoint(self):
        """Which is the honest answer, not a null result: the coupled solve
        proves the imposed supply temperature is reachable instead of
        assuming it."""
        supplies, _, saturated = self._with_fans([
            {"name": "fan1", "return_temp_c": 33.3, "intake_kg_s": 31.7},
        ])
        self.assertAlmostEqual(supplies["fan1"], self.model.supply_temp_c, places=2)
        self.assertEqual(saturated, [])

    def test_a_unit_without_authority_floats_above_it(self):
        supplies, _, saturated = self._with_fans([
            {"name": "fan1", "return_temp_c": 48.0, "intake_kg_s": 31.7},
        ])
        self.assertGreater(supplies["fan1"], self.model.supply_temp_c)
        self.assertEqual(saturated, ["fan1"])

    def test_a_case_with_no_coil_couples_to_nothing(self):
        import copy

        import yaml

        from aicfd.model import build_model

        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "pod-fanwall.yaml").read_text()
        )
        plain = build_model(copy.deepcopy(spec))
        self.assertEqual(
            coupled.supply_temperatures(plain, "unused"), ({}, {}, [])
        )


class WaterTemperatureTest(unittest.TestCase):
    """The one condition a plant changes without changing the machine."""

    def _model(self, water=None):
        import copy

        import yaml

        from aicfd.model import build_model

        spec = yaml.safe_load(
            (Path(__file__).resolve().parents[1]
             / "cases" / "hall-double-gallery.yaml").read_text()
        )
        spec = copy.deepcopy(spec)
        if water is not None:
            spec["fanwall"]["entering_water_c"] = water
        return build_model(spec)

    def test_the_spec_can_state_the_chilled_water_it_runs_on(self):
        self.assertEqual(self._model(21.0).equipment.coil.water_c, 21.0)
        self.assertEqual(self._model().equipment.coil.water_c, 18.0)

    def test_warmer_water_costs_capacity_at_the_same_return(self):
        cold = self._model(18.0).equipment.coil
        warm = self._model(21.0).equipment.coil
        air = 31.7 * 1.005
        self.assertLess(warm.operate(33.3, air).ceiling_kw,
                        cold.operate(33.3, air).ceiling_kw)

    def test_warmer_water_pushes_the_supply_up_about_one_for_one(self):
        """The dominant sensitivity, and the reason it is an input at all:
        once the valve is open the supply follows the water by the coil's own
        effectiveness, which on this machine is about 0.8 K per K."""
        air = 31.7 * 1.005
        coil = self._model(18.0).equipment.coil
        cold = coil.operate(33.3, air).supply_c
        warm = self._model(21.0).equipment.coil.operate(33.3, air).supply_c
        self.assertAlmostEqual(warm - cold, 3.0 * coil.epsilon(air, coil.water_max),
                               delta=0.05)
        self.assertGreater(warm - cold, 2.0, "almost one for one")

    def test_the_fit_is_not_re_read_at_the_new_water_temperature(self):
        """The manufacturer's selections were taken at the water they were
        taken at. Re-reading them at another temperature would change the
        water flow inferred behind every row and corrupt the conductance
        recovered from them -- the machine would silently become a different
        machine."""
        base = self._model().equipment.coil
        moved = self._model(21.0).equipment.coil
        self.assertAlmostEqual(moved.k_air, base.k_air)
        self.assertAlmostEqual(moved.k_water, base.k_water)
        self.assertAlmostEqual(moved.water_max, base.water_max)
        self.assertAlmostEqual(moved.residual_k, base.residual_k)

    def test_warm_water_is_what_takes_the_supply_out_of_control(self):
        """The finding the whole coupling exists to be able to produce."""
        air = 31.7 * 1.005
        held = self._model(18.0).equipment.coil.operate(33.3, air, 21.9)
        lost = self._model(21.0).equipment.coil.operate(33.3, air, 21.9)
        self.assertFalse(held.saturated)
        self.assertAlmostEqual(held.supply_c, 21.9, places=2)
        self.assertTrue(lost.saturated)
        self.assertGreater(lost.supply_c, 23.0)
