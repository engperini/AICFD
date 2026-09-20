"""The cases in `cases/` still describe rooms that can be built.

This is the ONLY place that looks at the working folder, and it asks the only
question that is fair to ask of it: does what is there still build. Every
other test reads a case the suite owns, from `tests/cases/`.

Nothing here asserts on the CONTENT of those files. A case edited through the
page -- a few rack loads set, a dimension changed, the comments gone -- is the
software doing its job, and a test that failed on it told the engineer to
`git restore` the work he had just done. Whether a save keeps a file readable
is tested on the save path, against a sandbox (ADR-056).

`cases/` is the engineer's working folder, so a failure here is usually not a
regression in the code: it is a case edited to something the generator
refuses, and the message says which value and how to put it back.
"""

from __future__ import annotations

import unittest

import yaml

from aicfd import model as m
from tests import support


class WorkedCasesTest(unittest.TestCase):
    def fail_clean(self, message: str):
        """`fail` from inside an `except` chains the original traceback onto
        the message, which buries it. This one does not."""
        raise self.failureException(message) from None

    def test_every_case_on_disk_builds(self):
        cases = sorted(support.CASES.glob("*.yaml"))
        self.assertTrue(cases, "the repository ships worked cases")
        for path in cases:
            with self.subTest(case=path.name):
                try:
                    spec = yaml.safe_load(path.read_text())
                except yaml.YAMLError as broken:
                    self.fail(f"cases/{path.name} is not valid YAML: {broken}")
                try:
                    m.build_model(spec)
                except Exception as refused:  # noqa: BLE001 -- reported, not handled
                    # `from None`: the reader needs the sentence, not the
                    # generator's stack.
                    refused = str(refused)
                    self.fail_clean(
                        f"cases/{path.name} does not build: {refused}\n"
                        f"        This is the case file, not the code. Fix the "
                        f"value the message names, or put the file back with\n"
                        f"            git restore cases/{path.name}"
                    )
