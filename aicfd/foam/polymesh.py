"""Read just enough of a polyMesh to answer questions about its patches.

Not a mesh library. There is exactly one question this exists to answer, and
it is one that costs a whole run to get wrong: after ``createBaffles`` splits
an internal surface in two, *which side is which*? The master patch keeps the
faces' original orientation and the slave gets them reversed, so which of the
two ends up facing the data hall follows from the mesh's own face winding, not
from anything AICFD wrote.

Guessing is cheap and wrong half the time. Reading the mesh is cheap and right.
"""

from __future__ import annotations

import re
from pathlib import Path

Vector = tuple[float, float, float]

_HEADER = re.compile(r"FoamFile\s*\{.*?\}", re.DOTALL)


def _body(path: Path) -> str:
    text = path.read_text(errors="replace")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = _HEADER.sub("", text, count=1)
    return re.sub(r"//[^\n]*", "", text)


_POINT = re.compile(r"\(\s*([-\d.eE+]+)\s+([-\d.eE+]+)\s+([-\d.eE+]+)\s*\)")
_FACE = re.compile(r"\d+\(([^)]*)\)")


def read_points(case_dir: str | Path, wanted: set[int] | None = None) -> dict[int, Vector]:
    """Point coordinates by index. ``wanted`` limits the parse to a few of them.

    A room-scale mesh has hundreds of thousands of points and this module needs
    three, so parsing them all would be the slowest thing in the pipeline for
    no reason.
    """
    text = _body(Path(case_dir) / "constant/polyMesh/points")
    points: dict[int, Vector] = {}
    for index, match in enumerate(_POINT.finditer(text)):
        if wanted is None or index in wanted:
            points[index] = tuple(float(g) for g in match.groups())  # type: ignore[misc]
            if wanted is not None and len(points) == len(wanted):
                break
    return points


def read_face(case_dir: str | Path, index: int) -> list[int]:
    """The point indices of one face, without parsing the rest."""
    text = _body(Path(case_dir) / "constant/polyMesh/faces")
    for position, match in enumerate(_FACE.finditer(text)):
        if position == index:
            return [int(i) for i in match.group(1).split()]
    raise IndexError(f"no face {index} in {case_dir}")


def read_boundary(case_dir: str | Path) -> dict[str, dict[str, str | int]]:
    """Patch name -> its entries, with nFaces and startFace as ints."""
    text = _body(Path(case_dir) / "constant/polyMesh/boundary")
    patches: dict[str, dict[str, str | int]] = {}
    for name, block in re.findall(r"(\w+)\s*\{([^{}]*)\}", text):
        entry: dict[str, str | int] = {}
        for key, value in re.findall(r"(\w+)\s+([^;]+);", block):
            entry[key] = int(value) if value.strip().isdigit() else value.strip()
        patches[name] = entry
    return patches


def patch_normal(case_dir: str | Path, patch: str) -> Vector:
    """The outward unit normal of a patch's first face.

    Outward means out of the cell the face belongs to. For a flat patch every
    face agrees, which is the only case this is used for.
    """
    case = Path(case_dir)
    boundary = read_boundary(case)
    if patch not in boundary:
        raise KeyError(f"no patch '{patch}' in {case}/constant/polyMesh/boundary")
    start = int(boundary[patch]["startFace"])

    face = read_face(case, start)
    points = read_points(case, wanted=set(face[:3]))
    a, b, c = (points[i] for i in face[:3])
    u = tuple(b[i] - a[i] for i in range(3))
    v = tuple(c[i] - a[i] for i in range(3))
    normal = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    length = sum(component**2 for component in normal) ** 0.5
    if length == 0:
        raise ValueError(f"degenerate first face on patch '{patch}'")
    return tuple(component / length for component in normal)  # type: ignore[return-value]
