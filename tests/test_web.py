"""The static pages hold together: every import resolves, every page loads.

There is no build step here (ADR-005), so nothing checks the web folder until
a browser does. A module that imports a name another file no longer exports
fails at parse time and takes the whole page with it -- not the card that
needed it, the page -- and the only symptom is a page that never finishes
loading.

That happened: `web/racks.js` was the results page's rack-inlet card, and the
page that configures the racks was written over it. `results.js` could not
import `RackInlets` any anymore and the results page stopped loading entirely.
Nothing in the suite noticed, because nothing in the suite read the imports
(ADR-057).
"""

from __future__ import annotations

import re
import unittest

from tests import support

WEB = support.REPO / "web"

#: `import { A, B as C } from './x.js'` and `import X from './x.js'`.
IMPORT = re.compile(
    r"^import\s+(?P<what>[^'\"]+?)\s+from\s+['\"](?P<from>\./[^'\"]+)['\"]",
    re.MULTILINE,
)
EXPORT = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:class|function|const|let|var)\s+(?P<name>\w+)",
    re.MULTILINE,
)
EXPORT_LIST = re.compile(r"^export\s*\{(?P<names>[^}]*)\}", re.MULTILINE)
SCRIPT = re.compile(r"<script[^>]+src=['\"](?P<src>[^'\"]+)['\"]")
STYLE = re.compile(r"<link[^>]+href=['\"](?P<href>[^'\"]+\.css)['\"]")


def exported(path) -> set[str]:
    text = path.read_text()
    names = {m.group("name") for m in EXPORT.finditer(text)}
    for match in EXPORT_LIST.finditer(text):
        for part in match.group("names").split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[-1].strip())
    return names


def imported(clause: str) -> set[str]:
    """The names an import clause binds, ignoring `* as ns`."""
    clause = clause.strip()
    if clause.startswith("*"):
        return set()
    names = set()
    braced = re.search(r"\{([^}]*)\}", clause)
    if braced:
        for part in braced.group(1).split(","):
            part = part.strip()
            if part:
                names.add(part.split(" as ")[0].strip())
        clause = clause[: braced.start()].rstrip().rstrip(",")
    if clause.strip():
        names.add("default")
    return names


class ModuleGraphTest(unittest.TestCase):
    def test_every_import_resolves_to_a_file(self):
        for module in sorted(WEB.glob("*.js")):
            for match in IMPORT.finditer(module.read_text()):
                target = (module.parent / match.group("from")).resolve()
                with self.subTest(module=module.name, imports=match.group("from")):
                    self.assertTrue(
                        target.is_file(),
                        f"web/{module.name} imports {match.group('from')}, "
                        f"which is not there",
                    )

    def test_every_imported_name_is_exported(self):
        """The failure this exists for: a file that is there, under a name
        that is no longer what it was."""
        for module in sorted(WEB.glob("*.js")):
            for match in IMPORT.finditer(module.read_text()):
                target = (module.parent / match.group("from")).resolve()
                if not target.is_file():
                    continue  # the test above says so
                available = exported(target)
                for name in imported(match.group("what")):
                    with self.subTest(module=module.name, name=name,
                                      target=target.name):
                        self.assertIn(
                            name, available,
                            f"web/{module.name} imports {name!r} from "
                            f"{match.group('from')}, which does not export it",
                        )

    def test_every_page_loads_files_that_exist(self):
        for page in sorted(WEB.glob("*.html")):
            text = page.read_text()
            for pattern, kind in ((SCRIPT, "src"), (STYLE, "href")):
                for match in pattern.finditer(text):
                    ref = match.group(1)
                    if ref.startswith(("http://", "https://", "//", "data:")):
                        continue
                    with self.subTest(page=page.name, ref=ref):
                        self.assertTrue(
                            (page.parent / ref).resolve().is_file(),
                            f"web/{page.name} loads {ref}, which is not there",
                        )

    def test_no_page_shares_a_script_with_a_module_someone_imports(self):
        """What the collision was: a page's own script and a module another
        page imports, in one file. Either use is fine; both in the same file
        means changing it for one breaks the other silently."""
        entry = {
            match.group("src").lstrip("./")
            for page in WEB.glob("*.html")
            for match in SCRIPT.finditer(page.read_text())
        }
        imports = {
            match.group("from").lstrip("./")
            for module in WEB.glob("*.js")
            for match in IMPORT.finditer(module.read_text())
        }
        self.assertEqual(
            entry & imports, set(),
            "a page's entry script is also imported as a module by another "
            "page; give one of the two its own file",
        )
