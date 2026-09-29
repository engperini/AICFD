"""A coupled run interrupted mid-loop continues from its newest field (ADR-132).

The container a long solve runs in can be recycled under it. Starting again
from nothing throws away every iteration already solved; `--resume` keeps the
mesh and the field and does what the next pass would have done. These tests
drive `solve_coupled` with the OpenFOAM commands stubbed, so what they check
is the loop's own bookkeeping: no mesh step is run again, the first segment
starts from the newest written time, the supplies read off that field are
written back before it, and the passes carry on until the loop closes.
"""
from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from aicfd import coupled, run

CONTROL = """FoamFile {{ }}
startFrom       startTime;
endTime         {end};
writeInterval   100;
"""

PIPELINE = (
    ("blockMesh", None),
    ("topoSet", None),
    ("createBaffles", ["-overwrite"]),
    ("decomposePar", ["-force"]),
    ("mpirun", ["-np", "4", "buoyantSimpleFoam", "-parallel"], "buoyantSimpleFoam"),
    ("reconstructPar", ["-latestTime"]),
)


class ResumeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.case = Path(self.tmp.name)
        (self.case / "system").mkdir()
        (self.case / "system" / "controlDict").write_text(CONTROL.format(end=2000))
        (self.case / "3400").mkdir()           # where the interrupted run got to
        self.ran: list[str] = []
        self.applied: list[str] = []
        self.supply = iter([24.0, 24.10, 24.12, 24.125])
        self.model = types.SimpleNamespace(equipment=types.SimpleNamespace(
            coil=types.SimpleNamespace(describe=lambda: {"duty_label": "compressor duty"})))

    def fake_command(self, case, command, args=None, log_name=None, **_):
        self.ran.append(log_name or command)
        if (log_name or command) == "buoyantSimpleFoam":
            end = run._end_time(case)
            (Path(case) / str(end)).mkdir(exist_ok=True)
        return types.SimpleNamespace(seconds=0.0)

    def solve(self):
        with mock.patch.object(run, "run_command", self.fake_command), \
             mock.patch.object(coupled, "supply_temperatures",
                               lambda model, step: ({"fan1": next(self.supply)}, {"fan1": 30.0}, [])), \
             mock.patch.object(coupled, "energy_closure", lambda model, step: 1.0), \
             mock.patch.object(coupled, "apply_supplies",
                               lambda case, time, supplies: self.applied.append(time) or 1):
            return run.solve_coupled(self.case, PIPELINE, self.model, segment=300,
                                     tolerance=0.02, resume=True)

    def test_no_mesh_step_runs_again(self):
        self.solve()
        for step in ("blockMesh", "topoSet", "createBaffles", "decomposePar"):
            self.assertNotIn(step, self.ran)

    def test_the_first_segment_starts_from_the_newest_field(self):
        self.solve()
        self.assertEqual(self.applied[0], "3400")
        self.assertTrue((self.case / "3700").is_dir())
        self.assertIn("startFrom       latestTime;", (self.case / "system" / "controlDict").read_text())

    def test_it_carries_on_until_the_loop_closes(self):
        _results, passes = self.solve()
        self.assertTrue(passes[-1].converged)
        self.assertEqual(passes[0].number, 2)   # pass 1 is the field it resumed from
        self.assertLessEqual(passes[-1].moved_k, 0.02)

    def test_a_case_with_nothing_solved_cannot_be_resumed(self):
        (self.case / "3400").rmdir()
        with self.assertRaises(RuntimeError) as caught:
            self.solve()
        self.assertIn("nothing to resume", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
