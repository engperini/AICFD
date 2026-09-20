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


class PerRackLoadTest(unittest.TestCase):
    """A hall is specified by one load per rack and never filled that way.

    Positions are reserved, staged or left for growth, and where the gaps sit
    decides how evenly the units load (ADR-054).
    """

    def spec(self) -> dict:
        return support.spec("hall-double-gallery")

    def test_a_position_can_carry_its_own_load(self):
        spec = self.spec()
        spec["racks"]["loads"] = {"F1B1-03": 4.0}
        by = {r.id: r.load_kw for r in m.build_model(spec).racks}
        self.assertEqual(by["F1B1-03"], 4.0)
        self.assertEqual(by["F1B1-02"], spec["racks"]["load_kw"])

    def test_a_position_can_carry_nothing(self):
        spec = self.spec()
        spec["racks"]["loads"] = {"F1B1-03": 0}
        model = m.build_model(spec)
        empty = next(r for r in model.racks if r.id == "F1B1-03")
        self.assertEqual(empty.load_w, 0.0)
        self.assertEqual(empty.rated_airflow_m3s, 0.0)
        self.assertEqual(model.unloaded_racks, 1)

    def test_the_total_is_the_sum_and_not_the_count_times_the_standard(self):
        spec = self.spec()
        standard = float(spec["racks"]["load_kw"])
        spec["racks"]["loads"] = {"F1B1-03": 0, "F1B1-04": 0}
        model = m.build_model(spec)
        self.assertAlmostEqual(
            model.total_load_w / 1000,
            standard * (len(model.racks) - 2),
            places=6,
        )

    def test_an_empty_cabinet_is_still_in_the_model(self):
        """It is an obstruction, a cell zone and a porous block; what it loses
        is the source. Deleting it instead would leave the row a cabinet short
        and the air would take the gap."""
        from aicfd import case as c

        spec = self.spec()
        spec["racks"]["loads"] = {"F1B1-03": 0}
        model = m.build_model(spec)
        self.assertIn("F1B1-03", [r.id for r in model.racks])
        options = c.fv_options(model)
        self.assertIn("F1B1-03Porosity", options)
        self.assertNotIn("F1B1-03Heat", options)
        self.assertIn("F1B1-02Heat", options)

    def test_an_empty_position_resists_like_the_row(self):
        """Blanked, not open: it is calibrated at the hall's standard however
        little it dissipates, so the row stays one resistance and the closed
        form is the same with gaps in it as without."""
        spec = self.spec()
        uniform = m.build_model(spec).rack_pressure_drop_pa
        self.assertGreater(uniform, 0)
        spec["racks"]["loads"] = {"F1B1-01": 0, "F1B1-02": 0}
        mixed = m.build_model(spec)
        self.assertAlmostEqual(mixed.rack_pressure_drop_pa, uniform, places=6)
        self.assertEqual(mixed.unloaded_racks, 2)
        for rack in mixed.racks:
            self.assertAlmostEqual(rack.darcy_forchheimer()[1],
                                   mixed.racks[-1].darcy_forchheimer()[1], places=6)

    def test_resistance_is_calibrated_on_the_standard_not_the_load(self):
        """The field that made every rack forty times too resistive when a
        positional rebuild filled it with the air density (ADR-054)."""
        spec = self.spec()
        spec["racks"]["loads"] = {"F1B1-01": 0}
        model = m.build_model(spec)
        standard = float(spec["racks"]["load_kw"])
        for rack in model.racks:
            self.assertEqual(rack.resistance_kw, standard)

    def test_a_hall_with_nothing_installed_asks_for_no_drop(self):
        """Every position empty still resists -- they are blanked. A standard
        of zero is the hall that has nothing in it at all."""
        spec = self.spec()
        spec["racks"]["loads"] = {r.id: 0 for r in m.build_model(spec).racks}
        blanked = m.build_model(spec)
        self.assertGreater(blanked.rack_pressure_drop_pa, 0.0)
        self.assertEqual(blanked.total_load_w, 0.0)

        spec = self.spec()
        spec["racks"]["load_kw"] = 0.0
        nothing = m.build_model(spec)
        self.assertEqual(nothing.rack_pressure_drop_pa, 0.0)
        self.assertEqual(nothing.total_load_w, 0.0)

    def test_a_load_outside_the_sane_range_is_refused(self):
        for bad in (-1, 500):
            with self.subTest(load=bad), self.assertRaises(ValueError):
                m.rack_loads({"racks": {"loads": {"F1B1-03": bad}}})

    def test_a_case_that_states_nothing_has_no_overrides(self):
        self.assertEqual(m.rack_loads(self.spec()), {})


class SupplyPlenumTest(unittest.TestCase):
    """The wall between the hall and the gallery made a double wall, with the
    cavity pressurised and grilles deciding where the air leaves (ADR-058)."""

    def spec(self, **plenum) -> dict:
        spec = support.spec("pod-plenum")
        spec["plenum"] = {**spec.get("plenum", {}), "enabled": True, **plenum}
        return spec

    def named(self, model, prefix):
        return [p for p in model.panels if p.name.startswith(prefix)]

    def test_without_one_the_unit_blows_straight_into_the_aisle(self):
        spec = self.spec()
        spec["plenum"]["enabled"] = False
        model = m.build_model(spec)
        self.assertIsNone(model.plenum_depth)
        self.assertEqual(self.named(model, "plenum_wall"), [])
        self.assertEqual(self.named(model, "supply"), [])

    def test_the_fan_wall_does_not_move(self):
        """The whole arrangement is a second leaf of the same wall. Nothing
        about the unit or the opening it sits in changes -- moving it was the
        first thing this got wrong."""
        spec = self.spec()
        spec["plenum"]["enabled"] = False
        without = m.build_model(spec)
        with_plenum = m.build_model(self.spec())
        before = [(f.name, f.position, f.extent) for f in without.panels
                  if f.kind == "fan"]
        after = [(f.name, f.position, f.extent) for f in with_plenum.panels
                 if f.kind == "fan"]
        self.assertEqual(before, after)
        for fan in [p for p in with_plenum.panels if p.kind == "fan"]:
            self.assertAlmostEqual(fan.position, with_plenum.dividers[0], places=6)

    def test_the_second_leaf_stands_the_plenum_depth_into_the_hall(self):
        model = m.build_model(self.spec(depth=1.2))
        self.assertAlmostEqual(model.plenum_depth, 1.2, places=6)
        wall, = self.named(model, "plenum_wall")
        self.assertEqual(wall.kind, "wall")
        self.assertAlmostEqual(wall.position, model.dividers[0] + 1.2, places=6)
        self.assertAlmostEqual(wall.extent[1][1], model.ceiling_z, places=6)

    def test_the_cavity_needs_no_lid(self):
        """The false ceiling already covers the hall, this strip included, so
        the plenum is closed at that level and the return passes over it
        exactly as it did before."""
        model = m.build_model(self.spec())
        self.assertEqual(self.named(model, "plenum_lid"), [])
        ceiling = model.panel("ceiling")
        wall, = self.named(model, "plenum_wall")
        self.assertLessEqual(ceiling.extent[0][0], wall.position)
        self.assertGreaterEqual(ceiling.extent[0][1], wall.position)

    def test_a_grille_sits_in_that_leaf_at_every_cold_aisle(self):
        model = m.build_model(self.spec())
        wall, = self.named(model, "plenum_wall")
        grilles = self.named(model, "supply")
        self.assertEqual(len(grilles), len(model.cold_aisles))
        for grille, aisle in zip(grilles, model.cold_aisles):
            self.assertAlmostEqual(grille.position, wall.position, places=6)
            self.assertAlmostEqual(grille.extent[1][0], 0.0, places=6)
            middle = sum(aisle) / 2
            self.assertLess(abs(sum(grille.extent[0]) / 2 - middle), model.cell(1))

    def test_a_grille_is_as_tall_as_a_rack_unless_the_case_says_otherwise(self):
        model = m.build_model(self.spec())
        rack_height = model.racks[0].box.hi[2]
        for grille in self.named(model, "supply"):
            self.assertAlmostEqual(grille.extent[1][1], rack_height, places=6)
        model = m.build_model(self.spec(grille={"height": 1.5}))
        for grille in self.named(model, "supply"):
            self.assertAlmostEqual(grille.extent[1][1], 1.5, places=6)

    def test_a_grille_can_be_shut_without_renumbering_the_rest(self):
        """A closed grille is a grille that is not there -- the leaf closes
        over it. If shutting one renumbered the others, a case would shut a
        different grille the next time it was read."""
        model = m.build_model(self.spec())
        names = [p.name for p in self.named(model, "supply")]
        shut = m.build_model(self.spec(closed=[names[0]]))
        self.assertNotIn(names[0], [p.name for p in self.named(shut, "supply")])
        self.assertEqual([p.name for p in self.named(shut, "supply")], names[1:])

    def test_the_grille_carries_its_component_s_resistance(self):
        model = m.build_model(self.spec())
        for grille in self.named(model, "supply"):
            self.assertGreater(grille.resistance, 0)
        model = m.build_model(self.spec(grille={"free_area": 1.0}))
        for grille in self.named(model, "supply"):
            self.assertAlmostEqual(grille.resistance, 0.0, places=6)

    def test_the_building_grows_by_the_plenum_and_the_clearances_do_not(self):
        """The plenum is added to the building, not taken out of the room: a
        case asks for so much clearance between the racks and the wall the
        hall has, and with a plenum that wall is its inner leaf."""
        spec = self.spec()
        spec["plenum"]["enabled"] = False
        without = m.build_model(spec)
        for depth in (1.2, 2.5):
            with self.subTest(depth=depth):
                with_plenum = m.build_model(self.spec(depth=depth))
                self.assertAlmostEqual(
                    with_plenum.domain.hi[0], without.domain.hi[0] + depth,
                    delta=with_plenum.cell(0))
                leaf, = self.named(with_plenum, "plenum_wall")
                gap = min(r.box.lo[0] for r in with_plenum.racks) - leaf.position
                was = (min(r.box.lo[0] for r in without.racks)
                       - without.dividers[0])
                # To within the mesh: every plane is snapped to a cell face,
                # and the model warns where that moves one (ADR-013).
                self.assertAlmostEqual(gap, was, delta=with_plenum.cell(0))

    def test_a_hall_grows_by_a_plenum_at_each_gallery(self):
        spec = support.spec("hall-double-gallery")
        without = m.build_model(spec)
        spec["plenum"] = {"enabled": True, "depth": 1.2}
        with_plenum = m.build_model(spec)
        self.assertEqual(len(with_plenum.dividers), 2)
        self.assertAlmostEqual(
            with_plenum.domain.hi[0], without.domain.hi[0] + 2 * 1.2, places=6)
        near, far = self.named(with_plenum, "plenum_wall")
        self.assertAlmostEqual(near.position, with_plenum.dividers[0] + 1.2, places=6)
        self.assertAlmostEqual(far.position, with_plenum.dividers[1] - 1.2, places=6)

    def test_a_grille_taller_than_the_ceiling_is_refused(self):
        with self.assertRaises(ValueError) as refused:
            m.build_model(self.spec(grille={"height": 99.0}))
        self.assertIn("false ceiling", str(refused.exception))


class PlenumSizingTest(unittest.TestCase):
    """The two things a supply plenum has to be checked for: whether the
    openings have the area for the duty, and whether the units have the
    pressure for what it costs (ADR-059)."""

    def hall(self, **plenum) -> dict:
        spec = support.spec("hall-double-gallery")
        spec["plenum"] = {"enabled": True, "depth": 1.2, **plenum}
        return spec

    def test_the_face_velocity_is_the_flow_over_the_gross_area(self):
        model = m.build_model(self.hall())
        sides = {g.position: 0.0 for g in model.plenum_grilles}
        for g in model.plenum_grilles:
            sides[g.position] += g.area
        expected = (model.airflow_m3s / len(sides)) / min(sides.values())
        self.assertAlmostEqual(model.plenum_face_velocity_ms, expected, places=6)

    def test_grilles_too_small_for_the_duty_are_said_so_before_the_run(self):
        """5 MW through 2 m grilles is 7,5 m/s at the face. That is not a
        detail: the air is thrown across the aisle instead of delivered, and
        the pressure it costs goes with the square of it."""
        model = m.build_model(self.hall())
        self.assertGreater(model.plenum_face_velocity_ms, 3.0)
        alerts = model.plenum_alerts()
        self.assertTrue(any("small for the duty" in a for a in alerts))
        self.assertTrue(any("m2" in a for a in alerts), "it says the area needed")

    def test_a_case_may_set_its_own_criterion(self):
        model = m.build_model(self.hall(max_face_velocity_ms=9.0))
        self.assertEqual(model.plenum_face_velocity_max_ms, 9.0)
        self.assertFalse(any("small for the duty" in a
                             for a in model.plenum_alerts()))

    def test_wide_enough_grilles_raise_nothing(self):
        model = m.build_model(self.hall(grille={"width": 6.0, "height": 3.0}))
        self.assertLess(model.plenum_face_velocity_ms, 3.0)
        self.assertFalse(any("small for the duty" in a
                             for a in model.plenum_alerts()))

    def test_a_unit_short_of_pressure_is_said_so_before_the_run(self):
        spec = self.hall(grille={"width": 0.6, "height": 0.6})
        model = m.build_model(spec)
        self.assertGreater(model.loop_pressure_drop_pa, model.fan_available_pa())
        self.assertTrue(any("do not have the pressure" in a
                            for a in model.plenum_alerts()))

    def test_the_loop_total_is_the_closed_forms_added_up(self):
        model = m.build_model(self.hall())
        self.assertAlmostEqual(
            model.loop_pressure_drop_pa,
            model.rack_pressure_drop_pa
            + model.grille_pressure_drop_pa
            + (model.mesh_pressure_drop_pa or 0.0)
            + (model.plenum_pressure_drop_pa or 0.0)
            + (model.supply_mesh_pressure_drop_pa or 0.0),
            places=6,
        )


class SupplyMeshTest(unittest.TestCase):
    """The same wall treatment the other way: the 13 x 13 mm mesh across the
    opening, no plenum, and a hall that keeps every dimension (ADR-060)."""

    def spec(self, as_mesh=True) -> dict:
        spec = support.spec("pod-plenum")
        spec["plenum"] = {**spec.get("plenum", {}), "enabled": True,
                          "as_mesh": as_mesh}
        return spec

    def test_the_hall_keeps_its_size(self):
        """The reason to choose it: a hall already built cannot grow."""
        plain = support.spec("pod-plenum")
        plain["plenum"] = {"enabled": False}
        mesh = m.build_model(self.spec())
        self.assertEqual(mesh.domain.hi, m.build_model(plain).domain.hi)
        self.assertGreater(
            m.build_model(self.spec(as_mesh=False)).domain.hi[0], mesh.domain.hi[0])

    def test_it_builds_no_plenum_and_no_grilles(self):
        model = m.build_model(self.spec())
        self.assertIsNone(model.plenum_depth)
        self.assertEqual(model.plenum_grilles, [])
        self.assertEqual(
            [p for p in model.panels if p.name.startswith("plenum_wall")], [])

    def test_it_is_the_same_mesh_as_the_return_side(self):
        from aicfd import components as library

        model = m.build_model(self.spec())
        self.assertAlmostEqual(
            model.supply_mesh_k, library.load("gallery-mesh-13").k, places=9)

    def test_it_costs_the_unit_pressure_on_its_own_face(self):
        model = m.build_model(self.spec())
        self.assertAlmostEqual(
            model.supply_mesh_pressure_drop_pa,
            model.supply_mesh_k * 0.5 * model.rho * model.fan_face_velocity_ms**2,
            places=9,
        )
        self.assertAlmostEqual(
            model.loop_pressure_drop_pa
            - model.rack_pressure_drop_pa
            - model.grille_pressure_drop_pa
            - (model.mesh_pressure_drop_pa or 0.0),
            model.supply_mesh_pressure_drop_pa,
            places=9,
        )
