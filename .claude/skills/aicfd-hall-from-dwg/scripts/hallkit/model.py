"""The typed model: what AICFD will read, built from the extraction on the
reference's heights and rules, quantised to the mesh cell, with every
correction, quantisation and open question logged.

Coordinates in the model are LOCAL: origin at the envelope's min corner,
z = 0 on the slab (under the plenum when there is a raised floor). Every
rect is (x0, y0, x1, y1); every box adds (z0, z1); every panel is
{"axis": "x"|"y"|"z", "at": v, "lo", "hi", "z": (z0, z1)} or, for a
horizontal panel, {"at": z, "rect": (x0, y0, x1, y1)}.

Two containment arrangements, read from the drawing:

  COLD  cabinets face the drafter's box (or a band of floor tiles): a lid
        at rack height, a door at each end, tiles under it, the room is the
        hot side and the return grilles go over the hot bands behind the
        rows (as many as the tiles in front).
  HOT   cabinets turn their backs to the box (a liquid-cooling POD, a
        classic HAC): a chimney from the rack tops to the false ceiling
        over both rows' back lines, doors from the floor to the ceiling,
        the return grilles in the ceiling over the aisle, the room is the
        cold side.
"""
from __future__ import annotations

import math
import re
from collections import defaultdict

from . import geom
from .extract import Extracted

ROW_TAG_RE = re.compile(r"^([A-Z]{1,2})[- ]?(\d{1,3})$")


class Log:
    def __init__(self):
        self.corrections: list[str] = []
        self.quantisations: list[str] = []
        self.omissions: list[str] = []
        self.questions: list[str] = []
        self.notes: list[str] = []
        self.alerts: list[str] = []     # the mesh cell changed the layout: read before running


def _cells_that_carry(widths, choices=(0.3, 0.25, 0.2, 0.15, 0.1, 0.05), tol=0.01) -> list[float]:
    """The cell sizes, largest first, that every drawn width is a whole number
    of, to within 10 mm -- a 605 mm cabinet is a 600 mm one to any mesh."""
    return [c for c in choices if all(abs(w - max(1, round(w / c)) * c) <= tol + 1e-9 for w in widths)]


def _grid_alerts(rows, hall, cell, cfg, log) -> None:
    """What the mesh cell did to the layout, row by row, and why.

    NOTHING IS MOVED HERE. The pipeline lays every cabinet on the grid one
    width at a time, so a width the cell does not divide comes out a whole
    number of cells (0,8 m on 0,3 m is 0,9 m) and the row grows by the
    difference towards whatever is at its far end -- and two rows that start
    a few millimetres apart in the drawing can round to opposite sides of a
    grid line and end a whole cell out of line. Both are consequences of the
    cell, not of the drawing, so they are reported with their cause and the
    cell that would carry the drawing exactly, and the engineer decides.
    """
    limit = float(cfg.get("row_growth_alert_m", 0.2))
    xw, xe = hall[0], hall[2]
    drawn_widths = sorted({round(r["rect"][2] - r["rect"][0], 3) for row in rows for r in row["racks"]})
    carry = _cells_that_carry(drawn_widths)
    tip = (f"an x cell of {carry[0]:g} m carries every drawn width ({', '.join(f'{w:g}' for w in drawn_widths)} m) exactly"
           if carry else "no cell down to 0,05 m carries every drawn width exactly")
    for row in rows:
        d0 = min(r["rect"][0] for r in row["racks"])
        d1 = max(r["rect"][2] for r in row["racks"])
        m0, m1 = row["span"]
        grew = (m1 - m0) - (d1 - d0)
        if abs(grew) <= limit + 1e-9:
            continue
        moved = sorted({(round(r["rect"][2] - r["rect"][0], 3), round(r["q"][2] - r["q"][0], 3)) for r in row["racks"]
                        if abs((r["q"][2] - r["q"][0]) - (r["rect"][2] - r["rect"][0])) > 0.005})
        n_moved = sum(1 for r in row["racks"] if abs((r["q"][2] - r["q"][0]) - (r["rect"][2] - r["rect"][0])) > 0.005)
        why = ", ".join(f"{a:g} m cabinets are {b:g} m" for a, b in moved) or "the gaps between cabinets rounded"
        log.alerts.append(
            f"row {row['id']}: {d1 - d0:.2f} m of row in the drawing, {m1 - m0:.2f} m in the model ({grew * 1000:+.0f} mm). "
            f"The {cell[0]:g} m x cell cannot hold the widths: {why} ({n_moved} of {len(row['racks'])}). "
            f"Clearance to the west wall {d0 - xw:.2f} -> {m0 - xw:.2f} m, to the east wall {xe - d1:.2f} -> {xe - m1:.2f} m. "
            f"Fix: {tip}")
    # rows that start (or end) together in the drawing and not in the model
    tol = float(cfg.get("row_alignment_tolerance_m", 0.05))
    for edge, name in ((0, "start"), (2, "end")):
        seen = []
        for row in rows:
            d = (min if edge == 0 else max)(r["rect"][edge] for r in row["racks"])
            m = row["span"][0 if edge == 0 else 1]
            seen.append((d, m, row["id"]))
        seen.sort()
        groups, cur = [], [seen[0]] if seen else []
        for item in seen[1:]:
            if item[0] - cur[-1][0] <= tol:
                cur.append(item)
            else:
                groups.append(cur)
                cur = [item]
        if cur:
            groups.append(cur)
        for g in groups:
            ms = sorted({round(m, 3) for _, m, _ in g})
            if len(g) < 2 or len(ms) < 2:
                continue
            spread_d = (max(d for d, _, _ in g) - min(d for d, _, _ in g)) * 1000
            by = {m: [rid for _, mm, rid in g if round(mm, 3) == m] for m in ms}
            log.alerts.append(
                f"rows {', '.join(rid for _, _, rid in g)} {name} within {spread_d:.0f} mm of each other in the drawing and "
                f"{(ms[-1] - ms[0]) * 1000:.0f} mm apart in the model (" + "; ".join(f"{'/'.join(v)} at x = {k:.2f}" for k, v in by.items()) +
                f"): their drawn {name}s fall on either side of a {cell[0]:g} m grid line and round apart. Nothing is realigned. "
                f"A finer x cell moves the grid line; {tip}")


class Quantiser:
    def __init__(self, cell, log: Log):
        self.cell = cell
        self.log = log

    def q(self, v: float, axis: int, what: str | None = None) -> float:
        c = self.cell[axis]
        out = round(round(v / c) * c, 6)
        if what and abs(out - v) > 0.001:
            self.log.quantisations.append(f"{what} {'xyz'[axis]} {v:.3f} -> {out:.3f} ({(out - v) * 1000:+.0f} mm)")
        return out

    def size(self, v: float, axis: int, what: str | None = None) -> float:
        """A length quantised to at least one cell."""
        c = self.cell[axis]
        out = round(max(1, round(v / c)) * c, 6)
        if what and abs(out - v) > 0.001:
            self.log.quantisations.append(f"{what} {'xyz'[axis]} length {v:.3f} -> {out:.3f} ({(out - v) * 1000:+.0f} mm)")
        return out


def _merge(ref: dict, answers: dict) -> dict:
    """answers override the reference, key by key, one level down."""
    out = {}
    for k, v in ref.items():
        out[k] = dict(v) if isinstance(v, dict) else v
    for k, v in (answers or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            merged = dict(out[k])
            for kk, vv in v.items():
                if isinstance(vv, dict) and isinstance(merged.get(kk), dict):
                    merged[kk] = {**merged[kk], **vv}
                else:
                    merged[kk] = vv
            out[k] = merged
        else:
            out[k] = v
    return out


def _sign(face: str | None) -> int | None:
    return None if face is None else (+1 if face == "+y" else -1 if face == "-y" else None)


def build(ex: Extracted, ref: dict, answers: dict) -> dict:
    cfg = _merge(ref, answers)
    log = Log()
    cell = [float(c) for c in cfg["mesh"]["cell_size"]]
    Q = Quantiser(cell, log)
    ox, oy = ex.envelope[0], ex.envelope[1]
    tol = float(cfg.get("tolerance_m", 0.02))

    def loc(r):
        return (r[0] - ox, r[1] - oy, r[2] - ox, r[3] - oy)

    # ---- heights ---------------------------------------------------------
    # THE ELEVATIONS ARE THE USER'S TO GIVE. The reference supplies a figure
    # for each so the model can be built and looked at, but every height
    # answers.yaml does not carry is an open question in the report until
    # the section drawing or the site confirms it.
    H = cfg["heights"]
    given = (answers or {}).get("heights") or {}
    H_WHAT = {"plenum": "raised floor depth (slab to deck; 0 = no raised floor)", "rack": "cabinet height",
              "unit": "cooling unit height", "ceiling": "floor to false ceiling", "hall": "floor to slab"}
    for key, what in H_WHAT.items():
        if key not in given:
            log.questions.append(f"height `{key}` ({what}): {H[key]} m taken from the reference -- confirm or give the drawing's figure (`heights.{key}`)")
    deck = Q.q(float(H["plenum"]), 2, "deck")
    has_deck = deck > 1e-9
    floor = deck                                    # the level cabinets stand on
    rack_top = Q.q(floor + float(H["rack"]), 2, "rack top")
    unit_top = Q.q(floor + float(H["unit"]), 2, "unit top")
    ceiling = Q.q(floor + float(H["ceiling"]), 2, "false ceiling")
    slab = Q.q(floor + float(H["hall"]), 2, "slab")
    if not (floor <= rack_top < ceiling < slab) or unit_top > ceiling:
        raise SystemExit(f"heights do not stack: floor {floor}, rack top {rack_top}, unit top {unit_top}, ceiling {ceiling}, slab {slab}")

    # ---- envelope, walls, galleries, hall ----------------------------------
    env = loc(ex.envelope)
    W = Q.q(env[2], 0, "envelope width")
    L = Q.q(env[3], 1, "envelope length")
    walls = []
    for w in ex.walls:
        r = loc(w["rect"])
        face = r[2] if w["side"] == "W" else r[0]        # the hall-side face
        at = Q.q(face, 0, f"dividing wall {w['side']} (hall-side face)")
        walls.append({"id": f"gallery_{w['side'].lower()}", "side": w["side"], "at": at, "thickness": geom.width(r)})
        log.corrections.append(
            f"dividing wall {w['side']}: {geom.width(r) * 1000:.0f} mm thick in the drawing, carried as a panel on its "
            f"hall-side face at x = {at:.2f}; the gallery takes the thickness")
    xw = max([w["at"] for w in walls if w["side"] == "W"] + [0.0])
    xe = min([w["at"] for w in walls if w["side"] == "E"] + [W])
    hall = (xw, 0.0, xe, L)
    galleries = []
    if any(w["side"] == "W" for w in walls):
        galleries.append({"id": "W", "rect": (0.0, 0.0, xw, L)})
    if any(w["side"] == "E" for w in walls):
        galleries.append({"id": "E", "rect": (xe, 0.0, W, L)})

    # ---- rows: bands of cabinets sharing a y band, split into segments at gaps ----
    racks = [dict(r, rect=loc(r["rect"])) for r in ex.racks]
    racks.sort(key=lambda r: (r["rect"][1], r["rect"][0]))
    bands: list[list[dict]] = []
    for r in racks:
        for b in bands:
            if abs(b[0]["rect"][1] - r["rect"][1]) < 0.15 and abs(b[0]["rect"][3] - r["rect"][3]) < 0.15:
                b.append(r)
                break
        else:
            bands.append([r])
    bands.sort(key=lambda b: -b[0]["rect"][1])   # from the north (top of the plan) down
    split_gap = float(cfg.get("row_split_gap_m", 1.0))
    early_boxes = [loc(b) for b in ex.aisle_boxes]
    rows: list[dict] = []
    band_rows: list[list[dict]] = []
    for b in bands:
        b.sort(key=lambda r: r["rect"][0])
        # A ROW IS ONE POD'S WORTH: where the drafter drew the containment, the
        # cabinets under one box's span are one row, whatever gaps the row has
        # (blanking panels close them); a cabinet under no box splits by gap.
        by_box: dict = {}
        loose: list[list[dict]] = []
        for r in b:
            cx = (r["rect"][0] + r["rect"][2]) / 2
            cy = (r["rect"][1] + r["rect"][3]) / 2
            hit = next((i for i, bx in enumerate(early_boxes) if bx[0] - 0.3 <= cx <= bx[2] + 0.3
                        and (abs(bx[1] - r["rect"][3]) < 0.3 or abs(bx[3] - r["rect"][1]) < 0.3)), None)
            if hit is not None:
                by_box.setdefault(hit, []).append(r)
            elif loose and r["rect"][0] - loose[-1][-1]["rect"][2] <= split_gap:
                loose[-1].append(r)
            else:
                loose.append([r])
        segs = sorted(list(by_box.values()) + loose, key=lambda seg: seg[0]["rect"][0])
        here = []
        for seg in segs:
            prefixes = {ROW_TAG_RE.match(r["tag"]).group(1) if r["tag"] and ROW_TAG_RE.match(r["tag"]) else None for r in seg}
            row = {"racks": seg, "prefix": prefixes.pop() if len(prefixes) == 1 else None, "band_index": len(band_rows)}
            rows.append(row)
            here.append(row)
        band_rows.append(here)
    seen = defaultdict(int)
    for row in rows:
        seen[row["prefix"]] += 1
    n_named = 0
    for row in rows:
        keep = row["prefix"] is not None and seen[row["prefix"]] == 1 and all(r["tag"] for r in row["racks"]) \
            and len({r["tag"] for r in row["racks"]}) == len(row["racks"])
        if keep:
            row["id"] = row["prefix"]
            for r in row["racks"]:
                r["id"] = r["tag"]
        else:
            n_named += 1
            row["id"] = f"F{n_named}"
            for k, r in enumerate(row["racks"], start=1):
                r["id"] = f"F{n_named}-{k:02d}"
            why = "no tags" if not any(r["tag"] for r in row["racks"]) else \
                f"tags {row['racks'][0]['tag']}.. are shared with another row or repeat"
            log.corrections.append(f"row {row['id']}: {why}; named {row['racks'][0]['id']}..{row['racks'][-1]['id']} from the north, west to east")

    # ---- the drafter's aisle boxes, and which way the cabinets look ----------
    boxes = [loc(b) for b in ex.aisle_boxes]
    box_layers = list(ex.aisle_box_layers) + [""] * (len(boxes) - len(ex.aisle_box_layers))
    box_kind_hint = "box"
    if not boxes and ex.tile_rects:
        ys = sorted({(round(t[1] - oy, 3), round(t[3] - oy, 3)) for t in ex.tile_rects})
        groups = []
        for y0, y1 in ys:
            if groups and y0 <= groups[-1][1] + 0.05:
                groups[-1] = (groups[-1][0], max(groups[-1][1], y1))
            else:
                groups.append((y0, y1))
        xs = [(t[0] - ox, t[2] - ox) for t in ex.tile_rects]
        boxes = [(min(x[0] for x in xs), g[0], max(x[1] for x in xs), g[1]) for g in groups]
        box_kind_hint = "tiles"
        box_layers = [""] * len(boxes)
        log.notes.append(f"aisles read from {len(ex.tile_rects)} floor tiles drawn in {len(boxes)} bands: cold aisles")

    def touches(row, box, side):
        """The box lies against the row's face on `side` (+1 above, -1 below) and overlaps it along x."""
        rb = row_rect(row)
        if side > 0:
            near = abs(box[1] - rb[3]) < 0.3
        else:
            near = abs(box[3] - rb[1]) < 0.3
        return near and min(rb[2], box[2]) - max(rb[0], box[0]) > 0.5

    def row_rect(row):
        return (min(r["rect"][0] for r in row["racks"]), row["racks"][0]["rect"][1],
                max(r["rect"][2] for r in row["racks"]), row["racks"][0]["rect"][3])

    # each row: the cabinets' own front marks (FRONT/BACK texts), by majority
    hints = (answers or {}).get("fronts") or {}
    for row in rows:
        marks = [r.get("front") for r in row["racks"] if r.get("front") in ("+y", "-y")]
        row["hint"] = None
        if row["id"] in hints:
            row["hint"] = "+y" if str(hints[row["id"]]).strip() in ("+y", "n", "N", "north") else "-y"
        elif marks:
            row["hint"] = max(set(marks), key=marks.count)
            if len(set(marks)) > 1:
                log.corrections.append(f"row {row['id']}: FRONT marks disagree ({marks.count('+y')} north, {marks.count('-y')} south); majority taken")

    # each box: the rows against it, and the kind (cold if fronts face it, hot if backs do)
    mode_cfg = str(cfg["containment"].get("aisle", "auto"))
    aisles = []
    for k, box in enumerate(boxes):
        below = [r for r in rows if touches(r, box, +1)]   # rows whose top face touches the box's bottom
        above = [r for r in rows if touches(r, box, -1)]
        if not below and not above:
            log.omissions.append(f"containment box at ({box[0]:.1f}, {box[1]:.1f})..({box[2]:.1f}, {box[3]:.1f}) touches no row: left out")
            continue
        votes = []
        for r in below:
            if r["hint"]:
                votes.append("cold" if r["hint"] == "+y" else "hot")
        for r in above:
            if r["hint"]:
                votes.append("cold" if r["hint"] == "-y" else "hot")
        # THE KIND OF THE BOX, by the strongest evidence first: what the user
        # answered (the BoD says HOT AISLE CONTAINMENT), then the layer the
        # drafter drew it on (AC HAC / CAC), then the cabinets' own marks. A
        # box the marks disagree about is a drafting slip (a POD block
        # mirrored with its FRONT/BACK texts left in place): the rows that
        # contradict the kind are turned round and the report says so.
        layer_hint = "hot" if re.search(r"HAC|HOT", box_layers[k], re.I) else ("cold" if re.search(r"CAC|COLD", box_layers[k], re.I) else None)
        if box_kind_hint == "tiles":
            kind, why = "cold", "floor tiles"
        elif mode_cfg in ("cold", "hot"):
            kind, why = mode_cfg, "answers.yaml `containment.aisle`"
        elif layer_hint:
            kind, why = layer_hint, f"layer '{box_layers[k]}'"
        elif votes:
            kind, why = max(set(votes), key=votes.count), "the cabinets' FRONT/BACK marks"
            if len(set(votes)) > 1:
                raise SystemExit(f"containment box {k + 1}: some cabinets face it and some turn their backs, and neither the layer nor "
                                 f"answers.yaml says which it is -- give `containment.aisle: cold|hot`")
        else:
            raise SystemExit("the drawing has containment boxes but no FRONT/BACK marks: say which kind they are with `containment.aisle: cold|hot` in answers.yaml")
        if votes and len(set(votes)) > 1:
            log.corrections.append(f"containment box {k + 1}: the FRONT/BACK marks disagree between its two rows; taken as {kind.upper()} by {why}")
        # rows whose marks contradict the kind are turned round
        for r, toward in [(r, "+y") for r in below] + [(r, "-y") for r in above]:
            want = toward if kind == "cold" else ("-y" if toward == "+y" else "+y")
            if r["hint"] and r["hint"] != want:
                log.corrections.append(f"row {r['id']}: its FRONT/BACK marks say the cabinets {'face' if kind == 'hot' else 'turn their backs to'} "
                                       f"the {kind} aisle; turned round to match the box ({why}) -- a mirrored POD block, most likely")
                r["hint"] = want
            elif not r["hint"]:
                r["hint"] = want
        aisles.append({"box": box, "kind": kind, "below": below, "above": above, "why": why})
    kinds = {a["kind"] for a in aisles}
    if len(kinds) > 1:
        raise SystemExit("cold and hot contained aisles in one hall: AICFD builds one arrangement per hall")
    containment = kinds.pop() if kinds else (mode_cfg if mode_cfg in ("cold", "hot") else "cold")

    # each row's front: its own mark, else from the aisle it stands against
    for row in rows:
        if row["hint"]:
            row["front"] = _sign(row["hint"])
            continue
        sides = []
        for a in aisles:
            if row in a["below"]:
                sides.append(+1 if a["kind"] == "cold" else -1)
            if row in a["above"]:
                sides.append(-1 if a["kind"] == "cold" else +1)
        if len(set(sides)) == 1:
            row["front"] = sides[0]
        elif len(set(sides)) > 1:
            raise SystemExit(f"row {row['id']} stands between two aisles of the same kind; give `fronts: {{{row['id']}: +y|-y}}`")
        else:
            row["front"] = None
            log.questions.append(f"row {row['id']}: no containment box or FRONT mark on either side -- which way do its cabinets face? (`fronts: {{{row['id']}: +y|-y}}`)")
    if any(r["front"] is None for r in rows):
        raise SystemExit("\n".join(log.questions))

    # ---- quantise the rows: y from the north down, aisles on the tile module for cold aisles ----
    module = float(cfg["tiles"]["size"])
    aisle_max = float(cfg.get("aisle_max_m", 3.0))
    prev_band = None
    for bi, here in enumerate(band_rows):
        first = here[0]["racks"][0]["rect"]
        dy = Q.size(first[3] - first[1], 1, f"row {here[0]['id']} depth")
        if prev_band is None:
            y0 = Q.q(first[1], 1, f"row {here[0]['id']}")
        else:
            gap = prev_band["y0"] - first[3]
            # A COLD AISLE IS A MULTIPLE OF THE 0,6 m MODULE (1,2 / 1,8 / 2,4 ...): the
            # tiles are 600 mm and so are the return grilles, so a drawing that says
            # 1,77 m is corrected and told so. A hot aisle keeps its drawn width on
            # the grid (a 2,20 m POD aisle stays 2,20).
            between_cold = containment == "cold" and gap <= aisle_max
            if between_cold:
                snapped = Q.q(max(module, round(gap / module) * module), 1)
                y0 = round(prev_band["y0"] - snapped - dy, 6)
                if abs(gap - snapped) > tol:
                    log.corrections.append(
                        f"aisle between {prev_band['id']} and {here[0]['id']}: {gap:.3f} m in the drawing is not a multiple of the "
                        f"{module:.1f} m module; carried as {snapped:.1f} m (row moved {(y0 - first[1]) * 1000:+.0f} mm)")
                elif abs(y0 - first[1]) > 0.001:
                    log.quantisations.append(f"row {here[0]['id']} y {first[1]:.3f} -> {y0:.3f} ({(y0 - first[1]) * 1000:+.0f} mm)")
            else:
                y0 = Q.q(first[1], 1, f"row {here[0]['id']}")
        if y0 < hall[1] - 1e-6:
            raise SystemExit(f"row {here[0]['id']} lands outside the hall after quantisation (y {y0:.2f})")
        for row in here:
            x = Q.q(row["racks"][0]["rect"][0], 0, f"row {row['id']} start")
            for i, r in enumerate(row["racks"]):
                gap = r["rect"][0] - row["racks"][i - 1]["rect"][2] if i else 0.0
                if gap > 0.1:
                    x += Q.q(gap, 0, f"rack {r['id']} gap before")
                    log.notes.append(f"rack {r['id']}: {gap:.2f} m of air before it in the row")
                w = Q.size(r["rect"][2] - r["rect"][0], 0, f"rack {r['id']} width")
                r["q"] = (round(x, 6), round(y0, 6), round(x + w, 6), round(y0 + dy, 6))
                x = round(x + w, 6)
            row["band"] = (y0, round(y0 + dy, 6))
            row["span"] = (row["racks"][0]["q"][0], row["racks"][-1]["q"][2])
            if row["span"][1] > xe + 1e-6 or row["span"][0] < xw - 1e-6:
                raise SystemExit(f"row {row['id']} runs outside the hall after quantisation ({row['span']})")
        prev_band = {"y0": y0, "id": here[0]["id"]}
    _grid_alerts(rows, hall, cell, cfg, log)

    # ---- the cage: panels from a rectangle or from segments ------------------
    cage_panels = []
    if ex.cage_rect is not None:
        c = loc(ex.cage_rect)
        for side, axis, at, lo, hi, hall_face in (("w", "x", c[0], c[1], c[3], hall[0]), ("e", "x", c[2], c[1], c[3], hall[2]),
                                                  ("s", "y", c[1], c[0], c[2], hall[1]), ("n", "y", c[3], c[0], c[2], hall[3])):
            if abs(at - hall_face) > 0.05:
                cage_panels.append({"id": side, "axis": axis, "at": at, "lo": lo, "hi": hi})
            else:
                log.notes.append(f"cage side {side} coincides with the hall wall: closed by the wall itself")
    for p, q in ex.cage_segments:
        p, q = (p[0] - ox, p[1] - oy), (q[0] - ox, q[1] - oy)
        if abs(p[0] - q[0]) < tol:
            cage_panels.append({"id": f"x{len(cage_panels) + 1}", "axis": "x", "at": p[0], "lo": min(p[1], q[1]), "hi": max(p[1], q[1])})
        elif abs(p[1] - q[1]) < tol:
            cage_panels.append({"id": f"y{len(cage_panels) + 1}", "axis": "y", "at": p[1], "lo": min(p[0], q[0]), "hi": max(p[0], q[0])})
        else:
            log.omissions.append(f"cage segment {p} -> {q} is not on an axis: left out")
    for p in cage_panels:
        a = 0 if p["axis"] == "x" else 1
        b = 1 - a
        p["at"] = Q.q(p["at"], a, f"cage panel {p['id']}")
        p["lo"] = Q.q(p["lo"], b, f"cage panel {p['id']} start")
        p["hi"] = Q.q(p["hi"], b, f"cage panel {p['id']} end")
        p["z"] = (floor, ceiling)
        for row in rows:
            for r in row["racks"]:
                rq = r["q"]
                if p["axis"] == "y" and rq[1] < p["at"] < rq[3] and rq[0] < p["hi"] and rq[2] > p["lo"]:
                    raise SystemExit(f"cage panel {p['id']} at y = {p['at']} crosses rack {r['id']}")
                if p["axis"] == "x" and rq[0] < p["at"] < rq[2] and rq[1] < p["hi"] and rq[3] > p["lo"]:
                    raise SystemExit(f"cage panel {p['id']} at x = {p['at']} crosses rack {r['id']}")
    cage_lines_y = sorted(p["at"] for p in cage_panels if p["axis"] == "y")

    # ---- the contained aisles and the open bands -------------------------------
    # With boxes, every contained aisle is one box: the rows against it, its
    # band between their faces. Without boxes (no drawing of the containment
    # at all), consecutive bands facing each other make one.
    contained = []
    if aisles:
        for a in aisles:
            rows_in = a["below"] + a["above"]
            lo_rows, hi_rows = a["below"], a["above"]
            y_lo = max(r["band"][1] for r in lo_rows) if lo_rows else None
            y_hi = min(r["band"][0] for r in hi_rows) if hi_rows else None
            if y_lo is None:
                y_lo = Q.q(a["box"][1], 1, "aisle far side")
            if y_hi is None:
                y_hi = Q.q(a["box"][3], 1, "aisle far side")
            contained.append({"rows": rows_in, "band": (y_lo, y_hi), "lone": not (lo_rows and hi_rows),
                              "far": ("n" if not hi_rows else "s") if not (lo_rows and hi_rows) else None,
                              "kind": a["kind"], "box": a["box"], "_below": lo_rows, "_above": hi_rows})
    else:
        rows_s = sorted(rows, key=lambda r: r["band"][0])
        for i, row in enumerate(rows_s):
            nxt = rows_s[i + 1] if i + 1 < len(rows_s) else None
            if nxt is None or any(row["band"][1] < cy < nxt["band"][0] for cy in cage_lines_y):
                continue
            facing = (row["front"] == +1 and nxt["front"] == -1)
            backing = (row["front"] == -1 and nxt["front"] == +1)
            if (facing and containment == "cold") or (backing and containment == "hot"):
                contained.append({"rows": [row, nxt], "band": (row["band"][1], nxt["band"][0]), "lone": False, "far": None,
                                  "kind": containment, "box": None, "_below": [row], "_above": [nxt]})

    # the open bands: every strip between/around rows that is not a contained aisle
    open_bands = []
    rows_s = sorted(rows, key=lambda r: (r["band"][0], r["span"][0]))
    ys = sorted({r["band"][0] for r in rows} | {r["band"][1] for r in rows})

    def obstacle_above(y):
        return min([cy for cy in cage_lines_y if cy > y + 1e-6] + [hall[3]])

    def obstacle_below(y):
        return max([cy for cy in cage_lines_y if cy < y - 1e-6] + [hall[1]])

    for row in rows:
        for side in (+1, -1):
            face_y = row["band"][1] if side > 0 else row["band"][0]
            in_aisle = any(row in c["rows"] and (abs(c["band"][0] - face_y) < 1e-6 if side > 0 else abs(c["band"][1] - face_y) < 1e-6)
                           for c in contained)
            if in_aisle:
                continue
            # the band from this face to the next row's face, cage line or wall over this row's span
            if side > 0:
                cands = [r["band"][0] for r in rows if r is not row and r["band"][0] >= face_y - 1e-6
                         and min(r["span"][1], row["span"][1]) - max(r["span"][0], row["span"][0]) > 0.5]
                far = min(cands + [obstacle_above(face_y)])
                band = (face_y, far)
            else:
                cands = [r["band"][1] for r in rows if r is not row and r["band"][1] <= face_y + 1e-6
                         and min(r["span"][1], row["span"][1]) - max(r["span"][0], row["span"][0]) > 0.5]
                far = max(cands + [obstacle_below(face_y)])
                band = (far, face_y)
            partner = [r for r in rows if r is not row and (abs(r["band"][0] - far) < 1e-6 if side > 0 else abs(r["band"][1] - far) < 1e-6)
                       and min(r["span"][1], row["span"][1]) - max(r["span"][0], row["span"][0]) > 0.5]
            key = (round(band[0], 6), round(band[1], 6), round(row["span"][0], 6))
            existing = next((ob for ob in open_bands if abs(ob["band"][0] - band[0]) < 1e-6 and abs(ob["band"][1] - band[1]) < 1e-6
                             and min(ob["span"][1], row["span"][1]) - max(ob["span"][0], row["span"][0]) > 0.5), None)
            hot = row["front"] != side   # the band behind a row is hot, the band its front looks at is cold
            if existing:
                if row not in existing["rows"]:
                    existing["rows"].append(row)
                existing["span"] = (min(existing["span"][0], row["span"][0]), max(existing["span"][1], row["span"][1]))
                continue
            open_bands.append({"rows": [row] + partner, "band": band, "span": row["span"], "hot": hot, "paired": bool(partner)})

    # ---- build the contained aisles ------------------------------------------------
    T = cfg["tiles"]
    tile = float(T["size"])
    lids, doors, closures, tiles, blanks, chimneys, grilles = [], [], [], [], [], [], []
    G = cfg["return_grilles"]
    gsize = float(G["size"])
    for k, ca in enumerate(contained, start=1):
        ca["id"] = f"{'c' if ca['kind'] == 'cold' else 'h'}{k}"
        spans = [r["span"] for r in ca["rows"]]
        union = (min(s[0] for s in spans), max(s[1] for s in spans))
        band = ca["band"]
        if ca["kind"] == "cold":
            # THE FLOOR AND THE CEILING HOLD THE SAME COUNT, and both are at least as
            # long as the rows: ceil(row / 0,6) tiles along, centred on the rows.
            length = union[1] - union[0]
            n_along = math.ceil(length / tile - 1e-9)
            x_start = Q.q(union[0] - (n_along * tile - length) / 2, 0)
            x_start = min(max(x_start, hall[0]), hall[2] - n_along * tile)
            span = (round(x_start, 6), round(x_start + n_along * tile, 6))
            z_top = rack_top
        else:
            span = (Q.q(union[0], 0), Q.q(union[1], 0))
            z_top = ceiling
        if ca["lone"]:
            far = band[1] if ca["far"] == "n" else band[0]
            closures.append({"id": f"{ca['id']}_{ca['far']}", "axis": "y", "at": far, "lo": span[0], "hi": span[1], "z": (floor, z_top)})
            log.corrections.append(
                f"{'cold' if ca['kind'] == 'cold' else 'hot'} aisle {ca['id']}: row {ca['rows'][0]['id']} stands against it alone; its "
                f"{'north' if ca['far'] == 'n' else 'south'} side is closed with a vertical panel at y = {far:.1f}")
        ca["span"] = span
        w = band[1] - band[0]
        if ca["kind"] == "cold":
            lids.append({"id": ca["id"], "at": rack_top, "rect": (span[0], band[0], span[1], band[1])})
        else:
            # THE CHIMNEY: a panel over each row's back line from the rack tops to the
            # false ceiling, the length of the aisle; the doors run floor to ceiling.
            for side_rows, back, tag in ((ca["_below"], band[0], "s"), (ca["_above"], band[1], "n")):
                if side_rows:
                    chimneys.append({"id": f"{ca['id']}_{tag}", "axis": "y", "at": back, "lo": span[0], "hi": span[1], "z": (rack_top, ceiling)})
        # A DOOR ON THE HALL'S WALL IS THE WALL. Where the containment runs up
        # to the dividing wall (a strip that grew to the next module), the wall
        # closes that end already, and a door in the same plane would claim the
        # same mesh faces twice.
        for tag, at in (("w", span[0]), ("e", span[1])):
            if abs(at - hall[0]) < 1e-6 or abs(at - hall[2]) < 1e-6:
                log.notes.append(f"aisle {ca['id']}: its {'west' if tag == 'w' else 'east'} end reaches the hall's wall at x = {at:.1f}; "
                                 f"the wall closes it and no door is written")
                continue
            doors.append({"id": f"{ca['id']}_{tag}", "axis": "x", "at": at, "lo": band[0], "hi": band[1], "z": (floor, z_top)})
        # blank panels: every part of the aisle's length along each SIDE's row line
        # with no cabinet on it (all the rows on that side together)
        for side_rows, face in ((ca["_below"], band[0]), (ca["_above"], band[1])):
            if not side_rows:
                continue
            covered = sorted((r["q"][0], r["q"][2]) for row in side_rows for r in row["racks"])
            x = span[0]
            n_b = 0
            names = "/".join(r["id"] for r in side_rows)
            for x0, x1 in covered + [(span[1], span[1])]:
                if x0 - x > 1e-6:
                    n_b += 1
                    blanks.append({"id": f"{names.replace('/', '_')}_{n_b}", "row": names, "axis": "y", "at": face,
                                   "lo": round(x, 6), "hi": round(min(x0, span[1]), 6), "z": (floor, rack_top)})
                x = max(x, x1)
            if n_b:
                total = sum(b["hi"] - b["lo"] for b in blanks if b["row"] == names)
                log.corrections.append(
                    f"aisle {ca['id']}: the containment runs {span[1] - span[0]:.1f} m and row {names} covers "
                    f"{sum(r['span'][1] - r['span'][0] for r in side_rows):.1f} m; {total:.1f} m of the row line closed with {n_b} blank panel(s)")
        if ca["kind"] == "cold":
            across = int(T["across"]) if str(T["across"]).isdigit() else min(3, max(1, int(w / tile + 1e-9)))
            start = min(max(Q.q(band[0] + (w - across * tile) / 2, 1), band[0]), band[1] - across * tile)
            for i in range(n_along):
                for j in range(across):
                    x0 = round(span[0] + i * tile, 6)
                    y0 = round(start + j * tile, 6)
                    tiles.append({"id": f"{ca['id']}-{i + 1:02d}-{j + 1}", "aisle": ca["id"], "rect": (x0, y0, round(x0 + tile, 6), round(y0 + tile, 6))})
            ca["tiles_across"], ca["tiles_along"] = across, n_along
            for row in ca["rows"]:
                row["cold_aisle"], row["cold_tiles"] = ca["id"], across * n_along
        else:
            # the return grilles sit in the ceiling over the hot aisle, filling it
            across = int(G["across"]) if str(G.get("across", "auto")).isdigit() else max(1, int(w / gsize + 1e-9))
            n_along = int((span[1] - span[0]) / gsize + 1e-9)
            start = min(max(Q.q(band[0] + (w - across * gsize) / 2, 1), band[0]), band[1] - across * gsize)
            for i in range(n_along):
                for j in range(across):
                    x0 = round(span[0] + i * gsize, 6)
                    y0 = round(start + j * gsize, 6)
                    grilles.append({"id": f"{ca['id']}-{i + 1:02d}-{j + 1}", "band": ca["id"], "rect": (x0, y0, round(x0 + gsize, 6), round(y0 + gsize, 6))})
            ca["grilles"] = {"count": n_along * across, "from": "chimney", "against": "/".join(r["id"] for r in ca["rows"])}
            ca["tiles_across"], ca["tiles_along"] = 0, 0

    # ---- the open bands: return grilles behind the rows of a cold-contained hall ----
    hot_bands = []
    if containment == "cold":
        mode = str(G.get("mode", "match_tiles"))
        for k, hb in enumerate([ob for ob in open_bands if ob["hot"]], start=1):
            hb["id"] = f"h{k}"
            band = hb["band"]
            w = band[1] - band[0]
            hb["grilles"] = {}
            strips_total = int(w / gsize + 1e-9)
            if strips_total == 0:
                log.notes.append(f"hot band {hb['id']} is {w:.2f} m wide: no return grille fits")
                hot_bands.append(hb)
                continue
            if mode == "fill":
                across = int(G["across"]) if str(G.get("across", "auto")).isdigit() else min(3, strips_total)
                start = min(max(Q.q(band[0] + (w - across * gsize) / 2, 1), band[0]), band[1] - across * gsize)
                n_along = int((hb["span"][1] - hb["span"][0]) / gsize + 1e-9)
                for i in range(n_along):
                    for j in range(across):
                        x0 = round(hb["span"][0] + i * gsize, 6)
                        y0 = round(start + j * gsize, 6)
                        grilles.append({"id": f"{hb['id']}-{i + 1:02d}-{j + 1}", "band": hb["id"], "rect": (x0, y0, round(x0 + gsize, 6), round(y0 + gsize, 6))})
                hb["grilles"] = {"count": n_along * across, "from": "fill", "against": "band"}
                hot_bands.append(hb)
                continue
            # EACH HOT AISLE TAKES THE COUNT OF THE COLD AISLE ACROSS THE ROW: as
            # many return grilles as that cold aisle has tiles, laid in contiguous
            # strips from the racks' back outward, sweeping the hall from the
            # north. What the band cannot hold is capped and reported.
            cold = [(r.get("cold_tiles", 0), r.get("cold_aisle")) for r in hb["rows"]]
            need, src = max(cold)
            if need == 0:
                hot_bands.append(hb)
                continue
            anchor = max(hb["rows"], key=lambda r: r["band"][1])
            upward = abs(anchor["band"][1] - band[0]) < 1e-6
            span = anchor["span"]
            length = span[1] - span[0]
            n_along = math.ceil(length / gsize - 1e-9)
            x_start = Q.q(span[0] - (n_along * gsize - length) / 2, 0)
            x_start = min(max(x_start, hall[0]), hall[2] - n_along * gsize)
            strips_needed = math.ceil(need / n_along) if n_along else 0
            strips_laid = min(strips_needed, strips_total)
            n = strips_laid * n_along
            if n < need:
                log.corrections.append(
                    f"hot band {hb['id']}: cold aisle {src} has {need} tiles, so at least {need} return grilles are asked for, "
                    f"and the {w:.1f} m band holds {strips_laid} strip(s) of {n_along}; {n} laid")
            placed = 0
            for j in range(strips_laid):
                y0 = round(band[0] + j * gsize, 6) if upward else round(band[1] - (j + 1) * gsize, 6)
                for i in range(n_along):
                    x0 = round(x_start + i * gsize, 6)
                    grilles.append({"id": f"{hb['id']}-{j + 1}-{i + 1:02d}", "band": hb["id"], "rect": (x0, y0, round(x0 + gsize, 6), round(y0 + gsize, 6))})
                    placed += 1
            hb["grilles"] = {"count": placed, "from": src, "against": anchor["id"]}
            gap = w - strips_laid * gsize
            if gap > 1e-6:
                log.notes.append(f"hot band {hb['id']}: {strips_laid} strip(s) of grilles against {anchor['id']}'s back, {gap:.1f} m without grilles on the far side")
            hot_bands.append(hb)
    else:
        # a hot-contained hall: the room is the cold side. With a raised floor the
        # tiles lie in front of each row, two across, along the row.
        if has_deck:
            for k, cb in enumerate([ob for ob in open_bands if not ob["hot"]], start=1):
                cb["id"] = f"c{k}"
                for row in cb["rows"]:
                    face = row["band"][1] if row["front"] > 0 else row["band"][0]
                    n_along = int((row["span"][1] - row["span"][0]) / tile + 1e-9)
                    across = int(T["across"]) if str(T["across"]).isdigit() else 2
                    across = min(across, max(1, int((cb["band"][1] - cb["band"][0]) / tile + 1e-9)))
                    for i in range(n_along):
                        for j in range(across):
                            x0 = round(row["span"][0] + i * tile, 6)
                            y0 = round(face + j * tile, 6) if row["front"] > 0 else round(face - (j + 1) * tile, 6)
                            tiles.append({"id": f"{row['id']}-{i + 1:02d}-{j + 1}", "aisle": cb["id"], "rect": (x0, y0, round(x0 + tile, 6), round(y0 + tile, 6))})
        for ob in open_bands:
            ob["id"] = ob.get("id", "open")
            ob["grilles"] = {}

    # ---- units ---------------------------------------------------------------------
    unit_kind = str(cfg.get("units", {}).get("kind", "auto"))
    if unit_kind == "auto":
        unit_kind = "downflow" if has_deck else "fanwall"
    units = []
    for u in ex.units:
        r = loc(u["rect"])
        uid = u["tag"] or f"U{len(units) + 1:02d}"
        x0 = Q.q(r[0], 0, f"unit {uid}")
        y0 = Q.q(r[1], 1, f"unit {uid}")
        w = Q.size(geom.width(r), 0, f"unit {uid} depth")
        d = Q.size(geom.height(r), 1, f"unit {uid} width")
        z0 = floor if unit_kind == "downflow" else 0.0
        units.append({"id": uid, "gallery": u["gallery"], "kind": unit_kind,
                      "dir": None if unit_kind == "downflow" else ("+x" if u["gallery"] == "W" else "-x"),
                      "box": (x0, y0, z0, round(x0 + w, 6), round(y0 + d, 6), round(z0 + (unit_top - floor), 6))})
        if not u["tag"]:
            log.corrections.append(f"unit at ({r[0]:.2f}, {r[1]:.2f}) has no tag in the drawing: named {uid}")
    for u in units:
        g = next((g for g in galleries if g["id"] == u["gallery"]), None)
        if g is None or not geom.contains(g["rect"], (u["box"][0], u["box"][1], u["box"][3], u["box"][4]), 1e-6):
            raise SystemExit(f"unit {u['id']} does not lie inside gallery {u['gallery']} after quantisation")
    units.sort(key=lambda u: (u["gallery"] != "W", u["box"][1]))
    for k, u in enumerate(units, start=1):
        if u["id"].startswith("U") and u["id"][1:].isdigit():
            u["id"] = f"U{k:02d}"
    if not units:
        log.questions.append("no cooling unit of a known footprint found in the galleries / technical rooms: give the units' "
                             "plan size (`unit_plan_sizes_m: [[w, d]]`) or their positions, and the model (`fanwall.model`)")

    # ---- the supply plenum: a double wall between the technical room and the hall ----
    # THE UNITS ARE MOUNTED IN THE OUTER LEAF AND BLOW INTO THE CAVITY; the inner
    # leaf -- the hall's wall -- carries the supply grilles. Read off the drawing:
    # fan walls standing off the hall's wall, behind a second thin masonry leaf,
    # make the space between the two the supply plenum (AICFD `plenum.enabled`,
    # ADR-058). The dividing wall of the model moves to the units' leaf, the
    # cavity is added to the hall's air volume, and the hall's own wall becomes
    # `wall:plenum_<side>` with `supply:` panels in it. A downflow hall has none.
    PL = cfg.get("plenum", {}) or {}
    min_depth = float(PL.get("min_depth_m", 0.3))
    plenums, supplies = [], []
    leaves = [dict(lf, rect=loc(lf["rect"])) for lf in ex.leaves]
    if unit_kind == "fanwall":
        for w in walls:
            side = w["side"]
            us = [u for u in units if u["gallery"] == side]
            if not us:
                continue
            faces = sorted(round(u["box"][3] if side == "W" else u["box"][0], 6) for u in us)
            face = max(set(faces), key=faces.count)      # the leaf every unit is mounted in
            depth = (w["at"] - face) if side == "W" else (face - w["at"])
            if depth < min_depth:
                continue
            divider = face
            leaf = None
            for lf in leaves:
                if lf["side"] != side:
                    continue
                cav = lf["rect"][2] if side == "W" else lf["rect"][0]   # the leaf's cavity-side face
                if abs(cav - face) <= 0.35 and (leaf is None or abs(cav - face) < abs(leaf[1] - face)):
                    leaf = (lf, cav)
            if leaf is not None:
                divider = Q.q(leaf[1], 0, f"units' leaf {side}")
            inner = w["at"]
            depth = round(abs(divider - inner), 6)
            for u in us:
                uface = u["box"][3] if side == "W" else u["box"][0]
                if abs(uface - divider) > 1e-6:
                    dx = divider - uface
                    b = u["box"]
                    u["box"] = (round(b[0] + dx, 6), b[1], b[2], round(b[3] + dx, 6), b[4], b[5])
                    log.corrections.append(f"unit {u['id']}: drawn {abs(dx) * 1000:.0f} mm {'over the plenum' if (dx < 0) == (side == 'W') else 'short of its leaf'}; "
                                           f"placed against the units' leaf at x = {divider:.2f}")
            w["at"] = divider
            w["plenum"] = True
            for g in galleries:
                if g["id"] == side:
                    g["rect"] = (0.0, 0.0, divider, L) if side == "W" else (divider, 0.0, W, L)
            plenums.append({"id": f"plenum_{side.lower()}", "side": side, "wall_at": inner, "divider_at": divider, "depth": depth,
                            "leaf": leaf is not None})
            log.corrections.append(
                f"gallery {side}: DOUBLE WALL -- the units are mounted in a leaf at x = {divider:.2f} and the hall's wall is at "
                f"x = {inner:.2f}; the {depth:.1f} m between them is the SUPPLY PLENUM: the units blow into it and the air "
                f"enters the hall through supply grilles in the hall's wall" + ("" if leaf is not None else
                " (no second leaf drawn: the units' discharge face is taken as the dividing wall)"))
        if plenums and not [u for u in units if any(p["side"] == u["gallery"] for p in plenums)]:
            pass
    if len({round(p["depth"], 3) for p in plenums}) > 1:
        log.notes.append("the supply plenums are not the same depth on the two sides (" + ", ".join(f"{p['side']} {p['depth']:.1f} m" for p in plenums)
                         + "): each carried as drawn")
    if plenums:
        # THE SUPPLY GRILLES: the plan seldom draws them. One per cold aisle,
        # centred on it, `plenum.grille.width` wide and the cabinets' height
        # tall -- AICFD's own rule -- unless answers.yaml places them.
        PG = PL.get("grille", {}) or {}
        gw = float(PG.get("width", 2.0))
        gh = float(PG["height"]) if PG.get("height") else float(H["rack"])
        gh_q = Q.size(gh, 2, "supply grille height")
        placed = (answers or {}).get("plenum", {}).get("grilles") if isinstance((answers or {}).get("plenum"), dict) else None
        cold_bands = []
        if containment == "hot":
            for ob in open_bands:
                if not ob["hot"] and not any(abs(cb[0] - ob["band"][0]) < 1e-6 and abs(cb[1] - ob["band"][1]) < 1e-6 for cb in cold_bands):
                    cold_bands.append((ob["band"][0], ob["band"][1]))
        else:
            cold_bands = [tuple(c["band"]) for c in contained if c["kind"] == "cold"]
        cold_bands.sort()
        for p in plenums:
            side = p["side"]
            n = 0
            if placed and side in placed:
                spans = [(float(a), float(b)) for a, b in placed[side]]
                how = "answers.yaml `plenum.grilles`"
            else:
                spans = []
                for b in cold_bands:
                    mid = (b[0] + b[1]) / 2
                    lo = Q.q(mid - gw / 2, 1)
                    hi = round(lo + Q.size(gw, 1), 6)
                    lo, hi = max(hall[1], lo), min(hall[3], hi)
                    spans.append((lo, hi))
                how = "one per cold aisle, centred (AICFD's rule)"
            for lo, hi in spans:
                n += 1
                lo_q, hi_q = Q.q(lo, 1, f"supply grille {side}{n}"), Q.q(hi, 1, f"supply grille {side}{n}")
                supplies.append({"id": f"{side.lower()}{n}", "plenum": p["id"], "side": side, "at": p["wall_at"],
                                 "lo": lo_q, "hi": hi_q, "z": (floor, round(floor + gh_q, 6))})
            p["grilles"] = n
            p["grille_size"] = (gw, gh_q)
            p["how"] = how
        confirmed = isinstance((answers or {}).get("plenum"), dict) and (answers["plenum"].get("grille") or placed)
        (log.notes if confirmed else log.questions).append(
            f"the plan does not place the SUPPLY GRILLES in the plenum wall: {sum(p['grilles'] for p in plenums)} laid, one per cold "
            f"aisle, {gw:g} x {gh_q:g} m centred on it" + (" (`plenum.grille` from answers.yaml)" if confirmed else
            " (AICFD's rule) -- confirm, or give `plenum.grille.width/height`, or `plenum.grilles: {W: [[y0, y1], ...], E: [...]}` from the elevation"))
    ceiling_rect = (min([w["at"] for w in walls if w["side"] == "W"] + [hall[0]]) if any(w["side"] == "W" for w in walls) else hall[0], hall[1],
                    max([w["at"] for w in walls if w["side"] == "E"] + [hall[2]]) if any(w["side"] == "E" for w in walls) else hall[2], hall[3])

    # ---- loads ------------------------------------------------------------------
    R = cfg["racks"]
    LC = cfg.get("liquid_cooling", {}) or {}
    frac = LC.get("air_fraction", {}) or {}
    default = float(R["load_kw"])
    overrides = {str(k).upper().replace(" ", ""): v for k, v in ((answers or {}).get("loads") or {}).items()}
    total_it, total_air, n_zero, n_liquid, n_spare = 0.0, 0.0, 0, 0, 0
    spare_kw = LC.get("spare_kw")
    for row in rows:
        for r in row["racks"]:
            it = None
            if r["id"] in overrides or (r["tag"] and r["tag"] in overrides):
                v = overrides.get(r["id"], overrides.get(r["tag"]))
                word = str(v).strip().upper()
                if word in ("ODF", "0", "0.0"):
                    it, r["kind"] = 0.0, "ODF"
                elif word in ("FUTURE", "FUTURO", "ROFR"):
                    it, r["kind"] = float(R["future_kw"]), "FUTURE"
                else:
                    it, r["kind"] = float(v), r.get("kind") if r.get("kind") not in ("SPARE", "FUTURE") else "IT"
            elif r["kind"] in ("SPARE", "FUTURE") and r.get("load_kw") is None:
                n_spare += 1
                cap = r.get("capacity_kw")
                if r["kind"] == "SPARE":
                    it = float(spare_kw) if spare_kw is not None else (float(cap) if cap else default)
                else:
                    it = float(R["future_kw"])
            elif r.get("load_kw") is None:
                it = default
                r["kind"] = "IT"
            else:
                it = float(r["load_kw"])
            r["it_kw"] = it
            if r.get("liquid"):
                n_liquid += 1
                key = "network" if r["kind"] == "NETWORK" else "server"
                f = float(frac.get(key, frac.get("server", 1.0)))
                r["load_kw"] = round(it * f, 3)
                r["air_fraction"] = f
            else:
                r["load_kw"] = it
            total_it += it
            total_air += r["load_kw"]
            n_zero += r["load_kw"] == 0
    if n_spare and spare_kw is None:
        log.questions.append(f"{n_spare} SPARE / future position(s) carry no load in the drawing: taken at their stated capacity "
                             f"(the infrastructure case) -- give `liquid_cooling.spare_kw` (0 for the day-one case)")
    if n_liquid:
        log.notes.append(f"{n_liquid} liquid-cooled cabinets: the sidecar carries their AIR share "
                         f"(server {frac.get('server', 1.0):.0%}, network {frac.get('network', 1.0):.0%} of IT); "
                         f"IT {total_it:.0f} kW, to the air {total_air:.0f} kW")
        if "liquid_cooling" not in (answers or {}):
            log.questions.append("liquid-cooled cabinets: confirm the air fractions (`liquid_cooling.air_fraction.server/network`), taken from the reference")
    if not overrides and not any(rk.get("load_kw") is not None or rk.get("kind") in ("SPARE", "FUTURE") for rk in ex.racks):
        log.questions.append(f"no cabinet in the drawing carries a load: every one is at the reference {default} kW -- give `loads:` per tag or `racks.load_kw`")
    if unit_kind == "fanwall" and "fanwall" not in (answers or {}):
        log.questions.append("the units are fan walls (no raised floor): give the plant in `fanwall:` (model, airflow_m3h, capacity_kw, supply_temp_c) -- the reference's downflow unit does not apply")

    return {
        "name": cfg.get("name", "hall"),
        "source": cfg.get("source", ""),
        "cell": cell,
        "origin": [float(ox), float(oy)],
        "transposed": ex.transposed,
        "envelope": (0.0, 0.0, W, L),
        "hall": hall,
        "galleries": galleries,
        "walls": walls,
        "heights": {"deck": deck, "floor": floor, "rack_top": rack_top, "unit_top": unit_top, "ceiling": ceiling, "slab": slab},
        "has_deck": has_deck,
        "heights_confirmed": sorted(k for k in H_WHAT if k in given),
        "containment": containment,
        "unit_kind": unit_kind,
        "rows": [{"id": r["id"], "front": r["front"], "band": r["band"], "span": r["span"],
                  "racks": [{"id": k["id"], "tag": k["tag"], "rect": k["q"], "load_kw": k["load_kw"], "it_kw": k.get("it_kw", k["load_kw"]),
                             "kind": k["kind"], "liquid": bool(k.get("liquid")), "label": k.get("label", ""), "drawing": k["rect"]}
                            for k in r["racks"]]} for r in rows],
        "cold_aisles": [{"id": c["id"], "band": c["band"], "span": c["span"], "rows": [r["id"] for r in c["rows"]],
                         "lone": c["lone"], "tiles_across": c["tiles_across"], "tiles_along": c["tiles_along"]}
                        for c in contained if c["kind"] == "cold"],
        "hot_aisles": [{"id": c["id"], "band": c["band"], "span": c["span"], "rows": [r["id"] for r in c["rows"]],
                        "lone": c["lone"], "grilles": c["grilles"]} for c in contained if c["kind"] == "hot"],
        "hot_bands": [{"id": h["id"], "band": h["band"], "span": h["span"], "rows": [r["id"] for r in h["rows"]],
                       "paired": h["paired"], "grilles": h["grilles"]} for h in hot_bands],
        "lids": lids, "doors": doors, "closures": closures, "blanks": blanks, "chimneys": chimneys, "tiles": tiles, "grilles": grilles,
        "cage": {"panels": cage_panels, "construction": cfg["cage"]["construction"]},
        "units": units,
        "plenums": plenums,
        "supplies": supplies,
        "ceiling_rect": ceiling_rect,
        "openings": cfg["gallery_openings"],
        "loads": {"total_kw": round(total_air, 2), "it_kw": round(total_it, 2), "zero": n_zero, "default_kw": default, "liquid": n_liquid},
        "config": cfg,
        "log": {"corrections": log.corrections, "quantisations": log.quantisations, "alerts": log.alerts,
                "omissions": log.omissions + ex.leftovers, "questions": log.questions, "notes": log.notes + ex.notes},
    }
