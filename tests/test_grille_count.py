"""The ceiling counted in its own modules, and the floor's plate rows.

`grilles.coverage` is a fraction of the row: the right input for an area and
the wrong one for a count. A reflected ceiling plan says *three 600 x 600
across this aisle*, and that was not sayable -- though the floor has had a
count since the raised floor existed (ADR-106).

The floor had its own gap: `tiles_per_rack` is laid outward from each cabinet
face and two rows face one aisle, so the aisle held an EVEN number. Three
plates across an 1,80 m aisle needed a key of its own.
"""

from __future__ import annotations

import copy
import unittest

import yaml

from aicfd import model as m
from tests import support


def hall(**changes) -> dict:
    spec = copy.deepcopy(support.spec("hall-cage"))
    for section, values in changes.items():
        spec.setdefault(section, {}).update(values)
    return spec


def opening(spec: dict):
    built = m.build_model(spec)
    return built, next(p for p in built.panels if p.name.startswith("grille"))


class CountingNothingChangesNothingTest(unittest.TestCase):
    """Every case in the repository is drawn with the coverage strip."""

    def test_the_strip_is_what_a_case_gets_without_a_count(self):
        _built, strip = opening(hall())
        self.assertAlmostEqual(strip.extent[1][1] - strip.extent[1][0], 1.2,
                               places=6, msg="the strip spans the whole aisle")

    def test_stating_a_count_does_not_move_the_other_direction(self):
        _b, strip = opening(hall())
        _c, counted = opening(hall(grilles={"across": 2}))
        self.assertEqual(counted.extent[0], strip.extent[0],
                         "counting across the aisle changed the length")


class AcrossTheAisleTest(unittest.TestCase):
    """What the reader asked for: three grilles over an 1,80 m aisle."""

    def test_three_modules_fill_an_eighteen_hundred_aisle(self):
        _built, grille = opening(
            hall(aisles={"hot": 1.8}, grilles={"across": 3}))
        self.assertAlmostEqual(grille.extent[1][1] - grille.extent[1][0], 1.8,
                               places=6)

    def test_fewer_modules_than_the_aisle_holds_sit_in_the_middle_of_it(self):
        """Centred, to within the cell: the block is rounded onto the mesh
        first, because an opening between cell faces is snapped and stops
        being the count it was (ADR-106)."""
        built, grille = opening(hall(aisles={"hot": 1.8}, grilles={"across": 2}))
        aisle = built.hot_aisles[0]
        lo, hi = grille.extent[1]
        cell = built.cell(1)
        self.assertAlmostEqual(hi - lo, 1.2, places=6)
        self.assertGreaterEqual(lo + 1e-9, aisle[0])
        self.assertLessEqual(hi - 1e-9, aisle[1])
        self.assertLessEqual(abs((lo - aisle[0]) - (aisle[1] - hi)), cell + 1e-9,
                             "the modules are more than a cell off centre")

    def test_a_single_module_keeps_its_own_width(self):
        """Centred and left there it landed between cell faces and came out
        400 mm wide after snapping (ADR-106)."""
        _built, grille = opening(hall(grilles={"across": 1}))
        self.assertAlmostEqual(grille.extent[1][1] - grille.extent[1][0], 0.6,
                               places=6)

    def test_more_modules_than_fit_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            opening(hall(grilles={"across": 4}))
        said = str(caught.exception)
        self.assertIn("grilles.across", said)
        self.assertIn("1.20 m aisle holds 2", said)

    def test_the_summary_says_how_many(self):
        built, _g = opening(hall(aisles={"hot": 1.8}, grilles={"across": 3}))
        row = next(r for r in m.summary_rows(built)
                   if r[0].startswith("Return grilles"))
        self.assertIn("3 across", row[1])
        self.assertIn("0.60 m", row[1])


class AlongTheRowTest(unittest.TestCase):
    def test_counting_along_needs_a_mesh_that_can_hold_it(self):
        """A 600 mm module on a 0,40 m cell is a module and a half: the
        opening would be snapped back and the count would be a fiction."""
        with self.assertRaises(ValueError) as caught:
            opening(hall(grilles={"along": 17}))
        said = str(caught.exception)
        self.assertIn("grilles.along", said)
        self.assertIn("1.50 cells", said)
        self.assertIn("0.30", said, "the refusal has to say what would work")

    def test_on_a_mesh_that_can_it_is_exact(self):
        _built, grille = opening(
            hall(grilles={"along": 17}, mesh={"cell_size": [0.3, 0.2, 0.2]}))
        self.assertAlmostEqual(grille.extent[0][1] - grille.extent[0][0],
                               17 * 0.6, places=6)

    def test_more_modules_than_the_row_holds_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            opening(hall(grilles={"along": 99},
                         mesh={"cell_size": [0.3, 0.2, 0.2]}))
        self.assertIn("grilles.along", str(caught.exception))


class PlateRowsAcrossTheAisleTest(unittest.TestCase):
    """Three plates between two rows, which an even count cannot give."""

    def plates_in_the_middle_aisle(self, **floor):
        spec = hall(aisles={"cold": 1.8}, floor=floor, cage={"aisle": 1.8})
        built = m.build_model(spec)
        band = built.cold_aisles[2]
        return built, sorted({(round(p.extent[1][0], 3), round(p.extent[1][1], 3))
                              for p in built.panels
                              if p.name.startswith("tile_")
                              and band[0] - 1e-6 <= p.extent[1][0]
                              and p.extent[1][1] <= band[1] + 1e-6})

    def test_one_each_side_leaves_the_middle_of_the_aisle_bare(self):
        """The arrangement before the key existed, and a real one -- but not
        the only one a floor can have."""
        _built, rows = self.plates_in_the_middle_aisle(tiles_per_rack=1)
        self.assertEqual(len(rows), 2)

    def test_three_across_tiles_the_aisle(self):
        built, rows = self.plates_in_the_middle_aisle(tiles_across=3)
        self.assertEqual(len(rows), 3)
        band = built.cold_aisles[2]
        self.assertAlmostEqual(rows[0][0], band[0], places=6)
        self.assertAlmostEqual(rows[-1][1], band[1], places=6)
        for (_lo, hi), (lo2, _hi2) in zip(rows, rows[1:]):
            self.assertAlmostEqual(hi, lo2, places=6, msg="a gap in the floor")

    def test_the_two_rows_split_them(self):
        """Two from the row nearer y = 0 and one from the other, which is how
        a floor grid runs. A perimeter aisle has one row facing it, and that
        row lays all three."""
        built, _rows = self.plates_in_the_middle_aisle(tiles_across=3)
        band = built.cold_aisles[2]
        facing = sorted(
            (row for row in built.rows
             if min(abs(row.front_y - band[0]), abs(row.front_y - band[1])) < 1e-6),
            key=lambda row: row.front_y,
        )
        self.assertEqual(len(facing), 2, "two rows face a middle aisle")
        counted = []
        for row in facing:
            stems = {rack.id.replace("-", "_") for rack in row.racks}
            plates = [p for p in built.panels
                      if p.name.startswith("tile_")
                      and p.name[len("tile_"):].rsplit("_", 1)[0] in stems]
            counted.append(len(plates) // len(row.racks))
        self.assertEqual(counted, [2, 1],
                         "the odd plate row goes to the row nearer y = 0")

    def test_a_count_wider_than_the_aisle_is_clamped_and_said(self):
        """It used to be refused. A number the aisle cannot hold is now laid
        to fit, with a note -- because the count a case arrives with is often
        one nobody typed (ADR-108)."""
        built, rows = self.plates_in_the_middle_aisle(tiles_across=6)
        self.assertEqual(len(rows), 3, "an 1,80 m aisle holds three")
        said = " ".join(w for w in built.warnings if w.startswith("floor plates"))
        self.assertIn("6 were asked for and 3 laid", said)
        self.assertIn("floor.tiles_across: 3", said)

    def test_out_of_range_is_refused_by_name(self):
        with self.assertRaises(ValueError) as caught:
            m.build_model(hall(floor={"tiles_across": 0}))
        self.assertIn("floor.tiles_across", str(caught.exception))
