"""The skills in `.claude/skills/` are files somebody uploads.

A skill is not read by this repository's code, so nothing here fails when one
is wrong -- it fails in whatever is loading it, often as a skill that simply
never triggers. Three ways that happens, and all three are checkable:

  the frontmatter does not parse     `Trigger on: "..."` inside an unquoted
                                     scalar is a YAML mapping where a string
                                     was meant, and a strict loader rejects
                                     the whole block. `datacenter-cfd` shipped
                                     that way (ADR-093)
  the name does not match the folder a skill is addressed by its name, and a
                                     name that disagrees with its directory is
                                     one nobody can call
  a bundled reference has drifted    `case-authoring` carries a copy of
                                     `docs/CASE-AUTHORING.md` so it works when
                                     uploaded on its own. A copy that no
                                     longer matches the manual is worse than
                                     none: the manual is tested against the
                                     code, and the copy is what the agent
                                     actually reads
"""

from __future__ import annotations

import re
import unittest

import yaml

from tests import support

SKILLS = support.REPO / ".claude" / "skills"

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

#: What a skill's description has to fit in to be loaded.
DESCRIPTION_LIMIT = 1024


def skills():
    return sorted(SKILLS.glob("*/SKILL.md"))


class FrontmatterTest(unittest.TestCase):
    def test_there_are_skills_to_check(self):
        self.assertTrue(skills(), f"no SKILL.md under {SKILLS}")

    def test_every_frontmatter_parses_as_yaml(self):
        for path in skills():
            with self.subTest(skill=path.parent.name):
                block = FRONTMATTER.match(path.read_text())
                self.assertIsNotNone(
                    block, f"{path} does not open with a --- frontmatter block")
                try:
                    data = yaml.safe_load(block.group(1))
                except yaml.YAMLError as broken:
                    self.fail(
                        f"{path} has frontmatter YAML cannot read: {broken}. "
                        "A colon followed by a space inside an unquoted value "
                        "is the usual cause -- quote the value, or drop the "
                        "colon"
                    )
                self.assertIsInstance(data, dict, f"{path}: frontmatter is not a mapping")

    def loaded(self) -> dict:
        """The frontmatter of every skill whose frontmatter parses.

        One that does not is reported by the test above and skipped here, so
        a single broken block gives one message rather than four.
        """
        out = {}
        for path in skills():
            block = FRONTMATTER.match(path.read_text())
            if not block:
                continue
            try:
                data = yaml.safe_load(block.group(1))
            except yaml.YAMLError:
                continue
            if isinstance(data, dict):
                out[path] = data
        return out

    def test_every_skill_states_a_name_and_a_description(self):
        for path, data in self.loaded().items():
            with self.subTest(skill=path.parent.name):
                for key in ("name", "description"):
                    self.assertIn(key, data, f"{path} has no {key}")
                    self.assertTrue(str(data[key]).strip(), f"{path}: {key} is empty")

    def test_the_name_matches_the_folder_it_is_in(self):
        for path, data in self.loaded().items():
            with self.subTest(skill=path.parent.name):
                self.assertEqual(
                    data["name"], path.parent.name,
                    "a skill is addressed by its name; this one disagrees with "
                    "its directory, so nobody can call it",
                )

    def test_no_description_is_longer_than_a_loader_will_take(self):
        for path, data in self.loaded().items():
            with self.subTest(skill=path.parent.name):
                self.assertLessEqual(
                    len(data["description"]), DESCRIPTION_LIMIT,
                    f"{path}: the description is "
                    f"{len(data['description'])} characters, over the "
                    f"{DESCRIPTION_LIMIT} a loader takes",
                )


class BundledReferenceTest(unittest.TestCase):
    """What the uploaded skill reads has to be what this repository tests.

    `docs/CASE-AUTHORING.md` is held to the code by
    `tests/test_case_authoring.py`. The copy inside the skill is not, so the
    only thing that can keep it honest is being the same file.
    """

    COPY = SKILLS / "case-authoring" / "reference" / "CASE-AUTHORING.md"
    SOURCE = support.REPO / "docs" / "CASE-AUTHORING.md"

    def test_the_skill_carries_the_manual(self):
        self.assertTrue(
            self.COPY.exists(),
            f"{self.COPY} is missing, so the skill is broken once uploaded: "
            "its own SKILL.md sends the reader to it",
        )

    def test_the_copy_is_the_manual(self):
        if not self.COPY.exists():
            self.skipTest("reported by test_the_skill_carries_the_manual")
        self.assertEqual(
            self.COPY.read_bytes(), self.SOURCE.read_bytes(),
            "the skill's copy of the manual has drifted from docs/. Refresh "
            "it:  cp docs/CASE-AUTHORING.md "
            ".claude/skills/case-authoring/reference/",
        )

    def test_the_skill_sends_the_reader_to_files_it_ships(self):
        skill = (SKILLS / "case-authoring" / "SKILL.md").read_text()
        for name in set(re.findall(r"`(reference/[A-Za-z0-9._/-]+)`", skill)):
            with self.subTest(file=name):
                self.assertTrue(
                    (SKILLS / "case-authoring" / name).exists(),
                    f"SKILL.md points at {name}, which the skill does not ship",
                )


if __name__ == "__main__":
    unittest.main()
