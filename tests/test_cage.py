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

    def test_a_cage_on_the_hall_wall_is_refused_with_a_clearance_that_fits(self):
        spec = yaml.safe_load(
            (support.REPO / "cases" / "pod-fanwall.yaml").read_text())
        hall = spec["hall"]["size"]
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="mesh", clearance=hall[1]))
        said = str(caught.exception)
        self.assertIn("cage.clearance", said)
        self.assertIn("hall wall", said)
        self.assertRegex(said, r"under [\d.,]+ m",
                         "the refusal has to say what would fit")
        self.assertRegex(said, r"stand [\d.,]+ m from one wall",
                         "the rows are rarely centred, so the refusal says "
                         "both gaps rather than half the leftover")

    def test_the_clearance_it_suggests_actually_builds(self):
        spec = yaml.safe_load(
            (support.REPO / "cases" / "pod-fanwall.yaml").read_text())
        with self.assertRaises(ValueError) as caught:
            m.build_model(pod(construction="mesh", clearance=spec["hall"]["size"][1]))
        import re

        fits = float(re.search(r"under ([\d.]+) m", str(caught.exception)).group(1))
        m.build_model(pod(construction="mesh", clearance=round(fits - 0.05, 2)))


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
