"""The verification plan: what the model holds, drawn so an engineer can check
it against the drawing in one look -- including the containment (lids,
doors, closures), the tiles, the return grilles, the cage and the units.
Coordinates are the model's (metres from the envelope's SW corner)."""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

RACK_FC = "#f5e6c8"      # every cabinet the same colour: the load is written in it
RACK_EC = "#8b5a2b"
RETURN_FC = "#fbe9e7"    # return grilles: the hot side, in a faded red -- they are
RETURN_EC = "#e39a93"    # in the ceiling, drawn as a projection onto the floor plan


def _rack_label(r: dict) -> str:
    kw = r["load_kw"]
    if r["kind"] == "ODF":
        load = "ODF 0 kW"
    elif r["kind"] == "FUTURE":
        load = f"fut {kw:g} kW"
    else:
        load = f"{kw:g} kW"
    return f"{r['id']}\n{load}"


def draw(m: dict, path: str, title: str) -> None:
    env = m["envelope"]
    W, L = env[2], env[3]
    H = m["heights"]
    fig, ax = plt.subplots(figsize=(11, 11 * L / W + 1.6), dpi=160)
    ax.add_patch(Rectangle((0, 0), W, L, fc="#fafafa", ec="#111", lw=2.2, zorder=1))
    for g in m["galleries"]:
        r = g["rect"]
        ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fc="#e9eff7", ec="none", zorder=1))
        ax.text((r[0] + r[2]) / 2, L + 0.35, f"gallery {g['id']}", ha="center", va="bottom", fontsize=7, color="#345")
    for w in m["walls"]:
        ax.plot([w["at"], w["at"]], [0, L], color="#333", lw=3.0, zorder=3, solid_capstyle="butt")
    for p in m.get("plenums", []):
        x0, x1 = sorted((p["wall_at"], p["divider_at"]))
        ax.add_patch(Rectangle((x0, 0), x1 - x0, L, fc="#dbeafe", ec="none", zorder=1.2))
        ax.plot([p["wall_at"], p["wall_at"]], [0, L], color="#333", lw=2.0, zorder=3, solid_capstyle="butt")
        ax.text((x0 + x1) / 2, L / 2, f"supply plenum {p['depth']:.1f} m", rotation=90, ha="center", va="center", fontsize=6, color="#1d4ed8", zorder=4)
    for sg in m.get("supplies", []):
        ax.plot([sg["at"], sg["at"]], [sg["lo"], sg["hi"]], color="#2e86de", lw=5.0, zorder=6, solid_capstyle="butt")
        ax.text(sg["at"] + (0.25 if sg["side"] == "W" else -0.25), (sg["lo"] + sg["hi"]) / 2, f"{sg['id']} {sg['hi'] - sg['lo']:.1f} x {sg['z'][1] - sg['z'][0]:.1f} m",
                rotation=90, ha="center", va="center", fontsize=4.8, color="#1d4ed8", zorder=7)
    # cage
    for p in m["cage"]["panels"]:
        if p["axis"] == "y":
            ax.plot([p["lo"], p["hi"]], [p["at"], p["at"]], color="#7b2cbf", lw=2.6, zorder=4)
        else:
            ax.plot([p["at"], p["at"]], [p["lo"], p["hi"]], color="#7b2cbf", lw=2.6, zorder=4)
    # the raised floor of the hall: 600 mm plates in a faint tone, on the tiles' grid
    hall = m["hall"]
    tile = float(m["config"]["tiles"]["size"])
    if not m.get("has_deck", True):
        pass
    elif m["tiles"]:
        gx0, gy0 = m["tiles"][0]["rect"][0], m["tiles"][0]["rect"][1]
    else:
        gx0, gy0 = hall[0], hall[1]
    x = gx0 - math.ceil((gx0 - hall[0]) / tile) * tile if m.get("has_deck", True) else hall[2]
    while x < hall[2] - 1e-9:
        ax.plot([max(x, hall[0]), max(x, hall[0])], [hall[1], hall[3]], color="#e4e4e4", lw=0.25, zorder=1.5)
        x += tile
    y = gy0 - math.ceil((gy0 - hall[1]) / tile) * tile if m.get("has_deck", True) else hall[3]
    while y < hall[3] - 1e-9:
        ax.plot([hall[0], hall[2]], [max(y, hall[1]), max(y, hall[1])], color="#e4e4e4", lw=0.25, zorder=1.5)
        y += tile
    # return grilles (ceiling plane) under everything else
    for g in m["grilles"]:
        r = g["rect"]
        ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fc=RETURN_FC, ec=RETURN_EC, lw=0.3, ls=(0, (2, 1.5)), zorder=2))
    # tiles
    for t in m["tiles"]:
        r = t["rect"]
        ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fc="#ffffff", ec="#7a9cc6", lw=0.45, zorder=2))
        ax.plot([r[0], r[2]], [r[1], r[3]], color="#b8cbe3", lw=0.3, zorder=2)
        ax.plot([r[0], r[2]], [r[3], r[1]], color="#b8cbe3", lw=0.3, zorder=2)
    # lids, doors, closures
    for lid in m["lids"]:
        r = lid["rect"]
        ax.add_patch(Rectangle((r[0], r[1]), r[2] - r[0], r[3] - r[1], fc="#4a90d9", alpha=0.10, ec="#1f5fa8", lw=1.2, ls="--", zorder=5))
        ca = next(c for c in m["cold_aisles"] if c["id"] == lid["id"])
        ax.text((r[0] + r[2]) / 2, (r[1] + r[3]) / 2, f"{lid['id']}: cold aisle {r[3] - r[1]:.1f} m, lid at {H['rack_top']:.1f} m, {ca['tiles_across']} x {ca['tiles_along']} tiles",
                ha="center", va="center", fontsize=5.6, color="#1f5fa8", zorder=7,
                bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.2))
    for ha in m.get("hot_aisles", []):
        b, sp = ha["band"], ha["span"]
        ax.add_patch(Rectangle((sp[0], b[0]), sp[1] - sp[0], b[1] - b[0], fc="#e07b39", alpha=0.10, ec="#b3541e", lw=1.2, ls="--", zorder=5))
        ax.text((sp[0] + sp[1]) / 2, (b[0] + b[1]) / 2, f"{ha['id']}: hot aisle {b[1] - b[0]:.1f} m, chimney to {H['ceiling']:.1f} m, "
                f"{ha['grilles'].get('count', 0)} return grilles in the ceiling", ha="center", va="center", fontsize=5.6, color="#b3541e", zorder=7,
                bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.2))
    for c in m.get("chimneys", []):
        ax.plot([c["lo"], c["hi"]], [c["at"], c["at"]], color="#b3541e", lw=2.0, zorder=6)
    for d in m["doors"]:
        ax.plot([d["at"], d["at"]], [d["lo"], d["hi"]], color="#1f5fa8" if m.get("containment", "cold") == "cold" else "#b3541e", lw=2.4, zorder=6)
    for c in m["closures"]:
        if c["axis"] == "y":
            ax.plot([c["lo"], c["hi"]], [c["at"], c["at"]], color="#0b3d91", lw=2.6, zorder=6)
        else:
            ax.plot([c["at"], c["at"]], [c["lo"], c["hi"]], color="#0b3d91", lw=2.6, zorder=6)
    for b in m.get("blanks", []):
        if b["axis"] == "y":
            ax.plot([b["lo"], b["hi"]], [b["at"], b["at"]], color="#333", lw=3.2, zorder=9, solid_capstyle="butt")
        else:
            ax.plot([b["at"], b["at"]], [b["lo"], b["hi"]], color="#333", lw=3.2, zorder=9, solid_capstyle="butt")
    for hb in m["hot_bands"]:
        b, s = hb["band"], hb["span"]
        g = hb["grilles"]
        ax.text((s[0] + s[1]) / 2, (b[0] + b[1]) / 2,
                f"{hb['id']}: hot {b[1] - b[0]:.1f} m, {g.get('count', 0)} return grilles in the ceiling (as {g.get('from')}), against {g.get('against')}",
                ha="center", va="center", fontsize=5.6, color="#a1443b", zorder=7,
                bbox=dict(fc="white", ec="none", alpha=0.85, pad=1.2))
    # racks
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            ax.add_patch(Rectangle((q[0], q[1]), q[2] - q[0], q[3] - q[1], fc=RACK_FC, ec=RACK_EC, lw=0.5, zorder=8))
            fy = q[3] if row["front"] > 0 else q[1]
            ax.plot([q[0] + 0.08, q[2] - 0.08], [fy, fy], color="#111", lw=1.6, zorder=9)
            rot = 90 if (q[2] - q[0]) < 0.7 else 0
            ax.text((q[0] + q[2]) / 2, (q[1] + q[3]) / 2, _rack_label(r), ha="center", va="center",
                    fontsize=3.0 if rot else 3.6, rotation=rot, color="#3b2a14", linespacing=1.1, zorder=10)
        s = row["span"]
        ax.text(s[0] - 0.15, (row["band"][0] + row["band"][1]) / 2, row["id"], ha="right", va="center", fontsize=6.5,
                fontweight="bold", color="#5a0f0f", zorder=10)
    # units
    for u in m["units"]:
        b = u["box"]
        ax.add_patch(Rectangle((b[0], b[1]), b[3] - b[0], b[4] - b[1], fc="#cfe3ff", ec="#2e6da4", lw=1.2, zorder=8))
        ax.text((b[0] + b[3]) / 2, (b[1] + b[4]) / 2, u["id"], ha="center", va="center", fontsize=5.5, rotation=90, color="#1a4d80", zorder=9)
        if u.get("dir"):
            sx = 1 if u["dir"] == "+x" else -1
            ax.annotate("", xy=((b[3] if sx > 0 else b[0]) + sx * 0.7, (b[1] + b[4]) / 2), xytext=((b[3] if sx > 0 else b[0]), (b[1] + b[4]) / 2),
                        arrowprops=dict(arrowstyle="->", color="#1a4d80", lw=1.2), zorder=9)

    ax.set_xlim(-2.4, W + 2.6)
    ax.set_ylim(-0.8, L + 1.0)
    ax.set_aspect("equal")
    ax.set_xticks(range(0, int(W) + 1, 2))
    ax.set_yticks(range(0, int(L) + 1, 2))
    ax.tick_params(labelsize=6, length=2)
    ax.set_xlabel("x [m] from the envelope's SW corner", fontsize=7)
    ax.set_ylabel("y [m]", fontsize=7)
    n_racks = sum(len(r["racks"]) for r in m["rows"])
    ax.set_title(title, fontsize=8, loc="left", pad=8)
    handles = [
        Patch(fc=RACK_FC, ec=RACK_EC, label="cabinet: name and load (kW) inside; ODF = no load, fut = future"),
        Line2D([0], [0], color="#111", lw=1.6, label="cabinet front (intake)"),
        Patch(fc="#ffffff", ec="#7a9cc6", label="floor tile 600 (deck) -- cold aisle"),
        Patch(fc="#4a90d9", alpha=0.15, ec="#1f5fa8", ls="--", label=f"cold-aisle lid at {H['rack_top']:.1f} m") if m.get("containment", "cold") == "cold"
        else Patch(fc="#e07b39", alpha=0.15, ec="#b3541e", ls="--", label=f"hot-aisle containment: chimney {H['rack_top']:.1f} -> {H['ceiling']:.1f} m, doors to the ceiling"),
        Line2D([0], [0], color="#1f5fa8", lw=2.4, label="containment door (row end) / end panel"),
        Line2D([0], [0], color="#333", lw=3.2, label="blank panel on the row line (no cabinet there)"),
        Patch(fc=RETURN_FC, ec=RETURN_EC, ls=(0, (2, 1.5)), label=f"return grille 600, projected from the ceiling at {H['ceiling']:.1f} m -- hot side"),
        Line2D([0], [0], color="#7b2cbf", lw=2.6, label=f"cage panel ({m['cage']['construction']}), deck to ceiling"),
        Line2D([0], [0], color="#333", lw=3.0, label="dividing wall (mesh below deck and above ceiling)"),
        Patch(fc="#cfe3ff", ec="#2e6da4", label=f"cooling unit ({m.get('unit_kind', 'downflow')})"),
        Line2D([0], [0], color="#d0d0d0", lw=0.6, label=f"raised floor: {tile * 1000:.0f} mm plates at {H['deck']:.1f} m" if m.get("has_deck", True) else "no raised floor: cabinets on the slab"),
    ]
    if m.get("plenums"):
        handles.append(Patch(fc="#dbeafe", ec="none", label="supply plenum: the cavity between the units' leaf and the hall's wall"))
        handles.append(Line2D([0], [0], color="#2e86de", lw=5.0, label="supply grille in the hall's wall (floor to cabinet height)"))
    ax.legend(handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.045), ncol=3, fontsize=5.8, frameon=False)
    plt.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
