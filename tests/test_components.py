"""The component library: the surfaces the air passes through.

What these defend is that a standard stated once is used everywhere, that a
surface marked as the building cannot be edited into something else, and that
a save which would leave a file unreadable never reaches disk (ADR-048).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import components as C
from aicfd import model as m


class LibraryTest(unittest.TestCase):
    def test_every_file_in_the_library_parses(self):
        self.assertTrue(C.available(), "the library should not be empty")
        for name in C.available():
            with self.subTest(component=name):
                C.load(name)

    def test_every_component_fills_a_role_the_page_knows(self):
        for name in C.available():
            with self.subTest(component=name):
                self.assertIn(C.load(name).role, C.ROLES)

    def test_a_free_area_is_a_ratio(self):
        for bad in (0, -0.1, 1.5):
            with self.subTest(free_area=bad), self.assertRaises(ValueError):
                C.parse({"id": "x", "role": "ceiling_return", "free_area": bad})

    def test_the_loss_coefficient_follows_the_free_area(self):
        wide = C.parse({"id": "a", "role": "ceiling_return", "free_area": 0.9})
        narrow = C.parse({"id": "b", "role": "ceiling_return", "free_area": 0.5})
        self.assertLess(wide.k, narrow.k)
        self.assertAlmostEqual(narrow.k, m.grille_loss_coefficient(0.5))

    def test_a_datasheet_k_beats_the_correlation(self):
        stated = C.parse({"id": "c", "role": "ceiling_return",
                          "free_area": 0.5, "loss_coefficient": 2.4})
        self.assertEqual(stated.k, 2.4)

    def test_an_open_aperture_costs_nothing(self):
        self.assertAlmostEqual(C.load("ceiling-return-600-open").k, 0.0, places=6)

    def test_the_house_standards_are_what_the_specification_says(self):
        """65 % for a ceiling return, 54 % for a floor plate, 5 % of leakage
        in a containment, 65 % for the mesh into the gallery."""
        self.assertAlmostEqual(C.load("ceiling-return-600").free_area, 0.65)
        self.assertAlmostEqual(C.load("floor-tile-600").free_area, 0.54)
        self.assertAlmostEqual(C.load("containment-panel").free_area, 0.05)
        self.assertAlmostEqual(C.load("gallery-mesh-13").free_area, 0.65)

    def test_every_default_a_case_falls_back_to_exists(self):
        for role, name in m.DEFAULT_COMPONENTS.items():
            with self.subTest(role=role):
                self.assertEqual(C.load(name).role, role)


class KindsTest(unittest.TestCase):
    """The library holds two kinds of number (ADR-049)."""

    def test_a_load_has_a_share_and_no_face(self):
        pdu = C.load("pdu-distribution-loss")
        self.assertEqual(pdu.kind, "load")
        self.assertAlmostEqual(pdu.share, 0.02)
        self.assertIsNone(pdu.free_area)
        self.assertIsNone(pdu.k, "a dissipation has no face for air to cross")

    def test_a_surface_has_a_face_and_no_share(self):
        for name in C.available():
            unit = C.load(name)
            if unit.kind != "surface":
                continue
            with self.subTest(component=name):
                self.assertIsNotNone(unit.free_area)
                self.assertIsNone(unit.share)

    def test_a_share_is_a_fraction_of_the_load(self):
        for bad in (-0.1, 1.0, 3):
            with self.subTest(share=bad), self.assertRaises(ValueError):
                C.parse({"id": "x", "role": "distribution_loss",
                         "kind": "load", "share": bad})

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            C.parse({"id": "x", "role": "containment", "kind": "guess",
                     "free_area": 0.5})

    def test_what_the_solver_does_not_read_yet_says_so(self):
        """A number held but not applied is worse than absent if the page does
        not say which it is: a run comes back unchanged and looks broken."""
        for name in ("containment-panel", "floor-tile-600", "pdu-distribution-loss"):
            with self.subTest(component=name):
                self.assertFalse(C.load(name).applied)
        for name in ("gallery-mesh-13", "ceiling-return-600"):
            with self.subTest(component=name):
                self.assertTrue(C.load(name).applied)

    def test_every_pattern_is_one_the_page_can_draw(self):
        js = (Path(__file__).resolve().parents[1] / "web" / "components.js").read_text()
        for name in C.available():
            pattern = C.load(name).pattern
            with self.subTest(component=name, pattern=pattern):
                self.assertIn(pattern, C.PATTERNS)
                if pattern != "none":
                    self.assertIn(f"'{pattern}'", js)

    def test_the_containment_leakage_is_editable(self):
        """It is a specification figure, not architecture, so it is offered."""
        self.assertFalse(C.load("containment-panel").fixed)


class SavingTest(unittest.TestCase):
    """Saving keeps the file a person can still read."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original = C.LIBRARY
        C.LIBRARY = Path(self.tmp.name)
        for path in self.original.glob("*.yaml"):
            shutil.copy(path, C.LIBRARY / path.name)

    def tearDown(self):
        C.LIBRARY = self.original
        self.tmp.cleanup()

    def test_a_save_that_changes_nothing_leaves_the_file_identical(self):
        """The comments say where each number came from, and the alignment is
        hand-made. A save that reflows either has lost the reasons."""
        for name in C.available():
            if C.load(name).fixed:
                continue
            with self.subTest(component=name):
                path = C.path_for(name)
                before = path.read_text()
                unit = C.load(name)
                C.save(name, {"free_area": unit.free_area, "note": unit.note,
                              "name": unit.name, "aperture": unit.aperture})
                self.assertEqual(path.read_text(), before)

    def test_a_changed_free_area_lands_and_the_file_still_parses(self):
        C.save("ceiling-return-600", {"free_area": 0.5})
        self.assertAlmostEqual(C.load("ceiling-return-600").free_area, 0.5)
        self.assertTrue(yaml.safe_load(C.path_for("ceiling-return-600").read_text()))

    def test_a_rewritten_note_does_not_orphan_its_own_lines(self):
        """A folded block carries its text on the indented lines beneath it.
        Replacing only the key line left those behind, and the keys under them
        were swallowed into the block: the file stopped parsing."""
        C.save("ceiling-return-600", {"note": "Short."})
        raw = yaml.safe_load(C.path_for("ceiling-return-600").read_text())
        self.assertEqual(raw["note"].strip(), "Short.")
        self.assertEqual(raw["id"], "ceiling-return-600")
        self.assertAlmostEqual(raw["free_area"], 0.65)

    def test_a_fixed_component_refuses_to_be_edited(self):
        with self.assertRaises(ValueError) as caught:
            C.save("gallery-mesh-13", {"free_area": 0.9})
        self.assertIn("fixed", str(caught.exception))
        self.assertAlmostEqual(C.load("gallery-mesh-13").free_area, 0.65)

    def test_a_save_that_would_break_the_file_is_not_written(self):
        path = C.path_for("ceiling-return-600")
        before = path.read_text()
        with self.assertRaises(ValueError):
            C.save("ceiling-return-600", {"free_area": 5})  # not a ratio
        self.assertEqual(path.read_text(), before)


class WiringTest(unittest.TestCase):
    """What the model does with them."""

    def spec(self) -> dict:
        return yaml.safe_load(
            (Path(__file__).resolve().parents[1] / "cases" / "pod-fanwall.yaml").read_text()
        )

    def test_the_gallery_opening_carries_the_mesh(self):
        model = m.build_model(self.spec())
        openings = [p for p in model.panels if p.name.startswith("plenum_opening")]
        self.assertTrue(openings)
        for panel in openings:
            with self.subTest(panel=panel.name):
                self.assertAlmostEqual(panel.resistance, C.load("gallery-mesh-13").k)

    def test_a_case_can_say_it_has_no_such_surface(self):
        spec = self.spec()
        spec["components"] = {"gallery_mesh": None}
        model = m.build_model(spec)
        for panel in model.panels:
            if panel.name.startswith("plenum_opening"):
                self.assertIsNone(panel.resistance)

    def test_the_mesh_costs_what_its_k_says_at_the_fan_flow(self):
        model = m.build_model(self.spec())
        openings = [p for p in model.panels if p.name.startswith("plenum_opening")]
        area = sum(p.area for p in openings)
        velocity = model.airflow_m3s / area
        self.assertAlmostEqual(
            model.mesh_pressure_drop_pa,
            openings[0].resistance * 0.5 * model.rho * velocity**2,
        )

    def test_a_ceiling_grille_falls_back_to_the_house_standard(self):
        spec = self.spec()
        spec["grilles"].pop("free_area", None)
        spec["grilles"].pop("loss_coefficient", None)
        self.assertAlmostEqual(m._grille_free_area(spec), 0.65)
        self.assertAlmostEqual(m._grille_k(spec), C.load("ceiling-return-600").k)

    def test_a_case_that_states_its_own_still_wins(self):
        spec = self.spec()
        spec["grilles"]["loss_coefficient"] = 2.4
        self.assertAlmostEqual(m._grille_k(spec), 2.4)


if __name__ == "__main__":
    unittest.main()


class NestedBlockTest(unittest.TestCase):
    """`set_map` writes a block a file does not have yet, and takes it away
    again when it is empty (ADR-054)."""

    TEXT = (
        "racks:\n"
        "  count: 3          # per row\n"
        "  load_kw: 6.0\n"
        "\n"
        "mesh:\n"
        "  cell_size: 0.1\n"
    )

    def edit(self, text, values):
        from aicfd import yamledit

        lines = text.split("\n")
        yamledit.set_map(lines, ["racks", "loads"], values)
        return "\n".join(lines)

    def test_the_block_lands_at_the_end_of_its_parent(self):
        out = self.edit(self.TEXT, {"R2": 0})
        self.assertIn("  load_kw: 6.0\n  loads:\n    R2: 0\n\nmesh:", out)
        self.assertIn("# per row", out, "a hand-written comment was rewritten")

    def test_removing_it_restores_the_file(self):
        with_block = self.edit(self.TEXT, {"R2": 0, "R7": 4.5})
        self.assertEqual(self.edit(with_block, {}), self.TEXT)

    def test_it_works_when_the_parent_is_the_last_block(self):
        text = "mesh:\n  cell_size: 0.1\nracks:\n  load_kw: 6.0\n"
        out = self.edit(text, {"R2": 0})
        self.assertTrue(out.endswith("  load_kw: 6.0\n  loads:\n    R2: 0\n"), out)
        self.assertEqual(self.edit(out, {}), text)

    def test_a_missing_parent_is_an_error_rather_than_a_new_block(self):
        from aicfd import yamledit

        with self.assertRaises(ValueError):
            lines = "mesh:\n  cell_size: 0.1\n".split("\n")
            yamledit.set_map(lines, ["racks", "loads"], {"R2": 0})
