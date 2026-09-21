"""The README is what a new machine reads first.

Someone who has just installed Docker and cloned the repository has nothing
else to go on: the install steps, the addresses of the two pages and the table
of worked results ARE the documentation at that moment. A sentence in there
that quietly went out of date is not a cosmetic defect -- it is the first thing
the reader is told, and being wrong there costs the credibility of everything
after it.

Two claims in that file decay on their own, without anybody editing them:

  a count of the unit tests   every commit that adds a test makes it wrong.
                              It said 208 while the suite ran 694.

  the worked-result table     a case solved and tracked under `reference/`, or
                              one retired from it, leaves the table behind. It
                              said "the two worked cases" over a table of
                              three.

Neither is caught by anything else: the prose is not executed and the table is
not read. So they are checked here, against the repository itself rather than
against a number written down a second time -- a guard that names what it
guards (ADR-056, ADR-061, ADR-077, ADR-084).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
README = REPO / "README.md"
REFERENCE = REPO / "reference"


class WorkedResultTableTest(unittest.TestCase):
    def table(self) -> set[str]:
        """The case names the README's worked-result table names."""
        rows = re.findall(r"^\| `([a-z0-9-]+)\.yaml` \|", README.read_text(), re.M)
        self.assertTrue(rows, "the worked-result table has no rows any more")
        return set(rows)

    def tracked(self) -> set[str]:
        return {p.name for p in REFERENCE.iterdir() if p.is_dir()}

    def test_the_table_lists_every_result_the_repository_ships(self):
        missing = self.tracked() - self.table()
        self.assertFalse(
            missing,
            f"{sorted(missing)} is a worked result under reference/ that the "
            "README's table does not mention",
        )

    def test_the_table_lists_nothing_the_repository_does_not_ship(self):
        extra = self.table() - self.tracked()
        self.assertFalse(
            extra,
            f"the README's table promises {sorted(extra)}, which has no "
            "tracked result under reference/",
        )

    def test_the_sentence_over_the_table_counts_the_same_rows(self):
        """`The three worked cases` -- in words, and it was wrong once."""
        words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
        said = re.search(r"The (\w+) worked cases in `cases/`", README.read_text())
        self.assertIsNotNone(said, "the sentence introducing the table is gone")
        self.assertIn(said.group(1), words, f"{said.group(1)!r} is not a number")
        self.assertEqual(
            words[said.group(1)], len(self.tracked()),
            "the sentence over the worked-result table counts a different "
            "number of cases than reference/ holds",
        )


class TestCountTest(unittest.TestCase):
    """No CURRENT document may quote how many tests there are.

    The number is wrong the moment a test is added, and nothing fails when it
    goes wrong: the README said 208 and then 186 while the suite ran 694, in
    two places, for months. There is no way to keep it true, so the rule is
    not to write it down -- `the whole suite` says the same thing and stays
    true.

    `docs/DECISIONS.md` is deliberately NOT in the list. An ADR is a dated
    record of a decision, and a sentence like "the suite went from 219 to 186
    tests" is a measurement of the moment it was written: still true, and
    falsified by editing it. Only the documents that describe the software AS
    IT IS NOW are held to this, and an ADR's own present-tense claims about
    the tool are covered by the tests of the thing they describe.
    """

    DOCS = ("README.md", "docs/ROADMAP.md", "AGENTS.md", "CLAUDE.md")

    def test_no_document_quotes_a_test_count(self):
        pattern = re.compile(r"\b\d{2,}\s+(?:unit\s+)?tests\b|\ball\s+\d{2,}\b")
        for name in self.DOCS:
            path = REPO / name
            if not path.exists():
                continue
            for number, line in enumerate(path.read_text().splitlines(), 1):
                with self.subTest(document=name, line=number):
                    self.assertIsNone(
                        pattern.search(line),
                        f"{name}:{number} quotes a test count, which goes out "
                        f"of date on the next commit: {line.strip()!r}",
                    )


if __name__ == "__main__":
    unittest.main()
