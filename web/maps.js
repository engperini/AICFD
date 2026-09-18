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
 * Colour follows the job of the data (see colormaps.js): temperature diverges
 * about the middle of the ASHRAE recommended band, pressure diverges about the
 * fan intake, speed is a single hue from still to fast.
 */

import { viewsFor, drawView, defaultCut, sheetScale, viewTransform } from './drawing.js';
import { Scale, buildLut } from './colormaps.js';

const ASHRAE_MID = (18 + 27) / 2;

export const FIELDS = {
  temperature: {
    label: 'Temperatura',
    field: 'T',
    kind: 'diverging',
    units: '°C',
    decimals: 1,
    scaleFor: (results) => {
      const { min, max } = results.fields.T;
      return new Scale({ kind: 'diverging', min, max, center: ASHRAE_MID });
    },
    caption: 'Azul: abaixo do meio da faixa ASHRAE (22,5 °C). Vermelho: acima.',
  },
  speed: {
    label: 'Velocidade',
    field: 'speed',
    kind: 'sequential',
    units: 'm/s',
    decimals: 2,
    scaleFor: (results) =>
      new Scale({ kind: 'sequential', min: 0, max: results.fields.speed.max }),
    caption: 'Ar parado ao lado de um rack é onde o calor acumula.',
  },
  pressure: {
    label: 'Pressão',
    field: 'P',
    kind: 'diverging',
    units: 'Pa',
    decimals: 1,
    scaleFor: (results) => {
      const { min, max } = results.fields.P;
      return new Scale({ kind: 'diverging', min, max, center: 0 });
    },
    caption:
      'Relativa à tomada do fan wall, sem a coluna hidrostática — o que um manômetro leria.',
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
    this.cells = [];
    this.#build();
    window.addEventListener('resize', debounce(() => this.render(), 150));
  }

  #build() {
    this.host.innerHTML = `
      <div class="maps-controls">
        <div class="control">
          <label for="map-field">Campo</label>
          <select id="map-field">${Object.entries(FIELDS)
            .filter(([, f]) => this.results.fields[f.field])
            .map(([k, f]) => `<option value="${k}">${f.label}</option>`)
            .join('')}</select>
        </div>
        <span class="card-sub" id="map-caption"></span>
      </div>
      <div class="views maps${this.views[0].long ? ' long' : ''}" id="map-views"></div>
      <div class="colorbar">
        <div class="colorbar-label">
          <span id="map-colorbar-name"></span>
        </div>
        <div class="colorbar-ramp" id="map-colorbar-ramp"></div>
        <div class="colorbar-ticks" id="map-colorbar-ticks"></div>
      </div>
      <div class="readout" id="map-probe" hidden></div>`;

    this.host.querySelector('#map-field').addEventListener('change', (e) => {
      this.fieldKey = e.target.value;
      this.render();
    });

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
                 step="${step}" value="${this.cuts[view.id]}" aria-label="posição do corte" />
        </div>
        <div class="map-stage" data-role="stage"></div>`;
      viewsHost.append(cell);
      const slider = cell.querySelector('[data-role="slider"]');
      slider.addEventListener('input', () => {
        this.cuts[view.id] = Number(slider.value);
        this.renderView(view, cell);
      });
      this.cells.push({ view, cell });
    }
    this.render();
  }

  render() {
    const mode = this.currentMode();
    const spec = FIELDS[this.fieldKey];
    this.scale = spec.scaleFor(this.results);
    this.lut = buildLut(spec.kind, mode);
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
      `corte em ${axisName} = ${fmt(at, 2)} m`;

    const { width, height, X, Y } = viewTransform(this.model, view, this.sheet);
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

    const svg = drawView(this.model, view, this.sheet, { at, transparent: true });
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
    const ramp = this.host.querySelector('#map-colorbar-ramp');
    const stops = [];
    for (let i = 0; i <= 16; i += 1) {
      const k = Math.round((i / 16) * 255) * 3;
      stops.push(`rgb(${this.lut[k]},${this.lut[k + 1]},${this.lut[k + 2]}) ${(i / 16) * 100}%`);
    }
    ramp.style.background = `linear-gradient(90deg, ${stops.join(', ')})`;
    // A diverging ramp is symmetric about its centre, so its ends can sit
    // beyond the data. Say what the field actually spans, so 13,9 degC on the
    // bar is never read as air that exists.
    const field = this.results.fields[spec.field];
    this.host.querySelector('#map-colorbar-name').textContent =
      `${spec.label} (${spec.units}) — campo de ${fmt(field.min, spec.decimals)} a ${fmt(field.max, spec.decimals)}`;
    this.host.querySelector('#map-colorbar-ticks').innerHTML = this.scale
      .ticks(5)
      .map(({ value }) => `<span>${fmt(value, spec.decimals)}</span>`)
      .join('');
  }
}

const fmt = (v, places) =>
  Number(v).toLocaleString('pt-BR', {
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
