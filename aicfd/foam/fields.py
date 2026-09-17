"""Readers for OpenFOAM ASCII field files.

AICFD's primary meshing path is a single ``blockMesh`` hex block over an
axis-aligned room (see docs/DECISIONS.md, ADR-008). On such a mesh the cell
ordering is fully predictable -- x varies fastest, then y, then z -- so a field
file can be read straight into a regular numpy grid without VTK or pyvista.

That keeps post-processing dependency-free and fast. Unstructured meshes
(snappyHexMesh) are out of scope here and will need a real VTK reader.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np

# "internalField   nonuniform List<scalar>\n72000\n(" / "internalField uniform 291;"
_NONUNIFORM = re.compile(
    r"internalField\s+nonuniform\s+List<(scalar|vector)>\s*\n?\s*(\d+)\s*\n?\s*\(",
    re.MULTILINE,
)
_UNIFORM = re.compile(
    r"internalField\s+uniform\s+(\([^)]*\)|[-\d.eE+]+)\s*;",
    re.MULTILINE,
)
_NUMBER = re.compile(r"[-+]?[\d.]+(?:[eE][-+]?\d+)?")

# The same list, but written as a patch's "value" rather than the internalField.
_PATCH_LIST = re.compile(
    r"value\s+nonuniform\s+List<(scalar|vector)>\s*\n?\s*(\d+)\s*\n?\s*\(",
    re.MULTILINE,
)


class FoamParseError(RuntimeError):
    """Raised when a field file does not look like the format we expect."""


def read_internal_field(path: str | Path) -> np.ndarray:
    """Read the ``internalField`` of an OpenFOAM volScalarField/volVectorField.

    Returns shape ``(n,)`` for scalars and ``(n, 3)`` for vectors. A uniform
    field is expanded only if its length can be inferred, so callers that need a
    uniform field materialised should pass ``n_cells`` to :func:`read_field`.
    """
    text = Path(path).read_text()

    match = _NONUNIFORM.search(text)
    if match:
        kind, count = match.group(1), int(match.group(2))
        body_start = match.end()
        body_end = _matching_paren(text, match.end() - 1)
        values = np.fromstring(
            text[body_start:body_end].replace("(", " ").replace(")", " "),
            sep=" ",
        )
        width = 3 if kind == "vector" else 1
        expected = count * width
        if values.size != expected:
            raise FoamParseError(
                f"{path}: header declares {count} {kind}s ({expected} numbers) "
                f"but {values.size} were parsed"
            )
        return values.reshape(count, 3) if width == 3 else values

    match = _UNIFORM.search(text)
    if match:
        numbers = [float(n) for n in _NUMBER.findall(match.group(1))]
        return np.array(numbers if len(numbers) > 1 else numbers[0])

    raise FoamParseError(f"{path}: no internalField found")


def read_field(path: str | Path, n_cells: int) -> np.ndarray:
    """Like :func:`read_internal_field`, but always returns ``n_cells`` entries.

    A uniform field is broadcast to full length so downstream code never has to
    special-case it.
    """
    values = read_internal_field(path)
    if values.ndim == 0:  # uniform scalar
        return np.full(n_cells, float(values))
    if values.ndim == 1 and values.size == 3 and n_cells != 3:  # uniform vector
        return np.tile(values, (n_cells, 1))
    if len(values) != n_cells:
        raise FoamParseError(
            f"{path}: expected {n_cells} cells, got {len(values)}"
        )
    return values


def read_patch_field(path: str | Path, patch: str) -> np.ndarray:
    """The values a field carries on one patch.

    Needed because the quantities that settle an air loop live on its
    boundaries, not in its cells: what crosses a patch is ``phi`` there, and
    the temperature of the air crossing it is ``T`` there. Reading them from
    the nearest cell centres instead is an approximation, and on a porous zone
    or a jet through a grille it is a bad one.

    Returns shape ``(n,)`` for scalars and ``(n, 3)`` for vectors. A patch
    whose value is uniform returns a single entry -- callers weight by ``phi``
    and so never need it materialised.
    """
    text = Path(path).read_text()
    start = text.find("boundaryField")
    if start < 0:
        raise FoamParseError(f"{path}: no boundaryField")
    block = _patch_block(text[start:], patch, path)

    match = _PATCH_LIST.search(block)
    if match:
        kind, count = match.group(1), int(match.group(2))
        body_end = _matching_paren(block, match.end() - 1)
        values = np.fromstring(
            block[match.end() : body_end].replace("(", " ").replace(")", " "), sep=" "
        )
        return values.reshape(count, 3) if kind == "vector" else values

    match = re.search(r"value\s+uniform\s+(\([^)]*\)|[-\d.eE+]+)\s*;", block)
    if match:
        numbers = [float(n) for n in _NUMBER.findall(match.group(1))]
        return np.array([numbers] if len(numbers) > 1 else numbers)

    # calculated/zeroGradient patches carry no value of their own
    return np.array([])


def _patch_block(text: str, patch: str, path) -> str:
    """The braces belonging to one patch entry, without the ones nested in it."""
    match = re.search(rf"^\s*{re.escape(patch)}\s*$", text, re.MULTILINE)
    if match is None:
        raise FoamParseError(f"{path}: no patch '{patch}' in boundaryField")
    open_index = text.index("{", match.end())
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[open_index : i + 1]
    raise FoamParseError(f"{path}: unterminated entry for patch '{patch}'")


def patch_names(path: str | Path) -> list[str]:
    """Every patch a field file carries a boundary condition for."""
    text = Path(path).read_text()
    start = text.find("boundaryField")
    if start < 0:
        return []
    return re.findall(r"^    (\w+)$", text[start:], re.MULTILINE)


def to_grid(values: np.ndarray, divisions: tuple[int, int, int]) -> np.ndarray:
    """Reshape a flat cell array into a ``(nz, ny, nx[, 3])`` grid.

    ``divisions`` is ``(nx, ny, nz)`` as written in blockMeshDict. The returned
    array is indexed ``[k, j, i]`` so that a constant-height slice is simply
    ``grid[k]`` -- the view an engineer asks for most often.
    """
    nx, ny, nz = divisions
    if values.shape[0] != nx * ny * nz:
        raise FoamParseError(
            f"cannot reshape {values.shape[0]} cells into {nx}x{ny}x{nz} "
            f"({nx * ny * nz} cells)"
        )
    tail = values.shape[1:]
    return values.reshape(nz, ny, nx, *tail)


def cell_centres(
    origin: tuple[float, float, float],
    size: tuple[float, float, float],
    divisions: tuple[int, int, int],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cell-centre coordinates along each axis for a uniformly graded block."""
    return tuple(  # type: ignore[return-value]
        origin[axis] + (np.arange(divisions[axis]) + 0.5) * size[axis] / divisions[axis]
        for axis in range(3)
    )


def _matching_paren(text: str, open_index: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_index``."""
    depth = 0
    for i in range(open_index, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                return i
    raise FoamParseError("unbalanced parentheses in field body")
