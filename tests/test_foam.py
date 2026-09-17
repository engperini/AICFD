"""Tests for the OpenFOAM readers and the KPI pass.

Run with: python -m unittest discover tests
"""

import tempfile
import unittest
from pathlib import Path

import numpy as np

from aicfd.foam import solverlog
from aicfd.foam.casedict import read_case
from aicfd.foam.fields import FoamParseError, read_field, to_grid
from aicfd.post import _ashrae_verdict, analyse

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CASE = REPO_ROOT / ".claude/skills/datacenter-cfd/reference-case"
#: `aicfd run` solves into runs/, leaving the template untouched.
SOLVED_CASE = REPO_ROOT / "runs" / "reference-case"

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


class CaseReaderTests(unittest.TestCase):
    def setUp(self):
        if not REFERENCE_CASE.exists():
            self.skipTest("reference case not present")
        self.geometry = read_case(REFERENCE_CASE)

    def test_reads_room_and_mesh(self):
        self.assertEqual(self.geometry.room.size, (6.0, 4.0, 3.0))
        self.assertEqual(self.geometry.divisions, (60, 40, 30))
        self.assertEqual(self.geometry.n_cells, 72000)

    def test_reads_patches(self):
        self.assertEqual(self.geometry.patches["fanwall"], "patch")
        self.assertEqual(self.geometry.patches["floor"], "wall")

    def test_reads_rack_zone_and_load(self):
        self.assertEqual(self.geometry.zones["rack"].lo, (3.0, 1.5, 0.0))
        self.assertEqual(self.geometry.zones["rack"].hi, (3.6, 2.5, 2.0))
        self.assertEqual(self.geometry.total_load_w, 5000.0)

    def test_finds_the_supply_patch_not_a_wall(self):
        # noSlip walls are also fixed-value; only a non-zero one is a supply.
        self.assertEqual(self.geometry.inlet_patch, "fanwall")
        self.assertEqual(self.geometry.inlet_velocity, (1.8, 0.0, 0.0))
        self.assertEqual(self.geometry.inlet_temperature_k, 291.0)


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


class PlausibleVelocityTests(unittest.TestCase):
    """The check that catches a field no amount of fan or buoyancy could drive."""

    def evaluate(self, peak_speed, supply=1.8, spread_k=2.0):
        import numpy as np

        from aicfd.foam.casedict import Box, CaseGeometry
        from aicfd.post import KELVIN, _evaluate
        from aicfd.foam import solverlog

        divisions = (4, 4, 4)
        geometry = CaseGeometry(
            room=Box(lo=(0, 0, 0), hi=(6.0, 4.0, 3.0)),
            divisions=divisions,
            inlet_velocity=(supply, 0.0, 0.0),
            inlet_temperature_k=291.0,
            inlet_patch="fanwall",
        )
        shape = (divisions[2], divisions[1], divisions[0])
        temperature = np.full(shape, 291.0)
        temperature[-1, -1, -1] = 291.0 + spread_k
        velocity = np.zeros((*shape, 3))
        velocity[..., 0] = supply
        velocity[0, 0, 0, 0] = peak_speed
        _, checks, warnings = _evaluate(
            geometry, {"T": temperature, "U": velocity}, solverlog.SolverLog()
        )
        check = next(c for c in checks if c.name == "plausible_velocity")
        return check, warnings

    def test_a_field_within_reach_passes(self):
        check, _ = self.evaluate(peak_speed=3.0)
        self.assertTrue(check.passed, check.detail)

    def test_an_impossible_field_fails(self):
        check, warnings = self.evaluate(peak_speed=14.3)
        self.assertFalse(check.passed, check.detail)
        self.assertTrue(
            any("unreliable" in w for w in warnings),
            "a failing velocity check must warn that the temperatures are "
            "from the same field",
        )

    def test_the_ceiling_follows_buoyancy_when_the_supply_is_slow(self):
        # A slow supply with a large temperature spread still permits real
        # motion; the check must not fail that.
        check, _ = self.evaluate(peak_speed=5.0, supply=0.2, spread_k=20.0)
        self.assertTrue(check.passed, check.detail)

    def test_a_still_room_still_has_a_floor(self):
        # With no supply and no spread the ceiling must not collapse to zero,
        # or every quiet case fails.
        check, _ = self.evaluate(peak_speed=0.2, supply=0.0, spread_k=0.0)
        self.assertTrue(check.passed, check.detail)


class ReferenceResultsTests(unittest.TestCase):
    """Guards the numbers the reference case is documented to produce."""

    @classmethod
    def setUpClass(cls):
        if not SOLVED_CASE.exists():
            raise unittest.SkipTest("run 'python -m aicfd run' first")
        cls.results, _, _ = analyse(SOLVED_CASE)

    def test_all_checks_pass(self):
        failed = [c.name for c in self.results.checks if not c.passed]
        self.assertEqual(failed, [], f"failing checks: {failed}")

    def test_rack_temperatures_match_the_documented_run(self):
        rack = self.results.kpis["zones"][0]
        self.assertAlmostEqual(rack["mean_temp_c"], 18.73, places=1)
        self.assertAlmostEqual(rack["peak_temp_c"], 19.50, places=1)

    def test_reads_the_solver_log_not_the_meshing_logs(self):
        # blockMesh/topoSet/checkMesh also write log.* files; picking one of
        # those reports a converged run as having no convergence data.
        self.assertEqual(self.results.kpis["iterations"], 800)
        self.assertIsNotNone(self.results.kpis["runtime_s"])

    def test_over_ventilation_is_reported(self):
        self.assertGreater(self.results.kpis["over_ventilation_factor"], 10)
        self.assertTrue(
            any("Supply airflow is" in w for w in self.results.warnings),
            "the over-ventilation warning should fire on this case",
        )


if __name__ == "__main__":
    unittest.main()
