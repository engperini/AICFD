/**
 * Color ramps for the field maps.
 *
 * Two jobs, two ramps -- chosen by what the number means, not by convention:
 *
 *  - "absolute"  Air temperature judged against the ASHRAE recommended envelope.
 *                That is a polarity question (too cold wastes energy <-> too hot
 *                risks equipment, with "on target" in the middle), so it gets a
 *                DIVERGING ramp: two opposite hues and a neutral gray midpoint
 *                anchored on the middle of the envelope.
 *  - "rise"      Temperature rise above supply air, and air speed. Pure magnitude
 *                from zero, so it gets a SEQUENTIAL ramp: one hue, light to dark.
 *
 * Both are built by interpolating in OKLab, which keeps the perceived steps even.
 * Anchors come from the project's validated palette; the reasoning is in
 * docs/DECISIONS.md, ADR-009.
 */

// --- OKLab <-> sRGB ---------------------------------------------------------

function srgbToLinear(c) {
  c /= 255;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearToSrgb(c) {
  const v = c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055;
  return Math.max(0, Math.min(255, Math.round(v * 255)));
}

export function hexToOklab(hex) {
  const r = srgbToLinear(parseInt(hex.slice(1, 3), 16));
  const g = srgbToLinear(parseInt(hex.slice(3, 5), 16));
  const b = srgbToLinear(parseInt(hex.slice(5, 7), 16));
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

export function oklabToRgb([L, a, b]) {
  const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
  return [
    linearToSrgb(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
    linearToSrgb(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
    linearToSrgb(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s),
  ];
}

function oklch(L, C, hDeg) {
  const h = (hDeg * Math.PI) / 180;
  return [L, C * Math.cos(h), C * Math.sin(h)];
}

// --- Anchors ----------------------------------------------------------------
//
// Cold pole is the palette's sequential blue at its dark end (#0d366b ->
// OKLCH 0.338 / 0.103 / 257deg). The warm pole mirrors it exactly -- same
// lightness and chroma, red hue -- so neither arm of the diverging ramp shouts
// louder than the other. On the dark surface both poles move to the light end
// for the same reason, and the neutral midpoint becomes the dark gray: the
// midpoint always has to be the step closest to the surface, or it stops
// reading as "nothing".

// THE SPECTRUM, offered because a client reads it, not because it is better.
//
// Blue through cyan, green, yellow and orange to red is what every commercial
// post-processor prints, so it is what a reader compares a report against --
// and a study nobody can set beside the consultant's is worth less than one
// they can. What it costs is real and worth knowing: the hue sequence is not
// perceptually even, so the cyan/green edge reads as a step the data does not
// have, and the whole thing collapses for a red-green colour-blind reader
// (ADR-101).
//
// Banded, which is how it is always presented, both faults shrink: a contour
// scale is read off the bar rather than judged by eye. So `spectrum` is an
// option, the diverging and sequential ramps stay the defaults, and the two
// never differ in what they are ENCODING -- only in the colours over it.
const SPECTRUM = [
  [0.00, '#4b2fd6'], [0.12, '#3b6fe6'], [0.25, '#2aa3e0'],
  [0.37, '#22c9cf'], [0.47, '#21d3a0'], [0.57, '#4bd85f'],
  [0.67, '#94e04a'], [0.75, '#d2e23c'], [0.82, '#f0d92f'],
  [0.88, '#f7b41b'], [0.93, '#f89320'], [0.97, '#f56a37'],
  [1.00, '#ef4444'],
].map(([at, hex]) => [at, hexToOklab(hex)]);

/**
 * The ramp the reader has asked for, over the one the field itself would pick
 * -- '' when they have not asked.
 *
 * Kept here rather than in the map card because two things read it: the maps,
 * and the report sheet, which sends it with the cover so the Word document
 * comes out in the colours the page was showing. Two copies of the key is one
 * copy too many (ADR-101).
 */
const RAMP_KEY = 'aicfd.ramp';

/**
 * Fired on `window` when the choice changes, so every card that paints with a
 * ramp repaints -- the field maps and the per-rack map are two cards on one
 * page, and one of them in the spectrum while the other stays blue is worse
 * than either alone.
 */
export const RAMP_EVENT = 'aicfd:ramp';

export function rememberedRamp() {
  try {
    return localStorage.getItem(RAMP_KEY) || '';
  } catch {
    return ''; // a private window, or site data blocked: the default ramp
  }
}

export function rememberRamp(name) {
  try {
    localStorage.setItem(RAMP_KEY, name);
  } catch {
    // The choice still applies to this page; it just is not remembered.
  }
  window.dispatchEvent(new CustomEvent(RAMP_EVENT, { detail: name }));
}

const RAMPS = {
  light: {
    coldPole: oklch(0.338, 0.103, 257),
    warmPole: oklch(0.338, 0.103, 25),
    neutral: hexToOklab('#f0efec'),
    seqLow: hexToOklab('#cde2fb'), // sequential blue, step 100
    seqHigh: hexToOklab('#0d366b'), // sequential blue, step 700
  },
  dark: {
    coldPole: oklch(0.85, 0.09, 257),
    warmPole: oklch(0.85, 0.09, 25),
    neutral: hexToOklab('#383835'),
    seqLow: hexToOklab('#0d366b'),
    seqHigh: hexToOklab('#cde2fb'),
  },
};

function mix(a, b, t) {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

const clamp01 = (t) => (t < 0 ? 0 : t > 1 ? 1 : t);

/**
 * Build a lookup table of `steps` RGB triples for a ramp.
 *
 * @param {'diverging'|'sequential'|'spectrum'} kind
 * @param {'light'|'dark'} mode
 * @returns {Uint8ClampedArray} packed RGB, 3 bytes per step
 */
export function buildLut(kind, mode, steps = 256) {
  const ramp = RAMPS[mode];
  const lut = new Uint8ClampedArray(steps * 3);
  for (let i = 0; i < steps; i += 1) {
    const t = steps === 1 ? 0 : i / (steps - 1);
    let lab;
    if (kind === 'diverging') {
      // Equal step count per arm: [0, 0.5] cold -> neutral, [0.5, 1] neutral -> warm.
      lab =
        t < 0.5
          ? mix(ramp.coldPole, ramp.neutral, t * 2)
          : mix(ramp.neutral, ramp.warmPole, (t - 0.5) * 2);
    } else if (kind === 'spectrum') {
      // The same anchors in both modes: a spectrum is not anchored on the
      // surface the way a diverging ramp's neutral midpoint has to be.
      let j = 1;
      while (j < SPECTRUM.length - 1 && SPECTRUM[j][0] < t) j += 1;
      const [at0, c0] = SPECTRUM[j - 1];
      const [at1, c1] = SPECTRUM[j];
      lab = mix(c0, c1, at1 === at0 ? 0 : (t - at0) / (at1 - at0));
    } else {
      lab = mix(ramp.seqLow, ramp.seqHigh, t);
    }
    const [r, g, b] = oklabToRgb(lab);
    lut[i * 3] = r;
    lut[i * 3 + 1] = g;
    lut[i * 3 + 2] = b;
  }
  return lut;
}

/**
 * A scale maps a physical value to a position on its ramp, and knows the tick
 * values a colorbar should print. Every field map ships one -- a continuous
 * color encoding without a numeric scale is unreadable.
 */
export class Scale {
  /**
   * @param {object} options
   * @param {'diverging'|'sequential'|'spectrum'} options.kind
   * @param {number} options.min domain minimum
   * @param {number} options.max domain maximum
   * @param {number} [options.center] value pinned to the ramp midpoint (diverging only)
   * @param {number} [options.step] band width, in the field's own units. With
   *   a step the scale is a *contour* scale: every value in a band gets that
   *   band's colour, so a colour can be read back as a number without a
   *   cursor. That is how a thermal plot is presented in any post-processor an
   *   engineer already trusts, and it is what a smooth wash cannot do
   *   (ADR-024). Without a step the ramp stays continuous.
   */
  constructor({ kind, min, max, center, step }) {
    this.kind = kind;
    this.min = min;
    this.max = max;
    this.center = center;
    this.step = step;
    if (kind === 'diverging') {
      // Symmetric half-width so the neutral really sits on `center`, and a
      // floor so a nearly-uniform field does not blow up into full saturation.
      this.half = Math.max(Math.abs(max - center), Math.abs(center - min), 0.5);
    }
  }

  /** The value a band holds, for `value`: its own midpoint. */
  banded(value) {
    if (!this.step) return value;
    const lo = this.min;
    const index = Math.floor((value - lo) / this.step);
    const count = Math.max(1, Math.round((this.max - lo) / this.step));
    const clamped = Math.min(Math.max(index, 0), count - 1);
    return lo + (clamped + 0.5) * this.step;
  }

  /** Position of `value` on the ramp, in [0, 1]. */
  position(value) {
    const v = this.banded(value);
    if (this.kind === 'diverging') {
      return clamp01(0.5 + (v - this.center) / (2 * this.half));
    }
    return clamp01((v - this.min) / (this.max - this.min || 1));
  }

  /** The value at ramp position `t` -- used to label the colorbar. */
  valueAt(t) {
    return this.kind === 'diverging'
      ? this.center + (t - 0.5) * 2 * this.half
      : this.min + t * (this.max - this.min);
  }

  /** Where `value` sits along the bar, in [0, 1], ignoring the banding. */
  positionOf(value) {
    return this.kind === 'diverging'
      ? clamp01(0.5 + (value - this.center) / (2 * this.half))
      : clamp01((value - this.min) / (this.max - this.min || 1));
  }

  /** The bands, low to high: their edges in value and along the bar. */
  bands() {
    if (!this.step) return [];
    const count = Math.max(1, Math.round((this.max - this.min) / this.step));
    return Array.from({ length: count }, (_, i) => {
      const lo = this.min + i * this.step;
      const hi = lo + this.step;
      return { lo, hi, t0: this.positionOf(lo), t1: this.positionOf(hi) };
    });
  }

  ticks(count = 5) {
    return Array.from({ length: count }, (_, i) => {
      const t = i / (count - 1);
      return { t, value: this.valueAt(t) };
    });
  }
}

/**
 * A round band width, close to `span / target`: 1, 2, 2.5 or 5 times a power
 * of ten. Contour levels an engineer reads off a legend are round numbers --
 * 10 Pa, 0,5 m/s -- never 9,74.
 */
export function niceStep(span, target = 16) {
  if (!(span > 0)) return 1;
  const raw = span / target;
  const pow = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * pow).find((c) => c >= raw - 1e-12) ?? raw;
}
