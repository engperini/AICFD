#!/usr/bin/env python3
"""A data hall drawing -> the files AICFD runs, in one pass.

    python3 hall_from_dwg.py --dwg LAYOUT.dwg --answers answers.yaml --out OUT/
    python3 hall_from_dwg.py --dxf LAYOUT.dxf --answers answers.yaml --out OUT/

The DWG is the input. It is decoded with the bundled LibreDWG converter into
OUT/work/<file>.raw.dxf (an intermediate, not a deliverable); a DXF the user
exported from AutoCAD is accepted with --dxf when a DWG will not decode.

Writes into OUT/:
    <name>.png          the verification plan (racks, fronts, lids, doors, tiles, grilles, cage, units)
    <name>.dxf          the treated drawing: the model on CFD_* layers, in the DWG's own coordinates, to overlay
    <name>.stl          the named-solid geometry AICFD reads (hall box, zero-thickness panels)
    <name>.view.stl     the same model to LOOK AT in another program: no hall box, 20 mm plates,
                        tiles on the deck, grilles under the ceiling; <name>.view/ has one STL per type
    <name>.3d.png       two isometric previews of the view model
    <name>.sections.png a longitudinal section through the rows and cross sections through a cold aisle and a hot band
    <name>.yaml         the sidecar case file (loads, plant, components, mesh, solver, the figures for the report)
    <name>.model.json   the model the four above were written from
    <name>.export.txt   corrections, quantisations, omissions, open questions, self-check
    <name>.aicfd.zip    THE DELIVERABLE: geometry, scenario, figures and answers in one file,
                        for `aicfd import` or the page's Import package (AICFD ADR-135).
                        Written only when the self-check passes.

Exit code 0: written and self-check passed (questions may remain: read them).
Exit code 1: refused -- the message says what the drawing or answers.yaml must supply.
Exit code 3: written, but the self-check failed (the files are not to be run).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import yaml  # noqa: E402

from hallkit import check, dxfout, dxfread, extract, model, package, plan, render, report, sections, sidecar, stl, viewstl  # noqa: E402

DWG2DXF = os.path.join(HERE, "..", "bin", "dwg2dxf")
REFERENCE = os.path.join(HERE, "..", "reference", "reference.yaml")


def convert(dwg: str, out_dir: str) -> str:
    work = os.path.join(out_dir, "work")
    os.makedirs(work, exist_ok=True)
    dxf = os.path.join(work, os.path.splitext(os.path.basename(dwg))[0] + ".raw.dxf")
    if os.path.exists(dxf):
        os.remove(dxf)
    r = subprocess.run([DWG2DXF, "-y", "-o", dxf, dwg], capture_output=True, text=True, timeout=900)
    if not os.path.exists(dxf) or os.path.getsize(dxf) < 1000:
        sys.exit("DWG could not be decoded (structurally damaged or an unsupported version). Ask for the DWG zipped, "
                 "a DXF 2018 export, or the DWG saved as 2013.\n" + (r.stderr or r.stdout)[-800:])
    return dxf


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dwg")
    ap.add_argument("--dxf")
    ap.add_argument("--answers", help="answers.yaml: the hall's name/anchor, loads, overrides of the reference")
    ap.add_argument("--reference", default=REFERENCE)
    ap.add_argument("--out", default="out")
    ap.add_argument("--units", choices=["m", "mm", "cm"], help="override the drawing's $INSUNITS")
    a = ap.parse_args()
    if not (a.dwg or a.dxf):
        ap.error("give --dwg or --dxf")
    os.makedirs(a.out, exist_ok=True)
    ref = yaml.safe_load(open(a.reference))
    answers = yaml.safe_load(open(a.answers)) if a.answers else {}
    answers = answers or {}
    src = a.dwg or a.dxf
    dxf = convert(a.dwg, a.out) if a.dwg else a.dxf
    scale = {"m": 1.0, "mm": 0.001, "cm": 0.01}.get(a.units) if a.units else None

    # the drawing as read is cached beside the raw DXF: a 200 MB architect's file
    # takes minutes to read and seconds to classify, and answers change often
    import pickle
    work = os.path.join(a.out, "work")
    os.makedirs(work, exist_ok=True)
    cache = os.path.join(work, os.path.basename(dxf) + ".read.pkl")
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(dxf):
        with open(cache, "rb") as f:
            dwg = pickle.load(f)
    else:
        dwg = dxfread.read(dxf, unit_override=scale, tol=float(ref.get("tolerance_m", 0.02)))
        with open(cache, "wb") as f:
            pickle.dump(dwg, f)
    ex = extract.extract(dwg, ref, answers)
    m = model.build(ex, ref, answers)
    name = m["name"]
    sha = hashlib.sha256(open(src, "rb").read()).hexdigest()[:16]
    m["source"] = (m["source"] + "; " if m["source"] else "") + f"{os.path.basename(src)} sha256 {sha}"

    stl_path = os.path.join(a.out, f"{name}.stl")
    yaml_path = os.path.join(a.out, f"{name}.yaml")
    png_path = os.path.join(a.out, f"{name}.png")
    dxf_path = os.path.join(a.out, f"{name}.dxf")
    view_path = os.path.join(a.out, f"{name}.view.stl")
    view_dir = os.path.join(a.out, f"{name}.view")
    iso_path = os.path.join(a.out, f"{name}.3d.png")
    sec_path = os.path.join(a.out, f"{name}.sections.png")
    json_path = os.path.join(a.out, f"{name}.model.json")
    txt_path = os.path.join(a.out, f"{name}.export.txt")

    counts = stl.write(m, stl_path)
    m["config"]["_answers"] = answers
    figures = [
        {"file": f"{name}.png", "caption": "The layout as read from the drawing: cabinets with their loads and fronts, "
                                          "the containment, the tiles, the return grilles, the cage and the cooling units."},
        {"file": f"{name}.3d.png", "caption": "The 3D model built from the drawing, false ceiling removed."},
        {"file": f"{name}.sections.png", "caption": "Sections through the rows, a contained aisle and the supply path: the heights and closures."},
    ]
    doc = sidecar.build(m, f"{name}.stl")
    doc["figures"] = figures   # AICFD prints them in the report's basis of design (ADR-130)
    sidecar.write(doc, yaml_path, m)
    with open(json_path, "w") as f:
        json.dump(m, f, indent=1, default=float)
    n_racks = sum(len(r["racks"]) for r in m["rows"])
    title = (f"{name} -- {os.path.basename(src)} -- AICFD geometry, grid {m['cell'][0]} m\n"
             f"{n_racks} cabinets / {len(m['rows'])} rows / {m['loads']['total_kw']:.1f} kW to the air | "
             f"{len(m['cold_aisles'])} contained cold aisles / {len(m.get('hot_aisles', []))} contained hot aisles, "
             f"{len(m['tiles'])} tiles | {len(m['grilles'])} return grilles | {len(m['units'])} units ({m.get('unit_kind')}) | "
             f"deck {m['heights']['deck']:.1f} m, ceiling {m['heights']['ceiling']:.1f} m, slab {m['heights']['slab']:.1f} m")
    plan.draw(m, png_path, title)
    dxfout.write(m, dxf_path)
    viewstl.write(m, view_path, view_dir)
    render.preview(m, iso_path, f"{name} -- 3D view model (deck {m['heights']['deck']:.1f} m, rack top {m['heights']['rack_top']:.1f} m, "
                    f"ceiling {m['heights']['ceiling']:.1f} m, slab {m['heights']['slab']:.1f} m)")
    sections.draw(m, sec_path, f"{name} -- sections of the model (deck {m['heights']['deck']:.1f} m, rack top {m['heights']['rack_top']:.1f} m, "
                  f"ceiling {m['heights']['ceiling']:.1f} m, slab {m['heights']['slab']:.1f} m)")
    fails = check.run(stl_path, yaml_path, m["cell"])
    text = report.write(m, counts, txt_path, os.path.basename(src), fails)
    print(text)
    written = [png_path, iso_path, sec_path, dxf_path, stl_path, view_path, view_dir + "/", yaml_path, json_path, txt_path]
    if not fails:
        zip_path = os.path.join(a.out, f"{name}.aicfd.zip")
        package.write(name, zip_path, stl_path, yaml_path, [png_path, iso_path, sec_path],
                      a.answers, txt_path, m["source"], (m.get("log") or {}).get("alerts", []))
        written.insert(0, zip_path)
    print("written:", *written, sep="\n  ")
    return 3 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
