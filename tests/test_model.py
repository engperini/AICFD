"""Tests for the POD geometry model.

The point of aicfd.model is that the drawing, the summary table and the mesh
all describe the same thing. So most of these check agreement rather than
absolute values: that what the model reports is what snapping actually left
behind, and that the air loop it describes is closed.
"""

from __future__ import annotations

import copy
import unittest

import yaml

from aicfd import model as m

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
    for path, value in overrides.items():
        section, _, field = path.partition("__")
        if field:
            spec[section][field] = value
        else:
            spec[section] = value
    return m.build_model(spec)


class GeometryTest(unittest.TestCase):
    def test_domain_is_gallery_plus_hall(self):
        model = build()
        self.assertEqual(model.domain.size[0], model.gallery.size[0] + model.hall.size[0])
        self.assertEqual(model.gallery.hi[0], model.hall.lo[0])

    def test_aisles_tile_the_width_with_no_gap(self):
        model = build()
        self.assertEqual(model.cold_aisle[1], model.rack_band[0])
        self.assertEqual(model.rack_band[1], model.hot_aisle[0])
        self.assertAlmostEqual(model.hot_aisle[1], model.domain.hi[1])

    def test_racks_are_adjacent_and_sit_in_the_rack_band(self):
        model = build()
        self.assertEqual(len(model.racks), 3)
        for a, b in zip(model.racks, model.racks[1:]):
            self.assertAlmostEqual(a.box.hi[0], b.box.lo[0])
        for rack in model.racks:
            self.assertAlmostEqual(rack.box.lo[1], model.rack_band[0])
            self.assertAlmostEqual(rack.box.hi[1], model.rack_band[1])

    def test_racks_breathe_across_the_aisles(self):
        """Front at 90 degrees to the cold aisle: air passes along y."""
        for rack in build().racks:
            self.assertEqual(rack.airflow_axis, 1)

    def test_grilles_sit_over_the_hot_aisle_not_the_racks(self):
        model = build()
        grilles = [p for p in model.panels if p.name.startswith("grille")]
        self.assertEqual(len(grilles), 3)
        for panel in grilles:
            self.assertEqual(panel.axis, 2)
            self.assertAlmostEqual(panel.position, model.ceiling_z)
            (y0, y1) = panel.extent[1]
            self.assertGreaterEqual(y0, model.hot_aisle[0] - 1e-9)
            self.assertLessEqual(y1, model.hot_aisle[1] + 1e-9)

    def test_grilles_are_adjacent_and_over_the_row(self):
        model = build()
        grilles = [p for p in model.panels if p.name.startswith("grille")]
        row = model.rack_span()
        self.assertAlmostEqual(grilles[0].extent[0][0], row[0])
        for a, b in zip(grilles, grilles[1:]):
            self.assertAlmostEqual(a.extent[0][1], b.extent[0][0])

    def test_containment_spans_rack_top_to_ceiling(self):
        model = build()
        wall = model.panel("containment_roofwall")
        self.assertEqual(wall.axis, 1)
        self.assertAlmostEqual(wall.position, model.rack_band[1])
        self.assertAlmostEqual(wall.extent[1][0], model.racks[0].box.hi[2])
        self.assertAlmostEqual(wall.extent[1][1], model.ceiling_z)

    def test_the_row_ends_are_closed(self):
        """A rack has side panels, and an open row end is a short circuit.

        Measured on the case that lacked them: 63% of the fan's duty entered
        the porous zone sideways through the two ends and only 22% through the
        rack fronts, because a few cells of blocked axis cost far less than
        1,2 m of the flow axis. The row then delivered a fifth of its rated
        pressure drop.
        """
        model = build()
        ends = [p for p in model.panels if p.name.startswith("rack_end")]
        self.assertEqual(len(ends), 2)
        row = model.rack_span()
        for panel, edge in zip(sorted(ends, key=lambda p: p.position), row):
            self.assertEqual(panel.axis, 0)
            self.assertAlmostEqual(panel.position, edge)
            self.assertEqual(panel.extent[0], model.rack_band)
            self.assertAlmostEqual(panel.extent[1][1], model.racks[0].box.hi[2])

    def test_the_row_tops_are_closed(self):
        """A rack has a top. Without one the cold-aisle pocket above the row
        pours down through the porous zone: 43% of the fan's duty did."""
        model = build()
        top = model.panel("rack_top")
        self.assertEqual(top.axis, 2)
        self.assertAlmostEqual(top.position, model.racks[0].box.hi[2])
        self.assertEqual(top.extent[0], model.rack_span())
        self.assertEqual(top.extent[1], model.rack_band)

    def test_the_row_is_closed_on_every_face_but_front_and_back(self):
        model = build()
        row = model.rack_span()
        depth = model.rack_band[1] - model.rack_band[0]
        height = model.racks[0].box.hi[2]
        sealed = sum(
            p.area for p in model.panels if p.name.startswith(("rack_end", "rack_top"))
        )
        self.assertAlmostEqual(sealed, 2 * depth * height + (row[1] - row[0]) * depth)

    def test_a_row_end_covers_exactly_the_rack_it_caps(self):
        model = build()
        end = model.panel("rack_end_5")
        rack = model.racks[0]
        self.assertAlmostEqual(
            end.area, (rack.box.hi[1] - rack.box.lo[1]) * rack.box.hi[2]
        )

    def test_containment_can_be_turned_off(self):
        model = build(containment={"enabled": False})
        self.assertFalse([p for p in model.panels if p.name.startswith("containment")])

    def test_the_air_loop_is_closed(self):
        """Every opening in the loop exists and has area to pass the flow."""
        model = build()
        names = {p.name for p in model.panels}
        self.assertIn("fan", names)
        self.assertIn("plenum_opening", names)
        self.assertTrue({"grille1", "grille2", "grille3"} <= names)
        for name in ("fan", "plenum_opening", "grille1"):
            self.assertGreater(model.panel(name).area, 0.0)

    def test_fan_wall_and_plenum_opening_are_in_the_dividing_wall(self):
        model = build()
        for name in ("fan", "plenum_opening"):
            panel = model.panel(name)
            self.assertEqual(panel.axis, 0)
            self.assertAlmostEqual(panel.position, model.hall.lo[0])

    def test_plenum_opening_is_above_the_false_ceiling(self):
        model = build()
        (z0, z1) = model.panel("plenum_opening").extent[1]
        self.assertAlmostEqual(z0, model.ceiling_z)
        self.assertAlmostEqual(z1, model.domain.hi[2])

    def test_fan_wall_opens_only_on_the_cold_aisle(self):
        model = build()
        (y0, y1) = model.panel("fan").extent[0]
        self.assertAlmostEqual(y0, model.cold_aisle[0])
        self.assertLessEqual(y1, model.cold_aisle[1] + 1e-9)


class MeshTest(unittest.TestCase):
    def test_an_aligned_spec_warns_about_nothing(self):
        self.assertEqual(build().warnings, [])

    def test_a_misaligned_dimension_is_reported_in_millimetres(self):
        model = build(grilles={"size": 0.61, "count": 3})
        self.assertTrue(model.warnings)
        self.assertIn("mm", model.warnings[0])

    def test_what_is_reported_is_what_gets_built(self):
        """Snapping happens before anyone reads the model (ADR-004's spirit)."""
        model = build(grilles={"size": 0.61, "count": 3})
        for panel in model.panels:
            cell = model.cell(panel.axis)
            self.assertAlmostEqual(
                panel.position / cell, round(panel.position / cell), places=6
            )
            for axis, extent in zip(panel.in_plane_axes, panel.extent):
                for value in extent:
                    c = model.cell(axis)
                    self.assertAlmostEqual(value / c, round(value / c), places=6)
        for rack in model.racks:
            for axis in range(3):
                c = model.cell(axis)
                for value in (rack.box.lo[axis], rack.box.hi[axis]):
                    self.assertAlmostEqual(value / c, round(value / c), places=6)

    def test_cells_can_differ_per_axis(self):
        """0,20 m in plan and 0,10 m in height keeps the 6,5 m ceiling exact."""
        model = build(mesh={"cell_size": [0.2, 0.2, 0.1]})
        self.assertEqual(model.cell_size, (0.2, 0.2, 0.1))
        self.assertEqual(model.divisions, (45, 21, 80))
        self.assertEqual(model.warnings, [])
        self.assertAlmostEqual(model.ceiling_z, 6.5)

    def test_a_scalar_cell_means_the_same_edge_on_every_axis(self):
        self.assertEqual(build().cell_size, (0.1, 0.1, 0.1))

    def test_grilles_carry_a_loss_coefficient_from_their_free_area(self):
        model = build(grilles={"size": 0.6, "count": 3, "free_area": 0.8})
        grille = model.panel("grille1")
        self.assertAlmostEqual(grille.resistance, m.grille_loss_coefficient(0.8))
        self.assertGreater(model.grille_pressure_drop_pa, 0.0)

    def test_a_fully_open_grille_costs_nothing_and_a_half_open_one_a_lot(self):
        self.assertAlmostEqual(m.grille_loss_coefficient(1.0), 0.0)
        self.assertGreater(m.grille_loss_coefficient(0.5), 4.0)
        self.assertLess(m.grille_loss_coefficient(0.8), 0.7)

    def test_a_datasheet_k_overrides_the_free_area_formula(self):
        model = build(grilles={"size": 0.6, "count": 3, "free_area": 0.8,
                               "loss_coefficient": 2.5})
        self.assertEqual(model.panel("grille1").resistance, 2.5)

    def test_the_fan_curve_is_interpolated_and_ends_at_free_delivery(self):
        model = build(fanwall={**SPEC["fanwall"],
                               "curve": [[0, 250], [5000, 100], [7500, 0]]})
        self.assertAlmostEqual(model.fan_available_pa(5000), 100.0)
        self.assertAlmostEqual(model.fan_available_pa(2500), 175.0)
        self.assertEqual(model.fan_available_pa(9000), 0.0)

    def test_the_operating_point_sits_on_both_curves(self):
        model = build(fanwall={**SPEC["fanwall"],
                               "curve": [[0, 250], [5000, 100], [7500, 0]]})
        q, dp = model.fan_operating_point(29.0)
        self.assertAlmostEqual(dp, model.fan_available_pa(q), delta=0.5)
        self.assertAlmostEqual(dp, 29.0 * (q / 5000) ** 2, delta=0.5)
        self.assertGreater(q, 5000)  # 29 Pa of resistance is far below the 100 Pa rating

    def test_snapping_never_makes_two_racks_overlap(self):
        """Nearest-snapping is monotonic, so adjacency survives it."""
        model = build(racks={**SPEC["racks"], "size": [0.57, 1.2, 2.2]})
        for a, b in zip(model.racks, model.racks[1:]):
            self.assertLessEqual(a.box.hi[0], b.box.lo[0] + 1e-9)

    def test_cell_count_follows_the_cell_size(self):
        coarse = build(mesh={"cell_size": 0.2})
        fine = build(mesh={"cell_size": 0.1})
        self.assertEqual(fine.n_cells, coarse.n_cells * 8)


class DerivedTest(unittest.TestCase):
    def test_design_delta_t_matches_the_load_and_the_flow(self):
        model = build()
        expected = model.total_load_w / (model.airflow_m3s * m.RHO_AIR * m.CP_AIR)
        self.assertAlmostEqual(model.design_delta_t_k, expected)

    def test_zero_airflow_does_not_divide_by_zero(self):
        model = build(fanwall={**SPEC["fanwall"], "airflow_m3h": 0.0})
        self.assertEqual(model.design_delta_t_k, float("inf"))

    def test_face_velocity_is_flow_over_area(self):
        model = build()
        fan = model.panel("fan")
        self.assertAlmostEqual(model.face_velocity("fan"), model.airflow_m3s / fan.area)

    def test_summary_quotes_the_snapped_geometry(self):
        """A 0.61 m grille built at 0.60 m has to be quoted at 0.60 m."""
        model = build(grilles={"size": 0.61, "count": 3})
        row = dict((r[0], r[1]) for r in m.summary_rows(model))
        self.assertIn("0,60", row["Grelhas do forro (3)"])

    def test_numbers_are_written_the_way_the_interface_reads_them(self):
        self.assertEqual(m.num(1234.5, 1), "1.234,5")
        self.assertEqual(m.num(0.19), "0,19")
        self.assertEqual(m.num(302400, 0), "302.400")


class SerialisationTest(unittest.TestCase):
    def test_payload_carries_everything_the_drawing_needs(self):
        model = build()
        payload = m.to_dict(model, SPEC)
        for key in ("domain", "gallery", "hall", "ceiling_z", "aisles", "racks", "panels"):
            self.assertIn(key, payload)
        self.assertEqual(len(payload["panels"]), len(model.panels))

    def test_payload_panels_keep_their_axis_and_extent(self):
        payload = m.to_dict(build(), SPEC)
        fan = next(p for p in payload["panels"] if p["name"] == "fan")
        self.assertEqual(fan["axis"], 0)
        self.assertEqual(len(fan["extent"]), 2)
        self.assertAlmostEqual(fan["area"], 7.2)


if __name__ == "__main__":
    unittest.main()
