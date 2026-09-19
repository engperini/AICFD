"""The Word deliverable, built end to end from a tracked result.

`results/pod-fanwall/` is committed, so this is a real run through the whole
figure and document path -- not a mock. What it defends is the one property
the deliverable has to have: **every number in it comes from the export**. A
report that quietly computed its own numbers, or fell back to a default when
one was missing, would be worse than no report at all.

Skipped where matplotlib or python-docx are absent: the tool's core -- model,
case, solve, checks -- must run without either.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

#: The worked POD's export, tracked under reference/ because it is evidence
#: rather than an artifact (ADR-032). A result solved locally lands in
#: results/ and never touches this one.
RESULT = Path(__file__).resolve().parents[1] / "reference" / "pod-fanwall"

try:  # the report's two extras
    import docx  # noqa: F401
    import matplotlib  # noqa: F401

    HAVE_EXTRAS = True
except ModuleNotFoundError:  # pragma: no cover - environment
    HAVE_EXTRAS = False


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
@unittest.skipUnless((RESULT / "viewer.json").exists(), "no exported result")
class ExportTest(unittest.TestCase):
    def setUp(self):
        from aicfd.figures import Export

        self.export = Export(RESULT)

    def test_the_fields_reshape_to_the_grid(self):
        nx, ny, nz = self.export.divisions
        for name in ("T", "Ux", "P", "speed"):
            self.assertEqual(self.export.fields[name].shape, (nz, ny, nx), name)

    def test_a_slice_is_the_plane_it_says_it_is(self):
        """A plan at rack mid-height has to cross the racks, not the air above
        them -- the axis order in fields.bin is the one thing a silent
        transpose would corrupt without raising anything."""
        export = self.export
        rack = export.racks[0]
        z = rack["hi"][2] / 2
        plan = export.slice("T", 2, z)
        self.assertEqual(plan.shape, (export.divisions[1], export.divisions[0]))
        # the hot aisle is warmer than the cold aisle on this plane
        model = export.model
        y_axis = export.axis(1)
        cold = model["cold_aisles"][0]
        hot = model["hot_aisles"][0]
        in_cold = (y_axis > cold[0]) & (y_axis < cold[1])
        in_hot = (y_axis > hot[0]) & (y_axis < hot[1])
        self.assertGreater(plan[in_hot].mean(), plan[in_cold].mean() + 2.0)


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
@unittest.skipUnless((RESULT / "viewer.json").exists(), "no exported result")
class DocumentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from aicfd.report import build

        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = build(RESULT, Path(cls.tmp.name) / "r.docx",
                         client="A Client", author="An Engineer")
        from docx import Document

        cls.doc = Document(str(cls.path))
        cls.text = "\n".join(p.text for p in cls.doc.paragraphs)
        cls.tables = cls.doc.tables

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_it_writes_a_document_with_the_sections_a_reviewer_reads(self):
        for heading in ("1  Summary", "2  Methodology", "3  Results",
                        "4  Conclusions", "5  Limitations"):
            self.assertIn(heading, self.text)

    def test_every_check_appears_with_its_verdict(self):
        import json

        payload = json.loads((RESULT / "viewer.json").read_text())
        names = {c["name"] for c in payload["checks"]}
        table = next(t for t in self.tables if t.rows[0].cells[0].text == "Check")
        listed = {row.cells[0].text for row in table.rows[1:]}
        self.assertEqual(listed, names)
        for row in table.rows[1:]:
            self.assertRegex(row.cells[1].text, r"^(PASS|FAIL)")

    def test_the_headline_numbers_are_the_export(self):
        """Not recomputed, not rounded from something else: the same numbers
        the page shows and `report.md` carries."""
        import json

        payload = json.loads((RESULT / "viewer.json").read_text())
        kpis = payload["kpis"]
        warmest = max(kpis["zones"], key=lambda z: z["inlet_top_c"])
        self.assertIn(f"{warmest['inlet_top_c']:,.2f} °C", self.text
                      + "\n".join(c.text for t in self.tables for r in t.rows
                                  for c in r.cells))
        self.assertIn(warmest["name"], self.text)

    def test_the_limits_are_in_the_document_not_only_in_the_readme(self):
        """A report circulated without its limits is a report that will be
        over-read. These are the four the tool cannot yet answer."""
        for phrase in ("failure case", "catalogue capacity",
                       "One load per rack", "conceptual-design mesh"):
            self.assertIn(phrase, self.text)

    def test_it_says_whether_the_result_may_be_quoted(self):
        import json

        payload = json.loads((RESULT / "viewer.json").read_text())
        if payload["valid"]:
            self.assertIn("All physical checks pass", self.text)
        else:
            self.assertIn("CHECKS FAILED", self.text)

    def test_the_figures_are_embedded_not_linked(self):
        shapes = self.doc.inline_shapes
        self.assertGreaterEqual(len(shapes), 6)


def _export_naming_a_unit(directory: Path, model_name: str, rows_below: bool,
                          water: bool = True):
    """A copy of the worked result, made to name a unit.

    The tracked exports predate the equipment library, and solving a hall to
    produce one would take the better part of an hour. So the fields, the
    geometry and the checks are the real run's; only the coil KPIs are
    computed here, by the same `coil_capacity` the exporter calls.

    `rows_below` extends the unit's table down to the return temperature this
    hall actually produces, which is the difference between the report saying
    what the plant's margin is and saying that it cannot.
    """
    import json
    import shutil

    import yaml

    from aicfd import equipment, post
    from aicfd.model import build_model

    source = Path(__file__).resolve().parents[1]
    shutil.copytree(source / "reference" / "hall-double-gallery", directory / "export")
    library = directory / "equipment"
    library.mkdir()
    text = (source / "equipment" / f"{model_name}.yaml").read_text()
    if rows_below:
        marker = "  - {return_c: 35,"
        text = text.replace(marker, (
            "  - {return_c: 32, nscc_kw: 385, airflow_m3h: 132800, "
            "power_kw: 26.5, supply_c: 22.2}\n"
            "  - {return_c: 33, nscc_kw: 424, airflow_m3h: 132730, "
            "power_kw: 26.4, supply_c: 22.1}\n"
            "  - {return_c: 34, nscc_kw: 464, airflow_m3h: 132660, "
            "power_kw: 26.3, supply_c: 22}\n" + marker), 1)
    if not water:
        # Strip the water conditions, so no coil can be fitted and the report
        # falls back to reading the table -- which is still what a unit
        # described by a capacity table alone gets (ADR-039).
        text = "\n".join(line for line in text.splitlines()
                          if "entering_water_c" not in line
                          and "leaving_water_c" not in line)
    (library / f"{model_name}.yaml").write_text(text)

    saved, equipment.LIBRARY = equipment.LIBRARY, library
    try:
        spec = yaml.safe_load(
            (source / "cases" / "hall-double-gallery.yaml").read_text())
        model = build_model(spec)
        payload = json.loads((directory / "export" / "viewer.json").read_text())
        kpis = payload["kpis"]
        kpis.update(post.coil_capacity(model, kpis["fans"], kpis))
        payload["model"]["spec"] = spec
        (directory / "export" / "viewer.json").write_text(json.dumps(payload))
        return directory / "export", library
    finally:
        equipment.LIBRARY = saved


class _UnitReport(unittest.TestCase):
    ROWS_BELOW = True
    WATER = True

    @classmethod
    def setUpClass(cls):
        from aicfd import equipment
        from aicfd.report import build

        cls.tmp = tempfile.TemporaryDirectory()
        export, library = _export_naming_a_unit(
            Path(cls.tmp.name), "CA80NPVG6", cls.ROWS_BELOW, cls.WATER)
        cls.saved, equipment.LIBRARY = equipment.LIBRARY, library
        cls.export = export
        path = build(export, Path(cls.tmp.name) / "r.docx",
                     client="A Client", author="An Engineer")
        from docx import Document

        cls.doc = Document(str(path))
        cls.figures = path.parent / "figures"
        cls.text = "\n".join(p.text for p in cls.doc.paragraphs)
        cls.cells = "\n".join(c.text for t in cls.doc.tables
                              for r in t.rows for c in r.cells)

    @classmethod
    def tearDownClass(cls):
        from aicfd import equipment

        equipment.LIBRARY = cls.saved
        cls.tmp.cleanup()


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class UnitReportTest(_UnitReport):
    """What the report says when it can read the unit's real capacity.

    The whole point of the equipment library: a plant judged against the one
    catalogue figure is judged against something it will not do (ADR-036).
    The document has to carry that distinction, not bury it.
    """

    def test_the_cover_names_the_machine(self):
        self.assertIn("Vertiv Liebert CWA CA80NPVG6", self.text)
        self.assertIn("14 × Vertiv Liebert CWA CA80NPVG6", self.text)

    def test_section_2_gives_the_selections_and_the_conditions_behind_them(self):
        self.assertIn("The cooling unit", self.text)
        for condition in ("Entering chilled water", "Leaving chilled water",
                          "Selected at external static pressure"):
            self.assertIn(condition, self.cells)

    def test_the_whole_capacity_table_is_printed(self):
        from aicfd import equipment

        unit = equipment.load("CA80NPVG6")
        for row in unit.capacity:
            self.assertIn(f"{row['nscc_kw']:,.1f} kW", self.cells)

    def test_the_capacity_figure_is_drawn(self):
        self.assertTrue((self.figures / "capacity.png").is_file())

    def test_unit_by_unit_gains_the_available_columns(self):
        table = next(t for t in self.doc.tables if t.rows[0].cells[0].text == "Unit")
        headers = [c.text for c in table.rows[0].cells]
        self.assertIn("Available", headers)
        self.assertIn("Of available", headers)
        self.assertIn("Of the rating", headers)

    def test_the_conclusions_use_available_capacity_not_the_catalogue(self):
        self.assertIn("the capacity the coils actually have", self.text)
        self.assertIn("kW available from", self.text)

    def test_the_catalogue_limitation_is_dropped_and_the_real_one_stated(self):
        """It would be false to keep saying capacity is the catalogue's when
        the report just modelled the true one. The honest limitations are the
        model's own: what it was fitted to, and what the valve can draw."""
        self.assertNotIn("catalogue capacity, not the capacity it actually has",
                         self.text)
        self.assertIn("fitted to the manufacturer's selections", self.text)
        self.assertIn("water valve wide open", self.text)
        self.assertIn("is a different machine", self.text)

    def test_the_coil_model_is_described_where_its_numbers_are_used(self):
        """The method belongs in the document. Every margin in section 4 rests
        on it, and a reader who disagrees has to be able to see what was
        assumed rather than take it on trust."""
        self.assertIn("counterflow chilled-water", self.text)
        self.assertIn("never on the temperatures", self.text)
        self.assertIn("Resistance on the air side", self.cells)
        self.assertIn("Error against those selections", self.cells)
        self.assertIn("recovered from the stated entering and leaving water",
                      self.text)

    def test_capacity_is_reported_at_this_hall_not_at_the_selection(self):
        """The whole point: the units did not run at any selection, and the
        capacity quoted for them is the coil's at the air they received."""
        import json

        payload = json.loads((self.export / "viewer.json").read_text())
        kpis = payload["kpis"]
        self.assertTrue(kpis.get("coil_model"))
        self.assertNotAlmostEqual(kpis["available_kw"], kpis["catalogue_kw"], places=0)


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class OutsideTheTableReportTest(_UnitReport):
    """And what it says when it cannot read a capacity at all.

    A unit whose selections do not say what water they were taken at supports
    no coil model, so the table is all there is -- and this hall returns air
    below the coldest row of it. The report must say which units, over what
    range, and that no margin can be stated, not quietly omit a column.
    """

    ROWS_BELOW = False
    WATER = False

    def test_it_says_what_it_could_not_read_and_why(self):
        self.assertIn("outside the selections this machine was characterised at",
                      self.text)
        self.assertIn("cannot state the plant's true margin", self.text)
        self.assertIn("refuses to extrapolate", self.text)

    def test_it_still_names_the_unit(self):
        self.assertIn("CA80NPVG6", self.text)

    def test_there_is_no_available_column_to_mislead_anyone(self):
        table = next(t for t in self.doc.tables if t.rows[0].cells[0].text == "Unit")
        self.assertNotIn("Of available", [c.text for c in table.rows[0].cells])

    def test_the_catalogue_limitation_stays(self):
        self.assertIn("catalogue capacity, not the capacity it actually has",
                      self.text)
