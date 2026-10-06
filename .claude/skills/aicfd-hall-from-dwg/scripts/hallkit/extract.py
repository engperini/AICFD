"""Classify the drawing's rectangles and texts by dimension and position.

Layers are not trusted: the same drafter puts rack rows on a cable-tray
layer and the containment on a "leito" one. What is trusted is size and
where a thing stands:

  hall        the closed rectangle that contains the anchor (the hall's
              name text, or a point the user gives), area over 100 m2, and
              the smallest such rectangle
  galleries   rectangles 2 to 12 m wide standing against the hall's long
              sides, with a gap of up to 0,5 m (the gap is the wall): the
              mechanical galleries or technical rooms the plant stands in
  racks       rectangles of a cabinet's plan size inside the hall
  units       rectangles of a plant unit's plan size inside a gallery
  aisle boxes rectangles 1 to 3 m wide and at least three cabinets long
              inside the hall, drawn by the drafter as the containment
              (cold or hot: decided later from the cabinets' fronts); or
              the bands of 600 mm tiles when the drawing carries those
  cage        a closed rectangle over 3 x 4 m inside the hall enclosing
              racks and smaller than the hall; or LINE segments on a layer
              whose name contains CAGE / MESH
  tags        a text of the tag pattern nearest a rack or unit, assigned
              uniquely by global distance
  fronts      a FRONT / BACK (FRENTE / COSTAS) text next to a cabinet's
              face says which way it looks
  loads       a text inside or within 1,5 m of a cabinet: "10kW",
              "LIQUID-COOLED SERVER RACK 225 kW", "SPARE ... (0~225 kW)",
              "NETWORK RACK 32 kW", "ODF", "SHUFFLE BOX", "FUTURE"

Everything not classified is listed in `leftovers` so the omission is on
the record.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from . import geom
from .dxfread import Drawing

TAG_RE = re.compile(r"^(?:[A-Z]{1,2}[- ]?\d{1,3}|F\d{1,2}[- ]?R?B?\d{1,3}|R\d{1,3})$")
UNIT_TAG_RE = re.compile(r"^(?:UE|CRAC|CRAH|FW|AC|AHU|FAN|UN)[- ]?\d{1,3}$", re.I)
KW_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*KW", re.I)
RANGE_RE = re.compile(r"\(?\s*0\s*[~\-–]\s*(\d+(?:[.,]\d+)?)\s*KW", re.I)
ZERO_WORDS = ("ODF", "PATCH", "PP", "MMR", "SHUFFLE")
FUTURE_WORDS = ("FUTURE", "FUTURO", "ROFR", "RESERVA")
FRONT_WORDS = ("FRONT", "FRENTE")
BACK_WORDS = ("BACK", "COSTAS", "TRASEIRA", "REAR")


@dataclass
class Extracted:
    hall: tuple                                  # inner rect of the hall (drawing coords)
    galleries: list[tuple]                       # rects
    walls: list[dict]                            # {"rect", "side": "W"/"E"}
    envelope: tuple
    racks: list[dict] = field(default_factory=list)    # {"rect","tag","load_kw","kind","layer","front","liquid","label"}
    units: list[dict] = field(default_factory=list)    # {"rect","tag","gallery"}
    aisle_boxes: list[tuple] = field(default_factory=list)
    aisle_box_layers: list[str] = field(default_factory=list)   # the layer each box was drawn on (HAC / CAC is a hint)
    tile_rects: list[tuple] = field(default_factory=list)
    cage_rect: tuple | None = None
    leaves: list[dict] = field(default_factory=list)    # {"rect", "side"}: thin walls parallel to the dividing wall inside a gallery
    cage_segments: list[tuple] = field(default_factory=list)   # ((x0,y0),(x1,y1))
    leftovers: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    transposed: bool = False


def _anchor(dwg: Drawing, hall_name: str | None, anchor_xy) -> tuple[float, float]:
    if anchor_xy:
        return float(anchor_xy[0]), float(anchor_xy[1])
    if hall_name:
        want = hall_name.upper().replace(" ", "")
        hits = [t for t in dwg.texts if want in t[0].upper().replace(" ", "")]
        if hits:
            hits.sort(key=lambda t: len(t[0]))   # the shortest text holding the name is the label itself
            return hits[0][1], hits[0][2]
    names = sorted({t[0] for t in dwg.texts if "HALL" in t[0].upper()})
    raise SystemExit(
        f"hall anchor not found: no text matches {hall_name!r}. Texts with HALL in the drawing: "
        f"{names[:8]}. Give `hall:` (the name as drawn) or `anchor_xy: [x, y]` in answers.yaml"
    )


def _pick_hall(dwg: Drawing, ax: float, ay: float):
    cands = [r for r, _ in dwg.rects if geom.contains_point(r, ax, ay) and geom.area(r) > 100]
    if not cands:
        raise SystemExit(f"no closed rectangle over 100 m2 contains the anchor ({ax:.2f}, {ay:.2f})")
    return min(cands, key=geom.area)


def extract(dwg: Drawing, ref: dict, answers: dict) -> Extracted:
    tol = float(ref.get("tolerance_m", 0.02))
    ax, ay = _anchor(dwg, answers.get("hall"), answers.get("anchor_xy"))
    hall = _pick_hall(dwg, ax, ay)
    gal_w = tuple(ref["gallery_width_range_m"])

    # A drafter who drew the DIVIDING WALLS as thin rectangles inside one
    # outer contour (the treated-DXF flavour) rather than the rooms as
    # separate contours: split the outer rectangle at the walls, the slot
    # holding the anchor is the hall and the slots outside it the galleries.
    thin = [r for r, _ in dwg.rects if geom.contains(hall, r, tol) and r != hall
            and 0.05 <= geom.width(r) <= 0.5 and geom.height(r) >= 0.9 * geom.height(hall)]
    thin_t = [r for r, _ in dwg.rects if geom.contains(hall, r, tol) and r != hall
              and 0.05 <= geom.height(r) <= 0.5 and geom.width(r) >= 0.9 * geom.width(hall)]
    if thin_t and not thin:
        dwg.transpose()
        hall = geom.transpose(hall)
        thin = [geom.transpose(r) for r in thin_t]
        transposed_walls = True
    else:
        transposed_walls = False
    split_galleries = []
    outer = None
    if thin:
        cuts = sorted(thin, key=lambda r: r[0])
        xs = [hall[0]] + [v for c in cuts for v in (c[0], c[2])] + [hall[2]]
        slots = [(xs[i], hall[1], xs[i + 1], hall[3]) for i in range(0, len(xs) - 1, 2)]
        mine = [s for s in slots if s[0] <= ax <= s[2]]
        if mine:
            outer = hall
            hall = mine[0]
            split_galleries = [s for s in slots if s != hall and gal_w[0] <= geom.width(s) <= gal_w[1]]

    # galleries: rooms against the hall on either side; decide the axis they lie on
    side_x, side_y = list(split_galleries), []
    for r, _ in dwg.rects:
        if geom.area(r) < 5 or geom.overlap(r, hall):
            continue
        w, h = geom.width(r), geom.height(r)
        along_y = h >= 0.5 * geom.height(hall) and gal_w[0] <= w <= gal_w[1]
        along_x = w >= 0.5 * geom.width(hall) and gal_w[0] <= h <= gal_w[1]
        if along_y and (0 <= hall[0] - r[2] <= 0.5 or 0 <= r[0] - hall[2] <= 0.5):
            side_x.append(r)
        if along_x and (0 <= hall[1] - r[3] <= 0.5 or 0 <= r[1] - hall[3] <= 0.5):
            side_y.append(r)
    transposed = transposed_walls
    if side_y and not side_x:
        dwg.transpose()
        hall = geom.transpose(hall)
        side_x = [geom.transpose(r) for r in side_y]
        transposed = True
    # one gallery per side: the one touching the hall (a drafter may nest room contours)
    galleries = []
    for side in ("W", "E"):
        cands = [r for r in side_x if (r[2] <= hall[0] + tol) == (side == "W")]
        if cands:
            galleries.append(min(cands, key=lambda r: abs((r[2] if side == "W" else r[0]) - (hall[0] if side == "W" else hall[2]))))
    galleries.sort(key=lambda r: r[0])
    if not galleries:
        gal_hint = answers.get("galleries")
        if gal_hint:
            galleries = [tuple(map(float, g)) for g in gal_hint]
        else:
            raise SystemExit("no mechanical gallery / technical room found against the hall; give `galleries: [[x0,y0,x1,y1], ...]` in answers.yaml")

    walls = []
    for g in galleries:
        if g[2] <= hall[0] + tol:
            walls.append({"rect": (g[2], hall[1], hall[0], hall[3]), "side": "W"})
        else:
            walls.append({"rect": (hall[2], hall[1], g[0], hall[3]), "side": "E"})
    for w in walls:
        if geom.width(w["rect"]) < tol:
            t = float(ref["wall_thickness_m"])
            w["rect"] = (w["rect"][0], w["rect"][1], w["rect"][0] + t, w["rect"][3]) if w["side"] == "W" \
                else (w["rect"][2] - t, w["rect"][1], w["rect"][2], w["rect"][3])
    envelope = hall
    for g in galleries:
        envelope = geom.union(envelope, g)
    claimed = set(thin) | {hall} | set(galleries)
    if outer is not None:
        claimed.add(outer)

    ex = Extracted(hall=hall, galleries=galleries, walls=walls, envelope=envelope, transposed=transposed)
    if transposed:
        ex.notes.append("drawing transposed: the galleries lie along y in the file, x in the model")

    # THE LEAVES OF A DOUBLE WALL. A fan-wall hall is often built with two
    # walls between the technical room and the hall: the units are mounted in
    # the outer one and blow into the cavity, and the inner one -- the hall's
    # wall -- carries the supply grilles. The cavity is the SUPPLY PLENUM. The
    # leaves are thin masonry contours parallel to the dividing wall, standing
    # inside the gallery's contour; the model decides which one the units sit in.
    for r, lay in [(rr, ll) for rr, ll in dwg.rects] + [(geom.rect_of(pts), ll) for pts, ll in dwg.outlines]:
        if r == hall or not (0.05 <= geom.width(r) <= 0.5) or geom.height(r) < 0.5 * geom.height(hall):
            continue
        for g in galleries:
            if geom.contains(g, r, 0.3) and not any(geom.overlap(r, w["rect"]) for w in walls):
                ex.leaves.append({"rect": r, "side": "W" if g[2] <= hall[0] + tol else "E", "layer": lay})
                break

    # racks, units, tiles
    sizes = [tuple(s) for s in ref["rack_plan_sizes_m"]]
    rack_tol = float(ref.get("rack_size_tolerance_m", 0.05))
    unit_sizes = [tuple(s) for s in (ref.get("unit_plan_sizes_m") or [ref.get("unit_plan_size_m")]) if s]
    unit_tol = float(ref.get("unit_size_tolerance_m", 0.12))
    tile = float(ref["tile_size_m"])
    used = set()
    for i, (r, layer) in enumerate(dwg.rects):
        if r in claimed:
            used.add(i)
            continue
        if any(geom.dims_match(r, s, rack_tol) for s in sizes) and geom.contains(hall, r, tol):
            ex.racks.append({"rect": r, "tag": None, "load_kw": None, "kind": "IT", "layer": layer, "front": None,
                             "liquid": False, "label": ""})
            used.add(i)
        elif any(geom.dims_match(r, s, unit_tol) for s in unit_sizes) and any(geom.contains(g, r, 0.5) for g in galleries):
            ex.units.append({"rect": r, "tag": None, "gallery": "W" if r[2] <= hall[0] + tol else "E"})
            used.add(i)
        elif geom.dims_match(r, (tile, tile), 0.02) and geom.contains(hall, r, tol):
            ex.tile_rects.append(r)
            used.add(i)
    # units drawn as BLOCKS: a block reference in a gallery whose footprint is a
    # unit's size, or whose name matches `units.block_pattern` (EFW500C, CRAH...)
    pat = (answers.get("units") or {}).get("block_pattern") or ref.get("units", {}).get("block_pattern")
    for name, r, layer, depth in sorted(dwg.inserts, key=lambda t: (t[3], -geom.area(t[1]))):   # outermost, largest first
        in_gallery = any(geom.contains(g, r, 0.6) for g in galleries)
        if not in_gallery:
            continue
        by_size = any(geom.dims_match(r, sz, unit_tol) for sz in unit_sizes)
        by_name = bool(pat) and re.search(pat, name, re.I) is not None
        if by_size or by_name:
            # a nested sub-block of the same machine overlaps the one already taken: skip it
            if any(geom.overlap(r, u["rect"]) and geom.area(geom.intersection(r, u["rect"]) or (0, 0, 0, 0)) > 0.5 * min(geom.area(r), geom.area(u["rect"]))
                   for u in ex.units):
                continue
            ex.units.append({"rect": r, "tag": None, "gallery": "W" if r[2] <= hall[0] + tol else "E", "block": name})
    # de-duplicate racks drawn twice (a block and its outline); keep the one with the tighter layer name
    uniq = []
    for rk in ex.racks:
        if not any(geom.near(rk["rect"][0], u["rect"][0], 0.05) and geom.near(rk["rect"][1], u["rect"][1], 0.05)
                   and geom.near(rk["rect"][2], u["rect"][2], 0.05) and geom.near(rk["rect"][3], u["rect"][3], 0.05) for u in uniq):
            uniq.append(rk)
    ex.racks = uniq

    # aisle boxes (the drafter's containment) and the cage
    box_w = tuple(ref.get("aisle_box_width_range_m", ref.get("cold_aisle_width_range_m", [1.0, 3.0])))
    for i, (r, layer) in enumerate(dwg.rects):
        if i in used or not geom.contains(hall, r, 0.05):
            continue
        w, h = geom.width(r), geom.height(r)
        short, long_ = min(w, h), max(w, h)
        if long_ >= float(ref.get("cold_aisle_min_length_m", 2.4)) and box_w[0] <= short <= box_w[1] \
                and not any(geom.overlap(r, rk["rect"], 0.05) for rk in ex.racks):
            ex.aisle_boxes.append(r)
            ex.aisle_box_layers.append(layer)
            used.add(i)
        elif w > 3 and h > 4 and geom.area(r) < 0.9 * geom.area(hall) and any(geom.contains(r, rk["rect"], 0.05) for rk in ex.racks) \
                and re.search(r"cage|mesh|tela|gaiola", layer, re.I):
            ex.cage_rect = r if ex.cage_rect is None else geom.union(ex.cage_rect, r)
            used.add(i)
    # de-duplicate aisle boxes drawn twice
    boxes, layers = [], []
    for b, lay in zip(ex.aisle_boxes, ex.aisle_box_layers):
        if not any(all(geom.near(b[k], o[k], 0.05) for k in range(4)) for o in boxes):
            boxes.append(b)
            layers.append(lay)
    ex.aisle_boxes, ex.aisle_box_layers = boxes, layers
    for p, q, layer in dwg.segments:
        if re.search(r"CAGE|MESH|TELA", layer, re.I) and geom.contains_point(hall, *p, 0.05) and geom.contains_point(hall, *q, 0.05):
            ex.cage_segments.append((p, q))
    if ex.cage_segments and ex.cage_rect is None:
        ex.notes.append(f"cage taken from {len(ex.cage_segments)} line segment(s) on a CAGE/MESH layer")

    # texts: tags, fronts, loads
    inside = [t for t in dwg.texts if geom.contains_point(envelope, t[1], t[2], 0.5)]
    rack_tags = [t for t in inside if TAG_RE.match(t[0].upper().replace(" ", ""))]
    _assign_unique(ex.racks, rack_tags, 1.0)
    unit_tags = [t for t in inside if UNIT_TAG_RE.match(t[0].replace(" ", ""))]
    _assign_unique(ex.units, unit_tags, 2.0)
    _fronts(ex.racks, inside)
    _loads(ex.racks, inside, ex)

    # leftovers: closed rectangles inside the envelope that nothing claimed, grouped by size and layer
    left = Counter()
    for i, (r, layer) in enumerate(dwg.rects):
        if i in used or not geom.contains(envelope, r, 0.05):
            continue
        left[(round(geom.width(r), 1), round(geom.height(r), 1), layer)] += 1
    for (w, h, layer), n in sorted(left.items(), key=lambda kv: -kv[1]):
        ex.leftovers.append(f"{n} x rectangle {w:.1f} x {h:.1f} m on layer {layer}")
    return ex


def _assign_unique(items: list[dict], texts, radius: float) -> None:
    """Text <-> item, each used once, nearest pairs first."""
    pairs = []
    for i, it in enumerate(items):
        cx, cy = geom.centre(it["rect"])
        for k, t in enumerate(texts):
            d = (t[1] - cx) ** 2 + (t[2] - cy) ** 2
            if d <= radius ** 2:
                pairs.append((d, i, t[0].upper().replace(" ", ""), k))
    pairs.sort()
    taken_item, taken_text = set(), set()
    for d, i, name, k in pairs:
        if i in taken_item or k in taken_text:
            continue
        items[i]["tag"] = name
        taken_item.add(i)
        taken_text.add(k)


def _fronts(racks: list[dict], texts) -> None:
    """A FRONT (or BACK) text within a third of a cabinet of one of its faces
    says which way the cabinet looks: +x, -x, +y or -y."""
    marks = [(t[0].strip().upper(), t[1], t[2]) for t in texts if t[0].strip().upper() in FRONT_WORDS + BACK_WORDS]
    if not marks:
        return
    for rk in racks:
        r = rk["rect"]
        cx, cy = geom.centre(r)
        w, h = geom.width(r), geom.height(r)
        best, bd = None, None
        for word, x, y in marks:
            dx, dy = x - cx, y - cy
            # the text sits on (or just outside) one face: the dominant offset, normalised by the half-size
            fx, fy = dx / (w / 2), dy / (h / 2)
            if abs(fx) > abs(fy):
                face, d = ("+x" if fx > 0 else "-x"), abs(fx)
                off = abs(dy) / (h / 2)
            else:
                face, d = ("+y" if fy > 0 else "-y"), abs(fy)
                off = abs(dx) / (w / 2)
            if 0.3 <= d <= 1.8 and off <= 1.1:
                score = abs(d - 1.0) + off
                if bd is None or score < bd:
                    best, bd = (word, face), score
        if best:
            word, face = best
            if word in BACK_WORDS:
                face = {"+x": "-x", "-x": "+x", "+y": "-y", "-y": "+y"}[face]
            rk["front"] = face


def _loads(racks: list[dict], texts, ex: Extracted) -> None:
    """The label inside (or within 1,5 m of) a cabinet: kW, kind, liquid."""
    cands = []
    for t in texts:
        s = t[0].upper()
        if not s.strip() or s.strip() in FRONT_WORDS + BACK_WORDS or TAG_RE.match(s.replace(" ", "")):
            continue
        if KW_RE.search(s) or any(w in s for w in ZERO_WORDS + FUTURE_WORDS) or "RACK" in s:
            cands.append((s, t[1], t[2]))
    for rk in racks:
        r = rk["rect"]
        cx, cy = geom.centre(r)
        hit = [c for c in cands if geom.contains_point(r, c[1], c[2], 0.02)]
        if not hit:
            near = sorted(((c[1] - cx) ** 2 + (c[2] - cy) ** 2, c) for c in cands)
            hit = [near[0][1]] if near and near[0][0] <= 1.5 ** 2 else []
        if not hit:
            continue
        s = " ".join(h[0] for h in hit)
        rk["label"] = re.sub(r"\s+", " ", s).strip()[:60]
        rk["liquid"] = "LIQUID" in s
        m_range = RANGE_RE.search(s)
        m_kw = KW_RE.search(s)
        if any(w in s for w in ZERO_WORDS):
            rk["load_kw"], rk["kind"] = 0.0, "ODF" if "ODF" in s else "PASSIVE"
        elif m_range or any(w in s for w in FUTURE_WORDS) or "SPARE" in s:
            rk["kind"] = "SPARE" if ("SPARE" in s or m_range) else "FUTURE"
            rk["load_kw"] = None
            rk["capacity_kw"] = float((m_range or m_kw).group(1).replace(",", ".")) if (m_range or m_kw) else None
        elif m_kw:
            rk["load_kw"] = float(m_kw.group(1).replace(",", "."))
            rk["kind"] = "NETWORK" if "NETWORK" in s else "IT"
