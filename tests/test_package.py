"""A drawn hall as one file: `<project>.aicfd.zip` in and out (ADR-135).

The package is built by the test from the reader's own downflow hall, so
nothing here reads `cases/`.
"""
from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

from aicfd import cases as case_store
from aicfd import model as M
from aicfd import package
from tests.test_geometry import _box, downflow_hall

SCENARIO = """# the converter's comment, which has to arrive
name: drawn-base
geometry: {file: geometry.stl, source: 'a drawing'}
racks: {load_kw: 5.0, airflow_cfm_per_kw: 158}
fanwall: {airflow_m3h: 20000, capacity_kw: 100, supply_temp_c: 20.0, static_pressure_pa: 100}
components: {ceiling_return: ceiling-return-600-open, gallery_mesh: gallery-mesh-13,
  floor_tile: floor-tile-600, cage: cage-mesh-13}
cage: {construction: mesh}
mesh: {cell_size: [0.2, 0.2, 0.2]}
figures:
- {file: figures/plan.png, caption: 'the plan'}
"""

STL = downflow_hall() + _box("unit:UE-02", (1.6, 5.4, 1.0), (2.4, 7.4, 3.0)) + "\n"


def build_zip(members: dict[str, bytes | str], manifest: dict | None = None) -> bytes:
    stl = members.get("geometry.stl", STL)
    stl = stl.encode() if isinstance(stl, str) else stl
    manifest = manifest if manifest is not None else {
        "format": package.FORMAT, "project": "drawn",
        "geometry": {"file": "geometry.stl", "sha256": hashlib.sha256(stl).hexdigest()},
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, blob in members.items():
            archive.writestr(name, blob)
    return buffer.getvalue()


def good_members() -> dict:
    return {"geometry.stl": STL, "scenarios/drawn-base.yaml": SCENARIO,
            "figures/plan.png": b"\x89PNG fake", "source/answers.yaml": "loads: {}\n"}


class PackageTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name) / "cases"


class ImportTest(PackageTest):
    def test_it_lands_as_a_project_whose_scenarios_build(self):
        done = package.import_package(build_zip(good_members()), self.root)
        self.assertEqual(done["project"], "drawn")
        folder = self.root / "drawn"
        for part in ("geometry.stl", "figures/plan.png", "source/answers.yaml",
                     "source/manifest.json", "drawn-base.yaml"):
            self.assertTrue((folder / part).is_file(), part)
        self.assertEqual(len(M.build_model(case_store.load("drawn-base", self.root)).racks), 4)

    def test_the_scenario_keeps_its_comments_and_is_stamped_with_the_geometry(self):
        package.import_package(build_zip(good_members()), self.root)
        text = (self.root / "drawn" / "drawn-base.yaml").read_text()
        self.assertIn("the converter's comment", text)
        self.assertEqual(yaml.safe_load(text)["geometry"]["sha256"],
                         hashlib.sha256(STL.encode()).hexdigest())

    def test_a_package_that_does_not_build_leaves_nothing_behind(self):
        members = good_members()
        members["scenarios/drawn-base.yaml"] = SCENARIO.replace("cell_size: [0.2, 0.2, 0.2]",
                                                               "cell_size: [0.3, 0.3, 0.3]")
        with self.assertRaises(package.PackageError) as caught:
            package.import_package(build_zip(members), self.root)
        self.assertIn("cannot be built", str(caught.exception))
        self.assertEqual([p.name for p in self.root.iterdir()], [])

    def test_a_geometry_changed_after_the_manifest_is_refused(self):
        members = good_members()
        manifest = {"format": package.FORMAT, "project": "drawn",
                    "geometry": {"file": "geometry.stl", "sha256": "0" * 64}}
        with self.assertRaises(package.PackageError) as caught:
            package.import_package(build_zip(members, manifest), self.root)
        self.assertIn("sha256", str(caught.exception))

    def test_a_member_outside_the_format_is_refused_by_name(self):
        members = good_members()
        members["../escape.txt"] = "x"
        with self.assertRaises(package.PackageError) as caught:
            package.import_package(build_zip(members), self.root)
        self.assertIn("escape.txt", str(caught.exception))

    def test_an_existing_project_is_not_overwritten(self):
        package.import_package(build_zip(good_members()), self.root)
        with self.assertRaises(package.PackageError):
            package.import_package(build_zip(good_members()), self.root)
        moved = good_members()
        moved["scenarios/drawn-other.yaml"] = moved.pop("scenarios/drawn-base.yaml")
        done = package.import_package(build_zip(moved), self.root, project="drawn-2")
        self.assertEqual(done["scenarios"], ["drawn-other"])

    def test_a_scenario_name_taken_elsewhere_is_refused(self):
        package.import_package(build_zip(good_members()), self.root)
        with self.assertRaises(package.PackageError) as caught:
            package.import_package(build_zip(good_members()), self.root, project="drawn-2")
        self.assertIn("unique", str(caught.exception))


class LockTest(PackageTest):
    def test_a_replaced_stl_is_refused_by_the_scenario(self):
        package.import_package(build_zip(good_members()), self.root)
        stl = self.root / "drawn" / "geometry.stl"
        stl.write_text(STL + _box("rack:Z01:+y", (8.0, 2.0, 1.0), (8.6, 3.2, 3.2)) + "\n")
        with self.assertRaises(ValueError) as caught:
            M.build_model(case_store.load("drawn-base", self.root))
        self.assertIn("not the geometry this scenario was imported with", str(caught.exception))


class ExportTest(PackageTest):
    def test_a_scenario_goes_out_as_the_package_it_came_in_as(self):
        package.import_package(build_zip(good_members()), self.root)
        filename, data = package.export_package("drawn-base", self.root)
        self.assertEqual(filename, "drawn-base.aicfd.zip")
        names = set(zipfile.ZipFile(io.BytesIO(data)).namelist())
        self.assertEqual(names, {"manifest.json", "geometry.stl", "figures/plan.png",
                                 "scenarios/drawn-base.yaml", "source/answers.yaml"})
        other = Path(tempfile.mkdtemp()) / "cases"
        done = package.import_package(data, other, project="copy")
        self.assertEqual(done["scenarios"], ["drawn-base"])

    def test_a_parametric_case_is_not_a_package(self):
        self.root.mkdir(parents=True)
        (self.root / "pod.yaml").write_text("name: pod\n")
        with self.assertRaises(package.PackageError):
            package.export_package("pod", self.root)


if __name__ == "__main__":
    unittest.main()
