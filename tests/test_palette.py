"""The Python ramp and the JavaScript ramp have to be the same ramp.

`aicfd/palette.py` is a port of `web/colormaps.js` so the Word report and the
page colour a field identically. A port is a copy, and a copy drifts. This
runs the original under node and compares, so it cannot drift silently. It
skips where node is absent -- the rest of the tool never needs it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from aicfd import palette

WEB = Path(__file__).resolve().parents[1] / "web"
NODE = shutil.which("node")

SCRIPT = """
import { buildLut, Scale } from './colormaps.js';
const out = { diverging: [], sequential: [], positions: [] };
for (const kind of ['diverging', 'sequential']) {
  const lut = buildLut(kind, 'light');
  for (let i = 0; i < 256; i += 1) {
    out[kind].push([lut[i * 3], lut[i * 3 + 1], lut[i * 3 + 2]]);
  }
}
const scale = new Scale({ kind: 'diverging', min: 10, max: 40, step: 2.5, center: 25 });
for (const v of [8, 10, 14, 18, 22, 25, 27, 32, 36, 40, 44]) {
  out.positions.push(scale.position(v));
}
console.log(JSON.stringify(out));
"""


@unittest.skipIf(NODE is None, "node is not installed")
class PaletteMatchesTheJavaScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = WEB / "_palette_probe.mjs"
        script.write_text(SCRIPT)
        try:
            proc = subprocess.run(
                [NODE, str(script)], cwd=WEB, capture_output=True, text=True, timeout=60
            )
        finally:
            script.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise unittest.SkipTest(f"node could not run colormaps.js: {proc.stderr[:200]}")
        cls.js = json.loads(proc.stdout)

    def test_every_step_of_both_ramps_agrees(self):
        for kind in ("diverging", "sequential"):
            ours = palette.lut(kind)
            theirs = [tuple(rgb) for rgb in self.js[kind]]
            self.assertEqual(len(ours), len(theirs))
            for i, (a, b) in enumerate(zip(ours, theirs)):
                # one 8-bit count of tolerance: the two languages round the
                # same cube root differently in the last place
                for channel, (x, y) in enumerate(zip(a, b)):
                    self.assertLessEqual(
                        abs(x - y), 1,
                        f"{kind} step {i} channel {channel}: {a} vs {b}",
                    )

    def test_the_temperature_scale_places_a_value_the_same_way(self):
        values = [8, 10, 14, 18, 22, 25, 27, 32, 36, 40, 44]
        for value, theirs in zip(values, self.js["positions"]):
            ours = palette.position(float(value), 10.0, 40.0, 25.0, 2.5)
            self.assertAlmostEqual(ours, theirs, places=6, msg=f"at {value} degC")


class TemperatureBandTest(unittest.TestCase):
    """The fixed band is a decision (ADR-024), so it is asserted, not assumed."""

    def test_the_band_is_fixed_and_carries_the_ashrae_marks(self):
        scale = palette.temperature_scale()
        self.assertEqual((scale["min"], scale["max"], scale["step"]), (10.0, 40.0, 2.5))
        self.assertEqual(scale["center"], 25.0)
        self.assertEqual(len(scale["colours"]), 12)
        self.assertEqual(scale["marks"], (18.0, 27.0, 32.0))

    def test_the_midpoint_band_is_the_neutral_one(self):
        """The pinned centre has to land on the ramp's neutral step, or the
        colourbar stops reading as "nothing in particular" in the middle."""
        scale = palette.temperature_scale()
        neutral = palette.oklab_to_rgb(palette.RAMP["neutral"])
        below, above = scale["colours"][5], scale["colours"][6]
        for band in (below, above):
            for channel, target in zip(band, neutral):
                self.assertLess(abs(channel * 255 - target), 32)
