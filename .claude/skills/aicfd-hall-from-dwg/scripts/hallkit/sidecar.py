"""The sidecar YAML: AICFD's case file without the room-building keys."""
from __future__ import annotations

import yaml

FORBIDDEN = ("pods", "aisles", "gallery", "hall", "floor", "grilles", "containment", "plenum")


def build(m: dict, stl_name: str) -> dict:
    cfg = m["config"]
    default = m["loads"]["default_kw"]
    loads = {}
    for row in m["rows"]:
        for r in row["racks"]:
            if abs(r["load_kw"] - default) > 1e-9:
                loads[r["id"]] = r["load_kw"]
    fan = dict(cfg["fanwall"])
    for k in ("count", "width", "height", "depth", "offset", "pitch"):
        fan.pop(k, None)
    if m.get("unit_kind") == "fanwall" and "fanwall" not in (cfg.get("_answers") or {}):
        fan["model"] = "TBD"   # the reference's downflow unit does not apply to a fan-wall hall
    racks = {"load_kw": default, "airflow_cfm_per_kw": cfg["racks"]["airflow_cfm_per_kw"]}
    if loads:
        racks["loads"] = loads
    out = {
        "name": m["name"],
        "geometry": {"file": stl_name, "source": m["source"]},
        "site": dict(cfg["site"]),
        "racks": racks,
        "fanwall": fan,
        "components": dict(cfg["components"]),
        "cage": {"construction": cfg["cage"]["construction"]},
        "mesh": {"cell_size": list(m["cell"])},
        "solver": dict(cfg["solver"]),
    }
    return out


def write(doc: dict, path: str, m: dict | None = None) -> None:
    header = (
        "# AICFD case -- geometry from the STL beside it; this file carries only what a\n"
        "# drawing cannot say. Written by aicfd-hall-from-dwg. The room is the drawing's\n"
        "# and is changed by importing it again; everything below is edited here or on\n"
        "# the page, one scenario per file (ADR-134).\n"
    )
    if m and m.get("loads", {}).get("liquid"):
        header += (f"# LIQUID-COOLED HALL: racks.loads carry the AIR share of each cabinet's IT load\n"
                   f"# (IT {m['loads']['it_kw']:.0f} kW, to the air {m['loads']['total_kw']:.0f} kW); the rest leaves in the coolant.\n")
    with open(path, "w") as f:
        f.write(header)
        yaml.safe_dump(doc, f, sort_keys=False, allow_unicode=True, default_flow_style=None)
