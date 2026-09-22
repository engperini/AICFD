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
