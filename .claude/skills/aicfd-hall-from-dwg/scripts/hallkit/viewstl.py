"""The STL a person opens in another program to check the 3D model.

The contract STL (`<name>.stl`) is what AICFD reads: the hall is a closed
box around everything, panels have no thickness, and the tiles lie in the
deck's own plane. Opened in a viewer that is an opaque box, and coplanar
faces flicker. So this writes the same model for LOOKING AT:

  * no hall box -- the slab is a thin plate and the outer walls are left out,
    so the room is seen from above and from the side;
  * every panel is a 20 mm plate: walls, doors, lids, cage, mesh, deck,
    ceiling;
  * tiles sit 20 mm ON the deck, return grilles hang 20 mm UNDER the ceiling,
    so both are visible against the plate they cut;
  * cabinets and units are the same boxes as in the contract.

`<name>.view.stl` holds it all; `<name>.view/` holds one STL per element
type so a viewer can show or hide racks, tiles, grilles, containment (lids,
doors, end panels and blank panels: one sealed set), units, cage and walls
separately. Names stay `type:id` inside each file.
"""
from __future__ import annotations

import os

from . import stl

T = 0.02  # plate thickness, metres


def _plate_x(at, y0, y1, z0, z1):
    return (at - T / 2, y0, z0, at + T / 2, y1, z1)


def _plate_y(at, x0, x1, z0, z1):
    return (x0, at - T / 2, z0, x1, at + T / 2, z1)


def _plate_z(at, rect, above: bool):
    x0, y0, x1, y1 = rect
    return (x0, y0, at, x1, y1, at + T) if above else (x0, y0, at - T, x1, y1, at)


def groups(m: dict) -> dict[str, list[str]]:
    H = m["heights"]
    env = m["envelope"]
    hall = m["hall"]
    out: dict[str, list[str]] = {}

    def add(kind, lines):
        out.setdefault(kind, []).extend(lines)

    floor = H.get("floor", H["deck"])
    has_deck = m.get("has_deck", H["deck"] > 0)
    add("slab", stl.box("slab", (env[0], env[1], -T, env[2], env[3], 0.0)))
    if has_deck:
        add("deck", stl.box("deck", _plate_z(H["deck"], env, above=False)))
    add("ceiling", stl.box("ceiling", _plate_z(H["ceiling"], m.get("ceiling_rect", hall), above=True)))
    for p in m.get("plenums", []):
        add("walls", stl.box(f"wall:{p['id']}", _plate_x(p["wall_at"], env[1], env[3], floor, H["ceiling"])))
    for sg in m.get("supplies", []):
        # three plates thick, so the grille stands proud of the wall on both faces and a viewer shows it
        b = _plate_x(sg["at"], sg["lo"], sg["hi"], sg["z"][0], sg["z"][1])
        add("supplies", stl.box(f"supply:{sg['id']}", (b[0] - T, b[1], b[2], b[3] + T, b[4], b[5])))
    for w in m["walls"]:
        add("walls", stl.box(f"wall:{w['id']}", _plate_x(w["at"], env[1], env[3], floor, H["ceiling"])))
        if has_deck:
            add("mesh", stl.box(f"mesh:{w['id']}_plenum", _plate_x(w["at"], env[1], env[3], 0.0, H["deck"])))
        add("mesh", stl.box(f"mesh:{w['id']}_return", _plate_x(w["at"], env[1], env[3], H["ceiling"], H["slab"])))
    for row in m["rows"]:
        front = "+y" if row["front"] > 0 else "-y"
        for r in row["racks"]:
            q = r["rect"]
            add("racks", stl.box(f"rack:{r['id']}:{front}", (q[0], q[1], floor, q[2], q[3], H["rack_top"])))
    for u in m["units"]:
        add("units", stl.box(f"unit:{u['id']}" + (f":{u['dir']}" if u.get("dir") else ""), u["box"]))
    for c in m.get("chimneys", []):
        add("containment", stl.box(f"wall:{c['id']}", _plate_y(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1])))
    for lid in m["lids"]:
        add("containment", stl.box(f"lid:{lid['id']}", _plate_z(lid["at"], lid["rect"], above=True)))
    for d in m["doors"]:
        add("containment", stl.box(f"door:{d['id']}", _plate_x(d["at"], d["lo"], d["hi"], d["z"][0], d["z"][1])))
    for c in m["closures"]:
        b = _plate_y(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1]) if c["axis"] == "y" else _plate_x(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1])
        add("containment", stl.box(f"wall:{c['id']}", b))
    for b in m.get("blanks", []):
        bb = _plate_y(b["at"], b["lo"], b["hi"], b["z"][0], b["z"][1]) if b["axis"] == "y" else _plate_x(b["at"], b["lo"], b["hi"], b["z"][0], b["z"][1])
        add("containment", stl.box(f"blank:{b['id']}", bb))   # a blank panel is part of the sealed containment set
    for t in m["tiles"]:
        add("tiles", stl.box(f"tile:{t['id']}", _plate_z(H["deck"], t["rect"], above=True)))
    for g in m["grilles"]:
        add("grilles", stl.box(f"grille:{g['id']}", _plate_z(H["ceiling"], g["rect"], above=False)))
    for p in m["cage"]["panels"]:
        b = _plate_y(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1]) if p["axis"] == "y" else _plate_x(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1])
        add("cage", stl.box(f"cage:{p['id']}", b))
    return out


def write(m: dict, path: str, folder: str) -> dict[str, int]:
    g = groups(m)
    with open(path, "w") as f:
        for kind, lines in g.items():
            f.write("\n".join(lines) + "\n")
    os.makedirs(folder, exist_ok=True)
    for old in os.listdir(folder):
        if old.endswith(".stl"):
            os.remove(os.path.join(folder, old))
    for kind, lines in g.items():
        with open(os.path.join(folder, f"{kind}.stl"), "w") as f:
            f.write("\n".join(lines) + "\n")
    return {k: sum(1 for ln in v if ln.startswith("solid ")) for k, v in g.items()}


def preview(m: dict, path: str, title: str) -> None:
    """An isometric render of the view model, so the engineer sees the 3D
    before opening it elsewhere. Every face goes into ONE collection so the
    depth sort is global (matplotlib sorts within a collection only); the
    deck, the walls and the ceiling are translucent."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import to_rgba
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    H = m["heights"]
    env = m["envelope"]
    faces, fcs, ecs = [], [], []

    def box(b, fc, ec="#444", alpha=1.0):
        x0, y0, z0, x1, y1, z1 = b
        v = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0), (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
        for f in ([v[0], v[1], v[2], v[3]], [v[4], v[5], v[6], v[7]], [v[0], v[1], v[5], v[4]],
                  [v[2], v[3], v[7], v[6]], [v[1], v[2], v[6], v[5]], [v[0], v[3], v[7], v[4]]):
            faces.append(f)
            fcs.append(to_rgba(fc, alpha))
            ecs.append(to_rgba(ec, min(1.0, alpha + 0.2)))

    box((env[0], env[1], -T, env[2], env[3], 0.0), "#d9d9d9", alpha=0.5)
    box(_plate_z(H["deck"], env, above=False), "#eeeeee", alpha=0.35)
    for w in m["walls"]:
        box(_plate_x(w["at"], env[1], env[3], H["deck"], H["ceiling"]), "#9a9a9a", alpha=0.25)
        box(_plate_x(w["at"], env[1], env[3], 0.0, H["deck"]), "#c9c9c9", alpha=0.15)
        box(_plate_x(w["at"], env[1], env[3], H["ceiling"], H["slab"]), "#c9c9c9", alpha=0.15)
    for t in m["tiles"]:
        box(_plate_z(H["deck"], t["rect"], above=True), "#7a9cc6", "#4a6fa0", alpha=0.9)
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            box((q[0], q[1], H["deck"], q[2], q[3], H["rack_top"]), "#f5e6c8", "#8b5a2b")
    for lid in m["lids"]:
        box(_plate_z(lid["at"], lid["rect"], above=True), "#4a90d9", "#1f5fa8", alpha=0.5)
    for d in m["doors"]:
        box(_plate_x(d["at"], d["lo"], d["hi"], d["z"][0], d["z"][1]), "#1f5fa8", alpha=0.7)
    for c in m["closures"]:
        b = _plate_y(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1]) if c["axis"] == "y" else _plate_x(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1])
        box(b, "#0b3d91", alpha=0.7)
    for p in m["cage"]["panels"]:
        b = _plate_y(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1]) if p["axis"] == "y" else _plate_x(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1])
        box(b, "#7b2cbf", alpha=0.3)
    for bl in m.get("blanks", []):
        b = _plate_y(bl["at"], bl["lo"], bl["hi"], bl["z"][0], bl["z"][1]) if bl["axis"] == "y" else _plate_x(bl["at"], bl["lo"], bl["hi"], bl["z"][0], bl["z"][1])
        box(b, "#4d4d4d", "#222", alpha=0.9)
    for u in m["units"]:
        box(u["box"], "#cfe3ff", "#2e6da4")
    for g in m["grilles"]:
        box(_plate_z(H["ceiling"], g["rect"], above=False), "#e39a93", "#c0605a", alpha=0.75)
    box(_plate_z(H["ceiling"], m["hall"], above=True), "#dddddd", alpha=0.08)

    fig = plt.figure(figsize=(15, 18), dpi=140)
    W, L = env[2] - env[0], env[3] - env[1]
    for k, (elev, azim, sub) in enumerate(((35, -55, "from the south-west, above"), (22, 125, "from the north-east, low"))):
        ax = fig.add_subplot(2, 1, k + 1, projection="3d")
        pc = Poly3DCollection(faces, facecolors=fcs, edgecolors=ecs, linewidths=0.15)
        ax.add_collection3d(pc)
        ax.set_xlim(env[0], env[2])
        ax.set_ylim(env[1], env[3])
        ax.set_zlim(0, H["slab"])
        ax.set_box_aspect((W, L, H["slab"] * 1.6))
        ax.view_init(elev=elev, azim=azim)
        ax.set_xlabel("x [m]", fontsize=7)
        ax.set_ylabel("y [m]", fontsize=7)
        ax.set_zlabel("z [m]", fontsize=7)
        ax.tick_params(labelsize=6)
        ax.set_title((title if k == 0 else "") + f"\n{sub}", fontsize=8, loc="left")
    plt.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
