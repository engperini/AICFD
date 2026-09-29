"""A hall read from a drawing: the STL + sidecar input (ADR-131).

The parametric layouts are tested for what they DERIVE. This reader derives
nothing -- the drawing placed every box -- so what is tested is that the
contract's solids come out as the same Model the layouts build, under the
same names, so the mesher and the checks run unchanged: the rows and their
fronts, the aisles read off them, the fans in the dividing wall, the holes in
it, the grilles at their component's K, the containment, the cage, the deck
and its plates, and the refusals a bad file earns by name.

The fixtures are written by the tests themselves: a small hall of a few
cabinets, on the 0,2 m grid the sidecar states.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aicfd import geometry
from aicfd import model as M

CELL = 0.2


def _r(bands):
    """Bands rounded to the millimetre: the snap leaves 5.6000000000000005 behind."""
    return [tuple(round(v, 3) for v in band) for band in bands]


def _box(name, lo, hi):
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
         (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    faces = [((0, 0, -1), (0, 1, 2, 3)), ((0, 0, 1), (4, 5, 6, 7)), ((0, -1, 0), (0, 1, 5, 4)),
             ((0, 1, 0), (3, 2, 6, 7)), ((1, 0, 0), (1, 2, 6, 5)), ((-1, 0, 0), (0, 3, 7, 4))]
    out = [f"solid {name}"]
    for n, (a, b, c, d) in faces:
        for tri in ((a, b, c), (a, c, d)):
            out.append(f"facet normal {n[0]} {n[1]} {n[2]}\nouter loop")
            out += [f"vertex {v[i][0]:.6f} {v[i][1]:.6f} {v[i][2]:.6f}" for i in tri]
            out.append("endloop\nendfacet")
    out.append(f"endsolid {name}")
    return "\n".join(out)


def _panel(name, axis, at, r):
    """A zero-thickness panel flat on `axis`; r = (a0, b0, a1, b1) on the other two axes."""
    a0, b0, a1, b1 = r
    if axis == "x":
        pts = [(at, a0, b0), (at, a1, b0), (at, a1, b1), (at, a0, b1)]
        n = (1, 0, 0)
    elif axis == "y":
        pts = [(a0, at, b0), (a1, at, b0), (a1, at, b1), (a0, at, b1)]
        n = (0, 1, 0)
    else:
        pts = [(a0, b0, at), (a1, b0, at), (a1, b1, at), (a0, b1, at)]
        n = (0, 0, 1)
    out = [f"solid {name}"]
    for tri in ((0, 1, 2), (0, 2, 3)):
        out.append(f"facet normal {n[0]} {n[1]} {n[2]}\nouter loop")
        out += [f"vertex {pts[i][0]:.6f} {pts[i][1]:.6f} {pts[i][2]:.6f}" for i in tri]
        out.append("endloop\nendfacet")
    out.append(f"endsolid {name}")
    return "\n".join(out)


def fanwall_hall() -> str:
    """A hall on the slab: a gallery at x 0..3, a fan wall, two rows of two
    cabinets facing each other across a contained HOT aisle, chimneys, doors,
    grilles over the aisle, a mesh above the ceiling into the gallery."""
    W, L, slab, ceil, top = 12.0, 8.0, 6.0, 4.0, 2.2
    parts = [_box("hall", (0, 0, 0), (W, L, slab)),
             _panel("ceiling", "z", ceil, (3.0, 0.0, W, L)),
             _panel("wall:gallery_w", "x", 3.0, (0.0, 0.0, L, slab)),
             _panel("mesh:gallery_w_return", "x", 3.0, (0.0, ceil, L, slab)),
             _box("unit:U01:+x", (1.2, 3.0, 0.0), (3.0, 5.0, 3.6))]
    # row F1 at y 2.0..3.2 facing -y (cold aisle 0..2), row F2 at y 4.4..5.6 facing +y
    for i in range(2):
        x0 = 5.0 + 0.6 * i
        parts.append(_box(f"rack:F1-{i + 1:02d}:-y", (x0, 2.0, 0.0), (x0 + 0.6, 3.2, top)))
        parts.append(_box(f"rack:F2-{i + 1:02d}:+y", (x0, 4.4, 0.0), (x0 + 0.6, 5.6, top)))
    parts += [_panel("wall:h1_s", "y", 3.2, (5.0, top, 6.2, ceil)),
              _panel("wall:h1_n", "y", 4.4, (5.0, top, 6.2, ceil)),
              _panel("door:h1_w", "x", 5.0, (3.2, 0.0, 4.4, ceil)),
              _panel("door:h1_e", "x", 6.2, (3.2, 0.0, 4.4, ceil)),
              _panel("grille:h1-01", "z", ceil, (5.0, 3.2, 5.6, 3.8)),
              _panel("grille:h1-02", "z", ceil, (5.6, 3.8, 6.2, 4.4))]
    return "\n".join(parts) + "\n"


def downflow_hall() -> str:
    """A hall on a 1,0 m raised floor: a downflow unit on the deck, two rows
    facing each other across a contained COLD aisle with a lid, tiles in it,
    a cage panel, mesh below the deck and above the ceiling."""
    W, L, deck, slab, ceil, top = 12.0, 8.0, 1.0, 6.0, 4.0, 3.2
    parts = [_box("hall", (0, 0, 0), (W, L, slab)),
             _panel("deck", "z", deck, (0.0, 0.0, W, L)),
             _panel("ceiling", "z", ceil, (3.0, 0.0, W, L)),
             _panel("wall:gallery_w", "x", 3.0, (0.0, 0.0, L, slab)),
             _panel("mesh:gallery_w_plenum", "x", 3.0, (0.0, 0.0, L, deck)),
             _panel("mesh:gallery_w_return", "x", 3.0, (0.0, ceil, L, slab)),
             _box("unit:UE-01", (1.6, 3.0, deck), (2.4, 5.0, 3.0))]
    for i in range(2):
        x0 = 5.0 + 0.6 * i
        parts.append(_box(f"rack:A{i + 1:02d}:+y", (x0, 2.0, deck), (x0 + 0.6, 3.2, top)))
        parts.append(_box(f"rack:B{i + 1:02d}:-y", (x0, 4.4, deck), (x0 + 0.6, 5.6, top)))
        parts.append(_panel(f"tile:c1-{i + 1}", "z", deck, (x0, 3.2, x0 + 0.6, 3.8)))
    parts += [_panel("lid:c1", "z", top, (5.0, 3.2, 6.2, 4.4)),
              _panel("door:c1_w", "x", 5.0, (3.2, deck, 4.4, top)),
              _panel("door:c1_e", "x", 6.2, (3.2, deck, 4.4, top)),
              _panel("grille:h1-01", "z", ceil, (5.0, 6.0, 5.6, 6.6)),
              _panel("cage:e", "x", 8.0, (0.0, deck, L, ceil))]
    return "\n".join(parts) + "\n"


def sidecar(stl: str, **extra) -> dict:
    spec = {
        "name": "drawn",
        "geometry": {"file": stl},
        "racks": {"load_kw": 5.0, "loads": {"F1-01": 10.0, "A01": 0}, "airflow_cfm_per_kw": 158},
        "fanwall": {"airflow_m3h": 20000, "capacity_kw": 100, "supply_temp_c": 20.0, "static_pressure_pa": 100},
        "components": {"ceiling_return": "ceiling-return-600-open", "gallery_mesh": "gallery-mesh-13",
                       "floor_tile": "floor-tile-600", "cage": "cage-mesh-13"},
        "cage": {"construction": "mesh"},
        "mesh": {"cell_size": [CELL, CELL, CELL]},
    }
    spec.update(extra)
    return spec


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, text: str, name: str = "hall.stl") -> str:
        path = self.dir / name
        path.write_text(text)
        return str(path)

    def build(self, text: str, **extra):
        spec = sidecar(self.write(text), **extra)
        return M.build_model(spec), spec


class FanWallHallTest(ReaderTest):
    def setUp(self):
        super().setUp()
        self.model, self.spec = self.build(fanwall_hall())

    def test_the_domain_is_the_hall_box_and_the_gallery_ends_at_the_dividing_wall(self):
        self.assertEqual(self.model.domain.hi, (12.0, 8.0, 6.0))
        self.assertEqual(len(self.model.galleries), 1)
        self.assertEqual(self.model.gallery.hi[0], 3.0)
        self.assertEqual(self.model.hall.lo[0], 3.0)
        self.assertEqual(self.model.dividers, [3.0])
        self.assertEqual(self.model.ceiling_z, 4.0)

    def test_the_cabinets_are_racks_in_rows_that_face_the_way_the_solid_says(self):
        rows = {row.id: row for row in self.model.rows}
        self.assertEqual(set(rows), {"F1", "F2"})
        self.assertEqual(rows["F1"].front_sign, 1)    # front -y: the air travels +y
        self.assertEqual(rows["F2"].front_sign, -1)
        self.assertEqual(_r([rows["F1"].band]), [(2.0, 3.2)])
        loads = {r.id: r.load_kw for r in self.model.racks}
        self.assertEqual(loads, {"F1-01": 10.0, "F1-02": 5.0, "F2-01": 5.0, "F2-02": 5.0})

    def test_the_aisles_are_read_off_the_fronts(self):
        self.assertEqual(_r(self.model.cold_aisles), [(0.0, 2.0), (5.6, 8.0)])
        self.assertEqual(_r(self.model.hot_aisles), [(3.2, 4.4)])
        self.assertEqual(self.model.contained, "hot")

    def test_the_fan_wall_stands_in_the_dividing_wall_facing_the_hall(self):
        fans = self.model.fans
        self.assertEqual([f.name for f in fans], ["fan1"])
        self.assertEqual(fans[0].axis, 0)
        self.assertEqual(fans[0].position, 3.0)
        self.assertEqual(_r(fans[0].extent), [(3.0, 5.0), (0.0, 3.6)])
        self.assertEqual(fans[0].sign, 1)
        self.assertIsNone(fans[0].return_z)
        self.assertEqual(self.model.airflow_m3h, 20000)

    def test_the_mesh_above_the_ceiling_is_the_plenum_opening_at_its_k(self):
        opening = self.model.panel("plenum_opening")
        self.assertEqual(opening.kind, "opening")
        self.assertEqual(_r(opening.extent), [(0.0, 8.0), (4.0, 6.0)])
        self.assertIsNotNone(opening.resistance)

    def test_the_containment_and_the_grilles_come_out_under_the_meshers_names(self):
        names = {p.name for p in self.model.panels}
        self.assertIn("containment_wall_h1_s", names)
        self.assertIn("containment_door_h1_w", names)
        self.assertIn("grille1", names)
        self.assertIn("grille2", names)
        self.assertIn("ceiling", names)
        grille = self.model.panel("grille1")
        self.assertEqual(grille.position, 4.0)
        self.assertIsNotNone(grille.resistance)
        # the rows are closed boxes: a lid and two ends each, as the layouts build them
        self.assertEqual(sum(1 for p in self.model.panels if p.name.startswith("rack_top")), 2)
        self.assertEqual(sum(1 for p in self.model.panels if p.name.startswith("rack_end")), 4)

    def test_the_spec_learns_what_the_drawing_decided(self):
        self.assertEqual(self.spec["containment"], {"enabled": True, "aisle": "hot"})
        self.assertNotIn("floor", self.spec)
        self.assertEqual(self.spec["fanwall"]["count"], 1)
        self.assertEqual(self.spec["hall"]["ceiling"], 4.0)

    def test_the_mesher_has_a_plan_for_every_wall(self):
        from aicfd import case

        zones = {name for name, _panels, _holes in case.wall_plan(self.model)}
        self.assertIn("divider", zones)
        self.assertIn("forro", zones)
        self.assertIn("containment_wall", zones)
        self.assertIn("containment_door", zones)
        self.assertIn("rack_top", zones)
        self.assertTrue(case.topo_set_dict(self.model))


class DownflowHallTest(ReaderTest):
    def setUp(self):
        super().setUp()
        self.model, self.spec = self.build(downflow_hall())

    def test_the_deck_is_the_floor_and_the_room_stands_on_it(self):
        self.assertEqual(self.model.floor_height, 1.0)
        self.assertEqual(self.model.hall.lo[2], 1.0)
        self.assertEqual(self.spec["floor"], {"enabled": True, "height": 1.0})
        deck = self.model.panel("floor_deck")
        self.assertEqual(deck.position, 1.0)
        self.assertEqual(_r(self.model.panel("floor_opening").extent), [(0.0, 8.0), (0.0, 1.0)])

    def test_a_downflow_unit_is_a_horizontal_pair_on_the_deck(self):
        fan = self.model.fans[0]
        self.assertEqual(fan.axis, 2)
        self.assertEqual(fan.position, 1.0)
        self.assertEqual(fan.return_z, 3.0)
        self.assertEqual(fan.sign, -1)
        self.assertEqual(_r(fan.extent), [(1.6, 2.4), (3.0, 5.0)])

    def test_the_cold_aisle_is_contained_with_its_lid_and_its_plates(self):
        self.assertEqual(self.model.contained, "cold")
        self.assertEqual(self.spec["containment"], {"enabled": True, "aisle": "cold"})
        names = {p.name for p in self.model.panels}
        self.assertIn("containment_lid_c1", names)
        tiles = [p for p in self.model.panels if p.name.startswith("tile_")]
        self.assertEqual(len(tiles), 2)
        self.assertEqual(tiles[0].position, 1.0)
        self.assertIsNotNone(self.model.floor_tile_k)

    def test_every_cabinet_resists_at_the_standard_load_whatever_it_dissipates(self):
        empty = next(r for r in self.model.racks if r.id == "A01")
        self.assertEqual(empty.load_kw, 0.0)
        self.assertEqual({r.resistance_kw for r in self.model.racks}, {5.0})

    def test_the_cage_is_a_resistance_and_knows_its_cabinets(self):
        self.assertEqual(self.model.cage, "mesh")
        cage = self.model.panel("cage_e")
        self.assertIsNotNone(cage.resistance)
        self.assertEqual(set(self.model.cage_racks), {"A01", "A02", "B01", "B02"})

    def test_the_equipment_check_sees_a_raised_floor(self):
        spec = sidecar(self.write(downflow_hall()), fanwall={
            "model": "P3100DA", "airflow_m3h": 27500, "capacity_kw": 100.5, "supply_temp_c": 22.0})
        model = M.build_model(spec)
        self.assertEqual(model.equipment.model, "P3100DA")


class RefusalTest(ReaderTest):
    def refused(self, text: str, fragment: str, **extra):
        with self.assertRaises(ValueError) as caught:
            self.build(text, **extra)
        self.assertIn(fragment, str(caught.exception))

    def test_a_box_off_the_grid_is_refused_by_solid(self):
        bad = fanwall_hall().replace("vertex 5.000000 ", "vertex 5.050000 ")   # every face of every solid at x = 5
        self.refused(bad, "not on the")
        self.refused(bad, "rack:F1-01:-y")

    def test_an_unknown_type_is_refused_by_name(self):
        self.refused(fanwall_hall() + _panel("ramp:r1", "y", 1.0, (0.0, 0.0, 1.0, 1.0)) + "\n", "ramp")

    def test_a_cabinet_without_a_front_is_refused(self):
        self.refused(fanwall_hall().replace("rack:F1-01:-y", "rack:F1-01"), "front")

    def test_a_fan_wall_off_its_wall_is_refused(self):
        self.refused(fanwall_hall().replace("unit:U01:+x", "unit:U01:-x"), "blows +x")

    def test_a_missing_file_says_where_it_looked(self):
        with self.assertRaises(ValueError) as caught:
            M.build_model(sidecar("nowhere.stl"))
        self.assertIn("nowhere.stl", str(caught.exception))

    def test_a_unit_named_out_of_service_by_its_tag(self):
        model, spec = self.build(fanwall_hall() + _box("unit:U02:+x", (1.2, 5.6, 0.0), (3.0, 7.6, 3.6)) + "\n",
                                 fanwall={"airflow_m3h": 20000, "capacity_kw": 100, "supply_temp_c": 20.0,
                                          "static_pressure_pa": 100, "out_of_service": ["U02"]})
        self.assertEqual(model.fans_off, ("fan2",))
        self.assertEqual(model.airflow_m3h, 20000)


class ParametricCasesAreUntouchedTest(unittest.TestCase):
    def test_a_case_without_geometry_takes_the_parametric_path(self):
        from tests import support

        model = M.build_model(support.spec("pod-fanwall"))
        self.assertFalse(any(w.startswith("Geometry read") for w in model.warnings))


if __name__ == "__main__":
    unittest.main()


class DoorOnTheWallTest(ReaderTest):
    def test_a_door_in_the_dividing_walls_plane_is_left_to_the_wall(self):
        text = downflow_hall() + _panel("door:c1_x", "x", 3.0, (3.2, 1.0, 4.4, 3.2)) + "\n"
        model, _ = self.build(text)
        self.assertNotIn("containment_door_c1_x", {p.name for p in model.panels})
        self.assertIn("containment_door_c1_w", {p.name for p in model.panels})
