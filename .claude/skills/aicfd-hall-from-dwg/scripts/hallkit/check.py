"""The contract's consistency rules, run on the WRITTEN files -- the STL is
parsed back, so what is checked is what AICFD will receive, not the model
that produced it. Each failure names the solid; each maps to a refusal
AICFD would give."""
from __future__ import annotations

import math
import re
from collections import defaultdict

import yaml

FORBIDDEN_KEYS = ["pods", "aisles", "gallery", "hall", "floor", "grilles", "containment", "plenum"]
FORBIDDEN_SUB = {"racks": ["size", "per_row", "count", "blocks", "widths"],
                 "fanwall": ["count", "width", "height", "depth", "offset", "pitch"],
                 "cage": ["pods", "sides", "aisle", "clearance", "height", "roof"]}


def parse_stl(path: str) -> list[dict]:
    solids, cur = [], None
    with open(path) as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            if t[0] == "solid":
                cur = {"name": t[1] if len(t) > 1 else "", "facets": []}
            elif t[0] == "facet":
                cur["facets"].append({"n": tuple(float(v) for v in t[2:5]), "v": []})
            elif t[0] == "vertex":
                cur["facets"][-1]["v"].append(tuple(float(v) for v in t[1:4]))
            elif t[0] == "endsolid":
                solids.append(cur)
                cur = None
    for s in solids:
        pts = [p for fc in s["facets"] for p in fc["v"]]
        s["bbox"] = tuple(min(p[k] for p in pts) for k in range(3)) + tuple(max(p[k] for p in pts) for k in range(3))
        parts = s["name"].split(":")
        s["type"], s["id"], s["attr"] = parts[0], (parts[1] if len(parts) > 1 else ""), (parts[2] if len(parts) > 2 else "")
    return solids


def _on_grid(v, c):
    return abs(v / c - round(v / c)) < 1e-6


def _ov(a, b, axes, tol=1e-6):
    """Overlap in area over the given axes of two bboxes."""
    for k in axes:
        if a[k + 3] <= b[k] + tol or b[k + 3] <= a[k] + tol:
            return False
    return True


def run(stl_path: str, sidecar_path: str, cell) -> list[str]:
    fails: list[str] = []
    S = parse_stl(stl_path)
    by = defaultdict(list)
    for s in S:
        by[s["type"]].append(s)
    names = defaultdict(list)
    for s in S:
        names[(s["type"], s["id"])].append(s)
    for (t, i), ss in names.items():
        if len(ss) > 1:
            fails.append(f"{t}:{i}: name used {len(ss)} times")
    if len(by["hall"]) != 1:
        fails.append(f"{len(by['hall'])} `hall` solids; exactly one is required")
        return fails
    hall = by["hall"][0]["bbox"]

    # facets: normals on an axis, boxes 12, panels 2, grid
    for s in S:
        nf = len(s["facets"])
        is_box = s["type"] in ("hall", "rack", "unit")
        if is_box and nf != 12:
            fails.append(f"{s['name']}: a box needs 12 facets, has {nf}")
        if not is_box and nf != 2:
            fails.append(f"{s['name']}: a panel needs 2 facets, has {nf}")
        for fc in s["facets"]:
            n = fc["n"]
            if sorted(abs(v) for v in n) != [0.0, 0.0, 1.0] and not any(abs(abs(v) - 1) < 1e-6 and all(abs(w) < 1e-6 for w in n if w is not v) for v in n):
                fails.append(f"{s['name']}: facet normal {n} is not on an axis")
            if len(fc["v"]) != 3 or len(set(fc["v"])) != 3:
                fails.append(f"{s['name']}: degenerate facet")
            for p in fc["v"]:
                for k in range(3):
                    if not _on_grid(p[k], cell[k]):
                        fails.append(f"{s['name']}: vertex {p} is off the {'xyz'[k]} grid of {cell[k]}")
                        break
        b = s["bbox"]
        if not is_box:
            flat = [k for k in range(3) if abs(b[k + 3] - b[k]) < 1e-9]
            if len(flat) != 1:
                fails.append(f"{s['name']}: a panel must be flat on exactly one axis")
        if s is not by["hall"][0]:
            for k in range(3):
                if b[k] < hall[k] - 1e-6 or b[k + 3] > hall[k + 3] + 1e-6:
                    fails.append(f"{s['name']}: outside the hall on {'xyz'[k]}")
                    break

    racks = by["rack"]
    units = by["unit"]
    panels = [s for s in S if s["type"] not in ("hall", "rack", "unit")]
    for r in racks:
        if r["attr"] not in ("+x", "-x", "+y", "-y"):
            fails.append(f"{r['name']}: no front")
    racks_f = [r for r in racks if r["attr"] in ("+x", "-x", "+y", "-y")]
    for i, a in enumerate(racks):
        for b in racks[i + 1:]:
            if _ov(a["bbox"], b["bbox"], (0, 1, 2)):
                fails.append(f"{a['name']} overlaps {b['name']}")
        for u in units:
            if _ov(a["bbox"], u["bbox"], (0, 1, 2)):
                fails.append(f"{a['name']} overlaps {u['name']}")
        for p in panels:
            pb = p["bbox"]
            flat = next(k for k in range(3) if abs(pb[k + 3] - pb[k]) < 1e-9)
            others = [k for k in range(3) if k != flat]
            if a["bbox"][flat] + 1e-6 < pb[flat] < a["bbox"][flat + 3] - 1e-6 and _ov(a["bbox"], pb, others):
                fails.append(f"{p['name']} passes through {a['name']}")
    # fronts free and tiles off the footprints
    for r in racks_f:
        b = r["bbox"]
        ax = 0 if r["attr"][1] == "x" else 1
        sign = 1 if r["attr"][0] == "+" else -1
        face = b[ax + 3] if sign > 0 else b[ax]
        for o in racks + units:
            if o is r:
                continue
            ob = o["bbox"]
            touching = abs((ob[ax] if sign > 0 else ob[ax + 3]) - face) < 1e-6
            if touching and _ov(b, ob, [k for k in (0, 1, 2) if k != ax]):
                fails.append(f"{r['name']}: its front is against {o['name']}")
    for t in by["tile"]:
        for r in racks:
            if _ov(t["bbox"], r["bbox"], (0, 1)):
                fails.append(f"{t['name']} lies under {r['name']}")
    # rows: same band + same front -> contiguous, straight
    rows = defaultdict(list)
    for r in racks_f:
        b = r["bbox"]
        ax = 0 if r["attr"][1] == "x" else 1
        rows[(r["attr"], round(b[ax], 6), round(b[ax + 3], 6))].append(r)
    for key, rs in rows.items():
        along = 1 if key[0][1] == "x" else 0
        rs.sort(key=lambda s: s["bbox"][along])
        for a, b in zip(rs, rs[1:]):
            gap = b["bbox"][along] - a["bbox"][along + 3]
            if 1e-6 < gap < min(cell) - 1e-6:
                fails.append(f"{a['name']} and {b['name']}: {gap * 1000:.0f} mm gap, smaller than a cell")
        if len(rs) == 1:
            pass  # a row of one is legal and is reported by the export text
    # deck / ceiling / units / tiles / grilles / lids / doors
    deck = by["deck"][0]["bbox"] if by["deck"] else None
    ceil = by["ceiling"][0]["bbox"] if by["ceiling"] else None
    if len(by["deck"]) > 1 or len(by["ceiling"]) > 1:
        fails.append("more than one deck or ceiling")
    if deck:
        z = deck[2]
        for r in racks:
            if abs(r["bbox"][2] - z) > 1e-6:
                fails.append(f"{r['name']}: does not start on the deck ({r['bbox'][2]} vs {z})")
        for u in units:
            if abs(u["bbox"][2] - z) > 1e-6:
                fails.append(f"{u['name']}: a downflow unit starts on the deck ({u['bbox'][2]} vs {z})")
        for t in by["tile"]:
            if abs(t["bbox"][2] - z) > 1e-6 or not _inside2d(t["bbox"], deck):
                fails.append(f"{t['name']}: not in the deck plane / extent")
    if ceil:
        z = ceil[2]
        for r in racks + units:
            if r["bbox"][5] > z + 1e-6:
                fails.append(f"{r['name']}: taller than the ceiling")
        for g in by["grille"]:
            if abs(g["bbox"][2] - z) > 1e-6 or not _inside2d(g["bbox"], ceil):
                fails.append(f"{g['name']}: not in the ceiling plane / extent")
        for lid in by["lid"]:
            if lid["bbox"][2] >= z - 1e-6:
                fails.append(f"{lid['name']}: not below the ceiling")
    walls = by["wall"]
    for u in units:
        b = u["bbox"]
        in_gallery = any(b[3] <= w["bbox"][0] + 1e-6 or b[0] >= w["bbox"][0] - 1e-6 for w in walls if abs(w["bbox"][3] - w["bbox"][0]) < 1e-9) \
            and not any(w["bbox"][0] - 1e-6 < (b[0] + b[3]) / 2 < w["bbox"][3] + 1e-6 for w in [by["ceiling"][0]] if ceil)
        if ceil and not in_gallery:
            fails.append(f"{u['name']}: stands under the ceiling, not in a gallery")
    # lids rest on rack faces; doors close lids
    for lid in by["lid"]:
        lb = lid["bbox"]
        for edge in (lb[1], lb[4]):
            on_face = any(abs(r["bbox"][1] - edge) < 1e-6 or abs(r["bbox"][4] - edge) < 1e-6 for r in racks) \
                or any(abs(w["bbox"][1] - edge) < 1e-6 and abs(w["bbox"][4] - w["bbox"][1]) < 1e-9 for w in walls)
            if not on_face:
                fails.append(f"{lid['name']}: edge y = {edge} rests on nothing")
        tops = {round(r["bbox"][5], 6) for r in racks}
        if round(lb[2], 6) not in tops:
            fails.append(f"{lid['name']}: not at a rack-top height")
        doors = [d for d in by["door"] if abs(d["bbox"][1] - lb[1]) < 1e-6 and abs(d["bbox"][4] - lb[4]) < 1e-6
                 and (abs(d["bbox"][0] - lb[0]) < 1e-6 or abs(d["bbox"][0] - lb[3]) < 1e-6)]
        at_wall = sum(1 for x in (lb[0], lb[3]) if any(abs(w["bbox"][0] - x) < 1e-6 and abs(w["bbox"][3] - w["bbox"][0]) < 1e-9
                                                      and w["bbox"][1] <= lb[1] + 1e-6 and w["bbox"][4] >= lb[4] - 1e-6 for w in walls))
        if len(doors) + at_wall != 2:
            fails.append(f"{lid['name']}: {len(doors)} door(s) and {at_wall} wall(s) close its ends, 2 expected")
        for d in doors:
            if abs(d["bbox"][5] - lb[2]) > 1e-6 or (deck and abs(d["bbox"][2] - deck[2]) > 1e-6):
                fails.append(f"{d['name']}: does not run from the deck to the lid")
    # a lid's edge beyond the cabinets is closed by blank panels on the row line
    for lid in by["lid"]:
        lb = lid["bbox"]
        for edge in (lb[1], lb[4]):
            xs = []
            for r in racks:
                if abs(r["bbox"][1] - edge) < 1e-6 or abs(r["bbox"][4] - edge) < 1e-6:
                    xs.append((r["bbox"][0], r["bbox"][3]))
            for b in by["blank"]:
                if abs(b["bbox"][1] - edge) < 1e-6 and abs(b["bbox"][4] - b["bbox"][1]) < 1e-9:
                    if b["bbox"][2] > deck[2] + 1e-6 or abs(b["bbox"][5] - lb[2]) > 1e-6:
                        fails.append(f"{b['name']}: does not run from the deck to the lid")
                    xs.append((b["bbox"][0], b["bbox"][3]))
            if not xs:
                continue  # an end panel (wall) closes this edge; checked above
            xs.sort()
            x = lb[0]
            for x0, x1 in xs:
                if x0 > x + 1e-6 and x < lb[3] - 1e-6:
                    fails.append(f"{lid['name']}: {(x0 - x) * 1000:.0f} mm open on its edge y = {edge} at x = {x:.2f}")
                x = max(x, x1)
            if x < lb[3] - 1e-6:
                fails.append(f"{lid['name']}: {(lb[3] - x) * 1000:.0f} mm open on its edge y = {edge} at x = {x:.2f}")
    # openings/meshes/cages lie in a wall plane inside its extent (cage panels are their own walls)
    for p in by["opening"] + by["mesh"] + by["supply"]:
        pb = p["bbox"]
        if not any(abs(w["bbox"][0] - pb[0]) < 1e-6 and abs(w["bbox"][3] - pb[3]) < 1e-6 and _inside2d_yz(pb, w["bbox"]) for w in walls):
            fails.append(f"{p['name']}: not in the plane and extent of a wall")
    # panels of one type do not overlap in area
    for t in ("tile", "grille", "lid"):
        ps = by[t]
        for i, a in enumerate(ps):
            for b in ps[i + 1:]:
                if _ov(a["bbox"], b["bbox"], (0, 1)) and abs(a["bbox"][2] - b["bbox"][2]) < 1e-9:
                    fails.append(f"{a['name']} overlaps {b['name']}")
    # sidecar
    doc = yaml.safe_load(open(sidecar_path))
    for k in FORBIDDEN_KEYS:
        if k in doc:
            fails.append(f"sidecar carries `{k}`, a room-building key")
    for k, subs in FORBIDDEN_SUB.items():
        for sk in subs:
            if isinstance(doc.get(k), dict) and sk in doc[k]:
                fails.append(f"sidecar carries `{k}.{sk}`, a room-building key")
    rack_ids = {r["id"] for r in racks}
    for rid in (doc.get("racks", {}).get("loads") or {}):
        if str(rid) not in rack_ids:
            fails.append(f"sidecar names rack {rid}, which is not in the STL")
    unit_ids = {u["id"] for u in units}
    for uid in (doc.get("fanwall", {}).get("out_of_service") or []):
        if not (isinstance(uid, int) or str(uid) in unit_ids):
            fails.append(f"sidecar takes {uid} out of service, which is not in the STL")
    if list(doc.get("mesh", {}).get("cell_size", [])) != list(cell):
        fails.append("sidecar cell size differs from the grid checked")
    return fails


def _inside2d(a, b, tol=1e-6):
    return a[0] >= b[0] - tol and a[1] >= b[1] - tol and a[3] <= b[3] + tol and a[4] <= b[4] + tol


def _inside2d_yz(a, b, tol=1e-6):
    return a[1] >= b[1] - tol and a[2] >= b[2] - tol and a[4] <= b[4] + tol and a[5] <= b[5] + tol


def summary(stl_path: str) -> dict:
    S = parse_stl(stl_path)
    out = defaultdict(int)
    for s in S:
        out[s["type"]] += 1
    return dict(out)
