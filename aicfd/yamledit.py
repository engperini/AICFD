"""Editing a YAML file in place, keeping everything a person put in it.

The library files under `equipment/` and `components/` are hand-written and
hand-aligned: the comment beside a number says where the number came from, and
the blank line above a block is what makes it readable. A page that saves a
change by re-dumping the parsed document throws all of that away -- the file
comes back correct and unrecognisable, and the next person to open it has lost
the reasons.

So a save rewrites the one line it has to and leaves every other byte alone,
trailing comment and column included. Editing a field and putting back the
same value leaves the file identical, which is the property the tests check.

Shared by both libraries because a second copy of this is how the two would
drift, and drift in a save path is silent.
"""

from __future__ import annotations

import yaml

#: Two spaces, as every file in the libraries is written.
INDENT = "  "


def merge(base: dict, changes: dict) -> dict:
    """`changes` over `base`, one level deep into each mapping."""
    out = dict(base)
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = {**out[key], **value}
        else:
            out[key] = value
    return out


def dig(data: dict, path: list[str]):
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def number(value: float) -> str:
    """Round-trip a float without a trailing `.0` on whole numbers."""
    return f"{float(value):g}"


def render(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return number(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(render(v) for v in value) + "]"
    text = str(value)
    return text if text and not any(c in text for c in ":#") else yaml.safe_dump(
        text, default_flow_style=True
    ).strip().rstrip("\n...").strip()


def find(lines: list[str], path: list[str]) -> int | None:
    """The line holding ``path``, or None when the file does not have it."""
    start = 0
    found = None
    for level, key in enumerate(path):
        prefix = f"{INDENT * level}{key}:"
        found = None
        for i in range(start, len(lines)):
            line = lines[i]
            if line.startswith(prefix) and (
                len(line) == len(prefix) or line[len(prefix)] in " \t"
            ):
                found = i
                break
            stripped = line.strip()
            if level and stripped and not stripped.startswith("#") and not line.startswith(
                INDENT * level
            ):
                return None  # left the parent block without finding it
        if found is None:
            return None
        start = found + 1
    return found


def set_scalar(lines: list[str], path: list[str], value) -> bool:
    """Replace one scalar, keeping its indentation and trailing comment.

    The comment keeps its column too, so editing one field and putting it
    back leaves the file byte for byte as it was. The alignment in these
    files is hand-made and worth that much care; when the new value is too
    wide for the old column, two spaces is the fallback.
    """
    index = find(lines, path)
    if index is None:
        return False
    line = lines[index]
    head, _, rest = line.partition(":")
    if rest.strip() in (">", "|", ">-", "|-", ">+", "|+"):
        return _set_block(lines, index, head, value)
    written = f"{head}: {render(value)}"
    if "#" not in rest:
        lines[index] = written.rstrip()
        return True
    column = len(line) - len(rest.partition("#")[2]) - 1
    lines[index] = written.ljust(max(column, len(written) + 2)) + "#" + rest.partition("#")[2]
    return True


def set_or_add(lines: list[str], path: list[str], value) -> None:
    """Write a scalar, adding the key -- and its block -- when the file lacks it.

    `set_scalar` can only change a key that is already there, and reports
    False for one that is not. On a save path an ignored False is a value the
    user typed, was told was saved, and then watched disappear on the next
    read. A half-written unit is precisely the file whose keys are missing, so
    the page that exists to finish it has to be able to add them (ADR-066).

    The new key goes at the end of its block, after the keys already there and
    the comments that belong to them; a block that does not exist at all is
    created by `set_map`.
    """
    if set_scalar(lines, path, value):
        return
    parent, key = path[:-1], path[-1]
    at = _insert_at(lines, parent)
    if at is None:
        set_map(lines, parent, {key: value})
        return
    lines.insert(at, f"{INDENT * len(parent)}{key}: {render(value)}")


def _set_block(lines: list[str], index: int, head: str, value) -> bool:
    """Rewrite a folded or literal block scalar and the lines it owns.

    A `note: >` carries its text on the indented lines beneath it. Replacing
    only the key line leaves those orphaned, which is not a broken value but a
    broken FILE: the next key is swallowed into the block and the document
    stops parsing. That happened, and it happened on the save path, where the
    damage is written to disk before anything reads it back.

    The paragraph is re-wrapped at the width the files are written to, and the
    block marker is kept, so a note that was folded stays folded.
    """
    import textwrap

    indent = len(head) - len(head.lstrip())
    body = " ".join(str(value).split())
    end = index + 1
    while end < len(lines) and (not lines[end].strip()
                                or len(lines[end]) - len(lines[end].lstrip()) > indent):
        end += 1
    # Unchanged text keeps its own line breaks. Re-wrapping is not invertible,
    # so rewriting on every save would reflow a paragraph nobody edited and
    # leave a diff on a file that did not change.
    if " ".join(" ".join(lines[index + 1:end]).split()) == body:
        return True
    wrapped = textwrap.wrap(body, width=76 - indent) or [""]
    lines[index:end] = [lines[index]] + [" " * (indent + 2) + w for w in wrapped]
    return True


def replace_list(lines: list[str], path: list[str], rows: list[str]) -> None:
    """Swap the list under ``path`` for ``rows``, leaving everything else.

    Everything else includes the comment lines between the key and its first
    item, which is where the column meanings are written.
    """
    index = find(lines, path)
    if index is None:
        # A file that never had the block. Empty rows means there is still
        # nothing to say, so say nothing; otherwise the block is written, for
        # the same reason `set_or_add` writes a missing key (ADR-066).
        if not rows:
            return
        at = _insert_at(lines, path[:-1])
        if at is None:
            raise ValueError(f"no {'.'.join(path[:-1])} block to write into")
        lines[at:at] = [f"{INDENT * (len(path) - 1)}{path[-1]}:", *rows]
        return
    first = next(
        (i for i in range(index + 1, len(lines)) if lines[i].lstrip().startswith("- ")),
        None,
    )
    if first is None:
        # The key is there with nothing under it. Its items go right after it,
        # below whatever comments the key carries.
        first = end = index + 1
        while end < len(lines) and lines[end].lstrip().startswith("#"):
            first = end = end + 1
    else:
        end = first
        while end < len(lines) and lines[end].lstrip().startswith("- "):
            end += 1
    lines[first:end] = rows


def set_map(lines: list[str], path: list[str], values: dict) -> None:
    """Write a nested mapping under ``path``, or remove it when empty.

    `set_scalar` can only change a key the file already has. A case states
    per-position rack loads in a block that is absent from most files, appears
    the first time a position disagrees with the standard, and has to go away
    again when the last one stops disagreeing -- so the block is created,
    rewritten and deleted here, and every other line of the case is left as
    the person who wrote it left it (ADR-054).

    The new block goes at the end of its parent, after the parent's own keys
    and the comments that belong to them.
    """
    parent, key = path[:-1], path[-1]
    level = len(parent)
    index = find(lines, path)
    if index is not None:
        end = index + 1
        while end < len(lines) and (
            not lines[end].strip()
            or len(lines[end]) - len(lines[end].lstrip()) > level * len(INDENT)
        ):
            end += 1
        # A trailing blank line belongs to whatever comes next, not to this
        # block, so removing the block must not take the separator with it.
        while end - 1 > index and not lines[end - 1].strip():
            end -= 1
    else:
        if not values:
            return
        start = find(lines, parent) if parent else None
        if parent and start is None:
            raise ValueError(f"no {'.'.join(parent)} block to write into")
        # `start or -1` would read the first line of the file as "not found",
        # and the block would be written above the document it belongs to.
        anchor = -1 if start is None else start
        end = anchor + 1
        while end < len(lines) and (
            not lines[end].strip()
            or len(lines[end]) - len(lines[end].lstrip()) >= level * len(INDENT)
        ):
            end += 1
        while end - 1 > anchor and not lines[end - 1].strip():
            end -= 1
        index = end
    block = []
    if values:
        block = [f"{INDENT * level}{key}:"] + [
            f"{INDENT * (level + 1)}{name}: {render(value)}"
            for name, value in values.items()
        ]
    lines[index:end] = block


def _leaves(data, prefix=()):
    """Every scalar (or list) in a nested mapping, by its path."""
    for key, value in data.items():
        path = (*prefix, str(key))
        if isinstance(value, dict):
            yield from _leaves(value, path)
        else:
            yield path, value


def _insert_at(lines: list[str], parent: list[str]) -> int | None:
    """Where a new key under ``parent`` goes: the end of that block."""
    level = len(parent)
    start = find(lines, parent) if parent else None
    if parent and start is None:
        return None
    anchor = -1 if start is None else start
    end = anchor + 1
    while end < len(lines) and (
        not lines[end].strip()
        or len(lines[end]) - len(lines[end].lstrip()) >= level * len(INDENT)
    ):
        end += 1
    while end - 1 > anchor and not lines[end - 1].strip():
        end -= 1
    return end


def rewrite(text: str, before: dict, after: dict) -> str | None:
    """``text`` with the values that changed between the two specs, or None.

    The point is everything it does NOT touch. A case file is hand-written --
    the comment beside `airflow_cfm_per_kw` is what tells the next person that
    the number sizes the rack's resistance -- and re-dumping the parsed
    document saves the right values and loses every reason behind them. This
    edits the lines that changed and leaves the rest of the bytes alone.

    None when the edit cannot be made faithfully: a key removed, a block whose
    parent is not there, or a result that does not parse back to what was
    asked for. The caller then falls back to the dump, because a file that is
    correct and bare beats a file that is pretty and wrong (ADR-061).
    """
    old = dict(_leaves(before))
    new = dict(_leaves(after))
    if set(old) - set(new):
        return None  # a key went away; not something this can express
    lines = text.split("\n")
    for path, value in new.items():
        if path in old and old[path] == value:
            continue
        if isinstance(value, dict):
            return None
        if set_scalar(lines, list(path), value):
            continue
        parent = list(path[:-1])
        # Walk up to the deepest block that exists, and build what is missing
        # from there down.
        while parent and find(lines, parent) is None:
            parent = parent[:-1]
        at = _insert_at(lines, parent)
        if at is None:
            return None
        missing = list(path[len(parent):])
        block = [
            f"{INDENT * (len(parent) + i)}{name}:"
            + (f" {render(value)}" if i == len(missing) - 1 else "")
            for i, name in enumerate(missing)
        ]
        # A new top-level block reads as its own paragraph.
        lines[at:at] = ([""] + block if not parent else block)
    out = "\n".join(lines)
    try:
        return out if yaml.safe_load(out) == after else None
    except yaml.YAMLError:
        return None
