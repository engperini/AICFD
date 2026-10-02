"""A hall read from a drawing, on the page: a project folder whose geometry is
fixed and whose scenarios are edited (ADR-134).

The project is written by the test into a throwaway `cases/`: the downflow
hall the reader's own tests use, as `geometry.stl`, and a commented scenario
beside it.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import cases as case_store
from aicfd import model as M
from aicfd import server
from tests.test_geometry import _box, downflow_hall

SCENARIO = """# a scenario of the drawn hall -- this comment has to survive an Apply
name: drawn-base
geometry: {file: geometry.stl, source: 'a drawing'}
racks:
  load_kw: 5.0
  airflow_cfm_per_kw: 158
  loads: {A01: 10.0}
fanwall:
  airflow_m3h: 20000
  capacity_kw: 100
  supply_temp_c: 20.0   # the setpoint under study
  static_pressure_pa: 100
  out_of_service: []
components: {ceiling_return: ceiling-return-600-open, gallery_mesh: gallery-mesh-13,
  floor_tile: floor-tile-600, cage: cage-mesh-13}
cage: {construction: mesh}
mesh:
  cell_size: [0.2, 0.2, 0.2]
solver: {max_iterations: 400, processors: 1}
"""


class ProjectTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "cases"
        self.project = self.root / "drawn"
        self.project.mkdir(parents=True)
        # the reader's downflow hall, with a second unit so one can fail
        (self.project / "geometry.stl").write_text(
            downflow_hall() + _box("unit:UE-02", (1.6, 5.4, 1.0), (2.4, 7.4, 3.0)) + "\n")
        self.file = self.project / "drawn-base.yaml"
        self.file.write_text(SCENARIO)
        original = server.CASES_DIR
        server.CASES_DIR = self.root
        self.addCleanup(lambda: setattr(server, "CASES_DIR", original))


class TheFolderTest(ProjectTest):
    def test_a_scenario_is_found_by_its_name_inside_its_project(self):
        self.assertEqual(case_store.spec_path("drawn-base", self.root), self.file)
        self.assertIn(self.file, case_store.every(self.root))

    def test_the_source_folder_holds_no_scenarios(self):
        (self.project / "source").mkdir()
        (self.project / "source" / "answers.yaml").write_text("x: 1\n")
        self.assertNotIn("answers", [p.stem for p in case_store.every(self.root)])

    def test_two_scenarios_by_one_name_are_refused_by_name(self):
        other = self.root / "other"
        other.mkdir()
        (other / "drawn-base.yaml").write_text(SCENARIO)
        with self.assertRaises(ValueError) as caught:
            case_store.spec_path("drawn-base", self.root)
        self.assertIn("rename one", str(caught.exception))

    def test_the_geometry_beside_it_is_found_and_never_recorded(self):
        spec = case_store.load("drawn-base", self.root)
        model = M.build_model(spec)
        self.assertEqual(len(model.racks), 4)
        self.assertNotIn("_base", M.to_dict(model, spec)["spec"])

    def test_the_unit_picker_judges_the_room_the_drawing_is(self):
        """The sidecar says nothing about a floor; the drawing has one."""
        raw = yaml.safe_load(self.file.read_text())
        raw["_base"] = str(self.project)
        self.assertEqual(M.wanted_arrangement(raw), "downflow")

    def test_a_copy_of_a_scenario_lands_beside_it(self):
        server.new_case("drawn-19c", "drawn-base")
        self.assertTrue((self.project / "drawn-19c.yaml").is_file())
        listed = {r["case"]: r for r in server.list_cases()["cases"]}
        self.assertEqual(listed["drawn-19c"]["project"], "drawn")
        self.assertTrue(listed["drawn-19c"]["imported"])


class TheFormTest(ProjectTest):
    def test_the_page_is_told_it_is_an_imported_case(self):
        payload = server.build_payload("drawn-base")
        self.assertEqual(payload["mode"], "imported")
        self.assertIn("supply_temp_c", payload["editable"])
        for locked in ("hall_height", "fan_count", "floor", "containment", "rack_size"):
            self.assertNotIn(locked, payload["editable"])

    def test_the_page_is_shown_the_room_it_cannot_edit(self):
        g = server.build_payload("drawn-base")["imported"]
        self.assertEqual(g["arrangement"], "downflow")
        self.assertEqual(g["counts"]["cabinets"], 4)
        self.assertEqual([u["tag"] for u in g["units"]], ["UE-01", "UE-02"])
        self.assertIn(0.2, g["cells"]["x"])
        self.assertNotIn(0.3, g["cells"]["x"], "the drawing has 0,2 m steps in x")

    def test_what_this_drawing_has_none_of_is_not_offered(self):
        editable = server.build_payload("drawn-base")["editable"]
        self.assertNotIn("plenum_grille_width", editable, "no supply grilles in a raised-floor hall")
        self.assertIn("floor_tile", editable)
        self.assertIn("cage_construction", editable)

    def test_a_field_the_drawing_decides_is_refused_by_name(self):
        out = server.update_case("drawn-base", {"fan_count": 3})
        self.assertTrue(any("fan_count" in r and "drawing" in r for r in out["rejected"]))
        self.assertNotIn("count", yaml.safe_load(self.file.read_text())["fanwall"])

    def test_apply_saves_the_edit_and_nothing_the_drawing_decided(self):
        out = server.update_case("drawn-base", {"supply_temp_c": 19.0})
        self.assertEqual(out["rejected"], [])
        text = self.file.read_text()
        saved = yaml.safe_load(text)
        self.assertEqual(saved["fanwall"]["supply_temp_c"], 19.0)
        self.assertIn("this comment has to survive an Apply", text)
        self.assertIn("# the setpoint under study", text)
        for derived in ("floor", "containment", "hall", "_base"):
            self.assertNotIn(derived, saved)
        self.assertNotIn("count", saved["fanwall"])

    def test_a_failed_unit_is_named_and_cleared(self):
        server.update_case("drawn-base", {"fan_out_of_service": "UE-01"})
        self.assertEqual(yaml.safe_load(self.file.read_text())["fanwall"]["out_of_service"],
                         ["UE-01"])
        self.assertEqual(M.build_model(server.load_spec("drawn-base")).fans_off, ("fan1",))
        server.update_case("drawn-base", {"fan_out_of_service": ""})
        self.assertEqual(yaml.safe_load(self.file.read_text())["fanwall"]["out_of_service"], [])


class TheRacksPageTest(ProjectTest):
    def test_it_lists_the_drawings_cabinets_under_their_own_ids(self):
        out = server.read_racks("drawn-base")
        self.assertEqual(out["mode"], "imported")
        self.assertEqual({r["id"] for r in out["racks"]}, {"A01", "A02", "B01", "B02"})
        self.assertEqual(out["row"], [])
        a01 = next(r for r in out["racks"] if r["id"] == "A01")
        self.assertEqual(a01["load_kw"], 10.0)

    def test_a_cabinets_load_is_written_and_its_place_is_not(self):
        out = server.write_racks("drawn-base", {"loads": {"B02": 7.5}, "widths": {"B02": 0.8}})
        self.assertTrue(any("widths" in r for r in out["rejected"]))
        saved = yaml.safe_load(self.file.read_text())
        self.assertEqual(saved["racks"]["loads"], {"B02": 7.5})
        self.assertNotIn("widths", saved["racks"])
        self.assertIn("this comment has to survive an Apply", self.file.read_text())

    def test_an_id_the_drawing_does_not_have_is_refused(self):
        out = server.write_racks("drawn-base", {"loads": {"Z99": 3.0}})
        self.assertTrue(any("Z99" in r for r in out["rejected"]))


if __name__ == "__main__":
    unittest.main()
