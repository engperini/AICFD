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


class SensorTest(unittest.TestCase):
    def test_the_probes_spread_across_first_middle_and_last_aisle(self):
        model = build()
        groups = {g.name: g for g in M.sensors(model)}
        ys = sorted(p[1] for p in groups["hot_aisle"].points)
        mids = [sum(b) / 2 for b in model.hot_aisles]
        self.assertEqual(ys, [mids[0], mids[1], mids[2]])
        fan_ys = sorted(p[1] for p in groups["fan_back"].points)
        self.assertEqual(len(set(fan_ys)), 3)


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

    def test_the_decomposition_slabs_the_longest_axis(self):
        text = case.decompose_par_dict(self.model, 4)
        self.assertIn("numberOfSubdomains 4;", text)
        self.assertIn("(1 4 1)", text)  # the hall is longest across y

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
