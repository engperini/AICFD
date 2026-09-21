"""A customer cage: a boundary inside the hall, round one customer's rows.

The drawing shows the same rectangle whichever way it is built, and the two
are different rooms. Drywall is a partition the air cannot cross at all; mesh
is a resistance it pays TWICE, once entering on the cold side and once leaving
on the hot one. Measured on the shipped POD with a cage round its row, 400
iterations each, everything else identical: the drywall cage costs 3,5% more
fan rise and puts the peak speed up 10,4%, because the air that cannot cross
the wall goes over the top of it instead (ADR-096).

That is the whole reason the construction is a field. What this module holds
is the two ways it can go wrong:

  the same face in two zones   a cage is named `cage_*` either way, and one
                               construction is a wall while the other is a
                               porous surface. Claimed by both, `createBaffles`
                               refuses the case (ADR-095 again, and the reason
                               `wall_plan` now excludes anything that resists)
  a room with no way in        drywall to the ceiling seals the racks off from
                               the supply while the ceiling grilles over their
                               own hot aisles still let air OUT. There is no
                               steady solution and the solver does not say so
                               politely -- four ranks died on a floating point
                               exception nine minutes in, having meshed and
                               decomposed perfectly
"""

from __future__ import annotations

import copy
import unittest

import yaml

from aicfd import case as case_module
from aicfd import components, model as m
from tests import support


def pod(**cage) -> dict:
    spec = yaml.safe_load((support.REPO / "cases" / "pod-fanwall.yaml").read_text())
    if cage:
        spec["cage"] = {"enabled": True, **cage}
    return spec


class SpecTest(unittest.TestCase):
    def test_a_case_without_one_is_unchanged(self):
        built = m.build_model(pod())
        self.assertFalse([p for p in built.panels if p.name.startswith("cage_")])
        self.assertIsNone(built.cage)

    def test_the_construction_has_to_be_one_of_the_two(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="glass", clearance=0.6))
        self.assertIn("mesh", str(caught.exception))
        self.assertIn("drywall", str(caught.exception))

    def test_a_clearance_outside_the_range_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="mesh", clearance=0.01))
        self.assertIn("cage.clearance", str(caught.exception))


class GeometryTest(unittest.TestCase):
    def cage(self, **kw):
        built = m.build_model(pod(construction="mesh", clearance=0.6, **kw))
        return built, [p for p in built.panels if p.name.startswith("cage_")]

    def test_it_encloses_every_rack_at_the_stated_clearance(self):
        built, walls = self.cage()
        self.assertEqual(len(walls), 4, "four walls and no roof")
        by = {w.name: w for w in walls}
        racks = built.racks
        for name, axis, want in (
            ("cage_near", 0, min(r.box.lo[0] for r in racks) - 0.6),
            ("cage_far", 0, max(r.box.hi[0] for r in racks) + 0.6),
            ("cage_left", 1, min(r.box.lo[1] for r in racks) - 0.6),
            ("cage_right", 1, max(r.box.hi[1] for r in racks) + 0.6),
        ):
            with self.subTest(wall=name):
                self.assertEqual(by[name].axis, axis)
                self.assertAlmostEqual(by[name].position, want, places=6)

    def test_it_runs_from_the_floor_to_the_false_ceiling_by_default(self):
        built, walls = self.cage()
        for wall in walls:
            with self.subTest(wall=wall.name):
                self.assertEqual(wall.extent[1], (built.hall.lo[2], built.ceiling_z))

    def test_a_roof_is_a_fifth_surface_of_the_same_construction(self):
        _built, walls = self.cage(roof=True)
        roof = [w for w in walls if w.name == "cage_roof"]
        self.assertEqual(len(roof), 1)
        self.assertEqual(roof[0].axis, 2)
        self.assertEqual(roof[0].kind, walls[0].kind)

    def test_a_side_the_room_closes_is_not_built(self):
        """A cage in a CORNER has fewer than four walls, and the missing ones
        are not missing: the room is the boundary there. A wall built on the
        hall wall is not one -- `topoSet` would take the boundary faces the
        room is made of."""
        # The POD's row stands 2,0 m from the near wall, 1,8 from one side
        # and 1,2 from the other, so a 2,0 m clearance leaves only the far
        # end as a wall the room does not already close.
        built = m.build_model(pod(construction="mesh", clearance=2.0,
                                  sides=["far"]))
        walls = [p.name for p in built.panels if p.name.startswith("cage_")]
        self.assertEqual(walls, ["cage_far"])

    def test_the_sides_it_drops_are_said_out_loud(self):
        built = m.build_model(pod(construction="mesh", clearance=2.0))
        notes = [w for w in built.warnings if w.startswith("cage ")]
        self.assertTrue(notes, "a side dropped in silence is a wall nobody "
                               "knows is missing")
        for note in notes:
            self.assertIn("the room already closes it", note)

    def test_a_wall_the_room_closes_runs_wall_to_wall(self):
        """The half that matters. Without clipping, the wall that IS the cage
        stops short of the room at both ends and the case models a partition
        with a gap nobody asked for."""
        built = m.build_model(pod(construction="mesh", clearance=2.0,
                                  sides=["far"]))
        wall = next(p for p in built.panels if p.name == "cage_far")
        self.assertAlmostEqual(wall.extent[0][0], built.hall.lo[1], places=6)
        self.assertAlmostEqual(wall.extent[0][1], built.hall.hi[1], places=6)

    def test_a_cage_the_room_closes_on_every_side_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="mesh", clearance=2.5))
        self.assertIn("no wall is left to build", str(caught.exception))

    def test_naming_a_side_the_room_closes_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="mesh", clearance=2.0,
                              sides=["left", "far"]))
        said = str(caught.exception)
        self.assertIn("cage.sides", said)
        self.assertIn("the room", said)


class PlacementTest(unittest.TestCase):
    """A cage goes where the drawing puts it: the middle of a hall, a corner,
    or across it dividing one customer from the rest (ADR-098)."""

    def hall(self, **cage) -> dict:
        spec = yaml.safe_load(
            (support.REPO / "cases" / "hall-double-gallery.yaml").read_text())
        spec["pods"] = 4
        spec["racks"]["blocks"] = 1
        spec["fanwall"]["count"] = 8
        if cage:
            spec["cage"] = {"enabled": True, "construction": "mesh", **cage}
        return spec

    def walls(self, **cage) -> list[str]:
        built = m.build_model(self.hall(**cage))
        return sorted(p.name[len("cage_"):] for p in built.panels
                      if p.name.startswith("cage_"))

    def test_the_middle_of_a_hall_has_four_walls(self):
        self.assertEqual(self.walls(clearance=1.2),
                         ["far", "left", "near", "right"])

    def test_a_cage_over_some_pods_only(self):
        built = m.build_model(self.hall(clearance=1.2, pods=[1, 2]))
        wall = next(p for p in built.panels if p.name == "cage_right")
        inside = [r for row in built.rows if row.id in ("F1", "F2", "F3", "F4")
                  for r in row.racks]
        outside = [r for row in built.rows if row.id in ("F5", "F6", "F7", "F8")
                   for r in row.racks]
        self.assertTrue(all(r.box.hi[1] <= wall.position + 1e-6 for r in inside),
                        "a rack the cage encloses is outside its wall")
        self.assertTrue(all(r.box.lo[1] >= wall.position - 1e-6 for r in outside),
                        "a rack outside the cage is inside its wall")

    def test_the_dividing_wall_of_an_edge_cage_is_the_only_one(self):
        """The arrangement the Fortaleza plan shows."""
        self.assertEqual(self.walls(clearance=0.6, pods=[1, 2],
                                    sides=["right"]), ["right"])

    def test_pods_have_to_be_neighbours(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(self.hall(clearance=1.2, pods=[1, 3]))
        self.assertIn("contiguous", str(caught.exception))

    def test_a_pod_the_hall_has_not_got_is_named(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(self.hall(clearance=1.2, pods=[9]))
        said = str(caught.exception)
        self.assertIn("cage.pods", said)
        self.assertIn("pods 1 to 4", said, "the refusal has to say what it has")

    def test_a_wall_may_not_cut_a_cabinet(self):
        """Too big a clearance walks the dividing wall into the next pod's
        rows, which meshes and models a partition through the middle of
        somebody's cabinets."""
        with self.assertRaises(ValueError) as caught:
            m.build_model(self.hall(clearance=3.5, pods=[1, 2], sides=["right"]))
        said = str(caught.exception)
        self.assertIn("inside", said)
        self.assertRegex(said, r"F\d", "the refusal has to name the cabinet")

    def test_a_side_is_a_side(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(self.hall(clearance=1.2, sides=["north"]))
        self.assertIn("cage.sides", str(caught.exception))


class BoundaryAisleTest(unittest.TestCase):
    """A cage wall stands in a cold aisle that BOTH sides breathe from.

    The row inside the cage and the row outside it face the same aisle, so a
    wall down the middle of it halves the aisle for each. At the hall's own
    1,20 m that is 0,60 m of cold aisle in front of a row of cabinets, which
    is a different room -- and nothing said so: the case built, meshed, and
    the first thing to notice was somebody looking at the drawing (ADR-099).

    `cage.aisle` widens the aisles a cage wall stands in, and only those:
    `aisles.cold` still draws every other one. `cage.clearance` then says
    where in that aisle the wall sits.
    """

    def hall(self, **cage) -> dict:
        spec = yaml.safe_load(
            (support.REPO / "cases" / "hall-double-gallery.yaml").read_text())
        spec["pods"] = 4
        spec["racks"]["blocks"] = 1
        spec["fanwall"]["count"] = 8
        spec["cage"] = {"enabled": True, "construction": "mesh",
                        "pods": [1, 2], "sides": ["right"], **cage}
        return spec

    def gaps(self, **cage) -> tuple:
        built = m.build_model(self.hall(**cage))
        wall = next(p for p in built.panels if p.name == "cage_right")
        rows = {row.id: row for row in built.rows}
        inside = max(r.box.hi[1] for r in rows["F4"].racks)
        outside = min(r.box.lo[1] for r in rows["F5"].racks)
        return round(wall.position - inside, 6), round(outside - wall.position, 6)

    def test_without_it_the_wall_halves_the_hall_s_own_aisle(self):
        cold = yaml.safe_load(
            (support.REPO / "cases" / "hall-double-gallery.yaml").read_text()
        )["aisles"]["cold"]
        inside, outside = self.gaps(clearance=cold / 2)
        self.assertAlmostEqual(inside + outside, cold, places=6)

    def test_it_widens_only_the_aisle_the_wall_stands_in(self):
        narrow = m.build_model(self.hall(clearance=1.2))
        wide = m.build_model(self.hall(clearance=1.2, aisle=4.2))
        grew = wide.domain.hi[1] - narrow.domain.hi[1]
        cold = narrow.cold_aisles
        self.assertGreater(grew, 0, "a wider boundary aisle makes a wider hall")
        # Exactly ONE aisle changed, so the hall grew by exactly its increase.
        self.assertAlmostEqual(
            grew, 4.2 - float(self.hall()["aisles"]["cold"]), places=6,
            msg="every other cold aisle has to stay as `aisles.cold` drew it")
        self.assertEqual(len(wide.cold_aisles), len(cold))

    def test_the_clearance_positions_the_wall_inside_it(self):
        centred = self.gaps(aisle=4.2, clearance=2.1)
        self.assertAlmostEqual(centred[0], 2.1, places=6)
        self.assertAlmostEqual(centred[1], 2.1, places=6)
        offset = self.gaps(aisle=4.2, clearance=3.0)
        self.assertAlmostEqual(offset[0], 3.0, places=6)
        self.assertAlmostEqual(offset[1], 1.2, places=6)

    def test_a_cage_in_the_middle_of_the_hall_widens_both_boundaries(self):
        spec = self.hall(aisle=4.2)
        spec["cage"]["pods"] = [2, 3]
        spec["cage"].pop("sides")
        narrow = dict(spec, cage=dict(spec["cage"]))
        narrow["cage"].pop("aisle")
        grew = (m.build_model(spec).domain.hi[1]
                - m.build_model(narrow).domain.hi[1])
        step = 4.2 - float(spec["aisles"]["cold"])
        self.assertAlmostEqual(grew, 2 * step, places=6,
                               msg="a cage between two pods divides the hall "
                                   "twice, so two aisles carry a wall")

    def test_an_aisle_outside_the_range_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(self.hall(aisle=0.2))
        self.assertIn("cage.aisle", str(caught.exception))


class TheWallSaysWhatItCostsTest(unittest.TestCase):
    """Said only when it costs a row something.

    A wall in an aisle wide enough to hold it leaves both rows the cold aisle
    the hall was drawn with, and there is nothing to report. Saying it anyway
    would be one more paragraph on every run, which is a fault this repository
    has already had to undo once (ADR-098).
    """

    def notes(self, **cage) -> list[str]:
        spec = BoundaryAisleTest().hall(**cage)
        return [w for w in m.build_model(spec).warnings if w.startswith("cage ")]

    def test_a_halved_aisle_is_reported_with_both_gaps(self):
        said = self.notes(clearance=1.2)
        self.assertTrue(said, "a wall that halves a cold aisle said nothing")
        self.assertIn("F4", said[0])
        self.assertIn("F5", said[0])
        self.assertIn("cage.aisle", said[0], "the note has to name the fix")

    def test_an_aisle_that_holds_it_is_not_reported(self):
        cold = float(BoundaryAisleTest().hall()["aisles"]["cold"])
        self.assertEqual(self.notes(aisle=2 * cold, clearance=cold), [])

    def test_only_the_starved_row_is_named_when_the_wall_is_off_centre(self):
        cold = float(BoundaryAisleTest().hall()["aisles"]["cold"])
        said = self.notes(aisle=3 * cold, clearance=6.9)
        self.assertTrue(said)
        self.assertIn("F5 breathes through", said[0])
        self.assertNotIn("F4 breathes through", said[0])


class ConstructionTest(unittest.TestCase):
    """Mesh is a resistance, drywall is a wall, and neither is both."""

    def built(self, how: str, **kw):
        return m.build_model(pod(construction=how, clearance=0.6, **kw))

    def test_mesh_carries_the_component_s_loss_coefficient(self):
        built = self.built("mesh")
        want = components.load("cage-mesh-13").k
        for wall in [p for p in built.panels if p.name.startswith("cage_")]:
            with self.subTest(wall=wall.name):
                self.assertEqual(wall.kind, "opening")
                self.assertAlmostEqual(wall.resistance, want, places=9)

    def test_drywall_is_a_wall_with_no_resistance(self):
        for wall in [p for p in self.built("drywall", height=2.6).panels
                     if p.name.startswith("cage_")]:
            with self.subTest(wall=wall.name):
                self.assertEqual(wall.kind, "wall")
                self.assertIsNone(wall.resistance)

    def test_a_case_may_state_its_own_loss_coefficient(self):
        spec = pod(construction="mesh", clearance=0.6)
        spec["cage"]["loss_coefficient"] = 4.2
        for wall in [p for p in m.build_model(spec).panels
                     if p.name.startswith("cage_")]:
            self.assertEqual(wall.resistance, 4.2)

    def test_no_cage_face_is_ever_claimed_by_both_a_wall_zone_and_a_porous_one(self):
        """The fault that breaks `createBaffles`, under one name prefix."""
        for how, kw in (("mesh", {}), ("drywall", {"height": 2.6})):
            built = self.built(how, **kw)
            zoned = {p.name for _n, ps, _h in case_module.wall_plan(built)
                     for p in ps if p.name.startswith("cage_")}
            porous = {p.name for p in case_module.porous(built)
                      if p.name.startswith("cage_")}
            with self.subTest(construction=how):
                self.assertFalse(zoned & porous,
                                 f"{sorted(zoned & porous)} is in a wall zone "
                                 "and a porous one at once")
                self.assertTrue(zoned or porous, "the cage is in no zone at all")

    def test_each_drywall_wall_is_its_own_zone(self):
        """A zone shares a normal and a cage has two, so one zone for all four
        fails an assertion deep in the generator rather than saying so."""
        built = self.built("drywall", height=2.6)
        zones = [(n, ps) for n, ps, _h in case_module.wall_plan(built)
                 if n.startswith("cage_")]
        self.assertEqual(len(zones), 4)
        for name, panels in zones:
            with self.subTest(zone=name):
                self.assertEqual(len({p.axis for p in panels}), 1)

    def test_the_whole_case_generates_both_ways(self):
        """`topo_set_dict` is where a zone with two normals asserts."""
        for how, kw in (("mesh", {}), ("drywall", {"height": 2.6})):
            with self.subTest(construction=how):
                built = self.built(how, **kw)
                text = case_module.topo_set_dict(built)
                self.assertIn("cage_near", text)
                case_module.create_baffles_dict(built)


class SealedDrywallTest(unittest.TestCase):
    """Drywall closed at the top is a room with an exit and no entry."""

    def test_drywall_to_the_ceiling_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="drywall", clearance=0.6))
        self.assertIn("no way in", str(caught.exception))

    def test_drywall_with_a_roof_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="drywall", clearance=0.6,
                              height=2.6, roof=True))
        self.assertIn("no way in", str(caught.exception))

    def test_the_refusal_names_both_ways_out(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="drywall", clearance=0.6))
        said = str(caught.exception)
        self.assertIn("cage.height", said)
        self.assertIn("mesh", said)

    def test_drywall_open_above_builds(self):
        built = m.build_model(pod(construction="drywall", clearance=0.6, height=2.6))
        walls = [p for p in built.panels if p.name.startswith("cage_")]
        self.assertEqual(len(walls), 4)
        for wall in walls:
            self.assertLess(wall.extent[1][1], built.ceiling_z)

    def test_a_mesh_cage_closed_at_the_top_is_fine(self):
        """It is not sealed: the air crosses it."""
        m.build_model(pod(construction="mesh", clearance=0.6, roof=True))


class LeakCheckTest(unittest.TestCase):
    """`sealed_envelope` has to tell the two constructions apart.

    They share a name prefix, and `createBaffles` names a porous pair
    `_below`/`_above` and a wall pair `_master`/`_slave`. Excusing the prefix
    would excuse a drywall leak, which is precisely what the check is for.
    """

    def test_a_mesh_cage_is_meant_to_pass_air(self):
        from aicfd.post import _passes_flow

        for patch in ("cage_near_below", "cage_left_above"):
            with self.subTest(patch=patch):
                self.assertTrue(_passes_flow(patch))

    def test_a_drywall_cage_is_not(self):
        from aicfd.post import _passes_flow

        for patch in ("cage_near_master", "cage_left_slave"):
            with self.subTest(patch=patch):
                self.assertFalse(
                    _passes_flow(patch),
                    "flow through a drywall cage is the leak this check exists "
                    "to find",
                )

    def test_the_surfaces_that_always_passed_flow_still_do(self):
        from aicfd.post import _passes_flow

        for patch in ("grille1_below", "plenum_opening_above", "tile_R1_1_below",
                      "supply1_below", "floor_opening_above"):
            with self.subTest(patch=patch):
                self.assertTrue(_passes_flow(patch))


class LibraryTest(unittest.TestCase):
    def test_the_role_exists_and_the_library_can_fill_it(self):
        self.assertIn("cage", components.ROLES)
        self.assertTrue(components.for_role("cage"),
                        "nothing in components/ can be a cage")

    def test_the_house_default_is_one_of_them(self):
        default = m.DEFAULT_COMPONENTS["cage"]
        self.assertIn(default, components.for_role("cage"))

    def test_a_case_may_name_another(self):
        spec = pod(construction="mesh", clearance=0.6)
        spec.setdefault("components", {})["cage"] = components.for_role("cage")[0]
        m.build_model(spec)


if __name__ == "__main__":
    unittest.main()
