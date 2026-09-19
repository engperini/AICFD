"""Tests for the POD geometry model.

The point of aicfd.model is that the drawing, the summary table and the mesh
all describe the same thing. So most of these check agreement rather than
absolute values: that what the model reports is what snapping actually left
behind, and that the air loop it describes is closed.
"""

from __future__ import annotations

import copy
import unittest
from pathlib import Path

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
        expected = model.total_load_w / (model.airflow_m3s * model.rho * m.CP_AIR)
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
        self.assertIn("0.60", row["Return grilles (3)"])

    def test_numbers_are_written_the_way_the_interface_reads_them(self):
        self.assertEqual(m.num(1234.5, 1), "1,234.5")
        self.assertEqual(m.num(0.19), "0.19")
        self.assertEqual(m.num(302400, 0), "302,400")


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


class RackFacesTest(unittest.TestCase):
    """The rack's lid and ends are the rack, and say so (ADR-045).

    They are real surfaces the solver blocks, but they lie exactly on the
    rack box the drawings already show. Drawn again as walls they take the
    containment colour and put a wall where the room has a rack.
    """

    def payload(self) -> dict:
        return m.to_dict(build(), SPEC)

    def test_the_rack_keeps_its_lid_and_its_ends(self):
        names = {p["name"] for p in self.payload()["panels"]}
        self.assertTrue(any(n.startswith("rack_top") for n in names),
                        "the lid is what stops the cold aisle entering from above")
        self.assertTrue(any(n.startswith("rack_end") for n in names))

    def test_they_are_marked_as_the_rack_and_the_room_is_not(self):
        panels = self.payload()["panels"]
        for panel in panels:
            with self.subTest(panel=panel["name"]):
                self.assertEqual(
                    panel["of_rack"],
                    panel["name"].startswith(("rack_top", "rack_end")),
                    "only the rack's own faces belong to the rack",
                )

    def test_containment_is_never_marked(self):
        marked = [p["name"] for p in self.payload()["panels"]
                  if p["of_rack"] and "containment" in p["name"]]
        self.assertEqual(marked, [], "the chimney is a wall of the room")

    def test_the_flag_survives_mesh_snapping(self):
        """It did not: the snapping pass rebuilt each Panel field by field and
        stopped one short, so the flag came back false on every panel."""
        model = build()
        self.assertTrue(
            [p for p in model.panels if p.of_rack],
            "snapping must not drop a field it was not told about",
        )

    def test_the_drawing_skips_them(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "drawing.js").read_text()
        self.assertEqual(js.count("ofRack(panel)"), 2,
                         "both the face-on and the edge-on pass have to skip them")
        self.assertIn("panel.of_rack ??", js,
                      "an export written before the flag falls back to the name")


if __name__ == "__main__":
    unittest.main()


class FanWallDepthTest(unittest.TestCase):
    """The unit's depth is drawn and never meshed (ADR-046).

    The solver sees a zero-thickness baffle pair, because a fan wall is a
    boundary condition rather than a volume. The drawings need the envelope
    so a reader can ask whether the mechanical gallery holds the machine.
    """

    def test_a_case_that_states_no_depth_gets_none(self):
        self.assertIsNone(m.fan_depth({"fanwall": {"width": 3.96}}))
        self.assertIsNone(m.fan_depth({}))

    def test_zero_is_no_depth_rather_than_a_flat_machine(self):
        self.assertIsNone(m.fan_depth({"fanwall": {"depth": 0}}))

    def test_a_stated_depth_is_read(self):
        self.assertEqual(m.fan_depth({"fanwall": {"depth": "1.48"}}), 1.48)

    def test_the_payload_carries_it(self):
        spec = copy.deepcopy(SPEC)
        spec["fanwall"]["depth"] = 1.2
        self.assertEqual(m.to_dict(m.build_model(spec), spec)["fan_depth_m"], 1.2)

    def test_the_mesh_never_sees_it(self):
        """A depth on the spec must not move a cell or a panel."""
        deep = copy.deepcopy(SPEC)
        deep["fanwall"]["depth"] = 2.5
        a, b = build(), m.build_model(deep)
        self.assertEqual(a.divisions, b.divisions)
        self.assertEqual([p.name for p in a.panels], [p.name for p in b.panels])
        self.assertEqual([p.position for p in a.panels], [p.position for p in b.panels])


class HotAisleExtentTest(unittest.TestCase):
    """A hot aisle is the pocket between two rack rows, and stops where they do.

    Painted the hall's whole length it ran past the end of the row and across
    the transverse aisle between two blocks, tinting bare floor as though the
    containment reached it.
    """

    def test_the_blocks_are_shorter_than_the_hall(self):
        model = build()
        payload = m.to_dict(model, SPEC)
        blocks = payload["blocks"]
        self.assertTrue(blocks, "the drawing needs somewhere to stop")
        hall = payload["hall"]
        self.assertGreaterEqual(min(b[0] for b in blocks), hall["lo"][0])
        self.assertLessEqual(max(b[1] for b in blocks), hall["hi"][0])

    def test_the_drawing_bounds_the_hot_tint_by_them(self):
        js = (Path(__file__).resolve().parent.parent / "web" / "drawing.js").read_text()
        self.assertIn("rackBlocks(model)", js)
        self.assertNotIn("...hotAisles(model).map((band) => [band, 'dw-hot'])", js,
                         "the hot tint must not run the hall's length again")


class ContainmentInSectionTest(unittest.TestCase):
    """A section along a hot aisle passes between the walls that contain it.

    Neither is cut, so both were drawn at the weight everything off the plane
    shares -- a thin dash whose every edge landed on something already on the
    page. The one view with no aisle tints was also the one with no sign of
    the containment in it (ADR-047).
    """

    WEB = Path(__file__).resolve().parent.parent / "web"

    def test_the_page_draws_the_contained_volume(self):
        js = (self.WEB / "drawing.js").read_text()
        css = (self.WEB / "drawing.css").read_text()
        self.assertIn("dw-contained", js)
        self.assertIn(".dw-contained", css)
        self.assertIn("svg.dw-over-map .dw-contained{fill:none", css,
                      "a wash over a field map would tint the temperatures")

    def test_it_is_drawn_only_where_the_aisle_tints_are_not(self):
        js = (self.WEB / "drawing.js").read_text()
        self.assertIn("!(view.h === 1 || view.v === 1)", js,
                      "where the aisle bands are on screen they already say it")

    def test_the_report_sections_draw_it_too(self):
        """The report's figures are matplotlib, so the containment had to be
        written twice; leaving one out is how the two drift."""
        py = (Path(__file__).resolve().parent.parent / "aicfd" / "figures.py").read_text()
        body = py[py.index("def section("):py.index("def rack_map(")]
        self.assertIn("containment_wall", body)
        self.assertIn("CONTAINMENT", body)


class SectionDrawsEachThingOnceTest(unittest.TestCase):
    """A section looks down an axis, so what lies along it piles up (ADR-050).

    The worked hall's 440 racks project onto ten rectangles in the transverse
    section. Drawn one per rack that is 44 copies of the same outline, and a
    dashed outline at 45% opacity stacked 44 deep is solid black: a rack the
    section does not cut came out looking like one it did.
    """

    WEB = Path(__file__).resolve().parent.parent / "web"

    def test_the_drawing_paints_each_rectangle_once(self):
        js = (self.WEB / "drawing.js").read_text()
        self.assertIn("const painted = new Set()", js)
        self.assertIn("painted.has(key)", js)

    def test_the_cut_ones_are_drawn_before_the_rest(self):
        """Solid beats dashed where both would land on the same rectangle,
        which is the drawing's own rule -- so order decides it."""
        js = (self.WEB / "drawing.js").read_text()
        self.assertIn("const racks = [...model.racks].sort(", js)

    def test_an_aisle_is_not_outlined_over_a_field_map(self):
        """A tint is not a surface. Outlined it drew a line the length of the
        hall along each side of every aisle -- an edge the room does not
        have."""
        css = (self.WEB / "drawing.css").read_text()
        self.assertIn("svg.dw-over-map .dw-cold,svg.dw-over-map .dw-hot{display:none}",
                      css)

    def test_the_room_is_cold_and_the_hot_aisles_are_the_exception(self):
        """One rule instead of a list of regions. Tinting the aisle bands alone
        left everything that was neither an aisle nor a rack as bare paper --
        above a rack, between two blocks, the whole longitudinal section --
        and white read as a gap in the drawing (ADR-052)."""
        js = (self.WEB / "drawing.js").read_text()
        self.assertIn("model.ceiling_z],\n    'dw-cold',", js,
                      "the hall below the false ceiling is the cold fill")
        self.assertNotIn("openFloor", js,
                         "the hall's own fill covers what that patched")

    def test_the_plenum_is_the_hot_side_of_the_loop(self):
        """It carries the air the racks just heated, and it was tinted like
        the cold room."""
        css = (self.WEB / "drawing.css").read_text()
        self.assertIn(".dw-plenum{fill:var(--hot)", css)

    def test_a_caption_is_set_on_a_halo(self):
        """The drawings are dense where the captions have to sit: `fan wall`
        lands on the wall it names."""
        css = (self.WEB / "drawing.css").read_text()
        self.assertIn("paint-order:stroke;stroke:var(--paper)", css)


class SensorChartLegendTest(unittest.TestCase):
    """Four places on one chart, three of them within a kelvin (ADR-053)."""

    WEB = Path(__file__).resolve().parent.parent / "web"

    def test_a_label_carries_a_mark_in_its_series_colour(self):
        """The text stays in ink. Colouring it was the intent and never
        worked: the class sets `fill`, and a CSS rule beats a presentation
        attribute, so every name came out the same grey."""
        js = (self.WEB / "app.js").read_text()
        self.assertIn('class="chart-mark"', js)
        self.assertNotIn('fill="${colour}">${g.label}', js,
                         "a class sets fill; the attribute never applied")

    def test_a_displaced_label_keeps_a_thread_to_its_line(self):
        js = (self.WEB / "app.js").read_text()
        self.assertIn('class="chart-leader"', js)
        self.assertIn(".chart-leader{", (self.WEB / "index.html").read_text())

    def test_a_sensor_marker_has_a_colour_of_its_own(self):
        """It is an instrument on a drawing, not a series on a chart. While it
        borrowed a categorical slot, re-stepping that slot for a chart
        repainted every sensor in every drawing."""
        css = (self.WEB / "drawing.css").read_text()
        self.assertIn("stroke:var(--sensor)", css)
        self.assertNotIn("var(--series-4)", css)
        for page in ("index.html", "results.html"):
            with self.subTest(page=page):
                self.assertIn("--sensor", (self.WEB / page).read_text())

    def test_both_pages_define_the_same_series_colours(self):
        """A reader moves between them comparing the same places."""
        import re

        def tokens(page):
            text = (self.WEB / page).read_text()
            return {m.group(1): m.group(2).lower() for m in
                    re.finditer(r"--(series-\d|sensor):\s*(#[0-9a-fA-F]{6})", text)}

        self.assertEqual(tokens("index.html"), tokens("results.html"))
