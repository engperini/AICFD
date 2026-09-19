/**
 * The components page: the surfaces the air has to get through.
 *
 * A data hall's air passes perforated surfaces on its way round the loop --
 * the grilles in the false ceiling, the woven mesh closing the plenum where it
 * opens into a mechanical gallery, the plates of a raised floor, and the
 * leakage a containment has -- and each costs pressure. One number decides
 * that cost: the open area over the gross area. Everything here follows from
 * it (ADR-048).
 *
 * Edited here rather than in a case file because these are standards. A house
 * specification fixes them once and every hall built to it uses the same
 * numbers, so saving changes every element of that kind in every hall at once,
 * which is what a specification is for. A case names a component; the
 * component says what it is.
 *
 * A component marked fixed is the building rather than a choice -- the mesh
 * closing the plenum is the same opening closed the same way in every hall --
 * so it is shown and never edited.
 */

const byId = document.getElementById.bind(document);
let state = null;

/** Free area is typed as a percentage; the library holds the ratio. */
const toPercent = (v) => (v === null || v === undefined ? '' : +(v * 100).toFixed(1));
const fromPercent = (v) => (v === '' ? null : Number(v) / 100);
const fmt = (v, digits = 2) =>
  v === null || v === undefined || !Number.isFinite(+v) ? '—' : (+v).toFixed(digits);

async function load() {
  const res = await fetch('/api/components');
  if (!res.ok) throw new Error(await res.text());
  state = await res.json();
  render();
}

function render() {
  const groups = state.roles.map((role) => ({
    ...role,
    items: state.components.filter((c) => c.role === role.role),
  }));
  byId('root').innerHTML = `
    <div class="layout">
      <div class="column">
        ${groups.filter((g) => g.items.length).map(card).join('')}
      </div>
      <div class="column">${aside()}</div>
    </div>`;
  for (const component of state.components) wire(component);
}

function card(group) {
  return `
    <section class="card">
      <div class="card-head">
        <span class="card-title">${group.label}</span>
        <span class="card-sub">${group.items.length === 1 ? 'one standard'
          : `${group.items.length} to choose between`}</span>
      </div>
      ${group.items.map(component).join('')}
    </section>`;
}

/**
 * One component. The free area is the input; the loss coefficient beside it is
 * what the solver will use, shown live so the consequence of a change is
 * visible before it is saved rather than after a solve.
 */
function component(c) {
  const locked = c.fixed;
  const box = (key, label, value, attrs = '') => `
    <div class="field">
      <label for="${c.id}-${key}">${label}</label>
      <input id="${c.id}-${key}" data-component="${c.id}" data-key="${key}"
             value="${value ?? ''}" ${attrs} ${locked ? 'readonly' : ''} />
    </div>`;
  return `
    <div class="role ${locked ? 'locked' : ''}" data-role-for="${c.id}">
      <h3>${c.name} ${locked ? '<span class="tag">fixed</span>' : ''}</h3>
      <p>${c.aperture || '&nbsp;'}</p>
      ${box('free_area', 'Free area %', toPercent(c.free_area),
            'type="number" step="0.1" min="0.1" max="100"')}
      <div class="field">
        <label>Loss coefficient K, on the face velocity</label>
        <input value="${fmt(c.k, 2)}" readonly
               id="${c.id}-k" style="opacity:.8" />
      </div>
      ${c.loss_coefficient !== null
        ? box('loss_coefficient', 'K from the datasheet, which wins',
              c.loss_coefficient, 'type="number" step="0.01" min="0"')
        : ''}
      ${c.size ? `<div class="field"><label>Face of one piece</label>
        <input value="${c.size[0]} × ${c.size[1]} m" readonly style="opacity:.8" /></div>` : ''}
      ${c.adjustable ? `<div class="field"><label>Damper range on site</label>
        <input value="${toPercent(c.adjustable[0])}–${toPercent(c.adjustable[1])} %"
               readonly style="opacity:.8" /></div>` : ''}
      <div class="field wide">
        <label for="${c.id}-note">What it is, and where</label>
        <textarea id="${c.id}-note" data-component="${c.id}" data-key="note"
                  ${locked ? 'readonly' : ''}>${c.note || ''}</textarea>
      </div>
      ${locked
        ? `<p class="note">The building rather than a choice: the same opening,
             closed the same way, in every hall built to this specification.
             It is here to be read and to be costed, and it is not edited.</p>`
        : `<div class="actions">
             <button class="primary" type="button" data-save="${c.id}">Save</button>
             <button type="button" data-undo="${c.id}">Undo</button>
             <span class="card-sub" data-status="${c.id}"></span>
           </div>`}
    </div>`;
}

function aside() {
  return `
    <section class="card">
      <div class="card-head"><span class="card-title">What a free area costs</span></div>
      <p class="prose">
        The loss coefficient beside each component is the thin-plate
        correlation on its open area, referred to the face velocity over the
        gross area — the same quantity a grille catalogue's pressure-against-velocity
        table is fitted to. Where a datasheet gives K directly it wins, because
        a measurement beats a correlation.
      </p>
      <table>
        <thead><tr><th>Free area</th><th>K</th></tr></thead>
        <tbody>${[0.8, 0.65, 0.54, 0.35, 0.05]
          .map((s) => `<tr><td>${toPercent(s)} %</td><td>${fmt(k(s), 2)}</td></tr>`)
          .join('')}</tbody>
      </table>
      <p class="note">
        The cost rises far faster than the opening closes: halving the free
        area from 65 % to 32 % does not double the pressure, it multiplies it
        by about seven.
      </p>
    </section>
    <section class="card">
      <div class="card-head"><span class="card-title">Where these are used</span></div>
      <p class="prose">
        Saving a component changes every element of that kind, in every case
        that names it. That is what a house specification is: the surfaces are
        standards, and a case that quietly used others would produce a fan duty
        nobody could reproduce.
      </p>
    </section>`;
}

/** The thin-plate correlation, mirrored from `model.grille_loss_coefficient`. */
function k(freeArea) {
  const s = Math.min(Math.max(freeArea, 0.05), 1);
  return (0.707 * (1 - s) ** 0.375 + (1 - s)) ** 2 / s ** 2;
}

function wire(c) {
  if (c.fixed) return;
  const inputs = [...document.querySelectorAll(`[data-component="${c.id}"]`)];
  const live = () => {
    const free = fromPercent(byId(`${c.id}-free_area`).value);
    const stated = byId(`${c.id}-loss_coefficient`);
    const shown = stated && stated.value !== '' ? Number(stated.value)
      : free ? k(free) : null;
    byId(`${c.id}-k`).value = fmt(shown, 2);
  };
  for (const input of inputs) input.addEventListener('input', live);

  const status = document.querySelector(`[data-status="${c.id}"]`);
  document.querySelector(`[data-save="${c.id}"]`).addEventListener('click', async () => {
    const body = {};
    for (const input of inputs) {
      const value = input.value.trim();
      body[input.dataset.key] = input.dataset.key === 'free_area'
        ? fromPercent(value)
        : input.dataset.key === 'loss_coefficient'
          ? (value === '' ? null : Number(value))
          : value;
    }
    status.textContent = 'saving…';
    try {
      const res = await fetch(`/api/components?id=${encodeURIComponent(c.id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.text()).slice(0, 300));
      await load();
      const fresh = document.querySelector(`[data-status="${c.id}"]`);
      if (fresh) fresh.textContent = 'saved';
    } catch (error) {
      status.textContent = String(error.message || error);
    }
  });
  document.querySelector(`[data-undo="${c.id}"]`).addEventListener('click', () => load());
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
