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

import { num } from './decimal.js';
// Keeps the case on the way back, so `back` returns to the case the reader came here from
// rather than to the one the server was started with (ADR-091).
import './case.js';

const byId = document.getElementById.bind(document);
let state = null;

/** Free area is typed as a percentage; the library holds the ratio. */
const toPercent = (v) => (v === null || v === undefined ? '' : +(v * 100).toFixed(1));
const fromPercent = (v) => (num(v) === null ? null : num(v) / 100);
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
  const tags = (locked ? '<span class="tag">fixed</span>' : '')
    + (c.applied ? '' : '<span class="tag pending">not in the solver yet</span>');
  return `
    <div class="role ${locked ? 'locked' : ''}" data-role-for="${c.id}">
      <div class="role-head">
        ${swatch(c)}
        <div>
          <h3>${c.name} ${tags}</h3>
          <p>${c.aperture || (c.kind === 'load' ? 'a share of the IT load' : '&nbsp;')}</p>
        </div>
      </div>
      ${c.kind === 'load'
        ? box('share', 'Share of the IT load %', toPercent(c.share),
              'type="text" inputmode="decimal"')
        : box('free_area', 'Free area %', toPercent(c.free_area),
              'type="text" inputmode="decimal"')}
      ${c.kind === 'surface' ? `<div class="field">
        <label>Loss coefficient K, on the face velocity</label>
        <input value="${fmt(c.k, 2)}" readonly
               id="${c.id}-k" style="opacity:.8" />
      </div>` : ''}
      ${c.loss_coefficient !== null && c.loss_coefficient !== undefined
        ? box('loss_coefficient', 'K from the datasheet, which wins',
              c.loss_coefficient, 'type="text" inputmode="decimal"')
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
      ${c.applied ? '' : `<p class="note pending-note">Held here and not yet read
           by the solver. The number is the specification's; what is missing is
           the modelling that releases it, so a run today answers as though this
           were ${c.kind === 'load' ? 'zero' : 'sealed'}.</p>`}
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

/**
 * The aperture, drawn from the component's own numbers.
 *
 * Not a photograph of a product. The open fraction of what is drawn IS the
 * free area the component states, so the swatch answers the question the page
 * is about -- how much of this surface is actually open -- rather than showing
 * what the thing looks like in a catalogue. It also means the picture cannot
 * disagree with the number beside it (ADR-049).
 */
function swatch(c) {
  // A share of the IT load has no aperture. Drawing an empty frame for it
  // would say there is a surface here with nothing in it, which is not what
  // a dissipation is.
  if (c.kind !== 'surface') return '';
  const open = c.free_area ?? 1;
  const W = 132;
  const H = 52;
  const ink = 'var(--text-secondary)';
  const gap = 'var(--surface)';
  let body = '';

  if (c.pattern === 'woven') {
    // A square weave: the aperture and the wire are on the datasheet, so the
    // pitch is drawn to scale and the open fraction falls out of it.
    const pitch = 12;
    const wire = pitch * (1 - Math.sqrt(open));
    for (let x = 0; x <= W; x += pitch) {
      body += `<rect x="${x}" y="0" width="${wire}" height="${H}" fill="${ink}"/>`;
    }
    for (let y = 0; y <= H; y += pitch) {
      body += `<rect x="0" y="${y}" width="${W}" height="${wire}" fill="${ink}"/>`;
    }
  } else if (c.pattern === 'eggcrate') {
    const pitch = 13;
    const bar = pitch * (1 - Math.sqrt(open));
    for (let x = 0; x <= W; x += pitch) {
      body += `<rect x="${x}" y="0" width="${Math.max(bar, 0.8)}" height="${H}" fill="${ink}"/>`;
    }
    for (let y = 0; y <= H; y += pitch) {
      body += `<rect x="0" y="${y}" width="${W}" height="${Math.max(bar, 0.8)}" fill="${ink}"/>`;
    }
  } else if (c.pattern === 'perforated') {
    // Round holes on a staggered pitch: area = pi r^2 per cell.
    const pitch = 11;
    const r = Math.sqrt((open * pitch * pitch) / Math.PI);
    body += `<rect width="${W}" height="${H}" fill="${ink}"/>`;
    for (let row = 0, y = pitch / 2; y < H; y += pitch, row += 1) {
      for (let x = (row % 2 ? pitch : pitch / 2); x < W; x += pitch) {
        body += `<circle cx="${x}" cy="${y}" r="${r}" fill="${gap}"/>`;
      }
    }
  } else if (c.pattern === 'slotted') {
    // A floor plate: slots between the folded column bases, and the damper
    // bar under them that sets how much of each slot is open.
    const pitch = 15;
    const slot = pitch * open;
    body += `<rect width="${W}" height="${H}" fill="${ink}"/>`;
    for (let x = 3; x < W - 3; x += pitch) {
      body += `<rect x="${x}" y="5" width="${slot}" height="${H - 10}" rx="1.5" fill="${gap}"/>`;
    }
    body += `<rect x="0" y="${H - 7}" width="${W}" height="3" fill="${gap}" opacity=".55"/>`;
  } else if (c.pattern === 'joint') {
    // Containment leakage is not a designed opening: it is the line where a
    // panel meets a rack, a door seal, a cable entry. Drawn as those.
    body += `<rect width="${W}" height="${H}" fill="${ink}" opacity=".85"/>`;
    const seam = ['M0,26 H132', 'M44,0 V52', 'M88,0 V52'];
    for (const d of seam) {
      body += `<path d="${d}" stroke="${gap}" stroke-width="1.6" stroke-dasharray="7 5" fill="none"/>`;
    }
    body += `<rect x="60" y="34" width="13" height="7" rx="1.5" fill="${gap}"/>`;
  } else {
    body += `<rect width="${W}" height="${H}" fill="none" stroke="${ink}"
               stroke-width="1.4" stroke-dasharray="5 4"/>`;
  }
  return `<svg class="swatch" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}"
            role="img" aria-label="${c.aperture || c.name}">
            <rect width="${W}" height="${H}" fill="${gap}"/>${body}</svg>`;
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
    const box = byId(`${c.id}-free_area`);
    if (!box) return; // a load has no face for air to cross
    const free = fromPercent(box.value);
    const stated = byId(`${c.id}-loss_coefficient`);
    const shown = stated && num(stated.value) !== null ? num(stated.value)
      : free ? k(free) : null;
    byId(`${c.id}-k`).value = fmt(shown, 2);
  };
  for (const input of inputs) input.addEventListener('input', live);

  const status = document.querySelector(`[data-status="${c.id}"]`);
  document.querySelector(`[data-save="${c.id}"]`).addEventListener('click', async () => {
    const body = {};
    for (const input of inputs) {
      const value = input.value.trim();
      body[input.dataset.key] = ['free_area', 'share'].includes(input.dataset.key)
        ? fromPercent(value)
        : input.dataset.key === 'loss_coefficient'
          ? num(value)
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
