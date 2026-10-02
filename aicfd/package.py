"""One file that carries a hall read from a drawing: `<project>.aicfd.zip` (ADR-135).

A drawn hall is five files or more -- the STL, the scenario, three figures,
the converter's answers -- and moving them by hand is how a report went out
without its drawings and a sidecar was pasted in without the STL it names. So
the converter writes one package, and AICFD takes one package::

    manifest.json            format, project, scenarios, the STL's sha256, source
    geometry.stl             the room
    figures/<file>           what the report opens with
    scenarios/<name>.yaml    one or more scenarios; geometry.file: geometry.stl
    source/<file>            what the converter was told and what it said

Importing checks the package, builds every scenario against the geometry it
came with, and only then writes `cases/<project>/` -- a package that does not
build leaves nothing behind (ADR-055). Each scenario is stamped with the STL's
sha256, and a geometry that no longer matches it is refused by name: a room
replaced by hand under scenarios that were judged against another one is not
the same study (ADR-135).

The same layout comes back out of `export`, so a scenario can be handed to a
colleague as the file it came in as.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import io
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

from aicfd import cases as case_store

FORMAT = "aicfd-package/1"
GEOMETRY = "geometry.stl"
#: The largest package taken. The 15 MW hall is under 2 MB.
MAX_BYTES = 200 * 1024 * 1024
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
#: What a package may hold, and nothing else: a member outside these is
#: refused by name rather than unpacked somewhere it was not expected.
MEMBERS = (
    re.compile(r"^manifest\.json$"),
    re.compile(r"^geometry\.stl$"),
    re.compile(r"^figures/[A-Za-z0-9][A-Za-z0-9._-]*\.(png|jpe?g|svg)$"),
    re.compile(r"^scenarios/[A-Za-z0-9][A-Za-z0-9_-]{0,63}\.yaml$"),
    re.compile(r"^source/[A-Za-z0-9][A-Za-z0-9._-]*\.(ya?ml|txt|json|md|csv)$"),
)


class PackageError(ValueError):
    """A package that cannot be imported, and why, in a sentence."""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stamp(text: str, digest: str) -> str:
    """The scenario's `geometry` block with the STL's sha256 in it.

    Text, not a dump: the scenario keeps every comment the converter wrote.
    The converter writes the block in flow style on one or two lines, which is
    what this edits; a block it cannot find is left alone and the scenario
    simply carries no lock."""
    if re.search(r"^geometry:.*\bsha256:", text, re.M):
        return re.sub(r"(\bsha256:\s*)[0-9a-f]+", rf"\g<1>{digest}", text, count=1)
    out, n = re.subn(r"^(geometry:\s*\{\s*file:\s*[^,}\n]+)", rf"\1, sha256: {digest}", text, count=1, flags=re.M)
    if n:
        return out
    return re.sub(r"^(geometry:\s*\n(\s+)file:[^\n]*\n)", rf"\1\2sha256: {digest}\n", text, count=1, flags=re.M)


def _read(data: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(data) > MAX_BYTES:
        raise PackageError(f"the package is {len(data) / 1e6:.0f} MB; the most taken is {MAX_BYTES / 1e6:.0f} MB")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise PackageError("that is not a .aicfd.zip package (not a zip file)") from None
    members: dict[str, bytes] = {}
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = info.filename.replace("\\", "/")
        if not any(p.match(name) for p in MEMBERS):
            raise PackageError(f"the package holds {name!r}, which is not part of the format "
                               f"(manifest.json, geometry.stl, figures/, scenarios/, source/)")
        members[name] = archive.read(info)
    if "manifest.json" not in members:
        raise PackageError("the package has no manifest.json")
    try:
        manifest = json.loads(members["manifest.json"])
    except ValueError as broken:
        raise PackageError(f"manifest.json is not JSON: {broken}") from None
    if manifest.get("format") != FORMAT:
        raise PackageError(f"manifest.json says format {manifest.get('format')!r}; this AICFD reads {FORMAT!r}")
    if GEOMETRY not in members:
        raise PackageError("the package has no geometry.stl")
    stated = str((manifest.get("geometry") or {}).get("sha256") or "")
    actual = hashlib.sha256(members[GEOMETRY]).hexdigest()
    if stated and stated != actual:
        raise PackageError("geometry.stl does not match the sha256 in manifest.json: the room was changed after "
                           "the package was written. Write the package again from the drawing")
    if not any(n.startswith("scenarios/") for n in members):
        raise PackageError("the package has no scenario (scenarios/<name>.yaml)")
    return manifest, members


def import_package(data: bytes, root: Path, project: str | None = None) -> dict:
    """Unpack a package into ``root/<project>/``, or refuse and write nothing.

    ``project`` names the folder; it defaults to the manifest's. A project
    that already exists is refused: importing over it would replace the room
    under scenarios that were judged against the old one."""
    from aicfd.model import build_model

    manifest, members = _read(data)
    project = (project or manifest.get("project") or "").strip()
    if not NAME.match(project):
        raise PackageError(f"{project!r} is not a project name: letters, digits, - and _ only")
    root = Path(root)
    target = root / project
    if target.exists() or (root / f"{project}.yaml").exists():
        raise PackageError(f"a project called {project!r} is already in cases/. Import it under another name, "
                           f"or remove the old one first")
    digest = hashlib.sha256(members[GEOMETRY]).hexdigest()
    scenarios = sorted(n[len("scenarios/"):-len(".yaml")] for n in members if n.startswith("scenarios/"))
    for name in scenarios:
        if case_store.exists(name, root):
            raise PackageError(f"a case called {name!r} already exists under cases/; a scenario name is a run "
                               f"directory and has to be unique. Rename it in the package")
    root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".importing-{project}-", dir=root))
    try:
        (staging / "figures").mkdir()
        (staging / case_store.SOURCE).mkdir()
        for member, blob in members.items():
            if member == "manifest.json":
                continue
            if member.startswith("scenarios/"):
                text = stamp(blob.decode("utf-8"), digest)
                (staging / member[len("scenarios/"):]).write_text(text)
            else:
                (staging / member).write_bytes(blob)
        manifest = {**manifest, "project": project, "imported": _now()}
        (staging / case_store.SOURCE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        for name in scenarios:
            path = staging / f"{name}.yaml"
            try:
                spec = yaml.safe_load(path.read_text()) or {}
            except yaml.YAMLError as broken:
                raise PackageError(f"scenarios/{name}.yaml is not YAML: {broken}") from None
            if str((spec.get("geometry") or {}).get("file")) != GEOMETRY:
                raise PackageError(f"scenarios/{name}.yaml names geometry.file "
                                   f"{(spec.get('geometry') or {}).get('file')!r}; in a package it is {GEOMETRY!r}")
            if spec.get("name") != name:
                path.write_text(re.sub(r"^name:.*$", f"name: {name}", path.read_text(), count=1, flags=re.M))
                spec["name"] = name
            try:
                build_model({**spec, "_base": str(staging)})
            except Exception as refused:  # noqa: BLE001 -- any refusal is a refusal
                raise PackageError(f"scenario {name} cannot be built: {refused}") from None
        staging.rename(target)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return {"project": project, "scenarios": scenarios, "folder": str(target)}


def export_package(name: str, root: Path, every_scenario: bool = False) -> tuple[str, bytes]:
    """A scenario -- or its whole project -- as the package it would arrive as.
    Returns the file name and the bytes."""
    path = case_store.spec_path(name, root)
    folder = path.parent
    if folder == Path(root) or not (folder / GEOMETRY).is_file():
        raise PackageError(f"{name} is not a scenario of a drawn hall (no {GEOMETRY} beside it); a parametric "
                           f"case travels as its YAML")
    chosen = sorted(folder.glob("*.yaml")) if every_scenario else [path]
    source = folder / case_store.SOURCE
    previous = {}
    if (source / "manifest.json").is_file():
        try:
            previous = json.loads((source / "manifest.json").read_text())
        except ValueError:
            previous = {}
    figures: set[str] = set()
    for scenario in chosen:
        spec = yaml.safe_load(scenario.read_text()) or {}
        for entry in spec.get("figures") or []:
            if isinstance(entry, dict) and str(entry.get("file", "")).startswith("figures/"):
                figures.add(entry["file"])
    manifest = {
        "format": FORMAT,
        "project": folder.name,
        "scenarios": [p.stem for p in chosen],
        "geometry": {"file": GEOMETRY, "sha256": sha256(folder / GEOMETRY)},
        "source": previous.get("source") or (yaml.safe_load(path.read_text()).get("geometry") or {}).get("source"),
        "written_by": "aicfd export",
        "created": _now(),
    }
    for key in ("grid_alerts", "converter"):
        if key in previous:
            manifest[key] = previous[key]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2) + "\n")
        archive.write(folder / GEOMETRY, GEOMETRY)
        for figure in sorted(figures):
            if (folder / figure).is_file():
                archive.write(folder / figure, figure)
        for scenario in chosen:
            archive.write(scenario, f"scenarios/{scenario.name}")
        if source.is_dir():
            for extra in sorted(source.iterdir()):
                if extra.is_file() and extra.name != "manifest.json" and any(
                        p.match(f"source/{extra.name}") for p in MEMBERS):
                    archive.write(extra, f"source/{extra.name}")
    stem = folder.name if every_scenario else name
    return f"{stem}.aicfd.zip", buffer.getvalue()


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()
