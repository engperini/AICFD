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
        raise ValueError(f"no {'.'.join(path)} block to replace")
    first = next(
        (i for i in range(index + 1, len(lines)) if lines[i].lstrip().startswith("- ")),
        None,
    )
    if first is None:
        raise ValueError(f"the {'.'.join(path)} block has no items")
    end = first
    while end < len(lines) and lines[end].lstrip().startswith("- "):
        end += 1
    lines[first:end] = rows
