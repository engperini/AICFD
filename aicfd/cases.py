"""Where a case lives (ADR-134).

A case is still named by one word -- `dh04-1mw-19c` -- and that word still
names its run, its export and its report. What changed is where the file is.
A hall read from a drawing is a PROJECT: one geometry, its figures, the answers
the converter was given, and as many scenarios as the engineer wants to try on
it. So it is a folder::

    cases/
      dh04-1mw/                 the project
        geometry.stl            the room, read from the drawing; never edited here
        figures/                what the report opens with
        source/                 the package's manifest and the converter's answers
        dh04-1mw.yaml           a scenario: everything a drawing cannot say
        dh04-1mw-19c.yaml       another, on the same geometry
      my-pod.yaml               a parametric case, a file on its own

A scenario is found by its name wherever it sits, one level down at most, and a
name has to be unique across the folder: two scenarios called the same would
share one run directory and overwrite each other's result.
"""

from __future__ import annotations

from pathlib import Path

import yaml

#: The folder a project keeps what is not a scenario in. Not searched for cases.
SOURCE = "source"


def every(root: Path) -> list[Path]:
    """Every case file under ``root``: loose ones, and one per scenario."""
    root = Path(root)
    if not root.is_dir():
        return []
    found = sorted(root.glob("*.yaml"))
    for project in sorted(p for p in root.iterdir() if p.is_dir() and p.name != SOURCE):
        found += sorted(project.glob("*.yaml"))
    return found


def spec_path(name: str, root: Path) -> Path:
    """The file case ``name`` is written in. FileNotFoundError when there is none,
    ValueError when two projects both have one by that name."""
    root = Path(root)
    loose = root / f"{name}.yaml"
    if loose.is_file():
        return loose
    nested = [p for p in every(root) if p.stem == name and p.parent != root]
    if len(nested) > 1:
        raise ValueError(
            f"two cases are called {name!r}: "
            + ", ".join(str(p.relative_to(root)) for p in nested)
            + ". A name is a run directory and a result; rename one of them.")
    if nested:
        return nested[0]
    raise FileNotFoundError(f"no case spec named {name!r} under {root}")


def exists(name: str, root: Path) -> bool:
    try:
        spec_path(name, root)
    except FileNotFoundError:
        return False
    return True


def load(name: str, root: Path) -> dict:
    """The case as a spec, with ``_base`` set to its folder so a geometry case
    finds the STL beside it. Private keys start with `_` and are never written
    back (`public`)."""
    path = spec_path(name, root)
    spec = yaml.safe_load(path.read_text()) or {}
    spec["_base"] = str(path.resolve().parent)
    return spec


def public(spec: dict) -> dict:
    """The spec without the keys this process added to it."""
    return {k: v for k, v in spec.items() if not str(k).startswith("_")}
