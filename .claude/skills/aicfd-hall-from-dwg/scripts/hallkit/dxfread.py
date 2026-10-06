"""Read a DXF as rectangles, segments and texts.

Nothing here decides what a rectangle IS -- that is `extract`. This stage
only turns the drawing's entities into geometry that can be classified by
dimension, whatever layer the drafter used:

  * closed axis-aligned polylines (LWPOLYLINE / POLYLINE) -> rects
  * closed polylines that are not rectangles -> outlines (kept for the cage
    and for the hall when the drafter drew an L-shape; reported)
  * LINE entities -> segments, and clusters of connected segments whose
    outline is a rectangle -> rects (exploded blocks: the units, the panels)
  * INSERT (block references) -> their virtual entities, one level down
  * TEXT / MTEXT / ATTRIB -> texts with an insertion point

Units are read from $INSUNITS and converted to metres; a drawing in
millimetres (the usual case for an architect's DWG) is scaled by 1/1000.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field

from . import geom

# $INSUNITS codes -> metres per drawing unit
_UNIT_SCALE = {0: None, 1: 0.0254, 2: 0.3048, 4: 0.001, 5: 0.01, 6: 1.0}


@dataclass
class Drawing:
    rects: list[tuple[tuple, str]] = field(default_factory=list)      # (rect, layer)
    outlines: list[tuple[list, str]] = field(default_factory=list)    # (points, layer)
    segments: list[tuple[tuple, tuple, str]] = field(default_factory=list)  # (p, q, layer)
    texts: list[tuple[str, float, float, str]] = field(default_factory=list)  # (text, x, y, layer)
    inserts: list[tuple[str, tuple, str, int]] = field(default_factory=list)  # (block name, bbox rect, layer, depth)
    scale: float = 1.0
    units_code: int | None = None
    layers: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    notes: list[str] = field(default_factory=list)

    def transpose(self) -> None:
        """Swap x and y everywhere: used once, when the galleries lie along y."""
        self.rects = [(geom.transpose(r), lay) for r, lay in self.rects]
        self.outlines = [([(p[1], p[0]) for p in pts], lay) for pts, lay in self.outlines]
        self.segments = [((p[1], p[0]), (q[1], q[0]), lay) for p, q, lay in self.segments]
        self.texts = [(t, y, x, lay) for t, x, y, lay in self.texts]
        self.inserts = [(n, geom.transpose(r), lay, d) for n, r, lay, d in self.inserts]


def _inserts(msp, scale, out, depth=0, max_depth=2, cache=None):
    """Block references with their footprint: equipment in an architect's
    drawing is a block (a fan wall, a CRAH, a CDU), often a detailed one of
    thousands of lines that no rectangle finder will read. The bounding box
    of the block's own entities, at its insertion, is its footprint."""
    from ezdxf import bbox as _bbox
    cache = {} if cache is None else cache
    for e in msp:
        if e.dxftype() != "INSERT":
            continue
        try:
            nested = list(e.virtual_entities())
        except Exception:
            continue
        key = (e.dxf.name, round(e.dxf.rotation, 1), round(e.dxf.xscale, 3), round(e.dxf.yscale, 3))
        try:
            ext = _bbox.extents(nested, fast=True)
            if ext.has_data:
                r = (ext.extmin.x * scale, ext.extmin.y * scale, ext.extmax.x * scale, ext.extmax.y * scale)
                if 0.3 <= (r[2] - r[0]) <= 30 and 0.3 <= (r[3] - r[1]) <= 30:
                    out.append((e.dxf.name, r, e.dxf.layer, depth))
        except Exception:
            pass
        if depth < max_depth:
            _inserts(nested, scale, out, depth + 1, max_depth, cache)


def _entities(msp, depth=0, max_depth=6):
    """Model-space entities, with block references opened recursively: an
    architect's base drawing is one block holding the room blocks holding the
    label blocks, and the rack rows are blocks of rack blocks. Each entity
    keeps its own layer (a nested entity on layer 0 inherits its parent's)."""
    for e in msp:
        kind = e.dxftype()
        if kind == "INSERT" and depth < max_depth:
            try:
                nested = list(e.virtual_entities())
            except Exception:  # a block that cannot be exploded is skipped
                nested = []
            for v, layer in _entities(nested, depth + 1, max_depth):
                yield v, (e.dxf.layer if layer == "0" else layer)
            try:
                for a in e.attribs:
                    yield a, e.dxf.layer
            except Exception:
                pass
        else:
            yield e, e.dxf.layer


def read(path: str, unit_override: float | None = None, tol: float = 0.02) -> Drawing:
    import ezdxf
    from ezdxf import recover

    doc, auditor = recover.readfile(path)
    dwg = Drawing()
    dwg.units_code = doc.header.get("$INSUNITS", 0)
    scale = unit_override if unit_override is not None else _UNIT_SCALE.get(dwg.units_code)
    if scale is None:
        scale = 1.0
        dwg.notes.append("$INSUNITS is unitless: coordinates taken as metres; pass --units mm if the drawing is in millimetres")
    dwg.scale = scale
    if auditor.has_errors:
        dwg.notes.append(f"DXF recovered with {len(auditor.errors)} error(s) audited")

    s = scale
    for e, layer in _entities(doc.modelspace()):
        kind = e.dxftype()
        dwg.layers[layer] += 1
        try:
            if kind in ("LWPOLYLINE", "POLYLINE"):
                if kind == "LWPOLYLINE":
                    pts = [(p[0] * s, p[1] * s) for p in e.get_points()]
                    closed = bool(e.closed)
                else:
                    pts = [(v.dxf.location.x * s, v.dxf.location.y * s) for v in e.vertices]
                    closed = bool(e.is_closed)
                if not closed and len(pts) >= 4 and \
                        geom.near(pts[0][0], pts[-1][0], 0.1) and geom.near(pts[0][1], pts[-1][1], 0.1):
                    gap = ((pts[0][0] - pts[-1][0]) ** 2 + (pts[0][1] - pts[-1][1]) ** 2) ** 0.5
                    if gap > tol:
                        dwg.notes.append(f"polyline on {layer} at ({pts[0][0]:.2f}, {pts[0][1]:.2f}) left {gap * 1000:.0f} mm open: closed")
                    closed = True  # drawn back to (or nearly to) the start without the closed flag
                if closed and geom.is_axis_rect(pts, tol):
                    r = geom.rect_of(pts)
                    if geom.width(r) > tol and geom.height(r) > tol:
                        dwg.rects.append((r, layer))
                elif closed:
                    dwg.outlines.append((pts, layer))
                else:
                    for i in range(len(pts) - 1):
                        dwg.segments.append((pts[i], pts[i + 1], layer))
            elif kind == "LINE":
                p, q = e.dxf.start, e.dxf.end
                dwg.segments.append(((p.x * s, p.y * s), (q.x * s, q.y * s), layer))
            elif kind == "TEXT":
                x, y = e.dxf.insert.x * s, e.dxf.insert.y * s
                try:  # aligned text keeps its position in the alignment point
                    if e.dxf.hasattr("align_point") and e.dxf.halign != 0:
                        x, y = e.dxf.align_point.x * s, e.dxf.align_point.y * s
                except Exception:
                    pass
                dwg.texts.append((e.dxf.text.strip(), x, y, layer))
            elif kind == "ATTRIB":
                dwg.texts.append((e.dxf.text.strip(), e.dxf.insert.x * s, e.dxf.insert.y * s, layer))
            elif kind == "MTEXT":
                dwg.texts.append((e.plain_text().strip(), e.dxf.insert.x * s, e.dxf.insert.y * s, layer))
        except Exception as exc:  # one bad entity does not stop the read
            dwg.notes.append(f"{kind} on {layer} skipped: {exc}")

    dwg.rects.extend(_rects_from_segments(dwg.segments, tol))
    _inserts(doc.modelspace(), s, dwg.inserts)
    return dwg


def _rects_from_segments(segments, tol):
    """Clusters of connected LINEs whose bounding box they outline -> rects.

    An exploded block (a unit, a panel, a PDU) is a handful of LINEs that
    touch end to end. Cluster by endpoint proximity (union-find on a 0,1 m
    grid), then keep the clusters whose segments all lie on the bounding
    box's four edges: that is a rectangle drawn as lines.
    """
    if not segments:
        return []
    n = len(segments)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    grid = defaultdict(list)
    cell = 0.1
    for i, (p, q, _) in enumerate(segments):
        for pt in (p, q):
            k = (round(pt[0] / cell), round(pt[1] / cell))
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((k[0] + dx, k[1] + dy), ()):
                        ri, rj = find(i), find(j)
                        if ri != rj:
                            parent[ri] = rj
            grid[k].append(i)
    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)
    out = []
    for idx in clusters.values():
        if len(idx) < 4 or len(idx) > 60:
            continue
        pts = [pt for i in idx for pt in segments[i][:2]]
        r = geom.rect_of(pts)
        if geom.width(r) < 0.2 or geom.height(r) < 0.2:
            continue
        on_edge = 0
        for i in idx:
            p, q, _ = segments[i]
            for pt in (p, q):
                if (geom.near(pt[0], r[0], tol) or geom.near(pt[0], r[2], tol)
                        or geom.near(pt[1], r[1], tol) or geom.near(pt[1], r[3], tol)):
                    on_edge += 1
        if on_edge >= 2 * len(idx) - 2:  # every endpoint on the outline (one stray allowed)
            layers = {segments[i][2] for i in idx}
            out.append((r, sorted(layers)[0]))
    return out
