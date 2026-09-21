"""`docs/CASE-AUTHORING.md` is a manual an AGENT follows, so it has to be true.

A human reading a stale manual notices: the key is not in the file they are
editing, the range does not match the error they just got, and they go and
look. An agent handed the same manual has nothing to check it against. It
writes the key the manual named, the build refuses the case, and the agent has
been told by the repository itself to do the wrong thing.

So the manual is not prose that happens to describe the code -- it is held to
it here, in the four ways it can go out of date:

  a key it documents      must be one the software actually reads
  a key the software has  must be documented, or an agent never learns of it
  a range it quotes       must be the range the software enforces
  a name it cites         -- a check, a component role, a library file --
                          must exist

The manual's reference tables are the machine-readable part: a row whose first
cell is a backticked dotted path. Everything else in the file is prose and is
not parsed, which is what keeps this test about facts rather than about wording.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANUAL = REPO / "docs" / "CASE-AUTHORING.md"

#: Keys the software reads that the page does not offer, so they are not in
#: `EDITABLE` and have to be named here. Each is a real key with a real reader:
#: a name, a library reference, a per-position override or a coupling control.
BEYOND_THE_FORM = {
    "name",
    "racks.type",
    "racks.row",
    "racks.loads",
    "racks.widths",
    "racks.blanks",
    "fanwall.model",
    "fanwall.curve",
    "plenum.closed",
    "plenum.mesh_loss_coefficient",
    "components.floor_tile",
    "components.gallery_mesh",
    "solver.residual_tolerance",
    "solver.couple",
    "solver.coupling_passes",
    "solver.coupling_segment",
}

#: A reference-table row: a backticked key, then type, range and source. The
#: four columns are what separates it from the check table in section 11,
#: which is two columns of prose and is read by `NamesTest` instead.
KEY_ROW = re.compile(
    r"^\| `([a-z_][a-z_0-9.]*)` \|([^|]*)\|([^|]*)\|([^|]*)\|\s*$", re.M
)

#: The check table: a backticked name and one column saying what a FAIL means.
CHECK_ROW = re.compile(r"^\| `([a-z_]+)` \|([^|]*)\|\s*$", re.M)


def documented() -> dict[str, str]:
    """Every key the manual's reference tables document, to its range cell."""
    return {m[0]: m[2] for m in KEY_ROW.findall(MANUAL.read_text())}


def editable() -> dict[str, tuple]:
    from aicfd.server import EDITABLE

    return {".".join(path): (cast, rng) for path, cast, rng in EDITABLE.values()}


class KeysTest(unittest.TestCase):
    def test_the_manual_exists_where_it_says_it_does(self):
        self.assertTrue(MANUAL.exists(), f"{MANUAL} is gone")

    def test_every_key_the_software_offers_is_documented(self):
        missing = sorted(set(editable()) - set(documented()))
        self.assertFalse(
            missing,
            f"{missing} is a spec key the software reads and the manual does "
            "not document. An agent following the manual will never set it",
        )

    def test_the_manual_documents_no_key_the_software_does_not_read(self):
        known = set(editable()) | BEYOND_THE_FORM
        invented = sorted(set(documented()) - known)
        self.assertFalse(
            invented,
            f"{invented} is documented in the manual and read nowhere in the "
            "software. An agent will write it and the case will ignore it",
        )

    def test_every_key_outside_the_form_is_really_read(self):
        """`BEYOND_THE_FORM` is a list, so it can rot like any other list.

        A key retired from the software stays here, stays in the manual, and
        the two tests above keep passing while the manual teaches a key that
        does nothing (ADR-056, ADR-084).
        """
        source = "\n".join(
            (REPO / "aicfd" / name).read_text()
            for name in ("model.py", "cli.py", "server.py", "case.py",
                         "post.py", "components.py", "racklib.py")
        )
        for key in sorted(BEYOND_THE_FORM):
            leaf = key.rsplit(".", 1)[-1]
            with self.subTest(key=key):
                self.assertIn(
                    f'"{leaf}"', source,
                    f"the manual documents {key}, and nothing in aicfd/ reads "
                    f"{leaf!r} any more",
                )


class RangeTest(unittest.TestCase):
    """A range the manual quotes is one an agent will trust before building."""

    @staticmethod
    def number(value: float) -> str:
        return f"{value:g}"

    def test_every_range_matches_the_one_the_software_enforces(self):
        rows = documented()
        for key, (_cast, rng) in editable().items():
            if not (isinstance(rng, tuple) and len(rng) == 2
                    and isinstance(rng[0], (int, float))):
                continue
            if key not in rows:
                continue  # reported by KeysTest, and once is enough
            with self.subTest(key=key):
                said = rows[key]
                for bound in rng:
                    self.assertIn(
                        self.number(bound), said,
                        f"the manual's row for {key} does not carry the bound "
                        f"{self.number(bound)} the software enforces: {said!r}",
                    )


class NamesTest(unittest.TestCase):
    """Every name the manual cites has to be a name that exists."""

    def cited(self, pattern: str) -> set[str]:
        return set(re.findall(pattern, MANUAL.read_text()))

    def test_the_check_table_names_the_checks_the_solver_runs(self):
        source = (REPO / "aicfd" / "post.py").read_text()
        real = set(re.findall(r'Check\(\s*\n\s*"([a-z_]+)"', source))
        self.assertTrue(real, "no checks found in post.py; the pattern moved")

        listed = {name for name, _ in CHECK_ROW.findall(MANUAL.read_text())}
        self.assertFalse(
            real - listed,
            f"{sorted(real - listed)} is a check the solver runs and the "
            "manual's table does not explain. An agent reading a FAIL it names "
            "has nowhere to look",
        )
        self.assertFalse(
            listed - real,
            f"{sorted(listed - real)} is explained in the manual and is not a "
            "check the solver runs any more",
        )

    def test_every_component_role_it_lists_is_a_real_role(self):
        from aicfd.components import ROLES

        roles = self.cited(r"`(ceiling_return|supply_grille|floor_tile|"
                           r"gallery_mesh|containment|distribution_loss)`")
        self.assertTrue(roles, "the manual lists no component roles any more")
        self.assertFalse(
            roles - set(ROLES),
            f"{sorted(roles - set(ROLES))} is not a component role",
        )
        self.assertFalse(
            set(ROLES) - roles,
            f"{sorted(set(ROLES) - roles)} is a component role the manual "
            "never mentions",
        )

    def test_every_library_file_it_names_exists(self):
        text = MANUAL.read_text()
        for folder, pattern in (
            ("equipment", r"`equipment/([A-Za-z0-9-]+)\.yaml`"),
            ("racks", r"`racks/([a-z0-9-]+)\.yaml`"),
            ("components", r"`components/([a-z0-9-]+)\.yaml`"),
        ):
            for name in set(re.findall(pattern, text)):
                with self.subTest(file=f"{folder}/{name}"):
                    self.assertTrue(
                        (REPO / folder / f"{name}.yaml").exists(),
                        f"the manual cites {folder}/{name}.yaml, which is gone",
                    )
        for name in set(re.findall(r"`racks\.type: ([a-z0-9-]+)`|"
                                   r"\{type: ([a-z0-9-]+)\}", text)):
            for candidate in filter(None, name if isinstance(name, tuple) else (name,)):
                with self.subTest(rack_type=candidate):
                    self.assertTrue(
                        (REPO / "racks" / f"{candidate}.yaml").exists(),
                        f"the manual's example names rack type {candidate!r}, "
                        "which is not in racks/",
                    )

    def test_the_worked_example_it_sends_the_reader_to_is_shipped(self):
        for name in self.cited(r"`cases/([a-z0-9-]+)\.yaml`"):
            with self.subTest(case=name):
                self.assertTrue(
                    (REPO / "cases" / f"{name}.yaml").exists(),
                    f"the manual sends the reader to cases/{name}.yaml",
                )


class DerivationTest(unittest.TestCase):
    """Section 2's arithmetic has to be the arithmetic `_hall_layout` does.

    It is the most load-bearing thing in the manual. An author reads it
    BACKWARDS -- given a hall measured on a drawing, solve for the inputs --
    so a formula that is close but not exact sends them to a rack count or a
    clearance that was never the one the building has, and the case builds
    perfectly well at the wrong size.

    The check is the formula as the manual states it, evaluated against every
    shipped hall. Duplicating the arithmetic is the point: two independent
    statements of it that must agree, one of them the one an agent will read.
    """

    def test_the_manual_derivation_reproduces_every_shipped_hall(self):
        import yaml

        from aicfd import model as model_module

        from tests import support

        halls = 0
        for name in support.SHIPPED_CASES:
            spec = yaml.safe_load((REPO / "cases" / f"{name}.yaml").read_text())
            if "pods" not in spec:
                continue  # a POD dimensions itself; section 2 says so
            halls += 1
            with self.subTest(case=name):
                built = model_module.build_model(spec)
                cell = model_module.parse_cell_size(spec["mesh"]["cell_size"])

                def on_grid(value, axis):
                    return max(1, round(value / cell[axis])) * cell[axis]

                aisles, racks = spec["aisles"], spec["racks"]
                perimeter, hot, cold = (
                    on_grid(float(aisles[key]), 1)
                    for key in ("perimeter", "hot", "cold")
                )
                rack_dy = on_grid(float(racks["size"][1]), 1)
                pods = int(spec["pods"])
                blocks = int(racks.get("blocks", 1))
                transverse = (
                    on_grid(float(aisles.get("transverse", cold)), 0)
                    if blocks > 1 else 0.0
                )
                block_length = int(racks["per_row"]) * on_grid(
                    float(racks["size"][0]), 0
                )
                row_length = blocks * block_length + (blocks - 1) * transverse
                plenum = model_module.plenum_for(spec, float(racks["size"][2]))
                sides = int(spec.get("gallery", {}).get("sides", 1))
                hall_length = (2 * perimeter + row_length
                               + sides * (plenum["depth"] if plenum else 0.0))
                total_x = sides * float(spec["gallery"]["depth"]) + hall_length
                total_y = (2 * perimeter + pods * (2 * rack_dy + hot)
                           + (pods - 1) * cold)

                self.assertAlmostEqual(
                    total_x, built.domain.hi[0], places=6,
                    msg="section 2's length formula no longer matches the "
                        "layout the software builds",
                )
                self.assertAlmostEqual(
                    total_y, built.domain.hi[1], places=6,
                    msg="section 2's width formula no longer matches the "
                        "layout the software builds",
                )
        self.assertTrue(halls, "no shipped hall to check the derivation against")


class ExampleTest(unittest.TestCase):
    """The YAML fragments in the manual have to parse and mean what it says."""

    def fragments(self) -> list[str]:
        return re.findall(r"```yaml\n(.*?)```", MANUAL.read_text(), re.S)

    def test_every_yaml_fragment_parses(self):
        import yaml

        blocks = self.fragments()
        self.assertTrue(blocks, "the manual shows no YAML any more")
        for i, block in enumerate(blocks):
            with self.subTest(fragment=i):
                yaml.safe_load(block)

    def test_the_typical_row_fragment_uses_only_keys_a_position_takes(self):
        """§6 tells an agent a position takes type, blank, width and load_kw.

        `row_plan` refuses anything else by name, so a fragment that drifts
        from that set is a case the agent will write and the build will reject.
        """
        import yaml

        from aicfd.model import row_plan

        for block in self.fragments():
            spec = yaml.safe_load(block)
            if isinstance(spec, dict) and (spec.get("racks") or {}).get("row"):
                row_plan(spec, len(spec["racks"]["row"]))

    def test_the_override_fragment_is_accepted_by_the_readers_it_documents(self):
        import yaml

        from aicfd.model import rack_blanks, rack_loads, rack_widths

        for block in self.fragments():
            spec = yaml.safe_load(block)
            if not isinstance(spec, dict) or "racks" not in spec:
                continue
            racks = spec["racks"]
            if "loads" in racks:
                self.assertTrue(rack_loads(spec))
            if "widths" in racks:
                self.assertTrue(rack_widths(spec))
            if "blanks" in racks:
                self.assertTrue(rack_blanks(spec))


if __name__ == "__main__":
    unittest.main()
