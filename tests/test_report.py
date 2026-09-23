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
from tests import support

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
        for heading in ("1  Introduction", "2  Summary", "3  Methodology",
                        "4  Results", "5  Conclusions", "6  Limitations"):
            self.assertIn(heading, self.text)

    def test_the_method_is_stated_before_any_number_that_rests_on_it(self):
        """The template's fixed opening. A reader who disagrees with a
        conclusion has to be able to see what was solved, with what, and under
        what assumptions, without being sent to a manual."""
        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        for phrase in ("OpenFOAM v1912", "buoyantSimpleFoam", "SIMPLE algorithm",
                       "k-epsilon", "counterflow heat exchanger",
                       "The supply air temperature is an output",
                       "accepted on physical grounds"):
            self.assertIn(phrase, whole)

    def test_the_introduction_walks_the_engineer_through_the_input(self):
        """The method has to say how a unit gets into the software: one
        selection, checked, turned into a coil, and a curve to review."""
        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        for phrase in ("A unit is described by one manufacturer selection",
                       "the selection is entered", "the selection is checked",
                       "the coil is built", "the curve is reviewed",
                       "replaces it with the manufacturer's own capacity curve"):
            self.assertIn(phrase, whole)

    def test_the_document_states_things_rather_than_contrasting_them(self):
        """A technical report asserts. These constructions set up a contrast
        with something the reader never proposed, and they read as a defence
        of the method rather than a statement of it."""
        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        for phrase in ("rather than", "would have", "and nothing about",
                       "which is not", "instead of", "worse than"):
            self.assertNotIn(phrase, whole, f"rhetorical: {phrase!r}")

    def test_the_body_sections_are_written_in_the_affirmative(self):
        """Sections 2 to 5 carry the study. What it does not cover is section
        6's subject, so the negations belong there."""
        body = self.text[self.text.index("2  Summary"):
                         self.text.index("6  Limitations")]
        for phrase in ("does not", "do not", "is not on", "What it does not"):
            self.assertNotIn(phrase, body, f"negated: {phrase!r}")

    def test_the_introduction_says_what_the_method_does(self):
        """Declarative. What the study does not cover is section 6's subject,
        and a method stated by negation reads as a defence."""
        import re

        introduction = self.text[self.text.index("1  Introduction"):
                                 self.text.index("2  Summary")]
        for phrase in ("is not", "does not", "cannot", "rather than an",
                       "never a", "not a transient"):
            self.assertNotIn(phrase, introduction, f"negated: {phrase!r}")

    def test_the_introduction_comes_before_the_summary(self):
        self.assertLess(self.text.index("1  Introduction"),
                        self.text.index("2  Summary"))

    def test_every_cross_reference_points_at_a_section_that_exists(self):
        """Renumbering a template is where stale references breed."""
        import re

        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        cited = set(re.findall(r"section (\d)(?:\.\d)?", whole))
        self.assertTrue(cited, "the document should cite its own sections")
        headings = set(re.findall(r"^(\d)  \w", self.text, re.MULTILINE))
        self.assertEqual(headings, {"1", "2", "3", "4", "5", "6"})
        self.assertLessEqual(cited, headings, "a reference to a section that "
                                              "does not exist")

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
        """A report circulated without its limits is over-read."""
        limits = self.text[self.text.index("6  Limitations"):]
        self.assertIn("Containment is modelled as perfect", limits)
        self.assertIn("PDU", limits)

    def test_a_limitation_is_asked_of_the_code_not_of_a_memory(self):
        """The list said "One load per rack. Unloaded positions and a real
        per-rack load map are not represented" for as long as ADR-054 had been
        shipping the load map, ADR-074 the blanking panel and ADR-054 the
        zero-load cabinet -- so the report told an engineer their layout was
        not represented while the solver was using it (ADR-089).

        These two are gone for good: one because it is false, one because
        "this is a simulation" is not a finding of this study."""
        limits = self.text[self.text.index("6  Limitations"):]
        for phrase in ("One load per rack", "No comparison against measurement"):
            self.assertNotIn(phrase, limits)

    def test_the_containment_limit_is_there_because_the_library_says_so(self):
        """Not because somebody wrote it down once: it is printed while the
        panel's leakage is unread by the solver, and would go when it is."""
        from aicfd import components
        limits = self.text[self.text.index("6  Limitations"):]
        self.assertEqual(
            "Containment is modelled as perfect" in limits,
            not components.load("containment-panel").applied,
        )

    def test_the_mesh_limit_follows_the_mesh(self):
        """A fine mesh must not be called a conceptual-design mesh."""
        import json
        payload = json.loads((RESULT / "viewer.json").read_text())
        cell = payload["model"]["cell_size"]
        rack = payload["model"]["racks"][0]
        plan = min((rack["hi"][a] - rack["lo"][a]) / cell[a] for a in (0, 1))
        limits = self.text[self.text.index("6  Limitations"):]
        self.assertEqual("conceptual-design mesh" in limits, plan < 4)

    def test_the_limits_leave_out_what_is_true_of_every_cfd_study(self):
        """Numerical uncertainty and a single modelled scenario are properties
        of the method, not findings of this study."""
        limits = self.text[self.text.index("6  Limitations"):]
        for phrase in ("all carry uncertainty", "Steady state only",
                       "One operating scenario", "chilled water plant nobody",
                       "branch balancing"):
            self.assertNotIn(phrase, limits)

    def test_it_says_whether_the_result_may_be_quoted(self):
        import json

        payload = json.loads((RESULT / "viewer.json").read_text())
        if payload["valid"]:
            self.assertIn("All physical checks pass", self.text)
        else:
            self.assertIn("CHECKS FAILED", self.text)

    def test_it_never_sends_the_reader_to_another_tool(self):
        """A report is read on its own. Anything it cites has to be in it."""
        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        for phrase in ("CSV", "results page", "download"):
            self.assertNotIn(phrase, whole)

    def test_the_unit_is_one_selection(self):
        """A study runs on the selection the engineer was issued, and the coil
        built from it. A set of further selections was how the method was
        arrived at, not how it is used, and a report that carries them invites
        a reader to go and find six more (ADR-042)."""
        whole = self.text + "\n" + "\n".join(
            c.text for tb in self.tables for r in tb.rows for c in r.cells
        )
        for phrase in ("selections", "further manufacturer", "whole set of"):
            self.assertNotIn(phrase, whole)
        self.assertIn("design selection", whole)

    def test_every_rack_is_in_the_annex_where_there_are_many(self):
        """Section 4.4 ranks the hall and shows the twelve that decide whether
        it passes; a reader asking what one rack breathes needs the rest, and
        a table that lives in the document travels with it."""
        import json

        payload = json.loads((RESULT / "viewer.json").read_text())
        zones = payload["kpis"]["zones"]
        if len(zones) <= 12:
            self.assertNotIn("Annex A", self.text)
            return
        self.assertIn("Annex A", self.text)
        annex = next(t for t in self.tables
                     if t.rows[0].cells[0].text == "Rack"
                     and len(t.rows) == len(zones) + 1)
        listed = {r.cells[0].text for r in annex.rows[1:]}
        self.assertEqual(listed, {z["name"] for z in zones})

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
        spec = support.spec("hall-double-gallery")
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

    def test_the_unit_is_given_with_the_conditions_behind_it(self):
        self.assertIn("The cooling unit", self.text)
        for condition in ("Entering chilled water", "Leaving chilled water",
                          "Selected at external static pressure"):
            self.assertIn(condition, self.cells)

    def test_the_design_selection_is_printed(self):
        """The one duty the plant was bought on. It is what the coil is built
        from, so every capacity in section 4 rests on these five numbers and
        a reader has to be able to check them (ADR-042)."""
        from aicfd import equipment

        design = equipment.load("CA80NPVG6").design
        self.assertIn("The design selection", self.text)
        self.assertIn(f"{design['nscc_kw']:,.1f} kW", self.cells)
        self.assertIn(f"{design['airflow_m3h']:,.0f} m³/h", self.cells)
        self.assertIn(f"{design['return_c']:,.1f} °C", self.cells)

    def test_the_reference_selections_stay_out_of_the_document(self):
        """The unit's file still holds them and the fit still used them, but a
        study runs on one selection. Printing seven invites a reader to go and
        find six more for the next machine."""
        from aicfd import equipment

        unit = equipment.load("CA80NPVG6")
        printed = [row for row in unit.capacity
                   if f"{row['nscc_kw']:,.1f} kW" in self.cells
                   and row["return_c"] != unit.design.get("return_c")]
        self.assertEqual(printed, [])

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
        limits = self.text[self.text.index("6  Limitations"):]
        self.assertNotIn("catalogue", limits)
        # The one thing the section DOES say about water is the plant this
        # study cannot see -- the chiller, the pumps -- which is the same half
        # the DX report already declared for its condenser (ADR-124).
        self.assertIn("CHILLED-WATER PLANT enters through those two numbers", limits)
        self.assertNotIn("rated water", limits)

    def test_the_coil_model_is_described_where_its_numbers_are_used(self):
        """The method belongs in the document. Every margin in section 4 rests
        on it, and a reader who disagrees has to be able to see what was
        assumed rather than take it on trust."""
        self.assertIn("characterises the counterflow coil", self.text)
        self.assertIn("Resistance on the air side", self.cells)
        self.assertIn("Design return air", self.cells)
        self.assertIn("Entering chilled water", self.cells)
        # and the physics itself is stated once, in the introduction
        self.assertIn("counterflow heat exchanger", self.cells)

    def test_capacity_is_reported_at_this_hall_not_at_the_selection(self):
        """The whole point: the units did not run at any selection, and the
        capacity quoted for them is the coil's at the air they received."""
        import json

        payload = json.loads((self.export / "viewer.json").read_text())
        kpis = payload["kpis"]
        self.assertTrue(kpis.get("coil_model"))
        self.assertNotAlmostEqual(kpis["available_kw"], kpis["catalogue_kw"], places=0)


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class WaterSideReportTest(unittest.TestCase):
    """A coil past its selection transfers more than the catalogue figure --
    that is the heat exchanger -- and the report says what the water would
    have to do for it (ADR-063)."""

    def test_it_says_what_the_water_would_have_to_do(self):
        from aicfd import report

        source = Path(report.__file__).read_text()
        self.assertIn("water side was sized for", source)
        self.assertIn("coil_water_out_c", source)
        self.assertNotIn("coil_outside_table_c", source)


class FixedIntroductionTest(unittest.TestCase):
    """Section 1 is the software's method, printed identically every time.

    It describes how AICFD models a data hall, so a reader meets the method
    before meeting any result that rests on it. A number from the study
    leaking into it would make the method look like a finding.
    """

    def _rendered(self):
        import docx

        from aicfd.report import _introduction

        doc = docx.Document()
        _introduction(doc)
        text = "\n".join(p.text for p in doc.paragraphs)
        cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        return text + "\n" + cells

    def test_it_takes_no_result_at_all(self):
        """The signature is the guarantee: with nothing to read from, nothing
        from a study can reach it."""
        import inspect

        from aicfd.report import _introduction

        self.assertEqual(list(inspect.signature(_introduction).parameters), ["doc"])

    def test_it_renders_the_same_text_every_time(self):
        first, second = self._rendered(), self._rendered()
        self.assertEqual(first, second)
        self.assertIn("1  Introduction", first)
        self.assertIn("1.6  Acceptance", first)

    def test_the_report_prints_exactly_that_text(self):
        """What the document carries is what the fixed section says, with no
        case-specific sentence spliced in."""
        import tempfile
        from pathlib import Path

        import docx

        from aicfd.report import build

        if not (RESULT / "viewer.json").exists():
            self.skipTest("no exported result")
        with tempfile.TemporaryDirectory() as tmp:
            path = build(RESULT, Path(tmp) / "r.docx")
            document = docx.Document(str(path))
            text = "\n".join(p.text for p in document.paragraphs)
            section = text[text.index("1  Introduction"):text.index("2  Summary")]
        for line in self._rendered().splitlines():
            if line.strip() and not line.startswith("1  Introduction"):
                self.assertIn(line.strip(), section + "\n" + "\n".join(
                    c.text for t in document.tables for r in t.rows for c in r.cells
                ))


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class EveryResultProducesAReportTest(unittest.TestCase):
    """The report generator is emitted for whatever the engineer already has.

    A result is written once and read for years. An export from an earlier
    version carries an earlier payload, and a report that raises on a field it
    does not find cannot be produced for a study somebody is holding -- which
    is the one thing a report generator must never do. Two stored halls did
    exactly that: their coil payload predates `design_return_c` and the
    section indexed it rather than asking for it (ADR-089).

    This walks every tracked result there is. It is the consistency check the
    deliverable needs: whatever combination of inputs a case used -- a raised
    floor, a supply plenum, a mesh leaf, networked units, a typical row with
    blanks -- the document comes out.
    """

    REPO = Path(__file__).resolve().parents[1]

    def results(self):
        for folder in ("reference", "results"):
            for path in sorted((self.REPO / folder).glob("*/viewer.json")):
                yield path.parent

    def test_every_tracked_result_builds_a_document(self):
        from aicfd.report import build

        found = list(self.results())
        self.assertTrue(found, "no tracked results to check")
        with tempfile.TemporaryDirectory() as tmp:
            for result in found:
                with self.subTest(result=result.name):
                    out = build(result, Path(tmp) / f"{result.name}.docx")
                    self.assertTrue(out.exists())
                    self.assertGreater(out.stat().st_size, 20_000)


class NamingTest(unittest.TestCase):
    """The machines are called what they are, everywhere (ADR-102)."""

    def test_the_model_decides_the_word_from_the_machine(self):
        from aicfd import equipment as library
        from aicfd.model import unit_naming

        for name, expected in (("P3100DA", "CRAC"),      # downflow, dx
                               ("HDCV5300F-HT", "CRAH"),  # downflow, water
                               ("CA80NPVG6", "fan wall")):
            with self.subTest(unit=name):
                self.assertEqual(unit_naming(library.load(name))["noun"], expected)

    def test_a_case_that_names_no_unit_is_still_named(self):
        from aicfd.model import unit_naming

        self.assertEqual(unit_naming(None)["noun"], "fan wall")
        self.assertEqual(unit_naming(None, 0.9)["noun"], "room unit")

    def test_the_payload_carries_it_so_nothing_has_to_guess(self):
        import copy

        import yaml

        from aicfd.model import build_model, to_dict

        spec = support.spec("hall-cage")
        built = build_model(copy.deepcopy(spec))
        payload = to_dict(built, spec)
        self.assertEqual(payload["unit_naming"]["noun"], "CRAC")

    def test_an_acronym_keeps_its_capitals_in_a_label(self):
        """`str.capitalize` turns CRAC into Crac, which is a different
        machine's name as far as a reader is concerned."""
        from aicfd.model import as_a_label

        self.assertEqual(as_a_label("CRAC"), "CRAC")
        self.assertEqual(as_a_label("CRAH"), "CRAH")
        self.assertEqual(as_a_label("fan wall"), "Fan wall")

    def test_the_summary_names_them_too(self):
        """The one line an engineer reads before every run."""
        import copy

        import yaml

        from aicfd import case as case_module
        from aicfd.model import build_model

        for name, word in (("hall-cage", "CRACs"),
                           ("pod-raised-floor", "CRAH"),
                           ("pod-fanwall", "Fan wall")):
            spec = support.spec(name)
            built = build_model(copy.deepcopy(spec))
            with self.subTest(case=name):
                self.assertIn(word, case_module.summary(built))

    def test_a_mesh_warning_names_the_machine_too(self):
        """`CRAC width: 2,553 m falls between grid lines` -- the warning is
        read beside a drawing that names the same thing (ADR-102)."""
        import copy

        import yaml

        from aicfd.model import build_model

        built = build_model(copy.deepcopy(support.spec("hall-cage")))
        said = " ".join(built.warnings)
        self.assertIn("CRAC", said)
        self.assertNotIn("fan wall", said)


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class FiguresShowTheRoomTest(unittest.TestCase):
    """What the drawings must carry: the cage, the rows, the unit tags.

    THE CAGE WAS ON NO FIGURE AT ALL. It was in the model, meshed, solved and
    named in the summary, and every figure in the report drew the hall without
    it (ADR-102). These check the drawing calls rather than the pixels: what
    matters is that the panel reaches the canvas.
    """

    class Recorder:
        """An axes that remembers what was asked of it."""

        def __init__(self):
            self.plots, self.texts = [], []

        def plot(self, *args, **kwargs):
            self.plots.append((args, kwargs))

        def annotate(self, text, *args, **kwargs):
            self.texts.append(text)

        def text(self, _x, _y, text, *args, **kwargs):
            self.texts.append(text)

    def export_with_a_cage(self):
        from aicfd.figures import Export

        for name in ("bytedance-cage-crac", "hall-cage-1mw"):
            for base in ("results", "reference"):
                path = support.REPO / base / name
                if (path / "viewer.json").is_file():
                    export = Export(path)
                    if export.panels("cage_"):
                        return export
        self.skipTest("no solved result with a cage in this clone")

    def test_the_cage_reaches_the_drawing(self):
        from aicfd.figures import _cage

        export = self.export_with_a_cage()
        ax = self.Recorder()
        _cage(ax, export, 0, 1)
        self.assertEqual(len(ax.plots), len(export.panels("cage_")),
                         "a cage panel the model built is missing from the plan")

    def test_mesh_and_drywall_are_not_the_same_line(self):
        """They are the same rectangle and different rooms (ADR-096), so the
        drawing has to distinguish them."""
        from aicfd.figures import CAGE_STYLE

        self.assertNotEqual(CAGE_STYLE["mesh"], CAGE_STYLE["drywall"])

    def test_every_row_is_named_on_the_plan(self):
        from aicfd.figures import Export, _row_labels

        export = Export(RESULT)
        ax = self.Recorder()
        _row_labels(ax, export, 0, 1)
        self.assertEqual(len(ax.texts), len(export.rows))
        for row in export.rows:
            self.assertTrue(any(str(row["id"]) in t for t in ax.texts),
                            f"row {row['id']} is not named on the plan")

    def test_each_machine_carries_its_tag(self):
        from aicfd.figures import Export

        export = Export(RESULT)
        self.assertEqual(export.unit_tag(0), f"{export.naming['tag']}-01")
        self.assertEqual(export.unit_tag(9), f"{export.naming['tag']}-10")

    def test_the_detail_drawing_needs_a_window(self):
        """The three dimensioned drawings are gone and the window is no longer
        optional: `geometry(...)` without one used to produce them (ADR-102)."""
        import inspect

        from aicfd.figures import geometry

        zoom = inspect.signature(geometry).parameters["zoom"]
        self.assertIs(zoom.default, inspect.Parameter.empty)

    def test_the_report_draws_no_dimensioned_room(self):
        import inspect

        from aicfd import report

        source = inspect.getsource(report._draw)
        for key in ("geo_a", "geo_b", "geo_c"):
            self.assertNotIn(key, source)


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class CageInTheReportTest(unittest.TestCase):
    """A hall with a customer cage is TWO rooms, and the document says so.

    The cage was drawn on no figure and named in no table: a reader had the
    hall's totals and no way to see what was inside the fence and what was
    outside it (ADR-102).
    """

    class Stub:
        """Enough of an export for the section under test."""

        def __init__(self, cage=True):
            caged = [{"name": f"F1-{i:02d}", "load_w": 10_000, "in_cage": True}
                     for i in range(1, 11)]
            caged.append({"name": "F1-11", "load_w": 0, "in_cage": True})
            outside = [{"name": f"F5-{i:02d}", "load_w": 4_000, "in_cage": False}
                       for i in range(1, 21)]
            self.racks = (caged + outside) if cage else [
                dict(r, in_cage=False) for r in outside]
            self.model = {"cage": "mesh", "cage_k": 1.618} if cage else {}

    def rendered(self, stub) -> str:
        import docx

        from aicfd.report import _cage_split

        doc = docx.Document()
        _cage_split(doc, stub)
        text = "\n".join(p.text for p in doc.paragraphs)
        cells = "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
        return text + "\n" + cells

    def test_it_totals_the_two_rooms_separately(self):
        said = self.rendered(self.Stub())
        self.assertIn("Inside the cage", said)
        self.assertIn("Rest of the data hall", said)
        self.assertIn("100.0 kW", said)   # 10 cabinets of 10 kW
        self.assertIn("80.0 kW", said)    # 20 of 4 kW
        self.assertIn("11", said)         # positions inside, the ODF included

    def test_it_says_how_the_cage_is_built(self):
        said = self.rendered(self.Stub())
        self.assertIn("mesh", said)
        self.assertIn("1.62", said, "the loss coefficient the air pays")

    def test_a_hall_without_a_cage_says_nothing_at_all(self):
        self.assertEqual(self.rendered(self.Stub(cage=False)).strip(), "")

    def test_the_layout_section_calls_it(self):
        """The section exists and is wired in: removing the call left every
        test passing and the table out of the document."""
        import inspect

        from aicfd import report

        self.assertIn("_cage_split(doc, export)",
                      inspect.getsource(report._layout_section))


@unittest.skipUnless(HAVE_EXTRAS, "python-docx and matplotlib are not installed")
class CageInTheResultsTest(unittest.TestCase):
    """The two rooms are judged separately, because they are let separately."""

    def zones(self):
        return [
            {"name": "F1-01", "row": "F1", "inlet_top_c": 31.2,
             "ashrae": {"verdict": "above recommended"}},
            {"name": "F1-02", "row": "F1", "inlet_top_c": 24.0,
             "ashrae": {"verdict": "within recommended"}},
            {"name": "F6-01", "row": "F6", "inlet_top_c": 22.5,
             "ashrae": {"verdict": "within recommended"}},
        ]

    def rendered(self, caged):
        import docx

        from aicfd.report import _cage_verdict

        doc = docx.Document()
        _cage_verdict(doc, self.zones(), caged)
        return "\n".join(p.text for p in doc.paragraphs)

    def test_it_names_the_worst_on_each_side_of_the_fence(self):
        said = self.rendered({"F1-01", "F1-02"})
        self.assertIn("31.2", said)
        self.assertIn("F1-01", said)
        self.assertIn("22.5", said)
        self.assertIn("F6-01", said)
        self.assertIn("1 of the 2", said)

    def test_a_hall_with_no_cage_says_nothing(self):
        self.assertEqual(self.rendered(set()).strip(), "")

    def test_the_rack_table_says_which_room_each_cabinet_is_in(self):
        import inspect

        from aicfd import report

        source = inspect.getsource(report._results)
        self.assertIn('"Where"', source)
        self.assertIn("_cage_verdict(doc, zones, caged)", source)


@unittest.skipUnless(HAVE_EXTRAS, "matplotlib is not installed")
class ContainmentIsDrawnAsItProjectsTest(unittest.TestCase):
    """A panel drawn corner to corner is a diagonal wherever the view sees
    the whole rectangle (ADR-110).

    It showed up twice: the end doors of a contained aisle, in the transverse
    section, and the lid of a contained COLD aisle in plan. Both came out as a
    line across the aisle from one corner to the other -- which reads as a
    barrier standing where the room is open.
    """

    class _Ax:
        def __init__(self):
            self.lines, self.patches = [], []

        def plot(self, xs, ys, **kwargs):
            self.lines.append((tuple(xs), tuple(ys)))

        def add_patch(self, patch):
            self.patches.append(patch)

    def mark(self, lo, hi, h, v):
        from aicfd.figures import _containment_mark

        ax = self._Ax()
        _containment_mark(ax, {"lo": lo, "hi": hi}, h, v, linewidth=0.9)
        return ax

    def test_a_panel_seen_edge_on_is_a_line(self):
        # A chimney wall (y-normal) in a transverse section: h=1, v=2.
        ax = self.mark([7.6, 12.8, 1.0], [18.0, 12.8, 3.2], 1, 2)
        self.assertEqual(ax.patches, [])
        self.assertEqual(ax.lines, [((12.8, 12.8), (1.0, 3.2))])

    def test_a_panel_seen_face_on_is_the_rectangle_it_is(self):
        # The same section looking at an end door (x-normal): the whole
        # rectangle is on the page, so a line would cross the aisle.
        ax = self.mark([7.6, 11.6, 1.0], [7.6, 12.8, 3.2], 1, 2)
        self.assertEqual(ax.lines, [])
        self.assertEqual(len(ax.patches), 1)
        patch = ax.patches[0]
        self.assertAlmostEqual(patch.get_x(), 11.6)
        self.assertAlmostEqual(patch.get_y(), 1.0)
        self.assertAlmostEqual(patch.get_width(), 1.2)
        self.assertAlmostEqual(patch.get_height(), 2.2)

    def test_a_cold_aisle_lid_is_a_rectangle_in_plan_and_a_line_in_section(self):
        lo, hi = [7.6, 11.6, 3.2], [18.0, 12.8, 3.2]
        plan = self.mark(lo, hi, 0, 1)
        self.assertEqual(len(plan.patches), 1, "the lid came out as a diagonal")
        section = self.mark(lo, hi, 1, 2)
        self.assertEqual(section.patches, [])
        self.assertEqual(section.lines, [((11.6, 12.8), (3.2, 3.2))])

    def test_the_figures_draw_the_side_a_contained_aisle_closes_with(self):
        """The panel that keeps the containment off the cage wall is on the
        drawing, or the report shows an aisle open to somebody else's room."""
        import inspect

        from aicfd import figures

        for function in (figures.plan, figures.section, figures.geometry):
            with self.subTest(figure=function.__name__):
                self.assertIn("containment_side", inspect.getsource(function))


class TheMachineIsDrawnAsTheMachineTest(unittest.TestCase):
    """Where a cooling unit's body is, in every view (ADR-111).

    A fan wall stands BEHIND its face, back into the gallery. A downflow unit
    stands ON the deck: its footprint is the panel and its height is what
    reaches up to the return face. Extruding the depth along the panel's own
    normal, which is what the page and the report both did, drew a 0,87 m tall
    CRAC in a room that holds a 1,97 m machine.
    """

    def box(self, panel, depth=None):
        from aicfd.figures import fan_body_box

        return fan_body_box(panel, depth)

    def downflow(self, **kw):
        return {"name": "fan1", "kind": "fan", "axis": 2, "position": 1.0,
                "sign": -1, "lo": [3.6, 0.4, 1.0], "hi": [4.4, 3.0, 1.0],
                **kw}

    def test_a_downflow_unit_stands_on_the_deck_up_to_its_return_face(self):
        lo, hi = self.box(self.downflow(return_z=2.97), depth=0.873)
        self.assertEqual((lo[0], hi[0]), (3.6, 4.4), "the footprint moved")
        self.assertEqual((lo[1], hi[1]), (0.4, 3.0), "the footprint moved")
        self.assertAlmostEqual(lo[2], 1.0, msg="the machine is off the deck")
        self.assertAlmostEqual(hi[2], 2.97, msg="the machine is not its height")

    def test_a_downflow_unit_is_never_extruded_by_its_depth(self):
        """The depth is the footprint, and it is already in the panel."""
        lo, hi = self.box(self.downflow(return_z=2.97), depth=0.873)
        self.assertGreater(hi[2] - lo[2], 1.0,
                           "the machine came out one depth tall")

    def test_an_export_without_the_return_face_draws_no_body(self):
        """An export written before `return_z` existed: poorer, never wrong."""
        self.assertIsNone(self.box(self.downflow()))
        self.assertIsNone(self.box(self.downflow(return_z=None), depth=0.873))

    def test_a_fan_wall_stands_behind_its_face(self):
        wall = {"name": "fan1", "kind": "fan", "axis": 0, "position": 4.4,
                "sign": 1, "lo": [4.4, 1.0, 0.0], "hi": [4.4, 4.4, 4.0]}
        lo, hi = self.box(wall, depth=1.5)
        self.assertAlmostEqual(lo[0], 2.9, msg="not set back into the gallery")
        self.assertAlmostEqual(hi[0], 4.4)
        self.assertEqual((lo[1], hi[1]), (1.0, 4.4), "the width moved")
        self.assertIsNone(self.box(wall, depth=None),
                          "a depth nothing states is a machine nothing knows")

    def test_every_drawing_asks_the_same_question(self):
        """Three drawings got this wrong three different ways. One rule now."""
        import inspect

        from aicfd import figures

        for function in (figures.plan, figures.section, figures.geometry):
            with self.subTest(figure=function.__name__):
                self.assertIn("fan_body_box", inspect.getsource(function))


class TheReportDescribesThePlantItHasTest(unittest.TestCase):
    """Section 1.4 is the fixed method, and the method has two coil models
    since ADR-103. It described the chilled-water one to a reader looking at
    fourteen direct-expansion CRACs (ADR-112)."""

    def source(self, function) -> str:
        import inspect

        from aicfd import report

        return inspect.getsource(getattr(report, function))

    def test_the_method_names_both_coils(self):
        said = self.source("_introduction")
        self.assertIn("CHILLED-WATER unit the cold side is the entering water",
                      said)
        self.assertIn("DIRECT-EXPANSION unit", said)
        self.assertIn("apparatus dew point", said)
        self.assertIn("counterflow heat exchanger", said,
                      "the chilled-water physics is still stated once")

    def test_the_introduction_states_the_rule_and_promises_no_verdict(self):
        """Section 1 is the method, printed identically every run, so it
        cannot say how this run came out.

        It said "all of them pass before a temperature in this document is
        quoted" on the front of a report whose cover said CHECKS FAILED, and
        counted "eleven" identities above a table of thirteen. The rule stays
        here; the verdict belongs where the numbers are (ADR-123).
        """
        import inspect

        from aicfd import report

        self.assertEqual(
            list(inspect.signature(report._introduction).parameters), ["doc"])
        # The RENDERED text, not the source: the source carries the comment
        # that explains what these sentences used to say.
        import docx

        doc = docx.Document()
        report._introduction(doc)
        printed = "\n".join(p.text for p in doc.paragraphs)
        self.assertIn("accepted only when every check that applies passes",
                      printed)
        self.assertNotIn("all of them pass before a temperature", printed)
        for counted in ("Eleven identities", "and seven more"):
            self.assertNotIn(counted, printed,
                             "a fixed section cannot count a variable list")

    def test_the_control_note_moves_what_that_plant_actually_moves(self):
        said = self.source("_control_section")
        self.assertIn('"dx"', said)
        self.assertIn("compressors", said)
        self.assertIn("the water side", said)


class TheConclusionsAgreeWithTheChecksTest(unittest.TestCase):
    """Section 5 restates section 4.1 in words. When the two disagree the
    reader has no way to tell which to believe (ADR-115)."""

    def source(self) -> str:
        import inspect

        from aicfd import report

        return inspect.getsource(report._conclusions)

    def test_the_fan_sentence_takes_the_cabinets_out_too(self):
        said = self.source()
        self.assertIn("rack_drop_pa", said,
                      "the conclusion still charges the unit for the cabinets")
        self.assertIn("room outside the cabinets costs", said)
        self.assertIn("cabinets' own fans carry", said)

    def test_the_energy_sentence_does_not_say_matches_when_it_does_not(self):
        said = self.source()
        self.assertIn("abs(closure - 100) <= 2", said)
        self.assertIn("still settling", said)


class TheReportDoesNotContradictItselfTest(unittest.TestCase):
    """Four places in this document quoted the same quantity two ways.

    A reader cannot tell which of two numbers to believe, and an engineer
    circulating a report that disagrees with itself has to defend both. Each
    of these is one measurement with one definition (ADR-115, ADR-123).
    """

    def source(self, function: str) -> str:
        import inspect

        from aicfd import report

        return inspect.getsource(getattr(report, function))

    def test_the_headline_pressure_is_the_one_the_check_took(self):
        """Section 2 charged the unit for the whole loop -- 113,6 Pa of the
        50 Pa it offers, 227 % -- above a section 5 that said 54 %."""
        said = self.source("_summary")
        self.assertIn("room_static_pa", said)
        self.assertNotIn("'fan_rise_pa'), 1)} Pa\",\n         f\"of the", said)

    def test_the_room_static_is_defined_once(self):
        """One subtraction, in the export, read by everyone who quotes it."""
        import inspect

        from aicfd import post

        source = inspect.getsource(post)
        self.assertIn('kpis["room_static_pa"]', source)
        for function in ("_summary", "_conclusions"):
            self.assertIn("room_static_pa", self.source(function))

    def test_the_two_supply_temperatures_are_labelled(self):
        """Section 2 printed the SOLVED supply under a heading that says
        manufacturer selection, so it read 23,2 degC where section 3's
        selection table read 18,8 -- of the same machine."""
        said = self.source("_summary")
        self.assertIn("Supply air temperature, at the selection point", said)
        self.assertIn("Supply air temperature delivered in this run", said)

    def test_the_two_elevations_are_labelled(self):
        said = self.source("_summary")
        self.assertIn("Site elevation, this hall", said)
        self.assertIn("the selection was taken at", said)

    def test_the_pressure_column_is_not_called_rise(self):
        """In pascals, beside four temperature columns."""
        said = self.source("_results")
        self.assertIn('headers.append("Loop static")', said)

    def test_every_check_the_solver_runs_has_a_meaning_in_the_table(self):
        """A FAIL with an empty 'What it catches' cell tells a reader
        nothing."""
        import re

        from pathlib import Path

        from aicfd.report import _CHECK_MEANING

        source = (Path(__file__).resolve().parents[1]
                  / "aicfd" / "post.py").read_text()
        real = set(re.findall(r'Check\(\s*\n\s*"([a-z_]+)"', source))
        self.assertTrue(real)
        self.assertFalse(real - set(_CHECK_MEANING),
                         "a check with no row in the report's meaning table")


class TheSnapListIsReadableTest(unittest.TestCase):
    """A 1 MW hall produced 33 bullets of which 10 were distinct (ADR-123)."""

    def test_identical_snaps_collapse_to_one_line(self):
        from aicfd.report import _grouped_warnings

        lines = _grouped_warnings([
            "rack height: 3.200 m falls between 0.25 m grid lines; the mesh uses 3.25 m (+50 mm).",
            "row end edge: 3.200 m falls between 0.25 m grid lines; the mesh uses 3.25 m (+50 mm).",
            "CRAC width: 2.553 m falls between 0.30 m grid lines; the mesh uses 2.70 m (+147 mm).",
        ])
        self.assertEqual(len(lines), 2)
        self.assertIn("rack height and row end edge: 3.200 m", lines[0])
        self.assertIn("CRAC width: 2.553 m", lines[1])

    def test_the_same_finding_about_four_aisles_is_one_line(self):
        from aicfd.report import _grouped_warnings

        note = ("floor plates: rows {a} and {b} face the same 1.20 m cold "
                "aisle, which holds 2 row(s) of plate.")
        lines = _grouped_warnings([
            note.format(a="F2", b="F3"), note.format(a="F6", b="F7"),
            note.format(a="F8", b="F9"), note.format(a="F10", b="F11"),
        ])
        self.assertEqual(len(lines), 1)
        self.assertIn("floor plates: rows F2/F3, rows F6/F7, rows F8/F9 and "
                      "rows F10/F11 face the same", lines[0])

    def test_nothing_is_dropped_or_reworded(self):
        from aicfd.report import _grouped_warnings

        one = "domain height: 6.600 m falls between 0.25 m grid lines."
        self.assertEqual(_grouped_warnings([one]), [one])

    def test_a_line_with_no_subject_survives(self):
        from aicfd.report import _grouped_warnings

        plain = "the cage wall at 12.00 m stands in a contained cold aisle."
        self.assertEqual(_grouped_warnings([plain]), [plain])


class TeamControlIsDescribedForTheMachineItRunsOnTest(unittest.TestCase):
    """`team` holds one fixed SETPOINT on a DX plant and shares the VALVE
    POSITION on a chilled-water one (ADR-064, ADR-125). The report described
    a third law, of neither."""

    def render(self, kind: str, control: str = "team") -> str:
        import docx

        from aicfd import report

        class Export:
            payload = {"model": {"fans": [{}, {}], "fan_control": control}}
            kpis = {"coil_model": {"kind": kind}}
            naming = {"noun": "CRAC" if kind == "dx" else "CRAH"}

        doc = docx.Document()
        report._control_section(doc, Export())
        return "\n".join(p.text for p in doc.paragraphs)

    def test_a_dx_team_holds_one_fixed_setpoint(self):
        said = self.render("dx")
        self.assertIn("NETWORKED on one supply air setpoint", said)
        self.assertIn("give the same steady answer", said)
        self.assertNotIn("valve", said)

    def test_a_chilled_water_team_shares_the_valve(self):
        said = self.render("chilled_water")
        self.assertIn("same valve position", said)
        self.assertIn("each at its own supply temperature", said)
        self.assertNotIn("deliver the same supply temperature", said)

    def test_independent_is_the_same_for_both(self):
        """The first paragraph, that is: the second names what the control
        moves, which IS different machinery (ADR-111)."""
        first = lambda text: text.split("\n")[1]
        self.assertEqual(first(self.render("dx", "independent")),
                         first(self.render("chilled_water", "independent")))
        self.assertIn("INDEPENDENTLY", first(self.render("dx", "independent")))


class TheWaterPlantIsAStatedLimitTest(unittest.TestCase):
    """The DX report says its condenser is not modelled; the chilled-water
    one said nothing about its chiller, which is the same half of the same
    machine."""

    def test_the_limitations_name_the_chiller(self):
        from aicfd import report

        class Export:
            payload = {"model": {"floor_height": 1.0},
                       "kpis": {"unit_model": "HXCV5000F-HT",
                                "coil_model": {"kind": "chilled_water",
                                               "water_c": 20.0,
                                               "water_max_m3h": 17.2}}}

        said = " ".join(report._model_limits(Export()))
        self.assertIn("CHILLED-WATER PLANT enters through those two numbers", said)
        self.assertIn("20.0 °C", said)
        self.assertIn("17.2 m³/h", said)

    def test_the_coil_table_says_what_the_water_is_held_at(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._coil_section)
        self.assertIn("Entering water the capacity is held at", said)
        self.assertIn("Water flow at full valve", said)


class TheSameReportReadsRightOnAnyMachineTest(unittest.TestCase):
    """Reading the CRAH twin of a DX hall found wording that was only true of
    the DX one, or of a hot-aisle hall, or of a program (ADR-124)."""

    def test_the_title_keeps_acronyms_and_units(self):
        from aicfd.report import title_of

        self.assertEqual(title_of("hall-cage-1mw-crah"), "Hall Cage 1 MW CRAH")
        self.assertEqual(title_of("hall-cage-1mw-fanwall"), "Hall Cage 1 MW Fan Wall")
        self.assertEqual(title_of("hall-10mw"), "Hall 10 MW")
        self.assertEqual(title_of("hall-double-gallery"), "Hall Double Gallery")

    def test_the_geometry_table_says_which_aisle_is_contained(self):
        from aicfd.report import _aisle_rows

        class Cold:
            def panels(self, prefix):
                return [{"name": "containment_lid_c1"}] if prefix == "containment_lid" else []

        class Hot:
            def panels(self, prefix):
                return [{"name": "containment_wall_1"}] if prefix == "containment_wall" else []

        model = {"hot_aisles": [1] * 6, "cold_aisles": [1] * 7}
        self.assertEqual(dict(_aisle_rows(Cold(), model, [1])),
                         {"Hot aisles": "6", "Contained cold aisles": "7 in plan"})
        self.assertEqual(dict(_aisle_rows(Hot(), model, [1])),
                         {"Contained hot aisles": "6 in plan", "Cold aisles": "7"})

    def test_the_captions_follow_the_contained_aisle(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._results)
        self.assertIn("which = _containment_of(export)", said)
        self.assertIn("over the lids of the contained cold aisles", said)
        self.assertIn("at the mouth of the contained hot aisles", said)

    def test_cabinet_widths_are_stated_as_specified_and_as_meshed(self):
        from aicfd.report import _widths_as_specified

        model = {"spec": {"racks": {"size": [0.8, 1.2, 2.2]}}}
        self.assertEqual(_widths_as_specified(model, [0.9]),
                         "0.8 m as specified, meshed as 0.9 m")
        self.assertEqual(_widths_as_specified(model, [0.8]), "0.8 m")
        self.assertEqual(_widths_as_specified({}, [0.6, 0.8]), "0.6 m, 0.8 m")

    def test_the_airflow_table_multiplies_out(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._methodology)
        self.assertIn('("3 — total delivered to the room", f"{_num(per_unit * units, 0)} m³/h")', said)

    def test_a_full_speed_curve_is_labelled_as_one(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._summary)
        self.assertIn("fans at full speed", said)


class TheFanWallReportDescribesTheRoomItHasTest(unittest.TestCase):
    """Reading the fan-wall twin of a raised-floor hall (ADR-124)."""

    def test_the_surfaces_table_lists_only_what_this_room_builds(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._surfaces_section)
        self.assertIn('"floor_tile": bool(model.get("floor_height"))', said)
        self.assertIn('"supply_grille": bool(model.get("plenum_depth_m"))', said)
        self.assertIn('"cage": bool(model.get("cage"))', said)

    def test_the_ceiling_grilles_face_velocity_is_the_aisle_exit_station(self):
        from aicfd.report import _face_velocity

        class Export:
            kpis = {"stations": [{"name": "aisle_exit", "label": "Aisle exit",
                                  "speed_ms": 0.989}]}

        self.assertEqual(_face_velocity(Export(), "ceiling_return"), "0.99 m/s")
        self.assertEqual(_face_velocity(Export(), "gallery_mesh"), "—")

    def test_the_export_carries_the_plate_and_grille_velocities(self):
        import inspect

        from aicfd import post

        source = inspect.getsource(post)
        self.assertIn('"floor_face_velocity_ms": k.get("floor_face_velocity_ms")', source)
        self.assertIn('"supply_face_velocity_ms": k.get("supply_face_velocity_ms")', source)

    def test_an_operated_airflow_is_not_called_the_datasheets(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._methodology)
        self.assertIn("the unit's airflow as operated", said)
        self.assertIn("by volume of the", said)
        self.assertIn("% by mass: this hall's air is", said)

    def test_the_mesh_prose_does_not_claim_planes_landed_that_were_moved(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._methodology)
        self.assertNotIn("tops land on cell faces", said)
        self.assertIn("is put on a cell", said)

    def test_a_hall_with_no_floor_gets_its_own_air_path_paragraph(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._methodology)
        self.assertIn("blow through the", said)
        self.assertIn("the room itself is the cold", said)


class StarvedCabinetsAreAFindingTest(unittest.TestCase):
    """A 20 kW cabinet between 4,7 kW neighbours rose 35 K and the report
    drew no conclusion from it (ADR-013, ADR-124)."""

    def render(self, zones):
        import docx

        from aicfd import report

        class Export:
            kpis = {"zones": zones, "fans": [], "energy_closure": 1.0,
                    "hvac": {"cfm_per_kw": 158}}
            model = {"operating": {"design_delta_t_k": 10.0}, "fans": []}
            payload = {"valid": True}

        doc = docx.Document()
        report._conclusions(doc, Export())
        return "\n".join(p.text for p in doc.paragraphs)

    def zone(self, name, inlet, peak, load=10000):
        return {"name": name, "inlet_temp_c": inlet, "inlet_top_c": inlet,
                "peak_temp_c": peak, "load_w": load}

    def test_a_cabinet_rising_more_than_twice_the_design_dt_is_named(self):
        said = self.render([self.zone("F2-01", 24.0, 59.0),
                            self.zone("F2-02", 24.0, 35.0)])
        self.assertIn("1 cabinet rises by more than twice the 10.0 K", said)
        self.assertIn("the worst is F2-01, at 35.0 K", said)
        self.assertIn("has no fan in this model", said)

    def test_a_hall_whose_cabinets_all_rise_as_designed_says_nothing(self):
        said = self.render([self.zone("F2-01", 24.0, 35.0)])
        self.assertNotIn("rises by more than", said)

    def test_an_empty_cabinet_cannot_be_starved(self):
        said = self.render([self.zone("F2-10", 24.0, 60.0, load=0)])
        self.assertNotIn("rises by more than", said)


class PassBoundariesAreOnTheConvergenceFigureTest(unittest.TestCase):
    """A restart renormalises the initial residual for one iteration: a spike
    to 1 on p_rgh that an engineer read as a blow-up (ADR-127)."""

    def test_the_boundaries_are_every_pass_end_but_the_last(self):
        from aicfd.figures import _pass_boundaries

        class Export:
            kpis = {"coupling": {"history": [
                {"iterations": 2000}, {"iterations": 2300}, {"iterations": 2600}]}}

        self.assertEqual(_pass_boundaries(Export()), [2000, 2300])

    def test_a_run_that_never_coupled_draws_none(self):
        from aicfd.figures import _pass_boundaries

        class Export:
            kpis = {"coupling": None}

        self.assertEqual(_pass_boundaries(Export()), [])

    def test_the_caption_explains_the_spike_only_when_there_were_passes(self):
        import inspect

        from aicfd import report

        said = inspect.getsource(report._results)
        self.assertIn("spike on p_rgh at a boundary is that restart", said)
        self.assertIn('.get("passes", 0) > 1', said)
