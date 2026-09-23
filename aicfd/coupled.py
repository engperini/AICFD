"""The supply air temperature is an output, not a boundary condition.

A fan wall does not decide what air it delivers. Its coil does, from the air
it is given: supply = return - epsilon x (return - chilled water). Imposing a
supply temperature and solving once therefore answers a question about a
machine that does not exist -- one whose delivered temperature is independent
of the room feeding it. The room and the machine have to be solved together
(ADR-040).

**How.** The room is solved in segments. After each, every unit's own return
temperature is read off the solved field, put through its coil, and written
back as that unit's supply temperature. The next segment continues from the
field the last one left. It stops when no unit's supply moves more than a
tolerance, which is convergence of the coupled problem rather than of the
flow alone.

**Why it converges, and fast.** The room fixes the temperature rise across
itself -- the load over the mass flow -- so a change in supply moves the
return one for one, and the coil passes only (1 - epsilon) of that back to the
supply. With epsilon near 0,88 the loop gain is about 0,12: each pass cuts the
error eight-fold, and three passes take it below a hundredth of a kelvin.
Nothing here relies on that estimate -- the loop measures what actually moved
and stops on it -- but it is why the cost is two or three segments and not
twenty.

**Why segments and not a boundary condition that computes itself.**
OpenFOAM's `codedFixedValue` would do this inside the solve, and that is how
it is often written up. It compiles C++ at run time, which needs a toolchain
the packaged OpenFOAM installs do not ship -- so a clone that runs everywhere
cannot rely on it. Segments need nothing but the solver, and the answer is the
same one: the coupled fixed point, reached from outside instead of inside.

**Each unit gets its own temperature.** Not the plant's average. A unit at the
end of a row that returns warmer air delivers warmer air, and a model that
gave every unit the mean would smear away exactly the imbalance a hall is
simulated to find.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from aicfd.model import CP_AIR, KELVIN

#: How much a supply temperature must stop moving, in K, before the coupled
#: problem is called converged. A tenth of the 1-2 K a conceptual mesh can
#: resolve per rack: tighter buys nothing a reader could act on.
TOLERANCE_K = 0.02

#: How many solver segments the loop may spend. Reached only when something is
#: genuinely not settling -- three is normally plenty -- and it is a cap rather
#: than a target so a pathological case ends with a result and a warning rather
#: than running forever.
MAX_PASSES = 5


@dataclass
class Pass:
    """One segment of the coupled solve, and what it moved."""

    number: int
    iterations: int
    supplies_c: dict[str, float]
    returns_c: dict[str, float]
    moved_k: float | None
    """The largest change in any unit's supply temperature this pass, or None
    on the first, which has nothing to be compared against. None rather than a
    sentinel number: `moved 99.000 K` read as a measurement, and a reader who
    took it for one would think the loop had diverged when it had not
    started."""
    converged: bool
    saturated: list[str]
    """Units with nothing left to give: their supply is no longer a setpoint.
    A chilled-water unit is there with its valve wide open, a
    direct-expansion one with its compressors at full duty."""
    duty_label: str = "water valve"
    """What THIS plant modulates, from its own coil (ADR-112). A line saying
    `at full water` ran for an hour on a hall of fourteen direct-expansion
    CRACs, which have no water in them."""
    closure: float | None = None
    """How much of the installed load the return air carried at the end of
    this segment, as a fraction. The supply can stop moving while the room is
    still warming towards it, and a loop that stopped there left a field
    carrying 86 % of its load (ADR-127)."""
    settling: bool = False
    """A pass run because the supply had stopped moving but the field had
    not yet filled: the machines' answer stands and the room catches up."""

    def summary(self) -> str:
        warm = max(self.supplies_c.values()) if self.supplies_c else 0.0
        return (
            f"pass {self.number}: {len(self.supplies_c)} units, warmest supply "
            f"{warm:.2f} degC, "
            + ("the first, nothing to compare against yet"
               if self.moved_k is None else f"moved {self.moved_k:.3f} K")
            + (f", return air carrying {self.closure * 100:.0f}% of the load"
               if self.closure is not None else "")
            + (" (converged)" if self.converged else
               " (supply settled, the room is still filling)" if self.settling
               else "")
            + (f", {len(self.saturated)} at full {self.duty_label}"
               if self.saturated else "")
        )


def energy_closure(model, step: str | Path) -> float | None:
    """The fraction of the installed load the return air carries, read off
    the field the same way the `energy_closure` check reads it."""
    from aicfd.post import recovered_load_w

    if not getattr(model, "total_load_w", None):
        return None
    return recovered_load_w(step, model) / model.total_load_w


def loop_closed(supply_settled: bool, closure: float | None,
                tolerance: float) -> bool:
    """The coupled loop is closed when the machines have stopped moving AND
    the room has filled behind them.

    The first alone stopped a 1 MW hall 300 iterations after a 1 K step in
    supply, with the return air carrying 86 % of the load: the units had
    agreed with each other and the room had not caught up. Both are read off
    the same field, so both are asked (ADR-127).
    """
    if not supply_settled:
        return False
    return closure is None or abs(closure - 1.0) <= tolerance


def supply_temperatures(model, step: str | Path) -> tuple[dict, dict, list]:
    """Each unit's supply air temperature, from its own return through its coil.

    Returns (supply by unit, return by unit, units at full water). The unit
    names are the patch prefixes -- `fan1`, `fan2` -- so the result can be
    written straight back onto the boundary field.
    """
    from aicfd.post import fan_flows

    unit = getattr(model, "equipment", None)
    coil = unit.coil if unit is not None else None
    if coil is None:
        return {}, {}, []
    supplies, returns, saturated = {}, {}, []
    seen = {}
    for fan in fan_flows(step, supply_temp_c=model.supply_temp_c):
        temperature = fan.get("return_temp_c")
        mass = fan.get("intake_kg_s")
        if temperature is None or not mass:
            continue
        seen[fan["name"]] = (temperature, mass * CP_AIR / 1000)
    for name, (temperature, air) in seen.items():
        # On a network the unit runs to the worst return its own gallery
        # sees, not to its own. Independent units are a team of one, so the
        # same line covers both (ADR-064).
        worst = max(seen[peer][0] for peer in model.team_of(name) if peer in seen)
        point = coil.operate_shared(worst, temperature, air, model.supply_temp_c)
        supplies[name] = point.supply_c
        returns[name] = temperature
        if point.saturated:
            saturated.append(name)
    return supplies, returns, saturated


# --- writing it back onto the field -------------------------------------------


def apply_supplies(case_dir: str | Path, time: str, supplies: dict) -> int:
    """Write each unit's supply temperature into the field the solver reads.

    Every copy of it: the reconstructed time directory and each processor's,
    because a parallel run continues from the decomposed fields and would
    otherwise carry on with the old boundary while the reconstructed one said
    something else -- a disagreement nothing downstream would notice.
    """
    case = Path(case_dir)
    targets = [case / time / "T", *sorted(case.glob(f"processor*/{time}/T"))]
    written = 0
    for path in targets:
        if not path.is_file():
            continue
        text = path.read_text()
        before = text
        for name, celsius in supplies.items():
            kelvin = celsius + KELVIN
            text = _set_patch_entry(text, f"{name}Supply", "value", f"uniform {kelvin:.4f}")
            # The intake's inletValue is the same physical quantity: what would
            # come back if flow ever reversed through it. Left behind, it would
            # quietly inject the old supply temperature on the one iteration it
            # mattered.
            text = _set_patch_entry(text, f"{name}Intake", "inletValue",
                                    f"uniform {kelvin:.4f}")
        if text != before:
            path.write_text(text)
            written += 1
    return written


def _set_patch_entry(text: str, patch: str, key: str, value: str) -> str:
    """Replace one keyword inside one patch block, leaving the rest alone.

    Written by hand rather than with a regex over the whole entry because a
    boundary value may be a parenthesised list spanning thousands of lines,
    and the terminating semicolon is the one after the list closes, not the
    first one encountered. A botched boundary file costs a whole solve.
    """
    match = re.search(rf"^\s*{re.escape(patch)}\s*$", text, re.MULTILINE)
    if match is None:
        return text
    brace = text.find("{", match.end())
    if brace < 0:
        return text
    end = _closing_brace(text, brace)
    block = text[brace:end]
    entry = re.search(rf"(^[ \t]*{re.escape(key)}\s+)", block, re.MULTILINE)
    if entry is None:
        return text
    stop = _entry_end(block, entry.end())
    if stop < 0:
        return text
    return text[:brace] + block[: entry.end()] + value + ";" + block[stop:] + text[end:]


def _closing_brace(text: str, opening: int) -> int:
    depth = 0
    for i in range(opening, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


def _entry_end(block: str, start: int) -> int:
    """Index just past the semicolon that closes this keyword's value."""
    depth = 0
    for i in range(start, len(block)):
        char = block[i]
        if char in "({":
            depth += 1
        elif char in ")}":
            depth -= 1
            if depth < 0:
                return -1
        elif char == ";" and depth == 0:
            return i + 1
    return -1


# --- the loop -----------------------------------------------------------------


def set_end_time(case_dir: str | Path, end: int, latest: bool) -> None:
    """Point controlDict at the next segment, continuing from what is there."""
    path = Path(case_dir) / "system" / "controlDict"
    text = path.read_text()
    text = re.sub(r"^startFrom\s+\S+;",
                  f"startFrom       {'latestTime' if latest else 'startTime'};",
                  text, count=1, flags=re.MULTILINE)
    text = re.sub(r"^endTime\s+\S+;", f"endTime         {end};", text,
                  count=1, flags=re.MULTILINE)
    path.write_text(text)


def latest_time(case_dir: str | Path) -> str | None:
    """The newest written time directory, reconstructed or decomposed."""
    case = Path(case_dir)
    roots = [case, *sorted(case.glob("processor*"))[:1]]
    times = []
    for root in roots:
        for child in root.iterdir() if root.is_dir() else []:
            if child.is_dir() and _is_time(child.name):
                times.append(child.name)
    return max(times, key=float) if times else None


def _is_time(name: str) -> bool:
    try:
        float(name)
    except ValueError:
        return False
    return name != "0"
