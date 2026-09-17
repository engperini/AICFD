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
 * @param {'diverging'|'sequential'} kind
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
   * @param {'diverging'|'sequential'} options.kind
   * @param {number} options.min domain minimum
   * @param {number} options.max domain maximum
   * @param {number} [options.center] value pinned to the ramp midpoint (diverging only)
   */
  constructor({ kind, min, max, center }) {
    this.kind = kind;
    this.min = min;
    this.max = max;
    this.center = center;
    if (kind === 'diverging') {
      // Symmetric half-width so the neutral really sits on `center`, and a
      // floor so a nearly-uniform field does not blow up into full saturation.
      this.half = Math.max(Math.abs(max - center), Math.abs(center - min), 0.5);
    }
  }

  /** Position of `value` on the ramp, in [0, 1]. */
  position(value) {
    if (this.kind === 'diverging') {
      return clamp01(0.5 + (value - this.center) / (2 * this.half));
    }
    return clamp01((value - this.min) / (this.max - this.min || 1));
  }

  /** The value at ramp position `t` -- used to label the colorbar. */
  valueAt(t) {
    return this.kind === 'diverging'
      ? this.center + (t - 0.5) * 2 * this.half
      : this.min + t * (this.max - this.min);
  }

  ticks(count = 5) {
    return Array.from({ length: count }, (_, i) => {
      const t = i / (count - 1);
      return { t, value: this.valueAt(t) };
    });
  }
}
