/**
 * Plan and sections with the solved field underneath.
 *
 * The same three drawings the engineer checked before the run -- transverse
 * section, longitudinal section, plan -- now drawn over a colour map of
 * temperature, air speed or pressure cut on the same plane. A slider moves the
 * cut; the lines move with it, so the containment and the racks always sit
 * where they are in the field being looked at. One field at a time and one
 * colour scale for all three views, because three colourbars is three chances
 * to misread which is which.
 *
 * Colour follows the job of the data (see colormaps.js): temperature and
 * pressure diverge (cool/warm, below/above the fan intake), speed is a single
 * hue from still to fast. Every field is drawn in discrete contour bands with
 * round edges, so a colour can be read back as a number off the legend rather
 * than only under the cursor -- and temperature is judged against a fixed
 * band with the ASHRAE limits marked on it (ADR-024).
 */

import { viewsFor, drawView, defaultCut, sheetScale, viewTransform } from './drawing.js';
import { Scale, buildLut, niceStep, rememberedRamp, rememberRamp }
  from './colormaps.js';

/**
 * The band every air temperature is coloured against, whatever the run, and
 * the contour interval inside it.
 *
 * Fixed rather than fitted to each field, because the reader's question is
 * "how hot is this air" and the answer has to mean the same thing from one
 * run to the next and from one view to the next. A scale stretched to the
 * field's own extremes answers a different question every time: one rack
 * starved at the mouth of an aisle reached 55 °C, and colouring against that
 * left a 22 °C cold aisle and a 36 °C hot aisle both washed out to the same
 * pale tone.
 *
 * 10 °C is below any supply anyone would design; 40 °C is above the hot aisle
 * of a 14 K rise. Air outside the band saturates at the end of the ramp and
 * the colourbar says so, so nothing is hidden -- only compressed, and the
 * numeric readout under the cursor is exact either way.
 */
export const TEMPERATURE_BAND = { min: 10, max: 40, step: 2.5 };

/**
 * ASHRAE's thermal guidelines for the air a rack breathes in, drawn on the
 * temperature bar. They are where the judgement happens, so they are marked
 * on the scale itself rather than left to the reader's memory -- and marking
 * them is also what lets the ramp's midpoint go back to meaning nothing in
 * particular, which is how a thermometer legend should read.
 */
const ASHRAE_MARKS = [
  { value: 18, label: '18' },
  { value: 27, label: '27' },
  { value: 32, label: '32' },
];

/**
 * Magnification steps offered per drawing. `1` is the fitted sheet, shared by
 * all three; above it the drawing overflows and scrolls.
 */
const ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];

/** How many contour bands a fitted scale aims for. */
const TARGET_BANDS = 16;

export const FIELDS = {
  temperature: {
    label: 'Temperature',
    field: 'T',
    kind: 'diverging',
    units: '°C',
    decimals: 1,
    marks: ASHRAE_MARKS,
    scaleFor: () =>
      new Scale({
        kind: 'diverging',
        ...TEMPERATURE_BAND,
        center: (TEMPERATURE_BAND.min + TEMPERATURE_BAND.max) / 2,
      }),
    caption:
      'ASHRAE rack-inlet limits are marked on the bar: recommended 18 to 27 °C, allowable A1 to 32 °C.',
  },
  speed: {
    label: 'Air speed',
    field: 'speed',
    kind: 'sequential',
    units: 'm/s',
    decimals: 2,
    scaleFor: (results) => {
      const step = niceStep(results.fields.speed.max, TARGET_BANDS);
      return new Scale({
        kind: 'sequential',
        min: 0,
        max: Math.ceil(results.fields.speed.max / step) * step,
        step,
      });
    },
    caption: 'Still air beside a rack is where heat accumulates.',
  },
  pressure: {
    label: 'Pressure',
    field: 'P',
    kind: 'diverging',
    units: 'Pa',
    decimals: 1,
    scaleFor: (results) => {
      const { min, max } = results.fields.P;
      const reach = Math.max(Math.abs(max), Math.abs(min), 0.5);
      const step = niceStep(2 * reach, TARGET_BANDS);
      const half = Math.ceil(reach / step) * step;
      return new Scale({ kind: 'diverging', min: -half, max: half, center: 0, step });
    },
    caption:
      'Relative to the fan wall intake, with the hydrostatic column removed: what a manometer would read.',
  },
};

/**
 * Mount the three maps into `host`.
 *
 * @param {HTMLElement} host
 * @param {import('./data.js').Results} results
 * @param {object} model the model payload (viewer.json `model`)
 * @param {() => 'light'|'dark'} currentMode
 */
export class FieldMaps {
  constructor(host, results, model, currentMode) {
    this.host = host;
    this.results = results;
    this.model = model;
    this.currentMode = currentMode;
    this.fieldKey = 'temperature';
    // A hall's plan runs across the page and each view takes a full row.
    this.views = viewsFor(model);
    this.cuts = Object.fromEntries(this.views.map((v) => [v.id, defaultCut(model, v)]));
    //: Magnification per drawing, 1 being the fitted sheet the three share.
    this.zoom = Object.fromEntries(this.views.map((v) => [v.id, 1]));
    this.cells = [];
    this.#build();
    window.addEventListener('resize', debounce(() => this.render(), 150));
  }

  #build() {
    this.host.innerHTML = `
      <div class="maps-controls">
        <div class="control">
          <label for="map-field">Field</label>
          <select id="map-field">${Object.entries(FIELDS)
            .filter(([, f]) => this.results.fields[f.field])
            .map(([k, f]) => `<option value="${k}">${f.label}</option>`)
            .join('')}</select>
        </div>
        <div class="control">
          <label for="map-ramp">Colours</label>
          <select id="map-ramp">
            <option value="">As the field asks</option>
            <option value="spectrum">Spectrum (blue to red)</option>
          </select>
        </div>
        <span class="card-sub" id="map-caption"></span>
      </div>
      <div class="views maps" id="map-views"></div>
      <div class="colorbar">
        <div class="colorbar-label">
          <span id="map-colorbar-name"></span>
        </div>
        <div class="scale-bar">
          <div class="scale-marks" id="map-colorbar-marks"></div>
          <div class="colorbar-ramp" id="map-colorbar-ramp"></div>
          <div class="scale-ticks" id="map-colorbar-ticks"></div>
        </div>
      </div>
      <div class="readout" id="map-probe" hidden></div>`;

    this.host.querySelector('#map-field').addEventListener('change', (e) => {
      this.fieldKey = e.target.value;
      this.render();
    });

    // WHICH COLOURS, not which encoding. The scale, its domain and its bands
    // are the field's; this picks the ramp painted over them, so two readers
    // looking at the same plot in different colours are looking at the same
    // numbers (ADR-101).
    const ramp = this.host.querySelector('#map-ramp');
    ramp.value = rememberedRamp();
    ramp.addEventListener('change', (e) => {
      this.ramp = e.target.value;
      rememberRamp(this.ramp);
      this.render();
    });
    this.ramp = ramp.value;

    const viewsHost = this.host.querySelector('#map-views');
    for (const view of this.views) {
      const cell = document.createElement('div');
      cell.className = 'view map-view';
      const d = this.model.domain;
      const lo = d.lo[view.normal];
      const hi = d.hi[view.normal];
      const step = this.model.cell_size[view.normal];
      cell.innerHTML = `
        <div class="map-head">
          <div><h3>${view.title}</h3><p class="map-cut" data-role="cut"></p></div>
          <input type="range" data-role="slider" min="${lo + step / 2}" max="${hi - step / 2}"
                 step="${step}" value="${this.cuts[view.id]}" aria-label="cut position" />
          <div class="map-zoom">
            <button type="button" data-role="out" aria-label="zoom out">−</button>
            <span data-role="zoom">fit</span>
            <button type="button" data-role="in" aria-label="zoom in">+</button>
          </div>
        </div>
        <div class="map-scroll" data-role="scroll">
          <div class="map-stage" data-role="stage"></div>
        </div>`;
      viewsHost.append(cell);
      const slider = cell.querySelector('[data-role="slider"]');
      slider.addEventListener('input', () => {
        this.cuts[view.id] = Number(slider.value);
        this.renderView(view, cell);
      });
      for (const [role, factor] of [['in', 1], ['out', -1]]) {
        cell.querySelector(`[data-role="${role}"]`).addEventListener('click', () => {
          this.#setZoom(view, cell, factor);
        });
      }
      this.cells.push({ view, cell });
    }
    this.render();
  }

  /**
   * Zoom one drawing, keeping the middle of what is on screen in the middle.
   *
   * A data hall in section is 51 m long and 7,5 m tall. Fitted to the page it
   * is 160 px high whatever the column is doing, and no amount of layout
   * changes that -- the aspect ratio is the aspect ratio. To actually look at
   * a plume or the stratification in a chimney you have to magnify and scroll,
   * which is what every post-processor does and what fitting alone cannot
   * replace (ADR-035).
   *
   * Zoom is per drawing: the fitted state is shared, so at "fit" the three
   * still share a scale and a metre is the same length in all of them. The
   * label says which state it is in, so a magnified drawing never passes for
   * the fitted one.
   */
  #setZoom(view, cell, direction) {
    const steps = ZOOM_STEPS;
    const current = this.zoom[view.id] ?? 1;
    const index = steps.indexOf(current);
    const next = steps[Math.min(steps.length - 1, Math.max(0, index + direction))];
    if (next === current) return;
    const scroll = cell.querySelector('[data-role="scroll"]');
    // Hold the centre of the visible area: zooming that jumps to a corner
    // loses the thing the reader was looking at.
    const anchor = {
      x: (scroll.scrollLeft + scroll.clientWidth / 2) / Math.max(scroll.scrollWidth, 1),
      y: (scroll.scrollTop + scroll.clientHeight / 2) / Math.max(scroll.scrollHeight, 1),
    };
    this.zoom[view.id] = next;
    this.renderView(view, cell);
    scroll.scrollLeft = anchor.x * scroll.scrollWidth - scroll.clientWidth / 2;
    scroll.scrollTop = anchor.y * scroll.scrollHeight - scroll.clientHeight / 2;
  }

  render() {
    const mode = this.currentMode();
    const spec = FIELDS[this.fieldKey];
    this.scale = spec.scaleFor(this.results);
    this.lut = buildLut(this.ramp || spec.kind, mode);
    this.host.querySelector('#map-caption').textContent = spec.caption;
    this.#renderColorbar(spec);

    const widths = this.cells.map(({ cell }) => cell.clientWidth - 24);
    this.sheet = sheetScale(this.model, this.views, widths);
    for (const entry of this.cells) this.renderView(entry.view, entry.cell);
  }

  renderView(view, cell) {
    const spec = FIELDS[this.fieldKey];
    const stage = cell.querySelector('[data-role="stage"]');
    const at = this.cuts[view.id];
    const axisName = 'xyz'[view.normal];
    cell.querySelector('[data-role="cut"]').textContent =
      `cut at ${axisName} = ${fmt(at, 2)} m`;

    const zoom = this.zoom[view.id] ?? 1;
    const sheet = this.sheet * zoom;
    const label = cell.querySelector('[data-role="zoom"]');
    if (label) {
      label.textContent = zoom === 1 ? 'fit' : `${zoom}×`;
      label.title = `${Math.round(sheet)} px per metre`;
    }
    const { width, height, X, Y } = viewTransform(this.model, view, sheet);
    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);

    // Paint the cut: one rectangle per cell, at the cell's own metres.
    //
    // A slice comes back with `u` along the lower in-plane axis and `v` along
    // the higher one (x then y for a plan, y then z for a transverse section).
    // The view decides which of those runs across the page: a hall's plan is
    // turned so that y is horizontal, and painting u as horizontal there put
    // 35 x-cells along an 88 m axis and coloured a third of the hall with the
    // wrong cells. So each cell is placed in room coordinates first and only
    // then mapped through the view, exactly as the lines over it are.
    const index = this.results.cellAt(view.normal, at);
    const slice = this.results.slice(spec.field, view.normal, index);
    const [uAxis, vAxis] = [0, 1, 2].filter((a) => a !== view.normal);
    const n = [this.results.nx, this.results.ny, this.results.nz];
    const origin = this.results.origin;
    const size = this.results.size;
    const du = size[uAxis] / n[uAxis];
    const dv = size[vAxis] / n[vAxis];
    const near = [0, 0, 0]; // the cell's low corner, in room metres
    const far = [0, 0, 0]; // and its high corner
    for (let v = 0; v < slice.height; v += 1) {
      near[vAxis] = origin[vAxis] + v * dv;
      far[vAxis] = near[vAxis] + dv;
      for (let u = 0; u < slice.width; u += 1) {
        const value = slice.values[v * slice.width + u];
        const t = this.scale.position(value);
        const k = Math.min(255, Math.max(0, Math.round(t * 255))) * 3;
        ctx.fillStyle = `rgb(${this.lut[k]},${this.lut[k + 1]},${this.lut[k + 2]})`;
        near[uAxis] = origin[uAxis] + u * du;
        far[uAxis] = near[uAxis] + du;
        const px = X(near[view.h]);
        const py = Y(far[view.v]);
        ctx.fillRect(px, py, X(far[view.h]) - px + 0.5, Y(near[view.v]) - py + 0.5);
      }
    }

    const svg = drawView(this.model, view, sheet, { at, transparent: true });
    stage.replaceChildren(canvas, svg);
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    this.#hover(svg, view, index, X, Y, spec);
  }

  /** Numeric readout under the cursor -- a colour is never the whole answer. */
  #hover(svg, view, index, X, Y, spec) {
    const probe = this.host.querySelector('#map-probe');
    const d = this.model.domain;
    const n = [this.results.nx, this.results.ny, this.results.nz];
    svg.addEventListener('pointermove', (event) => {
      const rect = svg.getBoundingClientRect();
      const sx = (event.clientX - rect.left) * (svg.viewBox.baseVal.width / rect.width);
      const sy = (event.clientY - rect.top) * (svg.viewBox.baseVal.height / rect.height);
      // invert the transform
      const h = d.lo[view.h] + (sx - X(d.lo[view.h])) / (X(d.hi[view.h]) - X(d.lo[view.h])) * (d.hi[view.h] - d.lo[view.h]);
      const v = d.hi[view.v] - (sy - Y(d.hi[view.v])) / (Y(d.lo[view.v]) - Y(d.hi[view.v])) * (d.hi[view.v] - d.lo[view.v]);
      if (h < d.lo[view.h] || h > d.hi[view.h] || v < d.lo[view.v] || v > d.hi[view.v]) {
        probe.hidden = true;
        return;
      }
      const ijk = [0, 0, 0];
      ijk[view.normal] = index;
      ijk[view.h] = this.results.cellAt(view.h, h);
      ijk[view.v] = this.results.cellAt(view.v, v);
      const value = this.results.valueAt(spec.field, ...ijk);
      probe.hidden = false;
      probe.textContent =
        `${fmt(value, spec.decimals)} ${spec.units} · ` +
        `x ${fmt(this.results.coordinate(0, ijk[0]), 2)} ` +
        `y ${fmt(this.results.coordinate(1, ijk[1]), 2)} ` +
        `z ${fmt(this.results.coordinate(2, ijk[2]), 2)} m`;
      probe.style.left = `${event.clientX + 14}px`;
      probe.style.top = `${event.clientY + 14}px`;
      void n;
    });
    svg.addEventListener('pointerleave', () => {
      probe.hidden = true;
    });
  }

  #renderColorbar(spec) {
    const field = this.results.fields[spec.field];
    renderScaleBar({
      host: this.host,
      prefix: 'map-colorbar',
      scale: this.scale,
      lut: this.lut,
      field,
      spec,
    });
  }
}

/**
 * Draw a contour legend: the bands as flat blocks, the band edges labelled,
 * and any threshold the field is judged against marked on the bar.
 *
 * Shared by the field maps and the per-rack map so both read the same way.
 */
export function renderScaleBar({ host, prefix, scale, lut, field, spec }) {
  const colour = (t) => {
    const k = Math.min(255, Math.max(0, Math.round(t * 255))) * 3;
    return `rgb(${lut[k]},${lut[k + 1]},${lut[k + 2]})`;
  };

  const bands = scale.bands();
  const stops = bands.length
    ? bands.flatMap(({ lo, hi, t0, t1 }) => {
        const c = colour(scale.position((lo + hi) / 2));
        return [`${c} ${t0 * 100}%`, `${c} ${t1 * 100}%`];
      })
    : Array.from({ length: 17 }, (_, i) => `${colour(i / 16)} ${(i / 16) * 100}%`);
  host.querySelector(`#${prefix}-ramp`).style.background =
    `linear-gradient(90deg, ${stops.join(', ')})`;

  // The bar and the field are two different ranges and both have to be
  // legible. A fixed band can stop short of the data and a symmetric ramp can
  // reach past it, so the caption always states what the field actually
  // spans, and an end the field runs past is marked as saturating.
  const low = scale.valueAt(0);
  const high = scale.valueAt(1);
  const under = field.min < low - 1e-9;
  const over = field.max > high + 1e-9;
  host.querySelector(`#${prefix}-name`).textContent =
    `${spec.label} (${spec.units})` +
    (scale.step ? ` · bands of ${fmt(scale.step, spec.decimals)}` : '') +
    ` · field from ${fmt(field.min, spec.decimals)} to ${fmt(field.max, spec.decimals)}` +
    (under || over ? ' (the ends saturate)' : '');

  // Label the band edges, thinning them out so nothing collides.
  const edges = bands.length
    ? [...bands.map((b) => ({ value: b.lo, t: b.t0 })), {
        value: bands[bands.length - 1].hi,
        t: bands[bands.length - 1].t1,
      }]
    : scale.ticks(5).map(({ t, value }) => ({ value, t }));
  const stride = Math.ceil(edges.length / 9);
  host.querySelector(`#${prefix}-ticks`).innerHTML = edges
    .map((edge, i) => {
      const last = i === edges.length - 1;
      if (i % stride !== 0 && !last) return '';
      const mark = (i === 0 && under) || (last && over) ? (i === 0 ? '−' : '+') : '';
      return `<span style="left:${edge.t * 100}%">${fmt(edge.value, spec.decimals)}${mark}</span>`;
    })
    .join('');

  const marks = host.querySelector(`#${prefix}-marks`);
  if (marks) {
    marks.innerHTML = (spec.marks || [])
      .filter((m) => m.value > low && m.value < high)
      .map(
        (m) =>
          `<span style="left:${scale.positionOf(m.value) * 100}%">${m.label}</span>`,
      )
      .join('');
  }
}

const fmt = (v, places) =>
  Number(v).toLocaleString('en-US', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}
