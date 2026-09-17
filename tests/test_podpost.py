"""Tests for POD post-processing.

The module's whole claim is that it reads what crosses a patch rather than
what is near it, and that the energy balance is a better verdict than a
residual. These check both on hand-written field files.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from aicfd import model as M
from aicfd import podpost
from aicfd.podcase import FAN_INTAKE, FAN_SUPPLY, KELVIN

SPEC = yaml.safe_load(
    """
name: t
gallery: {depth: 3.0}
hall: {size: [6.0, 4.2, 8.0], ceiling: 6.5}
aisles: {cold: 1.8, hot: 1.2}
racks: {count: 3, load_kw: 6.0, size: [0.6, 1.2, 2.2], offset_x: 2.0}
fanwall: {airflow_m3h: 5000, supply_temp_c: 20.0, height: 4.0, width: 1.8}
grilles: {size: 0.6, count: 3}
containment: {enabled: true}
mesh: {cell_size: 0.1}
"""
)

HEADER = """FoamFile
{{
    version 2.0; format ascii; class {cls}; location "{time}"; object {obj};
}}
dimensions [0 0 0 0 0 0 0];
internalField uniform {internal};
boundaryField
{{
"""


def field(path: Path, obj: str, internal: str, patches: dict[str, list[float]]):
    body = HEADER.format(cls="volScalarField", time=path.parent.name, obj=obj,
                         internal=internal)
    for name, values in patches.items():
        numbers = " ".join(f"{v:.6g}" for v in values)
        body += (
            f"    {name}\n    {{\n        type calculated;\n"
            f"        value nonuniform List<scalar> {len(values)}\n"
            f"({numbers})\n;\n    }}\n"
        )
    path.write_text(body + "}\n")


class PatchReadingTest(unittest.TestCase):
    """A closed loop carrying exactly its load out through the intake."""

    def setUp(self):
        self.model = M.build_model(SPEC)
        self.case = Path(tempfile.mkdtemp())
        step = self.case / "100"
        step.mkdir()
        # 1 kg/s out through four faces, 1 kg/s in, walls shut.
        field(step / "phi", "phi", "0", {
            FAN_INTAKE: [0.25, 0.25, 0.25, 0.25],
            FAN_SUPPLY: [-0.5, -0.5],
            "forro_master": [0.0, 0.0],
            "floor": [0.0],
        })
        # 18 kW on 1 kg/s of air is a 17.91 K rise.
        rise = self.model.total_load_w / (1.0 * 1005.0)
        supply = 20.0 + KELVIN
        field(step / "T", "T", f"{supply}", {
            FAN_INTAKE: [supply + rise] * 4,
            FAN_SUPPLY: [supply, supply],
            "forro_master": [supply, supply],
            "floor": [supply],
        })
        self.step = step

    def test_patch_flows_sum_each_patch(self):
        flows = podpost.patch_flows(self.step)
        self.assertAlmostEqual(flows[FAN_INTAKE], 1.0)
        self.assertAlmostEqual(flows[FAN_SUPPLY], -1.0)
        self.assertAlmostEqual(flows["forro_master"], 0.0)

    def test_the_return_temperature_is_flow_weighted(self):
        rise = self.model.total_load_w / 1005.0
        self.assertAlmostEqual(
            podpost.return_temperature(self.step), 20.0 + KELVIN + rise, places=3
        )

    def test_a_closed_balance_recovers_the_whole_load(self):
        recovered = podpost.recovered_load_w(self.step, self.model)
        # 1 W of slack: the fixture writes its temperatures at six figures.
        self.assertAlmostEqual(recovered, self.model.total_load_w, delta=1.0)

    def test_a_half_filled_field_recovers_half_the_load(self):
        """The failure mode this exists to catch: an unconverged thermal field."""
        supply = 20.0 + KELVIN
        rise = self.model.total_load_w / 1005.0 / 2
        field(self.step / "T", "T", f"{supply}", {
            FAN_INTAKE: [supply + rise] * 4,
            FAN_SUPPLY: [supply, supply],
            "forro_master": [supply, supply],
            "floor": [supply],
        })
        recovered = podpost.recovered_load_w(self.step, self.model)
        self.assertAlmostEqual(recovered / self.model.total_load_w, 0.5, places=3)

    def test_backflow_is_the_mass_going_the_wrong_way(self):
        field(self.step / "phi", "phi", "0", {
            FAN_INTAKE: [0.8, 0.8, -0.3, -0.3],
            FAN_SUPPLY: [-0.5, -0.5],
        })
        self.assertAlmostEqual(podpost.backflow(self.step, FAN_INTAKE), 0.6)
        self.assertAlmostEqual(podpost.backflow(self.step, FAN_SUPPLY), 0.0)

    def test_an_outlet_that_stores_no_value_says_so(self):
        """zeroGradient writes no value, and nan would hide it."""
        (self.step / "T").write_text(
            HEADER.format(cls="volScalarField", time="100", obj="T", internal="293")
            + f"    {FAN_INTAKE}\n    {{\n        type zeroGradient;\n    }}\n}}\n"
        )
        with self.assertRaises(ValueError) as caught:
            podpost.return_temperature(self.step)
        self.assertIn("inletOutlet", str(caught.exception))

    def test_written_times_are_ordered_numerically(self):
        for name in ("50", "1000", "200"):
            d = self.case / name
            d.mkdir(exist_ok=True)
            (d / "phi").write_text("x")
        self.assertEqual(
            podpost.written_times(self.case), ["50", "100", "200", "1000"]
        )


class ToleranceTest(unittest.TestCase):
    def test_mass_is_held_tighter_than_energy(self):
        """Mass has nowhere to go; the thermal field merely takes time."""
        self.assertLess(podpost.MASS_TOLERANCE, podpost.ENERGY_TOLERANCE)

    def test_any_real_backflow_through_a_fan_is_a_failure(self):
        self.assertLessEqual(podpost.BACKFLOW_TOLERANCE, 0.05)


if __name__ == "__main__":
    unittest.main()
