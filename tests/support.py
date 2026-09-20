"""What a test needs from the repository, without depending on a case file
that the user is meant to edit.

`cases/` is the engineer's working folder: the page writes to it, and a case
edited through the page is exactly what this software is for. Tests that read
those files as fixtures therefore fail on a change that is not a change to the
code -- a chosen grille, a different supply temperature, a fan wall that does
not fit -- and the image stops building for a reason that has nothing to do
with the image. That happened: one press of Apply left a case the generator
refuses, and `docker build` came back with thirty errors and two failures,
none of which named the file (ADR-056).

So a test that only needs *a* hall reads one through `spec` and is skipped,
with the file named, when that case cannot be built; and a test that writes
works on a copy through `sandbox`, never on the user's own files. Whether the
shipped cases build is one test of its own, in `test_cases.py`, which is where
that answer belongs.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
CASES = REPO / "cases"


def case_text(name: str) -> str:
    """The case file as written, or a skip naming it."""
    path = CASES / f"{name}.yaml"
    if not path.is_file():
        raise unittest.SkipTest(f"cases/{name}.yaml is not in this checkout")
    return path.read_text()


def spec(name: str) -> dict:
    """A worked case as a spec, skipped when the file on disk cannot be used.

    A skip rather than an error: the test has no fixture, which is not the
    same as the code being wrong, and `test_cases.py` says so once instead of
    every test that reads this file saying it again.
    """
    try:
        parsed = yaml.safe_load(case_text(name))
    except yaml.YAMLError as broken:
        raise unittest.SkipTest(
            f"cases/{name}.yaml is not valid YAML ({broken}); "
            f"restore it with `git restore cases/{name}.yaml`"
        ) from None
    from aicfd import model as m

    try:
        m.build_model(parsed)
    except (ValueError, KeyError) as refused:
        raise unittest.SkipTest(
            f"cases/{name}.yaml does not build ({refused}); this is the case "
            f"on disk, not the code -- fix the value the message names, or "
            f"restore it with `git restore cases/{name}.yaml`"
        ) from None
    return parsed


#: Cases the tests own, seeded into every sandbox. Not `cases/`: those belong
#: to whoever is using the software, and a test that reads them as a fixture
#: fails when they use it.
FIXTURES = Path(__file__).resolve().parent / "cases"


def sandbox(test: unittest.TestCase) -> Path:
    """Point the server at a throwaway copy of the fixture cases.

    A test that saves must not save into the folder the user keeps their work
    in, even if it puts it back afterwards: a suite interrupted between the
    two leaves the case as the test left it. And it must not depend on what
    is in that folder either, which is why the sandbox is seeded from
    `tests/cases/` rather than from `cases/` (ADR-056).
    """
    from aicfd import server

    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    copy = Path(tmp.name) / "cases"
    shutil.copytree(FIXTURES, copy)
    original = server.CASES_DIR
    server.CASES_DIR = copy
    test.addCleanup(lambda: setattr(server, "CASES_DIR", original))
    return copy
