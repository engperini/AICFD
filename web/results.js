/**
 * Wiring: load a solved case and lay out the four things it is read by --
 * the plan and sections over the field, the inlet of every rack, the
 * validation checks, and the residual history.
 *
 * Which case is shown comes from `?case=<name>`, resolved against `../results/`.
 */

import { Results } from './data.js';
import { ConvergenceChart } from './convergence.js';
import { FieldMaps } from './maps.js';
import { RackInlets } from './rack-inlets.js';
// Keeps the case on the way back, so `back` returns to the model page this came from
// rather than to the one the server was started with (ADR-091).
import { withCase } from './case.js';
// The ramp the maps are being shown in: the document follows the screen it
// was asked for from (ADR-101).
import { rememberedRamp } from './colormaps.js';
// What this room's machines are called. One rule, in the model: a tile
// reading `Fan wall` over a hall cooled by five CRACs is wrong about the
// room, not about the number under it (ADR-102).
import { unitNaming } from './drawing.js';

main();

async function main() {
  const params = new URLSearchParams(location.search);
  const caseName = params.get('case') || 'reference-case';
  const root = document.getElementById('root');

  // Yours first, the one shipped with the repository second. A result you
  // produce shadows the worked one of the same name, which is what keeps
  // `results/` out of version control and re-running a worked case from
  // colliding with the copy in the repository (ADR-032).
  let results;
  let shipped = false;
  try {
    results = await Results.load(`../results/${caseName}/`);
  } catch (mine) {
    try {
      results = await Results.load(`../reference/${caseName}/`);
      shipped = true;
    } catch (theirs) {
      root.innerHTML =
        `<div class="error"><strong>Could not load case "${caseName}".</strong>` +
        `<p>${mine.message}</p><p>${theirs.message}</p>` +
        `<code>aicfd post ${caseName}</code></div>`;
      return;
    }
  }

  const meta = results.meta;
  document.getElementById('case-name').textContent =
    `${meta.case} · t = ${meta.time} · ${meta.kpis.cells.toLocaleString()} cells`;
  if (shipped) {
    const badge = document.createElement('span');
    badge.className = 'badge';
    badge.textContent = 'shipped with the repository';
    badge.title =
      'This is the worked result the repository carries, not one solved here. '
      + `Run the case and it is replaced by yours: results/${caseName}/.`;
    document.querySelector('.topbar').insertBefore(
      badge, document.getElementById('verdict'),
    );
  }
  announceIfSuperseded(caseName);
  offerTheCommands(caseName);
  const verdict = document.getElementById('verdict');
  verdict.hidden = false;
  verdict.dataset.state = meta.valid ? 'pass' : 'fail';
  verdict.textContent = meta.valid
    ? '✓ Validation passed'
    : '✕ Validation failed';

  root.innerHTML = layoutHtml(meta);

  // Plan and sections over the field are how a result is read: the same
  // drawings that were checked before the run, now with the solved field
  // underneath them (ADR-024).
  let maps = null;
  let racks = null;
  if (meta.model) {
    maps = new FieldMaps(document.getElementById('maps'), results, meta.model, currentMode);
    if (meta.kpis.zones.length) {
      racks = new RackInlets(document.getElementById('racks'), meta, meta.model, currentMode);
    } else {
      document.getElementById('racks-card').hidden = true;
    }
  } else {
    // An export from before the model payload existed: the checks and the
    // residuals still read, the drawings cannot.
    document.getElementById('maps-card').hidden = true;
    document.getElementById('racks-card').hidden = true;
  }
  const chart = new ConvergenceChart(
    document.getElementById('convergence'),
    meta.residuals,
  );
  document.getElementById('legend').innerHTML = chart.legendHtml();
  document.getElementById('residual-table').innerHTML = residualTableHtml(chart);

  setupTheme(maps, racks);

  document.getElementById('toggle-table').addEventListener('click', (event) => {
    const table = document.getElementById('table-view');
    table.hidden = !table.hidden;
    event.target.textContent = table.hidden ? 'Show values' : 'Hide values';
  });
}

function setupTheme(maps, racks) {
  const button = document.getElementById('theme-toggle');
  const apply = () => {
    const mode = currentMode();
    button.textContent = mode === 'dark' ? 'Light' : 'Dark';
    // Dark mode is its own ramp, not a flipped one -- repaint both maps.
    maps?.render();
    racks?.render();
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

/** The room's machines, named as the model names them, capitalised for a
 * tile: `Fan wall`, `CRAC`, `CRAH`. */
function unitName(meta) {
  const noun = unitNaming(meta?.model).noun;
  return noun.charAt(0).toUpperCase() + noun.slice(1);
}

function layoutHtml(meta) {
  const kpis = meta.kpis;
  // Judged at the top of the rack when the export carries it -- the worst
  // point -- and at the face mean otherwise (older exports).
  const worst = (z) => z.inlet_top_c ?? z.inlet_temp_c;
  const warmest = kpis.zones.length
    ? kpis.zones.reduce((a, b) => (worst(a) > worst(b) ? a : b))
    : null;
  const over = kpis.zones.filter((z) => z.ashrae.above_recommended).length;

  return `
<main class="layout">
  <div class="column">
  <section class="card" id="maps-card">
    <div class="card-head">
      <span class="card-title">Plan and sections</span>
      <span class="card-sub">the solved field under the same drawings checked before the run; drag the cut</span>
    </div>
    <div id="maps"></div>
  </section>
  <section class="card" id="racks-card">
    <div class="card-head">
      <span class="card-title">Rack inlets</span>
      <span class="card-sub">every rack by the air it breathes; the worst rack is what decides the concept</span>
    </div>
    <div id="racks"></div>
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
          warmest ? `${worst(warmest).toFixed(1)} °C` : '—'
        }</div>
        <div class="hero-note">${
          warmest
            ? `${warmest.name}${warmest.inlet_top_c != null ? ', top of the rack' : ''} · ${warmest.ashrae.verdict}` +
              (kpis.zones.length > 1
                ? ` · ${over} of ${kpis.zones.length} racks above recommended`
                : '')
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
        ${
          kpis.fan_rise_pa == null
            ? ''
            : tile(
                kpis.fan_static_pa
                  ? `${unitName(meta)} (of ${kpis.fan_static_pa.toFixed(0)} Pa)`
                  : unitName(meta),
                kpis.fan_rise_pa.toFixed(1),
                'Pa',
              )
        }
        ${
          kpis.rack_drop_pa == null
            ? ''
            : tile('Across the racks', kpis.rack_drop_pa.toFixed(1), 'Pa')
        }
        ${
          kpis.energy_closure == null
            ? ''
            : tile('Load in return air', (kpis.energy_closure * 100).toFixed(0), '%')
        }
      </div>
    </section>

    <section class="card">
      <div class="card-head"><span class="card-title">Physical validation</span></div>
      <ul class="checks">${checksHtml(meta.checks)}</ul>
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

/** Per-rack checks repeat once per rack. Collapse a group when all of it
 * passes -- six identical PASS rows are noise that buries the one failure. A
 * group with any failure is listed in full. */
function checksHtml(checks) {
  const groups = new Map();
  for (const check of checks) {
    const key = groupKeyFor(check.name);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(check);
  }

  const rows = [];
  for (const [key, members] of groups) {
    if (key && members.length > 1 && members.every((c) => c.passed)) {
      rows.push(`<li data-state="group">
        <span class="mark" aria-hidden="true">✓</span>
        <span><span class="name">PASS — ${members.length} ${key}</span><br />
        <span class="group-detail">${members.map((c) => c.name).join(', ')}</span></span>
      </li>`);
      continue;
    }
    for (const check of members) {
      rows.push(`<li data-state="${check.passed ? 'pass' : 'fail'}">
        <span class="mark" aria-hidden="true">${check.passed ? '✓' : '✕'}</span>
        <span><span class="name">${check.status} — ${check.name}</span><br />
        <span class="detail">${check.detail}</span></span>
      </li>`);
    }
  }
  return rows.join('');
}

function groupKeyFor(name) {
  if (/^zone_.+_populated$/.test(name)) return 'rack zones populated';
  if (/^ashrae_/.test(name)) return 'racks within their ASHRAE envelope';
  return null;
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


/**
 * Say so when this result has been overtaken by a run still in flight.
 *
 * An export is a file. Nothing in it can know that the same case is being
 * re-solved right now, so a reader opening this page mid-run sees the
 * PREVIOUS answer with the current case's name on it and no hint of that.
 *
 * Asking the server closes that hole where a server exists. It fails silently
 * on purpose: this page is static by design and has to work from a plain file
 * server, from disk, or from anywhere `results/` has been copied to -- and
 * in those places there is no run to be overtaken by (ADR-030).
 */
async function announceIfSuperseded(caseName) {
  let run;
  try {
    run = (await (await fetch(withCase('../api/progress'))).json()).run;
  } catch {
    return; // no server, or not ours: nothing to say
  }
  if (!run || run.case !== caseName) return;
  if (!['meshing', 'solving', 'exporting'].includes(run.stage)) return;
  const bar = document.querySelector('.topbar');
  const note = document.createElement('span');
  note.className = 'badge';
  note.dataset.state = 'fail';
  note.textContent = '⟳ a run is in flight — this is the previous result';
  note.title =
    `Case "${caseName}" is being solved again (${run.stage}). What is on this ` +
    'page is the export from before that run, and it will be replaced when ' +
    'the run finishes.';
  bar.insertBefore(note, document.getElementById('verdict').nextSibling);
}


/**
 * The two things that were only ever commands: build the Word report, and
 * post-process the solved run again.
 *
 * Offered only when the API answers. The results page is a static page over
 * two files, and it has to stay one -- an export copied onto a laptop with no
 * Python on it still reads. So the buttons appear if there is a server behind
 * the page and simply do not exist if there is not, rather than appearing and
 * failing.
 *
 * Deliberately not buttons: new, build, doctor and verify. Those make or
 * check a case, and none of them belongs on a page about a finished result.
 */
async function offerTheCommands(caseName) {
  let can;
  try {
    const response = await fetch(`../api/commands?case=${encodeURIComponent(caseName)}`);
    if (!response.ok) return;
    can = await response.json();
    if (can.error) return;
  } catch {
    return; // a static copy of the export: there is nothing to call
  }
  const report = document.getElementById('write-report');
  const reread = document.getElementById('reread');
  report.hidden = !can.report;
  // Shown only when there is a solved case to re-read. A button that appears
  // and then explains why it could not work teaches the reader to distrust
  // the others.
  reread.hidden = !can.reread;
  reread.title = can.reread_note || '';
  if (can.report) report.addEventListener('click', () => openReportSheet(caseName));
  if (can.reread) reread.addEventListener('click', () => rereadRun(caseName, reread));
  if (!can.report) return;
  document.getElementById('report-cancel').addEventListener('click', closeReportSheet);
  document.getElementById('report-sheet').addEventListener('click', (event) => {
    if (event.target.id === 'report-sheet') closeReportSheet();
  });
  document.getElementById('report-form').addEventListener('submit', (event) => {
    event.preventDefault();
    downloadReport(caseName);
  });
}

const REMEMBERED = 'aicfd-report-cover';

function openReportSheet(caseName) {
  const form = document.getElementById('report-form');
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(REMEMBERED) || '{}');
  } catch { /* private window, or nothing stored: the blanks are fine */ }
  form.client.value = saved.client || '';
  form.author.value = saved.author || '';
  form.title.value = saved.title || '';
  form.title.placeholder = caseName;
  document.getElementById('report-sheet').hidden = false;
  form.client.focus();
}

function closeReportSheet() {
  document.getElementById('report-sheet').hidden = true;
}

async function downloadReport(caseName) {
  const form = document.getElementById('report-form');
  const note = document.getElementById('report-note');
  const go = document.getElementById('report-go');
  const cover = {
    client: form.client.value,
    author: form.author.value,
    title: form.title.value,
  };
  try {
    localStorage.setItem(REMEMBERED, JSON.stringify(cover));
  } catch { /* nothing to remember it with; the report is built either way */ }
  go.disabled = true;
  note.textContent = 'Building — the figures are drawn from the fields, so '
    + 'this takes a few seconds.';
  try {
    const response = await fetch(
      `../api/report?case=${encodeURIComponent(caseName)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // The cover is what the reader typed and is remembered as such; the
        // ramp is a view setting that belongs to the maps, so it rides along
        // with the request without being stored as part of the cover.
        body: JSON.stringify({ ...cover, ramp: rememberedRamp() }),
      },
    );
    const type = response.headers.get('Content-Type') || '';
    if (!response.ok || type.includes('application/json')) {
      const payload = await response.json().catch(() => ({}));
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    saveBlob(await response.blob(),
             filenameFrom(response) || `${caseName}-cfd-report.docx`);
    note.textContent = 'Downloaded.';
    setTimeout(closeReportSheet, 900);
  } catch (error) {
    note.textContent = `Not built: ${error.message}`;
  } finally {
    go.disabled = false;
  }
}

function filenameFrom(response) {
  const match = /filename="([^"]+)"/.exec(
    response.headers.get('Content-Disposition') || '');
  return match ? match[1] : null;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function rereadRun(caseName, button) {
  const label = button.textContent;
  button.disabled = true;
  button.textContent = 'Re-reading…';
  try {
    const response = await fetch(
      `../api/post?case=${encodeURIComponent(caseName)}`, { method: 'POST' });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload.error) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    // The page is built from the export it loaded. A new export means a new
    // page, so reload rather than patch half of it.
    location.reload();
  } catch (error) {
    button.disabled = false;
    button.textContent = label.trim();
    button.title = `Could not re-read: ${error.message}`;
    const bar = document.querySelector('.topbar');
    const note = document.createElement('span');
    note.className = 'badge';
    note.dataset.state = 'fail';
    note.textContent = `could not re-read: ${error.message}`;
    bar.append(note);
  }
}
