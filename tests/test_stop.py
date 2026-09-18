"""Stopping a solve the way a solver is meant to be stopped.

Not a signal and not a kill: OpenFOAM re-reads its controlDict every
iteration, so a stop is a word written into a file. What these defend is that
the word is the right one, that it is written atomically (the solver may be
reading that file at the moment it changes), and that it never survives into
the next run.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import case, run
from aicfd.model import build_model

SPEC = yaml.safe_load(
    """
name: s
gallery: {depth: 3.0}
hall: {size: [6.0, 4.2, 8.0], ceiling: 6.5}
aisles: {cold: 1.8, hot: 1.2}
racks: {count: 3, load_kw: 6.0, size: [0.6, 1.2, 2.2], offset_x: 2.0}
fanwall: {airflow_m3h: 5000, supply_temp_c: 20.0, height: 4.0, width: 1.8,
          static_pressure_pa: 100}
grilles: {size: 0.6, count: 3, free_area: 0.8}
containment: {enabled: true}
mesh: {cell_size: [0.2, 0.2, 0.1]}
"""
)


class StopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.case = Path(self.tmp.name) / "case"
        case.build(build_model(SPEC), self.case, max_iterations=2000)

    def tearDown(self):
        self.tmp.cleanup()

    def test_a_generated_case_is_stoppable_at_all(self):
        """The mechanism only exists because the generator writes
        runTimeModifiable; without it the solver reads controlDict once and a
        stop would do nothing at all, silently."""
        text = (self.case / "system" / "controlDict").read_text()
        self.assertIn("runTimeModifiable true;", text)
        self.assertIn(run.RUNNING_STOP, text)
        self.assertFalse(run.stop_requested(self.case))

    def test_stopping_asks_for_a_write(self):
        """writeNow, not noWriteNow: a run stopped without a write leaves
        nothing to post-process, and keeping what was computed is the whole
        point of stopping cleanly rather than killing the process."""
        self.assertTrue(run.request_stop(self.case))
        text = (self.case / "system" / "controlDict").read_text()
        self.assertIn("stopAt          writeNow;", text)
        self.assertNotIn("stopAt          endTime;", text)
        self.assertTrue(run.stop_requested(self.case))

    def test_nothing_else_in_the_dictionary_moves(self):
        before = (self.case / "system" / "controlDict").read_text()
        run.request_stop(self.case)
        after = (self.case / "system" / "controlDict").read_text()
        self.assertEqual(
            before.replace(run.RUNNING_STOP, run.STOP_NOW), after
        )

    def test_asking_twice_is_not_an_error(self):
        self.assertTrue(run.request_stop(self.case))
        self.assertTrue(run.request_stop(self.case))

    def test_it_leaves_no_temporary_behind(self):
        """The file is replaced atomically because the solver may be reading
        it; a leftover temporary would be the sign that it was not."""
        run.request_stop(self.case)
        left = [p.name for p in (self.case / "system").iterdir()
                if "stopping" in p.name]
        self.assertEqual(left, [])

    def test_a_case_with_no_control_dict_says_so(self):
        empty = Path(self.tmp.name) / "empty"
        empty.mkdir()
        self.assertFalse(run.request_stop(empty))
        self.assertFalse(run.stop_requested(empty))

    def test_the_next_run_is_not_stopped_before_it_starts(self):
        """A stop must not outlive its run. `aicfd run` regenerates the case,
        so the flag is gone -- this is what makes it safe to leave the file
        as it is after a stop."""
        run.request_stop(self.case)
        case.build(build_model(SPEC), self.case, max_iterations=2000)
        self.assertFalse(run.stop_requested(self.case))
