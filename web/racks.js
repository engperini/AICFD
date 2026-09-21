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
 *
 * Two more things a row is made of (ADR-074). A position has its own WIDTH,
 * because a hall is rarely one cabinet type; and a position can be a BLANKING
 * PANEL instead of a cabinet, which is a plate closing the row where no
 * cabinet stands. A zero-load cabinet and a blank are not the same thing and
 * the page never treats them as one: the cabinet still breathes and still
 * resists, the plate passes no air at all.
 *
 * The TYPICAL ROW at the top is the pattern every row of the hall is built
 * from. Edit it and every row changes together; edit a position in the table
 * below and only that one disagrees. Same division the load has always had.
 */
import { num, dec } from './decimal.js';

const byId = document.getElementById.bind(document);
let state = null;
let draft = {};          // per-position load, by rack id
let sizeDraft = {};      // per-position width, by rack id
let blankDraft = {};     // per-position blank/cabinet, by rack id
let planDraft = null;    // the typical row, null until it is touched

const fmt = (v, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(+v) ? '—' : (+v).toFixed(digits);

const whole = (v) => (Number.isFinite(+v) ? (+v).toLocaleString('en-US') : '—');

async function load() {
  const res = await fetch('/api/racks');
  const payload = await res.json();
  if (payload.error) throw new Error(payload.error);
  state = payload;
  draft = {};
  sizeDraft = {};
  blankDraft = {};
  planDraft = null;
  render();
}

function render() {
  const t = state.totals;
  byId('case-name').textContent = `${state.case} · racks`;
  byId('root').innerHTML = `
    <div class="layout">
      <div class="column">
        ${standardCard()}
        ${typicalRowCard()}
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
          <input id="standard" type="text" inputmode="decimal"
                 value="${dec(s.load_kw)}" />
        </div>
        <div class="field trio">
          <label>Rack size (w × d × h), m</label>
          <div class="three">
            <input id="size-w" type="text" inputmode="decimal"
                   value="${dec(s.size[0])}" />
            <input id="size-d" type="text" inputmode="decimal"
                   value="${dec(s.size[1])}" />
            <input id="size-h" type="text" inputmode="decimal"
                   value="${dec(s.size[2])}" />
          </div>
        </div>
        <div class="field">
          <label>Air the rack's own fans draw, CFM/kW</label>
          <input value="${s.cfm_per_kw ?? '—'}" readonly style="opacity:.8" />
        </div>
        <p class="note">This is the cabinet a position takes when it says
          nothing else. The width is the one a position can override, one by
          one below or for every row at once in the typical row; depth and
          height are the row's and are the same all along it. CFM/kW is set on
          the model page, beside the mesh it implies. The mesh can only place a
          width that divides into its x cell (${fmt(s.cell_x, 2)} m here) and
          says so when it moves one.</p>
        <div class="actions">
          <button class="primary" type="button" id="save">Save</button>
          <button type="button" id="undo">Undo</button>
          <span class="card-sub" id="status"></span>
        </div>
      </div>
    </section>`;
}

/**
 * The typical row: what every row of this hall is built from.
 *
 * A hall is not a count of cabinets, it is a row repeated. Eleven cabinets and
 * a 300 mm blank is twelve positions of two kinds, and stating the count as
 * well would only invite the two to disagree — so the pattern's length IS the
 * count, and the model page's rack count follows it.
 */
function typicalRowCard() {
  const plan = rowDraft();
  const standard = state.standard;
  const total = plan.reduce(
    (sum, e) => sum + (num(e.width) ?? standard.size[0]), 0);
  return `
    <section class="card">
      <div class="card-head">
        <span class="card-title">The typical row</span>
        <span class="card-sub">every row of the hall is built from this</span>
      </div>
      <div class="rack-table">
        <table>
          <thead><tr>
            <th>#</th><th>What stands here</th><th>Width m</th>
            <th>Load kW</th><th></th>
          </tr></thead>
          <tbody id="plan-rows">${plan.map(planRow).join('')
            || '<tr><td colspan="5" class="empty">no positions</td></tr>'}</tbody>
        </table>
      </div>
      <div class="rack-filter">
        <button type="button" id="add-cabinet">Add a cabinet</button>
        <button type="button" id="add-blank">Add a blank panel</button>
        <span class="spacer"></span>
        <span class="card-sub">${plan.length} positions ·
          ${fmt(total, 2)} m of row</span>
      </div>
      <p class="note">A <b>blank panel</b> closes the row where no cabinet
        stands: it is a plate, and no air crosses it. That is not a cabinet at
        zero load — that one still breathes and still resists like the cabinets
        either side of it. Use the blank for the metres a row does not fill,
        and the zero for a cabinet position nobody has racked yet.</p>
      <div class="actions">
        <button type="button" id="apply-row">Make every row exactly this</button>
        <span class="card-sub">drops every per-position exception below</span>
      </div>
    </section>`;
}

function planRow(entry, i) {
  const s = state.standard;
  const blank = !!entry.blank;
  return `<tr data-zero="${blank}">
    <td>${i + 1}</td>
    <td><select data-plan="${i}" data-field="blank">
          <option value="" ${blank ? '' : 'selected'}>Cabinet</option>
          <option value="1" ${blank ? 'selected' : ''}>Blank panel</option>
        </select></td>
    <td><input type="text" inputmode="decimal" data-plan="${i}"
               data-field="width" value="${dec(entry.width)}"
               placeholder="${s.size[0]}" /></td>
    <td>${blank ? '<span class="card-sub">—</span>'
        : `<input type="text" inputmode="decimal" data-plan="${i}"
                  data-field="load_kw" value="${dec(entry.load_kw)}"
                  placeholder="${s.load_kw}" />`}</td>
    <td><button type="button" class="ghost-button" data-plan-drop="${i}">–</button></td>
  </tr>`;
}

/** The pattern being edited: the draft where one exists, else the case's. */
function rowDraft() {
  if (planDraft === null) planDraft = state.row.map((e) => ({ ...e }));
  return planDraft;
}

function positionsCard() {
  return `
    <section class="card">
      <div class="card-head">
        <span class="card-title">Each position</span>
        <span class="card-sub">empty follows the typical row; 0 is a cabinet
          with nothing in it</span>
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
            <th>Position</th><th>Row</th><th>What stands here</th>
            <th>Width m</th><th>Load kW</th><th>Own fans m³/h</th>
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
        <div class="total"><b>${whole(t.cabinets)}</b><i>cabinets</i></div>
        <div class="total" data-state="${t.blanks ? 'off' : ''}">
          <b>${whole(t.blanks)}</b><i>blank panels</i></div>
        <div class="total" data-state="${t.unloaded ? 'off' : ''}">
          <b>${whole(t.unloaded)}</b><i>carry no load</i></div>
        <div class="total"><b>${fmt(t.load_kw, 0)} kW</b><i>IT load</i></div>
        <div class="total"><b>${whole(t.airflow_m3h)}</b><i>m³/h the racks draw</i></div>
      </div>
      <p class="prose">
        ${t.blanks
          ? `${whole(t.blanks)} of the ${whole(t.positions)} positions
             ${t.blanks === 1 ? 'is a plate' : 'are plates'}, closing the row
             where no cabinet stands. No air crosses them, which is what makes
             them different from a cabinet at zero load. `
          : ''}
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
  const standard = num(byId('standard').value) ?? 0;
  const shown = state.racks.filter((r) => {
    const value = current(r);
    if (onlyStated && value === standard) return false;
    return !needle || r.id.toUpperCase().includes(needle)
      || r.row.toUpperCase().includes(needle);
  });
  byId('rows').innerHTML = shown.map((r) => {
    const blank = isBlank(r);
    const value = current(r);
    const zero = !blank && value === 0;
    const differs = !blank && value !== standard;
    return `<tr data-stated="${differs}" data-zero="${zero || blank}">
      <td><span class="dot ${zero || blank ? 'zero' : differs ? 'stated' : ''}"></span>${r.id}</td>
      <td>${r.row}</td>
      <td><select data-kind="${r.id}">
            <option value="" ${blank ? '' : 'selected'}>Cabinet</option>
            <option value="1" ${blank ? 'selected' : ''}>Blank panel</option>
          </select></td>
      <td><input type="text" inputmode="decimal" data-width="${r.id}"
                 value="${dec(widthOf(r))}" placeholder="${state.standard.size[0]}" /></td>
      <td>${blank ? '<span class="card-sub">—</span>'
          : `<input type="text" inputmode="decimal" data-rack="${r.id}"
                    value="${dec(value)}" />`}</td>
      <td>${blank ? '<span class="card-sub">0</span>'
          : whole(Math.round(value * (state.standard.cfm_per_kw || 0) * 1.69901))}</td>
    </tr>`;
  }).join('') || '<tr><td colspan="6" class="empty">nothing matches</td></tr>';
  for (const input of byId('rows').querySelectorAll('[data-rack]')) {
    input.addEventListener('input', () => {
      draft[input.dataset.rack] = input.value;
      markChanged();
    });
  }
  for (const input of byId('rows').querySelectorAll('[data-width]')) {
    input.addEventListener('input', () => {
      sizeDraft[input.dataset.width] = input.value;
      markChanged();
    });
  }
  for (const select of byId('rows').querySelectorAll('[data-kind]')) {
    select.addEventListener('change', () => {
      blankDraft[select.dataset.kind] = select.value === '1';
      // The load cell appears and disappears with the kind, so the table is
      // redrawn rather than patched: a stale input holding a number for a
      // plate is exactly the disagreement this page exists to avoid.
      drawRows();
      markChanged();
    });
  }
  byId('shown-count')?.replaceChildren(String(shown.length));
}

/**
 * What the typical row puts at this position.
 *
 * The table below the pattern has to answer with the pattern, because that is
 * what the case will build. It used to fall back to the standard cabinet the
 * moment a position stopped stating its own figure -- so `Make every row
 * exactly this` dropped the exceptions and then showed every position at the
 * standard, and the typical row only appeared after a save and a reload. Edit
 * the pattern and the positions follow it now, live (ADR-083).
 */
function fromPlan(rack) {
  return rowDraft()[(rack.position || 0) - 1] || {};
}

/** What this position carries right now: its own figure, else the pattern's. */
function current(rack) {
  const edited = draft[rack.id];
  if (edited !== undefined && edited !== '') return num(edited) ?? 0;
  if (edited === undefined && rack.stated) return rack.load_kw;
  const planned = num(fromPlan(rack).load_kw);
  return planned === null ? (num(byId('standard').value) ?? 0) : planned;
}

/** Whether this position is a plate rather than a cabinet. */
function isBlank(rack) {
  const edited = blankDraft[rack.id];
  if (edited !== undefined && edited !== null) return edited;
  if (edited === undefined && rack.blank_stated) return !!rack.blank;
  return !!fromPlan(rack).blank;
}

/** How wide it is: its own figure, else the pattern's, else the standard. */
function widthOf(rack) {
  const edited = sizeDraft[rack.id];
  if (edited !== undefined && edited !== '') return num(edited);
  if (edited === undefined && rack.sized) return rack.width_m;
  const planned = num(fromPlan(rack).width);
  return planned === null ? num(byId('size-w').value) : planned;
}

/**
 * Redraw what a figure changes, and nothing that holds a caret.
 *
 * The typical row's own inputs keep their value, their focus and the page its
 * scroll; what follows from them -- the metres of row, the positions table and
 * the totals in the aside -- is rewritten (ADR-083).
 */
function refresh() {
  const plan = rowDraft();
  const width = plan.reduce(
    (sum, e) => sum + (num(e.width) ?? state.standard.size[0]), 0);
  const tally = document.querySelector('#plan-rows')
    ?.closest('.card')?.querySelector('.rack-filter .card-sub');
  if (tally) {
    tally.textContent = `${plan.length} positions · ${fmt(width, 2)} m of row`;
  }
  drawRows();
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

  // --- the typical row ------------------------------------------------------
  //
  // A NUMBER DOES NOT REDRAW THE PAGE. It used to: every keystroke called
  // render(), which rewrites the whole layout, so the field being typed into
  // was destroyed and rebuilt under the caret and the page jumped back to the
  // top. Typing "13.2" meant four fights with the scrollbar. Only a change of
  // KIND changes which cells exist -- a plate has no load cell -- so only that
  // redraws; a figure updates the draft and the read-outs that depend on it
  // (ADR-083).
  for (const input of document.querySelectorAll('[data-plan]')) {
    const apply = () => {
      const entry = rowDraft()[Number(input.dataset.plan)];
      const field = input.dataset.field;
      if (field === 'blank') {
        if (input.value) { entry.blank = true; delete entry.load_kw; }
        else delete entry.blank;
        render();
      } else if (num(input.value) === null) {
        delete entry[field];
        refresh();
      } else {
        entry[field] = num(input.value);
        refresh();
      }
      markChanged();
    };
    input.addEventListener(input.tagName === 'SELECT' ? 'change' : 'input', apply);
  }
  for (const button of document.querySelectorAll('[data-plan-drop]')) {
    button.addEventListener('click', () => {
      rowDraft().splice(Number(button.dataset.planDrop), 1);
      render();
      markChanged();
    });
  }
  byId('add-cabinet')?.addEventListener('click', () => {
    rowDraft().push({});
    render();
    markChanged();
  });
  byId('add-blank')?.addEventListener('click', () => {
    rowDraft().push({ blank: true, width: state.standard.size[0] });
    render();
    markChanged();
  });
  byId('apply-row')?.addEventListener('click', () => {
    // Every row exactly the typical one: the exceptions go, and the pattern
    // is the only thing left saying what a position is. The table below shows
    // it immediately, because `current`, `widthOf` and `isBlank` fall back to
    // the pattern rather than to the standard cabinet -- it used to take a
    // save and a reload to see what the button had done (ADR-083).
    for (const rack of state.racks) {
      draft[rack.id] = '';
      sizeDraft[rack.id] = '';
      blankDraft[rack.id] = null;
    }
    render();
    byId('status').textContent = 'every row will follow the typical one — not saved';
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
  const widths = {};
  const blanks = [];
  for (const rack of state.racks) {
    // An UNTOUCHED position is sent as null -- "whatever the typical row says"
    // -- unless the case stated it as an exception in the first place. Sending
    // the figure the server last computed froze the OLD pattern into every
    // position as an explicit exception, so editing the typical row changed
    // nothing for any position that was not already at the standard, and the
    // page had no way to say so (ADR-083).
    const value = draft[rack.id];
    loads[rack.id] = value !== undefined
      ? (value === '' ? null : value)
      : (rack.stated ? rack.load_kw : null);
    const width = sizeDraft[rack.id];
    widths[rack.id] = width !== undefined
      ? (width === '' ? null : width)
      : (rack.sized ? rack.width_m : null);
    // `null` in the draft is "back to whatever the typical row says", which is
    // how `Make every row exactly this` clears an exception.
    const kind = blankDraft[rack.id];
    if (kind === true || (kind === undefined && rack.blank_stated)) blanks.push(rack.id);
  }
  try {
    const res = await fetch('/api/racks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        load_kw: num(byId('standard').value),
        size: [num(byId('size-w').value), num(byId('size-d').value),
               num(byId('size-h').value)],
        row: rowDraft(),
        loads,
        widths,
        blanks,
      }),
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
