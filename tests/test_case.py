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
from aicfd import case
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


def build(**overrides) -> m.Model:
    spec = copy.deepcopy(SPEC)
    spec.update(overrides)
    return m.build_model(spec)


class PipelineTest(unittest.TestCase):
    def test_createbaffles_is_told_to_overwrite(self):
        """Without -overwrite it writes the new mesh where nothing reads it."""
        entry = next(e for e in case.PIPELINE if "createBaffles" in str(e))
        self.assertEqual(entry, ("createBaffles", ["-overwrite"]))

    def test_the_order_is_mesh_then_surgery_then_check_then_solve(self):
        names = [e[0] if isinstance(e, tuple) else e for e in case.PIPELINE]
        self.assertEqual(
            names,
            ["blockMesh", "topoSet", "createBaffles", "checkMesh", "buoyantSimpleFoam"],
        )

    def test_the_mesh_pipeline_stops_before_the_solver(self):
        self.assertNotIn("buoyantSimpleFoam", str(case.MESH_PIPELINE))


class TopoSetTest(unittest.TestCase):
    def setUp(self):
        self.model = build()
        self.text = case.topo_set_dict(self.model)

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
            self.assertLess(thickness, min(self.model.cell_size))

    def test_each_rack_gets_its_own_cell_zone(self):
        for rack in self.model.racks:
            self.assertIn(f"cellZone        {rack.id}", case.fv_options(self.model))
            self.assertIn(f"name    {rack.id}", self.text)


class BafflesTest(unittest.TestCase):
    def setUp(self):
        self.model = build()
        self.text = case.create_baffles_dict(self.model)

    def test_nothing_may_reach_the_outer_boundary(self):
        self.assertIn("internalFacesOnly true", self.text)

    def test_walls_are_patch_pairs_and_the_rest_are_explicit_patches(self):
        self.assertEqual(self.text.count("patchPairs"), len(case.wall_plan(self.model)))
        # the fan, plus every perforated surface: the ceiling grilles and the
        # mesh closing the plenum where it opens into the gallery (ADR-048)
        self.assertEqual(self.text.count("patches\n        {"),
                         1 + len(case.porous(self.model)))

    def test_a_grille_with_free_area_becomes_a_cyclic_pair_with_a_pressure_jump(self):
        model = build(grilles={"size": 0.6, "count": 3, "free_area": 0.8})
        text = case.create_baffles_dict(model)
        # two sides each, for the three grilles and the one gallery opening
        self.assertEqual(text.count("porousBafflePressure"),
                         2 * len(case.porous(model)))
        self.assertEqual(len(case.porous(model)), 4)
        self.assertIn("neighbourPatch  grille1_above", text)
        k = model.panel("grille1").resistance
        self.assertIn(f"I {k:.4g}", text)
        # everything but pressure passes straight through
        self.assertIn("U       { type cyclic; }", text)

    def test_grilles_get_their_own_face_zones(self):
        model = build(grilles={"size": 0.6, "count": 3, "free_area": 0.8})
        text = case.topo_set_dict(model)
        self.assertIn("name    grille1;\n        type    faceZoneSet;", text)

    def test_both_sides_of_the_fan_move_the_same_mass(self):
        """Volume would not do: the air leaving is warmer and thinner."""
        mass = self.model.airflow_m3s * case.supply_density(self.model)
        self.assertEqual(self.text.count(f"massFlowRate {mass:.6g}"), 2)
        self.assertIn("flowRateInletVelocity", self.text)
        self.assertIn("flowRateOutletVelocity", self.text)

    def test_nothing_can_blow_backwards_through_the_fan(self):
        """A pressure-driven intake let 2.3x the net flow reverse through it."""
        self.assertNotIn("type pressureInletOutletVelocity", self.text)
        # The fan's own entry, not whatever the first `slave` in the file
        # belongs to: the perforated surfaces are written before it.
        start = self.text.index(case.FAN_INTAKE)
        intake = self.text[start : self.text.index("slave", start)]
        self.assertIn("flowRateOutletVelocity", intake)
        # The patch is written as inletOutlet only so it stores a value the
        # energy balance can read; what it would admit is supply air, never a
        # guessed return that would invent heat.
        supply_k = self.model.supply_temp_c + case.KELVIN
        self.assertIn(f"inletValue uniform {supply_k:.2f}", intake)

    def test_the_supply_density_is_the_one_the_solver_will_compute(self):
        density = case.supply_density(self.model)
        self.assertAlmostEqual(density, 1.2041, places=3)

    def test_the_supply_carries_the_supply_temperature(self):
        supply_k = self.model.supply_temp_c + case.KELVIN
        supply = self.text[self.text.index(case.FAN_SUPPLY) :]
        self.assertIn(f"uniform {supply_k:.2f}", supply)

    def test_no_patch_fixes_the_pressure_so_the_solver_is_given_a_reference(self):
        """Both fan patches fix mass flow, which leaves p_rgh's level free."""
        self.assertNotIn("type prghPressure", self.text)
        self.assertNotIn("p_rgh   { type fixedValue", self.text)
        solution = case.fv_solution(self.model, 1e-4)
        self.assertIn("pRefCell", solution)
        self.assertIn("pRefValue       101325", solution)

    def test_every_field_the_solver_reads_gets_a_condition(self):
        for name in ("U", "T", "p_rgh", "p", "k", "epsilon", "nut", "alphat"):
            self.assertIn(f"{name} ", self.text)


class PorosityTest(unittest.TestCase):
    def test_the_rack_passes_the_axis_it_breathes_along(self):
        """The row is turned 90 degrees to the aisle, so the flow axis is y."""
        text = case.fv_options(build())
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
        text = case.fv_options(model)
        self.assertEqual(text.count("scalarSemiImplicitSource;"), len(model.racks))
        self.assertIn(f"h           ({model.racks[0].load_w:g} 0)", text)


class FieldsTest(unittest.TestCase):
    def test_the_zero_directory_covers_the_outer_walls_only(self):
        """The baffle patches do not exist yet; createBafflesDict declares them."""
        fields = case.initial_fields(build())
        for name, text in fields.items():
            self.assertNotIn(case.FAN_SUPPLY, text, name)

    def test_every_field_the_solver_needs_is_written(self):
        fields = case.initial_fields(build())
        self.assertEqual(
            set(fields),
            {"U", "T", "p_rgh", "p", "k", "epsilon", "nut", "alphat"},
        )

    def test_epsilon_is_derived_from_k_and_a_room_scale_length(self):
        model = build()
        k, epsilon = case.turbulence_initial_values(model)
        length = case.LENGTH_SCALE_FRACTION * model.domain.size[2]
        self.assertAlmostEqual(epsilon, case.C_MU**0.75 * k**1.5 / length)

    def test_the_velocity_scale_is_buoyant_when_the_supply_is_slower(self):
        """A 0,19 m/s supply face does not set the turbulence of a 8 m room."""
        model = build()
        k, _ = case.turbulence_initial_values(model)
        from_supply = 1.5 * (model.face_velocity("fan") * case.TURBULENCE_INTENSITY) ** 2
        self.assertGreater(k, from_supply * 10)


class WarmStartTest(unittest.TestCase):
    """A steady solve's initial condition is free; a cold one is not cheap."""

    def setUp(self):
        self.model = build()
        self.field = case.warm_start(self.model)
        self.values = [
            float(v) for v in re.findall(r"^\d+\.\d\d$", self.field, re.M)
        ]

    def test_there_is_one_value_per_cell(self):
        self.assertIn(f"\n{self.model.n_cells}\n", self.field)
        self.assertEqual(len(self.values), self.model.n_cells)

    def test_only_two_temperatures_are_seeded(self):
        """The shape of the answer, not a guess at its numbers."""
        supply = self.model.supply_temp_c + case.KELVIN
        self.assertEqual(len(set(self.values)), 2)
        self.assertAlmostEqual(min(self.values), supply, places=1)
        self.assertGreater(max(self.values), supply)

    def test_the_cold_aisle_starts_cold_and_the_gallery_warm(self):
        model = self.model
        nx, ny, _nz = model.divisions
        cx, cy, cz = model.cell_size
        supply = model.supply_temp_c + case.KELVIN

        def at(x, y, z):
            i, j, k = int(x / cx), int(y / cy), int(z / cz)
            return self.values[i + j * nx + k * nx * ny]

        cold_y = (model.cold_aisle[0] + model.cold_aisle[1]) / 2
        hot_y = (model.hot_aisle[0] + model.hot_aisle[1]) / 2
        row = model.rack_span()
        self.assertAlmostEqual(at(6.0, cold_y, 1.0), supply, places=1)
        self.assertGreater(at((row[0] + row[1]) / 2, hot_y, 1.0), supply)
        self.assertGreater(at(1.0, cold_y, 1.0), supply)  # the gallery
        self.assertGreater(at(6.0, cold_y, model.ceiling_z + 0.5), supply)  # plenum

    def test_the_field_is_written_into_the_zero_directory(self):
        text = case.initial_fields(self.model)["T"]
        self.assertIn("nonuniform List<scalar>", text)
        self.assertNotIn("internalField   uniform", text)

    def test_the_seed_can_be_turned_off_to_check_it(self):
        """The same case from a uniform field is what licenses the seed."""
        text = case.initial_fields(self.model, warm=False)["T"]
        self.assertIn("internalField   uniform", text)
        self.assertNotIn("nonuniform List<scalar>", text)


class BlockMeshTest(unittest.TestCase):
    def test_the_box_is_closed(self):
        """A POD's air never leaves the domain: the fan wall is where it cuts."""
        text = case.block_mesh_dict(build())
        self.assertEqual(text.count("type wall"), len(case.OUTER_PATCHES))
        self.assertNotIn("type patch", text)

    def test_the_divisions_follow_the_model(self):
        model = build()
        text = case.block_mesh_dict(model)
        self.assertIn(f"({' '.join(str(n) for n in model.divisions)})", text)


class SummaryTest(unittest.TestCase):
    def test_it_names_every_surface_the_surgery_builds(self):
        model = build()
        text = case.summary(model)
        for name, _panel, _holes in case.wall_plan(model):
            self.assertIn(name, text)
        self.assertIn(case.FAN_SUPPLY, text)


if __name__ == "__main__":
    unittest.main()


class SupplyPlenumCaseTest(unittest.TestCase):
    """What the solver is given for a plenum (ADR-058)."""

    def model(self, **plenum):
        spec = support.spec("pod-plenum")
        spec["plenum"] = {**spec.get("plenum", {}), "enabled": True, **plenum}
        return m.build_model(spec)

    def zones(self, model):
        return {name: (walls, holes) for name, walls, holes in case.wall_plan(model)}

    def test_the_wall_the_units_are_in_is_untouched(self):
        """The plenum is a second leaf. The existing wall still carries the
        fan walls and the opening into the return plenum, and nothing else."""
        zones = self.zones(self.model())
        _walls, holes = zones["divider"]
        self.assertEqual(sorted(h.name for h in holes), ["fan", "plenum_opening"])

    def test_the_grilles_pierce_the_second_leaf(self):
        zones = self.zones(self.model())
        self.assertIn("plenum_wall", zones)
        _walls, holes = zones["plenum_wall"]
        self.assertEqual([h.name for h in holes], ["supply1"])

    def test_a_supply_grille_is_a_porous_baffle_like_the_return_ones(self):
        model = self.model()
        self.assertIn("supply1", [p.name for p in case.porous(model)])
        text = case.create_baffles_dict(model)
        self.assertIn("supply1_below", text)
        self.assertIn("supply1_above", text)
        self.assertIn("porousBafflePressure", text)

    def test_every_surface_the_plenum_adds_is_written(self):
        text = case.topo_set_dict(self.model())
        for name in ("plenum_wall", "supply1"):
            with self.subTest(surface=name):
                self.assertIn(name, text)

    def test_a_shut_grille_leaves_the_leaf_solid(self):
        model = self.model(closed=["supply1"])
        self.assertEqual([p for p in model.panels if p.name.startswith("supply")], [])
        _walls, holes = self.zones(model)["plenum_wall"]
        self.assertEqual(holes, [])


class SurfaceListingTest(unittest.TestCase):
    """The summary's list of what createBaffles builds has to be all of it."""

    def test_a_porous_surface_that_is_nobody_s_hole_is_listed(self):
        """A grille punched through a wall is counted on that wall's own line
        -- "less 3 opening(s)" -- which is the readable way round. The mesh
        leaf is a surface on its own, so no wall's line mentions it, and it
        was built and not listed at all (ADR-060)."""
        model = m.build_model(support.spec("pod-mesh"))
        listed = case.summary(model).split(
            "Internal surfaces built by createBaffles:")[1]
        holes = {h.name for _z, _w, hs in case.wall_plan(model) for h in hs}
        standalone = [p for p in case.porous(model) if p.name not in holes]
        self.assertTrue(standalone, "this case has a surface of its own")
        for panel in standalone:
            with self.subTest(surface=panel.name):
                self.assertIn(panel.name, listed)

    def test_a_grille_in_a_wall_is_counted_on_that_wall(self):
        model = m.build_model(support.spec("pod-plenum"))
        listed = case.summary(model).split(
            "Internal surfaces built by createBaffles:")[1]
        self.assertIn("less 3 opening(s)", listed)   # the ceiling grilles
        self.assertIn("less 1 opening(s)", listed)   # the supply grille


class DownflowCaseTest(unittest.TestCase):
    """What a raised-floor case hands OpenFOAM (ADR-076)."""

    def model(self, **floor):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["fanwall"]["model"] = "HDCV5300F-HT"
        spec["floor"] = {"enabled": True, "height": 1.0,
                         "tiles_per_rack": 2, **floor}
        return m.build_model(spec)

    def test_the_deck_is_a_wall_with_the_plates_cut_out_of_it(self):
        model = self.model()
        plan = dict((name, (panels, holes))
                    for name, panels, holes in case.wall_plan(model))
        self.assertIn("piso", plan)
        panels, holes = plan["piso"]
        self.assertEqual([p.name for p in panels], ["floor_deck"])
        names = {p.name for p in holes}
        self.assertTrue(any(n.startswith("tile_") for n in names), names)
        # The unit discharges through the deck too, so its face is a hole.
        self.assertTrue(any(p.kind == "fan" for p in holes))

    def test_the_supply_opening_is_a_hole_in_the_dividing_wall(self):
        model = self.model()
        holes = next(holes for name, _p, holes in case.wall_plan(model)
                     if name == "divider")
        self.assertIn("floor_opening", {p.name for p in holes})

    def test_the_plates_and_the_opening_are_porous_surfaces(self):
        names = {p.name for p in case.porous(self.model())}
        self.assertIn("floor_opening", names)
        self.assertTrue(any(n.startswith("tile_") for n in names))

    def test_the_unit_becomes_two_baffles_with_one_live_side_each(self):
        """Give both sides of both planes a condition and the unit runs twice."""
        text = case.create_baffles_dict(self.model())
        self.assertIn("fan_supply", text)
        self.assertIn("fan_return", text)
        for patch in ("fanSupply", "fanIntake"):
            self.assertIn(f"name    {patch};", text)
        for backing in ("fanSupplyBack", "fanIntakeBack"):
            self.assertIn(f"name    {backing};", text)
        # Each plane carries exactly one mass-flow condition.
        self.assertEqual(text.count("flowRateInletVelocity"), 1)
        self.assertEqual(text.count("flowRateOutletVelocity"), 1)

    def test_the_intake_stores_a_temperature(self):
        """zeroGradient stores nothing, and the air crossing there is what the
        energy balance is built from."""
        text = case.create_baffles_dict(self.model())
        supply = text.index("fan_supply")
        self.assertIn("inletOutlet", text[text.index("fan_return"):])
        self.assertLess(supply, text.index("fan_return"))

    def test_both_faces_get_their_own_face_zone(self):
        text = case.topo_set_dict(self.model())
        self.assertIn("name    fan_supply;", text)
        self.assertIn("name    fan_return;", text)

    def test_a_fan_wall_case_is_untouched(self):
        """One plane, one baffle, the names it always had."""
        flat = m.build_model(support.spec("pod-fanwall"))
        text = case.create_baffles_dict(flat)
        self.assertNotIn("fanSupplyBack", text)
        self.assertNotIn("floor_opening", case.topo_set_dict(flat))
        self.assertEqual([p.name for p in flat.fans], ["fan"])


class EveryWallIsMeshedTest(unittest.TestCase):
    """A surface the model draws has to be a surface the solver has.

    The blanking panel was in `model.panels`, on the section drawing and in the
    row summary, and it was in no face zone -- so `createBaffles` never built
    one and the solver saw an open hole where the plate is. On the hall it was
    found in, 3,83 m3/s came out of one blanked position at 2,8 m/s, 28% of
    what the whole row passed, and the row delivered 54% of the resistance its
    curve asks for (ADR-081).

    This is the guard. It walks every shipped case and asserts that each wall
    panel the model carries is either built as a wall or punched as a hole in
    one, because either is a decision; being in neither is an oversight.
    """

    CASES = sorted(Path(__file__).resolve().parent.parent.glob("cases/*.yaml"))

    def test_every_case_ships_a_mesh_for_every_wall_it_draws(self):
        self.assertTrue(self.CASES, "no cases to check")
        for path in self.CASES:
            with self.subTest(case=path.name):
                model = m.build_model(yaml.safe_load(path.read_text()))
                plan = case.wall_plan(model)
                built = {p.name for _n, panels, _h in plan for p in panels}
                holes = {p.name for _n, _p, hs in plan for p in hs}
                missing = sorted(
                    p.name for p in model.panels
                    if p.kind == "wall" and p.name not in built | holes
                )
                self.assertEqual(missing, [], f"drawn but never meshed: {missing}")

    def test_a_blanked_position_is_one_of_them(self):
        """The case that caught it, stated as a case rather than a sweep."""
        spec = copy.deepcopy(SPEC)
        spec["racks"]["row"] = [{}, {"blank": True}, {}]
        model = m.build_model(spec)
        blanks = [p for p in model.panels if p.name.startswith("blank")]
        self.assertTrue(blanks, "the spec asked for a blanking panel")
        zoned = {p.name for _n, panels, _h in case.wall_plan(model) for p in panels}
        for panel in blanks:
            self.assertIn(panel.name, zoned)

    def test_the_plate_stands_on_the_cabinets_own_face(self):
        """Not across the whole position: the space behind a blanking plate is
        part of the contained aisle, as it is in a real row."""
        spec = copy.deepcopy(SPEC)
        spec["racks"]["row"] = [{}, {"blank": True}, {}]
        model = m.build_model(spec)
        row = model.rows[0]
        for panel in [p for p in model.panels if p.name.startswith("blank")]:
            self.assertEqual(panel.axis, 1)
            self.assertAlmostEqual(panel.position, row.front_y)
