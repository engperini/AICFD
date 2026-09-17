"""Tests for POD post-processing.

The module's whole claim is that it reads what crosses a patch rather than
what is near it, and that the energy balance is a better verdict than a
residual. These check both on hand-written field files.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import model as M
from aicfd import podpost
from aicfd.podcase import FAN_INTAKE, FAN_SUPPLY, KELVIN

SPEC = yaml.safe_load(
    """
name: t
gallery: {depth: 3.0}
hall: {size: [6.0, 4.2, 8.0], ceiling: 6.5}
aisles: {cold: 1.8, hot: 1.2}
racks: {count: 3, load_kw: 6.0, size: [0.6, 1.2, 2.2], offset_x: 2.0}
fanwall: {airflow_m3h: 5000, supply_temp_c: 20.0, height: 4.0, width: 1.8}
grilles: {size: 0.6, count: 3}
containment: {enabled: true}
mesh: {cell_size: 0.1}
"""
)

HEADER = """FoamFile
{{
    version 2.0; format ascii; class {cls}; location "{time}"; object {obj};
}}
dimensions [0 0 0 0 0 0 0];
internalField uniform {internal};
boundaryField
{{
"""


def field(path: Path, obj: str, internal: str, patches: dict[str, list[float]]):
    body = HEADER.format(cls="volScalarField", time=path.parent.name, obj=obj,
                         internal=internal)
    for name, values in patches.items():
        numbers = " ".join(f"{v:.6g}" for v in values)
        body += (
            f"    {name}\n    {{\n        type calculated;\n"
            f"        value nonuniform List<scalar> {len(values)}\n"
            f"({numbers})\n;\n    }}\n"
        )
    path.write_text(body + "}\n")


class PatchReadingTest(unittest.TestCase):
    """A closed loop carrying exactly its load out through the intake."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.case = Path(tempfile.mkdtemp())
        step = self.case / "100"
        step.mkdir()
        # 1 kg/s out through four faces, 1 kg/s in, walls shut.
        field(step / "phi", "phi", "0", {
            FAN_INTAKE: [0.25, 0.25, 0.25, 0.25],
            FAN_SUPPLY: [-0.5, -0.5],
            "forro_master": [0.0, 0.0],
            "floor": [0.0],
        })
        # 18 kW on 1 kg/s of air is a 17.91 K rise.
        rise = self.model.total_load_w / (1.0 * 1005.0)
        supply = 20.0 + KELVIN
        field(step / "T", "T", f"{supply}", {
            FAN_INTAKE: [supply + rise] * 4,
            FAN_SUPPLY: [supply, supply],
            "forro_master": [supply, supply],
            "floor": [supply],
        })
        self.step = step

    def test_patch_flows_sum_each_patch(self):
        flows = podpost.patch_flows(self.step)
        self.assertAlmostEqual(flows[FAN_INTAKE], 1.0)
        self.assertAlmostEqual(flows[FAN_SUPPLY], -1.0)
        self.assertAlmostEqual(flows["forro_master"], 0.0)

    def test_the_return_temperature_is_flow_weighted(self):
        rise = self.model.total_load_w / 1005.0
        self.assertAlmostEqual(
            podpost.return_temperature(self.step), 20.0 + KELVIN + rise, places=3
        )

    def test_a_closed_balance_recovers_the_whole_load(self):
        recovered = podpost.recovered_load_w(self.step, self.model)
        # 1 W of slack: the fixture writes its temperatures at six figures.
        self.assertAlmostEqual(recovered, self.model.total_load_w, delta=1.0)

    def test_a_half_filled_field_recovers_half_the_load(self):
        """The failure mode this exists to catch: an unconverged thermal field."""
        supply = 20.0 + KELVIN
        rise = self.model.total_load_w / 1005.0 / 2
        field(self.step / "T", "T", f"{supply}", {
            FAN_INTAKE: [supply + rise] * 4,
            FAN_SUPPLY: [supply, supply],
            "forro_master": [supply, supply],
            "floor": [supply],
        })
        recovered = podpost.recovered_load_w(self.step, self.model)
        self.assertAlmostEqual(recovered / self.model.total_load_w, 0.5, places=3)

    def test_backflow_is_the_mass_going_the_wrong_way(self):
        field(self.step / "phi", "phi", "0", {
            FAN_INTAKE: [0.8, 0.8, -0.3, -0.3],
            FAN_SUPPLY: [-0.5, -0.5],
        })
        self.assertAlmostEqual(podpost.backflow(self.step, FAN_INTAKE), 0.6)
        self.assertAlmostEqual(podpost.backflow(self.step, FAN_SUPPLY), 0.0)

    def test_an_outlet_that_stores_no_value_says_so(self):
        """zeroGradient writes no value, and nan would hide it."""
        (self.step / "T").write_text(
            HEADER.format(cls="volScalarField", time="100", obj="T", internal="293")
            + f"    {FAN_INTAKE}\n    {{\n        type zeroGradient;\n    }}\n}}\n"
        )
        with self.assertRaises(ValueError) as caught:
            podpost.return_temperature(self.step)
        self.assertIn("inletOutlet", str(caught.exception))

    def test_written_times_are_ordered_numerically(self):
        for name in ("50", "1000", "200"):
            d = self.case / name
            d.mkdir(exist_ok=True)
            (d / "phi").write_text("x")
        self.assertEqual(
            podpost.written_times(self.case), ["50", "100", "200", "1000"]
        )


class SamplerTest(unittest.TestCase):
    """The sampler has to be safe to call while the solver is still writing."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.case = Path(tempfile.mkdtemp())

    def write_step(self, iteration: int, complete: bool = True):
        """A time directory shaped like one buoyantSimpleFoam writes."""
        step = self.case / str(iteration)
        step.mkdir(exist_ok=True)
        supply = 20.0 + KELVIN
        rise = self.model.total_load_w / 1005.0  # 1 kg/s carries the whole load
        field(step / "phi", "phi", "0", {
            FAN_INTAKE: [0.25] * 4,
            FAN_SUPPLY: [-0.5, -0.5],
            "forro_master": [0.0],
        })
        field(step / "T", "T", f"{supply}", {
            FAN_INTAKE: [supply + rise] * 4,
            FAN_SUPPLY: [supply, supply],
            "forro_master": [supply],
        })
        field(step / "U", "U", "(0.2 0 0)", {
            FAN_INTAKE: [0.0], FAN_SUPPLY: [0.0], "forro_master": [0.0],
        })
        if complete:
            for name in ("phi", "T", "U"):
                path = step / name
                path.write_text(path.read_text() + "\n// ***** //\n")
        return step

    def test_a_half_written_step_is_not_sampled(self):
        """Sampling mid-write records numbers that never existed."""
        self.write_step(100, complete=False)
        self.assertEqual(podpost.sample(self.model, self.case), [])

    def test_a_complete_step_is_sampled_once(self):
        self.write_step(100)
        first = podpost.sample(self.model, self.case)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["iteration"], 100)
        self.assertEqual(podpost.sample(self.model, self.case), first)

    def test_the_history_survives_the_fields_being_purged(self):
        """purgeWrite deletes the time directory; the reading has to stay."""
        step = self.write_step(100)
        podpost.sample(self.model, self.case)
        for entry in step.iterdir():
            entry.unlink()
        step.rmdir()
        self.assertEqual(len(podpost.read_history(self.case)), 1)

    def test_every_place_is_measured_and_its_spread_reported(self):
        self.write_step(100)
        row = podpost.sample(self.model, self.case)[0]
        names = {place["name"] for place in row["places"]}
        self.assertEqual(
            names, {"cold_aisle", "hot_aisle", "plenum", "fan_back"}
        )
        for place in row["places"]:
            self.assertIn("spread_k", place)
            self.assertEqual(len(place["points_c"]), 3)

    def test_readings_come_back_in_iteration_order(self):
        for iteration in (300, 100, 200):
            self.write_step(iteration)
        history = podpost.sample(self.model, self.case)
        self.assertEqual([r["iteration"] for r in history], [100, 200, 300])

    def test_the_history_is_shaped_for_a_chart(self):
        self.write_step(100)
        self.write_step(200)
        shaped = podpost.sensor_history(self.model, self.case)
        self.assertEqual(shaped["iterations"], [100, 200])
        self.assertEqual(len(shaped["groups"]), 4)
        for group in shaped["groups"]:
            self.assertEqual(len(group["temp_c"]), 2)
        self.assertEqual(len(shaped["balance"]), 2)


class SensorPlacementTest(unittest.TestCase):
    def setUp(self):
        self.model = M.build_model(SPEC)
        self.groups = M.sensors(self.model)

    def test_there_are_three_points_in_each_of_the_four_places(self):
        self.assertEqual(len(self.groups), 4)
        for group in self.groups:
            self.assertEqual(len(group.points), 3)

    def test_the_aisle_probes_sit_at_rack_height_in_the_aisle(self):
        rack_mid = self.model.racks[0].box.hi[2] / 2
        for name, band in (
            ("cold_aisle", self.model.cold_aisle),
            ("hot_aisle", self.model.hot_aisle),
        ):
            group = next(g for g in self.groups if g.name == name)
            for x, y, z in group.points:
                self.assertAlmostEqual(z, rack_mid)
                self.assertGreater(y, band[0])
                self.assertLess(y, band[1])

    def test_the_plenum_probes_sit_above_the_false_ceiling(self):
        group = next(g for g in self.groups if g.name == "plenum")
        for _x, _y, z in group.points:
            self.assertGreater(z, self.model.ceiling_z)
            self.assertLess(z, self.model.domain.hi[2])

    def test_the_fan_back_probes_sit_in_the_gallery(self):
        group = next(g for g in self.groups if g.name == "fan_back")
        for x, _y, _z in group.points:
            self.assertLess(x, self.model.hall.lo[0])
            self.assertGreater(x, 0)

    def test_no_probe_sits_inside_a_rack(self):
        for group in self.groups:
            for point in group.points:
                for rack in self.model.racks:
                    inside = all(
                        rack.box.lo[a] <= point[a] <= rack.box.hi[a] for a in range(3)
                    )
                    self.assertFalse(inside, f"{group.name} probe inside {rack.id}")

    def test_the_probes_travel_with_the_geometry(self):
        """Move the aisle and the sensors move with it, or they measure nothing."""
        wider = dict(SPEC, aisles={"cold": 3.0, "hot": 1.2})
        moved = M.sensors(M.build_model(wider))
        before = next(g for g in self.groups if g.name == "cold_aisle").points[0]
        after = next(g for g in moved if g.name == "cold_aisle").points[0]
        self.assertGreater(after[1], before[1])


class DriftTest(unittest.TestCase):
    """A closed balance says the field is consistent; only stillness says
    it is settled. A warm start satisfies the first from iteration one."""

    def setUp(self):
        self.case = Path(tempfile.mkdtemp())

    def history(self, *rows):
        (self.case / podpost.SENSOR_FILE).write_text(json.dumps(list(rows)))

    def row(self, iteration, cold, hot, ret):
        return {
            "iteration": iteration,
            "return_temp_c": ret,
            "places": [
                {"name": "cold_aisle", "temp_c": cold},
                {"name": "hot_aisle", "temp_c": hot},
            ],
        }

    def test_one_sample_cannot_say_anything(self):
        self.history(self.row(100, 20.0, 27.5, 30.8))
        self.assertIsNone(podpost.drift(self.case))

    def test_drift_is_the_largest_move_any_place_made(self):
        self.history(
            self.row(100, 20.0, 27.5, 30.8), self.row(200, 20.1, 25.1, 30.8)
        )
        self.assertAlmostEqual(podpost.drift(self.case), 2.4, places=3)

    def test_the_return_temperature_counts_as_a_place(self):
        self.history(
            self.row(100, 20.0, 27.5, 30.8), self.row(200, 20.0, 27.5, 29.0)
        )
        self.assertAlmostEqual(podpost.drift(self.case), 1.8, places=3)

    def test_a_settled_field_drifts_below_the_tolerance(self):
        self.history(
            self.row(100, 20.0, 30.8, 30.8), self.row(200, 20.0, 30.85, 30.8)
        )
        self.assertLess(podpost.drift(self.case), podpost.STEADY_TOLERANCE)

    def test_the_tolerance_is_tighter_than_any_rise_worth_reporting(self):
        self.assertLessEqual(podpost.STEADY_TOLERANCE, 0.5)


class CompareTest(unittest.TestCase):
    """Two runs of the same case, from different initial fields, must agree."""

    def setUp(self):
        self.a = Path(tempfile.mkdtemp())
        self.b = Path(tempfile.mkdtemp())

    def write(self, case, iteration, hot, ret):
        (case / podpost.SENSOR_FILE).write_text(
            json.dumps([{
                "iteration": iteration,
                "return_temp_c": ret,
                "places": [
                    {"name": "hot_aisle", "label": "Corredor quente", "temp_c": hot}
                ],
            }])
        )

    def test_runs_that_land_together_agree(self):
        self.write(self.a, 800, 30.8, 30.8)
        self.write(self.b, 1600, 30.9, 30.85)
        result = podpost.compare(self.a, self.b)
        self.assertTrue(result["agree"])
        self.assertLess(result["worst_gap_k"], 1.0)

    def test_runs_that_land_apart_do_not(self):
        """If the seed chose the answer, this is where it shows."""
        self.write(self.a, 800, 30.8, 30.8)
        self.write(self.b, 800, 24.0, 24.0)
        result = podpost.compare(self.a, self.b)
        self.assertFalse(result["agree"])
        self.assertAlmostEqual(result["worst_gap_k"], 6.8, places=2)

    def test_a_run_with_no_samples_gives_no_verdict(self):
        self.write(self.a, 800, 30.8, 30.8)
        result = podpost.compare(self.a, self.b)
        self.assertIsNone(result["agree"])
        self.assertIn("no samples", result["reason"])


class ToleranceTest(unittest.TestCase):
    def test_mass_is_held_tighter_than_energy(self):
        """Mass has nowhere to go; the thermal field merely takes time."""
        self.assertLess(podpost.MASS_TOLERANCE, podpost.ENERGY_TOLERANCE)

    def test_any_real_backflow_through_a_fan_is_a_failure(self):
        self.assertLessEqual(podpost.BACKFLOW_TOLERANCE, 0.05)


if __name__ == "__main__":
    unittest.main()
