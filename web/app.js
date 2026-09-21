/**
 * The AICFD page: check the model, set the parameters, start the run, watch it.
 *
 * The order matters. Everything you need to catch a wrong dimension is on
 * screen before the Run button does anything, because a mistake found after a
 * twenty-minute solve has already cost the twenty minutes.
 */

import { viewsFor, drawView, sheetScale } from './drawing.js';

/**
 * Which case this page is looking at, and keeping it across a hop: `case.js`.
 *
 * The server was launched with one case and answered `/api/model` with it
 * whatever the page asked -- so opening `?case=other` changed the address bar
 * and nothing else, and the only way to look at a second room was to stop
 * `aicfd view` and start it again. Every call carries the case now, and the
 * server prefers it over the one it was started with (ADR-087). Importing
 * `case.js` also keeps it on every link out of this page, which is what the
 * links to the racks, equipment and components pages used to drop (ADR-091).
 */
import { CASE, withCase } from './case.js';

// One decimal separator for the whole tool: a dot going out, either coming
// in (ADR-083). The datasheet numbers this page writes into the fields go
// through it like every other value a form field carries.
import { dec } from './decimal.js';


/**
 * The input template: every field of the case spec, grouped the way an
 * engineer thinks about a data hall.
 *
 * A field appears when the loaded spec has it, so a POD shows `Racks in the
 * row` and a hall shows `Racks per row` and `PODs`, and neither can be edited
 * into the other's shape by accident. Fields marked `optional` are shown even
 * when absent, because they are the ones a user adds: a datasheet capacity, a
 * perimeter clearance, the site altitude.
 *
 * The paths come from the server (`model.editable`), so this file never holds
 * a second copy of where a value lives in the spec.
 */
const SECTIONS = [
  {
    title: 'Site',
    note: 'the air weighs what it weighs here',
    params: [{ key: 'altitude_m', label: 'Altitude', unit: 'm', step: 10, optional: true }],
  },
  {
    title: 'Room',
    params: [
      { key: 'pods', label: 'PODs (row + hot aisle + row)', unit: '', step: 1 },
      { key: 'hall_size', label: 'Hall size (x, y, z)', unit: 'm', text: true },
      { key: 'hall_height', label: 'Floor to slab', unit: 'm', step: 0.1 },
      { key: 'ceiling', label: 'False ceiling height', unit: 'm', step: 0.1 },
      { key: 'gallery_depth', label: 'Mechanical gallery depth', unit: 'm', step: 0.1 },
      { key: 'gallery_sides', label: 'Galleries (1, or 2 = one at each end)', unit: '', step: 1, optional: true },
    ],
  },
  {
    title: 'Aisles and clearances',
    params: [
      { key: 'cold_aisle', label: 'Cold aisle', unit: 'm', step: 0.1 },
      { key: 'hot_aisle', label: 'Hot aisle (contained)', unit: 'm', step: 0.1 },
      { key: 'perimeter', label: 'Perimeter clearance', unit: 'm', step: 0.1, optional: true },
      { key: 'transverse', label: 'Transverse aisle between rack blocks', unit: 'm', step: 0.1, optional: true },
    ],
  },
  {
    title: 'Racks',
    // The load is one number for the hall here; the page behind this link is
    // where a position says otherwise, zero included (ADR-054).
    racksLink: true,
    params: [
      { key: 'rack_count', label: 'Racks in the row', unit: '', step: 1 },
      { key: 'racks_per_row', label: 'Racks per row, per block', unit: '', step: 1 },
      { key: 'rack_blocks', label: 'Blocks each row is cut into', unit: '', step: 1, optional: true },
      { key: 'rack_load_kw', label: 'Load per rack', unit: 'kW', step: 0.5 },
      { key: 'rack_size', label: 'Rack size (w, d, h)', unit: 'm', text: true },
      { key: 'rack_offset_x', label: 'Row offset from the gallery', unit: 'm', step: 0.1 },
      { key: 'rack_cfm_per_kw', label: 'Rack airflow', unit: 'CFM/kW', step: 1, optional: true },
    ],
  },
  {
    title: 'Supply plenum',
    note: 'the hall wall doubled; what closes it decides where the air goes',
    componentsLink: true,
    params: [
      // One or the other, or neither: they are two ways of closing the same
      // wall and a case cannot have both. Neither is the room with no
      // plenum at all, which is the shorter hall (ADR-060).
      { key: 'plenum', label: 'Include Plenum with grilles', check: true, default: false, exclusive: 'plenum' },
      { key: 'plenum_as_mesh', label: 'No Plenum, only Mesh', check: true, default: false, exclusive: 'plenum' },
      { key: 'plenum_depth', label: 'Between the two leaves', unit: 'm', step: 0.1, optional: true },
      { key: 'plenum_grille_width', label: 'Supply grille width', unit: 'm', step: 0.1, optional: true },
      { key: 'plenum_grille_height', label: 'Supply grille height', unit: 'm', step: 0.1, optional: true },
    ],
  },
  {
    title: 'Raised floor',
    note: 'the space under the room, as a supply plenum',
    componentsLink: true,
    params: [
      // On, the units blow downward through the deck and the air reaches the
      // cold aisle through plates in the floor. It cannot be combined with a
      // supply plenum at the gallery wall: those are two ways of doing the
      // same thing, and a case asking for both has not chosen (ADR-076).
      { key: 'floor', label: 'Supply through a raised floor', check: true,
        default: false },
      { key: 'floor_height', label: 'Floor plenum depth', unit: 'm', step: 0.1,
        optional: true },
      { key: 'floor_tiles', label: 'Plates in front of each rack', unit: '',
        step: 1, optional: true },
    ],
  },
  {
    title: 'Fan walls',
    note: 'per unit, as the datasheet gives them',
    // The unit's own characterisation -- its capacity against the air it
    // receives -- lives on its own page. The everyday numbers stay here
    // (ADR-036), and WHICH unit is a row above them (ADR-092).
    equipment: true,
    unitPicker: true,
    params: [
      { key: 'fan_count', label: 'Units', unit: '', step: 1 },
      { key: 'airflow_m3h', label: 'Airflow per unit', unit: 'm³/h', step: 100 },
      { key: 'fan_capacity_kw', label: 'Sensible capacity', unit: 'kW', step: 1, optional: true },
      { key: 'fan_power_kw', label: 'Power input', unit: 'kW', step: 0.1, optional: true },
      { key: 'fan_width', label: 'Unit width', unit: 'm', step: 0.1 },
      { key: 'fan_height', label: 'Unit height', unit: 'm', step: 0.1 },
      { key: 'fan_depth', label: 'Unit depth', unit: 'm', step: 0.1, optional: true },
      { key: 'supply_temp_c', label: 'Supply temperature', unit: '°C', step: 0.5 },
      // Off is every unit to its own return, which is a unit with no network.
      // On, the units of one gallery run to the worst return any of them sees
      // (ADR-064).
      { key: 'fan_team', label: 'Units work as a team, per gallery', check: true, default: false, on: 'team' },
      { key: 'fan_static_pa', label: 'External static pressure', unit: 'Pa', step: 5, optional: true },
    ],
  },
  {
    title: 'Return air and containment',
    note: 'how much face there is, and what it is made of',
    // What each surface is made of -- its free area, and what that costs --
    // is a house standard rather than a per-case number, so it is chosen here
    // and edited on its own page (ADR-048, ADR-051). The two fields that used
    // to state it by hand are gone: they said the same thing as the component
    // and could disagree with it.
    componentsLink: true,
    components: true,
    params: [
      { key: 'grille_size', label: 'Grille size', unit: 'm', step: 0.05 },
      { key: 'grille_count', label: 'Grilles', unit: '', step: 1 },
      { key: 'grille_coverage', label: 'Ceiling covered over each hot aisle', unit: '0-1', step: 0.05 },
      { key: 'containment', label: 'Contain the hot aisle', check: true, optional: true },
    ],
  },
  {
    title: 'Mesh and solver',
    params: [
      { key: 'cell_size', label: 'Cell size (x, y, z)', unit: 'm', text: true },
      { key: 'max_iterations', label: 'Iteration cap', unit: '', step: 50 },
      { key: 'sensor_interval', label: 'Report every', unit: 'iterations', step: 10 },
      { key: 'processors', label: 'Processors', unit: '', step: 1, optional: true },
      { key: 'warm_start', label: 'Warm start', check: true, optional: true },
    ],
  },
];

/** The parameters this spec actually carries, section by section. */
function activeSections() {
  return SECTIONS.map((section) => ({
    ...section,
    params: section.params.filter(
      // A checkbox always has an answer -- on or off -- so it shows whether
      // or not the case mentions it. Dropping the ones a case said nothing
      // about is how the supply plenum ended up as four number boxes with no
      // way to turn the thing on (ADR-059).
      (p) => model.editable?.[p.key]
        && (p.optional || p.check || specValue(p.key) !== ''),
    ),
  })).filter((section) => section.params.length);
}

const RESIDUAL_ORDER = ['Ux', 'Uy', 'Uz', 'h', 'p_rgh', 'k', 'epsilon'];

let model = null;
let pollTimer = null;

main();

async function main() {
  setupTheme();
  wireCaseMenu();
  try {
    model = await fetchJson('/api/model');
  } catch (error) {
    document.getElementById('root').innerHTML =
      `<div class="error"><strong>Could not load the model.</strong>
       <p>${error.message}</p>
       <p>This page needs the AICFD server. Run <code>aicfd view</code>.</p></div>`;
    return;
  }
  if (model.error) {
    // No form to fix it in: the page draws itself from a model the generator
    // has refused to build. So it says where the file is and what it said,
    // which is the shortest way back (ADR-055).
    document.getElementById('root').innerHTML =
      `<div class="error"><strong>This case cannot be built.</strong>
       <p>${model.error}</p>
       ${model.file ? `<p>Edit <code>${model.file}</code> — the value the
         message names — and reload this page. Nothing was lost: the case is
         the file, and the solver has not been asked for anything.</p>` : ''}
       </div>`;
    return;
  }
  render();
  poll();
}

// --- rendering ---------------------------------------------------------------

function render() {
  document.getElementById('case-name').textContent =
    `${model.name} · ${model.cells.toLocaleString('en-US')} cells · ` +
    `${model.divisions.join(' × ')}`;
  setStage(model.run);

  document.getElementById('root').innerHTML = `
<main class="layout">
  <div class="column">
    <section class="card">
      <div class="card-head">
        <span class="card-title">Model</span>
        <span class="card-sub">drawn from the geometry that will be meshed</span>
      </div>
      <div class="views" id="views"></div>
      <div class="legend">
        <span><i class="swatch" style="background:var(--cold)"></i>fan wall / cold air</span>
        <span><i class="swatch" style="background:var(--hot)"></i>grilles and return</span>
        <span><i class="swatch" style="background:var(--containment)"></i>containment</span>
        <span><i class="swatch" style="background:var(--rack);border:1px solid var(--text-primary)"></i>racks</span>
        <span>dashed = beyond the section plane</span>
      </div>
    </section>

    <section class="card" id="sensors-card">
      <div class="card-head">
        <span class="card-title">Air loop</span>
        <span class="card-sub">the whole stream at each station, every
          ${model.spec?.solver?.sensor_interval ?? 100} iterations</span>
      </div>
      <div id="sensors"></div>
    </section>

    <section class="card">
      <div class="card-head">
        <span class="card-title">Convergence</span>
        <span class="card-sub" id="progress-note">${
          model.blocked ? `cannot run: ${model.blocked}` : 'nothing running'
        }</span>
        <span class="spacer" style="flex:1"></span>
        <a class="linkbutton" id="see-results"
           href="./results.html?case=${model.name}" hidden>See results</a>
        <button id="stop" type="button" hidden>Stop</button>
        <button id="run" class="primary" type="button">Run simulation</button>
      </div>
      <div id="progress"></div>
    </section>
  </div>

  <aside class="column">
    <section class="card">
      <div class="card-head"><span class="card-title">Inputs</span>
        <span class="card-sub">the case template; every field of the spec</span></div>
      <div class="params" id="params"></div>
      <div class="actions">
        <button id="apply" class="primary" type="button">Apply</button>
        <button id="reset" type="button">Undo</button>
      </div>
    </section>

    <section class="card">
      <div class="card-head"><span class="card-title">Check</span>
        <span class="card-sub">what the inputs imply</span></div>
      <div id="summary"></div>
    </section>

    <section class="card" id="alerts-card" hidden>
      <div class="card-head"><span class="card-title">Cooling capacity</span>
        <span class="card-sub">design criteria; these warn, they do not block a run</span></div>
      <ul class="notes alerts" id="alerts"></ul>
    </section>
    <section class="card" id="warnings-card" hidden>
      <div class="card-head"><span class="card-title">Mesh snapping</span></div>
      <ul class="notes" id="warnings"></ul>
    </section>
  </aside>
</main>`;

  drawViews();
  renderSensors(null);
  renderParams();
  renderSummary();
  renderWarnings();
  renderAlerts();
  drawProgress({ iterations: [], series: {} });

  document.getElementById('apply').addEventListener('click', applyChanges);
  document.getElementById('reset').addEventListener('click', () => renderParams());
  document.getElementById('run').addEventListener('click', startRun);
  document.getElementById('stop').addEventListener('click', stopRun);
  window.addEventListener('resize', debounce(drawViews, 150));
}

/**
 * Magnification steps offered per drawing. `1` is the fitted sheet, shared by
 * all of them; above it the drawing overflows and scrolls.
 *
 * The same ladder the results page climbs, because the reader moves between
 * the two comparing the same lines and a different set of stops would make
 * the same geometry a different size on each.
 */
const ZOOM_STEPS = [1, 1.5, 2, 3, 4, 6, 8];

/** Magnification per drawing, by view id; 1 is the fitted sheet. */
const viewZoom = {};

function drawViews() {
  const host = document.getElementById('views');
  if (!host) return;
  host.replaceChildren();
  const views = viewsFor(model);
  // Lay the cells out first, then pick one scale they can all live with: the
  // views are meant to be read against each other, so the fitted state is
  // shared and a metre is the same length in all of them.
  const cells = views.map((view) => {
    const cell = document.createElement('div');
    cell.className = 'view';
    cell.innerHTML = `
      <div class="view-head">
        <div><h3>${view.title}</h3><p>${view.subtitle}</p></div>
        <div class="view-zoom">
          <button type="button" data-role="out" aria-label="zoom out">−</button>
          <span data-role="zoom">fit</span>
          <button type="button" data-role="in" aria-label="zoom in">+</button>
        </div>
      </div>
      <div class="view-scroll" data-role="scroll">
        <div class="view-stage" data-role="stage"></div>
      </div>`;
    host.append(cell);
    for (const [role, direction] of [['in', 1], ['out', -1]]) {
      cell.querySelector(`[data-role="${role}"]`)
        .addEventListener('click', () => setZoom(view, cell, direction));
    }
    return cell;
  });
  // The scroller is the width to fit to: the card also carries its padding.
  sheet = sheetScale(model, views,
                     cells.map((c) => c.querySelector('[data-role="scroll"]').clientWidth));
  views.forEach((view, i) => renderView(view, cells[i]));
}

/** The fitted scale the drawings share, in px per metre. */
let sheet = 0;

function renderView(view, cell) {
  const zoom = viewZoom[view.id] ?? 1;
  const stage = cell.querySelector('[data-role="stage"]');
  const label = cell.querySelector('[data-role="zoom"]');
  label.textContent = zoom === 1 ? 'fit' : `${zoom}×`;
  label.title = `${Math.round(sheet * zoom)} px per metre`;
  stage.replaceChildren(drawView(model, view, sheet * zoom));
}

/**
 * Zoom one drawing, keeping the middle of what is on screen in the middle.
 *
 * Fitted to the page a hall in section is a couple of hundred pixels tall
 * whatever the layout does, because the aspect ratio is the aspect ratio. To
 * check a containment gap or where a fan wall edge lands on the mesh grid you
 * have to magnify and scroll (ADR-035) -- and that check belongs here, before
 * the solve is paid for, not only on the results page afterwards.
 *
 * Zoom is per drawing and the fitted state is shared, so at `fit` the views
 * still share a scale. The label says which state it is in, so a magnified
 * drawing never passes for the fitted one.
 */
function setZoom(view, cell, direction) {
  const current = viewZoom[view.id] ?? 1;
  const index = ZOOM_STEPS.indexOf(current);
  const next = ZOOM_STEPS[
    Math.min(ZOOM_STEPS.length - 1, Math.max(0, index + direction))];
  if (next === current) return;
  const scroll = cell.querySelector('[data-role="scroll"]');
  // Hold the centre of the visible area: zooming that jumps to a corner loses
  // the thing the reader was looking at.
  const anchor = {
    x: (scroll.scrollLeft + scroll.clientWidth / 2) / Math.max(scroll.scrollWidth, 1),
    y: (scroll.scrollTop + scroll.clientHeight / 2) / Math.max(scroll.scrollHeight, 1),
  };
  viewZoom[view.id] = next;
  renderView(view, cell);
  scroll.scrollLeft = anchor.x * scroll.scrollWidth - scroll.clientWidth / 2;
  scroll.scrollTop = anchor.y * scroll.scrollHeight - scroll.clientHeight / 2;
}

/**
 * The way through to the unit's own page, from the section that uses it.
 *
 * One manufacturer selection describes the machine, and from it the coil --
 * which is what decides every capacity number in a result and the supply air
 * temperature with it. Not a field anyone edits day to day, so it is one
 * click away rather than in the form, and the link names the unit so a reader
 * knows which machine the numbers in front of them came from (ADR-036,
 * ADR-063).
 */
function equipmentLink() {
  const named = model.spec?.fanwall?.model;
  const href = named
    ? `./equipment.html?model=${encodeURIComponent(named)}`
    : './equipment.html';
  return `<a class="group-link" href="${href}" title="${
    named
      ? `${named}: its selection, the coil fitted from it, and what it does at the air it receives`
      : 'the equipment library. Name a unit with fanwall.model to model its coil'
  }">${named ? `${named} ▸` : 'equipment ▸'}</a>`;
}

/**
 * The way through to the surfaces the air passes, from the section that uses
 * them.
 *
 * A grille's free area is a house standard rather than a number typed per
 * case -- every hall built to the specification uses the same one -- so it is
 * one click away rather than in this form (ADR-048).
 */
function componentsLink() {
  return '<a class="group-link" href="./components.html" '
    + 'title="free area of the ceiling grilles, the mesh into the gallery, '
    + 'the floor plates and the containment">components \u25b8</a>';
}

/**
 * The way through to the rack positions, from the group that sets their load.
 *
 * One load per rack is how a hall is bought; what each position actually
 * carries is a list, and a list does not belong in a form (ADR-054).
 */
function racksLink() {
  const positions = (model.racks || []).length;
  const off = (model.racks || []).filter((r) => (r.load_kw ?? 1) <= 0).length;
  return `<a class="group-link" href="./racks.html" title="the load each of the `
    + `${positions} positions carries">${off ? `${off} empty · ` : ''}positions \u25b8</a>`;
}

/**
 * Checkboxes that answer the same question: ticking one clears the others.
 *
 * The supply plenum's two are ways of closing one wall -- grilles, or mesh --
 * and a case cannot have both. Radio buttons would say so too, but they
 * cannot be cleared once set, and "neither" is the commonest answer here: a
 * hall with no plenum at all (ADR-060).
 */
function wireExclusiveChecks() {
  const groups = {};
  for (const section of activeSections()) {
    for (const p of section.params) {
      if (p.exclusive) (groups[p.exclusive] ??= []).push(p.key);
    }
  }
  for (const keys of Object.values(groups)) {
    for (const key of keys) {
      const box = document.getElementById(`p-${key}`);
      if (!box) continue;
      box.addEventListener('change', () => {
        if (!box.checked) return;
        for (const other of keys) {
          if (other === key) continue;
          const el = document.getElementById(`p-${other}`);
          if (el) el.checked = false;
        }
      });
    }
  }
}

function wireMeshPreset() {
  const box = document.getElementById('p-cell_size');
  const preset = document.getElementById('p-cell-preset');
  const note = document.getElementById('cell-note');
  if (!box || !preset || !note) return;
  const refresh = () => {
    note.textContent = meshNote(box.value);
    // A typed value that happens to match a preset selects it; anything else
    // falls back to Custom, so the select never claims something untrue.
    const match = MESH_PRESETS.find((m) => m.value.join(', ') === box.value.trim());
    preset.value = match ? match.value.join(', ') : '';
  };
  preset.addEventListener('change', () => {
    if (!preset.value) return;
    box.value = preset.value;
    refresh();
  });
  box.addEventListener('input', refresh);
  refresh();
}

function renderParams() {
  const host = document.getElementById('params');
  host.innerHTML = activeSections()
    .map(
      (section) => `<div class="param-group">
        <div class="param-group-head"><span class="group-title">${
          section.title
        }</span>${section.equipment ? equipmentLink() : ''}${
          section.componentsLink ? componentsLink() : ''
        }${section.racksLink ? racksLink() : ''}${
          section.note ? `<span class="group-note">${section.note}</span>` : ''
        }</div>
        ${section.unitPicker ? unitRow() : ''}
        ${section.components ? componentRows() : ''}
        ${section.params.map(inputHtml).join('')}
      </div>`,
    )
    .join('');
  wireMeshPreset();
  wireUnitPicker();
  wireComponentPickers();
  wireExclusiveChecks();
}

/**
 * What each surface in this case is made of, on one line each.
 *
 * The card used to carry the free area and the loss coefficient as two typed
 * fields. They said the same thing as the component the case uses and could
 * disagree with it, which is a drawing and a number arguing in front of the
 * reader. So the choice is here -- a name, and what it costs -- and the
 * numbers behind it are a click away on the page that owns them (ADR-051).
 *
 * A role with one option is a line rather than a select: there is nothing to
 * choose, and a select with one entry invites a reader to look for a second.
 */
function componentRows() {
  const roles = model.components || [];
  if (!roles.length) return '';
  return `<div class="components">${roles.map((role) => {
    const cost = role.kind === 'load'
      ? `${(role.share * 100).toFixed(1)} % of the IT load`
      : `${(role.free_area * 100).toFixed(0)} % free · K ${role.k.toFixed(2)}`;
    const name = (role.options.find((o) => o.id === role.chosen) || {}).name || '—';
    const picker = role.options.length > 1 && !role.fixed
      ? `<select data-component-role="${role.role}" aria-label="${role.label}">${
          role.options.map((o) => `<option value="${o.id}"${
            o.id === role.chosen ? ' selected' : ''}>${o.name}</option>`).join('')
        }</select>`
      : `<span class="component-name" title="${name}">${name}</span>`;
    const tag = role.fixed ? '<span class="component-tag">fixed</span>'
      : role.applied ? '' : '<span class="component-tag pending">not in the solver</span>';
    return `<div class="component-row">
      <span class="component-role">${role.label}</span>
      ${picker}
      <span class="component-cost">${cost}</span>${tag}
    </div>`;
  }).join('')}</div>`;
}

/**
 * Which machine cools this room, as a choice rather than a fact.
 *
 * The group showed the unit's name and linked to its datasheet, and that was
 * all: `fanwall.model` was not a field, so the only way to put another unit
 * in the case was to edit the YAML. A reader who opened the equipment page,
 * found their CRAH and selected it there changed the LIBRARY page they were
 * looking at and nothing about their case -- the model page came back with
 * the same unit and no way to change it (ADR-092).
 *
 * Every unit in the library is offered, including the ones that do not suit
 * this room, each saying why. Hiding them answers nothing: somebody looking
 * for their CRAH learns only that the software has never heard of it. They
 * are not disabled either, because ticking `floor.enabled` and choosing a
 * downflow unit is one edit made of two fields, and the Apply judges the
 * pair -- the generator has the final word and says it in a sentence
 * (ADR-055).
 */
function unitRow() {
  const lib = model.equipment;
  if (!lib || !lib.options.length) return '';
  const short = (o) => {
    if (o.suits) return '';
    if (o.cooling !== 'chilled_water') return ` — ${o.cooling}, no coil model`;
    // What the OPTION needs, not what the room has: a downflow unit needs a
    // raised floor wherever it is offered. Reading `lib.wants` here said the
    // opposite of the refusal the Apply would give.
    return ` — ${o.arrangement}, needs ${
      o.arrangement === 'downflow' ? 'a raised floor' : 'no raised floor'}`;
  };
  const chosen = lib.options.find((o) => o.model === lib.chosen);
  const options = [`<option value=""${lib.chosen ? '' : ' selected'}>—  none, the numbers below describe it</option>`]
    .concat(lib.options.map((o) => `<option value="${o.model}"${
      o.model === lib.chosen ? ' selected' : ''}>${o.model} · ${o.family}${
      short(o)}</option>`));
  const note = chosen
    ? (chosen.suits
        ? `${chosen.family} · ${chosen.arrangement}`
        : chosen.why)
    : 'no unit named: the capacity and the coil are whatever is typed below';
  return `<div class="components">
    <div class="component-row">
      <span class="component-role">Unit</span>
      <select id="fan-model" aria-label="Fan wall unit">${options.join('')}</select>
      <span class="component-cost" id="fan-model-note"${
        chosen && !chosen.suits ? ' data-tone="bad"' : ''}>${note}</span>
    </div>
  </div>`;
}

/**
 * The note under the picker follows the select at once, for the same reason
 * the component cost does: what the choice means is the reason for making
 * it, and seeing it only after Apply makes the select feel like it did
 * nothing. The spec is still only written by Apply.
 */
function wireUnitPicker() {
  const select = document.getElementById('fan-model');
  const note = document.getElementById('fan-model-note');
  if (!select || !note) return;
  select.addEventListener('change', () => {
    const option = (model.equipment?.options || [])
      .find((o) => o.model === select.value);
    if (!option) {
      note.textContent =
        'no unit named: the capacity and the coil are whatever is typed below';
      note.removeAttribute('data-tone');
      return;
    }
    note.textContent = option.suits
      ? `${option.family} · ${option.arrangement}`
      : option.why;
    if (option.suits) note.removeAttribute('data-tone');
    else note.setAttribute('data-tone', 'bad');
    fillFromDatasheet(option);
  });
}

/**
 * Put the chosen machine's numbers in the fields, now.
 *
 * The group above the picker is the unit's datasheet -- airflow, capacity,
 * power, supply temperature, the three dimensions, the static pressure. Those
 * fields held the PREVIOUS unit's figures, and the server fills only what a
 * case leaves blank (ADR-036), so picking another machine changed the name
 * over the old numbers and nothing else. A hall came back naming a 145 kW
 * CRAH and carrying 432.6 kW, 121,545 m3/h and a 3.96 m unit, and every
 * check passed on it (ADR-094).
 *
 * The fields are found through `model.editable`, which is the server's own
 * table of field to spec path -- so this cannot name a field the form has
 * not got, and cannot drift from where each number lives.
 *
 * Nothing is saved: these are the same boxes the reader can now type over,
 * and Apply is still what writes. What changes is that what they see under
 * the unit's name is the unit.
 */
function fillFromDatasheet(option) {
  const paths = model.editable || {};
  const fieldFor = (key) => Object.keys(paths).find(
    (field) => paths[field].length === 2
      && paths[field][0] === 'fanwall' && paths[field][1] === key);
  for (const [key, value] of Object.entries(option.defaults || {})) {
    const field = fieldFor(key);
    const input = field && document.getElementById(`p-${field}`);
    // `curve` has no field: it is the unit's P-Q curve, which the page never
    // showed. The server replaces it with the new unit's on Apply.
    if (!input) continue;
    input.value = dec(value);
  }
}

/**
 * Choosing a component is an edit to the spec, so it waits for Apply like
 * every other field rather than saving on change. The cost beside it updates
 * at once: what the choice costs is the reason for making it, and seeing it
 * only after a round trip makes the select feel like it did nothing.
 */
function wireComponentPickers() {
  for (const select of document.querySelectorAll('[data-component-role]')) {
    select.addEventListener('change', () => {
      const role = (model.components || []).find(
        (r) => r.role === select.dataset.componentRole);
      const option = role?.options.find((o) => o.id === select.value);
      const cost = select.parentElement.querySelector('.component-cost');
      if (option && cost && option.free_area != null) {
        cost.textContent =
          `${(option.free_area * 100).toFixed(0)} % free · K ${option.k.toFixed(2)}`;
      }
    });
  }
}

/**
 * Mesh presets, offered beside the cell-size box rather than instead of it.
 *
 * The cell size is the one input that decides what a run COSTS, and it is the
 * hardest to choose by typing: three numbers, and the consequence is a cell
 * count the engineer cannot do in their head. These are the three the worked
 * cases use, named by what they resolve, and the note under the box turns
 * whatever is in it -- preset or typed -- into the number that actually
 * matters (ADR-033).
 *
 * The axes differ on purpose: a rack is wide in x, and the vertical has to
 * land the false ceiling, the fan wall top and the rack tops on cell faces.
 */
const MESH_PRESETS = [
  { label: 'Conceptual — one rack per cell in plan', value: [0.6, 0.3, 0.25] },
  { label: 'Standard — half a rack in plan', value: [0.3, 0.3, 0.25] },
  { label: 'Fine — a quarter rack, for one POD', value: [0.2, 0.2, 0.1] },
];

/** Cells the domain would hold at this cell size, and what that costs. */
function meshNote(text) {
  const cell = String(text).split(/[,\s]+/).map(Number).filter((v) => v > 0);
  const d = model.domain;
  if (!d || (cell.length !== 1 && cell.length !== 3)) return '';
  const size = [0, 1, 2].map((a) => d.hi[a] - d.lo[a]);
  const each = [0, 1, 2].map((a) => Math.ceil(size[a] / (cell[cell.length === 1 ? 0 : a])));
  const cells = each[0] * each[1] * each[2];
  // Anchored on the worked cases: 275,400 cells settled in about 7 minutes on
  // 4 cores, and cost rises faster than the cell count because a finer mesh
  // also needs more iterations. Deliberately vague -- it is an order of
  // magnitude, and saying so is the point.
  const minutes = (cells / 275400) ** 1.3 * 7;
  const cost =
    minutes < 2 ? 'a couple of minutes'
      : minutes < 90 ? `roughly ${Math.round(minutes)} min on 4 cores`
        : `hours — ${Math.round(minutes / 60)} h or so on 4 cores`;
  return `${each.join(' × ')} = ${cells.toLocaleString('en-US')} cells · ${cost}`;
}

function inputHtml(p) {
  const value = specValue(p.key);
  if (p.check) {
    // Absent means the default, and the default is not always on: the hot
    // aisle is contained unless a case says otherwise, a supply plenum is
    // there only where a case asks for one (ADR-058). A switch whose spec
    // value is a word rather than a flag says which word means on.
    const on = value === undefined || value === null
      ? (p.default ?? true)
      : (p.on ? value === p.on : !!value);
    return `<div class="param">
      <label for="p-${p.key}">${p.label}</label>
      <input type="checkbox" id="p-${p.key}" ${on ? 'checked' : ''} />
    </div>`;
  }
  const field = `<input type="${p.text ? 'text' : 'number'}" id="p-${p.key}"
           step="${p.step ?? 'any'}" value="${formatValue(value)}" />`;
  if (p.key !== 'cell_size') {
    return `<div class="param">
      <label for="p-${p.key}">${p.label}${
        p.unit ? ` <span class="unit">${p.unit}</span>` : ''
      }</label>
      ${field}
    </div>`;
  }
  const current = formatValue(value);
  const options = MESH_PRESETS.map((preset) => {
    const text = preset.value.join(', ');
    return `<option value="${text}" ${text === current ? 'selected' : ''}>${
      preset.label
    }</option>`;
  }).join('');
  return `<div class="param">
      <label for="p-${p.key}">${p.label}${
        p.unit ? ` <span class="unit">${p.unit}</span>` : ''
      }</label>
      ${field}
    </div>
    <div class="param param-wide">
      <select id="p-cell-preset">
        <option value="">Custom — type the three values above</option>
        ${options}
      </select>
    </div>
    <p class="param-note" id="cell-note">${meshNote(current)}</p>`;
}

/** A list in the spec (an anisotropic cell) shows as "0.2, 0.2, 0.1". */
function formatValue(value) {
  return Array.isArray(value) ? value.join(', ') : value;
}

function specValue(key) {
  const path = model.editable?.[key];
  if (!path) return '';
  let node = model.spec;
  for (const step of path) node = node?.[step];
  // A field a case says nothing about shows the house standard it would get,
  // not an empty box. Blank says neither what the number would be nor that
  // there is one, so the reader has to guess whether leaving it empty means
  // anything (ADR-059).
  if (node === undefined || node === null) {
    return model.plenum_defaults?.[key] ?? '';
  }
  return node;
}

function renderSummary() {
  // Label, the dimension, and what it implies -- stacked rather than in three
  // columns, because "7,20 m² · 0,19 m/s" wrapped mid-number in a side panel.
  document.getElementById('summary').innerHTML = model.summary
    .map(
      ([label, a, b]) => `<div class="fact">
        <span class="fact-label">${label}</span>
        <span class="fact-a">${a}</span>
        <span class="fact-b">${b}</span>
      </div>`,
    )
    .join('');
}

function renderWarnings() {
  const card = document.getElementById('warnings-card');
  const list = document.getElementById('warnings');
  const items = model.warnings || [];
  card.hidden = items.length === 0;
  list.innerHTML = items
    .map((w) => `<li><span class="mark">!</span><span>${w}</span></li>`)
    .join('');
}

function renderAlerts() {
  const card = document.getElementById('alerts-card');
  const list = document.getElementById('alerts');
  const items = model.alerts || [];
  card.hidden = items.length === 0;
  list.innerHTML = items
    .map((a) => `<li><span class="mark">!</span><span>${a}</span></li>`)
    .join('');
}

function renderSensors(history) {
  const host = document.getElementById('sensors');
  if (!host) return;
  const groups = history?.groups?.length ? history.groups : null;
  const iterations = history?.iterations || [];
  const last = iterations.length - 1;

  const placed = (model.stations || []).map((station) => {
    const live = groups?.find((g) => g.name === station.name);
    return { ...station, live };
  });

  const balance = history?.balance?.length
    ? history.balance[history.balance.length - 1]
    : null;

  host.innerHTML = `${balance ? balanceStrip(balance) : ''}
  <table class="sensors">
    <thead><tr>
      <th>Station</th><th>Mixed</th><th>Range</th>
      <th>Flow</th><th>Face velocity</th>
    </tr></thead>
    <tbody>${placed
      .map(({ label, note, live }) => {
        const t = live?.temp_c?.[last];
        const low = live?.low_c?.[last];
        const high = live?.high_c?.[last];
        const flow = live?.flow_m3h?.[last];
        const v = live?.speed_ms?.[last];
        return `<tr>
          <td><span class="sensor-label">${label}</span>
              <span class="sensor-note">${note}</span></td>
          <td>${t == null ? '—' : `${fmt(t, 1)} °C`}</td>
          <td class="muted">${low == null || high == null ? '—' :
            `${fmt(low, 1)}–${fmt(high, 1)} °C`}</td>
          <td>${flow == null ? '—' : `${fmt(flow, 0)} m³/h`}</td>
          <td>${v == null ? '—' : `${fmt(v, 2)} m/s`}</td>
        </tr>`;
      })
      .join('')}</tbody></table>
    <p class="sensor-foot">Each row is the whole stream crossing that surface:
      the temperature is the mixing cup, weighted by what each part of the
      surface carries, and the range is what the air at it actually spans. A
      room whose cabinets carry different loads has no single temperature at
      any station, so the range is part of the reading.</p>
    ${iterations.length ? drawSensorChart(history) :
      `<p class="empty">readings appear from the first sample</p>`}`;
}

/**
 * The two numbers that say whether the run means anything yet.
 *
 * Energy closure first, because it is the one that cannot be satisfied by
 * accident: every watt installed has to leave as warmer air. A run still
 * filling shows it climbing; residuals show nothing at all.
 */
function balanceStrip(b) {
  const closure = b.closure == null ? null : b.closure * 100;
  const state =
    closure == null ? '' : closure >= 90 && closure <= 110 ? 'good' : 'warn';
  return `<div class="balance">
    <span class="balance-item" data-state="${state}">
      <b>${closure == null ? '—' : `${fmt(closure, 0)}%`}</b>
      <i>of the load in the return air</i></span>
    <span class="balance-item"><b>${fmt(b.return_temp_c, 1)} °C</b>
      <i>return at the fan wall</i></span>
    <span class="balance-item"><b>${fmt(b.peak_speed_ms, 2)} m/s</b>
      <i>peak air speed</i></span>
    ${b.fan_rise_pa == null ? '' : `<span class="balance-item">
      <b>${fmt(b.fan_rise_pa, 1)} Pa</b><i>fan wall pressure</i></span>`}
    <span class="balance-item" data-state="${b.backflow_kg_s > 0.01 ? 'warn' : ''}">
      <b>${fmt(b.backflow_kg_s, 3)} kg/s</b><i>backflow at the intake</i></span>
  </div>`;
}

/** Temperature at each place, against iteration. Linear -- these are degrees. */
function drawSensorChart(history) {
  const W = 640;
  const H = 180;
  // Room on the right for the longest place name, its colour mark and the
  // gap before it: `Behind the fan wall` is the one that has to fit.
  const pad = { left: 44, right: 154, top: 10, bottom: 26 };
  const its = history.iterations;
  const series = history.groups.filter((g) => g.temp_c?.length);
  if (!series.length || its.length < 2) return '';

  const values = series
    .flatMap((g) => [...g.temp_c, ...(g.low_c || []), ...(g.high_c || [])])
    .filter((v) => Number.isFinite(v));
  const lo = Math.floor(Math.min(...values) - 0.5);
  const hi = Math.ceil(Math.max(...values) + 0.5);
  const X = (i) => pad.left + (i / (its.length - 1)) * (W - pad.left - pad.right);
  const Y = (v) => pad.top + (1 - (v - lo) / (hi - lo || 1)) * (H - pad.top - pad.bottom);

  const ticks = [lo, (lo + hi) / 2, hi];
  const grid = ticks
    .map(
      (v) => `<line class="chart-grid" x1="${pad.left}" x2="${W - pad.right}"
                y1="${Y(v)}" y2="${Y(v)}"/>
              <text class="chart-tick" x="${pad.left - 6}" y="${Y(v) + 4}"
                text-anchor="end">${fmt(v, 0)}</text>`,
    )
    .join('');

  // The supply and the rack intake sit within a kelvin of each other at
  // steady state, so their end labels would overlap. Stack them apart.
  const ends = series
    .map((g, i) => ({ i, y: Y(g.temp_c[g.temp_c.length - 1]) }))
    .sort((a, b) => a.y - b.y);
  const labelY = {};
  let previous = -Infinity;
  for (const end of ends) {
    const y = Math.max(end.y, previous + 13);
    labelY[end.i] = y;
    previous = y;
  }

  // The band the air at a station actually spans, behind its mixing cup. One
  // line per station would say a half-populated row is one temperature, which
  // is the reading ADR-078 got rid of; the band is where that shows.
  const bands = series
    .map((g, i) => {
      const low = g.low_c || [];
      const high = g.high_c || [];
      const span = Math.min(low.length, high.length, its.length);
      const usable = [];
      for (let k = 0; k < span; k += 1) {
        if (Number.isFinite(low[k]) && Number.isFinite(high[k])) usable.push(k);
      }
      if (usable.length < 2) return '';
      const top = usable.map((k) => `${k === usable[0] ? 'M' : 'L'}${X(k).toFixed(1)},${Y(high[k]).toFixed(1)}`);
      const bottom = [...usable].reverse().map((k) => `L${X(k).toFixed(1)},${Y(low[k]).toFixed(1)}`);
      return `<path class="chart-band" fill="var(--series-${(i % 8) + 1})"
                d="${top.join('')}${bottom.join('')}Z"/>`;
    })
    .join('');

  const lines = series
    .map((g, i) => {
      const d = g.temp_c
        .map((v, k) => `${k ? 'L' : 'M'}${X(k).toFixed(1)},${Y(v).toFixed(1)}`)
        .join('');
      const colour = `var(--series-${(i % 8) + 1})`;
      const end = g.temp_c.length - 1;
      const endY = Y(g.temp_c[end]);
      const markX = X(end) + 8;
      // The label carries a mark in the series colour and its text in ink.
      // Colouring the text was the intent here and never worked -- the class
      // sets `fill`, and a CSS rule beats a presentation attribute, so the
      // three stacked names came out the same grey and identified nothing
      // (ADR-053). A mark also survives being read in print or by someone
      // who cannot separate the hues, which coloured text does not.
      const mark = `<rect class="chart-mark" x="${markX}" y="${labelY[i] - 1.5}"
        width="11" height="3" rx="1.5" fill="${colour}"/>`;
      // A label pushed off its own line needs a thread back to it, or the
      // stack is three names beside four lines and the reader guesses.
      const leader = Math.abs(labelY[i] - endY) < 1.5 ? ''
        : `<path class="chart-leader" stroke="${colour}"
             d="M${X(end).toFixed(1)},${endY.toFixed(1)}
                L${(markX - 3).toFixed(1)},${labelY[i].toFixed(1)}"/>`;
      return `<path class="chart-line" d="${d}" stroke="${colour}"/>
        ${leader}${mark}
        <text class="chart-tick" x="${markX + 16}" y="${labelY[i] + 4}"
          >${g.label}</text>`;
    })
    .join('');

  return `<svg class="sensor-chart" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="temperature at each place against iteration">
    ${grid}${bands}${lines}
    <text class="chart-tick" x="${(pad.left + W - pad.right) / 2}" y="${H - 6}"
      text-anchor="middle">iteration ${its[its.length - 1]}</text>
  </svg>`;
}

const fmt = (v, places) =>
  Number(v).toLocaleString('en-US', {
    minimumFractionDigits: places,
    maximumFractionDigits: places,
  });

// --- actions -----------------------------------------------------------------

async function applyChanges() {
  const button = document.getElementById('apply');
  button.disabled = true;
  const changes = {};
  for (const section of activeSections()) {
    for (const p of section.params) {
      const input = document.getElementById(`p-${p.key}`);
      if (!input) continue;
      if (p.check) {
        changes[p.key] = input.checked;
      } else if (input.value !== '') {
        changes[p.key] = p.text ? input.value : Number(input.value);
      }
    }
  }
  // Which unit the case names travels with the rest, so choosing it and
  // ticking the raised floor it needs land in the same Apply (ADR-092).
  const unit = document.getElementById('fan-model');
  if (unit) changes.fan_model = unit.value;
  // Which component fills a role travels with the rest: the server names the
  // field after the role, and validates it against the library (ADR-051).
  for (const select of document.querySelectorAll('[data-component-role]')) {
    changes[select.dataset.componentRole] = select.value;
  }

  try {
    const next = await fetchJson('/api/model', { method: 'POST', body: changes });
    if (next.error) throw new Error(next.error);
    model = next;
    render();
    if (next.rejected?.length) {
      alert(`Not accepted:\n${next.rejected.join('\n')}`);
    }
  } catch (error) {
    alert(`Could not apply: ${error.message}`);
  } finally {
    button.disabled = false;
  }
}

async function stopRun() {
  const stop = document.getElementById('stop');
  if (!confirm(
    'Stop at the next iteration?\n\n'
    + 'The solver writes the field it has and exits, and the result is read '
    + 'and checked as usual. A run cut short before it settled will fail the '
    + '"settled" check -- that is the answer saying how far it got.',
  )) return;
  stop.disabled = true;
  try {
    const response = await fetchJson('/api/stop', { method: 'POST', body: {} });
    if (response.blocked) throw new Error(response.blocked);
    setStage(response.run);
  } catch (error) {
    alert(`Could not stop: ${error.message}`);
    stop.disabled = false;
  }
}

async function startRun() {
  const button = document.getElementById('run');
  button.disabled = true;
  try {
    const response = await fetchJson('/api/run', { method: 'POST', body: {} });
    if (response.blocked) throw new Error(response.blocked);
    setStage(response.run);
  } catch (error) {
    alert(`Could not start: ${error.message}`);
    button.disabled = false;
  }
}

/**
 * The link to the exported result, told apart from the run on screen.
 *
 * Three states, because there are three things it can be, and opening the
 * wrong one silently is the failure this exists to prevent:
 *
 *  - nothing exported yet          -> no link at all
 *  - a run is in flight            -> disabled; whatever is on disk is about
 *                                     to be replaced by it
 *  - exported from other inputs    -> live, but it says so on the button and
 *                                     names what changed, because looking at
 *                                     the previous run on purpose is a
 *                                     legitimate thing to do
 */
function renderResultsLink(stage) {
  const link = document.getElementById('see-results');
  if (!link) return;
  const state = model.results || { exists: false, matches: false, note: '' };
  link.hidden = !state.exists;
  if (!state.exists) return;
  const busy = ['meshing', 'solving', 'exporting'].includes(stage);
  const stale = !state.matches;
  link.textContent = busy
    ? 'Results after the run'
    : stale
      ? 'See previous result'
      : 'See results';
  link.setAttribute('aria-disabled', busy ? 'true' : 'false');
  link.title = busy
    ? `A run is in flight. What is on disk is the previous result${
        state.note ? ` (${state.note})` : ''
      }; it is replaced when this run finishes.`
    : stale
      ? `This result is ${state.note}. Run again to replace it.`
      : '';
}

function setStage(run) {
  if (!run) return;
  const badge = document.getElementById('stage');
  badge.dataset.stage = run.stage;
  const labels = {
    idle: 'ready to run',
    meshing: `meshing${run.step ? ` · ${run.step}` : ''}`,
    solving: 'solving',
    exporting: 'reading the result',
    done: 'solved',
    failed: 'failed',
  };
  badge.textContent = labels[run.stage] || run.stage;
  const button = document.getElementById('run');
  if (button) {
    const busy = ['meshing', 'solving', 'exporting'].includes(run.stage);
    button.disabled = busy || Boolean(model.blocked);
    button.title = model.blocked || '';
  }
  renderResultsLink(run.stage);
  const stop = document.getElementById('stop');
  if (stop) {
    // Only while the solver is actually iterating: meshing is seconds long
    // and has no solver to ask.
    stop.hidden = run.stage !== 'solving';
    stop.disabled = Boolean(run.stopping);
    stop.textContent = run.stopping ? 'stopping…' : 'Stop';
    stop.title = run.stopping
      ? 'Asked. The solver finishes the iteration it is on, writes the field '
        + 'and exits; the result is then read and checked as usual.'
      : 'Stop at the next iteration, keeping what has been computed. The '
        + 'result is exported and held to the same eleven checks, so a run '
        + 'cut short before it settled will say so.';
  }
  const note = document.getElementById('progress-note');
  if (note && (run.stage === 'failed' || run.stage === 'done') && run.message) {
    note.textContent = run.message;
  }
}

// --- progress ----------------------------------------------------------------

let lastStage = null;

async function poll() {
  clearTimeout(pollTimer);
  try {
    const status = await fetchJson('/api/progress');
    // The results state was read when the page loaded. A run that has just
    // finished replaced the export, so ask again -- once, on the transition,
    // rather than parsing the export on every tick.
    if (lastStage !== status.run?.stage && status.run?.stage === 'done') {
      try {
        model.results = (await fetchJson('/api/model')).results;
      } catch {
        /* keep the stale state rather than losing the link entirely */
      }
    }
    lastStage = status.run?.stage ?? lastStage;
    setStage(status.run);
    drawProgress(status.residuals);
    renderSensors(status.sensors);
    const note = document.getElementById('progress-note');
    if (note && !model.blocked && status.residuals.total) {
      note.textContent = `${status.residuals.total.toLocaleString('en-US')} iterations`;
    }
  } catch {
    /* the server may be mid-restart; try again on the next tick */
  }
  const busy = ['meshing', 'solving', 'exporting'].includes(
    document.getElementById('stage')?.dataset.stage,
  );
  pollTimer = setTimeout(poll, busy ? 2000 : 8000);
}

function drawProgress(residuals) {
  const host = document.getElementById('progress');
  if (!host) return;
  const width = host.clientWidth;
  const height = host.clientHeight;
  host.replaceChildren();
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', width);
  svg.setAttribute('height', height);
  svg.setAttribute('viewBox', `0 0 ${width} ${height}`);

  const names = RESIDUAL_ORDER.filter((n) => residuals.series?.[n]?.length);
  if (!names.length) {
    svg.append(
      text(width / 2, height / 2, 'no data yet: the curve appears while the solver runs',
        'chart-empty'),
    );
    host.append(svg);
    return;
  }

  const M = { top: 12, right: 16, bottom: 30, left: 50 };
  const plotW = width - M.left - M.right;
  const plotH = height - M.top - M.bottom;
  const values = names.flatMap((n) => residuals.series[n]).filter((v) => v > 0);
  const loMag = Math.floor(Math.log10(Math.min(...values)));
  const hiMag = Math.ceil(Math.log10(Math.max(...values)));
  const n = residuals.iterations.length;
  const X = (i) => M.left + (i / Math.max(n - 1, 1)) * plotW;
  const Y = (v) => M.top + ((hiMag - Math.log10(v)) / Math.max(hiMag - loMag, 1)) * plotH;

  for (let mag = loMag; mag <= hiMag; mag += 1) {
    svg.append(line(M.left, Y(10 ** mag), M.left + plotW, Y(10 ** mag), 'chart-grid'));
    svg.append(text(M.left - 8, Y(10 ** mag) + 4, `1e${mag}`, 'chart-tick', 'end'));
  }
  svg.append(line(M.left, M.top + plotH, M.left + plotW, M.top + plotH, 'chart-axis'));
  for (let i = 0; i < 4; i += 1) {
    const index = Math.round((i / 3) * (n - 1));
    svg.append(
      text(X(index), M.top + plotH + 18, String(residuals.iterations[index]), 'chart-tick',
        'middle'),
    );
  }
  names.forEach((name, slot) => {
    const points = [];
    residuals.series[name].forEach((v, i) => {
      if (v > 0) points.push(`${X(i).toFixed(1)},${Y(v).toFixed(1)}`);
    });
    const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    poly.setAttribute('points', points.join(' '));
    poly.setAttribute('class', 'chart-line');
    poly.setAttribute('style', `stroke: var(--series-${slot + 1})`);
    svg.append(poly);
  });
  host.append(svg);
}

// --- helpers -----------------------------------------------------------------

function line(x1, y1, x2, y2, cls) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  node.setAttribute('x1', x1); node.setAttribute('y1', y1);
  node.setAttribute('x2', x2); node.setAttribute('y2', y2);
  node.setAttribute('class', cls);
  return node;
}

function text(x, y, content, cls, anchor) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', 'text');
  node.setAttribute('x', x); node.setAttribute('y', y);
  node.setAttribute('class', cls);
  if (anchor) node.setAttribute('text-anchor', anchor);
  node.textContent = content;
  return node;
}

async function fetchJson(url, { method = 'GET', body } = {}) {
  const response = await fetch(withCase(url), {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

function debounce(fn, ms) {
  let timer;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
}

function setupTheme() {
  const button = document.getElementById('theme-toggle');
  const current = () =>
    document.documentElement.dataset.theme ||
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const apply = () => {
    button.textContent = current() === 'dark' ? 'Light' : 'Dark';
    if (model) drawViews();
  };
  button.addEventListener('click', () => {
    document.documentElement.dataset.theme = current() === 'dark' ? 'light' : 'dark';
    apply();
  });
  apply();
}

/* --- the case menu ----------------------------------------------------------
 *
 * Opening, starting and importing a case were terminal-only: `aicfd new`, or
 * a `?case=` typed into the address bar. The page is the interface (ADR-005),
 * and a person who has to leave it to begin is not being served by it.
 *
 * Switching RELOADS. Every card's state comes from the server anyway, so
 * rebuilding it in place would be a second way of doing what a reload already
 * does correctly -- and a second way is a second thing to keep right.
 */

const menu = {
  panel: () => document.getElementById('case-menu'),
  say(message, tone = '') {
    const note = document.getElementById('case-status');
    if (!note) return;
    // The server answers `TypeError: ...`; the class name is for a log, not
    // for somebody who just pasted a case in.
    note.textContent = String(message).replace(/^[A-Za-z]*(Error|Exception):\s*/, '');
    if (tone) note.dataset.tone = tone; else delete note.dataset.tone;
  },
};

const ago = (seconds) => {
  const m = Math.round((Date.now() / 1000 - seconds) / 60);
  if (m < 1) return 'just now';
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  return h < 48 ? `${h} h ago` : `${Math.round(h / 24)} d ago`;
};

async function loadCaseMenu() {
  const list = document.getElementById('case-list');
  const from = document.getElementById('case-new-from');
  if (!list) return;
  try {
    // `/api/cases` marks which one is OPEN, so it needs the case like
    // every other call: without it the menu ticked whichever case the
    // server was started on (ADR-091).
    const payload = await (await fetch(withCase('/api/cases'))).json();
    if (payload.error) throw new Error(payload.error);
    list.innerHTML = payload.cases
      .map((c) => `<a href="?case=${encodeURIComponent(c.case)}"
           data-current="${c.current ? 1 : 0}"><span>${c.case}</span>
           <span class="when">${ago(c.modified)}</span></a>`)
      .join('') || '<p class="casemenu-note">nothing in cases/ yet</p>';
    from.innerHTML = '<option value="">from the starter</option>'
      + payload.cases.map((c) => `<option value="${c.case}">copy ${c.case}</option>`)
        .join('');
  } catch (e) {
    list.innerHTML = `<p class="casemenu-note" data-tone="bad">${e.message}</p>`;
  }
}

async function postCase(url, body, verb) {
  menu.say(`${verb}…`);
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const payload = await res.json();
    // The server builds the case before it writes it (ADR-055), so a refusal
    // here means nothing was saved -- which is worth saying, because the
    // engineer's next question is whether they have to go and delete a file.
    if (payload.error) { menu.say(`${payload.error} Nothing was saved.`, 'bad'); return; }
    menu.say(`${payload.case} created — opening…`, 'good');
    location.search = `?case=${encodeURIComponent(payload.case)}`;
  } catch (e) {
    menu.say(`${e.message} Nothing was saved.`, 'bad');
  }
}

function wireCaseMenu() {
  const el = (id) => document.getElementById(id);
  el('case-menu')?.addEventListener('toggle', (event) => {
    if (event.target.open) loadCaseMenu();
  });
  el('case-new')?.addEventListener('click', () => postCase('/api/cases', {
    name: el('case-new-name').value, from: el('case-new-from').value || null,
  }, 'creating'));
  el('case-import')?.addEventListener('click', () => postCase('/api/cases/import', {
    name: el('case-import-name').value, yaml: el('case-import-yaml').value,
  }, 'importing'));
  el('case-copy')?.addEventListener('click', async () => {
    try {
      const payload = await (await fetch(withCase('/api/cases/export'))).json();
      if (payload.error) throw new Error(payload.error);
      await navigator.clipboard.writeText(payload.yaml);
      menu.say(`${payload.case}.yaml copied — comments and all.`, 'good');
    } catch (e) {
      // Clipboard access is refused outside a secure context, so the text goes
      // in the box instead: a paste target beats an error nobody can act on.
      const box = el('case-import-yaml');
      if (box) {
        const payload = await (await fetch(withCase('/api/cases/export'))).json();
        box.value = payload.yaml || '';
        menu.say('Clipboard refused, so it is in the box below — select and copy.');
      } else {
        menu.say(e.message, 'bad');
      }
    }
  });
}
