"""Execute OpenFOAM utilities in an isolated environment.

This module is the *only* place in AICFD that shells out to OpenFOAM. See
docs/DECISIONS.md, ADR-002, for why every command runs under ``env -i``: the
Ubuntu package's ``etc/bashrc`` is incomplete, and a partially-sourced one
poisons ``WM_PROJECT_*`` so that every solver dies with a misleading
"Could not find mandatory etc entry 'controlDict'".
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

#: The minimal environment an OpenFOAM binary needs. Anything inherited from the
#: caller's shell is deliberately dropped.
FOAM_ENV = {
    "HOME": os.environ.get("HOME", "/root"),
    "WM_PROJECT_DIR": "/usr/share/openfoam",
    "WM_PROJECT": "OpenFOAM",
    "WM_PROJECT_VERSION": "v1912",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}

#: The standard pipeline for a blockMesh case, in order.
DEFAULT_PIPELINE = ("blockMesh", "topoSet", "checkMesh", "buoyantSimpleFoam")


class FoamNotInstalled(RuntimeError):
    pass


class FoamCommandFailed(RuntimeError):
    def __init__(self, command: str, returncode: int, log: Path):
        super().__init__(
            f"{command} exited {returncode}. Full output: {log}"
        )
        self.command = command
        self.returncode = returncode
        self.log = log


@dataclass
class StepResult:
    command: str
    returncode: int
    log: Path
    seconds: float


def check_install() -> dict[str, str | None]:
    """Locate the OpenFOAM utilities AICFD depends on."""
    return {name: shutil.which(name, path=FOAM_ENV["PATH"]) for name in DEFAULT_PIPELINE}


def run_command(
    case_dir: str | Path,
    command: str,
    *,
    args: list[str] | None = None,
    check: bool = True,
) -> StepResult:
    """Run one OpenFOAM utility in ``case_dir``, teeing its output to ``log.<cmd>``."""
    import time

    case = Path(case_dir).resolve()
    if shutil.which(command, path=FOAM_ENV["PATH"]) is None:
        raise FoamNotInstalled(
            f"'{command}' not found. Install with: "
            "apt-get install -y openfoam openfoam-examples"
        )

    log_path = case / f"log.{command}"
    started = time.monotonic()
    with log_path.open("w") as log:
        process = subprocess.run(
            [command, *(args or [])],
            cwd=case,
            env=FOAM_ENV,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    result = StepResult(
        command=command,
        returncode=process.returncode,
        log=log_path,
        seconds=time.monotonic() - started,
    )
    if check and process.returncode != 0:
        raise FoamCommandFailed(command, process.returncode, log_path)
    return result


def prepare(source: str | Path, destination: str | Path) -> Path:
    """Copy a case template into a fresh run directory.

    Solving in a copy keeps the template clean: an OpenFOAM run writes time
    directories, logs and a polyMesh into the case, and a template that has been
    run once is no longer a template.
    """
    source = Path(source)
    destination = Path(destination)
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("log.*", "VTK", "postProcessing", "polyMesh"),
    )
    # A template that was solved in place would otherwise drag its old results in.
    for entry in destination.iterdir():
        if entry.is_dir() and entry.name not in {"0", "system", "constant"}:
            shutil.rmtree(entry, ignore_errors=True)
    return destination


def solve(
    case_dir: str | Path,
    pipeline: tuple = DEFAULT_PIPELINE,
    on_step=None,
) -> list[StepResult]:
    """Run the full meshing and solving pipeline for a case.

    A pipeline entry is a command name, or a ``(name, [args])`` pair where the
    utility needs them -- ``createBaffles`` has to be told ``-overwrite``, or it
    writes the modified mesh into a new time directory and everything after it
    reads the mesh it was supposed to replace.
    """
    results = []
    for entry in pipeline:
        command, args = entry if isinstance(entry, tuple) else (entry, None)
        if on_step:
            on_step(command)
        results.append(run_command(case_dir, command, args=args))
    return results
