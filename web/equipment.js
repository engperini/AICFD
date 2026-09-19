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
  draft = clone(unit);
  render();
}

/** A working copy of the unit, deep enough that editing it leaves `unit` alone. */
function clone(source) {
  return {
    family: source.family,
    size: [...source.size],
    weight_kg: source.weight_kg,
    fans: { ...(source.fans || {}) },
    selection: { ...(source.selection || {}) },
    capacity: source.capacity.map((row) => ({ ...row })),
    curve: {
      ...(source.curve || {}),
      points: (source.curve?.points || []).map((point) => [...point]),
    },
  };
}

function render() {
  document.getElementById('unit-name').textContent =
    `${unit.family} ${unit.model}`;
  document.getElementById('root').innerHTML = `
    <div class="layout">
      <div class="column">
        <section class="card">
          <div class="card-head">
            <span class="card-title">Net sensible capacity</span>
            <span class="card-sub">against the air the unit receives</span>
          </div>
          <div id="chart"></div>
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
            <span class="spacer"></span>
            <input id="new-model" type="text" placeholder="new model name"
              aria-label="new model name" />
            <button id="save-as" type="button">Save as new unit</button>
          </div>
          <p class="prose note" id="status">
            Save writes every field on this page back to
            <code>equipment/${unit.model}.yaml</code>, comments and all.
            Saving as a new unit copies it under another name instead, because
            renaming would break the specs pointing at this one.
          </p>
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
  document.getElementById('save').addEventListener('click', () => save());
  document.getElementById('save-as').addEventListener('click', () => {
    const name = document.getElementById('new-model').value.trim();
    if (!name) {
      document.getElementById('new-model').focus();
      document.getElementById('status').textContent =
        'A new unit needs a model name.';
      return;
    }
    save(name);
  });
  document.getElementById('undo').addEventListener('click', () => {
    draft = clone(unit);
    render();
  });
  document.getElementById('add').addEventListener('click', () => {
    const last = draft.capacity[draft.capacity.length - 1];
    draft.capacity.push({ ...last, return_c: last.return_c + 1 });
    render();
  });
  for (const input of document.querySelectorAll('[data-row]')) {
    input.addEventListener('input', () => {
      const row = draft.capacity[Number(input.dataset.row)];
      row[input.dataset.key] = Number(input.value);
      drawChart();
    });
  }
  for (const button of document.querySelectorAll('[data-drop]')) {
    button.addEventListener('click', () => {
      draft.capacity.splice(Number(button.dataset.drop), 1);
      render();
    });
  }
  wireFields();
  wireCurve();
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
  const rows = draft.capacity
    .map(
      (row, i) => `<tr>${COLUMNS.map(
        ([key]) =>
          `<td><input type="number" step="any" value="${row[key]}"
             data-row="${i}" data-key="${key}" /></td>`,
      ).join('')}<td><button data-drop="${i}" type="button"
          ${draft.capacity.length <= 2 ? 'disabled' : ''}>−</button></td></tr>`,
    )
    .join('');
  return `<table><thead><tr>${head}<th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

/**
 * The fields a unit's identity is made of.
 *
 * Editable because a coil is very often the same machine under two names: a
 * manufacturer sells it as one part number, an integrator resells it as
 * another, and the selections are the same selections. Renaming the *model*
 * is a different matter — every spec points at that name — so the model is
 * read-only here and a new name means a new unit (`Save as new unit` below).
 */
const IDENTITY = [
  { path: 'family', label: 'Manufacturer and family', type: 'text' },
  { path: 'size.0', label: 'Width', suffix: 'm', step: 0.01 },
  { path: 'size.1', label: 'Depth', suffix: 'm', step: 0.01 },
  { path: 'size.2', label: 'Height', suffix: 'm', step: 0.01 },
  { path: 'weight_kg', label: 'Weight', suffix: 'kg', step: 1 },
  { path: 'fans.count', label: 'Fans', step: 1 },
  { path: 'fans.type', label: 'Fan type', type: 'text' },
  { path: 'fans.module', label: 'Fan module', type: 'text' },
  { path: 'fans.modulation', label: 'Fan modulation', suffix: '%', step: 0.1 },
];

/** The conditions the capacity table was selected at. */
const SELECTION = [
  { path: 'selection.elevation_m', label: 'Site elevation', suffix: 'm', step: 1 },
  { path: 'selection.esp_pa', label: 'External static pressure', suffix: 'Pa', step: 1 },
  { path: 'selection.entering_water_c', label: 'Entering water', suffix: '°C', step: 0.1 },
  { path: 'selection.leaving_water_c', label: 'Leaving water', suffix: '°C', step: 0.1 },
  { path: 'selection.entering_air_rh', label: 'Entering air RH', suffix: '%', step: 1 },
];

function factsHtml() {
  return `<table class="facts"><tbody>
      <tr><td>Model</td><td class="fixed">${unit.model}</td></tr>
      ${IDENTITY.map(fieldRow).join('')}
    </tbody></table>`;
}

function selectionHtml() {
  return `<table class="facts"><tbody>${SELECTION.map(fieldRow).join('')}</tbody></table>`;
}

function fieldRow(field) {
  const value = dig(draft, field.path);
  const shown = value == null ? '' : String(value).replace(/"/g, '&quot;');
  // A manufacturer's name is long and a number is short. Text gets the whole
  // width of the card, under its label, so a name is never read half-hidden.
  if (field.type === 'text') {
    return `<tr><td colspan="2"><label class="stacked"><span>${field.label}</span>
        <input data-field="${field.path}" type="text" value="${shown}" /></label></td></tr>`;
  }
  return `<tr><td>${field.label}</td><td><div class="field">
      <input data-field="${field.path}" type="number"
        step="${field.step ?? 'any'}" value="${shown}" />
      ${field.suffix ? `<span class="suffix">${field.suffix}</span>` : ''}
    </div></td></tr>`;
}

function wireFields() {
  for (const input of document.querySelectorAll('[data-field]')) {
    input.addEventListener('input', () => {
      const raw = input.value.trim();
      // An emptied number means "the file does not say", not zero: a weight
      // nobody filled in should stay absent rather than become 0 kg.
      const value = input.type === 'number' ? (raw === '' ? null : Number(raw)) : raw;
      plant(draft, input.dataset.field, value);
      if (input.dataset.field === 'family') {
        document.getElementById('unit-name').textContent =
          `${draft.family} ${unit.model}`.trim();
      }
    });
  }
}

/** Read a dotted path, where a numeric step indexes a list. */
function dig(source, path) {
  return path.split('.').reduce((node, key) => (node == null ? node : node[key]), source);
}

function plant(target, path, value) {
  const keys = path.split('.');
  const last = keys.pop();
  const node = keys.reduce((n, key) => n[key], target);
  node[last] = value;
}

function curveHtml() {
  const points = draft.curve.points || [];
  const measured = draft.curve.measured === true;
  return `<section class="card">
      <div class="card-head">
        <span class="card-title">P–Q curve</span>
        <span class="card-sub">what the fans can push against</span>
      </div>
      <table class="facts"><thead><tr><th>Airflow m³/h</th><th>Static Pa</th>
        <th></th></tr></thead><tbody>
        ${points
          .map(
            ([q, pa], i) => `<tr>
              <td><div class="field"><input type="number" step="any" value="${q}"
                data-point="${i}" data-axis="0" /></div></td>
              <td><div class="field"><input type="number" step="any" value="${pa}"
                data-point="${i}" data-axis="1" /></div></td>
              <td><button data-point-drop="${i}" type="button"
                ${points.length <= 2 ? 'disabled' : ''}>−</button></td></tr>`,
          )
          .join('')}
      </tbody></table>
      <div class="actions">
        <label class="note" style="display:flex;gap:6px;align-items:center">
          <input type="checkbox" id="measured" ${measured ? 'checked' : ''} />
          the manufacturer's own curve
        </label>
        <span class="spacer" style="flex:1"></span>
        <button id="add-point" type="button">Add a point</button>
      </div>
      ${
        measured
          ? ''
          : `<p class="prose note">Not measured. The selections give one point —
             the static pressure the unit was selected to overcome at its rated
             flow — and the rest has the shape of an EC fan array. It decides the
             uncontrolled operating point and nothing else; tick the box above
             once these are the manufacturer's numbers, and the report will say
             so.</p>`
      }
    </section>`;
}

function wireCurve() {
  for (const input of document.querySelectorAll('[data-point]')) {
    input.addEventListener('input', () => {
      draft.curve.points[Number(input.dataset.point)][Number(input.dataset.axis)] =
        Number(input.value);
    });
  }
  for (const button of document.querySelectorAll('[data-point-drop]')) {
    button.addEventListener('click', () => {
      draft.curve.points.splice(Number(button.dataset.pointDrop), 1);
      render();
    });
  }
  const add = document.getElementById('add-point');
  if (add) {
    add.addEventListener('click', () => {
      const points = draft.curve.points;
      const last = points[points.length - 1] || [0, 0];
      points.push([last[0], last[1]]);
      render();
    });
  }
  const measured = document.getElementById('measured');
  if (measured) {
    measured.addEventListener('change', () => {
      draft.curve.measured = measured.checked;
      render();
    });
  }
}

function factsTable(rows) {
  return `<table class="facts"><tbody>${rows
    .map(([a, b]) => `<tr><td>${a}</td><td style="text-align:right">${b}</td></tr>`)
    .join('')}</tbody></table>`;
}

// --- the chart ---------------------------------------------------------------

function drawChart() {
  const host = document.getElementById('chart');
  if (!host) return;
  const width = Math.max(host.clientWidth || 640, 360);
  const height = Math.round(Math.min(380, Math.max(240, width * 0.42)));
  const pad = { left: 62, right: 16, top: 16, bottom: 44 };
  const rows = [...draft.capacity]
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

/**
 * Write the draft back — over this unit, or, given a name, into a new one.
 *
 * The whole draft goes up every time rather than a diff. The server writes
 * each field into the file textually, in place, so a field that did not
 * change is rewritten identically and the file's comments — which are where
 * these numbers' provenance lives — survive untouched.
 */
async function save(newModel = '') {
  const status = document.getElementById('status');
  const buttons = [document.getElementById('save'), document.getElementById('save-as')];
  for (const button of buttons) button.disabled = true;
  status.textContent = 'saving…';
  try {
    const payload = await fetchJson(
      `/api/equipment?model=${encodeURIComponent(unit.model)}`,
      { method: 'POST', body: { ...draft, save_as: newModel } },
    );
    if (newModel) {
      location.search = `?model=${encodeURIComponent(payload.unit.model)}`;
      return;
    }
    unit = payload.unit;
    draft = clone(unit);
    render();
    document.getElementById('status').textContent =
      'Saved. Every result post-processed from now on reads this unit.';
  } catch (error) {
    status.textContent = `Not saved: ${error.message}`;
    for (const button of buttons) button.disabled = false;
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
