"""The cases in `cases/` still describe rooms that can be built.

This is the one place that answers that question. Every other test that reads
a worked case does it through `support.spec`, which skips when the file cannot
be used -- so a case broken on disk gives one failure that names the file
instead of thirty that name nothing (ADR-056).

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

    def test_the_shipped_cases_keep_their_comments(self):
        """A case is a template a person reads: the comment beside a number
        is what says where the number came from. Saving one from the page
        used to re-dump the document and take every comment with it."""
        for path in sorted(support.CASES.glob("*.yaml")):
            with self.subTest(case=path.name):
                commented = [
                    line for line in path.read_text().split("\n")
                    if "#" in line and not line.strip().startswith("#")
                ]
                self.assertTrue(
                    commented,
                    f"cases/{path.name} has lost the comments beside its "
                    f"numbers; put it back with `git restore cases/{path.name}`",
                )
