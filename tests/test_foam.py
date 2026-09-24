"""Tests for the OpenFOAM readers and the ASHRAE verdict.

The readers are the only place AICFD parses OpenFOAM's own file formats, so a
change in them is a change in every number downstream. The verdict is the one
piece of the standard the tool asserts on its own.

Run with: python -m unittest discover tests
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from aicfd.foam import solverlog
from aicfd.foam.fields import FoamParseError, read_field, to_grid
from aicfd.post import _ashrae_verdict

HEADER = """FoamFile { version 2.0; format ascii; class %s; object f; }
dimensions      [0 0 0 1 0 0 0];
"""


def write_field(directory: Path, body: str, kind: str = "volScalarField") -> Path:
    path = directory / "f"
    path.write_text(HEADER % kind + body)
    return path


class FieldReaderTests(unittest.TestCase):
    def test_reads_nonuniform_scalars(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_field(
                Path(tmp),
                "internalField   nonuniform List<scalar>\n4\n(\n1\n2\n3\n4\n)\n;\n",
            )
            np.testing.assert_allclose(read_field(path, 4), [1, 2, 3, 4])

    def test_reads_nonuniform_vectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_field(
                Path(tmp),
                "internalField   nonuniform List<vector>\n2\n(\n(1 2 3)\n(4 5 6)\n)\n;\n",
                kind="volVectorField",
            )
            np.testing.assert_allclose(read_field(path, 2), [[1, 2, 3], [4, 5, 6]])

    def test_broadcasts_uniform_scalar(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_field(Path(tmp), "internalField   uniform 291;\n")
            np.testing.assert_allclose(read_field(path, 3), [291, 291, 291])

    def test_broadcasts_uniform_vector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_field(
                Path(tmp),
                "internalField   uniform (1.8 0 0);\n",
                kind="volVectorField",
            )
            np.testing.assert_allclose(
                read_field(path, 2), [[1.8, 0, 0], [1.8, 0, 0]]
            )

    def test_rejects_a_count_that_does_not_match_the_body(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_field(
                Path(tmp),
                "internalField   nonuniform List<scalar>\n4\n(\n1\n2\n)\n;\n",
            )
            with self.assertRaises(FoamParseError):
                read_field(path, 4)

    def test_grid_is_indexed_k_j_i(self):
        # x varies fastest in OpenFOAM's cell order, so cell 1 must land at i=1.
        values = np.arange(2 * 3 * 4)  # nx=2, ny=3, nz=4
        grid = to_grid(values, (2, 3, 4))
        self.assertEqual(grid.shape, (4, 3, 2))
        self.assertEqual(grid[0, 0, 1], 1)
        self.assertEqual(grid[0, 1, 0], 2)
        self.assertEqual(grid[1, 0, 0], 6)

    def test_grid_rejects_a_mismatched_shape(self):
        with self.assertRaises(FoamParseError):
            to_grid(np.arange(10), (2, 3, 4))


class SolverLogTests(unittest.TestCase):
    LOG = """Time = 1

smoothSolver:  Solving for Ux, Initial residual = 0.5, Final residual = 1e-06, No Iterations 1
smoothSolver:  Solving for h, Initial residual = 0.4, Final residual = 1e-06, No Iterations 1
time step continuity errors : sum local = 1e-08, global = 2e-09, cumulative = -1e-3
ExecutionTime = 0.5 s  ClockTime = 1 s

Time = 2

smoothSolver:  Solving for Ux, Initial residual = 0.05, Final residual = 1e-07, No Iterations 1
smoothSolver:  Solving for Ux, Initial residual = 0.04, Final residual = 1e-07, No Iterations 1
smoothSolver:  Solving for h, Initial residual = 0.02, Final residual = 1e-07, No Iterations 1
ExecutionTime = 1.0 s  ClockTime = 2 s
"""

    def parse(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "log"
            path.write_text(text)
            return solverlog.parse(path)

    def test_counts_iterations_and_final_residuals(self):
        log = self.parse(self.LOG)
        self.assertEqual(log.iterations, [1, 2])
        # Only the first solve of a field per outer iteration is recorded.
        self.assertEqual(log.residuals["Ux"], [0.5, 0.05])
        self.assertEqual(log.final_residuals(), {"Ux": 0.05, "h": 0.02})
        self.assertEqual(log.execution_time_s, 1.0)

    def test_iteration_limit_is_not_convergence(self):
        self.assertTrue(self.parse(self.LOG).stopped_on_iteration_limit)

    def test_recognises_real_convergence(self):
        log = self.parse(self.LOG + "\nSIMPLE solution converged in 2 iterations\n")
        self.assertEqual(log.converged_at, 2)
        self.assertFalse(log.stopped_on_iteration_limit)

    def test_pads_a_field_that_appears_late(self):
        log = self.parse(self.LOG)
        self.assertEqual(len(log.residuals["h"]), len(log.iterations))


class AshraeTests(unittest.TestCase):
    def test_inside_the_recommended_band(self):
        verdict = _ashrae_verdict(22.0)
        self.assertTrue(verdict["within_recommended"])
        self.assertFalse(verdict["above_recommended"])

    def test_too_cold_is_flagged_but_is_not_a_risk(self):
        verdict = _ashrae_verdict(16.0)
        self.assertTrue(verdict["below_recommended"])
        self.assertFalse(verdict["above_recommended"])
        self.assertIn("A1", verdict["allowable_classes"])

    def test_too_hot_is_a_risk(self):
        verdict = _ashrae_verdict(33.0)
        self.assertTrue(verdict["above_recommended"])
        self.assertNotIn("A1", verdict["allowable_classes"])

    def test_beyond_every_envelope(self):
        verdict = _ashrae_verdict(50.0)
        self.assertEqual(verdict["allowable_classes"], [])


if __name__ == "__main__":
    unittest.main()


class AFailureSaysWhatToDoTest(unittest.TestCase):
    """`mpirun exited 1` is not a reason (ADR-113).

    Asking for more cores than the machine gives MPI slots for, the solver
    never starts. Open MPI says exactly why, in the log -- and the tool
    printed only the exit code, so the reason sat in a file nobody was told to
    open.
    """

    def failure(self, text: str):
        import tempfile
        from pathlib import Path

        from aicfd.run import FoamCommandFailed

        log = Path(tempfile.mkdtemp()) / "log.buoyantSimpleFoam"
        log.write_text(text)
        return FoamCommandFailed("mpirun", 1, log)

    def test_too_many_cores_is_named_and_the_remedy_given(self):
        failed = self.failure(
            "There are not enough slots available in the system to satisfy "
            "the 24 slots that were requested by the application\n")
        self.assertIn("solver.processors", str(failed))
        self.assertIn("core count", str(failed))
        self.assertIsNotNone(failed.why)

    def test_a_stale_decomposition_is_named(self):
        failed = self.failure(
            "number of processor directories = 9 is not equal to the number "
            "of processors = 16\n")
        self.assertIn("build it again", str(failed))

    def test_a_failure_it_cannot_read_still_points_at_the_log(self):
        failed = self.failure("Something nobody has seen before\n")
        self.assertIsNone(failed.why)
        self.assertIn("log.buoyantSimpleFoam", str(failed))
        self.assertIn("exited 1", str(failed))

    def test_a_log_that_is_not_there_is_not_a_second_failure(self):
        from pathlib import Path

        from aicfd.run import FoamCommandFailed

        failed = FoamCommandFailed("mpirun", 1, Path("/nowhere/log.x"))
        self.assertIn("exited 1", str(failed))


class TheLogIsTheWholeRunTest(unittest.TestCase):
    """A coupled solve runs the solver once per pass, and the log has to hold
    all of them (ADR-122).

    Truncating it each pass threw away every iteration but the last segment's.
    An engineer watching the file saw it empty and start again -- which is
    what a solver restarting from zero looks like -- and the report's
    convergence figure showed 300 iterations of a 2.800-iteration run.
    """

    def setUp(self):
        import shutil
        import tempfile
        from pathlib import Path

        self.case = Path(tempfile.mkdtemp())
        self.echo = shutil.which("echo", path="/usr/bin:/bin")

    def run_echo(self, text: str, append: bool):
        from aicfd import run

        return run.run_command(self.case, "echo", args=[text],
                               log_name="buoyantSimpleFoam", append=append)

    @unittest.skipIf(not __import__("shutil").which("echo", path="/usr/bin:/bin"),
                     "no echo on the foam PATH")
    def test_a_second_pass_keeps_what_the_first_wrote(self):
        self.run_echo("Time = 300", append=False)
        self.run_echo("Time = 600", append=True)
        text = (self.case / "log.buoyantSimpleFoam").read_text()
        self.assertIn("Time = 300", text)
        self.assertIn("Time = 600", text)

    @unittest.skipIf(not __import__("shutil").which("echo", path="/usr/bin:/bin"),
                     "no echo on the foam PATH")
    def test_a_fresh_run_still_starts_from_an_empty_log(self):
        """A new run must not inherit the last one's iterations."""
        self.run_echo("Time = 300", append=False)
        self.run_echo("Time = 100", append=False)
        text = (self.case / "log.buoyantSimpleFoam").read_text()
        self.assertNotIn("Time = 300", text)
        self.assertIn("Time = 100", text)

    def test_the_coupled_solve_appends_every_pass_after_the_first(self):
        import inspect

        from aicfd import run

        source = inspect.getsource(run.solve_coupled)
        self.assertIn("append=number > 1", source)

    def test_the_meshing_steps_still_overwrite(self):
        """blockMesh's log is that blockMesh's, not every blockMesh ever."""
        import inspect

        from aicfd import run

        self.assertIn("append: bool = False", inspect.getsource(run.run_command))
        self.assertNotIn("append=True", inspect.getsource(run.solve))


class TheRunLeavesARecordOfHowTheLoopEndedTest(unittest.TestCase):
    """Whether the coupled loop CLOSED decides whether the supply temperature
    in the report is the plant's answer or the last guess before the pass cap
    (ADR-123).

    It was returned to the caller and nowhere else, so the only way to know
    was to have watched the run. A reader of the report was not there.
    """

    def record(self, passes, **kw):
        from aicfd.run import read_coupling_record, write_coupling_record

        with tempfile.TemporaryDirectory() as tmp:
            write_coupling_record(Path(tmp), passes, kw.get("limit", 30),
                                  kw.get("tolerance", 0.02))
            return read_coupling_record(Path(tmp))

    def a_pass(self, number, moved, converged, saturated=()):
        from aicfd.coupled import Pass

        return Pass(number=number, iterations=300 * number,
                    supplies_c={"fan1": 21.9}, returns_c={"fan1": 30.0},
                    moved_k=moved, converged=converged,
                    saturated=list(saturated))

    def test_a_loop_that_closed_says_so(self):
        got = self.record([self.a_pass(1, None, False),
                           self.a_pass(2, 0.004, True)])
        self.assertTrue(got["converged"])
        self.assertEqual(got["passes"], 2)
        self.assertEqual(got["moved_k"], 0.004)
        self.assertEqual(len(got["history"]), 2)

    def test_a_loop_stopped_by_its_cap_says_so(self):
        got = self.record([self.a_pass(n, 1.2, False) for n in range(1, 6)],
                          limit=5)
        self.assertFalse(got["converged"])
        self.assertEqual(got["passes"], got["limit"])

    def test_a_run_that_never_coupled_leaves_nothing(self):
        from aicfd.run import read_coupling_record

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(read_coupling_record(Path(tmp)))

    def test_a_damaged_record_is_not_an_exception(self):
        from aicfd.run import COUPLING_RECORD, read_coupling_record

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / COUPLING_RECORD).write_text("{not json")
            self.assertIsNone(read_coupling_record(Path(tmp)))

    def test_the_solve_writes_it(self):
        import inspect

        from aicfd import run

        said = inspect.getsource(run.solve_coupled)
        self.assertIn("write_coupling_record(case, passes", said)

    def test_the_post_reads_it_into_the_export(self):
        import inspect

        from aicfd import post

        self.assertIn('kpis["coupling"] = read_coupling_record(case)',
                      inspect.getsource(post))

    def test_a_run_told_not_to_couple_says_so_on_the_record(self):
        """`solver.couple: false` is a choice, and the report has to state
        it -- which it can only do if it can tell the choice from a run that
        left no record at all (ADR-124)."""
        from aicfd.run import read_coupling_record, write_coupling_off

        with tempfile.TemporaryDirectory() as tmp:
            write_coupling_off(Path(tmp))
            got = read_coupling_record(Path(tmp))
        self.assertTrue(got["off"])
        self.assertEqual(got["passes"], 0)
        self.assertNotIn("converged", got)

    def test_the_limit_is_a_safety_net_not_a_target(self):
        """Every tracked run closed in two to five passes; the limit has to
        sit far above that, or it is a setting again by another name."""
        from aicfd.run import COUPLING_PASS_LIMIT, COUPLING_TOLERANCE_K

        self.assertGreaterEqual(COUPLING_PASS_LIMIT, 20)
        self.assertLessEqual(COUPLING_TOLERANCE_K, 0.05)

    def test_both_entry_points_leave_the_off_record(self):
        import inspect

        from aicfd import cli, server

        self.assertIn("write_coupling_off(target)", inspect.getsource(cli))
        self.assertIn("write_coupling_off(target)",
                      inspect.getsource(server.start_run))


class ALoopWhoseStepDoesNotShrinkIsStoppedTest(unittest.TestCase):
    """A fixed-point iteration that converges takes smaller steps; one whose
    step holds or grows is running away, and thirty passes of it is two
    hours of solver to learn what four already said (ADR-125)."""

    def passes(self, moved):
        from aicfd.coupled import Pass

        return [Pass(number=i + 1, iterations=300, supplies_c={}, returns_c={},
                     moved_k=m, converged=False, saturated=[])
                for i, m in enumerate(moved)]

    def test_a_runaway_is_caught_after_four_steps_that_never_shrank(self):
        from aicfd.run import _diverging

        self.assertTrue(_diverging(self.passes([None, 1.06, 1.08, 1.09, 1.10, 1.12])))

    def test_a_converging_loop_is_left_alone(self):
        from aicfd.run import _diverging

        self.assertFalse(_diverging(self.passes([None, 0.22, 0.063, 0.048, 0.043, 0.009])))
        self.assertFalse(_diverging(self.passes([None, 1.06, 1.08])),
                         "too early to tell")

    def test_a_step_that_shrinks_once_resets_the_count(self):
        from aicfd.run import _diverging

        self.assertFalse(_diverging(self.passes([None, 1.0, 1.1, 0.9, 1.0, 1.1])))

    def test_the_record_says_diverged(self):
        from aicfd.run import read_coupling_record, write_coupling_record

        with tempfile.TemporaryDirectory() as tmp:
            write_coupling_record(Path(tmp), self.passes([None, 1.0, 1.1]), 30,
                                  0.02, diverged=True)
            got = read_coupling_record(Path(tmp))
        self.assertTrue(got["diverged"])
        self.assertFalse(got["converged"])

    def test_the_remedy_calls_a_runaway_a_runaway(self):
        from aicfd.post import _closure_remedy

        said = _closure_remedy({"coupling": {"passes": 6, "limit": 30,
                                             "converged": False, "diverged": True,
                                             "moved_k": 1.12}})
        self.assertIn("kept moving by about 1.12 K every pass", said)
        self.assertIn("chasing its own return", said)


class SettlingPassesAreNeverARunawayTest(unittest.TestCase):
    def test_four_small_steps_in_a_row_are_convergence(self):
        """A room filling behind a settled supply takes passes whose supply
        barely moves; four of those are the loop closing (ADR-127)."""
        from aicfd.coupled import Pass
        from aicfd.run import _diverging

        passes = [Pass(number=i + 1, iterations=300, supplies_c={}, returns_c={},
                       moved_k=m, converged=False, saturated=[])
                  for i, m in enumerate([None, 1.0, 0.3, 0.01, 0.01, 0.012, 0.011])]
        self.assertFalse(_diverging(passes, 0.02))

    def test_the_record_carries_the_closure_and_the_settling_count(self):
        from aicfd.coupled import Pass
        from aicfd.run import read_coupling_record, write_coupling_record

        passes = [Pass(number=1, iterations=1600, supplies_c={}, returns_c={},
                       moved_k=None, converged=False, saturated=[], closure=0.86),
                  Pass(number=2, iterations=1900, supplies_c={}, returns_c={},
                       moved_k=0.01, converged=False, saturated=[], closure=0.91,
                       settling=True),
                  Pass(number=3, iterations=2200, supplies_c={}, returns_c={},
                       moved_k=0.005, converged=True, saturated=[], closure=0.99)]
        with tempfile.TemporaryDirectory() as tmp:
            write_coupling_record(Path(tmp), passes, 30, 0.02)
            got = read_coupling_record(Path(tmp))
        self.assertEqual(got["settling_passes"], 1)
        self.assertAlmostEqual(got["closure"], 0.99)
        self.assertTrue(got["converged"])


class EveryPassEndsOnAWrittenTimeTest(unittest.TestCase):
    """A 300-iteration segment under a 500 write interval left no new field;
    the loop read the same time twice, saw 0,00 K of movement and declared a
    room converged that had not been solved since the pass before (ADR-128)."""

    def test_the_end_of_a_run_is_a_written_iteration(self):
        from aicfd.case import written_end

        self.assertEqual(written_end(1600, 500), 2000)
        self.assertEqual(written_end(1600, 100), 1600)
        self.assertEqual(written_end(1600, 300), 1800)

    def test_the_segment_is_rounded_up_to_the_write_interval(self):
        import inspect

        from aicfd import run

        said = inspect.getsource(run.solve_coupled)
        self.assertIn("interval = _write_interval(case)", said)
        self.assertIn("segment = -(-segment // interval) * interval", said)

    def test_a_pass_that_saw_no_new_field_is_an_error_not_a_convergence(self):
        import inspect

        from aicfd import run

        said = inspect.getsource(run.solve_coupled)
        self.assertIn("if time == previous_time:", said)
        self.assertIn("wrote no new field on coupling pass", said)

    def test_the_write_interval_is_read_from_the_control_dict(self):
        from aicfd.run import _write_interval

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "system").mkdir()
            (Path(tmp) / "system" / "controlDict").write_text(
                "endTime         1600;\nwriteInterval   500;\n")
            self.assertEqual(_write_interval(Path(tmp)), 500)
