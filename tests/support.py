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

#: The engineer's working folder. Only `test_cases.py` looks in here, and only
#: to ask whether what is shipped still builds.
CASES = REPO / "cases"

#: The cases THIS REPOSITORY ships, by name.
#:
#: `cases/` is the engineer's working folder (ADR-056): the page writes to it,
#: and a case someone drops in to look at a room of their own is the software
#: doing its job. A guard that walks the folder holds THEIR file to a promise
#: this repository made about ITS files -- which has broken `docker build`
#: three times now (ADR-056, ADR-061, ADR-077). So a guard names what it
#: guards, and this is the list.
SHIPPED_CASES = (
    "hall-10mw",
    "hall-double-gallery",
    "hall-hotrow-independent",
    "hall-hotrow-team",
    "pod-fanwall",
    "pod-mesh",
    "pod-plenum",
    "pod-raised-floor",
    "pod-uneven-row",
)

#: Cases the tests own: what every other test reads, and what a sandbox is
#: seeded from. Not `cases/`, because those belong to whoever is using the
#: software and a test that reads them fails when they use it (ADR-056).
FIXTURES = Path(__file__).resolve().parent / "cases"


def spec(name: str) -> dict:
    """A case the TESTS own, as a spec.

    Never `cases/`. Reading the working folder was decoupled once already, for
    a case edited into something that would not build (ADR-056) -- but a case
    edited into something that builds perfectly well breaks a test just as
    hard, and more confusingly: an engineer sets a few rack loads through the
    page, which is the page doing its job, and the image stops building
    because a test asserted that this hall states no overrides. A fixture the
    user cannot edit is the only version of this that holds.
    """
    path = FIXTURES / f"{name}.yaml"
    if not path.is_file():
        raise AssertionError(
            f"tests/cases/{name}.yaml is missing -- a test asked for a case "
            f"the suite does not own. Copy the shipped case into it rather "
            f"than reading `cases/` (ADR-056)."
        )
    return yaml.safe_load(path.read_text())


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
