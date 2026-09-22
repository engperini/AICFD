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

    def test_every_shipped_case_builds(self):
        """The ten cases the repository GUARANTEES. A failure here is the
        repository's, and it stops a release."""
        for name in support.SHIPPED_CASES:
            path = support.CASES / f"{name}.yaml"
            with self.subTest(case=path.name):
                if not path.is_file():
                    self.skipTest(f"cases/{path.name} is not in this clone")
                self.builds(path, shipped=True)

    def test_a_case_of_your_own_is_reported_and_does_not_stop_the_build(self):
        """ANYTHING ELSE in the folder is the engineer's own work, and it is
        not the suite's business whether it is finished.

        A draft of somebody's next project stopped `docker build` with a
        message about their own cold aisle -- the image, the tests and the
        release, blocked by a file nobody had finished writing. The folder is
        theirs (ADR-056): a case of their own that does not build is SAID,
        once, and skipped (ADR-108).
        """
        mine = {f"{name}.yaml" for name in support.SHIPPED_CASES}
        theirs = [p for p in sorted(support.CASES.glob("*.yaml"))
                  if p.name not in mine]
        if not theirs:
            self.skipTest("no cases of your own in this clone")
        refused = []
        for path in theirs:
            try:
                m.build_model(yaml.safe_load(path.read_text()))
            except Exception as why:  # noqa: BLE001 -- reported, not handled
                refused.append(f"cases/{path.name}: {why}")
        if refused:
            self.skipTest(
                "a case of your own does not build -- the software is fine, "
                "the file is not: " + "; ".join(refused))

    def builds(self, path, shipped: bool = False):
        try:
            spec = yaml.safe_load(path.read_text())
        except yaml.YAMLError as broken:
            self.fail(f"cases/{path.name} is not valid YAML: {broken}")
        try:
            m.build_model(spec)
        except Exception as refused:  # noqa: BLE001 -- reported, not handled
            # `from None`: the reader needs the sentence, not the generator's
            # stack.
            self.fail_clean(
                f"cases/{path.name} does not build: {refused}\n"
                f"        This is the case file, not the code. Fix the "
                f"value the message names, or put the file back with\n"
                f"            git restore cases/{path.name}"
            )


class YourOwnCaseNeverStopsTheBuildTest(unittest.TestCase):
    """Proved against a draft the suite writes, not against what is on disk.

    A case of somebody's own that does not build stopped `docker build` --
    the image, the tests and the release, held up by a file nobody had
    finished writing (ADR-108). It has to be a SKIP that says what it found.
    """

    DRAFT = (
        "name: zz-draft\n"
        "pods: 2\n"
        "aisles: {cold: 1.2, hot: 1.2, perimeter: 1.8}\n"
        "racks: {per_row: 4, load_kw: 8.0, size: [0.6, 1.2, 2.2]}\n"
        "hall: {height: 5.0, ceiling: 3.5}\n"
        "gallery: {depth: 3.0}\n"
        "grilles: {size: 0.6}\n"
        "fanwall: {model: NO-SUCH-UNIT}\n"     # refused by name
    )

    def run_the_folder_test(self, folder) -> unittest.TestResult:
        import unittest.mock

        case = WorkedCasesTest(
            "test_a_case_of_your_own_is_reported_and_does_not_stop_the_build")
        result = unittest.TestResult()
        with unittest.mock.patch.object(support, "CASES", folder):
            case.run(result)
        return result

    def test_it_is_skipped_and_the_reason_names_the_file(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "zz-draft.yaml").write_text(self.DRAFT)
            result = self.run_the_folder_test(folder)
        self.assertEqual(result.failures, [], "a draft of your own failed the suite")
        self.assertEqual(result.errors, [])
        self.assertTrue(result.skipped, "nothing was said about it at all")
        said = result.skipped[0][1]
        self.assertIn("zz-draft", said)
        self.assertIn("the software is fine", said)

    def test_a_folder_of_good_drafts_passes(self):
        import tempfile
        from pathlib import Path

        import yaml as y

        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            spec = support.spec("pod-fanwall")
            (folder / "zz-fine.yaml").write_text(y.safe_dump(spec))
            result = self.run_the_folder_test(folder)
        self.assertEqual(result.failures, [])
        self.assertFalse(result.skipped, f"skipped a case that builds: {result.skipped}")


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
        for type_id in racklib.SHIPPED:
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
        self.assertIn("does not state its depth, height", str(caught.exception))

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


class Sum3RackTest(unittest.TestCase):
    """The SUM3 lease's own cabinets, as rack types (ADR-075)."""

    def build(self, **racks):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["racks"].update(racks)
        return m.build_model(spec)

    def test_the_three_are_in_the_catalogue(self):
        from aicfd import racklib

        for type_id, size in (
            ("sum3-high-density", [1.800, 1.800, 2.600]),
            ("sum3-low-density-compute", [1.200, 1.800, 2.600]),
            ("sum3-low-density-network", [1.200, 1.800, 2.600]),
        ):
            with self.subTest(type=type_id):
                self.assertEqual(list(racklib.load(type_id).size), size)

    def test_low_density_is_air_cooled_end_to_end(self):
        """The lease provides low density rows for 100% air cooling, so the
        cabinet's whole duty is what the room removes."""
        from aicfd import racklib

        for type_id, load in (("sum3-low-density-compute", 54.0),
                              ("sum3-low-density-network", 34.0)):
            with self.subTest(type=type_id):
                rack = racklib.load(type_id)
                self.assertEqual(rack.cooling, "air")
                self.assertEqual(rack.load_kw, load)
        model = self.build(row=[{"type": "sum3-low-density-compute"},
                                {"type": "sum3-low-density-network"}, {}])
        self.assertEqual([r.load_kw for r in model.rows[0].racks],
                         [54.0, 34.0, 6.0])

    def test_the_gpu_rack_states_no_split_so_it_is_refused(self):
        """The lease gives the liquid/air ratio per COLO, never per rack. At
        2,200 kW the gap between a guess and the truth is megawatts."""
        from aicfd import racklib

        rack = racklib.load("sum3-high-density")
        self.assertEqual(rack.cooling, "liquid")
        self.assertIsNone(rack.liquid_fraction)
        with self.assertRaises(ValueError) as caught:
            self.build(row=[{"type": "sum3-high-density"}, {}, {}])
        self.assertIn("2200 kW leaves in the coolant", str(caught.exception))

    def test_the_gpu_rack_builds_once_its_air_load_is_stated(self):
        model = self.build(row=[{"type": "sum3-high-density", "load_kw": 176.0},
                                {}, {}])
        self.assertEqual(model.rows[0].racks[0].load_kw, 176.0)
        self.assertAlmostEqual(model.rows[0].racks[0].box.size[0], 1.8, places=1)

    def test_a_deeper_type_at_a_position_says_only_its_width_is_taken(self):
        """A row is one band across the hall. Mixing depths inside it is
        geometry this model does not build, and dropping the number in
        silence is how a 1800 mm cabinet ends up drawn 1200 deep."""
        said = [w for w in self.build(
            row=[{"type": "sum3-low-density-compute"}, {}, {}]).warnings
            if "row has one depth" in w]
        self.assertEqual(len(said), 2, said)       # depth and height
        self.assertIn("1.800 m deep", said[0])
        self.assertIn("racks.type", said[0])

    def test_and_as_the_standard_it_sizes_the_whole_row(self):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["racks"].pop("size")
        spec["racks"]["type"] = "sum3-low-density-compute"
        box = m.build_model(spec).rows[0].racks[0].box
        self.assertAlmostEqual(box.size[1], 1.8, places=1)
        self.assertAlmostEqual(box.size[2], 2.6, places=1)


class RfpCabinetTest(unittest.TestCase):
    """The Fortaleza / Queretaro cabinets: a footprint and nothing else."""

    def test_both_are_in_the_catalogue_with_their_footprints(self):
        from aicfd import racklib

        for type_id, w, d in (("meta-600-1200", 0.600, 1.200),
                              ("meta-800-1300", 0.800, 1.300)):
            with self.subTest(type=type_id):
                rack = racklib.load(type_id)
                self.assertAlmostEqual(rack.size[0], w)
                self.assertAlmostEqual(rack.size[1], d)

    def test_the_height_is_the_engineers_and_the_file_says_so(self):
        """2200 mm did not come from the RFP, which gives a footprint and
        nothing else. A number somebody supplied has to be traceable to them
        rather than read later as the customer's."""
        from aicfd import racklib

        for type_id in ("meta-600-1200", "meta-800-1300"):
            with self.subTest(type=type_id):
                rack = racklib.load(type_id)
                self.assertAlmostEqual(rack.size[2], 2.200)
                self.assertTrue(rack.complete)
                text = (racklib.LIBRARY / f"{type_id}.yaml").read_text()
                self.assertIn("set by the engineer", text)
                self.assertIn("RFP states no height", text)

    def test_neither_states_a_per_rack_load(self):
        """The RFP gives a cage total across a mix of both cabinets and never
        splits it. The averages -- 7.73 and 7.39 kW -- are over that mix, so
        they belong to a case and not to either type."""
        from aicfd import racklib

        for type_id in ("meta-600-1200", "meta-800-1300"):
            with self.subTest(type=type_id):
                self.assertIsNone(racklib.load(type_id).load_kw)

    def test_the_mix_accounts_for_the_whole_cage(self):
        """54 + 21 = 75 and 95 + 20 = 115, so there is no third cabinet."""
        for counts, cage in (((54, 21), 75), ((95, 20), 115)):
            with self.subTest(cage=cage):
                self.assertEqual(sum(counts), cage)

    def test_it_is_deeper_than_the_other_800_wide_cabinets(self):
        """The generic 800s are 1200 deep, so a row of these is 100 mm deeper
        and does not drop into the same band."""
        from aicfd import racklib

        for other in ("generic-800-1200-46u", "generic-800-1200-48u"):
            with self.subTest(other=other):
                self.assertAlmostEqual(racklib.load(other).size[1], 1.200)
        self.assertAlmostEqual(racklib.load("meta-800-1300").size[1], 1.300)


class RaisedFloorTest(unittest.TestCase):
    """The room on an access floor, and the plenum under it (ADR-076)."""

    def spec(self, **floor):
        spec = copy.deepcopy(support.spec("pod-fanwall"))
        spec["fanwall"]["model"] = "HDCV5300F-HT"
        spec["floor"] = {"enabled": True, "height": 1.0,
                         "tiles_per_rack": 2, **floor}
        return spec

    def build(self, **floor):
        return m.build_model(self.spec(**floor))

    def named(self, model, prefix):
        return [p for p in model.panels if p.name.startswith(prefix)]

    def test_the_room_is_unchanged_and_the_building_is_taller(self):
        flat = m.build_model(support.spec("pod-fanwall"))
        up = self.build()
        lift = 1.0
        self.assertAlmostEqual(up.domain.hi[2], flat.domain.hi[2] + lift, places=2)
        self.assertAlmostEqual(up.ceiling_z, flat.ceiling_z + lift, places=2)
        # The room itself: the cabinet is the same cabinet, a storey higher.
        a, b = flat.racks[0].box, up.racks[0].box
        self.assertAlmostEqual(b.lo[2], a.lo[2] + lift, places=2)
        self.assertAlmostEqual(b.size[2], a.size[2], places=2)
        self.assertAlmostEqual(up.ceiling_z - b.hi[2], flat.ceiling_z - a.hi[2],
                               places=2)

    def test_the_aisles_do_not_move(self):
        flat = m.build_model(support.spec("pod-fanwall"))
        up = self.build()
        self.assertEqual(up.cold_aisles, flat.cold_aisles)
        self.assertEqual(up.hot_aisles, flat.hot_aisles)

    def test_the_deck_covers_the_gallery_too(self):
        """The units stand on it."""
        deck = self.named(self.build(), "floor_deck")[0]
        self.assertEqual(deck.kind, "wall")
        self.assertEqual(deck.axis, 2)
        self.assertAlmostEqual(deck.position, 1.0, places=2)
        model = self.build()
        (x0, x1), _ = deck.extent
        self.assertAlmostEqual(x0, model.domain.lo[0], places=2)
        self.assertAlmostEqual(x1, model.domain.hi[0], places=2)

    def test_the_opening_below_mirrors_the_one_above(self):
        """Same wall, same width, same mesh -- the plant pays for it twice."""
        model = self.build()
        below = self.named(model, "floor_opening")[0]
        above = self.named(model, "plenum_opening")[0]
        self.assertEqual(below.axis, above.axis)
        self.assertAlmostEqual(below.position, above.position, places=6)
        self.assertEqual(below.extent[0], above.extent[0])      # full width
        self.assertAlmostEqual(below.resistance, above.resistance, places=6)
        self.assertEqual((below.extent[1][0], below.extent[1][1]), (0.0, 1.0))

    def test_two_plates_stand_in_front_of_every_cabinet(self):
        model = self.build()
        tiles = self.named(model, "tile_")
        self.assertEqual(len(tiles), 2 * len(model.racks))
        for tile in tiles:
            self.assertEqual(tile.axis, 2)
            self.assertAlmostEqual(tile.position, 1.0, places=2)
            self.assertIsNotNone(tile.resistance)

    def test_a_plate_is_as_wide_as_the_cabinet_in_front_of_it(self):
        model = self.build()
        rack = model.racks[0]
        mine = [p for p in model.panels if p.name.startswith(f"tile_{rack.id}_")]
        for tile in mine:
            (x0, x1), _ = tile.extent
            self.assertAlmostEqual(x0, rack.box.lo[0], places=2)
            self.assertAlmostEqual(x1, rack.box.hi[0], places=2)

    def test_the_plates_lie_in_the_cold_aisle(self):
        model = self.build()
        cold = model.cold_aisles[0]
        for tile in self.named(model, "tile_"):
            _, (y0, y1) = tile.extent
            self.assertGreaterEqual(y0 + 1e-6, cold[0])
            self.assertLessEqual(y1 - 1e-6, cold[1])

    def test_the_count_is_what_the_case_sets(self):
        self.assertEqual(len(self.named(self.build(tiles_per_rack=3), "tile_")),
                         3 * 3)
        self.assertEqual(self.named(self.build(tiles_per_rack=0), "tile_"), [])

    def test_a_position_can_say_its_own_count(self):
        spec = self.spec()
        spec["racks"]["tiles"] = {"R2": 0}
        model = m.build_model(spec)
        self.assertEqual([p.name for p in model.panels
                          if p.name.startswith("tile_R2_")], [])
        self.assertEqual(len([p for p in model.panels
                              if p.name.startswith("tile_R1_")]), 2)

    def test_the_unit_keeps_its_place_and_gains_a_second_face(self):
        model = self.build()
        self.assertEqual(len(model.fans), 1, "one unit, not two panels")
        unit = model.fans[0]
        self.assertEqual(unit.axis, 2)
        self.assertAlmostEqual(unit.position, 1.0, places=2)
        self.assertIsNotNone(unit.return_z)
        self.assertGreater(unit.return_z, unit.position)
        # It reaches back into the gallery from the wall the fan wall stood in.
        (x0, x1), _ = unit.extent
        self.assertLessEqual(x1, model.hall.lo[0] + 1e-6)

    def test_the_unit_moves_what_one_unit_moves(self):
        """Two panels per unit would have halved this and nothing would say so."""
        flat = m.build_model(support.spec("pod-fanwall"))
        self.assertAlmostEqual(self.build().unit_airflow_m3h,
                               flat.unit_airflow_m3h, places=3)

    def test_a_raised_floor_and_a_supply_plenum_are_exclusive(self):
        spec = self.spec()
        spec["plenum"] = {"enabled": True}
        with self.assertRaises(ValueError) as caught:
            m.build_model(spec)
        self.assertIn("Choose one", str(caught.exception))

    def test_the_depth_is_held_to_a_range(self):
        for bad in (0.1, 4.0):
            with self.subTest(height=bad):
                with self.assertRaises(ValueError):
                    self.build(height=bad)
