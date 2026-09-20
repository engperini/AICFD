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
/** The fitted coil's capacity curve, or why there is none. Server-computed:
 *  the fit is the model's, and a second copy of it here could disagree. */
let coilFit = null;

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
  coilFit = payload.coil;
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
    // Omitting this was the whole of ADR-067: the card was on the page, the
    // server would have written it, and the draft the page edits had no
    // `design` to put it in.
    design: { ...(source.design || {}) },
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
            <span class="card-title">The design selection</span>
            <span class="card-sub">one selection describes the unit; the coil
              is fitted from this</span>
          </div>
          ${designHtml()}
          <p class="prose note">
            These five numbers and the water below are everything the model
            needs. From them it answers at any return air temperature — above
            this selection and below it — and at an airflow no selection
            used.
          </p>
        </section>

        <section class="card">
          <div class="card-head">
            <span class="card-title">Reference selections</span>
            <span class="card-sub">optional; more of the manufacturer's, if
              you have them</span>
          </div>
          <p class="prose note">
            Not needed, and most units have none. What they do is refine how
            the coil's resistance divides between air and water, and then
            check the fit against themselves. With none, that split is
            assumed — and the result says so, because it is then yours to
            validate.
          </p>
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
    // The table can now be emptied, so the first row back has to come from
    // the design selection rather than from a last row that is not there.
    const last = draft.capacity[draft.capacity.length - 1] ?? {
      return_c: draft.design?.return_c ?? 35,
      nscc_kw: draft.design?.nscc_kw ?? 0,
    };
    draft.capacity.push({ ...last, return_c: (last.return_c ?? 35) + 1 });
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
          >−</button></td></tr>`,
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

/**
 * THE selection: the one the coil is fitted from, and all a unit needs.
 *
 * It was editable through the API and absent from this page, so a reader
 * with one selection in hand typed it into the reference table below and was
 * told the unit needed two of them. It never did (ADR-063, ADR-065).
 */
const DESIGN = [
  { path: 'design.return_c', label: 'Return air', suffix: '°C', step: 0.1 },
  { path: 'design.supply_c', label: 'Supply air', suffix: '°C', step: 0.1 },
  { path: 'design.airflow_m3h', label: 'Airflow', suffix: 'm³/h', step: 100 },
  { path: 'design.nscc_kw', label: 'Net sensible capacity', suffix: 'kW', step: 0.1 },
  { path: 'design.power_kw', label: 'Power input', suffix: 'kW', step: 0.1 },
];

/** The conditions the selection was taken at. */
const SELECTION = [
  { path: 'selection.elevation_m', label: 'Site elevation', suffix: 'm', step: 1 },
  { path: 'selection.esp_pa', label: 'External static pressure', suffix: 'Pa', step: 1 },
  { path: 'selection.entering_water_c', label: 'Entering water', suffix: '°C', step: 0.1 },
  { path: 'selection.leaving_water_c', label: 'Leaving water', suffix: '°C', step: 0.1 },
  { path: 'selection.entering_air_rh', label: 'Entering air RH', suffix: '%', step: 1 },
  // Optional, and worth having: the water carries the gross duty, so without
  // it the coil's water-side limit is inferred from the net and under-reads
  // by the fan power's share (ADR-069).
  { path: 'selection.water_flow_lh', label: 'Water flow', suffix: 'l/h', step: 10 },
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

function designHtml() {
  return `<table class="facts"><tbody>${DESIGN.map(fieldRow).join('')}</tbody></table>`;
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
  // Create what is missing rather than throwing on it. A field whose block
  // the draft does not carry used to raise inside an input handler, where
  // nothing shows it: the page looked like it took the value and the value
  // was never anywhere (ADR-067).
  const node = keys.reduce((n, key) => (n[key] ??= {}), target);
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

/**
 * The fitted coil's capacity against the air it receives.
 *
 * The card promises capacity against return air, and what answers that is the
 * COIL, recovered from the design selection -- not the reference table. Most
 * units have no reference rows, and plotting only those left the chart blank
 * for exactly the unit this page exists to enter (ADR-067). The rows are
 * drawn on top, where they exist, as what they are: the manufacturer's own
 * points, to be read against the fit rather than instead of it.
 *
 * Where the coil cannot be fitted, the card says why. A selection that does
 * not close -- an airflow and a temperature rise that do not carry the
 * capacity claimed -- is the most useful thing this page can tell anyone, and
 * an empty box tells them nothing.
 */
function drawChart() {
  const host = document.getElementById('chart');
  if (!host) return;
  if (!coilFit || coilFit.problem || !coilFit.points.length) {
    host.replaceChildren(note(coilFit?.problem
      || 'Fill in the design selection above and the coil is fitted from it; '
         + 'this is where its capacity against return air is drawn.'));
    return;
  }
  const width = Math.max(host.clientWidth || 640, 360);
  const height = Math.round(Math.min(380, Math.max(240, width * 0.42)));
  const pad = { left: 62, right: 16, top: 26, bottom: 58 };
  const fit = coilFit.points;
  const rows = [...draft.capacity]
    .filter((r) => Number.isFinite(+r.return_c) && Number.isFinite(+r.nscc_kw))
    .sort((a, b) => a.return_c - b.return_c);
  const svg = el('svg', {
    class: 'chart', viewBox: `0 0 ${width} ${height}`,
    width, height, role: 'img',
    'aria-label': 'net sensible capacity against return air temperature',
  });
  const xs = [...fit.map((p) => p.return_c), ...rows.map((r) => +r.return_c)];
  const x0 = Math.min(...xs);
  const x1 = Math.max(...xs);
  // The y axis starts at zero: this is a magnitude, and a truncated axis
  // would make a 45 % rise look like a tenfold one.
  const yMax = niceTop(Math.max(...fit.map((p) => p.nscc_kw),
                                ...rows.map((r) => +r.nscc_kw)));
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
  const ticks = 5;
  for (let i = 0; i < ticks; i += 1) {
    const value = x0 + ((x1 - x0) * i) / (ticks - 1);
    svg.append(text(X(value), height - pad.bottom + 18, fmt(value, 0), 'middle'));
  }
  svg.append(text((pad.left + width - pad.right) / 2, height - 26,
    'return air temperature (°C)', 'middle', 'var(--text-secondary)'));
  svg.append(text(14, pad.top - 8, 'kW', 'start', 'var(--text-secondary)'));

  svg.append(el('path', {
    d: fit.map((p, i) => `${i ? 'L' : 'M'}${X(p.return_c)},${Y(p.nscc_kw)}`).join(' '),
    fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 2,
    'stroke-linejoin': 'round',
  }));

  // The design selection, marked where it sits on the curve it produced.
  const design = coilFit.design_return_c;
  const at = fit.reduce((best, p) =>
    Math.abs(p.return_c - design) < Math.abs(best.return_c - design) ? p : best);
  svg.append(el('circle', {
    cx: X(at.return_c), cy: Y(at.nscc_kw), r: 4.5,
    fill: 'var(--series-1)', stroke: 'var(--paper)', 'stroke-width': 2,
  }));
  svg.append(text(X(at.return_c), Y(at.nscc_kw) - 12,
    `${fmt(at.nscc_kw, 0)} kW`, 'middle', 'var(--text-primary)', 650));

  // A row the fit set aside is drawn hollow. It is still the manufacturer's
  // number and belongs on the picture, but a filled dot would say it agreed
  // with the curve when the message below says it does not.
  const took = new Set((coilFit.reference_returns || []).map((v) => +v.toFixed(3)));
  let counted = 0;
  for (const row of rows) {
    const used = took.has(+(+row.return_c).toFixed(3));
    counted += used ? 1 : 0;
    svg.append(el('circle', {
      cx: X(+row.return_c), cy: Y(+row.nscc_kw), r: 4,
      fill: used ? 'var(--series-2)' : 'var(--paper)',
      stroke: used ? 'var(--paper)' : 'var(--series-2)', 'stroke-width': 2,
    }));
  }

  // Two things are drawn, so both are named. Colour alone never says which.
  const legend = [['var(--series-1)', 'the coil, fitted from the design selection']];
  if (rows.length) {
    legend.push(['var(--series-2)',
      `reference selections (${counted} of ${rows.length} in the fit)`
      + (coilFit.reference_error_k != null
        ? ` · fit within ${fmt(coilFit.reference_error_k, 1)} K` : '')]);
  }
  let x = pad.left;
  for (const [colour, label] of legend) {
    svg.append(el('circle', { cx: x + 4, cy: height - 8, r: 4, fill: colour }));
    svg.append(text(x + 14, height - 4, label, 'start', 'var(--text-secondary)'));
    x += 18 + label.length * 5.4;
  }
  // A reference row whose own numbers disagree takes no part in the fit, and
  // the one place anybody will see that is here, next to the chart it is
  // missing from.
  const rejected = coilFit.rejected_references || [];
  host.replaceChildren(svg, ...rejected.map((why) => note(why, 'warn')));
}

/** What the chart says when there is no curve to draw. */
function note(message, kind = '') {
  const box = document.createElement('p');
  box.className = `chart-note${kind ? ` ${kind}` : ''}`;
  box.textContent = message;
  return box;
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
    coilFit = payload.coil;
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
