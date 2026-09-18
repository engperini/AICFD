/**
 * The equipment page: one unit's capacity table, drawn and editable.
 *
 * A fan wall's datasheet prints one capacity, and it is true at one return
 * air temperature — the one the unit was selected for. A room almost never
 * returns air at that temperature, so a plant compared against the catalogue
 * figure is compared against something it will not do. The table here is
 * several manufacturer selections of the same machine at the same conditions,
 * differing only in the air it receives, and every capacity number in a
 * result is read off it (ADR-036).
 *
 * Editing is deliberately plain. A unit is characterised once, from the
 * selections, and then left alone; what this page is for on a normal day is
 * *looking* at the curve that is deciding the answer.
 */

const NS = 'http://www.w3.org/2000/svg';
const params = new URLSearchParams(location.search);

let unit = null;
let draft = null;

main();

async function main() {
  wireTheme();
  const picker = document.getElementById('model-picker');
  picker.addEventListener('change', () => {
    location.search = `?model=${encodeURIComponent(picker.value)}`;
  });
  await load(params.get('model'));
}

async function load(model) {
  const root = document.getElementById('root');
  let payload;
  try {
    payload = await fetchJson(`/api/equipment${model ? `?model=${model}` : ''}`);
  } catch (error) {
    root.innerHTML =
      `<div class="error"><strong>Could not read the equipment library.</strong>` +
      `<p>${error.message}</p></div>`;
    return;
  }
  const picker = document.getElementById('model-picker');
  picker.innerHTML = payload.models
    .map((m) => `<option ${m === payload.unit?.model ? 'selected' : ''}>${m}</option>`)
    .join('');
  if (!payload.unit) {
    if (!payload.models.length) {
      root.innerHTML =
        `<div class="error"><strong>The equipment library is empty.</strong>` +
        `<p>Add a unit as <code>equipment/&lt;model&gt;.yaml</code>.</p></div>`;
      return;
    }
    location.search = `?model=${encodeURIComponent(payload.models[0])}`;
    return;
  }
  unit = payload.unit;
  draft = unit.capacity.map((row) => ({ ...row }));
  render();
}

function render() {
  document.getElementById('unit-name').textContent =
    `${unit.family} ${unit.model}`;
  const [low, high] = unit.span;
  const rise = span('nscc_kw');
  document.getElementById('root').innerHTML = `
    <div class="layout">
      <div class="column">
        <section class="card">
          <div class="card-head">
            <span class="card-title">Net sensible capacity</span>
            <span class="card-sub">against the air the unit receives</span>
          </div>
          <div id="chart"></div>
          <p class="prose">
            <b>${fmt(rise[0], 1)} kW at ${fmt(low, 0)} °C and
            ${fmt(rise[1], 1)} kW at ${fmt(high, 0)} °C</b> — the same machine,
            at the same water temperature, static pressure and fan speed. The
            only thing that changed is the air reaching the coil, and the
            capacity moved by ${fmt((rise[1] / rise[0] - 1) * 100, 0)} %.
            That is why a result is judged against this table and not against
            one catalogue figure.
          </p>
        </section>

        <section class="card">
          <div class="card-head">
            <span class="card-title">The selections</span>
            <span class="card-sub">one row per manufacturer selection; edit to
              re-characterise the unit</span>
          </div>
          <div style="overflow:auto">${tableHtml()}</div>
          <div class="actions">
            <button id="save" class="primary" type="button">Save</button>
            <button id="undo" type="button">Undo</button>
            <button id="add" type="button">Add a row</button>
            <span class="note" id="status">
              Between rows AICFD interpolates. Outside them it refuses rather
              than extrapolates — a coil curve is not a straight line.
            </span>
          </div>
        </section>
      </div>

      <aside class="column">
        <section class="card">
          <div class="card-head"><span class="card-title">Unit</span></div>
          ${factsHtml()}
        </section>
        <section class="card">
          <div class="card-head">
            <span class="card-title">Selected at</span>
            <span class="card-sub">change any of these and the table is not
              this unit's</span>
          </div>
          ${selectionHtml()}
        </section>
        ${curveHtml()}
      </aside>
    </div>`;

  drawChart();
  document.getElementById('save').addEventListener('click', save);
  document.getElementById('undo').addEventListener('click', () => {
    draft = unit.capacity.map((row) => ({ ...row }));
    render();
  });
  document.getElementById('add').addEventListener('click', () => {
    const last = draft[draft.length - 1];
    draft.push({ ...last, return_c: last.return_c + 1 });
    render();
  });
  for (const input of document.querySelectorAll('[data-row]')) {
    input.addEventListener('input', () => {
      const row = draft[Number(input.dataset.row)];
      row[input.dataset.key] = Number(input.value);
      drawChart();
    });
  }
  for (const button of document.querySelectorAll('[data-drop]')) {
    button.addEventListener('click', () => {
      draft.splice(Number(button.dataset.drop), 1);
      render();
    });
  }
}

const COLUMNS = [
  ['return_c', 'Return air °C'],
  ['nscc_kw', 'Net sensible kW'],
  ['airflow_m3h', 'Airflow m³/h'],
  ['power_kw', 'Power kW'],
  ['supply_c', 'Supply °C'],
];

function tableHtml() {
  const head = COLUMNS.map(([, label]) => `<th>${label}</th>`).join('');
  const rows = draft
    .map(
      (row, i) => `<tr>${COLUMNS.map(
        ([key]) =>
          `<td><input type="number" step="any" value="${row[key]}"
             data-row="${i}" data-key="${key}" /></td>`,
      ).join('')}<td><button data-drop="${i}" type="button"
          ${draft.length <= 2 ? 'disabled' : ''}>−</button></td></tr>`,
    )
    .join('');
  return `<table><thead><tr>${head}<th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

function factsHtml() {
  const f = unit.fans || {};
  const rows = [
    ['Model', unit.model],
    ['Family', unit.family],
    ['Size (w × d × h)', unit.size.map((v) => fmt(v, 2)).join(' × ') + ' m'],
    unit.weight_kg ? ['Weight', `${fmt(unit.weight_kg, 0)} kg`] : null,
    f.count ? ['Fans', `${f.count} × ${f.type || 'fan'}`] : null,
    f.module ? ['Fan module', f.module] : null,
    f.modulation ? ['Fan modulation', `${fmt(f.modulation, 1)} %`] : null,
  ].filter(Boolean);
  return factsTable(rows);
}

function selectionHtml() {
  const s = unit.selection || {};
  const rows = [
    s.elevation_m != null ? ['Site elevation', `${fmt(s.elevation_m, 0)} m`] : null,
    s.esp_pa != null ? ['External static pressure', `${fmt(s.esp_pa, 0)} Pa`] : null,
    s.entering_water_c != null
      ? ['Entering water', `${fmt(s.entering_water_c, 1)} °C`] : null,
    s.leaving_water_c != null
      ? ['Leaving water', `${fmt(s.leaving_water_c, 1)} °C`] : null,
    s.entering_air_rh != null
      ? ['Entering air RH', `${fmt(s.entering_air_rh, 0)} %`] : null,
  ].filter(Boolean);
  return factsTable(rows);
}

function curveHtml() {
  const c = unit.curve || {};
  if (!c.points?.length) return '';
  const measured = c.measured === true;
  return `<section class="card">
      <div class="card-head">
        <span class="card-title">P–Q curve</span>
        <span class="card-sub">${
          measured ? "the manufacturer's" : 'representative, not measured'
        }</span>
      </div>
      ${factsTable(c.points.map(([q, p]) => [`${fmt(q, 0)} m³/h`, `${fmt(p, 0)} Pa`]))}
      ${
        measured
          ? ''
          : `<p class="prose note">The selections give one point — the static
             pressure the unit was selected to overcome at its rated flow. The
             rest has the shape of an EC fan array. It decides the uncontrolled
             operating point and nothing else; replace it when you have the
             manufacturer's curve.</p>`
      }
    </section>`;
}

function factsTable(rows) {
  return `<table class="facts"><tbody>${rows
    .map(([a, b]) => `<tr><td>${a}</td><td style="text-align:right">${b}</td></tr>`)
    .join('')}</tbody></table>`;
}

/** Lowest and highest value of one column, over the draft. */
function span(key) {
  const values = draft.map((row) => Number(row[key])).filter(Number.isFinite);
  return [Math.min(...values), Math.max(...values)];
}

// --- the chart ---------------------------------------------------------------

function drawChart() {
  const host = document.getElementById('chart');
  if (!host) return;
  const width = Math.max(host.clientWidth || 640, 360);
  const height = Math.round(Math.min(380, Math.max(240, width * 0.42)));
  const pad = { left: 62, right: 16, top: 16, bottom: 44 };
  const rows = [...draft]
    .filter((r) => Number.isFinite(+r.return_c) && Number.isFinite(+r.nscc_kw))
    .sort((a, b) => a.return_c - b.return_c);
  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${width} ${height}`,
    width, height, role: 'img',
    'aria-label': 'net sensible capacity against return air temperature',
  });
  if (rows.length < 2) {
    host.replaceChildren(svg);
    return;
  }
  const xs = rows.map((r) => +r.return_c);
  const ys = rows.map((r) => +r.nscc_kw);
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  // The y axis starts at zero: this is a magnitude, and a truncated axis
  // would make a 45 % rise look like a tenfold one.
  const yMax = niceTop(Math.max(...ys));
  const X = (v) => pad.left + ((v - x0) / (x1 - x0 || 1)) * (width - pad.left - pad.right);
  const Y = (v) => height - pad.bottom - (v / yMax) * (height - pad.top - pad.bottom);

  for (let i = 0; i <= 4; i += 1) {
    const value = (yMax * i) / 4;
    svg.append(el('line', {
      x1: pad.left, x2: width - pad.right, y1: Y(value), y2: Y(value),
      stroke: 'var(--grid)', 'stroke-width': 1,
    }));
    svg.append(text(pad.left - 8, Y(value) + 4, fmt(value, 0), 'end'));
  }
  for (const value of xs) {
    svg.append(text(X(value), height - pad.bottom + 18, fmt(value, 0), 'middle'));
  }
  svg.append(text((pad.left + width - pad.right) / 2, height - 8,
    'return air temperature (°C)', 'middle', 'var(--text-secondary)'));
  svg.append(text(14, pad.top - 2, 'kW', 'start', 'var(--text-secondary)'));

  const d = rows.map((r, i) =>
    `${i ? 'L' : 'M'}${X(+r.return_c)},${Y(+r.nscc_kw)}`).join(' ');
  svg.append(el('path', {
    d, fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 2,
    'stroke-linejoin': 'round',
  }));
  for (const row of rows) {
    svg.append(el('circle', {
      cx: X(+row.return_c), cy: Y(+row.nscc_kw), r: 4,
      fill: 'var(--series-1)', stroke: 'var(--paper)', 'stroke-width': 2,
    }));
    svg.append(text(X(+row.return_c), Y(+row.nscc_kw) - 12,
      fmt(+row.nscc_kw, 0), 'middle', 'var(--text-primary)', 650));
  }
  host.replaceChildren(svg);
}

/** A top for the axis whose quarters are round numbers to read off. */
function niceTop(value) {
  const power = 10 ** Math.floor(Math.log10(value || 1));
  for (const multiple of [1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) {
    const top = multiple * power;
    if (top >= value) return top;
  }
  return value;
}

function el(tag, attrs = {}) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  return node;
}

function text(x, y, value, anchor, fill = 'var(--text-muted)', weight = 400) {
  const node = el('text', {
    x, y, 'text-anchor': anchor, fill, 'font-size': 11, 'font-weight': weight,
  });
  node.textContent = value;
  return node;
}

// --- saving -------------------------------------------------------------------

async function save() {
  const status = document.getElementById('status');
  const button = document.getElementById('save');
  button.disabled = true;
  status.textContent = 'saving…';
  try {
    const payload = await fetchJson(
      `/api/equipment?model=${encodeURIComponent(unit.model)}`,
      { method: 'POST', body: { capacity: draft } },
    );
    unit = payload.unit;
    draft = unit.capacity.map((row) => ({ ...row }));
    render();
    document.getElementById('status').textContent =
      'Saved. Every result post-processed from now on reads this table.';
  } catch (error) {
    status.textContent = `Not saved: ${error.message}`;
    button.disabled = false;
  }
}

async function fetchJson(url, { method = 'GET', body } = {}) {
  const response = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok || payload.error) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

const fmt = (value, digits = 1) =>
  Number(value).toLocaleString('en-US', {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });

function wireTheme() {
  const button = document.getElementById('theme-toggle');
  const apply = (mode) => {
    document.documentElement.dataset.theme = mode;
    button.textContent = mode === 'dark' ? 'Light' : 'Dark';
  };
  let mode = 'light';
  try {
    mode = localStorage.getItem('aicfd-theme') || 'light';
  } catch { /* private window: the default is fine */ }
  apply(mode);
  button.addEventListener('click', () => {
    mode = mode === 'dark' ? 'light' : 'dark';
    try {
      localStorage.setItem('aicfd-theme', mode);
    } catch { /* nothing to remember it with */ }
    apply(mode);
  });
}
