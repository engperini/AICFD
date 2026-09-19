"""Execute OpenFOAM utilities in an isolated environment.

This module is the *only* place in AICFD that shells out to OpenFOAM. See
docs/DECISIONS.md, ADR-002, for why every command runs under ``env -i``: the
Ubuntu package's ``etc/bashrc`` is incomplete, and a partially-sourced one
poisons ``WM_PROJECT_*`` so that every solver dies with a misleading
"Could not find mandatory etc entry 'controlDict'".
"""

from __future__ import annotations

import os
import re
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
    # A container runs as root, and Open MPI refuses that unless told twice.
    "OMPI_ALLOW_RUN_AS_ROOT": "1",
    "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1",
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
    log_name: str | None = None,
) -> StepResult:
    """Run one OpenFOAM utility in ``case_dir``, teeing its output to ``log.<cmd>``.

    ``log_name`` names the log after the program that matters when ``command``
    is only a launcher: ``mpirun ... buoyantSimpleFoam`` logs to
    ``log.buoyantSimpleFoam``, where everything that reads a solver log looks.
    """
    import time

    case = Path(case_dir).resolve()
    if shutil.which(command, path=FOAM_ENV["PATH"]) is None:
        raise FoamNotInstalled(
            f"'{command}' not found. Install with: "
            "apt-get install -y openfoam openfoam-examples"
        )

    log_path = case / f"log.{log_name or command}"
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

    A pipeline entry is a command name, a ``(name, [args])`` pair where the
    utility needs them -- ``createBaffles`` has to be told ``-overwrite``, or it
    writes the modified mesh into a new time directory and everything after it
    reads the mesh it was supposed to replace -- or ``(name, [args], log)`` to
    name the log after something other than the command.
    """
    results = []
    for entry in pipeline:
        command, args, log_name = _entry(entry)
        if on_step:
            on_step(log_name or command)
        results.append(run_command(case_dir, command, args=args, log_name=log_name))
    return results


def _entry(entry) -> tuple[str, list[str] | None, str | None]:
    if isinstance(entry, tuple):
        command, args, *rest = entry
        return command, args, (rest[0] if rest else None)
    return entry, None, None


def commands(pipeline: tuple) -> list[str]:
    """The programs a pipeline needs on PATH."""
    return [_entry(entry)[0] for entry in pipeline]


# --- stopping a run ------------------------------------------------------------

#: What `controlDict` says while a run is free to reach its iteration cap, and
#: what it is changed to in order to stop one. `writeNow` rather than
#: `noWriteNow`: a run stopped without a write leaves nothing to post-process,
#: and the whole point of stopping cleanly is to keep what has been computed.
RUNNING_STOP = "stopAt          endTime;"
STOP_NOW = "stopAt          writeNow;"


def request_stop(case_dir: str | Path) -> bool:
    """Ask a running solver to stop at its next iteration, keeping the field.

    This is OpenFOAM's own mechanism, not a signal: the solver re-reads
    ``system/controlDict`` every iteration (``runTimeModifiable true``, which
    the generator always writes), sees ``stopAt writeNow``, finishes the
    iteration it is on, writes the fields and exits 0. The pipeline then goes
    on to reconstruct and export exactly as it would have at the cap, so what
    is left behind is a *result* -- partial, and held to the same eleven
    checks, which is how a run stopped before it settled reports itself as
    such.

    Killing the process would leave a half-written time directory and no
    export. Every commercial tool's Stop button does what this does.

    The file is replaced atomically, because the solver may be reading it at
    that moment: ``os.replace`` swaps the directory entry, so a reader sees
    either the whole old file or the whole new one, never a partial write.

    Returns False when the case has no controlDict to change.
    """
    control = Path(case_dir) / "system" / "controlDict"
    if not control.is_file():
        return False
    text = control.read_text()
    if STOP_NOW in text:
        return True  # already asked; saying so is not an error
    if RUNNING_STOP not in text:
        return False
    temporary = control.with_suffix(".controlDict.stopping")
    temporary.write_text(text.replace(RUNNING_STOP, STOP_NOW))
    os.replace(temporary, control)
    return True


def stop_requested(case_dir: str | Path) -> bool:
    """Whether a stop has already been asked for on this case."""
    control = Path(case_dir) / "system" / "controlDict"
    return control.is_file() and STOP_NOW in control.read_text()


# --- solving the room and the machine together ---------------------------------


def solve_coupled(
    case_dir: str | Path,
    pipeline: tuple,
    model,
    segment: int = 300,
    max_passes: int = 5,
    tolerance: float = 0.02,
    on_step=None,
    on_pass=None,
) -> tuple[list[StepResult], list]:
    """Solve, then let each fan wall's coil set its own supply temperature.

    The first segment is the run as it would have been: the iteration cap the
    spec asks for, at the supply temperature it states. Every pass after it
    reads each unit's own return off the solved field, puts it through the
    unit's coil, writes the answer back as that unit's supply temperature and
    continues from the field already there. It stops when no unit's supply
    moves more than `tolerance`.

    So coupling is an addition to the run, never a reduction of it: the flow
    is solved exactly as far as before, and the passes are what it costs to
    make the supply temperature a result instead of an assumption (ADR-040).

    Falls back to a plain `solve` -- with no passes and no warning -- when the
    case names no unit or its selections support no coil. There is nothing to
    couple to, and a run that quietly did nothing different is the honest
    outcome.
    """
    from aicfd import coupled as loop

    unit = getattr(model, "equipment", None)
    if unit is None or unit.coil is None:
        return solve(case_dir, pipeline, on_step=on_step), []

    case = Path(case_dir)
    before, solver, after = _split_at_solver(pipeline)
    results = []
    for entry in before:
        command, args, log_name = _entry(entry)
        if on_step:
            on_step(log_name or command)
        results.append(run_command(case, command, args=args, log_name=log_name))

    end = _end_time(case)
    passes, previous = [], None
    for number in range(1, max_passes + 1):
        command, args, log_name = _entry(solver)
        if on_step:
            on_step(log_name or command)
        results.append(run_command(case, command, args=args, log_name=log_name))
        # Reconstructed before it is read: a decomposed run keeps its fields
        # per processor, and every reader downstream works on whole patches.
        #
        # Under the sampler's lock from the reconstruct through the read and
        # the write-back. The sampler rebuilds every write as it lands and
        # drops the older ones, so without it two reconstructions of the same
        # time race -- and a reader between them sees half a field, which is
        # how this died on `300/phi: no boundaryField`.
        from aicfd.post import RECONSTRUCT_LOCK

        with RECONSTRUCT_LOCK:
            if any(case.glob("processor*")):
                results.append(
                    run_command(case, "reconstructPar", args=["-latestTime"])
                )
            time = loop.latest_time(case)
            if time is None:
                break
            supplies, returns, saturated = loop.supply_temperatures(
                model, case / time
            )
            if not supplies:
                break
        moved = (max(abs(supplies[k] - previous[k]) for k in supplies if k in previous)
                 if previous else float("inf"))
        settled = moved <= tolerance
        record = loop.Pass(number=number, iterations=end, supplies_c=supplies,
                           returns_c=returns, moved_k=min(moved, 99.0),
                           converged=settled, saturated=saturated)
        passes.append(record)
        if on_pass:
            on_pass(record)
        previous = supplies
        if settled or number == max_passes:
            break
        with RECONSTRUCT_LOCK:
            loop.apply_supplies(case, time, supplies)
        end += segment
        loop.set_end_time(case, end, latest=True)

    for entry in after:
        command, args, log_name = _entry(entry)
        if on_step:
            on_step(log_name or command)
        results.append(run_command(case, command, args=args, log_name=log_name))
    return results, passes


def _split_at_solver(pipeline: tuple) -> tuple[list, object, list]:
    """(before, the solver entry, after). The solver is the one whose log is
    named after a Foam solver -- which is how it is named whether it runs
    bare or under mpirun."""
    for i, entry in enumerate(pipeline):
        command, _, log_name = _entry(entry)
        if (log_name or command).endswith("Foam"):
            return list(pipeline[:i]), entry, list(pipeline[i + 1:])
    raise ValueError("this pipeline has no solver to couple to")


def _end_time(case_dir: str | Path) -> int:
    text = (Path(case_dir) / "system" / "controlDict").read_text()
    found = re.search(r"^endTime\s+(\S+);", text, re.MULTILINE)
    return int(float(found.group(1))) if found else 0
