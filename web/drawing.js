/**
 * Orthographic views of the model: two sections and a plan, to scale.
 *
 * Everything is drawn from the model payload, which is the geometry the mesher
 * builds -- snapping to the cell grid included. A drawing that is merely
 * *about* the model would defeat the point of looking at it.
 *
 * Drawing convention, the usual one: solid where the section plane cuts, thin
 * and dashed for everything that lies off the plane. So in the plan, taken at
 * rack height, the ceiling grilles are dashed because they are overhead, and
 * in the transverse section the fan wall is dashed because it is metres away
 * along x.
 */

const NS = 'http://www.w3.org/2000/svg';
const PAD = { left: 88, right: 30, top: 16, bottom: 44 };

export const VIEWS = [
  {
    id: 'section-a',
    title: 'A · Corte transversal',
    subtitle: 'pelo meio da fila de racks',
    h: 1,
    v: 2,
    normal: 0,
    hLabel: 'y (m) — largura do data hall',
    height: 420,
  },
  {
    id: 'section-b',
    title: 'B · Corte longitudinal',
    subtitle: 'pelo corredor quente',
    h: 0,
    v: 2,
    normal: 1,
    hLabel: 'x (m) — galeria mecânica + data hall',
    height: 420,
  },
  {
    id: 'plan',
    title: 'C · Planta',
    subtitle: 'ao nível dos racks; grelhas do forro tracejadas (estão acima)',
    h: 0,
    v: 1,
    normal: 2,
    hLabel: 'x (m)',
    height: 320,
  },
];

/** Panels worth naming, seen face-on -- there is room for a full caption. */
const PANEL_LABEL = {
  fan: 'fan wall',
  plenum_opening: 'abertura p/ galeria',
};

/** The same panels seen edge-on, where the caption has to fit on a line. */
const EDGE_LABEL = {
  fan: 'fan wall',
  plenum_opening: 'retorno',
};

/** Colour class for a panel, by what it is. */
const klass = (panel) =>
  panel.kind === 'wall' ? 'dw-wall' : panel.kind === 'fan' ? 'dw-fan' : 'dw-opening';

function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

/** The plane each section is taken on, chosen to cut the informative thing. */
function sectionAt(model, view) {
  if (view.normal === 0) {
    const [lo, hi] = rackSpan(model);
    return (lo + hi) / 2; // through the middle of the row
  }
  if (view.normal === 1) {
    return (model.aisles.hot[0] + model.aisles.hot[1]) / 2; // the hot aisle
  }
  return model.racks.length ? model.racks[0].hi[2] / 2 : 1.0; // rack mid-height
}

function rackSpan(model) {
  if (!model.racks.length) return [0, 0];
  return [
    Math.min(...model.racks.map((r) => r.lo[0])),
    Math.max(...model.racks.map((r) => r.hi[0])),
  ];
}

const straddles = (lo, hi, axis, at) =>
  lo[axis] <= at + 1e-9 && hi[axis] >= at - 1e-9;

function panelBounds(panel) {
  const lo = [0, 0, 0];
  const hi = [0, 0, 0];
  lo[panel.axis] = hi[panel.axis] = panel.position;
  const inPlane = [0, 1, 2].filter((a) => a !== panel.axis);
  inPlane.forEach((axis, i) => {
    lo[axis] = panel.extent[i][0];
    hi[axis] = panel.extent[i][1];
  });
  return { lo, hi };
}

/**
 * One scale for all three views.
 *
 * Sections drawn at different scales invite exactly the mistake these
 * drawings are meant to catch: a height compared across two views that do not
 * share a ruler. So the sheet picks the largest scale every view can live
 * with, and each view's canvas is then sized to its own geometry.
 */
export function sheetScale(model, views, maxWidths) {
  const d = model.domain;
  let scale = Infinity;
  views.forEach((view, i) => {
    const hSpan = d.hi[view.h] - d.lo[view.h];
    const vSpan = d.hi[view.v] - d.lo[view.v];
    const plotW = Math.max(maxWidths[i] - PAD.left - PAD.right, 80);
    const plotH = view.height - PAD.top - PAD.bottom;
    scale = Math.min(scale, plotW / hSpan, plotH / vSpan);
  });
  return Math.max(scale, 8);
}

/** Canvas a view needs at the sheet scale. */
export function viewSize(model, view, scale) {
  const d = model.domain;
  return {
    width: (d.hi[view.h] - d.lo[view.h]) * scale + PAD.left + PAD.right,
    height: (d.hi[view.v] - d.lo[view.v]) * scale + PAD.top + PAD.bottom,
  };
}

export function drawView(model, view, scale) {
  const { width, height } = viewSize(model, view, scale);
  const svg = el('svg', {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': view.title,
  });

  const d = model.domain;
  const hSpan = d.hi[view.h] - d.lo[view.h];
  const ox = PAD.left;
  const oy = PAD.top;

  const X = (v) => ox + (v - d.lo[view.h]) * scale;
  const Y = (v) => oy + (d.hi[view.v] - v) * scale;
  const at = sectionAt(model, view);

  const paint = (lo, hi, cls, label) => {
    const x0 = X(Math.min(lo[view.h], hi[view.h]));
    const x1 = X(Math.max(lo[view.h], hi[view.h]));
    const y0 = Y(Math.max(lo[view.v], hi[view.v]));
    const y1 = Y(Math.min(lo[view.v], hi[view.v]));
    svg.append(
      el('rect', {
        x: x0,
        y: y0,
        width: Math.max(x1 - x0, 1.6),
        height: Math.max(y1 - y0, 1.6),
        class: cls,
      }),
    );
    if (label && x1 - x0 > 26 && y1 - y0 > 13) {
      svg.append(
        el('text', { x: (x0 + x1) / 2, y: (y0 + y1) / 2 + 3.5, class: 'dw-label' }, label),
      );
    }
    return { x0, y0, x1, y1 };
  };

  const line = (x1, y1, x2, y2, cls) =>
    svg.append(el('line', { x1, y1, x2, y2, class: cls }));

  // 1 — volumes. The transverse section looks along x, so gallery and hall
  // project onto the same rectangle; only the one the plane actually passes
  // through belongs on that drawing.
  for (const [box, cls] of [
    [model.gallery, 'dw-gallery'],
    [model.hall, 'dw-hallfill'],
  ]) {
    if (view.normal === 0 && !straddles(box.lo, box.hi, 0, at)) continue;
    paint(box.lo, box.hi, cls);
  }

  // 2 — aisle tints, only where the y axis is on screen, and only in the hall
  if (view.h === 1 || view.v === 1) {
    for (const [band, cls] of [
      [model.aisles.cold, 'dw-cold'],
      [model.aisles.hot, 'dw-hot'],
    ]) {
      const lo = [model.hall.lo[0], band[0], model.domain.lo[2]];
      const hi = [
        model.hall.hi[0],
        band[1],
        view.v === 2 ? model.ceiling_z : model.domain.hi[2],
      ];
      paint(lo, hi, cls);
    }
  }

  // 3 — return plenum, above the false ceiling and inside the hall only
  if (view.v === 2) {
    paint(
      [model.hall.lo[0], model.domain.lo[1], model.ceiling_z],
      [model.hall.hi[0], model.domain.hi[1], model.domain.hi[2]],
      'dw-plenum',
    );
  }

  // 4 — panels the section sees face-on: we look straight at the rectangle.
  //
  // One rule, no exceptions: cut by this plane -> solid; anywhere else ->
  // dashed outline. Filling a panel the section misses reads as though the
  // section went through it, which is the one thing these drawings exist to
  // make unambiguous.
  for (const panel of model.panels) {
    if (panel.name === 'ceiling') continue; // drawn as the heavy line below
    if (panel.axis !== view.normal) continue;
    const { lo, hi } = panelBounds(panel);
    const cut = Math.abs(panel.position - at) < 1e-6;
    paint(lo, hi, cut ? klass(panel) : `${klass(panel)} dw-beyond`,
      cut ? null : PANEL_LABEL[panel.name]);
  }

  // 5 — racks
  for (const rack of model.racks) {
    const cut = straddles(rack.lo, rack.hi, view.normal, at);
    paint(rack.lo, rack.hi, cut ? 'dw-rack' : 'dw-rack dw-behind', rack.id);
  }

  // 6 — the building's own lines: false ceiling and the gallery/hall wall
  if (view.v === 2) {
    line(
      X(view.h === 0 ? model.hall.lo[0] : d.lo[1]),
      Y(model.ceiling_z),
      X(view.h === 0 ? model.hall.hi[0] : d.hi[1]),
      Y(model.ceiling_z),
      'dw-ceiling',
    );
  }
  if (view.h === 0) {
    line(X(model.hall.lo[0]), Y(d.hi[view.v]),
      X(model.hall.lo[0]), Y(d.lo[view.v]), 'dw-divider');
  }

  // 7 — panels the section sees edge-on. These project to a line, so a
  // rectangle would collapse to a hairline and disappear under the wall it
  // sits in -- which is exactly where the fan wall and the plenum opening
  // live. Drawn last, as heavy strokes, so an opening reads as a gap punched
  // through the wall line underneath it.
  for (const panel of model.panels) {
    if (panel.name === 'ceiling' || panel.axis === view.normal) continue;
    const { lo, hi } = panelBounds(panel);
    const along = panel.axis === view.h ? view.v : view.h;
    const cut = straddles(lo, hi, view.normal, at);
    const cls = `${klass(panel)} dw-edge${cut ? '' : ' dw-edge-beyond'}`;
    const a = panel.axis === view.h
      ? [X(panel.position), Y(lo[along]), X(panel.position), Y(hi[along])]
      : [X(lo[along]), Y(panel.position), X(hi[along]), Y(panel.position)];
    line(a[0], a[1], a[2], a[3], cls);

    // Only name what this section actually cuts, and only when the caption
    // fits along the line: a label spilling past its own opening is worse
    // than no label at all.
    const label = cut ? EDGE_LABEL[panel.name] : null;
    const long = Math.abs(a[2] - a[0]) + Math.abs(a[3] - a[1]);
    if (label && long > label.length * 6) {
      const tx = (a[0] + a[2]) / 2;
      const ty = (a[1] + a[3]) / 2;
      // A vertical opening gets a label turned to run along it, so the text
      // stays beside the line instead of straddling the wall it pierces.
      svg.append(
        el('text', {
          x: tx,
          y: ty,
          dy: -5,
          class: `dw-edge-label ${klass(panel)}`,
          ...(panel.axis === view.h ? { transform: `rotate(-90 ${tx} ${ty})` } : {}),
        }, label),
      );
    }
  }

  // 8 — annotation
  const bounds = { left: PAD.left - 26, right: width - 6 };
  annotate(svg, model, view, X, Y, bounds);
  dimensions(svg, model, view, X, Y, width, height, hSpan);
  return svg;
}

function annotate(svg, model, view, X, Y, bounds) {
  // Anchored at the centre, so keep half the string inside the drawing.
  const put = (hx, vy, label, cls = 'dw-note') => {
    const half = label.length * 2.9;
    const x = Math.min(
      Math.max(X(hx), bounds.left + half),
      bounds.right - half,
    );
    svg.append(el('text', { x, y: Y(vy), class: cls }, label));
  };
  const mid = (a) => (a[0] + a[1]) / 2;

  if (view.id === 'section-a') {
    put(mid(model.aisles.cold), 0.45, 'corredor frio', 'dw-note dw-cold-t');
    put(mid(model.aisles.hot), model.ceiling_z - 1.1, 'chaminé', 'dw-note dw-hot-t');
  }
  if (view.id === 'section-b') {
    put(model.gallery.hi[0] / 2, model.domain.hi[2] - 0.55, 'galeria mecânica');
    put(mid([model.hall.lo[0], model.hall.hi[0]]), model.ceiling_z + 0.6,
      'plenum de retorno', 'dw-note dw-hot-t');
  }
  if (view.id === 'plan') {
    put(model.gallery.hi[0] / 2, model.domain.hi[1] - 0.35, 'galeria');
    put(mid([model.hall.lo[0], model.hall.hi[0]]), mid(model.aisles.cold),
      'corredor frio', 'dw-note dw-cold-t');
  }
}

function dimensions(svg, model, view, X, Y, width, height, hSpan) {
  const d = model.domain;
  const y = Y(d.lo[view.v]) + 20;
  const x0 = X(d.lo[view.h]);
  const x1 = X(d.hi[view.h]);
  svg.append(el('line', { x1: x0, x2: x1, y1: y, y2: y, class: 'dw-dim' }));
  for (const x of [x0, x1]) {
    svg.append(el('line', { x1: x, x2: x, y1: y - 3, y2: y + 3, class: 'dw-dim' }));
  }
  svg.append(
    el('text', { x: (x0 + x1) / 2, y: y - 5, class: 'dw-dimtext' }, fmt(hSpan)),
  );
  svg.append(
    el('text', { x: (x0 + x1) / 2, y: height - 6, class: 'dw-axis' }, view.hLabel),
  );

  // Levels, in the left margin, each with a leader out to the geometry so the
  // label cannot be mistaken for the one above or below it.
  const levels =
    view.v === 2
      ? [
          [d.hi[2], 'laje'],
          [model.ceiling_z, 'forro'],
          ...(model.racks.length ? [[model.racks[0].hi[2], 'topo rack']] : []),
        ]
      : [
          [model.aisles.cold[1], 'frente'],
          [model.aisles.racks[1], 'costas'],
        ];
  for (const [value, label] of levels) {
    const ly = Y(value);
    svg.append(
      el('line', { x1: x0 - 22, x2: x0, y1: ly, y2: ly, class: 'dw-dim dw-leader' }),
    );
    svg.append(
      el('text', { x: x0 - 26, y: ly - 2, class: 'dw-level' }, label),
    );
    svg.append(
      el('text', { x: x0 - 26, y: ly + 8, class: 'dw-level dw-level-v' }, fmt(value)),
    );
  }
}

const fmt = (v) => v.toFixed(2).replace('.', ',');
