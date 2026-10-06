"""A z-buffer render of the view model -- the same boxes `viewstl` writes --
so the 3D preview shows what a viewer will show: opaque surfaces, correct
occlusion, flat shading by face normal. Orthographic projection.

matplotlib's 3D axes have no depth buffer (they sort whole polygons by
their mean depth and paint in that order), which is what put the lid's
colour inside the aisle and doors in front of cabinets that hide them.
"""
from __future__ import annotations

import math

import numpy as np

from . import viewstl

COLOURS = {  # per view group, RGB 0..1
    "racks": (0.96, 0.90, 0.78), "units": (0.81, 0.89, 1.00), "containment": (0.29, 0.56, 0.85),
    "blank": (0.12, 0.24, 0.45), "tiles": (0.48, 0.61, 0.78), "grilles": (0.93, 0.64, 0.60),
    "cage": (0.62, 0.42, 0.85), "walls": (0.62, 0.62, 0.62), "mesh": (0.80, 0.80, 0.80),
    "deck": (0.91, 0.91, 0.91), "slab": (0.80, 0.80, 0.80), "ceiling": (0.85, 0.85, 0.85),
    "supplies": (0.35, 0.62, 0.90),
}
LIGHT = np.array([-0.4, -0.5, 0.75])
LIGHT = LIGHT / np.linalg.norm(LIGHT)

_FACES = viewstl.stl._FACES


def _boxes(m: dict, drop=("ceiling",)):
    """(box, group) for every plate and box of the view model."""
    out = []
    H = m["heights"]
    env = m["envelope"]
    T = viewstl.T
    floor = H.get("floor", H["deck"])
    has_deck = m.get("has_deck", H["deck"] > 0)
    out.append(((env[0], env[1], -T, env[2], env[3], 0.0), "slab"))
    if has_deck:
        out.append((viewstl._plate_z(H["deck"], env, above=False), "deck"))
    for w in m["walls"]:
        out.append((viewstl._plate_x(w["at"], env[1], env[3], floor, H["ceiling"]), "walls"))
        if has_deck:
            out.append((viewstl._plate_x(w["at"], env[1], env[3], 0.0, H["deck"]), "mesh"))
        if "ceiling" not in drop:
            out.append((viewstl._plate_x(w["at"], env[1], env[3], H["ceiling"], H["slab"]), "mesh"))
    for p in m.get("plenums", []):
        out.append((viewstl._plate_x(p["wall_at"], env[1], env[3], floor, H["ceiling"]), "walls"))
    for sg in m.get("supplies", []):
        b = viewstl._plate_x(sg["at"], sg["lo"], sg["hi"], sg["z"][0], sg["z"][1])
        # proud of the wall on both faces by more than a pixel at this scale, so
        # the grille is seen from the gallery side and from the hall side alike
        out.append(((b[0] - 0.12, b[1], b[2], b[3] + 0.12, b[4], b[5]), "supplies"))
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            out.append(((q[0], q[1], floor, q[2], q[3], H["rack_top"]), "racks"))
    for c in m.get("chimneys", []):
        out.append((viewstl._plate_y(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1]), "containment"))
    for u in m["units"]:
        out.append((tuple(u["box"]), "units"))
    for lid in m["lids"]:
        out.append((viewstl._plate_z(lid["at"], lid["rect"], above=True), "containment"))
    for d in m["doors"]:
        out.append((viewstl._plate_x(d["at"], d["lo"], d["hi"], d["z"][0], d["z"][1]), "containment"))
    for c in m["closures"]:
        b = viewstl._plate_y(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1]) if c["axis"] == "y" else viewstl._plate_x(c["at"], c["lo"], c["hi"], c["z"][0], c["z"][1])
        out.append((b, "containment"))
    for bl in m.get("blanks", []):
        b = viewstl._plate_y(bl["at"], bl["lo"], bl["hi"], bl["z"][0], bl["z"][1]) if bl["axis"] == "y" else viewstl._plate_x(bl["at"], bl["lo"], bl["hi"], bl["z"][0], bl["z"][1])
        out.append((b, "blank"))
    for t in m["tiles"]:
        out.append((viewstl._plate_z(H["deck"], t["rect"], above=True), "tiles"))
    for g in m["grilles"]:
        out.append((viewstl._plate_z(H["ceiling"], g["rect"], above=False), "grilles"))
    for p in m["cage"]["panels"]:
        b = viewstl._plate_y(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1]) if p["axis"] == "y" else viewstl._plate_x(p["at"], p["lo"], p["hi"], p["z"][0], p["z"][1])
        out.append((b, "cage"))
    if "ceiling" not in drop:
        out.append((viewstl._plate_z(H["ceiling"], m.get("ceiling_rect", m["hall"]), above=True), "ceiling"))
    return out


def _triangles(boxes):
    """Nx3x3 vertices, Nx3 normals, Nx3 colours."""
    V, N, C = [], [], []
    for b, group in boxes:
        lo, hi = (b[0], b[1], b[2]), (b[3], b[4], b[5])
        col = COLOURS[group]
        for n, corners in _FACES:
            pts = [tuple(hi[k] if c[k] else lo[k] for k in range(3)) for c in corners]
            V.append((pts[0], pts[1], pts[2]))
            V.append((pts[0], pts[2], pts[3]))
            N.append(n)
            N.append(n)
            C.append(col)
            C.append(col)
    return np.array(V, float), np.array(N, float), np.array(C, float)


def _camera(azim_deg: float, elev_deg: float):
    """Rotation taking world xyz to camera (right, up, depth-towards-viewer)."""
    a, e = math.radians(azim_deg), math.radians(elev_deg)
    # view direction from the scene towards the viewer
    d = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
    up = np.array([0.0, 0.0, 1.0])
    right = np.cross(up, d)
    right /= np.linalg.norm(right)
    up2 = np.cross(d, right)
    return np.stack([right, up2, d])  # rows


def render(m: dict, azim: float, elev: float, width_px: int = 1800, drop=("ceiling",)):
    V, N, C = _triangles(_boxes(m, drop))
    R = _camera(azim, elev)
    P = V @ R.T                                   # N x 3 x 3 (right, up, depth)
    shade = 0.55 + 0.45 * np.clip(N @ LIGHT, 0, 1)
    col = C * shade[:, None]
    xs, ys = P[:, :, 0], P[:, :, 1]
    x0, x1 = xs.min(), xs.max()
    y0, y1 = ys.min(), ys.max()
    scale = (width_px - 20) / (x1 - x0)
    height_px = int((y1 - y0) * scale) + 20
    img = np.ones((height_px, width_px, 3)) * 0.985
    zbuf = np.full((height_px, width_px), -np.inf)
    sx = (xs - x0) * scale + 10
    sy = (y1 - ys) * scale + 10
    depth = P[:, :, 2]
    # back-face skip: triangles whose normal points away from the viewer
    facing = (N @ R[2]) > -1e-9
    order = np.argsort(depth.mean(axis=1))        # coarse: far first (the z-buffer decides the rest)
    for i in order:
        if not facing[i]:
            continue
        tx, ty, tz = sx[i], sy[i], depth[i]
        bx0, bx1 = int(max(0, math.floor(tx.min()))), int(min(width_px - 1, math.ceil(tx.max())))
        by0, by1 = int(max(0, math.floor(ty.min()))), int(min(height_px - 1, math.ceil(ty.max())))
        if bx1 < bx0 or by1 < by0:
            continue
        gx, gy = np.meshgrid(np.arange(bx0, bx1 + 1) + 0.5, np.arange(by0, by1 + 1) + 0.5)
        # barycentric coordinates
        (ax, ay), (bx, by), (cx, cy) = (tx[0], ty[0]), (tx[1], ty[1]), (tx[2], ty[2])
        det = (bx - ax) * (cy - ay) - (cx - ax) * (by - ay)
        if abs(det) < 1e-12:
            continue
        l1 = ((bx - gx) * (cy - gy) - (cx - gx) * (by - gy)) / det
        l2 = ((cx - gx) * (ay - gy) - (ax - gx) * (cy - gy)) / det
        l3 = 1 - l1 - l2
        eps = -1e-6
        inside = (l1 >= eps) & (l2 >= eps) & (l3 >= eps)
        if not inside.any():
            continue
        z = l1 * tz[0] + l2 * tz[1] + l3 * tz[2]
        sub = zbuf[by0:by1 + 1, bx0:bx1 + 1]
        win = inside & (z > sub)
        sub[win] = z[win]
        img[by0:by1 + 1, bx0:bx1 + 1][win] = col[i]
    return img


def preview(m: dict, path: str, title: str) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch

    # azimuth = where the viewer stands: -55 is +x/-y (south-east), 125 is -x/+y (north-west)
    views = [(-55, 35, "from the south-east, above -- false ceiling removed"),
             (125, 22, "from the north-west, low -- false ceiling removed")]
    if m.get("plenums"):
        # the supply grilles sit in the hall's wall, low, between the PODs: seen
        # only along the cold aisles, so one view per plenum wall, straight on
        for p in m["plenums"]:
            az = 0 if p["side"] == "W" else 180
            views.append((az, 48, f"from the {'east' if az == 0 else 'west'}, looking at the {'west' if az == 0 else 'east'} wall "
                                  f"along the cold aisles -- the supply plenum's grilles"))
    imgs = [render(m, a, e) for a, e, _ in views]
    fig, axes = plt.subplots(len(imgs), 1, figsize=(14, 7 * sum(i.shape[0] / i.shape[1] for i in imgs) + 2.2), dpi=130)
    for ax, img, (_, _, sub) in zip(axes, imgs, views):
        ax.imshow(img, interpolation="nearest")
        ax.set_axis_off()
        ax.set_title(sub, fontsize=8, loc="left")
    handles = [Patch(fc=COLOURS[k], ec="#555", label=lbl) for k, lbl in (
        ("racks", "cabinets"), ("containment", "containment: lids, doors, end panels"), ("blank", "blank panels (row line)"),
        ("tiles", "floor tiles (on the deck)"), ("grilles", "return grilles (under the ceiling, drawn floating)"),
        ("cage", "cage panels"), ("units", "cooling units"), ("walls", "dividing walls (mesh below deck, lighter)"),
        ("supplies", "supply grilles in the plenum wall"))]
    fig.legend(handles=handles, loc="lower center", ncol=4, fontsize=7, frameon=False)
    fig.suptitle(title, fontsize=9, x=0.01, ha="left")
    plt.tight_layout(rect=(0, 0.04, 1, 0.98))
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
