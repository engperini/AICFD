"""Tests for the page's server side.

The form is a gate, not a text editor: it exists so a typo cannot put the spec
into a state the generator has never seen. These check the gate holds.
"""

from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from aicfd import server
from tests import support


class ApplyChangesTest(unittest.TestCase):
    def spec(self) -> dict:
        return {
            "fanwall": {"airflow_m3h": 5000, "supply_temp_c": 20.0, "height": 4.0},
            "racks": {"count": 3, "load_kw": 6.0},
            "aisles": {"cold": 1.8, "hot": 1.2},
            "mesh": {"cell_size": 0.1},
        }

    def test_a_good_change_lands_in_the_spec(self):
        spec, rejected = server.apply_changes(self.spec(), {"airflow_m3h": 7200})
        self.assertEqual(rejected, [])
        self.assertEqual(spec["fanwall"]["airflow_m3h"], 7200.0)

    def test_an_unknown_parameter_is_refused_not_written(self):
        spec, rejected = server.apply_changes(self.spec(), {"gravity": 3.7})
        self.assertNotIn("gravity", spec)
        self.assertTrue(any("not an editable parameter" in r for r in rejected))

    def test_out_of_range_is_refused_and_the_old_value_survives(self):
        spec, rejected = server.apply_changes(self.spec(), {"cell_size": 5.0})
        self.assertEqual(spec["mesh"]["cell_size"], 0.1)
        self.assertTrue(any("cell_size" in r for r in rejected))

    def test_the_wrong_type_is_refused(self):
        spec, rejected = server.apply_changes(self.spec(), {"airflow_m3h": "muito"})
        self.assertEqual(spec["fanwall"]["airflow_m3h"], 5000)
        self.assertTrue(any("float" in r for r in rejected))

    def test_a_cell_size_may_be_one_number_or_three(self):
        spec, rejected = server.apply_changes(self.spec(), {"cell_size": "0.2, 0.2, 0.1"})
        self.assertEqual(rejected, [])
        self.assertEqual(spec["mesh"]["cell_size"], [0.2, 0.2, 0.1])
        spec, rejected = server.apply_changes(self.spec(), {"cell_size": "0.15"})
        self.assertEqual(spec["mesh"]["cell_size"], 0.15)
        _spec, rejected = server.apply_changes(self.spec(), {"cell_size": "0.2, 0.1"})
        self.assertTrue(rejected)

    def test_counts_are_integers(self):
        spec, _ = server.apply_changes(self.spec(), {"rack_count": 7.9})
        self.assertEqual(spec["racks"]["count"], 7)

    def test_one_bad_change_does_not_block_the_good_ones(self):
        spec, rejected = server.apply_changes(
            self.spec(), {"airflow_m3h": 6000, "cell_size": 99.0}
        )
        self.assertEqual(spec["fanwall"]["airflow_m3h"], 6000.0)
        self.assertEqual(len(rejected), 1)

    def test_every_editable_parameter_names_a_real_spec_field(self):
        for key, (path, _caster, _limits) in server.EDITABLE.items():
            self.assertTrue(path and all(isinstance(step, str) for step in path), key)


class ProgressTest(unittest.TestCase):
    LOG = """
Time = 1

smoothSolver:  Solving for Ux, Initial residual = 1, Final residual = 0.05
smoothSolver:  Solving for h, Initial residual = 0.9, Final residual = 0.02
GAMG:  Solving for p_rgh, Initial residual = 0.5, Final residual = 0.009

Time = 2

smoothSolver:  Solving for Ux, Initial residual = 0.2, Final residual = 0.01
smoothSolver:  Solving for h, Initial residual = 0.1, Final residual = 0.004
GAMG:  Solving for p_rgh, Initial residual = 0.08, Final residual = 0.002
"""

    def write(self, text: str) -> str:
        import tempfile
        from pathlib import Path

        tmp = Path(tempfile.mkdtemp())
        (tmp / "case").mkdir()
        (tmp / "case" / "log.buoyantSimpleFoam").write_text(text)
        server.RUNS_DIR = tmp
        self.addCleanup(setattr, server, "RUNS_DIR", server.REPO_ROOT / "runs")
        return "case"

    def test_residuals_come_out_per_iteration(self):
        progress = server.read_progress(self.write(self.LOG))
        self.assertEqual(progress["iterations"], [1, 2])
        self.assertEqual(progress["series"]["Ux"], [1.0, 0.2])
        self.assertEqual(progress["series"]["p_rgh"], [0.5, 0.08])

    def test_only_the_first_residual_of_an_iteration_counts(self):
        """buoyantSimpleFoam solves p_rgh twice per step; the first is the news."""
        progress = server.read_progress(
            self.write(
                "Time = 1\n"
                "GAMG:  Solving for p_rgh, Initial residual = 0.5, Final residual = 1e-3\n"
                "GAMG:  Solving for p_rgh, Initial residual = 0.01, Final residual = 1e-4\n"
            )
        )
        self.assertEqual(progress["series"]["p_rgh"], [0.5])

    def test_a_missing_log_is_not_an_error(self):
        self.assertEqual(server.read_progress("nothing-here")["iterations"], [])

    def test_a_long_run_is_thinned_for_the_chart(self):
        text = "".join(
            f"Time = {i}\nsmoothSolver:  Solving for Ux, Initial residual = 0.1,"
            f" Final residual = 0.01\n\n"
            for i in range(1, 3001)
        )
        progress = server.read_progress(self.write(text), max_points=100)
        self.assertLessEqual(len(progress["iterations"]), 110)
        self.assertEqual(progress["total"], 3000)


class SolverAvailabilityTest(unittest.TestCase):
    def test_the_page_is_told_when_it_cannot_run(self):
        """A Run button that dies on a missing function explains nothing."""
        blocked = server.solver_available()
        self.assertTrue(blocked is None or isinstance(blocked, str))


if __name__ == "__main__":
    unittest.main()


class ResultsStateTest(unittest.TestCase):
    """What the page is told about the export it is offering a link to.

    Found by a user on their first run: the link opened a superseded result
    under the current case's name and said nothing about it. These are the
    four things the page has to be able to distinguish (ADR-030, ADR-032).
    """

    def setUp(self):
        import tempfile

        from aicfd import server

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.server = server
        self.saved = (server.RESULTS_DIR, server.REFERENCE_DIR, server.RUNS_DIR)
        server.RESULTS_DIR = root / "results"
        server.REFERENCE_DIR = root / "reference"
        server.RUNS_DIR = root / "runs"
        self.spec = {"name": "c", "racks": {"load_kw": 12.0, "count": 3}}

    def tearDown(self):
        (self.server.RESULTS_DIR, self.server.REFERENCE_DIR,
         self.server.RUNS_DIR) = self.saved
        self.tmp.cleanup()

    def _export(self, base, spec, name="c"):
        import json

        out = base / name
        out.mkdir(parents=True)
        (out / "viewer.json").write_text(json.dumps({"model": {"spec": spec}}))
        return out / "viewer.json"

    def test_nothing_exported(self):
        state = self.server.results_state("c", self.spec)
        self.assertFalse(state["exists"])

    def test_the_shipped_result_is_named_as_such(self):
        self._export(self.server.REFERENCE_DIR, self.spec)
        state = self.server.results_state("c", self.spec)
        self.assertTrue(state["exists"])
        self.assertTrue(state["matches"])
        self.assertIn("shipped with the repository", state["note"])

    def test_your_own_result_shadows_the_shipped_one(self):
        self._export(self.server.REFERENCE_DIR, {"name": "other"})
        self._export(self.server.RESULTS_DIR, self.spec)
        state = self.server.results_state("c", self.spec)
        self.assertTrue(state["matches"])
        self.assertEqual(state["note"], "")

    def test_different_inputs_are_named(self):
        import copy

        older = copy.deepcopy(self.spec)
        older["racks"]["load_kw"] = 8.0
        self._export(self.server.RESULTS_DIR, older)
        state = self.server.results_state("c", self.spec)
        self.assertFalse(state["matches"])
        self.assertIn("racks.load_kw", state["note"])

    def test_a_killed_run_leaves_a_solve_nobody_read(self):
        """Ctrl+C on the container writes fields and no export. Everything
        about the export is still right, including its spec -- only the
        clock says a solve happened after it."""
        import os
        import time

        export = self._export(self.server.RESULTS_DIR, self.spec)
        step = self.server.RUNS_DIR / "c" / "700"
        step.mkdir(parents=True)
        (step / "phi").write_text("")
        later = time.time() + 60
        os.utime(step / "phi", (later, later))
        state = self.server.results_state("c", self.spec)
        self.assertFalse(state["matches"])
        self.assertIn("iteration 700", state["note"])
        self.assertIn("aicfd post c", state["note"])


class CommandsTest(unittest.TestCase):
    """What the results page is allowed to offer.

    Asked before the buttons are drawn, so a button that cannot work is never
    shown. A button that appears and then explains why it failed teaches the
    reader to distrust the others.
    """

    def test_a_case_with_no_result_offers_nothing(self):
        from aicfd.server import commands_for

        can = commands_for("no-such-case-anywhere")
        self.assertFalse(can["report"])
        self.assertFalse(can["reread"])
        self.assertIn("not on this machine", can["reread_note"])

    def test_a_shipped_result_can_still_be_reported_on(self):
        from aicfd.server import REFERENCE_DIR, commands_for

        name = "pod-fanwall"
        if not (REFERENCE_DIR / name / "viewer.json").is_file():
            self.skipTest("the worked result is not in this clone")
        self.assertTrue(commands_for(name)["report"])


class ReportEndpointTest(unittest.TestCase):
    """The Word report, as the button asks for it."""

    def setUp(self):
        import tempfile
        from pathlib import Path

        from aicfd import server

        try:
            import docx  # noqa: F401
            import matplotlib  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("python-docx and matplotlib are not installed")
        if not (server.REFERENCE_DIR / "pod-fanwall" / "viewer.json").is_file():
            self.skipTest("the worked result is not in this clone")
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = server.REPORTS_DIR
        server.REPORTS_DIR = Path(self.tmp.name)

    def tearDown(self):
        from aicfd import server

        server.REPORTS_DIR = self.saved
        self.tmp.cleanup()

    def test_it_writes_a_document_and_never_touches_the_export(self):
        from aicfd import server

        before = sorted(p.name for p in
                        (server.REFERENCE_DIR / "pod-fanwall").iterdir())
        out = server.write_report("pod-fanwall", {"client": "A Client"})
        self.assertTrue(out.is_file())
        self.assertGreater(out.stat().st_size, 10_000)
        self.assertEqual(
            sorted(p.name for p in (server.REFERENCE_DIR / "pod-fanwall").iterdir()),
            before,
            "producing a document must not modify the result it was read from",
        )

    def test_a_case_with_no_result_says_so_rather_than_writing_nothing(self):
        from aicfd import server

        with self.assertRaises(FileNotFoundError):
            server.write_report("no-such-case-anywhere", {})


class RereadTest(unittest.TestCase):
    def test_a_case_that_was_never_solved_here_says_so(self):
        from aicfd.server import reread_run

        with self.assertRaises(Exception) as caught:
            reread_run("no-such-case-anywhere")
        self.assertNotIsInstance(caught.exception, AttributeError)


class ComposeMountsTest(unittest.TestCase):
    """Every directory the software writes to has to come from the clone.

    A missing mount does not fail: the write succeeds inside the container,
    the page confirms it, and the file is gone at the next `docker compose
    down`. The user reads that as the page not saving (ADR-034).
    """

    def mounts(self) -> dict:
        import yaml

        from aicfd.server import REPO_ROOT

        compose = yaml.safe_load((REPO_ROOT / "docker-compose.yml").read_text())
        bound = {}
        for line in compose["services"]["aicfd"]["volumes"]:
            host, _, rest = line.partition(":")
            container, _, options = rest.partition(":")
            bound[host.lstrip("./")] = options
        return bound

    def test_everything_written_at_run_time_comes_from_the_clone(self):
        bound = self.mounts()
        for directory in ("equipment", "components", "reports", "runs",
                          "results", "cases"):
            with self.subTest(directory=directory):
                self.assertIn(directory, bound,
                              f"{directory}/ is written by the software and must be mounted")
                self.assertNotIn("ro", bound[directory].split(","),
                                 f"{directory}/ is written to; a read-only mount fails the write")

    def test_the_worked_results_stay_read_only(self):
        self.assertIn("ro", self.mounts()["reference"].split(","))

    def test_every_library_the_software_writes_to_is_in_the_list_above(self):
        """The list is by name, so a library added later is covered only once
        its name is added too -- which is how `components/` went a commit
        without a mount. This finds the next one instead of waiting for it."""
        import re

        from aicfd import components, equipment

        libraries = {p.name for p in (equipment.LIBRARY, components.LIBRARY)}
        source = Path(__file__).read_text()
        listed = re.search(r'for directory in \(([^)]*)\)', source).group(1)
        for name in sorted(libraries):
            with self.subTest(library=name):
                self.assertIn(f'"{name}"', listed,
                              f"{name}/ is a library the pages write to")


class StaleAssetTest(unittest.TestCase):
    """The browser must not keep a stylesheet or a page across a pull.

    The clone is the application (ADR-034), so `git pull` has to take effect
    on the next reload. A browser holding the previous `drawing.css` while
    the server hands it the new HTML loses whichever rules moved between the
    two and breaks the page -- and that reads as the change being wrong
    rather than absent, which sends everyone looking in the wrong place.
    """

    def serve(self, path: str):
        """Headers this handler would send for `path`, without a socket."""
        import http.server
        import io

        class Probe(server.Handler):
            def __init__(self):  # no socket, no request parsing
                self.wfile = io.BytesIO()
                self.rfile = io.BytesIO()
                self.request_version = "HTTP/1.0"
                self.requestline = f"GET {path} HTTP/1.0"
                self.client_address = ("127.0.0.1", 0)
                self.command = "GET"
                self.path = path
                self.headers = http.server.BaseHTTPRequestHandler.MessageClass(
                    io.StringIO(""))
                self.directory = str(server.REPO_ROOT)

        probe = Probe()
        head = probe.send_head()
        if head:
            head.close()
        sent = probe.wfile.getvalue().decode("latin-1")
        return [line.split(":", 1)[1].strip()
                for line in sent.splitlines()
                if line.lower().startswith("cache-control:")]

    def test_a_static_file_is_revalidated_every_time(self):
        for path in ("/web/drawing.css", "/web/results.html", "/web/app.js"):
            with self.subTest(path=path):
                self.assertEqual(self.serve(path), ["no-cache"])

    def test_one_directive_only(self):
        """Two Cache-Control headers is an argument the browser resolves."""
        self.assertEqual(len(self.serve("/web/index.html")), 1)


class SharedDrawingStylesTest(unittest.TestCase):
    """The drawings' layout lives in one file, loaded by both pages.

    `drawing.css` exists so the model page and the results page cannot drift.
    A rule that only one of them carries is how they would.
    """

    def css(self) -> str:
        return (server.REPO_ROOT / "web" / "drawing.css").read_text()

    def test_both_pages_load_it(self):
        for page in ("index.html", "results.html"):
            with self.subTest(page=page):
                self.assertIn(
                    'href="./drawing.css"',
                    (server.REPO_ROOT / "web" / page).read_text(),
                    f"{page} draws with drawing.css and must load it",
                )

    def test_it_carries_the_layout_the_pages_stopped_carrying(self):
        css = self.css()
        for rule in (".view-head", ".map-head", ".view-zoom", ".map-zoom",
                     ".view-scroll", ".map-scroll", ".view-stage", ".map-stage"):
            with self.subTest(rule=rule):
                self.assertIn(rule, css)

    def test_a_magnified_drawing_scrolls_inside_its_own_card(self):
        css = self.css()
        self.assertIn("min-width:0", css,
                      "without it a magnified drawing widens its grid column")
        self.assertIn("overflow:auto", css)

    def test_a_narrow_drawing_is_centred_under_the_others(self):
        """The card centres the SCROLLER, which is full width.

        Without centring the stage inside it, a transverse section -- narrow
        because the room is -- sits against the left edge while the plan and
        the longitudinal section it is read against run the full width.
        """
        css = self.css()
        self.assertIn("margin-inline:auto", css)
        self.assertIn("width:max-content", css,
                      "auto margins centre a block only once it has its own width")

    def test_a_fitted_drawing_is_never_taller_than_its_scroller(self):
        """`fit` has to fit. A fixed cap could not: it held a hall's plan
        below what the page had room for and a POD's sections far below it,
        and raising it far enough for the POD would label a drawing `fit`
        that still had to be scrolled."""
        js = (server.REPO_ROOT / "web" / "drawing.js").read_text()
        self.assertIn("heightCap()", js)
        self.assertNotIn("view.height - PAD.top", js,
                         "sheetScale must clamp the view's height to the window")
        self.assertIn("window.innerHeight * 0.8", js,
                      "the scroller is capped at 80vh in drawing.css")


class ComponentChoiceTest(unittest.TestCase):
    """Which component fills a role is a per-case choice; what it is made of
    is not (ADR-051)."""

    def spec(self) -> dict:
        """Built here rather than read from `cases/`.

        These tests assert what a spec that has chosen nothing does, and a
        user who picks a grille on the page writes that choice into the case
        -- which is the page working, not a regression (ADR-056)."""
        return {
            "name": "choice-test",
            "hall": {"height": 7.5, "ceiling": 5.5},
            "gallery": {"depth": 9.0, "sides": 2},
            "racks": {"per_row": 22, "blocks": 2, "load_kw": 11.59,
                      "size": [0.6, 1.2, 2.2], "airflow_cfm_per_kw": 158.0},
            "fanwall": {"count": 14, "width": 3.96, "airflow_m3h": 104181.0},
            "grilles": {"size": 0.6, "coverage": 1.0},
        }

    def test_a_case_can_choose_another_ceiling_grille(self):
        spec, rejected = server.apply_changes(
            self.spec(), {"ceiling_return": "ceiling-return-600-perforated"})
        self.assertEqual(rejected, [])
        self.assertEqual(spec["components"]["ceiling_return"],
                         "ceiling-return-600-perforated")

    def test_a_name_the_library_does_not_have_is_refused(self):
        spec, rejected = server.apply_changes(self.spec(), {"ceiling_return": "nope"})
        self.assertEqual(len(rejected), 1)
        self.assertIn("nope", rejected[0])
        self.assertNotIn("components", spec)

    def test_a_component_of_the_wrong_role_is_refused(self):
        """A floor plate is not a ceiling grille, however valid its file."""
        _spec, rejected = server.apply_changes(
            self.spec(), {"ceiling_return": "floor-tile-600"})
        self.assertEqual(len(rejected), 1)

    def test_the_page_is_told_what_each_role_uses_and_what_else_there_is(self):
        from aicfd import components as library
        from aicfd import model as m

        spec = self.spec()
        roles = {r["role"]: r for r in m.components_in_use(spec)}
        self.assertEqual(set(roles), set(library.ROLES))
        for role, entry in roles.items():
            with self.subTest(role=role):
                self.assertIsNotNone(entry["chosen"], "every role has a house default")
                self.assertTrue(entry["options"])
                self.assertIn(entry["chosen"], [o["id"] for o in entry["options"]])

    def test_the_card_no_longer_states_a_free_area_of_its_own(self):
        """It said the same thing as the component and could disagree with it."""
        js = (server.REPO_ROOT / "web" / "app.js").read_text()
        for key in ("'grille_free_area'", "'grille_k'"):
            self.assertNotIn(key, js, f"{key} duplicates the component")
        self.assertIn("componentRows()", js)


class RackPageTest(unittest.TestCase):
    """The page that sets a load per position (ADR-054)."""

    CASE = "hall"  # the fixture, not the user's case (ADR-056)

    def setUp(self):
        support.sandbox(self)
        self.path = server.CASES_DIR / f"{self.CASE}.yaml"
        self.before = self.path.read_text()

    def test_it_lists_every_position_with_what_it_carries(self):
        payload = server.read_racks(self.CASE)
        self.assertEqual(payload["totals"]["positions"], len(payload["racks"]))
        self.assertEqual(payload["totals"]["unloaded"], 0)
        self.assertAlmostEqual(payload["totals"]["load_kw"],
                               payload["totals"]["nominal_kw"], places=1)
        for rack in payload["racks"][:3]:
            self.assertFalse(rack["stated"], "a fresh case states no overrides")

    def test_a_position_set_to_zero_is_written_and_counted(self):
        first = server.read_racks(self.CASE)["racks"][0]["id"]
        payload = server.write_racks(self.CASE, {"loads": {first: 0}})
        self.assertEqual(payload["rejected"], [])
        self.assertEqual(payload["totals"]["unloaded"], 1)
        got = next(r for r in payload["racks"] if r["id"] == first)
        self.assertEqual(got["load_kw"], 0.0)
        self.assertTrue(got["stated"])

    def test_the_file_states_only_what_differs(self):
        """So that raising the standard later moves every position that never
        disagreed with it."""
        import yaml

        racks = server.read_racks(self.CASE)["racks"]
        standard = server.read_racks(self.CASE)["standard"]["load_kw"]
        server.write_racks(self.CASE, {"loads": {r["id"]: standard for r in racks}})
        spec = yaml.safe_load(self.path.read_text())
        self.assertNotIn("loads", spec["racks"])

    def test_a_position_edited_back_to_the_standard_stops_being_stated(self):
        first = server.read_racks(self.CASE)["racks"][0]["id"]
        standard = server.read_racks(self.CASE)["standard"]["load_kw"]
        server.write_racks(self.CASE, {"loads": {first: 0}})
        payload = server.write_racks(self.CASE, {"loads": {first: standard}})
        self.assertEqual(payload["totals"]["unloaded"], 0)
        self.assertFalse(next(r for r in payload["racks"] if r["id"] == first)["stated"])

    def test_an_unknown_position_is_refused_rather_than_written(self):
        payload = server.write_racks(self.CASE, {"loads": {"NOT-A-RACK": 0}})
        self.assertEqual(len(payload["rejected"]), 1)
        self.assertIn("NOT-A-RACK", payload["rejected"][0])
        self.assertEqual(payload["totals"]["unloaded"], 0)

    def test_a_load_outside_the_range_is_refused(self):
        first = server.read_racks(self.CASE)["racks"][0]["id"]
        payload = server.write_racks(self.CASE, {"loads": {first: 500}})
        self.assertEqual(len(payload["rejected"]), 1)
        self.assertEqual(payload["totals"]["unloaded"], 0)

    def test_the_standard_moves_every_position_that_has_no_load_of_its_own(self):
        payload = server.write_racks(self.CASE, {"load_kw": 8.0})
        self.assertEqual(payload["standard"]["load_kw"], 8.0)
        self.assertTrue(all(r["load_kw"] == 8.0 for r in payload["racks"]))

    def test_a_save_keeps_every_comment_in_the_case(self):
        """A case file is hand-written and the comment beside a number is
        what says where the number came from. Re-dumping the parsed document
        would save the right values and lose all of that (ADR-048)."""
        commented = [
            line for line in self.before.split("\n")
            if "#" in line and not line.strip().startswith("#")
        ]
        self.assertTrue(commented, "this case has no trailing comments to lose")
        first = server.read_racks(self.CASE)["racks"][0]["id"]
        server.write_racks(self.CASE, {"loads": {first: 0}})
        after = self.path.read_text()
        for line in commented:
            self.assertIn(line, after, "a hand-written line was rewritten")

    def test_a_save_that_changes_nothing_leaves_the_file_alone(self):
        standard = server.read_racks(self.CASE)["standard"]["load_kw"]
        server.write_racks(self.CASE, {"load_kw": standard, "loads": {}})
        self.assertEqual(self.path.read_text(), self.before)

    def test_clearing_the_last_override_takes_the_block_with_it(self):
        first = server.read_racks(self.CASE)["racks"][0]["id"]
        server.write_racks(self.CASE, {"loads": {first: 0}})
        self.assertIn("loads:", self.path.read_text())
        server.write_racks(self.CASE, {"loads": {first: None}})
        self.assertEqual(self.path.read_text(), self.before)


class ImpossibleChangeTest(unittest.TestCase):
    """A change the generator refuses must not reach the file (ADR-055).

    Every value here is inside its own range; what they describe together is
    a room that cannot exist. Saved before it was built, that refusal became
    the case on disk, and from then on the page failed to load with the same
    error -- so the form that could undo it never came back.
    """

    CASE = "hall"  # the fixture, not the user's case (ADR-056)

    def setUp(self):
        support.sandbox(self)
        self.path = server.CASES_DIR / f"{self.CASE}.yaml"
        self.before = self.path.read_text()

    def apply(self, changes):
        """What the page's Apply does, as `do_POST` does it."""
        spec, rejected = server.apply_changes(server.load_spec(self.CASE), changes)
        try:
            payload = server.payload_for(self.CASE, spec)
        except ValueError as refused:
            payload = server.build_payload(self.CASE)
            payload["rejected"] = rejected + [str(refused)]
            return payload
        server.save_spec(self.CASE, spec)
        payload["rejected"] = rejected
        return payload

    def test_it_is_refused_rather_than_written(self):
        payload = self.apply({"fan_count": 40, "fan_width": 4.0})
        self.assertTrue(payload["rejected"])
        self.assertIn("do not fit", payload["rejected"][-1])
        self.assertEqual(self.path.read_text(), self.before)

    def test_the_page_still_loads_afterwards(self):
        self.apply({"fan_count": 40, "fan_width": 4.0})
        self.assertNotIn("error", server.build_payload(self.CASE))

    def test_a_change_that_does_build_is_still_written(self):
        payload = self.apply({"supply_temp_c": 21.0})
        self.assertEqual(payload["rejected"], [])
        self.assertNotEqual(self.path.read_text(), self.before)
        self.assertEqual(
            server.load_spec(self.CASE)["fanwall"]["supply_temp_c"], 21.0
        )

    def test_a_case_that_cannot_build_says_which_file_to_edit(self):
        broken = yaml.safe_load(self.before)
        broken["fanwall"]["count"] = 400
        self.path.write_text(yaml.safe_dump(broken, sort_keys=False))

        handler = server.Handler.__new__(server.Handler)
        handler.case_name = self.CASE
        sent = {}
        handler._json = lambda payload: sent.update(payload)
        handler.path = "/api/model"
        server.Handler.do_GET(handler)
        self.assertIn("error", sent)
        self.assertTrue(sent["file"].endswith(f"cases/{self.CASE}.yaml"))
        self.assertIn("do not fit", sent["error"])

    def test_a_case_outside_the_repository_still_names_its_file(self):
        """`relative_to` raises rather than declines, so the error panel's own
        path lookup became a second error (ADR-056)."""
        self.assertTrue(server.case_path(self.CASE).endswith(
            f"cases/{self.CASE}.yaml"))

    def test_the_page_tells_the_reader_where_that_file_is(self):
        js = (server.REPO_ROOT / "web" / "app.js").read_text()
        self.assertIn("model.file", js)


class PlenumCardTest(unittest.TestCase):
    """The card a reader meets before deciding anything (ADR-059)."""

    def test_the_house_standard_reaches_the_page(self):
        """An empty box says neither what the number would be nor that there
        is one, so the reader has to guess whether leaving it empty means
        anything."""
        payload = server.build_payload("pod-fanwall")
        defaults = payload["plenum_defaults"]
        self.assertEqual(defaults["plenum_depth"], 1.2)
        self.assertEqual(defaults["plenum_grille_width"], 2.0)
        self.assertAlmostEqual(defaults["plenum_grille_height"], 2.2, places=3)

    def test_the_face_velocity_is_not_a_field(self):
        """It is a consequence of the airflow and the opening, both of which
        the engineer does choose. Offering it as an input said otherwise
        (ADR-059)."""
        self.assertNotIn("plenum_face_velocity", server.EDITABLE)
        js = (server.REPO_ROOT / "web" / "app.js").read_text()
        self.assertNotIn("plenum_face_velocity", js)

    def test_it_is_reported_beside_the_fan_wall_s_own(self):
        from aicfd import model as m

        rows = m.summary_rows(m.build_model(server.load_spec("pod-plenum")))
        labels = [r[0] for r in rows]
        self.assertTrue(any(l.startswith("Supply grilles") for l in labels))
        supply = next(r for r in rows if r[0].startswith("Supply grilles"))
        self.assertIn("m/s", supply[2])

    def test_the_page_falls_back_to_it(self):
        js = (server.REPO_ROOT / "web" / "app.js").read_text()
        self.assertIn("model.plenum_defaults?.[key]", js)

    def test_a_checkbox_shows_whether_or_not_the_case_mentions_it(self):
        """Filtering on what the case states dropped the two switches and
        left the plenum as four number boxes with no way to turn it on."""
        js = (server.REPO_ROOT / "web" / "app.js").read_text()
        self.assertIn("p.optional || p.check || specValue(p.key) !== ''", js)

    def test_both_arrangements_are_switches_and_both_are_editable(self):
        for key in ("plenum", "plenum_as_mesh"):
            with self.subTest(key=key):
                self.assertIn(key, server.EDITABLE)
                self.assertIs(server.EDITABLE[key][1], bool)
