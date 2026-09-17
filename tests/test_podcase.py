"""Tests for the fan-wall POD case generator.

These check the dictionary text, not a solve: what they are guarding is the
handful of choices that produce a case which runs, converges and is wrong --
a face selection that also caught the faces crossing it, a rack resisting the
axis it should pass, a mesh-modifying utility writing somewhere nobody reads.
"""

from __future__ import annotations

import copy
import re
import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import model as m
from aicfd import podcase

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


def build(**overrides) -> m.Model:
    spec = copy.deepcopy(SPEC)
    spec.update(overrides)
    return m.build_model(spec)


class PipelineTest(unittest.TestCase):
    def test_createbaffles_is_told_to_overwrite(self):
        """Without -overwrite it writes the new mesh where nothing reads it."""
        entry = next(e for e in podcase.PIPELINE if "createBaffles" in str(e))
        self.assertEqual(entry, ("createBaffles", ["-overwrite"]))

    def test_the_order_is_mesh_then_surgery_then_check_then_solve(self):
        names = [e[0] if isinstance(e, tuple) else e for e in podcase.PIPELINE]
        self.assertEqual(
            names,
            ["blockMesh", "topoSet", "createBaffles", "checkMesh", "buoyantSimpleFoam"],
        )

    def test_the_mesh_pipeline_stops_before_the_solver(self):
        self.assertNotIn("buoyantSimpleFoam", str(podcase.MESH_PIPELINE))


class TopoSetTest(unittest.TestCase):
    def setUp(self):
        self.model = build()
        self.text = podcase.topo_set_dict(self.model)

    def test_every_face_selection_is_narrowed_by_normal(self):
        """boxToFace takes any face whose centre is in the box, including the
        ones running perpendicular through it."""
        boxes = self.text.count("source  boxToFace")
        normals = self.text.count("source  normalToFace")
        zones = self.text.count("type    faceZoneSet")
        # One normalToFace per surface; the extra boxToFace calls are the holes.
        self.assertEqual(normals, zones)
        self.assertGreater(boxes, normals)

    def test_the_divider_is_holed_by_the_fan_and_the_plenum_opening(self):
        divider = self.text[self.text.index("dividerFaces") : self.text.index("forroFaces")]
        self.assertEqual(divider.count("action  delete"), 2)

    def test_the_false_ceiling_is_holed_by_every_grille(self):
        forro = self.text[self.text.index("forroFaces") :]
        forro = forro[: forro.index("containment")]
        self.assertEqual(forro.count("action  delete"), 3)

    def test_the_selection_box_is_thinner_than_a_cell(self):
        """A thicker box would catch the faces one row either side."""
        face_boxes = re.findall(
            r"source  boxToFace;\s*\n\s*box     \(([^)]*)\) \(([^)]*)\)", self.text
        )
        self.assertTrue(face_boxes)
        for lo, hi in face_boxes:
            thickness = min(
                abs(float(b) - float(a)) for a, b in zip(lo.split(), hi.split())
            )
            self.assertLess(thickness, self.model.cell_size)

    def test_each_rack_gets_its_own_cell_zone(self):
        for rack in self.model.racks:
            self.assertIn(f"cellZone        {rack.id}", podcase.fv_options(self.model))
            self.assertIn(f"name    {rack.id}", self.text)


class BafflesTest(unittest.TestCase):
    def setUp(self):
        self.model = build()
        self.text = podcase.create_baffles_dict(self.model)

    def test_nothing_may_reach_the_outer_boundary(self):
        self.assertIn("internalFacesOnly true", self.text)

    def test_the_fan_is_the_only_pair_that_is_not_a_wall(self):
        self.assertEqual(self.text.count("patchPairs"), len(podcase.wall_plan(self.model)))
        self.assertEqual(self.text.count("patches\n        {"), 1)

    def test_both_sides_of_the_fan_move_the_same_mass(self):
        """Volume would not do: the air leaving is warmer and thinner."""
        mass = self.model.airflow_m3s * podcase.supply_density(self.model)
        self.assertEqual(self.text.count(f"massFlowRate {mass:.6g}"), 2)
        self.assertIn("flowRateInletVelocity", self.text)
        self.assertIn("flowRateOutletVelocity", self.text)

    def test_nothing_can_blow_backwards_through_the_fan(self):
        """A pressure-driven intake let 2.3x the net flow reverse through it."""
        self.assertNotIn("type pressureInletOutletVelocity", self.text)
        intake = self.text[self.text.index(podcase.FAN_INTAKE) : self.text.index("slave")]
        self.assertIn("flowRateOutletVelocity", intake)
        # The patch is written as inletOutlet only so it stores a value the
        # energy balance can read; what it would admit is supply air, never a
        # guessed return that would invent heat.
        supply_k = self.model.supply_temp_c + podcase.KELVIN
        self.assertIn(f"inletValue uniform {supply_k:.2f}", intake)

    def test_the_supply_density_is_the_one_the_solver_will_compute(self):
        density = podcase.supply_density(self.model)
        self.assertAlmostEqual(density, 1.2041, places=3)

    def test_the_supply_carries_the_supply_temperature(self):
        supply_k = self.model.supply_temp_c + podcase.KELVIN
        supply = self.text[self.text.index(podcase.FAN_SUPPLY) :]
        self.assertIn(f"uniform {supply_k:.2f}", supply)

    def test_no_patch_fixes_the_pressure_so_the_solver_is_given_a_reference(self):
        """Both fan patches fix mass flow, which leaves p_rgh's level free."""
        self.assertNotIn("type prghPressure", self.text)
        self.assertNotIn("p_rgh   { type fixedValue", self.text)
        solution = podcase.fv_solution(self.model, 1e-4)
        self.assertIn("pRefCell", solution)
        self.assertIn("pRefValue       101325", solution)

    def test_every_field_the_solver_reads_gets_a_condition(self):
        for name in ("U", "T", "p_rgh", "p", "k", "epsilon", "nut", "alphat"):
            self.assertIn(f"{name} ", self.text)


class PorosityTest(unittest.TestCase):
    def test_the_rack_passes_the_axis_it_breathes_along(self):
        """The row is turned 90 degrees to the aisle, so the flow axis is y."""
        text = podcase.fv_options(build())
        self.assertIn("e1      (0 1 0)", text)
        self.assertNotIn("e1      (1 0 0)", text)

    def test_the_blocked_axes_carry_the_large_coefficients(self):
        model = build()
        d_flow, f_flow = model.racks[0].darcy_forchheimer()
        self.assertLess(d_flow, m.BLOCKED_D)
        self.assertLess(f_flow, m.BLOCKED_F)

    def test_resistance_rises_as_the_rack_is_asked_to_pass_less_air(self):
        light = build(racks={**SPEC["racks"], "load_kw": 2.0})
        heavy = build(racks={**SPEC["racks"], "load_kw": 12.0})
        self.assertGreater(
            light.racks[0].darcy_forchheimer()[1],
            heavy.racks[0].darcy_forchheimer()[1],
        )

    def test_the_whole_load_becomes_a_heat_source(self):
        model = build()
        text = podcase.fv_options(model)
        self.assertEqual(text.count("scalarSemiImplicitSource;"), len(model.racks))
        self.assertIn(f"h           ({model.racks[0].load_w:g} 0)", text)


class FieldsTest(unittest.TestCase):
    def test_the_zero_directory_covers_the_outer_walls_only(self):
        """The baffle patches do not exist yet; createBafflesDict declares them."""
        fields = podcase.initial_fields(build())
        for name, text in fields.items():
            self.assertNotIn(podcase.FAN_SUPPLY, text, name)

    def test_every_field_the_solver_needs_is_written(self):
        fields = podcase.initial_fields(build())
        self.assertEqual(
            set(fields),
            {"U", "T", "p_rgh", "p", "k", "epsilon", "nut", "alphat"},
        )

    def test_epsilon_is_derived_from_k_and_a_room_scale_length(self):
        model = build()
        k, epsilon = podcase.turbulence_initial_values(model)
        length = podcase.LENGTH_SCALE_FRACTION * model.domain.size[2]
        self.assertAlmostEqual(epsilon, podcase.C_MU**0.75 * k**1.5 / length)

    def test_the_velocity_scale_is_buoyant_when_the_supply_is_slower(self):
        """A 0,19 m/s supply face does not set the turbulence of a 8 m room."""
        model = build()
        k, _ = podcase.turbulence_initial_values(model)
        from_supply = 1.5 * (model.face_velocity("fan") * podcase.TURBULENCE_INTENSITY) ** 2
        self.assertGreater(k, from_supply * 10)


class WarmStartTest(unittest.TestCase):
    """A steady solve's initial condition is free; a cold one is not cheap."""

    def setUp(self):
        self.model = build()
        self.field = podcase.warm_start(self.model)
        self.values = [
            float(v) for v in re.findall(r"^\d+\.\d\d$", self.field, re.M)
        ]

    def test_there_is_one_value_per_cell(self):
        self.assertIn(f"\n{self.model.n_cells}\n", self.field)
        self.assertEqual(len(self.values), self.model.n_cells)

    def test_only_two_temperatures_are_seeded(self):
        """The shape of the answer, not a guess at its numbers."""
        supply = self.model.supply_temp_c + podcase.KELVIN
        self.assertEqual(len(set(self.values)), 2)
        self.assertAlmostEqual(min(self.values), supply, places=1)
        self.assertGreater(max(self.values), supply)

    def test_the_cold_aisle_starts_cold_and_the_gallery_warm(self):
        model = self.model
        nx, ny, _nz = model.divisions
        cell = model.cell_size
        supply = model.supply_temp_c + podcase.KELVIN

        def at(x, y, z):
            i, j, k = (int(v / cell) for v in (x, y, z))
            return self.values[i + j * nx + k * nx * ny]

        cold_y = (model.cold_aisle[0] + model.cold_aisle[1]) / 2
        hot_y = (model.hot_aisle[0] + model.hot_aisle[1]) / 2
        row = model.rack_span()
        self.assertAlmostEqual(at(6.0, cold_y, 1.0), supply, places=1)
        self.assertGreater(at((row[0] + row[1]) / 2, hot_y, 1.0), supply)
        self.assertGreater(at(1.0, cold_y, 1.0), supply)  # the gallery
        self.assertGreater(at(6.0, cold_y, model.ceiling_z + 0.5), supply)  # plenum

    def test_the_field_is_written_into_the_zero_directory(self):
        text = podcase.initial_fields(self.model)["T"]
        self.assertIn("nonuniform List<scalar>", text)
        self.assertNotIn("internalField   uniform", text)

    def test_the_seed_can_be_turned_off_to_check_it(self):
        """The same case from a uniform field is what licenses the seed."""
        text = podcase.initial_fields(self.model, warm=False)["T"]
        self.assertIn("internalField   uniform", text)
        self.assertNotIn("nonuniform List<scalar>", text)


class BlockMeshTest(unittest.TestCase):
    def test_the_box_is_closed(self):
        """A POD's air never leaves the domain: the fan wall is where it cuts."""
        text = podcase.block_mesh_dict(build())
        self.assertEqual(text.count("type wall"), len(podcase.OUTER_PATCHES))
        self.assertNotIn("type patch", text)

    def test_the_divisions_follow_the_model(self):
        model = build()
        text = podcase.block_mesh_dict(model)
        self.assertIn(f"({' '.join(str(n) for n in model.divisions)})", text)


class CliRoutingTest(unittest.TestCase):
    """The spec's shape picks the generator, not a flag to remember."""

    def test_a_pod_spec_is_recognised(self):
        from aicfd.cli import is_pod_spec

        path = Path(tempfile.mkdtemp()) / "pod.yaml"
        path.write_text(yaml.safe_dump(SPEC))
        self.assertTrue(is_pod_spec(path))

    def test_an_m2_room_spec_is_not(self):
        from aicfd.cli import is_pod_spec

        path = Path(tempfile.mkdtemp()) / "room.yaml"
        path.write_text(
            yaml.safe_dump({"name": "r", "room": {"size": [8, 5, 3]}, "cracs": []})
        )
        self.assertFalse(is_pod_spec(path))

    def test_something_that_is_not_yaml_is_not_a_pod(self):
        from aicfd.cli import is_pod_spec

        path = Path(tempfile.mkdtemp()) / "nope.yaml"
        path.write_text(": : not yaml : :")
        self.assertFalse(is_pod_spec(path))


class SummaryTest(unittest.TestCase):
    def test_it_names_every_surface_the_surgery_builds(self):
        model = build()
        text = podcase.summary(model)
        for name, _panel, _holes in podcase.wall_plan(model):
            self.assertIn(name, text)
        self.assertIn(podcase.FAN_SUPPLY, text)


if __name__ == "__main__":
    unittest.main()
