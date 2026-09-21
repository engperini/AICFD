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
const out = { diverging: [], sequential: [], spectrum: [], positions: [] };
for (const kind of ['diverging', 'sequential', 'spectrum']) {
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

    def test_every_step_of_every_ramp_agrees(self):
        for kind in ("diverging", "sequential", "spectrum"):
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


class SpectrumTest(unittest.TestCase):
    """The rainbow a reader may ask for (ADR-101).

    It is offered because a client compares this study with one printed by a
    commercial post-processor, and that comparison is only possible in the
    colours they already have. What it must never become is a second
    ENCODING: the domain, the bands, the pinned centre and the ASHRAE marks
    are the field's either way, so the same plot in two ramps is the same
    numbers.
    """

    def test_every_anchor_lands_on_the_ramp(self):
        """The colours in the picture the reader asked for, in that order."""
        table = palette.lut("spectrum", 401)
        for at, lab in palette.SPECTRUM:
            got = table[round(at * 400)]
            want = palette.oklab_to_rgb(lab)
            for channel, (x, y) in enumerate(zip(got, want)):
                self.assertLessEqual(
                    abs(x - y), 1,
                    f"the anchor at {at:g} came out {got}, not {want} "
                    f"(channel {channel})",
                )

    def test_it_runs_violet_to_red(self):
        table = palette.lut("spectrum")
        self.assertEqual(table[0], palette.oklab_to_rgb(palette.SPECTRUM[0][1]))
        self.assertEqual(table[-1], palette.oklab_to_rgb(palette.SPECTRUM[-1][1]))
        # Blue at the cold end, red at the hot one: not a statement about the
        # middle, but the two a reader reads first must not be swapped.
        self.assertGreater(table[0][2], table[0][0], "the cold end is not blue")
        self.assertGreater(table[-1][0], table[-1][2], "the hot end is not red")

    def test_asking_for_it_changes_the_colours_and_nothing_else(self):
        default = palette.temperature_scale()
        spectrum = palette.temperature_scale("spectrum")
        for key in ("min", "max", "step", "center", "edges", "marks"):
            self.assertEqual(
                spectrum[key], default[key],
                f"the ramp moved {key}: a colour would mean a different "
                "number depending on which ramp the reader picked",
            )
        self.assertEqual(len(spectrum["colours"]), len(default["colours"]))
        self.assertNotEqual(spectrum["colours"], default["colours"])

    def test_the_fitted_scale_takes_it_the_same_way(self):
        values = [21.4, 23.9, 26.1, 31.2]
        default = palette.fitted_scale(values)
        spectrum = palette.fitted_scale(values, ramp="spectrum")
        for key in ("min", "max", "step", "center", "edges", "marks"):
            self.assertEqual(spectrum[key], default[key])
        self.assertNotEqual(spectrum["colours"], default["colours"])

    def test_the_name_the_reader_may_ask_for_is_the_one_the_ramp_answers_to(self):
        """`OPTIONAL_RAMPS` is what the page, the server and `--colours`
        offer. A name in it that `lut` does not know would fall through to the
        sequential ramp and paint the wrong picture without saying so."""
        for name in palette.OPTIONAL_RAMPS:
            self.assertNotEqual(
                palette.lut(name), palette.lut("sequential"),
                f"'{name}' is offered and is not a ramp",
            )
