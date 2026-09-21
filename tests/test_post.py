"""Tests for POD post-processing.

The module's whole claim is that it reads what crosses a patch rather than
what is near it, and that the energy balance is a better verdict than a
residual. These check both on hand-written field files.
"""

from __future__ import annotations

import dataclasses
import copy
import inspect
import json
import tempfile
import copy
import unittest
from pathlib import Path

import numpy as np
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

    def test_every_station_is_measured_with_its_range(self):
        self.write_step(100)
        row = post.sample(self.model, self.case)[0]
        self.assertEqual(
            [station["name"] for station in row["stations"]],
            ["supply", "rack_intake", "aisle_exit", "unit_return"],
        )
        for station in row["stations"]:
            for key in ("temp_c", "low_c", "high_c", "flow_m3h", "speed_ms"):
                self.assertIn(key, station)

    def test_the_loop_carries_one_flow_through_every_station(self):
        """A station that reported the cabinets' RATED flow said the row moved
        twice what the units deliver, on a row bought for more than it carries."""
        self.write_step(100)
        row = post.sample(self.model, self.case)[0]
        flows = {s["flow_m3h"] for s in row["stations"] if s["flow_m3h"] is not None}
        self.assertEqual(len(flows), 1, f"stations disagree on the flow: {flows}")

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


class StationTest(unittest.TestCase):
    """Four stations on the loop, declared without a single coordinate."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.stations = M.stations(self.model)

    def test_the_loop_is_measured_where_the_air_goes(self):
        self.assertEqual(
            [s.name for s in self.stations],
            ["supply", "rack_intake", "aisle_exit", "unit_return"],
        )

    def test_a_station_carries_no_coordinates(self):
        """The fault ADR-078 fixed was a measurement tied to a location. A
        station names a surface the whole airflow crosses; where a logger
        would hang is not part of it."""
        for station in self.stations:
            self.assertFalse(
                [f for f in dataclasses.fields(station)
                 if f.name in ("points", "point", "position")],
                f"{station.name} carries a coordinate",
            )

    def test_a_room_unit_and_a_fan_wall_are_named_as_what_they_are(self):
        floor = copy.deepcopy(SPEC)
        floor["floor"] = {"enabled": True, "height": 1.0}
        wall = M.stations(self.model)[0].note
        room = M.stations(M.build_model(floor))[0].note
        self.assertIn("fan wall", wall)
        self.assertIn("room unit", room)

    def test_a_case_with_no_racks_has_nothing_to_measure(self):
        empty = M.build_model(SPEC)
        empty.racks = []
        self.assertEqual(M.stations(empty), [])


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
            "stations": [
                {"name": "rack_intake", "temp_c": cold},
                {"name": "aisle_exit", "temp_c": hot},
            ],
        }

    def test_one_sample_cannot_say_anything(self):
        self.history(self.row(100, 20.0, 27.5, 30.8))
        self.assertIsNone(post.drift(self.case))

    def test_drift_is_the_largest_move_any_station_made(self):
        self.history(
            self.row(100, 20.0, 27.5, 30.8), self.row(200, 20.1, 25.1, 30.8)
        )
        self.assertAlmostEqual(post.drift(self.case), 2.4, places=3)

    def test_the_return_temperature_counts_as_a_station(self):
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

    def kpis(self, leaving, arriving, aisle=(30.9, 31.1)):
        low, high = aisle
        return {
            "aisle_exit_c": leaving,
            "return_temp_c": arriving,
            "stations_now": [
                {"name": "aisle_exit", "temp_c": leaving,
                 "low_c": low, "high_c": high},
                {"name": "unit_return", "temp_c": arriving,
                 "low_c": arriving, "high_c": arriving},
            ],
            "drift_k": 0.05,
        }

    def check(self, kpis):
        return post.return_path_check(kpis)

    def test_a_settled_return_path_agrees_with_itself(self):
        self.assertTrue(self.check(self.kpis(31.0, 31.02)).passed)

    def test_a_plenum_still_filling_is_caught(self):
        """The case that slipped through: settled at 0,1 K, 3,2 K to go."""
        self.assertFalse(self.check(self.kpis(31.2, 28.0)).passed)

    def test_a_gallery_still_filling_is_caught(self):
        self.assertFalse(self.check(self.kpis(31.4, 21.5)).passed)

    def test_a_row_of_uneven_load_is_not_a_broken_path(self):
        """The fault this check used to invent (ADR-078).

        Three 0 kW cabinets between three 32 kW ones, and the air crossing the
        ceiling spans sixteen kelvin. The streams still close, and the run is
        sound.
        """
        result = self.check(self.kpis(30.9, 30.92, aisle=(20.0, 36.7)))
        self.assertTrue(result.passed)
        self.assertIn("the room being uneven", result.detail)

    def test_an_even_room_says_nothing_about_its_spread(self):
        """The note earns its place only where it explains a disagreement."""
        self.assertNotIn("uneven", self.check(self.kpis(31.0, 31.02)).detail)

    def test_an_uneven_aisle_cannot_fail_a_closed_path(self):
        """The whole point: the spread is reported, the streams are judged."""
        for aisle in ((20.0, 36.7), (29.0, 45.0), (31.0, 31.0)):
            with self.subTest(aisle=aisle):
                self.assertTrue(self.check(self.kpis(31.0, 31.0, aisle)).passed)

    def test_a_leak_is_still_caught_however_even_the_room(self):
        """And the converse: a uniform aisle cannot pass a path that leaks."""
        result = self.check(self.kpis(31.0, 26.0, aisle=(31.0, 31.0)))
        self.assertFalse(result.passed)
        self.assertIn("air is joining the return path", result.detail)

    def test_a_step_with_no_streams_measured_is_not_judged(self):
        self.assertIsNone(self.check({"places_now": []}))

    def test_the_supply_is_not_on_the_return_path(self):
        """It is 11 K colder by design; comparing it would fail every run."""
        self.assertNotIn(
            "supply", self.check(self.kpis(31.0, 31.02)).detail
        )

    def test_the_tolerance_allows_real_stratification_but_not_a_filling_volume(self):
        self.assertGreater(post.RETURN_PATH_TOLERANCE, 1.0)
        self.assertLess(post.RETURN_PATH_TOLERANCE, 3.0)


class AisleExitTemperatureTest(unittest.TestCase):
    """The mixing cup at the ceiling: what the containment actually passes."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        n = self.model.divisions
        self.x = (np.arange(n[0]) + 0.5) * self.model.cell_size[0]
        self.y = (np.arange(n[1]) + 0.5) * self.model.cell_size[1]
        self.z = (np.arange(n[2]) + 0.5) * self.model.cell_size[2]
        self.shape = (n[2], n[1], n[0])

    def grid(self, temperature, up):
        """A field that is `temperature` and rising at `up` everywhere."""
        return {
            "x": self.x, "y": self.y, "z": self.z,
            "T": np.full(self.shape, float(temperature)),
            "U": np.stack([np.zeros(self.shape), np.zeros(self.shape),
                           np.full(self.shape, float(up))]),
        }

    def layer(self):
        """The row index of the cells just below the false ceiling."""
        return int(np.argmin(abs(self.z - (self.model.ceiling_z
                                           - self.model.cell_size[2] / 2))))

    def aisle(self):
        lo, hi = self.model.hot_aisles[0]
        return np.where((self.y >= lo) & (self.y <= hi))[0]

    def test_a_uniform_aisle_reads_its_own_temperature(self):
        self.assertEqual(post.aisle_exit_temperature(self.model,
                                                     self.grid(31.0, 0.5)), 31.0)

    def test_air_that_is_not_leaving_is_not_measured(self):
        """A ceiling the air is pressed against but does not cross."""
        self.assertIsNone(post.aisle_exit_temperature(self.model,
                                                      self.grid(31.0, -0.5)))

    def test_a_cabinet_that_passes_cold_air_moves_the_cup_by_its_share(self):
        """Half the aisle at 20 degC, half at 40, and the cup lands between --
        weighted by what each half carries, not by where a probe landed."""
        grid = self.grid(40.0, 0.5)
        aisle, layer = self.aisle(), self.layer()
        half = self.x.size // 2
        grid["T"][layer][np.ix_(aisle, np.arange(half))] = 20.0
        cup = post.aisle_exit_temperature(self.model, grid)
        self.assertGreater(cup, 20.0)
        self.assertLess(cup, 40.0)

    def test_a_grille_that_carries_nothing_weighs_nothing(self):
        """The same two halves, but the cold half is under solid ceiling. A
        probe there would read 20 degC; the cup does not see it at all."""
        grid = self.grid(40.0, 0.5)
        aisle, layer = self.aisle(), self.layer()
        half = self.x.size // 2
        cold = np.ix_(aisle, np.arange(half))
        grid["T"][layer][cold] = 20.0
        grid["U"][2][layer][cold] = 0.0
        self.assertEqual(post.aisle_exit_temperature(self.model, grid), 40.0)

    def test_the_gallery_is_not_on_the_return_path(self):
        """It has no false ceiling, so this height is room air there. Counting
        it would mix the supply side into the return (ADR-078)."""
        grid = self.grid(31.0, 0.5)
        layer = self.layer()
        outside = np.where(self.x < self.model.hall.lo[0])[0]
        grid["T"][layer][np.ix_(self.aisle(), outside)] = 5.0
        self.assertEqual(post.aisle_exit_temperature(self.model, grid), 31.0)


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

    def test_a_surface_crossed_the_other_way_still_reads_a_positive_drop(self):
        """Two galleries, one at each end, so their supply meshes face opposite
        ways: the air leaves the first towards higher x and the second towards
        lower x. createBaffles gives the master to the owner cell -- lower x --
        so `_below` is upstream on one and downstream on the other. Reading
        both the same way made one drop negative, and the weighted mean of
        +0,4 and -0,4 Pa is nothing (ADR-080)."""
        step = self.case / "100"
        step.mkdir()
        field(step / "phi", "phi", "0", {
            # first gallery: air runs below -> above
            "supply1_below": [0.2] * 9, "supply1_above": [-0.2] * 9,
            # second gallery: the same surface, crossed the other way
            "supply2_below": [-0.2] * 9, "supply2_above": [0.2] * 9,
        })
        field(step / "p_rgh", "p_rgh", "101325", {
            "supply1_below": [101329.0] * 9, "supply1_above": [101325.0] * 9,
            "supply2_below": [101325.0] * 9, "supply2_above": [101329.0] * 9,
        })
        self.assertAlmostEqual(
            post.grille_pressure_drop(step, "supply"), 4.0, places=3
        )

    def test_a_surface_the_air_is_leaking_back_through_reads_negative(self):
        """The sign follows the AIR, not the patch name -- but a pair whose
        pressure rises in the direction the air travels is still reported as
        the negative drop it is, because that is a finding."""
        step = self.case / "100"
        step.mkdir()
        field(step / "phi", "phi", "0", {
            "supply1_below": [0.2] * 9, "supply1_above": [-0.2] * 9,
        })
        field(step / "p_rgh", "p_rgh", "101325", {
            "supply1_below": [101321.0] * 9, "supply1_above": [101325.0] * 9,
        })
        self.assertAlmostEqual(
            post.grille_pressure_drop(step, "supply"), -4.0, places=3
        )

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
                "stations": [
                    {"name": "aisle_exit", "label": "Aisle exit", "temp_c": hot}
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


class FloorResistanceTest(unittest.TestCase):
    """A raised floor's plates are a grille like the others (ADR-076)."""

    def model(self):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["fanwall"]["model"] = "HDCV5300F-HT"
        spec["floor"] = {"enabled": True, "height": 1.0, "tiles_per_rack": 2}
        return M.build_model(spec)

    def test_the_plates_are_sized_on_their_gross_face(self):
        model = self.model()
        area = sum(t.area for t in model.floor_tiles)
        self.assertAlmostEqual(model.floor_face_velocity_ms,
                               model.airflow_m3s / area, places=4)

    def test_the_drop_is_k_rho_v_squared_over_two(self):
        model = self.model()
        v = model.floor_face_velocity_ms
        k = model.floor_tiles[0].resistance
        self.assertAlmostEqual(model.floor_pressure_drop_pa,
                               k * 0.5 * model.rho * v ** 2, places=4)

    def test_a_case_on_the_slab_has_neither(self):
        model = M.build_model(support.spec("pod-fanwall"))
        self.assertEqual(model.floor_tiles, [])
        self.assertIsNone(model.floor_face_velocity_ms)
        self.assertIsNone(model.floor_pressure_drop_pa)

    def test_the_plates_are_in_the_pressure_budget(self):
        model = self.model()
        named = dict(model.pressure_budget())
        self.assertIn("the floor plates", named)
        self.assertAlmostEqual(named["the floor plates"],
                               model.floor_pressure_drop_pa, places=4)
        self.assertAlmostEqual(sum(named.values()),
                               model.loop_pressure_drop_pa, places=4)

    def test_the_mesh_is_counted_at_both_ends_of_the_wall(self):
        """The same opening exists above the ceiling for the return and below
        the deck for the supply, and the air crosses both."""
        model = self.model()
        named = dict(model.pressure_budget())
        self.assertIn("the mesh under the deck", named)
        self.assertAlmostEqual(named["the mesh under the deck"],
                               named["the mesh into the gallery, above the ceiling"],
                               places=6)

    def test_the_check_reads_the_plates_and_not_the_other_grilles(self):
        """Every cyclic pair ends `_below`; taking them all mixed surfaces
        with different open areas into one mean that matched no one's K."""
        import inspect

        source = inspect.getsource(post)
        self.assertIn('grille_pressure_drop(step, "tile_")', source)
        # And the other kinds are still read against their own.
        for prefix in ('"plenum_opening"', '"supply"'):
            self.assertIn(f"grille_pressure_drop(step, {prefix})", source)

    def test_the_report_calls_it_a_room_unit(self):
        """A downflow unit is not a fan wall, and the reader is checking a
        drawing against this."""
        import inspect

        source = inspect.getsource(post)
        self.assertIn('"Room unit" if k.get("floor_height") else "Fan wall"',
                      source)


class FlowSpreadTest(unittest.TestCase):
    """What a quadratic resistance costs depends on how evenly it is fed."""

    def setUp(self):
        self.case = Path(tempfile.mkdtemp())
        self.step = self.case / "100"
        self.step.mkdir()

    def write(self, fluxes):
        field(self.step / "phi", "phi", "0", {
            "supply1_below": list(fluxes),
            "supply1_above": [-f for f in fluxes],
        })

    def test_an_evenly_fed_surface_spreads_by_one(self):
        self.write([0.2] * 16)
        self.assertAlmostEqual(post.flow_spread(self.step, "supply"), 1.0, places=3)

    def test_the_spread_is_mean_of_the_square_over_the_square_of_the_mean(self):
        """Half the faces at twice the flux: mean 0.75, mean of squares 0.625,
        so the surface costs 1,11 times what its mean face velocity asks."""
        self.write([0.5] * 8 + [1.0] * 8)
        self.assertAlmostEqual(
            post.flow_spread(self.step, "supply"), 0.625 / 0.75**2, places=3
        )

    def test_a_surface_no_flow_reaches_is_not_divided_by_zero(self):
        self.write([0.0] * 16)
        self.assertEqual(post.flow_spread(self.step, "supply"), 1.0)

    def test_the_direction_of_the_flux_does_not_change_the_spread(self):
        """A mesh fed from the other side is fed just as evenly."""
        self.write([-0.2] * 16)
        self.assertAlmostEqual(post.flow_spread(self.step, "supply"), 1.0, places=3)


class ResistanceVerdictTest(unittest.TestCase):
    """The closed form for THIS field, not for the drawing board (ADR-082)."""

    def test_an_evenly_fed_surface_is_judged_against_its_rated_drop(self):
        passed, why = post._resistance_verdict(2.4, 2.2)
        self.assertTrue(passed)
        self.assertEqual(why, "")

    def test_a_badly_fed_surface_is_judged_against_what_its_k_asks_here(self):
        """0,88 Pa where the rating says 0,31 is 284% and looks like an
        instrument fault. Reached 2,9 times harder than the rating assumes,
        its own K asks 0,90 Pa of this field -- and 0,88 is 2% under it."""
        passed, why = post._resistance_verdict(0.88, 0.308, 2.925)
        self.assertTrue(passed)
        self.assertIn("times harder", why)
        self.assertIn("0.90 Pa", why)

    def test_a_surface_that_is_not_delivering_its_k_still_fails(self):
        """The correction is for the flow, not for the resistance: half the
        drop under the same distribution is still half the drop."""
        self.assertFalse(post._resistance_verdict(0.30, 0.308, 2.06)[0])

    def test_an_even_surface_says_nothing_about_spreading(self):
        self.assertEqual(post._resistance_verdict(2.2, 2.2, 1.01)[1], "")


class ReverseFlowTest(unittest.TestCase):
    """Air crossing a surface backwards is not free (ADR-082)."""

    def setUp(self):
        self.case = Path(tempfile.mkdtemp())
        self.step = self.case / "100"
        self.step.mkdir()

    def write(self, fluxes):
        field(self.step / "phi", "phi", "0", {
            "supply1_below": list(fluxes),
            "supply1_above": [-f for f in fluxes],
        })

    def test_a_surface_crossed_one_way_reverses_nothing(self):
        self.write([0.2] * 16)
        self.assertEqual(post.reverse_fraction(self.step, "supply"), 0.0)

    def test_what_goes_back_is_measured_against_the_net(self):
        """Twelve faces out at 0,2 and four back at 0,1: the net is 2,0 and
        0,4 of it returns."""
        self.write([0.2] * 12 + [-0.1] * 4)
        self.assertAlmostEqual(
            post.reverse_fraction(self.step, "supply"), 0.4 / 2.0, places=4
        )

    def test_the_spread_divides_by_the_NET_face_velocity(self):
        """The fault that left 38% of a measured drop unexplained. Dividing by
        the mean magnitude credits the surface for its own return flow: here
        the mean magnitude is 0,175 and the net per face 0,125, and a
        resistance follows the second."""
        self.write([0.2] * 12 + [-0.1] * 4)
        mean_square = (12 * 0.2**2 + 4 * 0.1**2) / 16
        self.assertAlmostEqual(
            post.flow_spread(self.step, "supply"),
            mean_square / (2.0 / 16) ** 2, places=3,
        )
        # and it is emphatically not the mean-magnitude answer
        self.assertNotAlmostEqual(
            post.flow_spread(self.step, "supply"),
            mean_square / 0.175**2, places=2,
        )

    def test_a_surface_that_only_churns_has_no_rated_velocity(self):
        """Equal flow each way: the net is nothing, so there is no face
        velocity to be measured against and no ratio to report."""
        self.write([0.2] * 8 + [-0.2] * 8)
        self.assertEqual(post.flow_spread(self.step, "supply"), 1.0)

    def test_the_verdict_says_both_faults_apart(self):
        """Uneven flow wants a deeper plenum; air going round in circles wants
        the units aimed differently. One number, two remedies."""
        _passed, why = post._resistance_verdict(0.88, 0.308, 2.9, 0.097)
        self.assertIn("2.9 times harder", why)
        self.assertIn("10% of the mass", why)
        self.assertNotIn("going back the other way",
                         post._resistance_verdict(2.46, 2.20, 1.06, 0.0)[1])
