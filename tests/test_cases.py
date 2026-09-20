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

import copy
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


class TypicalRowTest(unittest.TestCase):
    """A row is a pattern of positions, not a count of identical cabinets.

    Three things a position can now say for itself: how wide it is, what it
    carries, and whether it is a cabinet at all (ADR-074).
    """

    def build(self, **racks):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["racks"].update(racks)
        return m.build_model(spec)

    def widths(self, model):
        return [round(r.box.size[0], 3) for r in model.rows[0].racks]

    def blanks(self, model):
        return [p for p in model.panels if p.name.startswith("blank_")]

    def test_no_row_is_the_count_of_standard_cabinets(self):
        model = self.build()
        self.assertEqual(len(model.rows[0].racks), 3)
        self.assertEqual(self.blanks(model), [])

    def test_a_position_can_be_wider_than_the_standard(self):
        model = self.build(row=[{}, {"width": 0.8}, {}])
        self.assertEqual(self.widths(model), [0.6, 0.8, 0.6])

    def test_a_position_can_carry_its_own_load(self):
        model = self.build(row=[{}, {"load_kw": 12.0}, {}])
        self.assertEqual([r.load_kw for r in model.rows[0].racks], [6.0, 12.0, 6.0])

    def test_a_blank_is_a_plate_and_not_a_cabinet(self):
        model = self.build(row=[{}, {}, {"blank": True, "width": 0.4}])
        self.assertEqual(len(model.rows[0].racks), 2)
        plates = self.blanks(model)
        self.assertEqual(len(plates), 1)
        plate = plates[0]
        self.assertEqual(plate.kind, "wall")
        self.assertAlmostEqual(plate.extent[0][1] - plate.extent[0][0], 0.4, places=2)
        # Floor to rack height, facing the cold aisle: the path a cabinet
        # would have taken is closed and nothing else is.
        self.assertAlmostEqual(plate.extent[1][0], 0.0)
        self.assertAlmostEqual(plate.extent[1][1], 2.2, places=2)

    def test_a_blank_carries_no_load_and_no_air(self):
        model = self.build(row=[{}, {}, {"blank": True}])
        self.assertAlmostEqual(model.total_load_w / 1000.0, 12.0)

    def test_the_row_is_as_long_as_its_parts(self):
        """Not count x width. A blank or a wider cabinet used to leave the
        containment running past the row's end."""
        model = self.build(row=[{}, {"width": 1.0}, {"blank": True, "width": 0.4}])
        racks = model.rows[0].racks
        plate = self.blanks(model)[0]
        self.assertAlmostEqual(plate.extent[0][0], racks[-1].box.hi[0], places=2)
        span = plate.extent[0][1] - racks[0].box.lo[0]
        self.assertAlmostEqual(span, 0.6 + 1.0 + 0.4, places=2)

    def test_identical_widths_snap_identically(self):
        """Two 0.8 m cabinets on a 0.6 m cell came out 0.60 and 1.20, because
        the snapper moved each face and the error walked down the row. The
        width is rounded now, so a width is a width."""
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["mesh"]["cell_size"] = [0.6, 0.2, 0.1]
        spec["racks"]["row"] = [{"width": 0.8}, {"width": 0.8}, {"width": 0.8}]
        widths = self.widths(m.build_model(spec))
        self.assertEqual(len(set(widths)), 1, widths)

    def test_it_says_which_width_the_mesh_moved(self):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["mesh"]["cell_size"] = [0.6, 0.2, 0.1]
        spec["racks"]["row"] = [{"width": 0.8}] * 3
        said = [w for w in m.build_model(spec).warnings if "fall between" in w]
        self.assertEqual(len(said), 1, "said once per width, not once per cabinet")
        self.assertIn("0.800 m wide", said[0])

    def test_a_per_position_width_overrides_the_pattern(self):
        model = self.build(row=[{}, {"width": 0.8}, {}], widths={"R3": 1.0})
        self.assertEqual(self.widths(model), [0.6, 0.8, 1.0])

    def test_a_per_position_blank_overrides_the_pattern(self):
        model = self.build(blanks=["R1"])
        self.assertEqual([r.id for r in model.rows[0].racks], ["R2", "R3"])
        self.assertEqual(len(self.blanks(model)), 1)

    def test_a_malformed_position_is_refused_by_name(self):
        for bad, why in (([{"colour": "red"}], "colour"),
                         (["a cabinet"], "mapping")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError) as caught:
                    self.build(row=bad)
                self.assertIn(why, str(caught.exception))

    def test_a_hall_row_takes_the_pattern_too(self):
        spec = copy.deepcopy(support.spec("hall-double-gallery"))
        spec["racks"]["row"] = [{}] * 4 + [{"blank": True, "width": 0.6}]
        model = m.build_model(spec)
        self.assertTrue(model.rows)
        for row in model.rows:
            self.assertEqual(len(row.racks), 4)
        self.assertEqual(len(self.blanks(model)), len(model.rows))


class RackTypeTest(unittest.TestCase):
    """The catalogue: what a cabinet is, kept out of the case (ADR-075)."""

    def build(self, **racks):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["racks"].update(racks)
        return m.build_model(spec)

    def test_every_shipped_type_parses(self):
        from aicfd import racklib

        self.assertTrue(racklib.available())
        for type_id in racklib.available():
            with self.subTest(type=type_id):
                rack = racklib.load(type_id)
                self.assertEqual(rack.id, type_id)
                self.assertTrue(rack.source, "a type says where it came from")
                self.assertIn(rack.cooling, ("air", "liquid"))

    def test_a_case_can_take_its_standard_from_a_type(self):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["racks"].pop("size")
        spec["racks"]["type"] = "generic-800-1200-46u"
        rack = m.build_model(spec).rows[0].racks[0]
        self.assertAlmostEqual(rack.box.size[0], 0.8, places=2)

    def test_what_the_case_states_still_wins(self):
        rack = self.build(type="generic-800-1200-46u",
                          size=[0.6, 1.2, 2.2]).rows[0].racks[0]
        self.assertAlmostEqual(rack.box.size[0], 0.6, places=2)

    def test_a_position_can_name_a_type(self):
        model = self.build(row=[{"type": "generic-800-1200-46u"}, {}, {}])
        widths = [round(r.box.size[0], 2) for r in model.rows[0].racks]
        self.assertEqual(widths[0], 0.8)
        self.assertEqual(widths[1], 0.6)

    def test_an_air_type_brings_its_own_load(self):
        model = self.build(row=[{"type": "shuffle-box-600-1200-48u"}, {}, {}])
        self.assertEqual(model.rows[0].racks[0].load_kw, 0.0)
        self.assertEqual(model.rows[0].racks[1].load_kw, 6.0)

    def test_a_liquid_types_air_load_is_the_share_that_is_not_coolant(self):
        """Its rated duty is the cabinet's, not what reaches the room's air.
        225 kW with 96% into the coolant is 9 kW of air load, and that is the
        document's own arithmetic rather than anybody's assumption."""
        model = self.build(row=[{"type": "type-e-liquid-225kw"}, {}, {}])
        self.assertAlmostEqual(model.rows[0].racks[0].load_kw, 9.0, places=2)

    def test_and_never_its_rated_duty_nor_the_halls_standard(self):
        model = self.build(row=[{"type": "type-e-liquid-225kw"}, {}, {}])
        carried = model.rows[0].racks[0].load_kw
        self.assertNotAlmostEqual(carried, 225.0)
        self.assertNotAlmostEqual(carried, 6.0)   # the hall's air standard

    def test_a_stated_load_still_wins_over_the_derived_share(self):
        model = self.build(row=[{"type": "type-e-liquid-225kw", "load_kw": 12.0},
                                {}, {}])
        self.assertAlmostEqual(model.rows[0].racks[0].load_kw, 12.0)

    def test_a_liquid_type_that_states_no_fraction_is_refused(self):
        """Then there really is nothing to go on, and a guess would be the
        difference between 9 kW and 225."""
        from aicfd import racklib

        real = racklib.resolve

        def without(type_id):
            rack = real(type_id)
            return rack.__class__(**{**rack.__dict__, "liquid_fraction": None})

        racklib.resolve = without
        self.addCleanup(lambda: setattr(racklib, "resolve", real))
        with self.assertRaises(ValueError) as caught:
            self.build(row=[{"type": "type-e-liquid-225kw"}, {}, {}])
        self.assertIn("leaves in the coolant", str(caught.exception))

    def test_an_incomplete_type_is_refused_by_name(self):
        """Its own document marks the depth 'further confirmation'. A number
        invented to fill the field is a row that does not fit."""
        with self.assertRaises(ValueError) as caught:
            self.build(row=[{"type": "liquid-network-800"}])
        self.assertIn("does not state its depth", str(caught.exception))

    def test_an_unknown_type_lists_what_there_is(self):
        from aicfd import racklib

        with self.assertRaises(racklib.UnknownRackType) as caught:
            racklib.load("no-such-cabinet")
        self.assertIn("generic-600-1200-45u", str(caught.exception))

    def test_the_type_e_pitch_is_the_605_not_the_600(self):
        """The RFP says the 605 mm on the drawings is 600 of rack body plus 5
        of engineering tolerance. A row is laid out on the pitch."""
        from aicfd import racklib

        self.assertAlmostEqual(racklib.load("type-e-liquid-225kw").size[0], 0.605)
