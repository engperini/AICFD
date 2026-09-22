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

//: What the plan needs below and beside the geometry for its band chains.
//: Per view, so adding them cannot move a section the engineer did not ask to
//: have changed.
const PLAN_PAD = { bottom: 84, left: 130 };
const padFor = (view) => (view.id === 'plan'
  ? { ...PAD, ...PLAN_PAD } : PAD);

/**
 * The tallest a drawing may be, in px.
 *
 * The three views share one scale, so that a metre is the same length in all
 * of them and a reader can compare across drawings. `sheetScale` takes the
 * smallest scale that fits every view, which means the most constrained view
 * sets the size of all of them -- and while these were fixed at 320 and 420 px
 * the constraint was always the HEIGHT: a 51 x 32 m hall had to fit its plan
 * into 320 px, so everything was drawn at 10 px/m and the sections came out
 * 74 px tall.
 *
 * An absolute ceiling, reached only on a very tall window; `heightCap` is
 * what normally applies.
 */
const MAX_VIEW_HEIGHT = 980;

/**
 * How tall a fitted drawing may actually be here: as tall as its scroller.
 *
 * A fixed cap serves two rooms badly at once. It bound a 51 x 32 m hall,
 * whose plan it held to 19 px per metre when the page had room for 20,5;
 * and it bound a 9 x 4,2 x 8 m POD far harder, where the sections are nearly
 * square and the window had height to spare. Taking it from the window fits
 * both: each drawing is as large as the screen in front of the reader allows.
 *
 * 80% of the window is what `drawing.css` gives the scroller. Going past it
 * would leave a drawing labelled `fit` that still had to be scrolled, and the
 * one thing that label must not do is lie.
 */
function heightCap() {
  if (typeof window === 'undefined') return MAX_VIEW_HEIGHT;
  const scroller = Math.round(window.innerHeight * 0.8) - 8; // spare for rounding
  return Math.max(360, Math.min(MAX_VIEW_HEIGHT, scroller));
}

export const VIEWS = [
  {
    id: 'section-a',
    title: 'A · Transverse section',
    subtitle: 'through the middle of the rack row',
    h: 1,
    v: 2,
    normal: 0,
    hLabel: 'y (m) — hall width',
    height: MAX_VIEW_HEIGHT,
  },
  {
    id: 'section-b',
    title: 'B · Longitudinal section',
    subtitle: 'through the hot aisle',
    h: 0,
    v: 2,
    normal: 1,
    hLabel: 'x (m) — mechanical gallery + hall',
    height: MAX_VIEW_HEIGHT,
  },
  {
    id: 'plan',
    title: 'C · Plan',
    subtitle: 'at rack height; ceiling grilles dashed, they are overhead',
    h: 0,
    v: 1,
    normal: 2,
    hLabel: 'x (m)',
    height: MAX_VIEW_HEIGHT,
  },
];

/**
 * The views for a given model. A hall is many times longer across (y) than
 * along the rows (x), so its plan is turned to run across the page rather
 * than down it. Every view takes a full row either way, so the shared fitted
 * scale is set by the page width and not by the narrowest neighbour.
 */
export function viewsFor(model) {
  const d = model.domain;
  const long = isLong(model);
  return VIEWS.map((view) => {
    if (view.id !== 'plan' || !long) return { ...view, long };
    return {
      ...view,
      h: 1,
      v: 0,
      hLabel: 'y (m) — hall width',
      height: MAX_VIEW_HEIGHT,
      long,
    };
  });
}

export function isLong(model) {
  const d = model.domain;
  return d.hi[1] - d.lo[1] > 1.5 * (d.hi[0] - d.lo[0]);
}

/** Panels worth naming, seen face-on -- there is room for a full caption. */
const PANEL_LABEL = {
  plenum_opening: 'opening to gallery',
  plenum_opening2: 'opening to gallery',
};

/** The same panels seen edge-on, where the caption has to fit on a line. */
const EDGE_LABEL = {
  plenum_opening: 'return',
  plenum_opening2: 'return',
  floor_opening: 'supply',
  floor_opening2: 'supply',
};

const isSupply = (panel) => /^supply\d/.test(panel.name);
const isPlenumWall = (panel) => /^plenum_wall/.test(panel.name);
// A raised floor's pieces: the perforated plates in the cold aisle, the deck
// itself, and the mesh in the dividing wall below it (ADR-076).
const isTile = (panel) => /^tile_/.test(panel.name);
const isDeck = (panel) => panel.name === 'floor_deck';
// The customer cage. Drawn as neither containment nor a grille: it is a
// SECURITY boundary, and a reader looking for it on the plan has to find it
// (ADR-096, ADR-102).
const isCage = (panel) => /^cage_/.test(panel.name);

/**
 * What THIS room's machines are called: a wall of fans in a gallery is a fan
 * wall, a unit standing in the room is a CRAC when it makes its own cold and a
 * CRAH when it is fed chilled water.
 *
 * The model decides it and carries it in the payload, so the page, the report
 * and the prose all say the same word. A drawing that labelled five
 * direct-expansion room units `fan wall` was wrong in the one place a reader
 * checks the model against the room they know (ADR-102). An export written
 * before the key existed falls back to the same rule the model applies.
 */
export const unitNaming = (model) =>
  model?.unit_naming
  ?? (model?.floor_height
    ? { noun: 'room unit', plural: 'room units', tag: 'AC' }
    : { noun: 'fan wall', plural: 'fan walls', tag: 'FW' });

/** A machine is named once per drawing: seventeen captions reading "CRAC" say
 * less than one, and the rest are the same blue rectangle in the same wall. */
const fanLabel = (panel, seen, model) => {
  if (isSupply(panel)) {
    if (seen.supply) return null;
    seen.supply = true;
    return 'supply grille';
  }
  if (panel.kind !== 'fan' || seen.fan) return null;
  seen.fan = true;
  return unitNaming(model).noun;
};

/**
 * Does this face belong to the rack row rather than to the room?
 *
 * The rack's own lid and ends are real surfaces -- they stop the cold aisle
 * entering the porous zone sideways -- but they lie exactly on the rack box,
 * which these drawings already show. Drawn again as walls they take the
 * containment colour and put a wall where the room has a rack (ADR-045).
 *
 * The model says so. An export written before it said so does not, and the
 * results page draws from whatever export it was handed, so the name answers
 * for those and only those: `??` keeps an explicit `false` explicit.
 */
const ofRack = (panel) => panel.of_rack ?? /^rack_(top|end)(_|$)/.test(panel.name);

/**
 * The x ranges a rack row actually occupies: one per block, because a row cut
 * by a transverse aisle is two rows as far as the air is concerned. Falls
 * back to the racks' own extent for a case that names no blocks.
 */
const rackBlocks = (model) =>
  model.blocks?.length ? model.blocks : [rackSpan(model)];

const coldAisles = (model) => model.cold_aisles || [model.aisles.cold];
const hotAisles = (model) => model.hot_aisles || [model.aisles.hot];

/** Colour class for a panel, by what it is. */
const klass = (panel, model) =>
  // The plenum's inner leaf is the building, not containment. Every `wall`
  // is drawn in the containment green, and a green line across the end of
  // the hall says that end is contained, which it is not (ADR-058, and the
  // same misreading ADR-045 answered over the racks).
  //
  // A supply grille is an `opening` and is drawn like every other grille.
  // Giving it a colour of its own -- the cold blue of the air through it --
  // made it a new thing to learn on a drawing where red already means "a
  // grille is here".
  // Mesh and drywall are the same rectangle and different rooms, so the line
  // says which: dashed for what the air crosses, solid for what it cannot
  // (ADR-096, ADR-102). The model carries the word.
  isCage(panel) ? `dw-cage${model?.cage === 'drywall' ? '' : ' dw-cage-mesh'}`
    : isPlenumWall(panel) ? 'dw-partition'
      // The deck is the floor, not a wall to look at: drawn faint so the room
      // on top of it stays the thing being read.
      : isDeck(panel) ? 'dw-deck'
        : panel.kind === 'wall' ? 'dw-wall'
          : panel.kind === 'fan' ? 'dw-fan' : 'dw-opening';

function el(tag, attrs = {}, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

/** The plane each section is taken on, chosen to cut the informative thing. */
function sectionAt(model, view) {
  if (view.normal === 0) {
    // Through the middle of a rack block -- not of the whole row, which on a
    // hall with two blocks is the transverse aisle between them.
    const blocks = model.blocks?.length ? model.blocks : [rackSpan(model)];
    const [lo, hi] = blocks[Math.floor(blocks.length / 2)];
    return (lo + hi) / 2;
  }
  if (view.normal === 1) {
    const hot = hotAisles(model);
    const aisle = hot[Math.floor(hot.length / 2)]; // a hot aisle, the middle one
    return (aisle[0] + aisle[1]) / 2;
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
    const plotW = Math.max(maxWidths[i] - padFor(view).left - PAD.right, 80);
    const pad = padFor(view);
    const plotH = Math.min(view.height, heightCap()) - pad.top - pad.bottom;
    scale = Math.min(scale, plotW / hSpan, plotH / vSpan);
  });
  return Math.max(scale, 8);
}

/** Canvas a view needs at the sheet scale. */
export function viewSize(model, view, scale) {
  const d = model.domain;
  const pad = padFor(view);
  return {
    width: (d.hi[view.h] - d.lo[view.h]) * scale + pad.left + PAD.right,
    height: (d.hi[view.v] - d.lo[view.v]) * scale + pad.top + pad.bottom,
  };
}

/**
 * The pixel transform a view uses, so anything painted under the SVG -- a
 * field map on a canvas -- lands on exactly the same metres.
 */
export function viewTransform(model, view, scale) {
  const { width, height } = viewSize(model, view, scale);
  const d = model.domain;
  return {
    width,
    height,
    X: (v) => padFor(view).left + (v - d.lo[view.h]) * scale,
    Y: (v) => padFor(view).top + (d.hi[view.v] - v) * scale,
  };
}

/** Where a section is cut by default; exported so a slider can start there. */
export function defaultCut(model, view) {
  return sectionAt(model, view);
}

/**
 * @param {object} [options]
 * @param {number} [options.at] cut position along the view's normal, metres
 * @param {boolean} [options.transparent] no volume fills, for use over a map
 */
export function drawView(model, view, scale, options = {}) {
  const { width, height, X, Y } = viewTransform(model, view, scale);
  const svg = el('svg', {
    width,
    height,
    viewBox: `0 0 ${width} ${height}`,
    role: 'img',
    'aria-label': view.title,
  });
  if (options.transparent) svg.classList.add('dw-over-map');

  const d = model.domain;
  const hSpan = d.hi[view.h] - d.lo[view.h];
  const at = options.at ?? sectionAt(model, view);

  /**
   * What is already on the page, by class and projected rectangle.
   *
   * A section looks down an axis, so everything along that axis lands on the
   * same rectangle: in the transverse section the worked hall's 440 racks
   * project onto ten. Drawn one per rack that is 44 copies of the same
   * outline, and an outline at 45% opacity stacked 44 deep is solid black --
   * a rack that the section does not cut came out looking like one it did
   * (ADR-050).
   */
  const painted = new Set();

  const paint = (lo, hi, cls, label, options = {}) => {
    const x0 = X(Math.min(lo[view.h], hi[view.h]));
    const x1 = X(Math.max(lo[view.h], hi[view.h]));
    const y0 = Y(Math.max(lo[view.v], hi[view.v]));
    const y1 = Y(Math.min(lo[view.v], hi[view.v]));
    // Once per rectangle per class. Whatever reaches here first wins, which
    // is why the callers put the solid ones down before the dashed.
    const key = `${cls}|${x0.toFixed(2)},${y0.toFixed(2)},${x1.toFixed(2)},${y1.toFixed(2)}`;
    const twin = `${cls.split(' ')[0]}|${x0.toFixed(2)},${y0.toFixed(2)},${x1.toFixed(2)},${y1.toFixed(2)}`;
    if (painted.has(key) || painted.has(twin)) return { x0, y0, x1, y1 };
    painted.add(key);
    painted.add(twin);
    svg.append(
      el('rect', {
        x: x0,
        y: y0,
        width: Math.max(x1 - x0, 1.6),
        height: Math.max(y1 - y0, 1.6),
        class: cls,
      }),
    );
    // Wide enough for THIS text, not for a nominal 26 px: at 9.5 px bold a
    // character is about 5.8 px, so "F10B1-12" needs 50. Magnify a hall and a
    // 0,6 m rack passes 26 px long before its own id fits, and the labels run
    // into each other across the whole row.
    if (label && x1 - x0 > Math.max(26, label.length * 5.8 + 4) && y1 - y0 > 13) {
      // A panel's caption sits near the top of its rectangle rather than in
      // the middle of it. The middle of a fan wall in section is where the
      // racks and the sensors are, so `fan wall` was set across a rack with a
      // sensor marker through it; the top of the same rectangle is clear
      // (ADR-052). A rack's own id stays centred: the rectangle IS the rack,
      // and there is nothing else in it.
      const y = options.labelAtTop
        ? y0 + 11
        : (y0 + y1) / 2 + 3.5;
      svg.append(el('text', { x: (x0 + x1) / 2, y, class: 'dw-label' }, label));
    }
    return { x0, y0, x1, y1 };
  };

  const line = (x1, y1, x2, y2, cls) =>
    svg.append(el('line', { x1, y1, x2, y2, class: cls }));

  // 1 — volumes. The transverse section looks along x, so gallery and hall
  // project onto the same rectangle; only the one the plane actually passes
  // through belongs on that drawing.
  const galleries = model.galleries ?? [model.gallery];
  for (const [box, cls] of [
    ...galleries.map((box) => [box, 'dw-gallery']),
    [model.hall, 'dw-hallfill'],
  ]) {
    if (view.normal === 0 && !straddles(box.lo, box.hi, 0, at)) continue;
    paint(box.lo, box.hi, cls);
  }

  // 2 — what volume is what.
  //
  // One rule: the hall below the false ceiling is cold-side air. That is what
  // the fan wall fills it with and what a person standing in it breathes, so
  // it is the room's default state and the contained hot aisles are the
  // exception painted over it.
  //
  // Tinting the aisle bands alone left everything that was neither an aisle
  // nor a rack as bare paper -- the space above a rack, the transverse aisle
  // between two blocks, the whole of the longitudinal section. White read as
  // a gap in the drawing rather than as the room (ADR-052).
  // THE ROOM STANDS ON THE DECK. With a raised floor the volume below it is
  // the supply plenum, not the room: painting the room's air from the slab
  // ran the hot aisles straight down through the plenum, so the section
  // showed a contained hot aisle a metre below the floor anybody stands on
  // (ADR-107). Without a floor the two are the same line.
  const deck = model.floor_height || model.domain.lo[2];
  paint(
    [model.hall.lo[0], model.domain.lo[1], deck],
    [model.hall.hi[0], model.domain.hi[1], model.ceiling_z],
    'dw-cold',
  );
  // The hot aisles, where the y axis is on screen to show them. They stop
  // where the rack rows stop: an aisle is the space between two rows, and
  // beyond the last rack there are no rows to be between. And they stop at
  // the deck, for the reason above.
  if (view.h === 1 || view.v === 1) {
    for (const band of hotAisles(model)) {
      for (const [x0, x1] of rackBlocks(model)) {
        paint(
          [x0, band[0], deck],
          [x1, band[1], view.v === 2 ? model.ceiling_z : model.domain.hi[2]],
          'dw-hot',
        );
      }
    }
  }

  // 3 — return plenum, above the false ceiling and inside the hall only
  if (view.v === 2) {
    paint(
      [model.hall.lo[0], model.domain.lo[1], model.ceiling_z],
      [model.hall.hi[0], model.domain.hi[1], model.domain.hi[2]],
      'dw-plenum',
    );
    // 3b — the supply plenum under the deck, where a case has one. It runs
    // under the gallery as well as under the hall, because that is where the
    // units discharge into it (ADR-076). Cold rather than hot: it is the
    // supply side of the loop, and the drawing already says cold in blue.
    if (model.floor_height) {
      paint(
        [model.domain.lo[0], model.domain.lo[1], model.domain.lo[2]],
        [model.domain.hi[0], model.domain.hi[1], model.floor_height],
        'dw-cold',
      );
    }
  }

  // 4 — panels the section sees face-on: we look straight at the rectangle.
  //
  // One rule, no exceptions: cut by this plane -> solid; anywhere else ->
  // dashed outline. Filling a panel the section misses reads as though the
  // section went through it, which is the one thing these drawings exist to
  // make unambiguous.
  const seen = {};
  const enclosing = new Map();
  for (const panel of model.panels) {
    if (panel.name === 'ceiling') continue; // drawn as the heavy line below
    if (ofRack(panel)) continue; // it IS the rack; see below
    if (panel.axis !== view.normal) continue;
    const { lo, hi } = panelBounds(panel);
    const cut = Math.abs(panel.position - at) < 1e-6;
    if (!cut && panel.kind === 'wall' && !(view.h === 1 || view.v === 1)) {
      // The containment, held back to step 6.6 -- see there. Both walls of an
      // aisle project onto the same rectangle, and ten of them stacked would
      // turn a 10% wash into an opaque one, so each is kept once.
      const id = [lo[view.h], hi[view.h], lo[view.v], hi[view.v]].join();
      if (!enclosing.has(id)) enclosing.set(id, [lo, hi]);
      continue;
    }
    paint(lo, hi, cut ? klass(panel, model) : `${klass(panel, model)} dw-beyond`,
      cut ? null : PANEL_LABEL[panel.name] ?? fanLabel(panel, seen, model),
      { labelAtTop: true });
  }

  // 5 — racks.
  //
  // A rack the plane cuts wins over one behind it at the same place: solid
  // beats dashed where both would be drawn, which is the drawing's own rule.
  // So the cut ones go down first and the rest only fill what is left.
  const racks = [...model.racks].sort(
    (a, b) => Number(straddles(b.lo, b.hi, view.normal, at))
            - Number(straddles(a.lo, a.hi, view.normal, at)),
  );
  const labelled = [];
  for (const rack of racks) {
    const cut = straddles(rack.lo, rack.hi, view.normal, at);
    // No caption from `paint` on the plan: the block below writes the name AND
    // the load in the cabinet, and `paint`'s own centred id printed straight
    // through it -- one small horizontal label lying across two big rotated
    // ones, in every cabinet wide enough to earn it.
    const plan = view.id === 'plan';
    const box = paint(rack.lo, rack.hi, cut ? 'dw-rack' : 'dw-rack dw-behind',
      plan ? '' : rack.id);
    if (cut && plan) labelled.push([rack, box]);
  }

  // 5b — what each cabinet is, written in it. Only on the plan: a section
  // cuts a row lengthways and the same name would print once over a strip of
  // fifteen. The type is sized from the drawn rectangle rather than fixed, so
  // it fits whatever the sheet scale turned out to be -- small on a hall of
  // 224, comfortable on a POD of three, and legible in either at a zoom.
  for (const [rack, box] of labelled) {
    const w = box.x1 - box.x0;
    const h = box.y1 - box.y0;
    const along = Math.max(w, h);          // the text runs the long way
    const across = Math.min(w, h);         // and two lines have to fit across
    const size = Math.min(across / 2.9, along / 5.5);
    if (size < 1.4) continue;              // below this it is a smudge, not a name
    const cx = (box.x0 + box.x1) / 2;
    const cy = (box.y0 + box.y1) / 2;
    const turn = h > w ? `rotate(-90 ${cx} ${cy})` : '';
    // Centred as a PAIR, so the two lines sit inside the cabinet rather than
    // the name in it and the load half out of the bottom of it.
    const rows = [
      [rack.id, 'dw-rack-id', -size * 0.6],
      [`${fmt(rack.load_kw ?? 0)} kW`, 'dw-rack-kw', size * 0.6],
    ];
    for (const [text, cls, dy] of rows) {
      svg.append(el('text', {
        x: cx, y: cy + dy, class: cls, transform: turn,
        'font-size': `${size.toFixed(2)}px`,
      }, text));
    }
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
    // One dividing wall per gallery: the hall's own two x faces when there is
    // a gallery at each end, the near one alone when there is only one.
    for (const x of model.dividers ?? [model.hall.lo[0]]) {
      line(X(x), Y(d.hi[view.v]), X(x), Y(d.lo[view.v]), 'dw-divider');
    }
  }

  // 6.4 — the supply plenum, where the case asks for one.
  //
  // Only the volume: the inner leaf and the grilles are panels, and step 7
  // already draws every panel this view sees edge-on. Drawing them here as
  // well put a second line over the first and a second caption beside it.
  //
  // The cavity between the two leaves of the wall carries the same cold
  // supply air as the room, so it is tinted the same. What makes it a plenum
  // is not its air but its two boundaries: the wall the units are mounted in,
  // which is the one that was always there, and the inner leaf whose grilles
  // decide where the air leaves (ADR-058).
  if (view.h === 0 || view.v === 0) {
    for (const wall of model.panels.filter(isPlenumWall)) {
      const divider = (model.dividers ?? [model.hall.lo[0]]).reduce(
        (best, x) => (Math.abs(x - wall.position) < Math.abs(best - wall.position)
          ? x : best),
        model.hall.lo[0],
      );
      const a = [Math.min(wall.position, divider), d.lo[1], d.lo[2]];
      const b = [Math.max(wall.position, divider), d.hi[1], model.ceiling_z];
      // The cold aisle's own tint: it is the same air, on its way to the
      // same place, and a colour of its own would be a third thing to learn
      // on a drawing that already says what cold-side air looks like.
      paint(a, b, 'dw-cold');
    }
  }

  // 6.5 — the fan wall's depth: drawn, never meshed.
  //
  // The solver sees a zero-thickness baffle pair, because a fan wall is a
  // boundary condition rather than a volume. On a drawing that leaves a
  // machine 3,7 m tall and 1,5 m deep as a single line, and a reader asking
  // whether the mechanical gallery is deep enough for it has nothing to
  // measure. The body is the unit's own depth off its datasheet, set back on
  // the gallery side -- `sign` is which side that is -- and it appears only
  // in the views where depth is on screen, which is to say wherever the fan
  // wall is seen edge-on (ADR-046).
  if (model.fan_depth_m) {
    for (const panel of model.panels) {
      if (panel.kind !== 'fan' || panel.axis === view.normal) continue;
      const { lo, hi } = panelBounds(panel);
      const along = panel.axis === view.h ? view.v : view.h;
      const back = panel.position - model.fan_depth_m * (panel.sign ?? 1);
      const a = [0, 0, 0];
      const b = [0, 0, 0];
      a[panel.axis] = Math.min(back, panel.position);
      b[panel.axis] = Math.max(back, panel.position);
      a[along] = lo[along];
      b[along] = hi[along];
      const cut = straddles(lo, hi, view.normal, at);
      paint(a, b, cut ? 'dw-fanbody' : 'dw-fanbody dw-beyond');
    }
  }

  // 6.6 — the containment the section plane sits inside.
  //
  // A section along a hot aisle passes BETWEEN the two walls that contain it,
  // so neither is cut and both project onto the same rectangle: rack top to
  // ceiling, over the length of the block. That rectangle is the contained
  // volume, and it is what a section of a contained hall is read for.
  //
  // Drawn here rather than with the other panels because every edge of it
  // lands on something already on the page -- the ceiling line along its top,
  // the end doors down its sides, the rack tops along its bottom -- so as an
  // outline alone it was invisible. Painted the way the plan paints the same
  // thing: the hot aisle's own tint inside the containment's green. Over a
  // field map it goes back to an outline, where a wash would tint the
  // temperatures underneath it.
  //
  // Only in the view that carries no aisle tints. Where y is on screen the
  // bands in step 2 already say which volume is which, and a second wash over
  // them says it twice.
  for (const [lo, hi] of enclosing.values()) paint(lo, hi, 'dw-contained');

  // 7 — panels the section sees edge-on. These project to a line, so a
  // rectangle would collapse to a hairline and disappear under the wall it
  // sits in -- which is exactly where the fan wall and the plenum opening
  // live. Drawn last, as heavy strokes, so an opening reads as a gap punched
  // through the wall line underneath it.
  const seenEdge = {};
  for (const panel of model.panels) {
    if (panel.name === 'ceiling' || panel.axis === view.normal) continue;
    // The rack's own lid and ends lie exactly on the rack box, which step 5
    // has already drawn. Drawn again as walls they read as containment: in
    // the transverse section the lid came out as a heavy green line across
    // the top of the rack, meeting the chimney wall in an L and putting a
    // wall where the room has a rack. The chimney now lands on the rack,
    // which is what it does (ADR-045).
    if (ofRack(panel)) continue;
    const { lo, hi } = panelBounds(panel);
    const along = panel.axis === view.h ? view.v : view.h;
    const cut = straddles(lo, hi, view.normal, at);
    const cls = `${klass(panel, model)} dw-edge${cut ? '' : ' dw-edge-beyond'}`;
    const a = panel.axis === view.h
      ? [X(panel.position), Y(lo[along]), X(panel.position), Y(hi[along])]
      : [X(lo[along]), Y(panel.position), X(hi[along]), Y(panel.position)];
    line(a[0], a[1], a[2], a[3], cls);

    // Only name what this section actually cuts, and only when the caption
    // fits along the line: a label spilling past its own opening is worse
    // than no label at all.
    const label = cut
      ? EDGE_LABEL[panel.name] ?? fanLabel(panel, seenEdge, model) : null;
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
          class: `dw-edge-label ${klass(panel, model)}`,
          ...(panel.axis === view.h ? { transform: `rotate(-90 ${tx} ${ty})` } : {}),
        }, label),
      );
    }
  }


  // 9 — annotation
  const bounds = { left: padFor(view).left - 26, right: width - 6 };
  annotate(svg, model, view, X, Y, bounds);
  dimensions(svg, model, view, X, Y, width, height, hSpan);
  return svg;
}

function annotate(svg, model, view, X, Y, bounds) {
  // Notes are placed in room coordinates and mapped through the view, so a
  // plan turned on its side keeps its captions where they belong. Anchored
  // at the centre, so keep half the string inside the drawing.
  const put = (point, label, cls = 'dw-note') => {
    const half = label.length * 2.9;
    const x = Math.min(
      Math.max(X(point[view.h]), bounds.left + half),
      bounds.right - half,
    );
    svg.append(el('text', { x, y: Y(point[view.v]), class: cls }, label));
  };
  const mid = (a) => (a[0] + a[1]) / 2;
  const cold = coldAisles(model)[0];
  const hot = hotAisles(model)[0];
  const hallMidX = mid([model.hall.lo[0], model.hall.hi[0]]);
  const galleries = model.galleries ?? [model.gallery];
  const galleryMids = galleries.map((box) => mid([box.lo[0], box.hi[0]]));

  if (view.id === 'section-a') {
    // IN THE ROOM, which stands on the deck. At 0,45 m absolute the caption
    // sat inside the under-floor plenum on every raised-floor case, naming
    // the supply plenum `cold aisle` (ADR-107).
    const deck = model.floor_height || 0;
    // WHICH aisle is contained decides both captions. A lid over the cold
    // aisle means the room above the racks is hot and open -- calling that
    // hot aisle a chimney, which this did unconditionally, described the
    // other arrangement (ADR-100, ADR-110).
    const lidded = model.panels.some((p) => p.name.startsWith('containment_lid'));
    const walled = model.panels.some((p) => p.name.startsWith('containment_wall'));
    put([hallMidX, mid(cold), deck + 0.45],
      lidded ? 'contained cold aisle' : 'cold aisle', 'dw-note dw-cold-t');
    put([hallMidX, mid(hot), model.ceiling_z - 1.1],
      walled ? 'chimney' : 'hot aisle', 'dw-note dw-hot-t');
    if (model.floor_height) {
      put([hallMidX, mid(cold), model.floor_height / 2],
        'underfloor supply plenum', 'dw-note dw-cold-t');
    }
  }
  if (view.id === 'section-b') {
    for (const x of galleryMids) {
      put([x, 0, model.domain.hi[2] - 0.55], 'mechanical gallery');
    }
    // The plenum is one volume however many galleries feed from it -- saying
    // so on the drawing is the whole point of the double-gallery layout.
    put([hallMidX, 0, model.ceiling_z + 0.6],
      galleries.length > 1 ? 'return plenum (shared)' : 'return plenum',
      'dw-note dw-hot-t');
    if (model.floor_height) {
      put([hallMidX, 0, model.floor_height / 2],
        'underfloor supply plenum', 'dw-note dw-cold-t');
    }
  }
  if (view.id === 'plan') {
    for (const x of galleryMids) put([x, model.domain.hi[1] - 0.35, 0], 'gallery');
    put([hallMidX, mid(cold), 0], 'cold aisle', 'dw-note dw-cold-t');
  }
}

/**
 * The parts a plan is read for, each dimensioned ONCE where it first occurs.
 *
 * A hall repeats: four pods, sixteen rows, the same aisle between each pair.
 * Dimensioning every instance would put seventeen figures down one margin and
 * say nothing the first four do not. So each distinct part is measured where
 * it first appears, and the reader carries it along the repeat -- which is how
 * a layout drawing has always been read.
 *
 * Returns segments along one axis as `{lo, hi, label}`, in order.
 */
function planBands(model, axis) {
  const seg = [];
  const seen = new Set();
  const add = (lo, hi, label) => {
    const key = `${label}|${(hi - lo).toFixed(2)}`;
    if (hi - lo < 0.05 || seen.has(key)) return;
    seen.add(key);
    seg.push({ lo, hi, label });
  };
  // THE DATA HALL ITSELF, on both axes. The overall line under the drawing
  // used to be the modelled DOMAIN -- galleries plus hall, one number that is
  // neither room -- and the hall's own length and width were nowhere (ADR-105).
  if (model.hall) add(model.hall.lo[axis], model.hall.hi[axis], 'data hall');
  if (axis === 0) {
    for (const g of model.galleries) add(g.lo[0], g.hi[0], 'gallery');
    const supply = model.panels.filter((p) => p.name.startsWith('supply_mesh'));
    if (supply.length) {
      const wall = model.galleries[0].hi[0];
      add(Math.min(wall, supply[0].position), Math.max(wall, supply[0].position),
        'supply plenum');
    }
    // THE CABINETS, not the block they were asked to fill. `model.blocks` is
    // what the rows turned out to be (ADR-085) and a hall whose rows differ
    // carries one span each -- dimensioning them all as `row` printed 10,40
    // and 10,00 down the same margin with the cabinets visibly stopping
    // before the longer one. The racks are drawn; this measures them, once
    // per distinct length (ADR-105).
    const blocks = model.blocks || [];
    for (const [lo, hi] of blocks) add(lo, hi, 'racks');
    for (let i = 1; i < blocks.length; i += 1) {
      add(blocks[i - 1][1], blocks[i][0], 'cross aisle');
    }
  } else {
    const rows = model.rows || [];
    for (const r of rows) add(r.band[0], r.band[1], 'row depth');
    for (const [lo, hi] of model.hot_aisles || []) add(lo, hi, 'hot aisle');
    for (const [lo, hi] of model.cold_aisles || []) add(lo, hi, 'cold aisle');
    const fan = (model.panels || []).find((q) => q.kind === 'fan');
    if (fan) add(fan.extent[0][0], fan.extent[0][1], unitNaming(model).noun);
  }
  return seg.sort((a, b) => a.lo - b.lo || a.hi - b.hi);
}

/**
 * The band chains, one per axis. Horizontal below the geometry, vertical in
 * the left margin, each on its own lane so two short dimensions side by side
 * cannot print over one another.
 */
function planDimensions(svg, model, view, X, Y, width, height) {
  const d = model.domain;
  const tick = (x1, y1, x2, y2) =>
    svg.append(el('line', { x1, x2, y1, y2, class: 'dw-dim' }));

  const lanes = [];
  const lane = (lo, hi) => {
    // The first lane this segment does not collide in. Short dimensions next
    // to each other are the whole reason a chain needs more than one line.
    for (let i = 0; i < lanes.length; i += 1) {
      if (lanes[i].every(([a, b]) => hi <= a + 0.01 || lo >= b - 0.01)) {
        lanes[i].push([lo, hi]);
        return i;
      }
    }
    lanes.push([[lo, hi]]);
    return lanes.length - 1;
  };

  for (const axis of [view.h, view.v]) {
    lanes.length = 0;
    const horizontal = axis === view.h;
    // The level leaders already own the first 26 px of the left margin, so the
    // vertical chain starts outside them and grows further out.
    const base = horizontal ? Y(d.lo[view.v]) + 18 : X(d.lo[view.h]) - 46;
    for (const { lo, hi, label } of planBands(model, axis)) {
      // A horizontal chain packs into as few lanes as it can. A vertical one
      // does not: its text runs along the band and overhangs it, so two
      // dimensions that do not overlap in metres still overlap on the page.
      const step = (horizontal ? lane(lo, hi) : lanes.push([]) - 1) * 15;
      const a = horizontal ? X(lo) : Y(hi);
      const b = horizontal ? X(hi) : Y(lo);
      const at = horizontal ? base + step : base - step;
      if (horizontal) {
        tick(a, at, b, at);
        tick(a, at - 3, a, at + 3);
        tick(b, at - 3, b, at + 3);
        svg.append(el('text', { x: (a + b) / 2, y: at - 4, class: 'dw-dimtext' },
          fmt(hi - lo)));
        svg.append(el('text', { x: (a + b) / 2, y: at + 9, class: 'dw-bandlabel' },
          label));
      } else {
        tick(at, a, at, b);
        tick(at - 3, a, at + 3, a);
        tick(at - 3, b, at + 3, b);
        // ONE line, not two. A vertical chain's text runs along the band, and
        // a 1,2 m row on a hall's scale is shorter than the words describing
        // it -- so two rotated lines side by side collide with each other and
        // with the level leaders. Value and name on one line halves the ink
        // and reads the way a dimension is spoken.
        const m = (a + b) / 2;
        svg.append(el('text', {
          x: at - 4, y: m, class: 'dw-dimtext',
          transform: `rotate(-90 ${at - 4} ${m})`,
        }, `${fmt(hi - lo)}  ${label}`));
      }
    }
  }
}

function dimensions(svg, model, view, X, Y, width, height, hSpan) {
  const d = model.domain;
  // The plan carries the band chains between the geometry and this line, so
  // the overall stands clear of them. The sections have nothing in between.
  const plan = view.id === 'plan';
  if (plan) planDimensions(svg, model, view, X, Y, width, height);
  const y = Y(d.lo[view.v]) + (plan ? 56 : 20);
  // The plan's overall is the DATA HALL. Gallery plus hall in one figure is
  // the box the mesher builds and no room anybody stands in; the galleries
  // carry their own dimension in the chain above (ADR-105). A section keeps
  // the domain, which is what it is a section of.
  const ends = plan && model.hall
    ? [model.hall.lo[view.h], model.hall.hi[view.h]]
    : [d.lo[view.h], d.hi[view.h]];
  const x0 = X(ends[0]);
  const x1 = X(ends[1]);
  svg.append(el('line', { x1: x0, x2: x1, y1: y, y2: y, class: 'dw-dim' }));
  for (const x of [x0, x1]) {
    svg.append(el('line', { x1: x, x2: x, y1: y - 3, y2: y + 3, class: 'dw-dim' }));
  }
  svg.append(
    el('text', { x: (x0 + x1) / 2, y: y - 5, class: 'dw-dimtext' },
      plan && model.hall ? `${fmt(ends[1] - ends[0])} data hall` : fmt(hSpan)),
  );
  svg.append(
    el('text', { x: (x0 + x1) / 2, y: height - 6, class: 'dw-axis' }, view.hLabel),
  );

  // Levels, in the left margin, each with a leader out to the geometry so the
  // label cannot be mistaken for the one above or below it.
  const [spanLo, spanHi] = rackSpan(model);
  const levels =
    view.v === 2
      ? [
          [d.hi[2], 'slab'],
          [model.ceiling_z, 'ceiling'],
          ...(model.racks.length ? [[model.racks[0].hi[2], 'rack top']] : []),
        ]
      : view.v === 1
        ? [
            [model.aisles.cold[1], 'front'],
            [model.aisles.racks[1], 'back'],
          ]
        : [
            [spanLo, 'rows'],
            [spanHi, ''],
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

const fmt = (v) => v.toFixed(2);
