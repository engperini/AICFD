"""Section views of the model, for checking the heights and the closures:

  * a longitudinal section (y-z) through the middle of the rows: every row
    in profile, the lids over the cold aisles, the tiles under them, the
    return grilles over the hot bands, the cage panel, the deck, the false
    ceiling, the slab;
  * a cross section (x-z) through a cold aisle: the units in the galleries,
    the dividing walls with their mesh openings below the deck and above the
    ceiling, the tiles, the lid and the doors at the row ends;
  * a cross section (x-z) through a hot band: the grilles in the ceiling.

Drawn from the model's rectangles, so what is shown is what the STL holds.
"""
from __future__ import annotations

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402

C = {"rack": ("#f5e6c8", "#8b5a2b"), "unit": ("#cfe3ff", "#2e6da4"), "lid": "#1f5fa8", "door": "#1f5fa8",
     "tile": "#4a6fa0", "grille": "#c0605a", "cage": "#7b2cbf", "wall": "#333", "mesh": "#7d7d7d",
     "deck": "#555", "ceiling": "#555", "slab": "#222"}


def _frame(ax, m, horiz: str):
    """The building envelope in section: slab, plenum, deck, hall, ceiling, return plenum."""
    H = m["heights"]
    env = m["envelope"]
    lo, hi = (env[0], env[2]) if horiz == "x" else (env[1], env[3])
    ax.add_patch(Rectangle((lo, 0), hi - lo, H["slab"], fc="#fbfbfb", ec="none", zorder=0))
    if m.get("has_deck", True):
        ax.add_patch(Rectangle((lo, 0), hi - lo, H["deck"], fc="#eef2f7", ec="none", zorder=0))      # supply plenum
    ax.add_patch(Rectangle((lo, H["ceiling"]), hi - lo, H["slab"] - H["ceiling"], fc="#fdeeee", ec="none", zorder=0))  # return plenum
    ax.plot([lo, hi], [0, 0], color=C["slab"], lw=2.5, zorder=3)
    ax.plot([lo, hi], [H["slab"], H["slab"]], color=C["slab"], lw=2.5, zorder=3)
    if m.get("has_deck", True):
        ax.plot([lo, hi], [H["deck"], H["deck"]], color=C["deck"], lw=1.4, zorder=3)
    ax.plot([lo, lo], [0, H["slab"]], color=C["slab"], lw=2.5, zorder=3)
    ax.plot([hi, hi], [0, H["slab"]], color=C["slab"], lw=2.5, zorder=3)
    if m.get("has_deck", True):
        ax.text(lo + 0.2, H["deck"] / 2, f"supply plenum {H['deck']:.1f} m", fontsize=6, va="center", color="#456")
    ax.text(lo + 0.2, (H["ceiling"] + H["slab"]) / 2, f"return plenum {H['slab'] - H['ceiling']:.1f} m", fontsize=6, va="center", color="#844")


def _hits(rect, axis, at):
    """True when the plan rect is crossed by the section plane axis = at."""
    return rect[axis] <= at <= rect[axis + 2]


def longitudinal(ax, m, x_cut):
    H = m["heights"]
    hall = m["hall"]
    _frame(ax, m, "y")
    ax.plot([hall[1], hall[3]], [H["ceiling"], H["ceiling"]], color=C["ceiling"], lw=1.4, zorder=3)
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            if _hits(q, 0, x_cut):
                ax.add_patch(Rectangle((q[1], H.get("floor", H["deck"])), q[3] - q[1], H["rack_top"] - H.get("floor", H["deck"]), fc=C["rack"][0], ec=C["rack"][1], lw=0.8, zorder=4))
                fy = q[3] if row["front"] > 0 else q[1]
                ax.plot([fy, fy], [H.get("floor", H["deck"]), H["rack_top"]], color="#111", lw=1.2, ls=(0, (2, 1)), zorder=5)  # intake face marker, not a panel
                ax.text((q[1] + q[3]) / 2, H.get("floor", H["deck"]) + 0.3, r["id"], fontsize=5, ha="center", rotation=90, color="#5a0f0f", zorder=6)
        ax.text((row["band"][0] + row["band"][1]) / 2, H["rack_top"] + 0.12, row["id"], fontsize=6.5, ha="center", fontweight="bold", color="#5a0f0f")
    for lid in m["lids"]:
        r = lid["rect"]
        if _hits(r, 0, x_cut):
            ax.plot([r[1], r[3]], [lid["at"], lid["at"]], color=C["lid"], lw=3, zorder=5)
    for c in m["closures"]:
        if c["axis"] == "y" and c["lo"] <= x_cut <= c["hi"]:
            ax.plot([c["at"], c["at"]], [c["z"][0], c["z"][1]], color=C["door"], lw=3, zorder=5)
    for t in m["tiles"]:
        r = t["rect"]
        if _hits(r, 0, x_cut):
            ax.add_patch(Rectangle((r[1], H["deck"] - 0.04), r[3] - r[1], 0.08, fc=C["tile"], ec="none", zorder=5))
    for g in m["grilles"]:
        r = g["rect"]
        if _hits(r, 0, x_cut):
            ax.add_patch(Rectangle((r[1], H["ceiling"] - 0.04), r[3] - r[1], 0.08, fc=C["grille"], ec="none", zorder=5))
    for p in m["cage"]["panels"]:
        if p["axis"] == "y" and p["lo"] <= x_cut <= p["hi"]:
            ax.plot([p["at"], p["at"]], [p["z"][0], p["z"][1]], color=C["cage"], lw=2.5, zorder=5)
    for ca in m["cold_aisles"]:
        b = ca["band"]
        ax.text((b[0] + b[1]) / 2, H.get("floor", H["deck"]) + 0.55, f"{ca['id']}\ncold", fontsize=5.5, ha="center", color=C["lid"])
    for ha in m.get("hot_aisles", []):
        b = ha["band"]
        if ha["span"][0] <= x_cut <= ha["span"][1]:
            ax.text((b[0] + b[1]) / 2, H.get("floor", H["deck"]) + 0.55, f"{ha['id']}\nhot", fontsize=5.5, ha="center", color=C["grille"])
    for c in m.get("chimneys", []):
        if c["lo"] <= x_cut <= c["hi"]:
            ax.plot([c["at"], c["at"]], [c["z"][0], c["z"][1]], color="#b3541e", lw=3, zorder=5)
    for hb in m["hot_bands"]:
        b = hb["band"]
        ax.text((b[0] + b[1]) / 2, H["ceiling"] - 0.5, f"{hb['id']}\nhot", fontsize=5.5, ha="center", color=C["grille"])
    ax.set_xlim(m["envelope"][1] - 0.3, m["envelope"][3] + 0.3)
    ax.set_ylim(-0.3, H["slab"] + 0.3)
    ax.set_aspect("equal")
    ax.set_xlabel("y [m]", fontsize=7)
    ax.set_ylabel("z [m]", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_title(f"Longitudinal section at x = {x_cut:.1f} m (through the rows) -- looking towards -x. "
                 "Dotted black = cabinet intake face (a marker, not a panel); blue = lid / door / end panel", fontsize=8, loc="left")


def cross(ax, m, y_cut, label):
    H = m["heights"]
    hall = m["hall"]
    env = m["envelope"]
    _frame(ax, m, "x")
    cr = m.get("ceiling_rect", hall)
    ax.plot([cr[0], cr[2]], [H["ceiling"], H["ceiling"]], color=C["ceiling"], lw=1.4, zorder=3)
    for p in m.get("plenums", []):
        x0, x1 = sorted((p["wall_at"], p["divider_at"]))
        ax.add_patch(Rectangle((x0, 0), x1 - x0, H["ceiling"], fc="#dbeafe", ec="none", zorder=1))
        ax.plot([p["wall_at"], p["wall_at"]], [H.get("floor", H["deck"]), H["ceiling"]], color=C["wall"], lw=3, zorder=4)
        ax.text((x0 + x1) / 2, H["ceiling"] - 0.4, f"supply\nplenum\n{p['depth']:.1f} m", fontsize=5, ha="center", va="top", color="#1d4ed8", zorder=6)
    for sg in m.get("supplies", []):
        if sg["lo"] <= y_cut <= sg["hi"]:
            ax.plot([sg["at"], sg["at"]], [sg["z"][0], sg["z"][1]], color="#2e86de", lw=5, zorder=5, solid_capstyle="butt")
            ax.text(sg["at"], sg["z"][1] + 0.1, f"supply grille {sg['id']}", fontsize=5, ha="center", color="#1d4ed8", zorder=6)
    for w in m["walls"]:
        ax.plot([w["at"], w["at"]], [H.get("floor", H["deck"]), H["ceiling"]], color=C["wall"], lw=3, zorder=4)
        if m.get("has_deck", True):
            ax.plot([w["at"], w["at"]], [0, H["deck"]], color=C["mesh"], lw=2, ls=(0, (1.5, 1.5)), zorder=4)
        ax.plot([w["at"], w["at"]], [H["ceiling"], H["slab"]], color=C["mesh"], lw=2, ls=(0, (1.5, 1.5)), zorder=4)
        ax.text(w["at"], H["slab"] + 0.08, "wall / mesh above and below", fontsize=5, ha="center", color="#555")
    for u in m["units"]:
        b = u["box"]
        if b[1] <= y_cut <= b[4]:
            ax.add_patch(Rectangle((b[0], b[2]), b[3] - b[0], b[5] - b[2], fc=C["unit"][0], ec=C["unit"][1], lw=0.8, zorder=4))
            ax.text((b[0] + b[3]) / 2, (b[2] + b[5]) / 2, u["id"], fontsize=5.5, ha="center", va="center", rotation=90, color="#1a4d80", zorder=6)
            if u.get("dir"):
                sx = 1 if u["dir"] == "+x" else -1
                ax.annotate("", xy=((b[3] if sx > 0 else b[0]) + sx * 0.8, (b[2] + b[5]) / 2), xytext=((b[3] if sx > 0 else b[0]), (b[2] + b[5]) / 2),
                            arrowprops=dict(arrowstyle="->", color="#1a4d80", lw=1.0))
            else:
                ax.annotate("", xy=((b[0] + b[3]) / 2, b[5] + 0.5), xytext=((b[0] + b[3]) / 2, b[5] + 0.05), arrowprops=dict(arrowstyle="<-", color="#1a4d80", lw=0.8))
                ax.annotate("", xy=((b[0] + b[3]) / 2, b[2] - 0.5), xytext=((b[0] + b[3]) / 2, b[2] - 0.05), arrowprops=dict(arrowstyle="->", color="#1a4d80", lw=0.8))
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            if _hits(q, 1, y_cut):
                ax.add_patch(Rectangle((q[0], H.get("floor", H["deck"])), q[2] - q[0], H["rack_top"] - H.get("floor", H["deck"]), fc=C["rack"][0], ec=C["rack"][1], lw=0.6, zorder=4))
    for lid in m["lids"]:
        r = lid["rect"]
        if _hits(r, 1, y_cut):
            ax.plot([r[0], r[2]], [lid["at"], lid["at"]], color=C["lid"], lw=3, zorder=5)
            ax.text((r[0] + r[2]) / 2, lid["at"] + 0.1, f"lid {lid['id']} at {lid['at']:.1f} m", fontsize=5.5, ha="center", color=C["lid"])
    for ha in m.get("hot_aisles", []):
        if ha["band"][0] <= y_cut <= ha["band"][1]:
            for xx in (ha["span"][0], ha["span"][1]):
                pass
    for c in m.get("chimneys", []):
        # a chimney panel lies in a y plane along the aisle; seen across the aisle it is the aisle's two edges
        pass
    for d in m["doors"]:
        if d["lo"] <= y_cut <= d["hi"]:
            ax.plot([d["at"], d["at"]], [d["z"][0], d["z"][1]], color=C["door"], lw=3, zorder=5)
    for t in m["tiles"]:
        r = t["rect"]
        if _hits(r, 1, y_cut):
            ax.add_patch(Rectangle((r[0], H["deck"] - 0.04), r[2] - r[0], 0.08, fc=C["tile"], ec="none", zorder=5))
    for g in m["grilles"]:
        r = g["rect"]
        if _hits(r, 1, y_cut):
            ax.add_patch(Rectangle((r[0], H["ceiling"] - 0.04), r[2] - r[0], 0.08, fc=C["grille"], ec="none", zorder=5))
    for p in m["cage"]["panels"]:
        if p["axis"] == "x" and p["lo"] <= y_cut <= p["hi"]:
            ax.plot([p["at"], p["at"]], [p["z"][0], p["z"][1]], color=C["cage"], lw=2.5, zorder=5)
    for b in m.get("blanks", []):
        if b["axis"] == "x" and b["lo"] <= y_cut <= b["hi"]:
            ax.plot([b["at"], b["at"]], [b["z"][0], b["z"][1]], color="#333", lw=3, zorder=5)
    ax.set_xlim(env[0] - 0.3, env[2] + 0.3)
    ax.set_ylim(-0.3, H["slab"] + 0.3)
    ax.set_aspect("equal")
    ax.set_xlabel("x [m]", fontsize=7)
    ax.set_ylabel("z [m]", fontsize=7)
    ax.tick_params(labelsize=6)
    ax.set_title(f"Cross section at y = {y_cut:.1f} m ({label}) -- looking towards +y", fontsize=8, loc="left")


def draw(m: dict, path: str, title: str) -> None:
    rows = m["rows"]
    # the longitudinal cut goes through the middle of the longest row, so it
    # crosses cabinets in every pod column that row's x covers (a cut at the
    # hall's centre can fall in the walkway between two columns of pods)
    longest = max(rows, key=lambda r: r["span"][1] - r["span"][0]) if rows else None
    x_cut = round((longest["span"][0] + longest["span"][1]) / 2, 1) if longest else (m["hall"][0] + m["hall"][2]) / 2
    if longest:
        # land on a cabinet, not on a blank
        mids = [((k["rect"][0] + k["rect"][2]) / 2) for k in longest["racks"]]
        x_cut = round(min(mids, key=lambda v: abs(v - x_cut)), 2)
    cold = m["cold_aisles"][0] if m["cold_aisles"] else None
    hot = next((h for h in m["hot_bands"] if h["grilles"].get("count")), m["hot_bands"][0] if m["hot_bands"] else None)
    if hot is None and m.get("hot_aisles"):
        hot = m["hot_aisles"][0]
    if cold is None and m.get("hot_aisles") and len(m["hot_aisles"]) > 1:
        cold = None
    row_cut = None
    if cold is None and rows:
        row_cut = longest   # a hot-contained hall: cut across a row to show the cabinet and the chimney over it
    supply = m["supplies"][0] if m.get("supplies") else None
    n = 1 + (cold is not None or row_cut is not None) + (hot is not None) + (supply is not None)
    W = m["envelope"][2] - m["envelope"][0]
    L = m["envelope"][3] - m["envelope"][1]
    fig = plt.figure(figsize=(15, 4.2 + 3.4 * (n - 1)), dpi=150)
    gs = fig.add_gridspec(n, 1, height_ratios=[1.0] + [W / L * 1.05] * (n - 1))
    ax = fig.add_subplot(gs[0])
    longitudinal(ax, m, x_cut)
    k = 1
    if cold is not None:
        y = round((cold["band"][0] + cold["band"][1]) / 2, 2)
        cross(fig.add_subplot(gs[k]), m, y, f"cold aisle {cold['id']}, {cold['tiles_across']} tiles across")
        k += 1
    elif row_cut is not None:
        y = round((row_cut["band"][0] + row_cut["band"][1]) / 2, 2)
        cross(fig.add_subplot(gs[k]), m, y, f"row {row_cut['id']}: cabinets with the chimney over their backs")
        k += 1
    if hot is not None:
        y = round((hot["band"][0] + hot["band"][1]) / 2, 2)
        cross(fig.add_subplot(gs[k]), m, y, f"hot aisle {hot['id']}, {hot['grilles'].get('count', 0)} return grilles in the ceiling")
        k += 1
    if supply is not None:
        y = round((supply["lo"] + supply["hi"]) / 2, 2)
        cross(fig.add_subplot(gs[k]), m, y, f"supply grille {supply['id']}: the units' leaf, the plenum and the grille in the hall's wall")
    fig.suptitle(title, fontsize=9, x=0.01, ha="left")
    plt.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
