/**
 * Every rack, painted by the temperature of the air it breathes in.
 *
 * This is the number a conceptual design is judged on: not the field, but the
 * inlet of the worst rack. The plan is the same drawing as the model page's,
 * with each rack filled by its inlet temperature -- at the top of the rack by
 * default, because that is where recirculating or leaking hot air arrives
 * first and so the point ASHRAE is checked against. A table lists the warmest
 * racks with every number behind the colour, and the whole set can be taken
 * away as CSV.
 *
 * One sequential ramp, light to dark: this is a magnitude, and the reader's
 * question is "which racks are warmest", not "which side of a threshold".
 * The threshold itself (27 degC recommended) is written in the table, where a
 * number belongs.
 */

import { viewsFor, drawView, sheetScale, viewTransform } from './drawing.js';
import { Scale, buildLut, niceStep } from './colormaps.js';
import { renderScaleBar } from './maps.js';

const METRICS = {
  inlet_top_c: { label: 'Top of the rack (worst point)', short: 'top' },
  inlet_temp_c: { label: 'Mean over the inlet face', short: 'mean' },
};

const ASHRAE_RECOMMENDED_MAX = 27;
const LISTED = 10;

export class RackInlets {
  /**
   * @param {HTMLElement} host
   * @param {object} meta viewer.json
   * @param {object} model the model payload (viewer.json `model`)
   * @param {() => 'light'|'dark'} currentMode
   */
  constructor(host, meta, model, currentMode) {
    this.host = host;
    this.model = model;
    this.currentMode = currentMode;
    this.metric = 'inlet_top_c';
    this.showAll = false;
    const boxes = new Map(meta.geometry.zones.map((z) => [z.name, z]));
    this.racks = meta.kpis.zones
      .filter((z) => boxes.has(z.name))
      .map((z) => ({ ...z, lo: boxes.get(z.name).lo, hi: boxes.get(z.name).hi }));
    if (this.racks.some((r) => r.inlet_top_c == null)) this.metric = 'inlet_temp_c';
    this.view = viewsFor(model).find((v) => v.id === 'plan');
    this.#build();
    window.addEventListener('resize', debounce(() => this.render(), 150));
  }

  #build() {
    const options = Object.entries(METRICS)
      .filter(([key]) => this.racks.every((r) => r[key] != null))
      .map(([key, m]) => `<option value="${key}">${m.label}</option>`)
      .join('');
    this.host.innerHTML = `
      <div class="racks-controls">
        <div class="control">
          <label for="rack-metric">Paint by</label>
          <select id="rack-metric">${options}</select>
        </div>
        <button type="button" id="rack-csv">Download CSV (${this.racks.length} racks)</button>
        <span class="card-sub" id="rack-caption"></span>
      </div>
      <div class="racks-stage"><div class="wrap" id="rack-stage"></div></div>
      <div class="colorbar">
        <div class="colorbar-label"><span id="rack-colorbar-name"></span></div>
        <div class="scale-bar">
          <div class="colorbar-ramp" id="rack-colorbar-ramp"></div>
          <div class="scale-ticks" id="rack-colorbar-ticks"></div>
        </div>
      </div>
      <div class="racks-table" id="rack-table"></div>
      <div class="racks-note" id="rack-note"></div>
      <div class="readout" id="rack-probe" hidden></div>`;
    const select = this.host.querySelector('#rack-metric');
    select.value = this.metric;
    select.addEventListener('change', () => {
      this.metric = select.value;
      this.render();
    });
    this.host.querySelector('#rack-csv').addEventListener('click', () => this.download());
    this.render();
  }

  get scale() {
    // Fitted, not fixed: this card's job is to rank racks whose inlets can
    // differ by less than a kelvin, and the fixed band the field maps use
    // (ADR-024) would paint all of them one colour. Banded on a round step
    // all the same, so the legend can be read as numbers.
    const values = this.racks.map((r) => r[this.metric]);
    const low = Math.min(...values);
    const high = Math.max(Math.max(...values), low + 0.5);
    const step = niceStep(high - low, 8);
    return new Scale({
      kind: 'sequential',
      min: Math.floor(low / step) * step,
      max: Math.ceil(high / step) * step,
      step,
    });
  }

  render() {
    if (!this.racks.length) return;
    const mode = this.currentMode();
    const lut = buildLut('sequential', mode);
    const scale = this.scale;
    const metric = METRICS[this.metric];
    this.host.querySelector('#rack-caption').textContent =
      `Every rack by its inlet temperature (${metric.short}). ` +
      `ASHRAE recommends up to ${ASHRAE_RECOMMENDED_MAX} °C.`;

    const stage = this.host.querySelector('#rack-stage');
    const outer = stage.parentElement;
    const sheet = sheetScale(this.model, [this.view], [outer.clientWidth - 24]);
    const { width, height, X, Y } = viewTransform(this.model, this.view, sheet);
    const view = this.view;

    const canvas = document.createElement('canvas');
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d');
    ctx.scale(dpr, dpr);
    for (const rack of this.racks) {
      const t = scale.position(rack[this.metric]);
      const k = Math.min(255, Math.max(0, Math.round(t * 255))) * 3;
      ctx.fillStyle = `rgb(${lut[k]},${lut[k + 1]},${lut[k + 2]})`;
      const x0 = X(Math.min(rack.lo[view.h], rack.hi[view.h]));
      const x1 = X(Math.max(rack.lo[view.h], rack.hi[view.h]));
      const y0 = Y(Math.max(rack.lo[view.v], rack.hi[view.v]));
      const y1 = Y(Math.min(rack.lo[view.v], rack.hi[view.v]));
      ctx.fillRect(x0, y0, Math.max(x1 - x0, 1), Math.max(y1 - y0, 1));
    }

    const svg = drawView(this.model, view, sheet, { transparent: true });
    stage.replaceChildren(canvas, svg);
    stage.style.width = `${width}px`;
    stage.style.height = `${height}px`;
    this.#hover(svg, X, Y);
    this.#colorbar(lut, scale, metric);
    this.#table();
  }

  #hover(svg, X, Y) {
    const probe = this.host.querySelector('#rack-probe');
    const view = this.view;
    svg.addEventListener('pointermove', (event) => {
      const rect = svg.getBoundingClientRect();
      const sx = (event.clientX - rect.left) * (svg.viewBox.baseVal.width / rect.width);
      const sy = (event.clientY - rect.top) * (svg.viewBox.baseVal.height / rect.height);
      const hit = this.racks.find((r) => {
        const x0 = X(Math.min(r.lo[view.h], r.hi[view.h]));
        const x1 = X(Math.max(r.lo[view.h], r.hi[view.h]));
        const y0 = Y(Math.max(r.lo[view.v], r.hi[view.v]));
        const y1 = Y(Math.min(r.lo[view.v], r.hi[view.v]));
        return sx >= x0 && sx <= x1 && sy >= y0 && sy <= y1;
      });
      if (!hit) {
        probe.hidden = true;
        return;
      }
      probe.hidden = false;
      probe.textContent =
        `${hit.name} · inlet ${fmt(hit.inlet_temp_c, 1)} °C, ` +
        `top ${fmt(hit.inlet_top_c ?? hit.inlet_temp_c, 1)} °C, ` +
        `outlet ${fmt(hit.peak_temp_c, 1)} °C · ${hit.ashrae.verdict}`;
      probe.style.left = `${event.clientX + 14}px`;
      probe.style.top = `${event.clientY + 14}px`;
    });
    svg.addEventListener('pointerleave', () => {
      probe.hidden = true;
    });
  }

  #colorbar(lut, scale, metric) {
    const values = this.racks.map((r) => r[this.metric]);
    renderScaleBar({
      host: this.host,
      prefix: 'rack-colorbar',
      scale,
      lut,
      field: { min: Math.min(...values), max: Math.max(...values) },
      spec: {
        label: `Rack inlet, ${metric.short}`,
        units: '°C',
        decimals: 1,
      },
    });
  }

  #table() {
    const sorted = [...this.racks].sort((a, b) => b[this.metric] - a[this.metric]);
    const listed = this.showAll ? sorted : sorted.slice(0, LISTED);
    const over = this.racks.filter((r) => r.ashrae.above_recommended).length;
    const values = this.racks.map((r) => r[this.metric]);
    const rows = listed
      .map(
        (r) => `<tr>
          <td>${r.name}</td>
          <td>${r.row ?? '—'}</td>
          <td>${r.position ?? '—'}</td>
          <td>${fmt(r.inlet_temp_c, 1)}</td>
          <td class="${r.ashrae.above_recommended ? 'over' : ''}">${fmt(r.inlet_top_c ?? r.inlet_temp_c, 1)}</td>
          <td>${fmt(r.peak_temp_c, 1)}</td>
          <td>${fmt(r.peak_temp_c - r.inlet_temp_c, 1)}</td>
          <td>${r.ashrae.within_recommended ? 'ok' : r.ashrae.verdict}</td>
        </tr>`,
      )
      .join('');
    this.host.querySelector('#rack-table').innerHTML = `<table>
      <thead><tr><th>Rack</th><th>Row</th><th>Pos.</th><th>Inlet °C</th>
      <th>Top °C</th><th>Outlet °C</th><th>ΔT K</th><th>ASHRAE</th></tr></thead>
      <tbody>${rows}</tbody></table>`;
    const note = this.host.querySelector('#rack-note');
    const more = sorted.length > LISTED;
    note.innerHTML =
      `${this.racks.length} racks · inlet at the top from ${fmt(Math.min(...values), 1)} to ` +
      `${fmt(Math.max(...values), 1)} °C · ${over} above recommended.` +
      (more
        ? ` <button type="button" id="rack-more" class="linkish">${
            this.showAll ? `Show only the ${LISTED} warmest` : `Show all ${sorted.length}`
          }</button>`
        : '');
    const button = note.querySelector('#rack-more');
    if (button) {
      button.addEventListener('click', () => {
        this.showAll = !this.showAll;
        this.#table();
      });
    }
  }

  download() {
    const header = [
      'rack', 'row', 'position', 'load_kw', 'inlet_mean_c', 'inlet_top_c',
      'outlet_c', 'delta_t_k', 'ashrae',
    ];
    const lines = this.racks.map((r) =>
      [
        r.name, r.row ?? '', r.position ?? '', (r.load_w / 1000).toFixed(2),
        r.inlet_temp_c.toFixed(2), (r.inlet_top_c ?? r.inlet_temp_c).toFixed(2),
        r.peak_temp_c.toFixed(2), (r.peak_temp_c - r.inlet_temp_c).toFixed(2),
        r.ashrae.verdict,
      ].join(';'),
    );
    const blob = new Blob([[header.join(';'), ...lines].join('\n')], {
      type: 'text/csv;charset=utf-8',
    });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = 'racks.csv';
    link.click();
    URL.revokeObjectURL(link.href);
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
