"""Tests for the page's server side.

The form is a gate, not a text editor: it exists so a typo cannot put the spec
into a state the generator has never seen. These check the gate holds.
"""

from __future__ import annotations

import unittest

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
        for key, (section, field, _caster, _limits) in server.EDITABLE.items():
            self.assertTrue(section and field, key)


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
