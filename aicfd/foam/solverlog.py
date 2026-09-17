"""Parse a ``buoyantSimpleFoam`` log into a residual history.

The convergence chart in the viewer, and the automated verdict in
:mod:`aicfd.post`, both read from here. A solver that exits 0 has only finished
its iteration loop -- whether the residuals actually fell is a separate
question, and this module is what answers it (ADR-007).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_TIME = re.compile(r"^Time = ([\d.eE+-]+)", re.MULTILINE)
_RESIDUAL = re.compile(
    r"Solving for (\w+), Initial residual = ([\d.eE+-]+), "
    r"Final residual = ([\d.eE+-]+)"
)
_CONTINUITY = re.compile(
    r"time step continuity errors : sum local = ([\d.eE+-]+), "
    r"global = ([\d.eE+-]+), cumulative = ([\d.eE+-]+)"
)
_EXECUTION = re.compile(r"ExecutionTime = ([\d.]+) s")
_CONVERGED = re.compile(r"SIMPLE solution converged in (\d+) iterations")


@dataclass
class SolverLog:
    """Residual history for one solver run."""

    iterations: list[int] = field(default_factory=list)
    residuals: dict[str, list[float]] = field(default_factory=dict)
    continuity: list[float] = field(default_factory=list)
    execution_time_s: float | None = None
    converged_at: int | None = None
    """Iteration at which OpenFOAM's own residualControl was satisfied, if ever."""

    @property
    def completed_iterations(self) -> int:
        return len(self.iterations)

    def final_residuals(self) -> dict[str, float]:
        """Last *initial* residual per field -- the standard convergence measure."""
        return {name: values[-1] for name, values in self.residuals.items() if values}

    @property
    def stopped_on_iteration_limit(self) -> bool:
        """True when the run ran out of iterations instead of meeting a tolerance.

        This is the difference between "converged" and "gave up", and the
        reference case is a live example: it stops at endTime with residuals
        still near 1e-4 because no residualControl was set.
        """
        return self.converged_at is None and self.completed_iterations > 0


def parse(log_path: str | Path) -> SolverLog:
    text = Path(log_path).read_text(errors="replace")
    result = SolverLog()

    # Split on time steps so each residual is attributed to its own iteration.
    marks = list(_TIME.finditer(text))
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(text)
        block = text[mark.end() : end]
        result.iterations.append(int(float(mark.group(1))))

        seen: set[str] = set()
        for name, initial, _final in _RESIDUAL.findall(block):
            if name in seen:
                continue  # only the first solve of a field in an outer iteration
            seen.add(name)
            result.residuals.setdefault(name, []).append(float(initial))

        continuity = _CONTINUITY.search(block)
        if continuity:
            result.continuity.append(abs(float(continuity.group(2))))

    # A field that appears late would otherwise be misaligned with `iterations`.
    for name, values in result.residuals.items():
        missing = len(result.iterations) - len(values)
        if missing > 0:
            result.residuals[name] = [float("nan")] * missing + values

    times = _EXECUTION.findall(text)
    if times:
        result.execution_time_s = float(times[-1])

    converged = _CONVERGED.search(text)
    if converged:
        result.converged_at = int(converged.group(1))

    return result
