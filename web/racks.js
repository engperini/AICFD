/**
 * The rack page: one load for the hall, and every position that disagrees.
 *
 * A hall is specified by one load per rack, because that is how a hall is
 * bought and how a fan wall is sized against it. No hall is ever filled that
 * way. Positions are reserved, staged, or left for growth, and where the gaps
 * sit is not a detail: an unloaded cabinet is a path the cold aisle can take
 * straight to the hot one, and a row with its end positions empty loads its
 * unit differently from one with the gaps in the middle (ADR-054).
 *
 * So the standard is set once at the top and every position takes it, and the
 * table below is where a position says otherwise — zero included. The case
 * file stores only the positions that differ, so raising the hall's load later
 * moves every rack that never disagreed with it.
 *
 * Everything here reaches the solver. A position's load is its own heat
 * source; set it to zero and the source is not written at all. The cabinet
 * stays where it is and keeps the resistance of the row around it, because an
 * empty position is blanked rather than left open.
 */

const byId = document.getElementById.bind(document);
let state = null;
let draft = {};

const fmt = (v, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(+v) ? '—' : (+v).toFixed(digits);
const whole = (v) => (Number.isFinite(+v) ? (+v).toLocaleString('en-US') : '—');

async function load() {
  const res = await fetch('/api/racks');
  const payload = await res.json();
  if (payload.error) throw new Error(payload.error);
  state = payload;
  draft = {};
  render();
}

function render() {
  const t = state.totals;
  byId('case-name').textContent = `${state.case} · racks`;
  byId('root').innerHTML = `
    <div class="layout">
      <div class="column">
        ${standardCard()}
        ${positionsCard()}
      </div>
      <div class="column">${aside(t)}</div>
    </div>`;
  wire();
}

function standardCard() {
  const s = state.standard;
  return `
    <section class="card">
      <div class="card-head">
        <span class="card-title">Every rack</span>
        <span class="card-sub">what a position carries unless it says otherwise</span>
      </div>
      <div class="role">
        <div class="field">
          <label for="standard">Load per rack, kW</label>
          <input id="standard" type="number" step="0.01" min="0.1" max="200"
                 value="${s.load_kw}" />
        </div>
        <div class="field">
          <label>Rack size (w × d × h), m</label>
          <input value="${s.size.join(' × ')}" readonly style="opacity:.8" />
        </div>
        <div class="field">
          <label>Air the rack's own fans draw, CFM/kW</label>
          <input value="${s.cfm_per_kw ?? '—'}" readonly style="opacity:.8" />
        </div>
        <p class="note">Size and CFM/kW are geometry and are set on the model
          page, where the mesh they imply is shown beside them. Changing the
          load here moves every position that has not been given one of its
          own.</p>
        <div class="actions">
          <button class="primary" type="button" id="save">Save</button>
          <button type="button" id="undo">Undo</button>
          <span class="card-sub" id="status"></span>
        </div>
      </div>
    </section>`;
}

function positionsCard() {
  return `
    <section class="card">
      <div class="card-head">
        <span class="card-title">Each position</span>
        <span class="card-sub">blank follows the standard; 0 is a cabinet with
          nothing in it</span>
      </div>
      <div class="rack-filter">
        <input type="search" id="filter" placeholder="row or position, e.g. F1B1" />
        <label><input type="checkbox" id="only-stated" /> only those that differ</label>
        <span class="spacer"></span>
        <button type="button" id="zero-filtered">Set the shown to 0</button>
        <button type="button" id="reset-filtered">Back to the standard</button>
      </div>
      <div class="rack-table">
        <table>
          <thead><tr>
            <th>Position</th><th>Row</th><th>Load kW</th><th>Own fans m³/h</th>
          </tr></thead>
          <tbody id="rows"></tbody>
        </table>
      </div>
    </section>`;
}

function aside(t) {
  return `
    <section class="card">
      <div class="card-head"><span class="card-title">This hall</span></div>
      <div class="totals">
        <div class="total"><b>${whole(t.positions)}</b><i>positions</i></div>
        <div class="total" data-state="${t.unloaded ? 'off' : ''}">
          <b>${whole(t.unloaded)}</b><i>carry no load</i></div>
        <div class="total"><b>${fmt(t.load_kw, 0)} kW</b><i>IT load</i></div>
        <div class="total"><b>${whole(t.airflow_m3h)}</b><i>m³/h the racks draw</i></div>
      </div>
      <p class="prose">
        ${t.unloaded
          ? `Nominal would be ${fmt(t.nominal_kw, 0)} kW with every position
             filled. The difference is ${fmt(t.nominal_kw - t.load_kw, 0)} kW
             the plant does not have to remove, spread the way
             ${whole(t.unloaded)} empty ${t.unloaded === 1 ? 'cabinet' : 'cabinets'}
             ${t.unloaded === 1 ? 'is' : 'are'} spread — which is what decides
             how evenly the units load, not the total.`
          : 'Every position carries the standard, so the hall is at its nominal load.'}
      </p>
    </section>
    <section class="card">
      <div class="card-head"><span class="card-title">What a zero does</span></div>
      <p class="prose">
        The cabinet stays in the model: it is still an obstruction, still a
        cell zone, and still resists like the cabinets either side of it — an
        empty position is blanked, which is what blanking plates are for. What
        it loses is its heat source. So the row still costs the fan what the
        row costs, and the hall simply has less in it to cool.
      </p>
      <p class="note">
        Delete the position instead and you lose that: the row would be short
        a cabinet and the air would take the gap. The rack-resistance check
        says how many positions carry nothing beside its verdict, so a hall
        below its nominal load is never read as one at it.
      </p>
    </section>`;
}

/** The table body, rebuilt on every filter change rather than hidden in CSS. */
function drawRows() {
  const needle = byId('filter').value.trim().toUpperCase();
  const onlyStated = byId('only-stated').checked;
  const standard = Number(byId('standard').value);
  const shown = state.racks.filter((r) => {
    const value = current(r);
    if (onlyStated && value === standard) return false;
    return !needle || r.id.toUpperCase().includes(needle)
      || r.row.toUpperCase().includes(needle);
  });
  byId('rows').innerHTML = shown.map((r) => {
    const value = current(r);
    const zero = value === 0;
    const differs = value !== standard;
    return `<tr data-stated="${differs}" data-zero="${zero}">
      <td><span class="dot ${zero ? 'zero' : differs ? 'stated' : ''}"></span>${r.id}</td>
      <td>${r.row}</td>
      <td><input type="number" step="0.01" min="0" max="200" data-rack="${r.id}"
                 value="${value}" /></td>
      <td>${whole(Math.round(value * (state.standard.cfm_per_kw || 0) * 1.69901))}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="4" class="empty">nothing matches</td></tr>';
  for (const input of byId('rows').querySelectorAll('[data-rack]')) {
    input.addEventListener('input', () => {
      draft[input.dataset.rack] = input.value;
      markChanged();
    });
  }
  byId('shown-count')?.replaceChildren(String(shown.length));
}

/** What this position carries right now: the draft, else what the case says. */
function current(rack) {
  const edited = draft[rack.id];
  if (edited !== undefined && edited !== '') return Number(edited);
  if (edited === '') return Number(byId('standard').value);
  return rack.load_kw;
}

function markChanged() {
  byId('status').textContent = 'not saved';
}

function wire() {
  byId('filter').addEventListener('input', drawRows);
  byId('only-stated').addEventListener('change', drawRows);
  byId('standard').addEventListener('input', () => { drawRows(); markChanged(); });

  // The two bulk buttons act on what the filter is showing, so `F1B1` then
  // `Set the shown to 0` empties one block without touching the rest.
  const filtered = () => {
    const needle = byId('filter').value.trim().toUpperCase();
    return state.racks.filter((r) => !needle
      || r.id.toUpperCase().includes(needle) || r.row.toUpperCase().includes(needle));
  };
  byId('zero-filtered').addEventListener('click', () => {
    for (const r of filtered()) draft[r.id] = '0';
    drawRows();
    markChanged();
  });
  byId('reset-filtered').addEventListener('click', () => {
    for (const r of filtered()) draft[r.id] = '';
    drawRows();
    markChanged();
  });

  byId('save').addEventListener('click', save);
  byId('undo').addEventListener('click', () => load());
  drawRows();
}

async function save() {
  const button = byId('save');
  button.disabled = true;
  byId('status').textContent = 'saving…';
  // Every position is sent, not only the edited ones: the server drops those
  // equal to the standard, so a position edited back to it stops being stated
  // and follows the standard again from then on.
  const loads = {};
  for (const rack of state.racks) {
    const value = draft[rack.id];
    loads[rack.id] = value === undefined ? rack.load_kw : (value === '' ? null : value);
  }
  try {
    const res = await fetch('/api/racks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ load_kw: Number(byId('standard').value), loads }),
    });
    const payload = await res.json();
    if (payload.error) throw new Error(payload.error);
    state = payload;
    draft = {};
    render();
    byId('status').textContent = payload.rejected?.length
      ? `Not accepted: ${payload.rejected.join('; ')}`
      : 'saved';
  } catch (error) {
    byId('status').textContent = String(error.message || error);
  } finally {
    button.disabled = false;
  }
}

function setupTheme() {
  const button = byId('theme-toggle');
  const dark = () => document.documentElement.dataset.theme === 'dark'
    || (!document.documentElement.dataset.theme
        && window.matchMedia('(prefers-color-scheme: dark)').matches);
  const paint = () => { button.textContent = dark() ? 'Light' : 'Dark'; };
  button.addEventListener('click', () => {
    document.documentElement.dataset.theme = dark() ? 'light' : 'dark';
    paint();
  });
  paint();
}

setupTheme();
load().catch((error) => {
  byId('root').innerHTML =
    `<div class="layout"><div class="column"><section class="card">
       <p class="prose">${String(error.message || error)}</p>
     </section></div></div>`;
});
