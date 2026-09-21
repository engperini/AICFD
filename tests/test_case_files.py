"""What the GENERATOR makes of a model, as opposed to what the model says.

`tests/test_case.py` holds the OpenFOAM dictionaries to their contents. This
file holds the step before: that every surface the model describes is one the
generator actually builds.
"""

from __future__ import annotations

import copy
import unittest




class EveryPanelReachesTheMesherTest(unittest.TestCase):
    """A surface the model builds and `createBaffles` never makes is a hole.

    It has happened four times. A blanking panel was in the model, on the
    drawing and in the summary, and not in `WALL_GROUPS` -- so the solver saw
    an open position and 3,83 m3/s came out of it, 28% of what the whole row
    passed (ADR-081). A cold-aisle containment LID went the same way: built,
    drawn, listed, unmeshed, and the rows delivered 4% of their rated
    resistance with the warmest intake at 47 degC (ADR-100).

    Every time, the model was right and the generator's list of prefixes had
    not been told. A list is what goes stale, so this checks the two against
    each other instead of adding a fifth name to one of them: every panel the
    model builds has to end up in a zone, in `porous()`, as a hole in a wall,
    or as a fan.
    """

    def test_every_panel_is_claimed_by_the_case_generator(self):
        import yaml

        from aicfd import case as case_module
        from aicfd import model as model_module
        from tests import support

        for name in support.SHIPPED_CASES:
            spec = yaml.safe_load(
                (support.REPO / "cases" / f"{name}.yaml").read_text())
            for aisle in ("hot", "cold"):
                trial = copy.deepcopy(spec)
                trial.setdefault("containment", {})["aisle"] = aisle
                try:
                    built = model_module.build_model(trial)
                except ValueError:
                    continue  # an arrangement this case cannot have
                plan = case_module.wall_plan(built)
                claimed = {p.name for _z, ps, _h in plan for p in ps}
                claimed |= {h.name for _z, _w, hs in plan for h in hs}
                claimed |= {p.name for p in case_module.porous(built)}
                claimed |= {p.name for p in built.panels if p.kind == "fan"}
                orphans = sorted(p.name for p in built.panels
                                 if p.name not in claimed)
                with self.subTest(case=name, containment=aisle):
                    self.assertFalse(
                        orphans,
                        f"{orphans} is built by the model and never reaches "
                        "createBaffles: the solver sees a hole where the "
                        "drawing shows a surface",
                    )
