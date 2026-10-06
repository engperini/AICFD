"""The treated DXF: what the model holds, drawn back in the DRAWING's own
coordinates (origin restored, transposition undone, metres) on CFD_* layers,
so the engineer can overlay it on the DWG in AutoCAD and see every cabinet,
tile, lid, door, grille, cage panel and unit exactly where the model has it.
It is an output for conference, not an input: the STL is what AICFD reads."""
from __future__ import annotations

import ezdxf
from ezdxf.enums import TextEntityAlignment

LAYERS = {  # name: ACI colour
    "CFD_ENVELOPE": 1, "CFD_WALLS": 8, "CFD_MESH_OPENINGS": 9, "CFD_RACKS": 2, "CFD_RACKS_TAG": 2,
    "CFD_RACK_FRONT": 7, "CFD_TILES": 4, "CFD_LIDS": 5, "CFD_DOORS": 5, "CFD_GRILLES": 1,
    "CFD_CAGE": 6, "CFD_UNITS": 5, "CFD_UNITS_TAG": 5, "CFD_BLANKS": 8, "CFD_CHIMNEY": 30,
    "CFD_PLENUM": 8, "CFD_SUPPLY": 4,
}


def write(m: dict, path: str) -> None:
    ox, oy = m["origin"]
    T = m["transposed"]

    def P(x, y):
        """model -> drawing coordinates"""
        if T:
            x, y = y, x
        return (x + ox, y + oy)

    doc = ezdxf.new("R2018")
    doc.header["$INSUNITS"] = 6
    for name, colour in LAYERS.items():
        doc.layers.add(name, color=colour)
    msp = doc.modelspace()

    def rect(r, layer):
        pts = [P(r[0], r[1]), P(r[2], r[1]), P(r[2], r[3]), P(r[0], r[3])]
        msp.add_lwpolyline(pts, close=True, dxfattribs={"layer": layer})

    def line(a, b, layer):
        msp.add_line(P(*a), P(*b), dxfattribs={"layer": layer})

    def text(s, x, y, layer, h=0.12, rot=0):
        if T:
            rot = 90 - rot
        msp.add_text(s, dxfattribs={"layer": layer, "height": h, "rotation": rot}).set_placement(P(x, y), align=TextEntityAlignment.MIDDLE_CENTER)

    env = m["envelope"]
    rect(env, "CFD_ENVELOPE")
    for w in m["walls"]:
        line((w["at"], env[1]), (w["at"], env[3]), "CFD_WALLS")
    for p in m.get("plenums", []):
        line((p["wall_at"], env[1]), (p["wall_at"], env[3]), "CFD_PLENUM")
        text(f"supply plenum {p['depth']:.1f} m", (p["wall_at"] + p["divider_at"]) / 2, (env[1] + env[3]) / 2, "CFD_PLENUM", 0.2, 90)
    for sg in m.get("supplies", []):
        line((sg["at"], sg["lo"]), (sg["at"], sg["hi"]), "CFD_SUPPLY")
        text(f"supply grille {sg['id']} {sg['hi'] - sg['lo']:.1f} x {sg['z'][1] - sg['z'][0]:.1f} m", sg["at"], (sg["lo"] + sg["hi"]) / 2, "CFD_SUPPLY", 0.12, 90)
    for row in m["rows"]:
        for r in row["racks"]:
            q = r["rect"]
            rect(q, "CFD_RACKS")
            fy = q[3] if row["front"] > 0 else q[1]
            line((q[0], fy), (q[2], fy), "CFD_RACK_FRONT")
            text(r["id"], (q[0] + q[2]) / 2, (q[1] + q[3]) / 2, "CFD_RACKS_TAG", 0.1, 90 if q[2] - q[0] < 0.7 else 0)
    for t in m["tiles"]:
        rect(t["rect"], "CFD_TILES")
    for g in m["grilles"]:
        rect(g["rect"], "CFD_GRILLES")
    for lid in m["lids"]:
        rect(lid["rect"], "CFD_LIDS")
        r = lid["rect"]
        text(f"{lid['id']} lid z={lid['at']:.1f}", (r[0] + r[2]) / 2, (r[1] + r[3]) / 2, "CFD_LIDS", 0.15)
    for d in m["doors"]:
        line((d["at"], d["lo"]), (d["at"], d["hi"]), "CFD_DOORS")
    for c in m["closures"]:
        if c["axis"] == "y":
            line((c["lo"], c["at"]), (c["hi"], c["at"]), "CFD_DOORS")
        else:
            line((c["at"], c["lo"]), (c["at"], c["hi"]), "CFD_DOORS")
    for c in m.get("chimneys", []):
        line((c["lo"], c["at"]), (c["hi"], c["at"]), "CFD_CHIMNEY")
    for b in m.get("blanks", []):
        if b["axis"] == "y":
            line((b["lo"], b["at"]), (b["hi"], b["at"]), "CFD_BLANKS")
        else:
            line((b["at"], b["lo"]), (b["at"], b["hi"]), "CFD_BLANKS")
    for p in m["cage"]["panels"]:
        if p["axis"] == "y":
            line((p["lo"], p["at"]), (p["hi"], p["at"]), "CFD_CAGE")
        else:
            line((p["at"], p["lo"]), (p["at"], p["hi"]), "CFD_CAGE")
    for u in m["units"]:
        b = u["box"]
        rect((b[0], b[1], b[3], b[4]), "CFD_UNITS")
        text(u["id"], (b[0] + b[3]) / 2, (b[1] + b[4]) / 2, "CFD_UNITS_TAG", 0.15, 90)
    doc.saveas(path)
