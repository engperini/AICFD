"""Tests for POD post-processing.

The module's whole claim is that it reads what crosses a patch rather than
what is near it, and that the energy balance is a better verdict than a
residual. These check both on hand-written field files.
"""

from __future__ import annotations

import copy
import inspect
import json
import tempfile
import copy
import unittest
from pathlib import Path

import yaml

from aicfd import model as M
from aicfd import post
from aicfd.case import FAN_INTAKE, FAN_SUPPLY, KELVIN
from tests import support

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
        flows = post.patch_flows(self.step)
        self.assertAlmostEqual(flows[FAN_INTAKE], 1.0)
        self.assertAlmostEqual(flows[FAN_SUPPLY], -1.0)
        self.assertAlmostEqual(flows["forro_master"], 0.0)

    def test_the_return_temperature_is_flow_weighted(self):
        rise = self.model.total_load_w / 1005.0
        self.assertAlmostEqual(
            post.return_temperature(self.step), 20.0 + KELVIN + rise, places=3
        )

    def test_a_closed_balance_recovers_the_whole_load(self):
        recovered = post.recovered_load_w(self.step, self.model)
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
        recovered = post.recovered_load_w(self.step, self.model)
        self.assertAlmostEqual(recovered / self.model.total_load_w, 0.5, places=3)

    def test_backflow_is_the_mass_going_the_wrong_way(self):
        field(self.step / "phi", "phi", "0", {
            FAN_INTAKE: [0.8, 0.8, -0.3, -0.3],
            FAN_SUPPLY: [-0.5, -0.5],
        })
        self.assertAlmostEqual(post.backflow(self.step, FAN_INTAKE), 0.6)
        self.assertAlmostEqual(post.backflow(self.step, FAN_SUPPLY), 0.0)

    def test_an_outlet_that_stores_no_value_says_so(self):
        """zeroGradient writes no value, and nan would hide it."""
        (self.step / "T").write_text(
            HEADER.format(cls="volScalarField", time="100", obj="T", internal="293")
            + f"    {FAN_INTAKE}\n    {{\n        type zeroGradient;\n    }}\n}}\n"
        )
        with self.assertRaises(ValueError) as caught:
            post.return_temperature(self.step)
        self.assertIn("inletOutlet", str(caught.exception))

    def test_written_times_are_ordered_numerically(self):
        for name in ("50", "1000", "200"):
            d = self.case / name
            d.mkdir(exist_ok=True)
            (d / "phi").write_text("x")
        self.assertEqual(
            post.written_times(self.case), ["50", "100", "200", "1000"]
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
        field(step / "p_rgh", "p_rgh", "101325", {
            FAN_INTAKE: [101325.0] * 4,
            FAN_SUPPLY: [101333.0, 101333.0],
            "forro_master": [101325.0],
        })
        if complete:
            for name in ("phi", "T", "U", "p_rgh"):
                path = step / name
                path.write_text(path.read_text() + "\n// ***** //\n")
        return step

    def test_a_half_written_step_is_not_sampled(self):
        """Sampling mid-write records numbers that never existed."""
        self.write_step(100, complete=False)
        self.assertEqual(post.sample(self.model, self.case), [])

    def test_a_complete_step_is_sampled_once(self):
        self.write_step(100)
        first = post.sample(self.model, self.case)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["iteration"], 100)
        self.assertEqual(post.sample(self.model, self.case), first)

    def test_the_history_survives_the_fields_being_purged(self):
        """purgeWrite deletes the time directory; the reading has to stay."""
        step = self.write_step(100)
        post.sample(self.model, self.case)
        for entry in step.iterdir():
            entry.unlink()
        step.rmdir()
        self.assertEqual(len(post.read_history(self.case)), 1)

    def test_every_place_is_measured_and_its_spread_reported(self):
        self.write_step(100)
        row = post.sample(self.model, self.case)[0]
        names = {place["name"] for place in row["places"]}
        self.assertEqual(
            names, {"cold_aisle", "hot_aisle", "plenum", "fan_back"}
        )
        for place in row["places"]:
            self.assertIn("spread_k", place)
            self.assertIn("pressure_pa", place)
            self.assertEqual(len(place["points_c"]), 3)

    def test_pressure_is_reported_against_the_fan_intake(self):
        """Absolute pressure is 101 325 Pa everywhere and says nothing."""
        self.write_step(100)
        row = post.sample(self.model, self.case)[0]
        for place in row["places"]:
            self.assertAlmostEqual(place["pressure_pa"], 0.0, places=3)

    def test_the_fan_wall_rise_is_the_jump_across_the_pair(self):
        self.write_step(100)
        row = post.sample(self.model, self.case)[0]
        self.assertAlmostEqual(row["fan_rise_pa"], 8.0, places=2)

    def test_readings_come_back_in_iteration_order(self):
        for iteration in (300, 100, 200):
            self.write_step(iteration)
        history = post.sample(self.model, self.case)
        self.assertEqual([r["iteration"] for r in history], [100, 200, 300])

    def test_the_history_is_shaped_for_a_chart(self):
        self.write_step(100)
        self.write_step(200)
        shaped = post.sensor_history(self.model, self.case)
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
        (self.case / post.SENSOR_FILE).write_text(json.dumps(list(rows)))

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
        self.assertIsNone(post.drift(self.case))

    def test_drift_is_the_largest_move_any_place_made(self):
        self.history(
            self.row(100, 20.0, 27.5, 30.8), self.row(200, 20.1, 25.1, 30.8)
        )
        self.assertAlmostEqual(post.drift(self.case), 2.4, places=3)

    def test_the_return_temperature_counts_as_a_place(self):
        self.history(
            self.row(100, 20.0, 27.5, 30.8), self.row(200, 20.0, 27.5, 29.0)
        )
        self.assertAlmostEqual(post.drift(self.case), 1.8, places=3)

    def test_a_settled_field_drifts_below_the_tolerance(self):
        self.history(
            self.row(100, 20.0, 30.8, 30.8), self.row(200, 20.0, 30.85, 30.8)
        )
        self.assertLess(post.drift(self.case), post.STEADY_TOLERANCE)

    def test_the_tolerance_is_tighter_than_any_rise_worth_reporting(self):
        self.assertLessEqual(post.STEADY_TOLERANCE, 0.5)


class ReturnPathTest(unittest.TestCase):
    """Nothing heats or cools the air between the racks and the fan intake."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.case = Path(tempfile.mkdtemp())

    def kpis(self, hot, plenum, fan):
        return {
            "places_now": [
                {"name": "hot_aisle", "temp_c": hot},
                {"name": "plenum", "temp_c": plenum},
                {"name": "fan_back", "temp_c": fan},
                {"name": "cold_aisle", "temp_c": 20.0},
            ],
            "drift_k": 0.05,
        }

    def check(self, kpis):
        from aicfd.post import RETURN_PATH, RETURN_PATH_TOLERANCE

        path = {
            p["name"]: p["temp_c"]
            for p in kpis["places_now"]
            if p["name"] in RETURN_PATH
        }
        return max(path.values()) - min(path.values()) <= RETURN_PATH_TOLERANCE

    def test_a_settled_return_path_agrees_with_itself(self):
        self.assertTrue(self.check(self.kpis(31.0, 30.9, 31.1)))

    def test_a_plenum_still_filling_is_caught(self):
        """The case that slipped through: settled at 0,1 K, 3,2 K to go."""
        self.assertFalse(self.check(self.kpis(31.2, 28.0, 30.8)))

    def test_a_gallery_still_filling_is_caught(self):
        self.assertFalse(self.check(self.kpis(31.4, 28.1, 21.5)))

    def test_the_cold_aisle_is_not_on_the_return_path(self):
        """It is 11 K colder by design; including it would fail every run."""
        self.assertNotIn("cold_aisle", post.RETURN_PATH)

    def test_the_tolerance_allows_real_stratification_but_not_a_filling_volume(self):
        self.assertGreater(post.RETURN_PATH_TOLERANCE, 1.0)
        self.assertLess(post.RETURN_PATH_TOLERANCE, 3.0)


class RackResistanceTest(unittest.TestCase):
    """What the rack curve asks for, against what the field delivers."""

    def setUp(self):
        self.model = M.build_model(SPEC)

    def test_the_asked_for_drop_comes_from_the_rack_curve(self):
        """Closed form, because containment makes the throughflow exact."""
        model = self.model
        face = sum(r.face_area for r in model.racks)
        u = model.airflow_m3s / face
        _d, f = model.racks[0].darcy_forchheimer()
        expected = 0.5 * model.rho * f * u**2 * model.racks[0].depth
        self.assertAlmostEqual(model.rack_pressure_drop_pa, expected, places=6)

    def test_it_lands_near_the_nominal_drop_when_flow_matches_rating(self):
        """The fan moves 5 000 m3/h and the racks want 4 832 at 158 CFM/kW, so
        the nominal 25 Pa scaled by the square of that ratio."""
        ratio = self.model.airflow_m3h / self.model.rack_demand_m3h
        self.assertAlmostEqual(
            self.model.rack_pressure_drop_pa, M.RACK_PRESSURE_DROP * ratio**2, delta=0.3
        )

    def test_it_scales_with_the_square_of_the_airflow(self):
        doubled = M.build_model(
            dict(SPEC, fanwall={**SPEC["fanwall"], "airflow_m3h": 10000})
        )
        self.assertAlmostEqual(
            doubled.rack_pressure_drop_pa / self.model.rack_pressure_drop_pa,
            4.0,
            places=3,
        )

    def test_a_case_with_no_racks_asks_for_nothing(self):
        model = M.build_model(dict(SPEC, racks={**SPEC["racks"], "count": 0}))
        self.assertEqual(model.rack_pressure_drop_pa, 0.0)

    def test_the_datasheet_pressure_is_carried_through(self):
        model = M.build_model(
            dict(SPEC, fanwall={**SPEC["fanwall"], "static_pressure_pa": 100})
        )
        self.assertEqual(model.fan_static_pa, 100.0)
        self.assertIsNone(self.model.fan_static_pa)

    def test_the_tolerance_would_not_pass_the_gap_we_have(self):
        """5,3 Pa delivered against 25,8 asked is 21%; this must fail it."""
        self.assertLess(post.RESISTANCE_TOLERANCE, 0.79)


class FanCapacityTest(unittest.TestCase):
    """The datasheet pressure is a limit, so it is checked against."""

    def test_a_pod_that_fits_the_machine_passes(self):
        model = M.build_model(
            dict(SPEC, fanwall={**SPEC["fanwall"], "static_pressure_pa": 100})
        )
        self.assertEqual(model.fan_static_pa, 100.0)
        self.assertLess(model.rack_pressure_drop_pa, model.fan_static_pa)

    def test_a_pod_that_does_not_fit_is_visible_before_the_solve(self):
        """Quadrupling the airflow puts the racks alone past the machine."""
        model = M.build_model(
            dict(
                SPEC,
                fanwall={
                    **SPEC["fanwall"],
                    "airflow_m3h": 20000,
                    "static_pressure_pa": 100,
                },
            )
        )
        self.assertGreater(model.rack_pressure_drop_pa, model.fan_static_pa)

    def test_without_a_datasheet_there_is_nothing_to_check(self):
        self.assertIsNone(M.build_model(SPEC).fan_static_pa)


class GrilleAndFanBudgetTest(unittest.TestCase):
    """The datasheet numbers turn into checks, not decoration."""

    def setUp(self):
        self.model = M.build_model(
            dict(
                SPEC,
                grilles={"size": 0.6, "count": 3, "free_area": 0.8, "loss_coefficient": 2.4},
                fanwall={**SPEC["fanwall"], "static_pressure_pa": 100,
                         "curve": [[0, 250], [2500, 200], [5000, 100], [6500, 40], [7500, 0]]},
            )
        )
        self.case = Path(tempfile.mkdtemp())

    def test_the_grille_drop_asked_for_follows_k_and_face_velocity(self):
        m = self.model
        area = 3 * 0.36
        v = m.airflow_m3s / area
        self.assertAlmostEqual(m.grille_pressure_drop_pa, 2.4 * 0.5 * m.rho * v**2, places=6)
        self.assertAlmostEqual(m.grille_pressure_drop_pa, 2.4, delta=0.3)

    def test_the_measured_grille_drop_is_flow_weighted_across_the_pairs(self):
        step = self.case / "100"
        step.mkdir()
        field(step / "phi", "phi", "0", {
            "grille1_below": [0.1] * 9, "grille1_above": [-0.1] * 9,
            "grille2_below": [0.3] * 9, "grille2_above": [-0.3] * 9,
        })
        field(step / "p_rgh", "p_rgh", "101325", {
            "grille1_below": [101327.0] * 9, "grille1_above": [101325.0] * 9,
            "grille2_below": [101329.0] * 9, "grille2_above": [101325.0] * 9,
        })
        # (0.9*2 + 2.7*4) / 3.6 = 3.5
        self.assertAlmostEqual(post.grille_pressure_drop(step), 3.5, places=3)

    def test_no_grille_pairs_means_no_measurement(self):
        step = self.case / "100"
        step.mkdir()
        field(step / "phi", "phi", "0", {FAN_INTAKE: [1.0], FAN_SUPPLY: [-1.0]})
        self.assertIsNone(post.grille_pressure_drop(step))

    def test_the_perforated_surfaces_are_not_leaks(self):
        """A cyclic pair carries the return flow by design.

        The ceiling return grilles, the woven mesh closing the plenum where it
        opens into a mechanical gallery, a supply plenum's grilles, and a
        raised floor's plates and the mesh below its deck. Leaving any of them
        out makes the sealed-envelope check fail on a hall that is sealed
        (ADR-048, ADR-076).
        """
        for prefix in ("grille", "plenum_opening", "supply",
                       "floor_opening", "tile_"):
            with self.subTest(prefix=prefix):
                self.assertIn(prefix, post.PASSES_FLOW)
        # And a real wall is still a leak when it carries flow.
        for wall in ("divider", "forro", "piso", "rack_top", "containment_wall"):
            with self.subTest(wall=wall):
                self.assertFalse(wall.startswith(post.PASSES_FLOW))

    def test_the_fan_capacity_check_reads_the_curve_at_the_rated_flow(self):
        self.assertAlmostEqual(self.model.fan_available_pa(), 100.0)
        q, dp = self.model.fan_operating_point(29.0)
        self.assertGreater(q, self.model.airflow_m3h)
        self.assertLess(dp, 100.0)

    def test_the_viewer_gets_pressure_and_the_model(self):
        import inspect

        source = inspect.getsource(post.export)
        self.assertIn('"P": grid["p_rgh"] - reference', source)
        self.assertIn('payload["model"]', source)


class CompareTest(unittest.TestCase):
    """Two runs of the same case, from different initial fields, must agree."""

    def setUp(self):
        self.a = Path(tempfile.mkdtemp())
        self.b = Path(tempfile.mkdtemp())

    def write(self, case, iteration, hot, ret):
        (case / post.SENSOR_FILE).write_text(
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
        result = post.compare(self.a, self.b)
        self.assertTrue(result["agree"])
        self.assertLess(result["worst_gap_k"], 1.0)

    def test_runs_that_land_apart_do_not(self):
        """If the seed chose the answer, this is where it shows."""
        self.write(self.a, 800, 30.8, 30.8)
        self.write(self.b, 800, 24.0, 24.0)
        result = post.compare(self.a, self.b)
        self.assertFalse(result["agree"])
        self.assertAlmostEqual(result["worst_gap_k"], 6.8, places=2)

    def test_a_run_with_no_samples_gives_no_verdict(self):
        self.write(self.a, 800, 30.8, 30.8)
        result = post.compare(self.a, self.b)
        self.assertIsNone(result["agree"])
        self.assertIn("no samples", result["reason"])


class ExportTest(unittest.TestCase):
    """The viewer payload, in the shape the M1 viewer already reads."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.case = Path(tempfile.mkdtemp())
        self.out = Path(tempfile.mkdtemp())

    def test_a_pod_carries_its_internal_surfaces_to_the_viewer(self):
        """Without them the viewer draws a slice through an empty box."""
        names = {p.name for p in self.model.panels}
        self.assertIn("ceiling", names)  # the forro
        self.assertIn("fan", names)
        self.assertTrue(any(n.startswith("containment") for n in names))
        self.assertTrue(any(n.startswith("grille") for n in names))
        self.assertTrue(any(n.startswith("rack_") for n in names))

    def test_the_default_slice_crosses_the_racks(self):
        """Halfway up the box lands above them, where nothing happens."""
        model = self.model
        index = int(round(model.racks[0].box.hi[2] / 2 / model.cell(2)))
        self.assertLess(index, model.divisions[2] // 2)
        self.assertGreater(index * model.cell(2), 0.0)
        self.assertLess(index * model.cell(2), model.racks[0].box.hi[2])

    def test_throughflow_is_not_reported_for_a_closed_row(self):
        """It is 100% by construction; quoting it would suggest a measurement."""
        import inspect

        from aicfd import post as pp

        source = inspect.getsource(pp._viewer_kpis)
        self.assertIn('"throughflow_ratio": None', source)


class ToleranceTest(unittest.TestCase):
    def test_mass_is_held_tighter_than_energy(self):
        """Mass has nowhere to go; the thermal field merely takes time."""
        self.assertLess(post.MASS_TOLERANCE, post.ENERGY_TOLERANCE)

    def test_any_real_backflow_through_a_fan_is_a_failure(self):
        self.assertLessEqual(post.BACKFLOW_TOLERANCE, 0.05)


if __name__ == "__main__":
    unittest.main()


class SupplyMeshDutyTest(unittest.TestCase):
    """A mesh leaf is a surface the solver builds, not a number bolted onto
    the duty afterwards (ADR-060)."""

    def test_the_field_is_what_says_what_it_costs(self):
        """It was first modelled as a K added to `fan_capacity`, on the
        grounds that a mesh over the units' own opening cannot redirect
        anything. It is not over the opening: it closes the plenum's hall
        side, so the solver has it and the duty must not count it twice."""
        source = inspect.getsource(post._checks)
        self.assertNotIn("supply_mesh_pressure_drop_pa", source)
        self.assertNotIn("from its K rather than from the field", source)

    def test_the_mesh_leaf_is_checked_like_any_other_surface(self):
        source = inspect.getsource(post._checks)
        self.assertIn("plenum_resistance", source)
        self.assertIn("supply_drop_pa", source)


class NegligibleResistanceTest(unittest.TestCase):
    """A ratio is the right test while there is something to divide
    (ADR-060)."""

    def test_two_pressures_that_are_both_nearly_zero_agree(self):
        """The mesh leaf failed at "0.00 Pa against 0.00 Pa (0%)", which is
        not a disagreement about anything."""
        passed, why = post._resistance_verdict(0.001, 0.004)
        self.assertTrue(passed)
        self.assertIn("too open for the ratio", why)

    def test_a_real_surface_is_still_judged_on_its_ratio(self):
        self.assertTrue(post._resistance_verdict(2.4, 2.39)[0])
        self.assertFalse(post._resistance_verdict(1.7, 26.8)[0])
        self.assertEqual(post._resistance_verdict(2.4, 2.39)[1], "")

    def test_a_big_miss_is_not_excused_by_a_small_asked(self):
        """A surface that asks for nothing and delivers pascals is a
        disagreement, and the absolute test has to catch it."""
        self.assertFalse(post._resistance_verdict(3.0, 0.01)[0])


class RackDropSamplingTest(unittest.TestCase):
    """The drop is measured across the RACK, not across everything under it.

    The two are the same room when the cabinets stand on the slab, which is
    why this read right for years. Put the room on an access floor and
    everything below the rack top includes the supply plenum, whose pressure
    drives the whole loop: averaged into both planes it dragged the measured
    drop to 70% of what the rack curve asks, and the check called the field
    wrong when it was the sampling (ADR-076).
    """

    def grid(self, model, plenum_pa: float):
        """A synthetic field: a clean drop across the row, and a plenum at a
        wildly different pressure under it."""
        import numpy as np

        cell = model.cell_size
        x = np.arange(cell[0] / 2, model.domain.hi[0], cell[0])
        y = np.arange(cell[1] / 2, model.domain.hi[1], cell[1])
        z = np.arange(cell[2] / 2, model.domain.hi[2], cell[2])
        row = model.rows[0]
        p = np.zeros((len(z), len(y), len(x)))
        # 30 Pa in front of the row, 0 behind it, at every height.
        p[:, y < row.front_y, :] = 30.0
        # And the plenum, which is nothing to do with the row.
        p[z < (model.floor_height or 0.0), :, :] = plenum_pa
        return {"x": x, "y": y, "z": z, "p_rgh": p}

    def model(self, raised: bool):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        if raised:
            spec["fanwall"]["model"] = "HDCV5300F-HT"
            spec["floor"] = {"enabled": True, "height": 1.0, "tiles_per_rack": 2}
        return M.build_model(spec)

    def test_the_plenum_does_not_reach_the_measurement(self):
        model = self.model(raised=True)
        quiet, loud = (post.rack_pressure_drop(model, self.grid(model, pa))[0]
                       for pa in (0.0, 900.0))
        self.assertAlmostEqual(quiet, loud, places=3,
                               msg="the plenum's pressure leaked into the drop")
        self.assertAlmostEqual(quiet, 30.0, places=3)

    def test_a_case_on_the_slab_measures_what_it_always_did(self):
        model = self.model(raised=False)
        self.assertIsNone(model.floor_height)
        drop, rows = post.rack_pressure_drop(model, self.grid(model, 0.0))
        self.assertAlmostEqual(drop, 30.0, places=3)
        self.assertEqual(len(rows), len(model.rows))
