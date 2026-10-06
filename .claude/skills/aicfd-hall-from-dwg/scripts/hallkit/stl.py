"""Write the model as the named-solid ASCII STL AICFD reads.

One `solid type:id[:attr]` per object. Boxes are 12 facets with outward
normals and right-hand winding; panels are 2 facets, zero thickness, the
normal along the axis the panel is flat on. Every normal is on an axis.
"""
from __future__ import annotations

_FACES = (  # (normal, the four corners as (ix, iy, iz) picks of (lo, hi)), counter-clockwise seen from outside
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
    ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
    ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
    ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
)


def _f(v: float) -> str:
    return f"{v:.3f}"


def _facet(out, n, a, b, c):
    out.append(f"  facet normal {n[0]:d} {n[1]:d} {n[2]:d}")
    out.append("    outer loop")
    for p in (a, b, c):
        out.append(f"      vertex {_f(p[0])} {_f(p[1])} {_f(p[2])}")
    out.append("    endloop")
    out.append("  endfacet")


def box(name: str, b) -> list[str]:
    """b = (x0, y0, z0, x1, y1, z1)."""
    lo, hi = (b[0], b[1], b[2]), (b[3], b[4], b[5])
    out = [f"solid {name}"]
    for n, corners in _FACES:
        pts = [tuple(hi[k] if c[k] else lo[k] for k in range(3)) for c in corners]
        _facet(out, n, pts[0], pts[1], pts[2])
        _facet(out, n, pts[0], pts[2], pts[3])
    out.append(f"endsolid {name}")
    return out


def panel(name: str, axis: str, at: float, rect) -> list[str]:
    """A rectangle flat on `axis` at coordinate `at`; rect = (a0, b0, a1, b1)
    spans the other two axes in xyz order (y,z for an x-panel; x,z for a
    y-panel; x,y for a z-panel)."""
    a0, b0, a1, b1 = rect
    if axis == "x":
        pts = [(at, a0, b0), (at, a1, b0), (at, a1, b1), (at, a0, b1)]
        n = (1, 0, 0)
    elif axis == "y":
        pts = [(a0, at, b0), (a0, at, b1), (a1, at, b1), (a1, at, b0)]
        n = (0, 1, 0)
    else:
        pts = [(a0, b0, at), (a1, b0, at), (a1, b1, at), (a0, b1, at)]
        n = (0, 0, 1)
    out = [f"solid {name}"]
    _facet(out, n, pts[0], pts[1], pts[2])
    _facet(out, n, pts[0], pts[2], pts[3])
    out.append(f"endsolid {name}")
    return out


def write(m: dict, path: str) -> dict:
    """Write the STL; return the counts per type."""
    H = m["heights"]
    env = m["envelope"]
    hall = m["hall"]
    L = []
    counts = {}

    def add(lines, kind):
        L.extend(lines)
        counts[kind] = counts.get(kind, 0) + 1

    floor = H.get("floor", H["deck"])
    add(box("hall", (env[0], env[1], 0.0, env[2], env[3], H["slab"])), "hall")
    if m.get("has_deck", H["deck"] > 0):
        add(panel("deck", "z", H["deck"], (env[0], env[1], env[2], env[3])), "deck")
    cr = m.get("ceiling_rect", hall)
    add(panel("ceiling", "z", H["ceiling"], (cr[0], cr[1], cr[2], cr[3])), "ceiling")
    for w in m["walls"]:
        add(panel(f"wall:{w['id']}", "x", w["at"], (env[1], 0.0, env[3], H["slab"])), "wall")
        kind_b = m["openings"]["below_deck"]
        kind_a = m["openings"]["above_ceiling"]
        if m.get("has_deck", H["deck"] > 0):
            add(panel(f"{kind_b}:{w['id']}_plenum", "x", w["at"], (env[1], 0.0, env[3], H["deck"])), kind_b)
        add(panel(f"{kind_a}:{w['id']}_return", "x", w["at"], (env[1], H["ceiling"], env[3], H["slab"])), kind_a)
    for p in m.get("plenums", []):
        # the hall's own wall, floor to false ceiling: the inner leaf of the double wall
        add(panel(f"wall:{p['id']}", "x", p["wall_at"], (env[1], floor, env[3], H["ceiling"])), "wall")
    for sg in m.get("supplies", []):
        add(panel(f"supply:{sg['id']}", "x", sg["at"], (sg["lo"], sg["z"][0], sg["hi"], sg["z"][1])), "supply")
    for row in m["rows"]:
        front = "+y" if row["front"] > 0 else "-y"
        for r in row["racks"]:
            q = r["rect"]
            add(box(f"rack:{r['id']}:{front}", (q[0], q[1], floor, q[2], q[3], H["rack_top"])), "rack")
    for u in m["units"]:
        name = f"unit:{u['id']}" + (f":{u['dir']}" if u.get("dir") else "")
        add(box(name, u["box"]), "unit")
    for c in m.get("chimneys", []):
        add(panel(f"wall:{c['id']}", c["axis"], c["at"], (c["lo"], c["z"][0], c["hi"], c["z"][1])), "wall")
    for lid in m["lids"]:
        add(panel(f"lid:{lid['id']}", "z", lid["at"], lid["rect"]), "lid")
    for d in m["doors"]:
        add(panel(f"door:{d['id']}", d["axis"], d["at"], (d["lo"], d["z"][0], d["hi"], d["z"][1])), "door")
    for c in m["closures"]:
        add(panel(f"wall:{c['id']}", c["axis"], c["at"], (c["lo"], c["z"][0], c["hi"], c["z"][1])), "wall")
    for b in m.get("blanks", []):
        add(panel(f"blank:{b['id']}", b["axis"], b["at"], (b["lo"], b["z"][0], b["hi"], b["z"][1])), "blank")
    for t in m["tiles"]:
        add(panel(f"tile:{t['id']}", "z", H["deck"], t["rect"]), "tile")
    for g in m["grilles"]:
        add(panel(f"grille:{g['id']}", "z", H["ceiling"], g["rect"]), "grille")
    for p in m["cage"]["panels"]:
        add(panel(f"cage:{p['id']}", p["axis"], p["at"], (p["lo"], p["z"][0], p["hi"], p["z"][1])), "cage")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    return counts
