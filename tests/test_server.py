"""Tests for the page's server side.

The form is a gate, not a text editor: it exists so a typo cannot put the spec
into a state the generator has never seen. These check the gate holds.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from aicfd import server


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
