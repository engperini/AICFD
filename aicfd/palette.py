"""The page's colour ramps, in Python, for the report's figures.

`web/colormaps.js` is the source of truth: the page and the Word deliverable
have to colour the same field the same way, or a number read off one and
checked against the other is a different number. This module is a port, not a
second design -- same OKLab interpolation, same anchors, same banded scale
(ADR-009, ADR-024) -- and `tests/test_palette.py` runs the JavaScript under
node and compares, so the two cannot drift apart silently.
"""

from __future__ import annotations

import math

# --- sRGB <-> OKLab -----------------------------------------------------------


def _srgb_to_linear(c: float) -> float:
    c /= 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> int:
    v = c * 12.92 if c <= 0.0031308 else 1.055 * c ** (1 / 2.4) - 0.055
    return max(0, min(255, round(v * 255)))


def hex_to_oklab(value: str) -> tuple[float, float, float]:
    r = _srgb_to_linear(int(value[1:3], 16))
    g = _srgb_to_linear(int(value[3:5], 16))
    b = _srgb_to_linear(int(value[5:7], 16))
    l = (0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b) ** (1 / 3)
    m = (0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b) ** (1 / 3)
    s = (0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b) ** (1 / 3)
    return (
        0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
        1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
        0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
    )


def oklab_to_rgb(lab) -> tuple[int, int, int]:
    big_l, a, b = lab
    l = (big_l + 0.3963377774 * a + 0.2158037573 * b) ** 3
    m = (big_l - 0.1055613458 * a - 0.0638541728 * b) ** 3
    s = (big_l - 0.0894841775 * a - 1.291485548 * b) ** 3
    return (
        _linear_to_srgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
        _linear_to_srgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
        _linear_to_srgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
    )


def _oklch(big_l: float, chroma: float, hue_deg: float):
    h = math.radians(hue_deg)
    return (big_l, chroma * math.cos(h), chroma * math.sin(h))


#: The light-surface ramp. The cold pole is the palette's sequential blue at
#: its dark end; the warm pole mirrors it exactly -- same lightness and chroma,
#: red hue -- so neither arm shouts louder than the other.
RAMP = {
    "cold_pole": _oklch(0.338, 0.103, 257),
    "warm_pole": _oklch(0.338, 0.103, 25),
    "neutral": hex_to_oklab("#f0efec"),
    "seq_low": hex_to_oklab("#cde2fb"),
    "seq_high": hex_to_oklab("#0d366b"),
}


def _mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def lut(kind: str, steps: int = 256) -> list[tuple[int, int, int]]:
    """``steps`` RGB triples along a ramp.

    ``diverging`` gives each arm the same number of steps, so the neutral
    midpoint lands exactly halfway; ``sequential`` runs light to dark in one
    hue.
    """
    table = []
    for i in range(steps):
        t = 0.0 if steps == 1 else i / (steps - 1)
        if kind == "diverging":
            lab = (
                _mix(RAMP["cold_pole"], RAMP["neutral"], t * 2)
                if t < 0.5
                else _mix(RAMP["neutral"], RAMP["warm_pole"], (t - 0.5) * 2)
            )
        else:
            lab = _mix(RAMP["seq_low"], RAMP["seq_high"], t)
        table.append(oklab_to_rgb(lab))
    return table


#: The categorical series colours, in fixed order, as the page defines them
#: (``--series-1`` onwards on the light surface). Assigned in order and never
#: cycled: a colour follows the series it names, so the same field is the same
#: colour on the page and in the document, and adding a series never repaints
#: the others.
SERIES = (
    "#2a78d6", "#eb6834", "#1baf7a", "#eda100",
    "#e87ba4", "#008300", "#4a3aa7", "#e34948",
)


#: The band every air temperature is coloured against, whatever the run
#: (ADR-024). Fixed rather than fitted, so a colour means the same thing from
#: one figure and one run to the next.
TEMPERATURE_BAND = {"min": 10.0, "max": 40.0, "step": 2.5}

#: ASHRAE's thermal guidelines for the air a rack breathes, marked on the bar
#: because that is where the judgement happens.
ASHRAE_MARKS = (18.0, 27.0, 32.0)


def banded(value: float, lo: float, hi: float, step: float | None) -> float:
    """Snap ``value`` to the midpoint of the band it falls in.

    Values outside the domain snap to the end band rather than off the scale,
    which is what makes the fixed 10-40 degC band safe: air outside it is
    compressed into the end colour and the legend says so, but nothing is
    dropped.
    """
    if not step:
        return value
    index = math.floor((value - lo) / step)
    count = max(1, round((hi - lo) / step))
    return lo + (min(max(index, 0), count - 1) + 0.5) * step


def position(value: float, lo: float, hi: float, center: float | None = None,
             step: float | None = None) -> float:
    """Where ``value`` sits on the ramp, in [0, 1].

    With a ``center`` the ramp is diverging and the pinned value lands on the
    neutral midpoint; the half-width is the longer of the two arms, so a
    lopsided domain keeps both arms on the same scale rather than stretching
    the short one.
    """
    v = banded(value, lo, hi, step)
    if center is None:
        return min(max((v - lo) / (hi - lo) if hi > lo else 0.0, 0.0), 1.0)
    half = max(center - lo, hi - center) or 1.0
    return min(max(0.5 + (v - center) / (2 * half), 0.0), 1.0)


def bands(lo: float, hi: float, step: float) -> list[float]:
    """Band edges from ``lo`` to ``hi``, inclusive of both."""
    n = max(1, int(round((hi - lo) / step)))
    return [lo + i * step for i in range(n + 1)]


def band_colours(lo: float, hi: float, step: float, kind: str = "diverging",
                 center: float | None = None) -> list[tuple[float, float, float]]:
    """One colour per band, taken at the band's midpoint, as 0-1 floats.

    A contour scale rather than a smooth wash: every value inside a band gets
    that band's colour, so a colour can be read back as a number off the
    legend without a cursor. That is how a thermal plot is presented in any
    post-processor an engineer already trusts.
    """
    table = lut(kind)
    edges = bands(lo, hi, step)
    out = []
    for a, b in zip(edges, edges[1:]):
        t = position((a + b) / 2, lo, hi, center)
        r, g, blue = table[min(len(table) - 1, int(round(t * (len(table) - 1))))]
        out.append((r / 255, g / 255, blue / 255))
    return out


def nice_step(span: float, target: int = 16) -> float:
    """A round band width that divides ``span`` into about ``target`` bands.

    Round, because the legend is read as numbers: 2,5 K bands can be counted
    off a bar, 2,37 K bands cannot.
    """
    if not span > 0:
        return 1.0
    raw = span / target
    power = 10 ** math.floor(math.log10(raw))
    for multiple in (1, 2, 2.5, 5, 10):
        if multiple * power >= raw - 1e-12:
            return multiple * power
    return raw


def fitted_scale(values, target: int = 8) -> dict:
    """A sequential scale fitted to ``values``, in round bands.

    Used where the question is "which of these is worst", not "is this air
    acceptable": every rack in a healthy hall sits inside one band of the
    fixed temperature scale, so painting them against it says only that they
    are all fine -- true, and useless for ranking them. The caption has to say
    the scale is fitted, or the two figures look like they disagree.
    """
    values = [v for v in values if v is not None]
    if not values:
        return temperature_scale()
    low, high = min(values), max(max(values), min(values) + 0.5)
    step = nice_step(high - low, target)
    lo = math.floor(low / step) * step
    hi = math.ceil(high / step) * step
    return {
        "min": lo,
        "max": hi,
        "step": step,
        "center": None,
        "edges": bands(lo, hi, step),
        "colours": band_colours(lo, hi, step, "sequential"),
        "marks": tuple(m for m in ASHRAE_MARKS if lo < m < hi),
    }


def temperature_scale() -> dict:
    """The fixed temperature scale the page and the report share."""
    lo, hi, step = (TEMPERATURE_BAND[k] for k in ("min", "max", "step"))
    return {
        "min": lo,
        "max": hi,
        "step": step,
        "center": (lo + hi) / 2,
        "edges": bands(lo, hi, step),
        "colours": band_colours(lo, hi, step, "diverging", (lo + hi) / 2),
        "marks": ASHRAE_MARKS,
    }
