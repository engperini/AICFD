"""Tests for the minimal polyMesh reader.

It exists to answer one question -- which side of a baffle pair faces the data
hall -- so these check it reads a hand-written mesh the way OpenFOAM would.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from aicfd.foam import polymesh

HEADER = """FoamFile
{{
    version     2.0;
    format      ascii;
    class       {cls};
    location    "constant/polyMesh";
    object      {obj};
}}
"""

# A unit cube: 8 points, one face written twice with opposite winding so the
# test can tell a +z normal from a -z one.
POINTS = """(0 0 0)
(1 0 0)
(1 1 0)
(0 1 0)
(0 0 1)
(1 0 1)
(1 1 1)
(0 1 1)"""

FACES = """4(0 3 2 1)
4(4 5 6 7)"""


class ReaderTest(unittest.TestCase):
    def setUp(self):
        self.case = Path(tempfile.mkdtemp())
        mesh = self.case / "constant/polyMesh"
        mesh.mkdir(parents=True)
        (mesh / "points").write_text(
            HEADER.format(cls="vectorField", obj="points") + f"8\n(\n{POINTS}\n)\n"
        )
        (mesh / "faces").write_text(
            HEADER.format(cls="faceList", obj="faces") + f"2\n(\n{FACES}\n)\n"
        )
        (mesh / "boundary").write_text(
            HEADER.format(cls="polyBoundaryMesh", obj="boundary")
            + """2
(
    down
    {
        type            wall;
        nFaces          1;
        startFace       0;
    }
    up
    {
        type            patch;
        nFaces          1;
        startFace       1;
    }
)
"""
        )

    def test_it_reads_the_patches_and_their_offsets(self):
        boundary = polymesh.read_boundary(self.case)
        self.assertEqual(set(boundary), {"down", "up"})
        self.assertEqual(boundary["up"]["startFace"], 1)
        self.assertEqual(boundary["down"]["type"], "wall")

    def test_the_outward_normal_follows_the_face_winding(self):
        self.assertEqual(polymesh.patch_normal(self.case, "down"), (0.0, 0.0, -1.0))
        self.assertEqual(polymesh.patch_normal(self.case, "up"), (0.0, 0.0, 1.0))

    def test_it_reads_only_the_points_it_is_asked_for(self):
        points = polymesh.read_points(self.case, wanted={0, 6})
        self.assertEqual(set(points), {0, 6})
        self.assertEqual(points[6], (1.0, 1.0, 1.0))

    def test_the_header_is_not_mistaken_for_data(self):
        """'version 2.0' and 'format ascii' sit right above the point list."""
        self.assertEqual(len(polymesh.read_points(self.case)), 8)

    def test_an_unknown_patch_says_so(self):
        with self.assertRaises(KeyError):
            polymesh.patch_normal(self.case, "sideways")


if __name__ == "__main__":
    unittest.main()
