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


class PlenumDrawingTest(unittest.TestCase):
    """The plenum reads as what it is on a drawing (ADR-058)."""

    def drawing(self) -> str:
        return (WEB / "drawing.js").read_text()

    def test_the_inner_leaf_is_not_drawn_in_the_containment_colour(self):
        """Every `wall` wears the containment green. A green line across the
        end of the hall says that end is contained, which it is not -- the
        same misreading ADR-045 answered over the racks."""
        self.assertIn("isPlenumWall", self.drawing())
        self.assertIn(".dw-partition{", (WEB / "drawing.css").read_text())

    def test_a_supply_grille_is_drawn_like_every_other_grille(self):
        """Red is what a grille looks like here. A colour of its own was a
        new thing to learn on a drawing that already had a word for it."""
        js = self.drawing()
        self.assertNotIn("dw-supply", js)
        self.assertNotIn(".dw-supply", (WEB / "drawing.css").read_text())

    def test_the_panels_are_drawn_once(self):
        """Step 7 already draws every panel a view sees edge-on. Drawing the
        plenum's again put a second line over the first and a second caption
        beside it."""
        js = self.drawing()
        self.assertNotIn("dw-supplyedge", js)
        self.assertEqual(js.count("// 6.4"), 1)


class RacksPageTest(unittest.TestCase):
    """The rack page writes widths, blanks and the typical row (ADR-074)."""

    def setUp(self):
        support.sandbox(self)

    def test_it_serves_every_position_including_the_blanks(self):
        from aicfd import server

        out = server.read_racks("pod-fanwall")
        self.assertIn("row", out)
        self.assertIn("rows", out)
        for place in out["racks"]:
            for key in ("id", "row", "blank", "width_m", "load_kw", "sized"):
                self.assertIn(key, place)
        self.assertEqual(out["totals"]["cabinets"] + out["totals"]["blanks"],
                         out["totals"]["positions"])

    def test_the_standard_cabinet_is_editable_here(self):
        from aicfd import server

        out = server.write_racks("pod-fanwall", {"size": [0.7, 1.2, 2.2]})
        self.assertEqual(out["rejected"], [])
        self.assertEqual(server.read_racks("pod-fanwall")["standard"]["size"][0], 0.7)

    def test_a_typical_row_survives_a_save_and_a_reload(self):
        from aicfd import server

        plan = [{}, {"width": 0.8, "load_kw": 12.0}, {"blank": True, "width": 0.4}]
        out = server.write_racks("pod-fanwall", {"row": plan})
        self.assertEqual(out["rejected"], [])
        back = server.read_racks("pod-fanwall")
        self.assertEqual(back["row"], plan)
        self.assertEqual(back["totals"]["blanks"], 1)
        self.assertEqual(back["totals"]["cabinets"], 2)

    def test_the_rows_length_becomes_the_count(self):
        """Saying it twice only invites the two to disagree."""
        from aicfd import server

        server.write_racks("pod-fanwall", {"row": [{}] * 7})
        self.assertEqual(server.read_racks("pod-fanwall")["totals"]["positions"], 7)

    def test_per_position_widths_and_blanks_survive(self):
        from aicfd import server

        server.write_racks("pod-fanwall", {"widths": {"R2": 0.9}, "blanks": ["R1"]})
        back = {r["id"]: r for r in server.read_racks("pod-fanwall")["racks"]}
        self.assertTrue(back["R1"]["blank"])
        self.assertAlmostEqual(back["R2"]["width_m"], 0.9, places=2)
        self.assertTrue(back["R2"]["sized"])

    def test_a_blank_position_can_be_edited_back_into_a_cabinet(self):
        """Validating against the built racks would refuse this: a blank is
        not in `model.racks`, so its own id would read as unknown."""
        from aicfd import server

        server.write_racks("pod-fanwall", {"blanks": ["R1"]})
        out = server.write_racks("pod-fanwall", {"blanks": [], "loads": {"R1": 9.0}})
        self.assertEqual(out["rejected"], [])
        back = {r["id"]: r for r in server.read_racks("pod-fanwall")["racks"]}
        self.assertFalse(back["R1"]["blank"])
        self.assertAlmostEqual(back["R1"]["load_kw"], 9.0)

    def test_a_bad_position_is_named_and_nothing_else_is_lost(self):
        from aicfd import server

        out = server.write_racks("pod-fanwall", {
            "row": [{}, {"width": 9.0}],
            "widths": {"NOPE": 0.8},
        })
        self.assertTrue(any("9" in r for r in out["rejected"]))
        self.assertTrue(any("NOPE" in r for r in out["rejected"]))

    def test_the_file_keeps_its_comments(self):
        from aicfd import server

        server.write_racks("pod-fanwall", {
            "row": [{}, {"blank": True, "width": 0.4}],
            "widths": {"R1": 0.8},
        })
        text = (server.CASES_DIR / "pod-fanwall.yaml").read_text()
        self.assertIn("sizes its resistance", text)
        self.assertIn("from the gallery wall", text)
