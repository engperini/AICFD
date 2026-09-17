/**
 * Wiring: load a case, build the room view, the KPI panel and the residual chart.
 *
 * Which case is shown comes from `?case=<name>`, resolved against `../results/`.
 */

import { Results } from './data.js';
import { RoomScene } from './scene.js';
import { Scale, buildLut } from './colormaps.js';
import { ConvergenceChart } from './convergence.js';

const ASHRAE_RECOMMENDED = [18, 27];

// Each entry pairs a field with the ramp its meaning calls for -- see colormaps.js.
const FIELD_VIEWS = {
  temperature: {
    label: 'Air temperature vs. ASHRAE',
    field: 'T',
    kind: 'diverging',
    units: '°C',
    scaleFor: (results) => {
      const { min, max } = results.fields.T;
      return new Scale({
        kind: 'diverging',
        min,
        max,
        center: (ASHRAE_RECOMMENDED[0] + ASHRAE_RECOMMENDED[1]) / 2,
      });
    },
    caption: 'Blue: below the 18–27 °C recommended band. Red: above it.',
  },
  rise: {
    label: 'Temperature rise above supply',
    field: 'T',
    kind: 'sequential',
    units: 'K',
    scaleFor: (results, meta) => {
      const supply = meta.kpis.supply_temp_c;
      return new Scale({
        kind: 'sequential',
        min: supply,
        max: results.fields.T.max,
      });
    },
    caption: 'Heat the air has picked up since leaving the CRAC.',
  },
  speed: {
    label: 'Air speed',
    field: 'speed',
    kind: 'sequential',
    units: 'm/s',
    scaleFor: (results) =>
      new Scale({ kind: 'sequential', min: 0, max: results.fields.speed.max }),
    caption: 'Stagnant air next to a rack is where heat accumulates.',
  },
};

const AXES = [
  { value: 2, label: 'height (z)' },
  { value: 1, label: 'across (y)' },
  { value: 0, label: 'along flow (x)' },
];

main();

async function main() {
  const params = new URLSearchParams(location.search);
  const caseName = params.get('case') || 'reference-case';
  const root = document.getElementById('root');

  let results;
  try {
    results = await Results.load(`../results/${caseName}/`);
  } catch (error) {
    root.innerHTML =
      `<div class="error"><strong>Could not load case "${caseName}".</strong>` +
      `<p>${error.message}</p><code>aicfd post ${caseName}</code></div>`;
    return;
  }

  const meta = results.meta;
  document.getElementById('case-name').textContent =
    `${meta.case} · t = ${meta.time} · ${meta.kpis.cells.toLocaleString()} cells`;
  const verdict = document.getElementById('verdict');
  verdict.hidden = false;
  verdict.dataset.state = meta.valid ? 'pass' : 'fail';
  verdict.textContent = meta.valid
    ? '✓ Validation passed'
    : '✕ Validation failed';

  root.innerHTML = layoutHtml(meta);
  const scene = new RoomScene(document.getElementById('viewport'), results);
  const chart = new ConvergenceChart(
    document.getElementById('convergence'),
    meta.residuals,
  );
  document.getElementById('legend').innerHTML = chart.legendHtml();
  document.getElementById('residual-table').innerHTML = residualTableHtml(chart);

  setupTheme(scene);
  setupControls(scene, results, meta);
  setupReadout(scene);

  document.getElementById('toggle-table').addEventListener('click', (event) => {
    const table = document.getElementById('table-view');
    table.hidden = !table.hidden;
    event.target.textContent = table.hidden ? 'Show values' : 'Hide values';
  });
}

// --- controls ---------------------------------------------------------------

function setupControls(scene, results, meta) {
  const viewSelect = document.getElementById('field-view');
  const axisSelect = document.getElementById('slice-axis');
  const slider = document.getElementById('slice-index');
  const readoutValue = document.getElementById('slice-value');
  const caption = document.getElementById('colorbar-caption');

  const apply = () => {
    const view = FIELD_VIEWS[viewSelect.value];
    const scale = view.scaleFor(results, meta);
    scene.setField(view.field, scale, view.kind);
    renderColorbar(view, scale, currentMode());
    caption.textContent = view.caption;
  };

  const applySlice = () => {
    const axis = Number(axisSelect.value);
    const divisions = meta.grid.divisions[axis];
    if (Number(slider.max) !== divisions - 1) {
      slider.max = divisions - 1;
      slider.value = Math.floor(divisions / 2);
    }
    scene.setSlice(axis, Number(slider.value));
    readoutValue.textContent = scene.sliceLabel();
  };

  viewSelect.addEventListener('change', apply);
  axisSelect.addEventListener('change', applySlice);
  slider.addEventListener('input', applySlice);

  applySlice();
  apply();
  scene.refreshColorbar = () => apply();
}

function setupReadout(scene) {
  const readout = document.getElementById('probe');
  scene.onProbe = (probe) => {
    if (!probe) {
      readout.hidden = true;
      return;
    }
    const [x, y, z] = probe.point;
    readout.hidden = false;
    readout.innerHTML =
      `<strong>${probe.values.T.toFixed(2)} °C</strong> · ` +
      `${probe.values.speed.toFixed(2)} m/s<br />` +
      `x ${x.toFixed(2)} · y ${y.toFixed(2)} · z ${z.toFixed(2)} m`;
  };
}

function setupTheme(scene) {
  const button = document.getElementById('theme-toggle');
  const apply = () => {
    const mode = currentMode();
    button.textContent = mode === 'dark' ? 'Light' : 'Dark';
    const styles = getComputedStyle(document.documentElement);
    scene.applyTheme(mode, {
      axis: styles.getPropertyValue('--axis').trim(),
      grid: styles.getPropertyValue('--grid').trim(),
      accent: styles.getPropertyValue('--scene-accent').trim(),
      zoneFill: styles.getPropertyValue('--scene-zone').trim(),
      zoneEdge: styles.getPropertyValue('--scene-zone-edge').trim(),
    });
    scene.refreshColorbar?.();
  };
  button.addEventListener('click', () => {
    document.documentElement.dataset.theme = currentMode() === 'dark' ? 'light' : 'dark';
    apply();
  });
  matchMedia('(prefers-color-scheme: dark)').addEventListener('change', apply);
  apply();
}

function currentMode() {
  const stamped = document.documentElement.dataset.theme;
  if (stamped) return stamped;
  return matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
}

// --- rendering --------------------------------------------------------------

function renderColorbar(view, scale, mode) {
  const lut = buildLut(view.kind, mode, 32);
  const stops = [];
  for (let i = 0; i < 32; i += 1) {
    stops.push(
      `rgb(${lut[i * 3]},${lut[i * 3 + 1]},${lut[i * 3 + 2]}) ${(i / 31) * 100}%`,
    );
  }
  document.getElementById('colorbar-ramp').style.background =
    `linear-gradient(90deg, ${stops.join(',')})`;
  document.getElementById('colorbar-name').textContent =
    `${view.label} (${view.units})`;
  document.getElementById('colorbar-ticks').innerHTML = scale
    .ticks(5)
    .map((tick) => {
      const value = view.kind === 'sequential' && view.units === 'K'
        ? tick.value - scale.min
        : tick.value;
      return `<span>${value.toFixed(1)}</span>`;
    })
    .join('');
}

function layoutHtml(meta) {
  const kpis = meta.kpis;
  const warmest = kpis.zones.length
    ? kpis.zones.reduce((a, b) => (a.inlet_temp_c > b.inlet_temp_c ? a : b))
    : null;

  return `
<main class="layout">
  <div class="column">
  <section class="card viewport-card">
    <div class="controls">
      <div class="control">
        <label for="field-view">Show</label>
        <select id="field-view">
          ${Object.entries(FIELD_VIEWS)
            .map(([key, view]) => `<option value="${key}">${view.label}</option>`)
            .join('')}
        </select>
      </div>
      <div class="control">
        <label for="slice-axis">Slice</label>
        <select id="slice-axis">
          ${AXES.map(
            (axis) =>
              `<option value="${axis.value}"${
                axis.value === 2 ? ' selected' : ''
              }>${axis.label}</option>`,
          ).join('')}
        </select>
        <input type="range" id="slice-index" min="0" max="1" value="0" />
        <span class="slice-value" id="slice-value"></span>
      </div>
    </div>
    <div class="viewport-wrap">
      <div id="viewport"></div>
      <div class="readout" id="probe" hidden></div>
    </div>
    <div class="colorbar">
      <div class="colorbar-label">
        <span id="colorbar-name"></span>
        <span class="card-sub" id="colorbar-caption"></span>
      </div>
      <div class="colorbar-ramp" id="colorbar-ramp"></div>
      <div class="colorbar-ticks" id="colorbar-ticks"></div>
    </div>
  </section>
  <section class="card chart-card">
  <div class="card-head">
    <span class="card-title">Convergence</span>
    <span class="card-sub">initial residual per iteration, log scale</span>
    <span class="spacer" style="flex:1"></span>
    <button class="ghost-button" id="toggle-table" type="button">Show values</button>
  </div>
  <div class="card-head" style="border-bottom:none;padding-bottom:4px">
    <span class="legend" id="legend"></span>
  </div>
  <div id="convergence"></div>
  <div class="table-view" id="table-view" hidden>
    <table id="residual-table"></table>
  </div>
</section>
  </div>

  <aside class="panel">
    <section class="card">
      <div class="hero">
        <div class="hero-label">Warmest rack inlet</div>
        <div class="hero-value">${
          warmest ? `${warmest.inlet_temp_c.toFixed(1)} °C` : '—'
        }</div>
        <div class="hero-note">${
          warmest
            ? `${warmest.name} · ${warmest.ashrae.verdict}`
            : 'no rack zones in this case'
        }</div>
      </div>
      <div class="tiles">
        ${tile('IT load', (kpis.total_load_w / 1000).toFixed(1), 'kW')}
        ${tile('Supply air', Math.round(kpis.supply_flow_m3h).toLocaleString(), 'm³/h')}
        ${tile('Supply temp', kpis.supply_temp_c.toFixed(1), '°C')}
        ${tile('Bulk ΔT', kpis.bulk_delta_t_k.toFixed(2), 'K')}
        ${tile('Peak air temp', kpis.temp_max_c.toFixed(1), '°C')}
        ${tile('Peak air speed', kpis.speed_max_ms.toFixed(2), 'm/s')}
      </div>
    </section>

    ${
      kpis.zones.length
        ? `<section class="card">
      <div class="card-head"><span class="card-title">Racks</span></div>
      <table>
        <thead><tr><th>Rack</th><th>Load</th><th>Inlet</th><th>Peak</th><th>Rise</th></tr></thead>
        <tbody>${kpis.zones
          .map(
            (zone) => `<tr>
              <td>${zone.name}</td>
              <td>${(zone.load_w / 1000).toFixed(1)} kW</td>
              <td>${zone.inlet_temp_c.toFixed(1)} °C</td>
              <td>${zone.peak_temp_c.toFixed(1)} °C</td>
              <td>${zone.rise_k.toFixed(2)} K</td>
            </tr>`,
          )
          .join('')}</tbody>
      </table>
    </section>`
        : ''
    }

    <section class="card">
      <div class="card-head"><span class="card-title">Physical validation</span></div>
      <ul class="checks">
        ${meta.checks
          .map(
            (check) => `<li data-state="${check.passed ? 'pass' : 'fail'}">
              <span class="mark" aria-hidden="true">${check.passed ? '✓' : '✕'}</span>
              <span><span class="name">${check.status} — ${check.name}</span><br />
              <span class="detail">${check.detail}</span></span>
            </li>`,
          )
          .join('')}
      </ul>
    </section>

    ${
      meta.warnings.length
        ? `<section class="card">
      <div class="card-head"><span class="card-title">Engineering notes</span></div>
      <ul class="notes">${meta.warnings
        .map(
          (warning) =>
            `<li><span class="mark" aria-hidden="true">!</span><span>${warning}</span></li>`,
        )
        .join('')}</ul>
    </section>`
        : ''
    }
  </aside>
</main>`;
}

function tile(label, value, unit) {
  return `<div class="tile">
    <div class="tile-label">${label}</div>
    <div class="tile-value">${value} <span class="tile-unit">${unit}</span></div>
  </div>`;
}

function residualTableHtml(chart) {
  return (
    `<thead><tr><th>Field</th><th>Final initial-residual</th></tr></thead><tbody>` +
    chart.finalValues
      .map(
        (entry) => `<tr>
        <td><span class="swatch" style="background: var(--series-${entry.slot})"></span>
        ${entry.name}</td>
        <td>${entry.value == null ? '—' : entry.value.toExponential(2)}</td>
      </tr>`,
      )
      .join('') +
    `</tbody>`
  );
}
