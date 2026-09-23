"""Tests for the data-hall layout: a POD's parts, repeated.

A hall is rows in pairs facing a contained hot aisle, cold aisles between the
pairs and round the edge, and a fan wall in front of every cold aisle. What
these check is that the repetition keeps every property the POD had -- the
loop closes, every plane is on the mesh, each unit gets its own boundary
pair -- and that the things that only exist with more than one of something
(fan walls that must not overlap, rows that face opposite ways) hold too.
"""

from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np
import yaml

from aicfd import model as M
from aicfd import case, post
from aicfd.case import KELVIN

SPEC = yaml.safe_load(
    """
name: h
gallery: {depth: 3.0}
hall: {height: 8.0, ceiling: 6.5}
pods: 3
aisles: {cold: 1.8, hot: 1.2, perimeter: 1.8}
racks: {per_row: 4, load_kw: 13.0, size: [0.6, 1.2, 2.25]}
fanwall: {width: 3.6, height: 4.0, airflow_m3h: 20000, supply_temp_c: 20.0,
          static_pressure_pa: 100}
grilles: {size: 0.6, loss_coefficient: 2.4, free_area: 0.8}
containment: {enabled: true}
mesh: {cell_size: [0.6, 0.3, 0.25]}
"""
)


def build(**overrides) -> M.Model:
    spec = copy.deepcopy(SPEC)
    for path, value in overrides.items():
        section, _, field = path.partition("__")
        if field:
            spec[section][field] = value
        else:
            spec[section] = value
    return M.build_model(spec)


class LayoutTest(unittest.TestCase):
    def test_a_pod_is_two_rows_and_the_hall_has_pods_of_them(self):
        model = build()
        self.assertEqual(len(model.rows), 6)
        self.assertEqual(len(model.racks), 6 * 4)
        self.assertEqual(len(model.hot_aisles), 3)
        self.assertEqual(len(model.cold_aisles), 4)  # between pods, plus both edges

    def test_the_two_rows_of_a_pod_face_each_other_across_the_hot_aisle(self):
        model = build()
        a, b = model.rows[0], model.rows[1]
        hot = model.hot_aisles[0]
        self.assertEqual(a.front_sign, +1)
        self.assertEqual(b.front_sign, -1)
        self.assertAlmostEqual(a.back_y, hot[0])
        self.assertAlmostEqual(b.back_y, hot[1])
        for rack in a.racks:
            self.assertEqual(rack.airflow_sign, +1)
        for rack in b.racks:
            self.assertEqual(rack.airflow_sign, -1)

    def test_the_bands_tile_the_width_with_no_gap(self):
        model = build()
        edges = sorted(
            [band for band in model.cold_aisles]
            + [band for band in model.hot_aisles]
            + [row.band for row in model.rows]
        )
        self.assertAlmostEqual(edges[0][0], 0.0)
        for (lo, hi), (next_lo, _) in zip(edges, edges[1:]):
            self.assertAlmostEqual(hi, next_lo)
        self.assertAlmostEqual(edges[-1][1], model.domain.hi[1])

    def test_the_perimeter_is_kept_along_the_rows_too(self):
        model = build()
        lo, hi = model.rack_span()
        self.assertAlmostEqual(lo - model.hall.lo[0], 1.8)
        self.assertAlmostEqual(model.hall.hi[0] - hi, 1.8)

    def test_one_fan_wall_per_cold_aisle_none_overlapping_all_inside(self):
        model = build()
        fans = model.fans
        self.assertEqual(len(fans), len(model.cold_aisles))
        spans = sorted(f.extent[0] for f in fans)
        for (_, hi), (lo, _) in zip(spans, spans[1:]):
            self.assertLessEqual(hi, lo + 1e-9)
        self.assertGreaterEqual(spans[0][0], 0.0)
        self.assertLessEqual(spans[-1][1], model.domain.hi[1] + 1e-9)
        for fan in fans:
            self.assertAlmostEqual(fan.area, 3.6 * 4.0)

    def test_each_fan_wall_faces_its_own_cold_aisle(self):
        """The unit centred on an aisle overlaps that aisle, corners aside."""
        model = build()
        for fan, aisle in zip(model.fans, model.cold_aisles):
            (lo, hi) = fan.extent[0]
            self.assertLess(lo, aisle[1])
            self.assertGreater(hi, aisle[0])

    def test_too_many_fan_walls_for_the_wall_is_refused(self):
        with self.assertRaises(ValueError):
            M.place_fans([(0, 1), (1, 2), (2, 3)], width=4.5, wall=10.0, cell=0.3)

    def test_the_spec_airflow_is_per_unit(self):
        model = build()
        self.assertAlmostEqual(model.unit_airflow_m3h, 20000)
        self.assertAlmostEqual(model.airflow_m3h, 20000 * len(model.fans))
        self.assertAlmostEqual(
            model.fan_face_velocity_ms, 20000 / 3600 / model.fans[0].area
        )

    def test_every_row_is_boxed_and_every_hot_aisle_contained(self):
        model = build()
        names = [p.name for p in model.panels]
        for row in model.rows:
            self.assertIn(f"rack_top_{row.id}", names)
            self.assertEqual(
                sum(1 for n in names if n.startswith(f"rack_end_{row.id}_")), 2
            )
        for k in range(3):
            self.assertIn(f"containment_wall_{k + 1}_a", names)
            self.assertIn(f"containment_wall_{k + 1}_b", names)
            self.assertEqual(
                sum(1 for n in names if n.startswith(f"containment_door_{k + 1}_")), 2
            )
            self.assertIn(f"grille{k + 1}", names)

    def test_the_grille_strip_covers_the_hot_aisle_ceiling(self):
        model = build()
        grille = model.panel("grille1")
        self.assertEqual(grille.extent[0], model.rack_span())
        self.assertEqual(grille.extent[1], model.hot_aisles[0])
        self.assertAlmostEqual(grille.position, model.ceiling_z)

    def test_an_aligned_hall_warns_about_nothing(self):
        self.assertEqual(build().warnings, [])

    def test_a_misaligned_rack_height_is_reported_once_not_per_rack(self):
        model = build(racks={**SPEC["racks"], "size": [0.6, 1.2, 2.2]})
        about_racks = [w for w in model.warnings if "rack height" in w]
        self.assertEqual(len(about_racks), 1)

    def test_the_pod_spec_still_builds_one_row(self):
        pod = M.build_model(
            yaml.safe_load(
                """
name: t
gallery: {depth: 3.0}
hall: {size: [6.0, 4.2, 8.0], ceiling: 6.5}
aisles: {cold: 1.8, hot: 1.2}
racks: {count: 3, load_kw: 6.0, size: [0.6, 1.2, 2.2], offset_x: 2.0}
fanwall: {airflow_m3h: 5000, supply_temp_c: 20.0, height: 4.0, width: 1.8}
grilles: {size: 0.6, count: 3}
mesh: {cell_size: 0.1}
"""
            )
        )
        self.assertEqual(len(pod.rows), 1)
        self.assertEqual(len(pod.fans), 1)
        self.assertEqual(pod.fans[0].name, "fan")
        self.assertEqual(case.fan_patches(pod), [("fanIntake", "fanSupply")])


def _uniform_grid(model, temperature=30.0, up=0.5):
    """A field at one temperature, rising everywhere."""
    nx, ny, nz = model.divisions
    shape = (nz, ny, nx)
    cx, cy, cz = model.cell_size
    return {
        "x": (np.arange(nx) + 0.5) * cx,
        "y": (np.arange(ny) + 0.5) * cy,
        "z": (np.arange(nz) + 0.5) * cz,
        "T": np.full(shape, float(temperature)),
        "U": np.stack([np.zeros(shape), np.zeros(shape), np.full(shape, float(up))]),
    }


class StationTest(unittest.TestCase):
    """A hall is measured across all of itself, not in three chosen aisles."""

    def setUp(self):
        # Six pods, so there are more aisles than the old instrument had
        # probes and the ones it never saw can be tested.
        self.model = build(pods=6)
        self.grid = _uniform_grid(self.model)
        self.layer = int(np.argmin(abs(
            self.grid["z"] - (self.model.ceiling_z - self.model.cell_size[2] / 2)
        )))

    def test_every_hot_aisle_reaches_the_cup(self):
        """The probes sampled the first, middle and last aisle. A hall of
        sixteen rows has thirteen the old instrument could not see; heat one
        of those and the measurement has to move (ADR-078)."""
        self.assertGreater(len(self.model.hot_aisles), 3)
        for index, (lo, hi) in enumerate(self.model.hot_aisles):
            with self.subTest(aisle=index):
                grid = _uniform_grid(self.model)
                rows = np.where((grid["y"] >= lo) & (grid["y"] <= hi))[0]
                grid["T"][self.layer][rows] = 60.0
                self.assertGreater(
                    post.aisle_exit_temperature(self.model, grid), 30.0
                )

    def test_neither_gallery_is_counted(self):
        """A gallery has no false ceiling, so this height is room air there."""
        grid = _uniform_grid(self.model)
        outside = np.where(
            (grid["x"] < self.model.hall.lo[0]) | (grid["x"] > self.model.hall.hi[0])
        )[0]
        grid["T"][self.layer][:, outside] = 5.0
        self.assertEqual(post.aisle_exit_temperature(self.model, grid), 30.0)


class CaseTest(unittest.TestCase):
    def setUp(self):
        self.model = build()

    def test_each_fan_wall_gets_its_own_baffle_pair_moving_its_share(self):
        text = case.create_baffles_dict(self.model)
        for intake, supply in case.fan_patches(self.model):
            self.assertIn(f"name    {intake};", text)
            self.assertIn(f"name    {supply};", text)
        share = self.model.unit_airflow_m3h / 3600 * case.supply_density(self.model)
        self.assertEqual(text.count(f"massFlowRate {share:.6g}"), 2 * len(self.model.fans))
        for fan in self.model.fans:
            self.assertIn(f"zoneName    {fan.name};", text)

    def test_walls_of_a_kind_share_one_zone(self):
        names = [name for name, _p, _h in case.wall_plan(self.model)]
        self.assertEqual(
            names,
            ["divider", "forro", "rack_top", "rack_end", "containment_wall", "containment_door"],
        )
        top = next(panels for name, panels, _ in case.wall_plan(self.model) if name == "rack_top")
        self.assertEqual(len(top), len(self.model.rows))

    def test_the_divider_is_holed_by_every_fan_and_the_return(self):
        text = case.topo_set_dict(self.model)
        divider = text[text.index("dividerFaces") : text.index("forroFaces")]
        self.assertEqual(divider.count("action  delete"), len(self.model.fans) + 1)

    def test_grouped_faces_are_added_then_narrowed_once(self):
        text = case.topo_set_dict(self.model)
        top = text[text.index("rack_topFaces") : text.index("rack_endFaces")]
        self.assertEqual(top.count("action  new"), 2)  # the faceSet, then the zone
        self.assertEqual(top.count("action  add"), len(self.model.rows) - 1)
        self.assertEqual(top.count("normalToFace"), 1)

    def test_the_parallel_pipeline_decomposes_solves_under_mpi_and_stitches(self):
        steps = case.pipeline(4)
        self.assertEqual(steps[: len(case.MESH_PIPELINE)], case.MESH_PIPELINE)
        commands = [s[0] if isinstance(s, tuple) else s for s in steps]
        self.assertEqual(commands[-3:], ["decomposePar", "mpirun", "reconstructPar"])
        mpirun = steps[-2]
        self.assertEqual(mpirun[1], ["-np", "4", "buoyantSimpleFoam", "-parallel"])
        self.assertEqual(mpirun[2], "buoyantSimpleFoam")  # the log keeps the solver's name
        self.assertEqual(case.pipeline(1), case.PIPELINE)

    def test_the_decomposition_shares_as_few_faces_as_it_can(self):
        """Every face on a processor boundary is exchanged each iteration, so
        the cut that shares fewest is the cut to make (ADR-113)."""
        text = case.decompose_par_dict(self.model, 4)
        self.assertIn("numberOfSubdomains 4;", text)
        nx, ny, nz = case.decomposition(self.model, 4)
        self.assertIn(f"({nx} {ny} {nz})", text,
                      "the dictionary does not carry the chosen cut")
        cells = self.model.divisions
        total = cells[0] * cells[1] * cells[2]

        def faces(n):
            return sum((n[a] - 1) * (total // cells[a]) for a in range(3))

        for cores in (4, 9, 16, 24):
            chosen = case.decomposition(self.model, cores)
            slab = [1, 1, 1]
            slab[max(range(3), key=lambda a: cells[a])] = cores
            with self.subTest(cores=cores):
                self.assertEqual(chosen[0] * chosen[1] * chosen[2], cores,
                                 "the cores are not all used")
                self.assertLessEqual(faces(chosen), faces(tuple(slab)),
                                     "slabbing one axis would share fewer")

    def test_no_axis_is_cut_more_finely_than_it_has_cells(self):
        """A subdomain with no cells in it is one `decomposePar` refuses."""
        cells = self.model.divisions
        for cores in (7, 12, 32, 64):
            n = case.decomposition(self.model, cores)
            with self.subTest(cores=cores):
                for axis in range(3):
                    self.assertLessEqual(n[axis], cells[axis])

    def test_a_core_count_the_mesh_cannot_hold_is_refused_by_name(self):
        # A prime larger than any axis of this mesh: nothing divides it, so
        # every candidate would leave a subdomain outside the mesh.
        with self.assertRaises(ValueError) as caught:
            case.decomposition(self.model, 10_007)
        self.assertIn("solver.processors", str(caught.exception))
        self.assertIn("no cells in it", str(caught.exception))

    def test_the_warm_seed_is_warm_in_every_hot_aisle_and_cold_in_every_cold_one(self):
        text = case.warm_start(self.model)
        values = [float(v) for v in text.split("(")[1].split(")")[0].split()]
        nx, ny, nz = self.model.divisions
        cx, cy, cz = self.model.cell_size
        x = self.model.rack_span()[0] + 0.3
        i = int(x / cx)
        k = int(1.0 / cz)
        for lo, hi in self.model.hot_aisles:
            j = int((lo + hi) / 2 / cy)
            self.assertGreater(values[(k * ny + j) * nx + i], 20 + KELVIN + 1)
        for lo, hi in self.model.cold_aisles:
            j = int((lo + hi) / 2 / cy)
            self.assertAlmostEqual(values[(k * ny + j) * nx + i], 20 + KELVIN, places=1)


HEADER = """FoamFile
{{
    version 2.0; format ascii; class volScalarField; location "{time}"; object {obj};
}}
dimensions [0 0 0 0 0 0 0];
internalField uniform {internal};
boundaryField
{{
"""


def field(path: Path, obj: str, internal: str, patches: dict[str, list[float]]):
    body = HEADER.format(time=path.parent.name, obj=obj, internal=internal)
    for name, values in patches.items():
        numbers = " ".join(f"{v:.6g}" for v in values)
        body += (
            f"    {name}\n    {{\n        type calculated;\n"
            f"        value nonuniform List<scalar> {len(values)}\n"
            f"({numbers})\n;\n    }}\n"
        )
    path.write_text(body + "}\n")


class ManyFansPostTest(unittest.TestCase):
    """Two units, read off their own patches, added up for the balances."""

    def setUp(self):
        self.step = Path(tempfile.mkdtemp()) / "100"
        self.step.mkdir()
        supply = 20.0 + KELVIN
        field(self.step / "phi", "phi", "0", {
            "fan1Intake": [0.5, 0.5], "fan1Supply": [-1.0],
            "fan2Intake": [0.25, 0.25], "fan2Supply": [-0.5],
            "rack_top_master": [0.0],
        })
        field(self.step / "T", "T", f"{supply}", {
            "fan1Intake": [supply + 10, supply + 10], "fan1Supply": [supply],
            "fan2Intake": [supply + 12, supply + 12], "fan2Supply": [supply],
            "rack_top_master": [supply],
        })
        field(self.step / "p_rgh", "p_rgh", "101325", {
            "fan1Intake": [101325.0, 101325.0], "fan1Supply": [101355.0],
            "fan2Intake": [101325.0, 101325.0], "fan2Supply": [101370.0],
            "rack_top_master": [101325.0],
        })

    def test_fan_pairs_are_found_in_unit_order(self):
        self.assertEqual(
            post.fan_pairs(self.step),
            [("fan1Intake", "fan1Supply"), ("fan2Intake", "fan2Supply")],
        )

    def test_each_unit_reports_its_own_flow_and_rise(self):
        fans = post.fan_flows(self.step)
        self.assertEqual([f["name"] for f in fans], ["fan1", "fan2"])
        self.assertAlmostEqual(fans[0]["supply_kg_s"], 1.0)
        self.assertAlmostEqual(fans[1]["intake_kg_s"], 0.5)
        self.assertAlmostEqual(fans[0]["rise_pa"], 30.0)
        self.assertAlmostEqual(fans[1]["rise_pa"], 45.0)

    def test_the_return_temperature_is_flow_weighted_across_units(self):
        # 1 kg/s at +10 K and 0.5 kg/s at +12 K -> +10.67 K
        rise = post.return_temperature(self.step) - (20.0 + KELVIN)
        self.assertAlmostEqual(rise, (1.0 * 10 + 0.5 * 12) / 1.5, places=3)

    def test_a_fan_patch_is_never_a_leak(self):
        for name in ("fanIntake", "fan12Supply", "fan3Intake"):
            self.assertTrue(post._is_fan_patch(name))
        self.assertFalse(post._is_fan_patch("fan_wall"))
        self.assertFalse(post._is_fan_patch("rack_top_master"))


class RackReadingTest(unittest.TestCase):
    def test_a_row_facing_minus_y_reads_its_inlet_on_the_high_side(self):
        import numpy as np

        model = build()
        nx, ny, nz = model.divisions
        cx, cy, cz = model.cell_size
        y = (np.arange(ny) + 0.5) * cy
        z = (np.arange(nz) + 0.5) * cz
        # Temperature rises with y: the +y-facing row must see the cooler
        # side as its inlet, the -y-facing row the warmer side.
        T = np.broadcast_to(y[None, :, None] + 20.0, (nz, ny, nx)).copy()
        # and the top layer of the room is 5 K warmer, so the top-of-rack
        # reading has to differ from the face mean
        T[int(2.0 / cz), :, :] += 5.0
        grid = {"T": T, "x": (np.arange(nx) + 0.5) * cx, "y": y, "z": z}
        rows = {r["id"]: r for r in post.rack_temperatures(model, grid)}
        a = model.rows[0].racks[0]
        b = model.rows[1].racks[0]
        self.assertLess(rows[a.id]["inlet_c"], rows[a.id]["outlet_c"])
        self.assertGreater(rows[b.id]["inlet_c"], rows[b.id]["outlet_c"])
        self.assertGreater(rows[a.id]["inlet_top_c"], rows[a.id]["inlet_c"])
        self.assertEqual(rows[a.id]["row"], "F1")
        self.assertEqual(rows[b.id]["position"], 1)


if __name__ == "__main__":
    unittest.main()


DOUBLE = yaml.safe_load(
    """
name: d
gallery: {depth: 3.0, sides: 2}
hall: {height: 8.0, ceiling: 6.5}
pods: 3
aisles: {cold: 1.8, hot: 1.2, perimeter: 1.8, transverse: 2.4}
racks: {per_row: 4, blocks: 2, load_kw: 13.0, size: [0.6, 1.2, 2.25]}
fanwall: {count: 6, width: 3.6, height: 4.0, airflow_m3h: 20000,
          supply_temp_c: 20.0, static_pressure_pa: 100}
grilles: {size: 0.6, loss_coefficient: 2.4, free_area: 0.8}
containment: {enabled: true}
mesh: {cell_size: [0.6, 0.3, 0.25]}
"""
)


class DoubleGalleryTest(unittest.TestCase):
    """A gallery at each end of the hall and rows cut into blocks (ADR-027).

    The thing being defended is that the second gallery is a *mirror* and not
    a special case: the same walls, the same openings, one plenum shared by
    both, and a baffle pair whose intake still faces the gallery even though
    the gallery is now on the other side of it.
    """

    def setUp(self):
        self.model = M.build_model(copy.deepcopy(DOUBLE))

    def test_two_galleries_at_the_two_ends(self):
        m = self.model
        self.assertEqual(len(m.galleries), 2)
        self.assertAlmostEqual(m.galleries[0].hi[0], m.hall.lo[0])
        self.assertAlmostEqual(m.galleries[1].lo[0], m.hall.hi[0])
        self.assertAlmostEqual(m.galleries[1].hi[0], m.domain.hi[0])
        self.assertEqual(m.dividers, [m.hall.lo[0], m.hall.hi[0]])
        # the length is gallery + perimeter + two blocks + transverse +
        # perimeter + gallery
        self.assertAlmostEqual(
            m.domain.size[0], 3.0 + 1.8 + 2 * 4 * 0.6 + 2.4 + 1.8 + 3.0
        )

    def test_the_plenum_is_one_volume_opening_into_both(self):
        """The single-plenum rule: the false ceiling covers the whole hall and
        stops at each gallery, so the volume above it is continuous and every
        gallery draws from the same air."""
        m = self.model
        ceiling = m.panel("ceiling")
        self.assertAlmostEqual(ceiling.extent[0][0], m.hall.lo[0])
        self.assertAlmostEqual(ceiling.extent[0][1], m.hall.hi[0])
        openings = [p for p in m.panels if p.name.startswith("plenum_opening")]
        self.assertEqual(len(openings), 2)
        for opening, x in zip(openings, m.dividers):
            self.assertAlmostEqual(opening.position, x)
            self.assertAlmostEqual(opening.extent[1][0], m.ceiling_z)
            self.assertAlmostEqual(opening.extent[0][1], m.domain.hi[1])

    def test_units_split_between_the_galleries(self):
        m = self.model
        self.assertEqual(len(m.fans), 6)
        for fan in m.fans[:3]:
            self.assertAlmostEqual(fan.position, m.hall.lo[0])
            self.assertEqual(fan.sign, 1)
        for fan in m.fans[3:]:
            self.assertAlmostEqual(fan.position, m.hall.hi[0])
            self.assertEqual(fan.sign, -1)
        # an odd count gives the extra unit to the first gallery
        spec = copy.deepcopy(DOUBLE)
        spec["fanwall"]["count"] = 7
        odd = M.build_model(spec)
        low = [f for f in odd.fans if f.sign > 0]
        self.assertEqual((len(low), len(odd.fans) - len(low)), (4, 3))

    def test_the_far_gallery_swaps_master_and_slave(self):
        """createBaffles gives the master to the cell at lower x. For the far
        gallery that cell is in the hall, so the halves swap -- otherwise the
        unit would supply into its own return and still read as converged."""
        m = self.model
        near = case._fan_baffle(m, m.fans[0], 0.1, 0.01)
        far = case._fan_baffle(m, m.fans[-1], 0.1, 0.01)
        self.assertIn("master\n            {\n                name    fan1Intake;", near)
        self.assertIn("slave\n            {\n                name    fan1Supply;", near)
        self.assertIn("master\n            {\n                name    fan6Supply;", far)
        self.assertIn("slave\n            {\n                name    fan6Intake;", far)
        # and the conditions travel with the name, not with the role
        self.assertRegex(far, r"(?s)fan6Supply;.*?flowRateInletVelocity")
        self.assertRegex(far, r"(?s)fan6Intake;.*?flowRateOutletVelocity")

    def test_every_divider_is_its_own_zone_with_its_own_holes(self):
        plan = dict((zone, (panels, holes)) for zone, panels, holes in
                    case.wall_plan(self.model))
        self.assertIn("divider", plan)
        self.assertIn("divider2", plan)
        for name, x in (("divider", self.model.hall.lo[0]),
                        ("divider2", self.model.hall.hi[0])):
            panels, holes = plan[name]
            self.assertAlmostEqual(panels[0].position, x)
            self.assertEqual(len(holes), 4)  # three units and one opening
            for hole in holes:
                self.assertAlmostEqual(hole.position, x)

    def test_each_block_is_a_containment_volume_of_its_own(self):
        m = self.model
        self.assertEqual(len(m.rack_blocks), 2)
        # three hot aisles seen in plan, but six contained volumes
        self.assertEqual(len(m.hot_aisles), 3)
        doors = [p for p in m.panels if p.name.startswith("containment_door")]
        self.assertEqual(len(doors), 3 * 2 * 2)  # two ends per volume
        walls = [p for p in m.panels if p.name.startswith("containment_wall")]
        self.assertEqual(len(walls), 3 * 2 * 2)  # two sides per volume
        # the chimney area counts the blocks, not the gap between them
        block = m.rack_blocks[0]
        self.assertAlmostEqual(
            m.chimney_area, 2 * 3 * (block[1] - block[0]) * 1.2
        )

    def test_rows_and_racks_carry_their_block(self):
        m = self.model
        self.assertEqual(len(m.rows), 3 * 2 * 2)
        self.assertEqual(
            [r.id for r in m.rows[:4]], ["F1B1", "F2B1", "F1B2", "F2B2"]
        )
        self.assertEqual(len(m.racks), 3 * 2 * 2 * 4)
        self.assertEqual(m.racks[0].id, "F1B1-01")
        self.assertTrue(all(len(set(r.id for r in m.racks)) == len(m.racks)
                            for _ in (0,)))
        # a block's racks sit inside that block's span, facing the same way
        for row in m.rows:
            lo = min(r.box.lo[0] for r in row.racks)
            hi = max(r.box.hi[0] for r in row.racks)
            self.assertIn((round(lo, 6), round(hi, 6)),
                          [(round(a, 6), round(b, 6)) for a, b in m.rack_blocks])

    def test_one_grille_strip_per_block_per_hot_aisle(self):
        grilles = [p for p in self.model.panels if p.name.startswith("grille")]
        self.assertEqual(len(grilles), 3 * 2)
        spans = {(round(p.extent[0][0], 6), round(p.extent[0][1], 6)) for p in grilles}
        self.assertEqual(
            spans, {(round(a, 6), round(b, 6)) for a, b in self.model.rack_blocks}
        )

    def test_the_air_in_both_galleries_is_left_out_of_the_aisle_cup(self):
        """Two galleries, neither with a false ceiling: air at ceiling height
        in either is room air and is not leaving the containment."""
        m = self.model
        grid = _uniform_grid(m)
        layer = int(np.argmin(abs(
            grid["z"] - (m.ceiling_z - m.cell_size[2] / 2)
        )))
        self.assertEqual(len(m.galleries), 2)
        for gallery in m.galleries:
            columns = np.where(
                (grid["x"] >= gallery.lo[0]) & (grid["x"] <= gallery.hi[0])
            )[0]
            self.assertGreater(columns.size, 0)
            grid["T"][layer][:, columns] = 5.0
        self.assertEqual(post.aisle_exit_temperature(m, grid), 30.0)

    def test_a_single_gallery_hall_is_unchanged(self):
        """The default is what it always was: one gallery, one block, one
        opening -- no spec that does not ask for the new layout may move."""
        m = M.build_model(copy.deepcopy(SPEC))
        self.assertEqual(len(m.galleries), 1)
        self.assertEqual(m.dividers, [m.hall.lo[0]])
        self.assertEqual([r.id for r in m.rows[:2]], ["F1", "F2"])
        self.assertEqual(m.racks[0].id, "F1-01")
        self.assertEqual(
            [p.name for p in m.panels if p.name.startswith("plenum_opening")],
            ["plenum_opening"],
        )
        self.assertTrue(all(fan.sign == 1 for fan in m.fans))


class EveryRowIsTheSameRowTest(unittest.TestCase):
    """A typical row is a pattern, so every block has to lay it out the same.

    It did not. `snap_to_mesh` moves each box face to the nearest cell face,
    one face at a time, and a row is built by accumulating widths from its
    block's origin. A block that begins half a cell off the grid therefore has
    every cumulative face sitting on a rounding tie, and each one falls
    whichever way the arithmetic takes it -- which REDISTRIBUTES the widths.

    Measured: a hall whose first block began at 12,5 m on a 0,2 m grid laid a
    typical row of nine 0,60 m cabinets and six 0,80 m ones out as
    0,80 / 0,40 / 0,60x6 / 0,80x7 -- ten centimetres longer, with a 0,40 m
    cabinet nobody specified. The second block started at 25,0 m, landed on the
    grid, and came out exactly right. One hall, two different rows, and the
    drawing was telling the truth about the mesh (ADR-085).
    """

    OFF_GRID = dict(mesh={"cell_size": [0.2, 0.3, 0.25]},
                    aisles={"cold": 2.7, "hot": 2.2,
                            "perimeter": 2.3, "transverse": 2.3})

    def mixed_width_hall(self, **over):
        spec = copy.deepcopy(SPEC)
        spec.update(copy.deepcopy(self.OFF_GRID))
        spec.update(over)
        spec["racks"]["blocks"] = 2
        spec["racks"]["per_row"] = 9
        spec["racks"]["row"] = ([{"width": 0.6}] * 6) + ([{"width": 0.8}] * 3)
        return M.build_model(spec)

    def widths(self, model):
        return {row.id: tuple(round(r.box.hi[0] - r.box.lo[0], 6)
                              for r in row.racks) for row in model.rows}

    def test_every_block_lays_the_typical_row_out_identically(self):
        widths = self.widths(self.mixed_width_hall())
        distinct = set(widths.values())
        self.assertEqual(
            len(distinct), 1,
            f"{len(distinct)} different rows in one hall: "
            + "; ".join(f"{k}={v}" for k, v in sorted(widths.items())[:4]),
        )

    def test_no_cabinet_comes_out_a_width_nobody_asked_for(self):
        for row in self.mixed_width_hall().rows:
            for rack in row.racks:
                width = round(rack.box.hi[0] - rack.box.lo[0], 6)
                with self.subTest(rack=rack.id):
                    self.assertIn(width, (0.6, 0.8))

    def test_a_row_starts_on_a_cell_face(self):
        """The fix, stated as the property it gives: snap the origin and every
        face lands on the grid, because the widths already do (ADR-075)."""
        model = self.mixed_width_hall()
        cell = model.cell(0)
        for row in model.rows:
            for rack in row.racks:
                for face in (rack.box.lo[0], rack.box.hi[0]):
                    with self.subTest(rack=rack.id, face=face):
                        self.assertAlmostEqual(face / cell, round(face / cell),
                                               places=6)

    def test_the_rows_are_the_length_their_pattern_says(self):
        model = self.mixed_width_hall()
        asked = 6 * 0.6 + 3 * 0.8
        for row in model.rows:
            with self.subTest(row=row.id):
                self.assertAlmostEqual(
                    row.racks[-1].box.hi[0] - row.racks[0].box.lo[0], asked,
                    places=6)


class EveryPodIsTheSamePodTest(unittest.TestCase):
    """A hall repeats, so its bands have to repeat identically.

    The hall's width is a chain -- perimeter, row, hot aisle, row, cold aisle,
    row, ... -- laid out by accumulating unsnapped parts and rounded afterwards
    one boundary at a time. The rounding drifts down the chain: a 2,2 m hot
    aisle on a 0,3 m grid came out 2,1 m in three pods and 2,4 m in the fourth,
    so one contained aisle had 14% more chimney cross-section than its
    neighbours and nothing in the output said so (ADR-085).
    """

    def hall(self, hot=2.2, cold=2.7, cell=(0.2, 0.3, 0.25)):
        spec = copy.deepcopy(SPEC)
        spec["pods"] = 4
        spec["mesh"] = {"cell_size": list(cell)}
        spec["aisles"] = {"cold": cold, "hot": hot,
                          "perimeter": 2.3, "transverse": 2.3}
        return M.build_model(spec)

    def test_every_hot_aisle_is_the_same_width(self):
        widths = {round(hi - lo, 6) for lo, hi in self.hall().hot_aisles}
        self.assertEqual(len(widths), 1, f"hot aisles came out {sorted(widths)}")

    def test_every_row_is_the_same_depth(self):
        depths = {round(r.band[1] - r.band[0], 6) for r in self.hall().rows}
        self.assertEqual(len(depths), 1, f"row depths came out {sorted(depths)}")

    def test_the_inner_cold_aisles_agree(self):
        """The perimeter is its own figure and may differ; the aisles between
        pods are all the same aisle and may not."""
        model = self.hall()
        inner = {round(hi - lo, 6) for lo, hi in model.cold_aisles[1:-1]}
        self.assertEqual(len(inner), 1, f"cold aisles came out {sorted(inner)}")

    def test_a_band_that_cannot_be_exact_is_inexact_the_same_way(self):
        """2,2 m does not fit a 0,3 m grid at all. What it must not do is fit
        differently in different pods."""
        model = self.hall(hot=2.2)
        widths = {round(hi - lo, 6) for lo, hi in model.hot_aisles}
        self.assertEqual(widths, {2.1})

    def test_a_band_that_fits_is_left_alone(self):
        """The correction is for the chain, not an excuse to move a figure the
        grid can carry: 2,4 m is eight cells of 0,3 and stays 2,4."""
        self.assertEqual(
            {round(hi - lo, 6) for lo, hi in self.hall(hot=2.4).hot_aisles},
            {2.4},
        )

    def test_every_band_boundary_lands_on_a_cell_face(self):
        model = self.hall()
        cell = model.cell(1)
        for lo, hi in [*model.hot_aisles, *model.cold_aisles,
                       *(r.band for r in model.rows)]:
            for face in (lo, hi):
                with self.subTest(face=face):
                    self.assertAlmostEqual(face / cell, round(face / cell), places=6)


class RowsMayDifferInLengthTest(unittest.TestCase):
    """A row whose one position the mesh had to move is a different length.

    `racks.widths` overrides one position of one row, and a width the mesh
    has to move is not the width that was asked for -- so that row ends
    somewhere its neighbour does not. The ends were taken from whichever row of the pair was
    built LAST: a row's own top and end walls were built at its neighbour's
    end, leaving its last cabinet uncapped, and a drawing dimensioned a row of
    10,40 m over cabinets that stopped at 10,00 (ADR-104).
    """

    def hall(self, **widths) -> dict:
        """This suite's own hall, with one position's width overridden. Not a
        shipped case: those belong to whoever is using the tool (ADR-056)."""
        spec = copy.deepcopy(SPEC)
        spec["racks"]["widths"] = widths
        return spec

    def built(self, **widths):
        return M.build_model(self.hall(**widths))

    def ends_of(self, model, row_id: str) -> list[float]:
        return sorted(p.position for p in model.panels
                      if p.name.startswith(f"rack_end_{row_id}_"))

    def racks_of(self, model, row_id: str) -> tuple[float, float]:
        row = next(r for r in model.rows if r.id == row_id)
        return (min(r.box.lo[0] for r in row.racks),
                max(r.box.hi[0] for r in row.racks))

    def test_each_row_is_capped_at_its_own_end(self):
        model = self.built(**{"F1-02": 1.0})
        for row_id in ("F1", "F2"):
            lo, hi = self.racks_of(model, row_id)
            with self.subTest(row=row_id):
                walls = self.ends_of(model, row_id)
                self.assertEqual(len(walls), 2)
                for wall, face in zip(walls, (lo, hi)):
                    self.assertAlmostEqual(
                        wall, face, places=6,
                        msg="the row's end wall is not at its own end")

    def test_the_rows_really_do_differ(self):
        """The premise: if the widths did not make them differ there would be
        nothing to check."""
        model = self.built(**{"F1-02": 1.0})
        self.assertNotAlmostEqual(*(self.racks_of(model, r)[1]
                                    for r in ("F1", "F2")))

    def test_the_lid_covers_the_longer_row_of_the_pair(self):
        """A lid that stops where the shorter row stops leaves the other
        row's last cabinet outside the containment."""
        model = self.built(**{"F1-02": 1.0})
        longest = max(self.racks_of(model, r)[1] for r in ("F1", "F2"))
        tops = [p for p in model.panels if p.name.startswith("rack_top_F1")]
        self.assertTrue(tops)
        grille = next(p for p in model.panels if p.name.startswith("grille"))
        self.assertGreaterEqual(grille.extent[0][1] + 1e-6, longest - 1e-6)

    def test_the_build_says_which_rows_differ(self):
        said = " ".join(self.built(**{"F1-02": 1.0}).warnings)
        self.assertIn("not the same length", said)
        self.assertIn("F1", said)
        self.assertIn("F2", said)

    def test_a_uniform_hall_says_nothing_of_the_sort(self):
        self.assertNotIn("not the same length", " ".join(self.built().warnings))


class WhereTheMachinesStandTest(unittest.TestCase):
    """A row of units is dimensioned off the wall and by its pitch, the way a
    layout drawing dimensions one (ADR-121).

    What set the spacing before was the COLD AISLES: one unit centred on each,
    which is what a fan wall is for and what a downflow unit has nothing to do
    with. On a hall whose aisles are not evenly spaced -- a 6 m aisle where a
    cage wall stands, 1,2 m at the perimeter -- the machines came out at gaps
    of 1,5 / 4,5 / 4,2 / 2,1 / 2,1 / 1,5 m, and the drawing they were taken
    from has them at one pitch from 240 mm off the wall.
    """

    def row(self, **kw):
        return M.row_of_units(7, 2.553, 34.8, 0.1, **kw)

    def test_a_stated_row_is_laid_where_the_drawing_puts_it(self):
        row = self.row(offset=0.24, pitch=4.8)
        self.assertEqual(len(row), 7)
        self.assertAlmostEqual(row[0][0], 0.2, places=6)  # 0,24 on a 0,1 grid
        for before, after in zip(row, row[1:]):
            self.assertAlmostEqual(after[0] - before[0], 4.8, places=6)

    def test_a_pitch_on_its_own_centres_the_row(self):
        row = self.row(pitch=4.8)
        head, tail = row[0][0], 34.8 - row[-1][1]
        self.assertAlmostEqual(head, tail, places=1)
        for before, after in zip(row, row[1:]):
            self.assertAlmostEqual(after[0] - before[0], 4.8, places=6)

    def test_an_offset_on_its_own_shares_what_is_left(self):
        """A drawing that dimensions the two end gaps and nothing between."""
        row = self.row(offset=0.24)
        self.assertAlmostEqual(row[0][0], 0.2, places=6)
        self.assertAlmostEqual(34.8 - row[-1][1], 0.2, places=1)
        gaps = [after[0] - before[0] for before, after in zip(row, row[1:])]
        self.assertAlmostEqual(max(gaps) - min(gaps), 0.0, places=6)

    def test_every_edge_lands_on_the_mesh(self):
        for cell in (0.1, 0.2, 0.3):
            for lo, hi in M.row_of_units(5, 2.553, 34.8, cell, offset=0.24,
                                         pitch=5.4):
                with self.subTest(cell=cell, edge=lo):
                    self.assertAlmostEqual(lo / cell, round(lo / cell), places=6)
                    self.assertAlmostEqual(hi / cell, round(hi / cell), places=6)

    def test_a_pitch_inside_the_unit_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            self.row(pitch=1.5)
        self.assertIn("fanwall.pitch", str(caught.exception))
        self.assertIn("inside its neighbour", str(caught.exception))

    def test_a_row_that_runs_off_the_wall_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            self.row(offset=0.24, pitch=6.0)
        self.assertIn("fanwall.offset/pitch", str(caught.exception))

    def test_an_offset_with_no_room_left_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            M.row_of_units(7, 2.553, 34.8, 0.1, offset=17.0)
        self.assertIn("fanwall.offset", str(caught.exception))

    def test_units_that_cannot_fit_the_wall_at_all_are_refused(self):
        with self.assertRaises(ValueError) as caught:
            M.row_of_units(20, 2.553, 34.8, 0.1)
        self.assertIn("do not fit", str(caught.exception))


class TheArrangementDecidesTheDefaultTest(unittest.TestCase):
    """A fan wall faces the aisle it feeds; a downflow unit faces the deck.

    Aligning a downflow unit with a cold aisle is a fan wall's rule applied
    where it does not belong: the machine discharges into the plenum and the
    plates distribute (ADR-072, ADR-121).
    """

    def spec(self, name: str) -> dict:
        from tests import support

        return support.spec(name)

    def units(self, spec) -> list[tuple[float, float]]:
        built = M.build_model(spec)
        gallery = [p for p in built.panels if p.kind == "fan"]
        half = len(gallery) // (len(built.galleries or [1]))
        axis = 1 if gallery[0].axis == 2 else 0
        return [p.extent[axis] for p in gallery[:half]]

    def test_a_downflow_hall_gets_an_even_row(self):
        spec = self.spec("hall-cage")
        spec.setdefault("floor", {})["enabled"] = True
        row = self.units(spec)
        gaps = [b[0] - a[0] for a, b in zip(row, row[1:])]
        self.assertTrue(gaps)
        self.assertLess(max(gaps) - min(gaps), 0.35,
                        f"the units are not evenly spaced: {gaps}")

    def test_a_fan_wall_hall_still_faces_its_aisles(self):
        spec = self.spec("hall-double-gallery")
        built = M.build_model(spec)
        if built.floor_height:
            self.skipTest("this fixture is a raised floor")
        fans = [p for p in built.panels if p.kind == "fan"]
        centres = sorted((p.extent[0][0] + p.extent[0][1]) / 2 for p in fans)
        aisles = sorted((lo + hi) / 2 for lo, hi in built.cold_aisles)
        for centre in centres[: len(aisles)]:
            with self.subTest(centre=centre):
                self.assertTrue(
                    any(abs(centre - a) < 2.0 for a in aisles),
                    "a fan wall no longer stands in front of an aisle")
