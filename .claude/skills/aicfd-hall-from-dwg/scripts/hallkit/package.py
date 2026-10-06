"""The one file AICFD imports: `<name>.aicfd.zip` (AICFD ADR-135).

    manifest.json            format, project, scenarios, the STL's sha256, source, grid alerts
    geometry.stl             the room (the contract STL, renamed)
    figures/<name>.png ...   what the report opens with
    scenarios/<name>.yaml    the sidecar, its geometry.file and figures pointing inside the package
    source/answers.yaml      what the converter was told
    source/export.txt        what it said back

AICFD's `aicfd import` (or the page's Import package) checks the manifest and the
hash, builds the scenario against the geometry, and only then writes
cases/<project>/. This writer mirrors that format; AICFD's reader is the judge.
"""
from __future__ import annotations

import datetime
import hashlib
import io
import json
import os
import re
import zipfile

FORMAT = "aicfd-package/1"


def write(name: str, out_path: str, stl_path: str, yaml_path: str, figures: list[str],
          answers_path: str | None, export_txt: str | None, source: str, alerts: list[str]) -> str:
    with open(stl_path, "rb") as handle:
        stl = handle.read()
    digest = hashlib.sha256(stl).hexdigest()
    with open(yaml_path) as handle:
        text = handle.read()
    text = text.replace(f"file: {os.path.basename(stl_path)}", "file: geometry.stl", 1)
    text = re.sub(r"^(geometry:\s*\{\s*file:\s*geometry\.stl)", rf"\1, sha256: {digest}", text, count=1, flags=re.M)
    for fig in figures:
        text = text.replace(f"{{file: {os.path.basename(fig)},", f"{{file: figures/{os.path.basename(fig)},")
    manifest = {
        "format": FORMAT,
        "project": name,
        "scenarios": [name],
        "geometry": {"file": "geometry.stl", "sha256": digest},
        "source": source,
        "written_by": "aicfd-hall-from-dwg",
        "created": datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat(),
        "grid_alerts": alerts,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        z.writestr("geometry.stl", stl)
        for fig in figures:
            z.write(fig, f"figures/{os.path.basename(fig)}")
        z.writestr(f"scenarios/{name}.yaml", text)
        if answers_path and os.path.isfile(answers_path):
            z.write(answers_path, "source/answers.yaml")
        if export_txt and os.path.isfile(export_txt):
            z.write(export_txt, "source/export.txt")
    with open(out_path, "wb") as f:
        f.write(buffer.getvalue())
    return out_path
