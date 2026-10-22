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


def parallel_rows_hall() -> str:
    """A hall on a 1,0 m raised floor whose rows run PARALLEL to the dividing
    wall: a gallery at x 0..3 with a downflow unit on the deck, and two rows of
    two cabinets stood end to end along y, facing each other across a contained
    COLD aisle that runs along y -- fronts `+x` and `-x`, bands on x (ADR-137)."""
    W, L, deck, slab, ceil, top = 12.0, 8.0, 1.0, 6.0, 4.0, 3.2
    parts = [_box("hall", (0, 0, 0), (W, L, slab)),
             _panel("deck", "z", deck, (0.0, 0.0, W, L)),
             _panel("ceiling", "z", ceil, (3.0, 0.0, W, L)),
             _panel("wall:gallery_w", "x", 3.0, (0.0, 0.0, L, slab)),
             _panel("mesh:gallery_w_plenum", "x", 3.0, (0.0, 0.0, L, deck)),
             _panel("mesh:gallery_w_return", "x", 3.0, (0.0, ceil, L, slab)),
             _box("unit:UE-01", (1.6, 3.0, deck), (2.4, 5.0, 3.0))]
    for i in range(2):
        y0 = 2.0 + 0.6 * i
        parts.append(_box(f"rack:A{i + 1:02d}:+x", (5.0, y0, deck), (6.2, y0 + 0.6, top)))
        parts.append(_box(f"rack:B{i + 1:02d}:-x", (7.4, y0, deck), (8.6, y0 + 0.6, top)))
        for j in range(2):
            x0 = 6.2 + 0.6 * j
            parts.append(_panel(f"tile:c1-{i + 1}-{j + 1}", "z", deck, (x0, y0, x0 + 0.6, y0 + 0.6)))
    parts += [_panel("lid:c1", "z", top, (6.2, 2.0, 7.4, 3.2)),
              _panel("door:c1_s", "y", 2.0, (6.2, deck, 7.4, top)),
              _panel("door:c1_n", "y", 3.2, (6.2, deck, 7.4, top)),
              _panel("grille:h1-01", "z", ceil, (9.0, 2.0, 9.6, 2.6))]
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


class RowsParallelToTheWallsTest(ReaderTest):
    """A hall whose rows run along the dividing wall rather than toward it
    (ADR-137): the gallery is at the end of a long room and the aisles are
    stacked along the room's length. The reader used to refuse it for a front
    of `+x`."""

    def setUp(self):
        super().setUp()
        self.model, self.spec = self.build(parallel_rows_hall())

    def test_the_rows_run_along_y_and_are_stacked_along_x(self):
        self.assertEqual(self.model.row_axis, 1)
        self.assertEqual(self.model.across, 0)
        rows = {row.id: row for row in self.model.rows}
        self.assertEqual(set(rows), {"A", "B"})
        self.assertEqual(_r([rows["A"].band, rows["B"].band]), [(5.0, 6.2), (7.4, 8.6)])
        self.assertEqual(_r([rows["A"].span]), [(2.0, 3.2)])
        self.assertEqual([row.axis for row in self.model.rows], [1, 1])

    def test_the_air_goes_through_the_cabinets_along_x(self):
        a = next(r for r in self.model.racks if r.id == "A01")
        b = next(r for r in self.model.racks if r.id == "B01")
        self.assertEqual((a.airflow_axis, a.airflow_sign), (0, -1))   # front +x: the air travels toward -x
        self.assertEqual((b.airflow_axis, b.airflow_sign), (0, 1))

    def test_the_aisles_are_read_off_the_fronts_on_x(self):
        self.assertEqual(_r(self.model.cold_aisles), [(6.2, 7.4)])
        self.assertEqual(_r(self.model.hot_aisles), [(3.0, 5.0), (8.6, 12.0)])
        self.assertEqual(self.model.contained, "cold")

    def test_a_cabinets_lid_and_ends_close_it_on_the_axes_it_runs_along(self):
        top = self.model.panel("rack_top_A_1")
        self.assertEqual((top.axis, _r(top.extent)), (2, [(5.0, 6.2), (2.0, 3.2)]))
        ends = sorted((p.position for p in self.model.panels if p.name.startswith("rack_end_A_1_")))
        self.assertEqual(ends, [2.0, 3.2])
        end = next(p for p in self.model.panels if p.name.startswith("rack_end_A_1_"))
        self.assertEqual((end.axis, _r(end.extent)), (1, [(5.0, 6.2), (1.0, 3.2)]))

    def test_the_plates_and_the_lid_are_read_where_the_drawing_put_them(self):
        tiles = [p for p in self.model.panels if p.name.startswith("tile_")]
        self.assertEqual(len(tiles), 4)
        self.assertIn("containment_lid_c1", {p.name for p in self.model.panels})
        self.assertEqual(_r(self.model.panel("containment_door_c1_s").extent), [(6.2, 7.4), (1.0, 3.2)])

    def test_the_export_says_which_way_the_rows_run(self):
        out = M.to_dict(self.model, self.spec)
        self.assertEqual(out["row_axis"], 1)
        self.assertEqual({row["axis"] for row in out["rows"]}, {1})

    def test_rows_along_x_are_unchanged(self):
        model, _spec = self.build(downflow_hall())
        self.assertEqual((model.row_axis, model.across), (0, 1))
        self.assertEqual({row.axis for row in model.rows}, {0})

    def test_fronts_on_both_axes_are_refused(self):
        bad = parallel_rows_hall().replace("rack:B02:-x", "rack:B02:-y")
        with self.assertRaises(ValueError) as caught:
            self.build(bad)
        self.assertIn("rack:B02", str(caught.exception))
        self.assertIn("runs the same way", str(caught.exception))

    def test_a_front_along_the_rows_is_refused_by_name(self):
        """A `+y` front among cabinets that face on x is named, not guessed at."""
        bad = parallel_rows_hall().replace("rack:A01:+x", "rack:A01:+y")
        with self.assertRaises(ValueError) as caught:
            self.build(bad)
        self.assertIn("rack:A01", str(caught.exception))

    def test_a_row_facing_an_aisle_with_no_plate_is_named(self):
        """The end row of a hall faces the walkway to the wall; on a raised
        floor nothing feeds it, and the cabinets breathe room air."""
        text = parallel_rows_hall()
        # turn row B round: it now faces the open end of the hall at x 8.6..12
        text = text.replace("rack:B01:-x", "rack:B01:+x").replace("rack:B02:-x", "rack:B02:+x")
        model, _spec = self.build(text)
        note = next((w for w in model.warnings if w.startswith("Cabinets facing an aisle nothing feeds")), "")
        self.assertIn("B face the", note)
        self.assertIn("x = 8.60..12.00", note)
        self.assertNotIn("A face", note)

    def test_a_fed_aisle_says_nothing(self):
        self.assertFalse([w for w in self.model.warnings if "nothing feeds" in w])

    def test_the_mesher_builds_the_case_and_warms_the_hot_aisles_on_x(self):
        from aicfd import case

        target = self.dir / "run"
        case.build(self.model, target)
        self.assertTrue((target / "system" / "blockMeshDict").is_file())
        values = [float(v) for v in case.warm_start(self.model).splitlines()[3:-1]]
        nx, ny, nz = self.model.divisions
        cx, cy, cz = self.model.cell_size

        def at(x, y, z):
            i, j, k = int(x / cx), int(y / cy), int(z / cz)
            return values[(k * ny + j) * nx + i]

        supply = self.model.supply_temp_c + 273.15
        # in a hot band, level with the racks: warm
        self.assertGreater(at(9.5, 2.5, 2.0), supply + 0.5)
        # in a rack's own place, and in the cold aisle: not seeded warm
        self.assertAlmostEqual(at(5.5, 2.5, 2.0), supply, places=1)
        self.assertAlmostEqual(at(6.5, 2.5, 2.0), supply, places=1)
        # past the end of the rows (y > 3.2) a hot band is not seeded: no rows to be between
        self.assertAlmostEqual(at(9.5, 6.0, 2.0), supply, places=1)


class ASealedVolumeTest(ReaderTest):
    """A drywall enclosure round an empty corner closes a volume the air never
    reaches, and the solver died on it at the first iteration with a floating
    point exception (ADR-138)."""

    BOX = "\n".join([
        _panel("wall:k_w", "x", 10.0, (6.0, 1.0, 7.0, 4.0)),
        _panel("wall:k_e", "x", 11.0, (6.0, 1.0, 7.0, 4.0)),
        _panel("wall:k_s", "y", 6.0, (10.0, 1.0, 11.0, 4.0)),
        _panel("wall:k_n", "y", 7.0, (10.0, 1.0, 11.0, 4.0)),
    ]) + "\n"

    def test_it_is_found_and_given_a_leak_through_one_of_its_walls(self):
        model, _spec = self.build(parallel_rows_hall() + self.BOX)
        leaks = [p for p in model.panels if p.name.startswith("leak")]
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0].resistance, M.LEAK_K)
        self.assertAlmostEqual(leaks[0].area, CELL * CELL, places=6)
        self.assertEqual(M.sealed_pockets(model), [], "the leak has to join it to the room")
        note = next(w for w in model.warnings if w.startswith("A sealed volume"))
        self.assertIn("x 10.00..11.00, y 6.00..7.00, z 1.00..4.00", note)

    def test_the_wall_with_the_leak_keeps_the_rest_of_its_area(self):
        model, _spec = self.build(parallel_rows_hall() + self.BOX)
        leak = next(p for p in model.panels if p.name.startswith("leak"))
        pieces = [p for p in model.panels if p.axis == leak.axis
                  and abs(p.position - leak.position) < 1e-6 and p.kind == "wall"
                  and p.name.startswith("containment")]
        self.assertAlmostEqual(sum(p.area for p in pieces) + leak.area, 3.0, places=6)

    def test_a_room_with_no_sealed_volume_gets_no_leak(self):
        self.assertFalse([p for p in self.build(parallel_rows_hall())[0].panels
                          if p.name.startswith("leak")])

    def test_the_mesher_refuses_a_sealed_volume_rather_than_the_solver_dying_on_it(self):
        from aicfd import case

        model, _spec = self.build(parallel_rows_hall() + self.BOX)
        model.panels = [p for p in model.panels if not p.name.startswith("leak")]
        model.panels.append(M.Panel("plug", "wall", axis=0, position=10.0, extent=((6.0, 7.0), (1.0, 4.0))))
        with self.assertRaises(ValueError) as caught:
            case.build(model, self.dir / "run")
        self.assertIn("sealed volume", str(caught.exception))

    def test_the_leak_is_a_surface_the_air_may_cross(self):
        from aicfd import case, post

        model, _spec = self.build(parallel_rows_hall() + self.BOX)
        self.assertIn("leak1", {p.name for p in case.porous(model)})
        self.assertTrue("leak1_below".startswith(post.PASSES_FLOW))


class TheAislesAreMeasuredWhereTheyAreTest(unittest.TestCase):
    """The post-processing picks cells by the aisle bands, and the bands are on
    x for a row that runs along y (ADR-137)."""

    STEP = 0.5

    def grid(self, nx=24, ny=16, nz=4):
        import numpy as np

        return {"x": (np.arange(nx) + 0.5) * self.STEP, "y": (np.arange(ny) + 0.5) * self.STEP,
                "z": (np.arange(nz) + 0.5) * self.STEP}

    def model(self, along: int):
        from types import SimpleNamespace

        hall = SimpleNamespace(lo=(3.0, 0.0, 1.0), hi=(12.0, 8.0, 6.0))
        # the band is on the axis the rows are stacked along
        band = (4.0, 5.0) if along == 0 else (9.0, 10.0)
        return SimpleNamespace(row_axis=along, across=1 - along, hot_aisles=[band], hall=hall)

    def coordinates(self, picks):
        j, i = picks
        return ({(jj + 0.5) * self.STEP for jj in set(j.ravel().tolist())},
                {(ii + 0.5) * self.STEP for ii in set(i.ravel().tolist())})

    def test_rows_along_x_pick_a_y_band_inside_the_halls_x_range(self):
        from aicfd import post

        ys, xs = self.coordinates(post._hot_aisle_cells(self.model(0), self.grid()))
        self.assertTrue(ys and all(4.0 <= y <= 5.0 for y in ys))
        self.assertTrue(xs and all(3.0 <= x <= 12.0 for x in xs))

    def test_rows_along_y_pick_an_x_band_inside_the_halls_y_range(self):
        from aicfd import post

        ys, xs = self.coordinates(post._hot_aisle_cells(self.model(1), self.grid()))
        self.assertTrue(xs and all(9.0 <= x <= 10.0 for x in xs))
        self.assertTrue(ys and all(0.0 <= y <= 8.0 for y in ys))
        self.assertEqual(len(ys), 16, "every y inside the hall, not one band of them")


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


def two_size_hall() -> str:
    """The raised-floor hall with a second, smaller downflow unit beside the
    first in the same gallery (ADR-139)."""
    return downflow_hall() + _box("unit:UE-02", (1.6, 5.6, 1.0), (2.4, 7.6, 3.0)) + "\n"


class APlantOfTwoSizesTest(ReaderTest):
    """`fanwall` is THE unit; `fanwall.units` names the ones that differ and
    each carries its own rating, in the solve and in every sum (ADR-139)."""

    PLANT = {"airflow_m3h": 20000, "capacity_kw": 100, "power_kw": 4.0, "supply_temp_c": 20.0,
             "static_pressure_pa": 100, "units": {"UE-02": {"airflow_m3h": 10000, "capacity_kw": 50,
                                                           "power_kw": 2.0}}}

    def setUp(self):
        super().setUp()
        self.model, self.spec = self.build(two_size_hall(), fanwall=dict(self.PLANT))
        self.big, self.small = self.model.fans

    def test_each_unit_moves_its_own_airflow_and_the_plant_moves_the_sum(self):
        self.assertTrue(self.model.mixed_plant)
        self.assertEqual(self.model.flow_of(self.big), 20000)
        self.assertEqual(self.model.flow_of(self.small), 10000)
        self.assertEqual(self.model.airflow_m3h, 30000)

    def test_capacity_and_power_are_summed_unit_by_unit(self):
        h = self.model.hvac()
        self.assertEqual(h["capacity_kw"], 150)
        self.assertEqual(h["power_kw"], 6.0)
        self.assertIsNone(h["unit_capacity_kw"])
        self.assertIsNone(h["units_needed"])
        self.assertEqual([(g["units"], g["capacity_kw"]) for g in h["plant"]], [(1, 100), (1, 50)])

    def test_no_single_per_unit_figure_is_left_for_a_reader_to_misquote(self):
        self.assertIsNone(self.model.unit_capacity_kw)
        self.assertIsNone(self.model.unit_power_kw)

    def test_the_summary_writes_the_plant_out_by_size(self):
        rows = {row[0]: row[1] for row in M.summary_rows(self.model)}
        self.assertIn("1 x 20,000 + 1 x 10,000 = 30,000 m3/h", rows["Supply airflow"])
        self.assertIn("1 x 100 + 1 x 50 kW = 150 kW", rows["Cooling capacity"])

    def test_each_units_boundary_imposes_its_own_mass(self):
        from aicfd import case

        rho = case.supply_density(self.model)
        for fan, flow in ((self.big, 20000), (self.small, 10000)):
            text = "\n".join(case._downflow_baffles(self.model, fan, 0.01, 0.01))
            self.assertIn(f"massFlowRate {flow / 3600 * rho:.6g};", text)

    def test_the_export_carries_every_units_rating(self):
        ratings = M.to_dict(self.model, self.spec)["operating"]["unit_ratings"]
        self.assertEqual(ratings["fan1"], {"airflow_m3h": 20000, "capacity_kw": 100, "power_kw": 4.0})
        self.assertEqual(ratings["fan2"], {"airflow_m3h": 10000, "capacity_kw": 50, "power_kw": 2.0})

    def test_a_unit_out_of_service_takes_its_own_airflow_with_it(self):
        model, _ = self.build(two_size_hall(), fanwall={**self.PLANT, "out_of_service": ["UE-02"]})
        self.assertEqual(model.airflow_m3h, 20000)
        self.assertEqual(model.hvac()["capacity_kw"], 100)

    def test_a_unit_given_the_plants_own_figures_is_one_size_after_all(self):
        model, _ = self.build(two_size_hall(), fanwall={**self.PLANT, "units": {"UE-02": {"airflow_m3h": 20000,
                                                                                          "capacity_kw": 100}}})
        self.assertFalse(model.mixed_plant)
        self.assertEqual(model.unit_capacity_kw, 100)
        self.assertEqual(model.airflow_m3h, 40000)

    def test_a_unit_is_named_by_its_number_or_its_panel_too(self):
        for key in ("2", "fan2"):
            model, _ = self.build(two_size_hall(), fanwall={**self.PLANT, "units": {key: {"airflow_m3h": 10000}}})
            self.assertEqual(model.flow_of(model.fans[1]), 10000)

    def refused(self, fragment: str, **fanwall):
        with self.assertRaises(ValueError) as caught:
            self.build(two_size_hall(), fanwall={**self.PLANT, **fanwall})
        self.assertIn(fragment, str(caught.exception))

    def test_a_unit_the_drawing_does_not_have_is_refused_by_name(self):
        self.refused("names no unit", units={"UE-09": {"airflow_m3h": 10000}})

    def test_a_figure_a_unit_does_not_take_is_refused(self):
        self.refused("not a unit's figure", units={"UE-02": {"airflow": 10000}})

    def test_one_machines_file_is_refused_for_a_plant_of_two(self):
        self.refused("fanwall.model", model="P3100DA", airflow_m3h=27500, capacity_kw=100.5)

    def test_one_fans_curve_is_refused_for_a_plant_of_two(self):
        self.refused("fanwall.curve", curve=[[0, 300], [30000, 0]])


class AWallBetweenTwoHallsTest(ReaderTest):
    """Two halls sharing a gallery, a raised floor and a ceiling void, with a
    solid wall between them from the deck to the false ceiling (ADR-140)."""

    def hall(self) -> str:
        return (downflow_hall()
                + _panel("wall:partition_dh", "y", 6.8, (3.0, 1.0, 12.0, 4.0)) + "\n"
                + _panel("grille:h2-01", "z", 4.0, (5.0, 7.2, 5.6, 7.8)) + "\n")

    def test_it_is_a_wall_named_for_what_it_is(self):
        model, _ = self.build(self.hall())
        wall = model.panel("partition_dh")
        self.assertEqual((wall.kind, wall.axis, round(wall.position, 3)), ("wall", 1, 6.8))
        self.assertEqual(_r(wall.extent), [(3.0, 12.0), (1.0, 4.0)])

    def test_the_mesher_builds_it(self):
        from aicfd import case

        model, _ = self.build(self.hall())
        zones = {name for name, _panels, _holes in case.wall_plan(model)}
        self.assertIn("partition_dh", zones)

    def test_open_below_the_deck_and_above_the_ceiling_it_seals_nothing(self):
        model, _ = self.build(self.hall())
        self.assertFalse([p for p in model.panels if p.name.startswith("leak")])


class AParametricPlantOfTwoSizesTest(unittest.TestCase):
    def test_the_units_are_named_by_their_number(self):
        from tests import support

        spec = support.spec("hall")
        fan = {k: v for k, v in spec["fanwall"].items() if k not in ("model", "curve")}
        fan["units"] = {1: {"airflow_m3h": 50000, "capacity_kw": 200}}
        spec["fanwall"] = fan
        model = M.build_model(spec)
        self.assertTrue(model.mixed_plant)
        self.assertEqual(model.flow_of(model.fans[0]), 50000)
        self.assertEqual(model.airflow_m3h, 50000 + 104181 * (len(model.fans) - 1))


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


def plenum_hall() -> str:
    """The fan-wall hall with the wall into it doubled: an inner leaf at
    x = 4.2 and a supply grille in it in front of each cold aisle."""
    leaf = [_panel("wall:plenum_w", "x", 4.2, (0.0, 0.0, 8.0, 4.0)),
            _panel("supply:w1", "x", 4.2, (0.4, 0.0, 1.6, 2.0)),
            _panel("supply:w2", "x", 4.2, (6.0, 0.0, 7.6, 2.0))]
    return fanwall_hall() + "\n".join(leaf) + "\n"


class SupplyGrilleSizeTest(ReaderTest):
    """One size for every supply grille, chosen by the scenario (ADR-134)."""

    def supplies(self, model):
        return sorted((p for p in model.panels if p.name.startswith("supply")),
                      key=lambda p: p.extent[0][0])

    def test_the_drawn_size_needs_no_override(self):
        model, _ = self.build(plenum_hall())
        self.assertEqual([_r([p.extent[0], p.extent[1]]) for p in self.supplies(model)],
                         [[(0.4, 1.6), (0.0, 2.0)], [(6.0, 7.6), (0.0, 2.0)]])
        self.assertFalse(any("cut to" in w for w in model.warnings))

    def test_every_grille_takes_the_size_about_its_centre_on_its_sill(self):
        model, _ = self.build(plenum_hall(), plenum={"grille": {"width": 0.8, "height": 3.0}})
        first, second = self.supplies(model)
        self.assertEqual(_r([first.extent[0], first.extent[1]]), [(0.6, 1.4), (0.0, 3.0)])
        self.assertEqual(_r([second.extent[0], second.extent[1]]), [(6.4, 7.2), (0.0, 3.0)])
        self.assertTrue(any("cut to 0.8 x 3 m" in w for w in model.warnings))

    def test_a_grille_through_the_false_ceiling_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.build(plenum_hall(), plenum={"grille": {"height": 4.4}})
        self.assertIn("false ceiling", str(caught.exception))

    def test_a_grille_past_the_end_of_its_leaf_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.build(plenum_hall(), plenum={"grille": {"width": 4.0}})
        self.assertIn("past the end of its leaf", str(caught.exception))

    def test_grilles_run_into_each_other_are_refused(self):
        with self.assertRaises(ValueError) as caught:
            self.build(plenum_hall(), plenum={"grille": {"width": 5.8}})
        self.assertIn("plenum.grille.width", str(caught.exception))
