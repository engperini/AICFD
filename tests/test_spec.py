"""Tests for the room spec and the case generator.

These guard the layer an engineer actually touches: the validation messages
they will read when they mistype something, and the unit conversions that
decide whether the physics is representative.
"""

import re
import tempfile
import unittest
from pathlib import Path

from aicfd import case as case_builder
from aicfd.spec import SpecError, from_dict, load

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = REPO_ROOT / "cases" / "datahall-small.yaml"


def minimal(**overrides) -> dict:
    spec = {
        "name": "t",
        "room": {"size": [8.0, 5.0, 3.0]},
        "racks": [
            {"id": "A1", "position": [3.0, 1.0], "size": [1.0, 0.6, 2.0], "load_kw": 6.0}
        ],
        "cracs": [{"id": "C1", "airflow_m3h": 1800, "supply_temp_c": 20.0}],
        "mesh": {"cell_size": 0.2},
    }
    spec.update(overrides)
    return spec


class DerivedQuantityTests(unittest.TestCase):
    def test_supply_velocity_comes_from_real_airflow(self):
        # 1800 m3/h over the 5 x 3 m wall = 0.5 m3/s / 15 m2.
        spec = from_dict(minimal())
        self.assertAlmostEqual(spec.supply_velocity_ms, 0.5 / 15.0, places=6)

    def test_bulk_delta_t(self):
        # 6 kW into 0.5 m3/s of air.
        spec = from_dict(minimal())
        self.assertAlmostEqual(spec.design_delta_t_k, 6000 / (0.5 * 1.19 * 1005), places=3)

    def test_rack_airflow_is_derived_from_load_when_absent(self):
        rack = from_dict(minimal()).racks[0]
        # 6 kW at the default 11 K rise.
        self.assertAlmostEqual(
            rack.rated_airflow_m3h, 6000 / (11.0 * 1.19 * 1005) * 3600, places=1
        )

    def test_rack_airflow_accepts_cfm(self):
        spec = from_dict(
            minimal(
                racks=[
                    {
                        "id": "A1",
                        "position": [3.0, 1.0],
                        "size": [1.0, 0.6, 2.0],
                        "load_kw": 6.0,
                        "airflow_cfm": 1000,
                    }
                ]
            )
        )
        self.assertAlmostEqual(spec.racks[0].rated_airflow_m3h, 1699.01, places=1)

    def test_flow_weighted_supply_temperature(self):
        spec = from_dict(
            minimal(
                cracs=[
                    {"id": "C1", "airflow_m3h": 1000, "supply_temp_c": 18.0},
                    {"id": "C2", "airflow_m3h": 3000, "supply_temp_c": 22.0},
                ]
            )
        )
        self.assertAlmostEqual(spec.supply_temp_c, 21.0, places=6)

    def test_darcy_forchheimer_reproduces_the_requested_pressure_drop(self):
        rack = from_dict(minimal()).racks[0]
        _, f = case_builder.darcy_forchheimer(rack)
        u, length, rho = rack.face_velocity_ms, rack.size[0], 1.19
        recovered = 0.5 * rho * f * u**2 * length
        self.assertAlmostEqual(recovered, rack.pressure_drop_pa, places=6)

    def test_mesh_divisions_follow_cell_size(self):
        spec = from_dict(minimal())
        self.assertEqual(spec.divisions, (40, 25, 15))
        self.assertEqual(spec.n_cells, 40 * 25 * 15)


class ValidationTests(unittest.TestCase):
    def assertSpecError(self, raw, pattern):
        with self.assertRaises(SpecError) as context:
            from_dict(raw)
        self.assertRegex(str(context.exception), pattern)

    def test_rack_outside_the_room(self):
        self.assertSpecError(
            minimal(
                racks=[{"id": "A1", "position": [7.5, 1.0], "size": [1.0, 0.6, 2.0]}]
            ),
            r"rack 'A1' extends .* outside the 0-8\.00 m room",
        )

    def test_overlapping_racks(self):
        self.assertSpecError(
            minimal(
                racks=[
                    {"id": "A1", "position": [3.0, 1.0], "size": [1.0, 0.6, 2.0]},
                    {"id": "A2", "position": [3.5, 1.0], "size": [1.0, 0.6, 2.0]},
                ]
            ),
            r"racks 'A1' and 'A2' overlap",
        )

    def test_duplicate_rack_ids(self):
        self.assertSpecError(
            minimal(
                racks=[
                    {"id": "A1", "position": [3.0, 1.0], "size": [1.0, 0.6, 2.0]},
                    {"id": "A1", "position": [5.0, 1.0], "size": [1.0, 0.6, 2.0]},
                ]
            ),
            r"two racks share the id 'A1'",
        )

    def test_crac_without_airflow_is_refused_not_guessed(self):
        self.assertSpecError(
            minimal(cracs=[{"id": "C1", "supply_temp_c": 20.0}]),
            r"airflow_m3h .* is required",
        )

    def test_no_cracs(self):
        self.assertSpecError(minimal(cracs=[]), r"no CRACs")

    def test_no_racks(self):
        self.assertSpecError(minimal(racks=[]), r"no racks")

    def test_room_in_millimetres_is_caught(self):
        self.assertSpecError(
            minimal(room={"size": [8000.0, 5000.0, 3000.0]}),
            r"Check the units",
        )

    def test_rack_thinner_than_two_cells(self):
        self.assertSpecError(
            minimal(mesh={"cell_size": 0.5}),
            r"rack 'A1' is thinner than two cells",
        )

    def test_mesh_too_large_to_finish(self):
        self.assertSpecError(minimal(mesh={"cell_size": 0.01}), r"will not finish")

    def test_unsupported_crac_placement_says_what_is_supported(self):
        self.assertSpecError(
            minimal(cracs=[{"id": "C1", "airflow_m3h": 1800, "patch": "zmax"}]),
            r"Only 'xmin'",
        )

    def test_malformed_vector_names_the_field(self):
        self.assertSpecError(minimal(room={"size": [8.0, 5.0]}), r"room\.size")


class WarningTests(unittest.TestCase):
    def test_over_ventilation_warns(self):
        # The reference case's failure mode, expressed as a spec.
        spec = from_dict(minimal(cracs=[{"id": "C1", "airflow_m3h": 77760}]))
        self.assertTrue(any("far more air" in w for w in spec.warnings), spec.warnings)

    def test_under_ventilation_warns(self):
        spec = from_dict(minimal(cracs=[{"id": "C1", "airflow_m3h": 500}]))
        self.assertTrue(any("short of air" in w for w in spec.warnings), spec.warnings)

    def test_a_balanced_room_warns_about_nothing(self):
        spec = from_dict(minimal(cracs=[{"id": "C1", "airflow_m3h": 1700}]))
        self.assertEqual(spec.warnings, [])


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.spec = from_dict(minimal())
        self.tmp = tempfile.TemporaryDirectory()
        self.case = case_builder.build(self.spec, Path(self.tmp.name) / "case")

    def tearDown(self):
        self.tmp.cleanup()

    def test_writes_a_complete_case(self):
        for relative in (
            "system/blockMeshDict",
            "system/topoSetDict",
            "system/controlDict",
            "system/fvSolution",
            "system/fvSchemes",
            "constant/fvOptions",
            "constant/g",
            "constant/thermophysicalProperties",
            "constant/turbulenceProperties",
            "0/U",
            "0/T",
            "0/p",
            "0/p_rgh",
            "0/k",
            "0/epsilon",
            "0/nut",
            "0/alphat",
        ):
            self.assertTrue((self.case / relative).exists(), relative)

    def test_the_generated_case_reads_back_identically(self):
        # The generator and the reader are independent; this is the round trip
        # that keeps them honest.
        from aicfd.foam.casedict import read_case

        geometry = read_case(self.case)
        self.assertEqual(geometry.room.size, self.spec.size)
        self.assertEqual(geometry.divisions, self.spec.divisions)
        self.assertEqual(geometry.total_load_w, self.spec.total_load_w)
        self.assertEqual(geometry.inlet_patch, "fanwall")
        self.assertAlmostEqual(
            geometry.inlet_velocity[0], self.spec.supply_velocity_ms, places=3
        )
        self.assertIn("A1", geometry.zones)

    def test_rack_boxes_snap_to_cell_boundaries(self):
        # boxToCell selects by cell centre, so an edge landing mid-cell makes
        # the selection depend on floating-point rounding.
        text = (self.case / "system/topoSetDict").read_text()
        box = re.search(r"box\s+\(([^)]*)\)\s*\(([^)]*)\)", text)
        cell = self.spec.cell_size_actual
        for group in box.groups():
            for axis, value in enumerate(float(v) for v in group.split()):
                steps = value / cell[axis]
                self.assertAlmostEqual(steps, round(steps), places=6)


    def test_endtime_and_residual_control_agree_with_the_spec(self):
        control = (self.case / "system/controlDict").read_text()
        solution = (self.case / "system/fvSolution").read_text()
        self.assertIn(f"endTime         {self.spec.solver.max_iterations}", control)
        self.assertIn("residualControl", solution)
        self.assertIn(f"U               {self.spec.solver.residual_tolerance:g}", solution)

    def test_every_rack_gets_a_zone_a_resistance_and_a_heat_source(self):
        spec = from_dict(
            minimal(
                racks=[
                    {"id": "A1", "position": [3.0, 1.0], "size": [1.0, 0.6, 2.0], "load_kw": 6},
                    {"id": "A2", "position": [3.0, 2.0], "size": [1.0, 0.6, 2.0], "load_kw": 9},
                ]
            )
        )
        case = case_builder.build(spec, Path(self.tmp.name) / "multi")
        topo = (case / "system/topoSetDict").read_text()
        options = (case / "constant/fvOptions").read_text()
        for rack_id, watts in (("A1", 6000), ("A2", 9000)):
            self.assertIn(f"name    {rack_id};", topo)
            self.assertIn(f"{rack_id}Porosity", options)
            self.assertIn(f"h           ({watts} 0);", options)


class SnappingTests(unittest.TestCase):
    """Rack zones after the boxes are aligned to the mesh."""

    def boxes(self, y_a, y_b, cell=0.1):
        from aicfd.case import zone_boxes

        spec = from_dict(
            minimal(
                racks=[
                    {"id": "A", "position": [3.0, y_a], "size": [1.0, 0.6, 2.0], "load_kw": 5},
                    {"id": "B", "position": [3.0, y_b], "size": [1.0, 0.6, 2.0], "load_kw": 5},
                ],
                cracs=[{"id": "C1", "airflow_m3h": 3400}],
                mesh={"cell_size": cell},
            )
        )
        return spec, zone_boxes(spec)

    def test_snapping_preserves_rack_size_when_already_aligned(self):
        _, boxes = self.boxes(1.2, 2.0)
        self.assertAlmostEqual(boxes["A"][1][1] - boxes["A"][0][1], 0.6, places=6)

    def test_snapping_never_creates_an_overlap(self):
        # Rounding to nearest is monotonic, so disjoint stays disjoint even when
        # the racks sit off-grid and very close.
        for y_a, y_b in ((1.24, 1.86), (1.26, 1.94), (1.21, 1.82), (1.29, 1.9)):
            _, boxes = self.boxes(y_a, y_b)
            self.assertLessEqual(
                boxes["A"][1][1], boxes["B"][0][1] + 1e-9, f"{y_a} / {y_b}"
            )

    def test_a_gap_narrower_than_a_cell_closes_and_is_reported(self):
        spec, boxes = self.boxes(1.26, 1.94)  # a real 0.08 m gap
        self.assertAlmostEqual(boxes["A"][1][1], boxes["B"][0][1], places=6)
        self.assertTrue(
            any("closes the gap" in w for w in spec.warnings), spec.warnings
        )

    def test_a_gap_wider_than_a_cell_survives_silently(self):
        spec, boxes = self.boxes(1.2, 2.0)  # a 0.2 m aisle
        self.assertGreater(boxes["B"][0][1] - boxes["A"][1][1], 0)
        self.assertFalse([w for w in spec.warnings if "closes the gap" in w])


class ExampleCaseTests(unittest.TestCase):
    def test_the_bundled_example_is_valid_and_realistic(self):
        spec = load(EXAMPLE)
        self.assertGreater(len(spec.racks), 1)
        # The whole point of M2: a bulk rise a real hall would show, not the
        # 0.19 K the reference case produces.
        self.assertTrue(
            8.0 <= spec.design_delta_t_k <= 16.0,
            f"design delta-T is {spec.design_delta_t_k:.1f} K",
        )


if __name__ == "__main__":
    unittest.main()
