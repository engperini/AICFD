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


class CaseTravelsTest(unittest.TestCase):
    """The case a reader is looking at survives every hop between pages.

    ADR-087 made the API calls carry the case so the server stopped answering
    with whichever one it was started on. It did not make the NAVIGATION carry
    it, and the case lives in `location.search` -- so three separate things
    threw it away and each one was silent:

      a link between pages   `href="./"` and `href="./racks.html"` dropped it,
                             so `back` and every group link landed on the
                             server's start case
      a page rewriting its
      own query string       `location.search = "?model=X"` replaced the whole
                             search, case included
      a fetch without it     `/api/racks` then READ AND WROTE the start case.
                             Measured: with the server on `pod-fanwall` and
                             `hall-10mw` open, changing the standard load on
                             the racks page and pressing Save wrote 13.75 kW
                             into `cases/pod-fanwall.yaml` and left
                             `hall-10mw.yaml` untouched (ADR-091)

    The last one is why this is a test and not a fix. A page that shows the
    wrong room is a nuisance; a page that SAVES to the wrong room is a
    corruption, it announces nothing, and the only clue is a case name in a
    header nobody reads twice.

    So the case-scoped endpoints are read out of the server rather than listed
    here: a route that starts using `self._case()` is covered the day it does.
    """

    #: Pages, as opposed to the modules they import. A page is what a reader
    #: navigates to, so a page is what has to keep the case.
    PAGES = ("app.js", "racks.js", "results.js", "equipment.js",
             "components.js")

    def case_scoped_routes(self) -> set[str]:
        """The API paths whose answer depends on which case is open.

        Read out of the router: a branch is case-scoped when its body reaches
        for `self._case()`. Naming them here instead would be a list to keep,
        and a list is what goes stale (ADR-056, ADR-084).
        """
        source = (support.REPO / "aicfd" / "server.py").read_text()
        branch = re.compile(r'self\.path\.startswith\("(/api/[a-z/]+)"\)')
        starts = [(m.group(1), m.end()) for m in branch.finditer(source)]
        routes = set()
        for i, (route, at) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else len(source)
            if "self._case()" in source[at:end]:
                routes.add(route)
        self.assertTrue(routes, "no case-scoped route found; the shape moved")
        return routes

    def test_every_page_keeps_the_case_on_its_links(self):
        """`case.js` is the one place that does it, so every page imports it."""
        for page in self.PAGES:
            with self.subTest(page=page):
                source = (WEB / page).read_text()
                self.assertRegex(
                    source, r"import\s+(?:\{[^}]*\}\s+from\s+)?'\./case\.js'",
                    f"web/{page} does not import case.js, so every link out of "
                    "it drops the case and the reader lands on the case the "
                    "server was started with",
                )

    def test_a_page_never_replaces_its_query_string_without_the_case(self):
        """`location.search = "?x=y"` throws away everything else in it.

        The exception is a line that SETS the case, which is the case menu
        opening another room -- it is the one place the old value should go.
        """
        for path in sorted(WEB.glob("*.js")):
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "location.search =" not in line:
                    continue
                with self.subTest(file=path.name, line=number):
                    self.assertTrue(
                        "withCase(" in line or "?case=" in line,
                        f"web/{path.name}:{number} replaces the whole query "
                        f"string and loses the case: {line.strip()!r}. Wrap it "
                        "in withCase()",
                    )

    def test_every_case_scoped_fetch_carries_the_case(self):
        routes = self.case_scoped_routes()
        calls = re.compile(r"fetch\(\s*([^)]*?)[,)]", re.S)
        for path in sorted(WEB.glob("*.js")):
            source = path.read_text()
            for match in calls.finditer(source):
                target = match.group(1)
                route = next(
                    (r for r in routes
                     if f"'{r}" in target or f"`{r}" in target
                     or f"'..{r}" in target or f"`..{r}" in target),
                    None,
                )
                if route is None:
                    continue
                number = source.count("\n", 0, match.start()) + 1
                with self.subTest(file=path.name, line=number, route=route):
                    self.assertTrue(
                        "withCase(" in target or "case=" in target,
                        f"web/{path.name}:{number} calls {route}, whose answer "
                        "depends on which case is open, without saying which. "
                        "The server falls back to the one it was started with, "
                        "and for a POST that means writing the wrong case",
                    )


class DatasheetFieldsFollowTheUnitTest(unittest.TestCase):
    """Picking a unit has to put THAT unit's numbers in the boxes.

    The fields above the picker are the machine's datasheet. They held the
    previous unit's figures, and the server fills only what a case leaves
    blank (ADR-036) -- so a hall came back naming a 145 kW CRAH and carrying
    432.6 kW, and the reader had no way to see it (ADR-094).
    """

    def picker(self) -> str:
        source = (WEB / "app.js").read_text()
        return source[source.index("function wireUnitPicker"):
                      source.index("function wireComponentPickers")]

    def handler(self) -> str:
        """`wireUnitPicker` alone -- the change handler, without the helper
        it calls. Checking the whole block passes when the helper is defined
        and never called, which is exactly the regression to catch."""
        source = (WEB / "app.js").read_text()
        return source[source.index("function wireUnitPicker"):
                      source.index("function fillFromDatasheet")]

    def test_the_picker_writes_the_datasheet_into_the_fields(self):
        self.assertIn("fillFromDatasheet(", self.handler(),
                      "the change handler never calls it, so picking a unit "
                      "leaves the previous one's numbers in the fields")

    def test_it_uses_the_defaults_the_server_computed(self):
        """Not a second mapping from a unit to the fields: the server sends
        `defaults` already keyed by the spec key it belongs to."""
        picker = self.picker()
        self.assertIn("option.defaults", picker,
                      "the page works the datasheet out for itself")

    def test_it_finds_the_fields_through_the_server_s_own_table(self):
        """`model.editable` maps field to spec path. Anything else here is a
        second copy of where each number lives."""
        picker = self.picker()
        self.assertIn("model.editable", picker)
        self.assertIn("'fanwall'", picker)


class UnitPickerOnThePageTest(unittest.TestCase):
    """The unit the case uses is a field, so the page has to offer it and send it.

    `fanwall.model` became editable when the picker was added (ADR-092). A
    server field nothing on the page reads or writes is a field that does not
    exist as far as a reader is concerned -- which is exactly the state this
    fixed, from the other side.
    """

    def app(self) -> str:
        return (WEB / "app.js").read_text()

    def test_the_fan_wall_group_carries_the_picker(self):
        source = self.app()
        head = source[source.index("title: 'Fan walls'"):]
        head = head[:head.index("params: [")]
        self.assertIn(
            "unitPicker: true", head,
            "the Fan walls group does not ask for the unit picker, so the "
            "case's unit is shown and cannot be changed",
        )

    def test_the_chosen_unit_is_sent_with_the_rest_of_the_changes(self):
        source = self.app()
        self.assertIn(
            "changes.fan_model", source,
            "the page never sends fan_model, so choosing a unit does nothing "
            "on Apply",
        )
        self.assertIn(
            "getElementById('fan-model')", source,
            "nothing reads the picker's value",
        )

    def test_the_picker_uses_the_reason_the_server_computed(self):
        """The page must not restate the rule.

        `equipment_mismatch` decides whether a unit suits a room and says why
        in a sentence. A page that worked it out again from `arrangement`
        would be a second opinion, and the first version of this picker got
        it backwards -- it told the reader a downflow CRAH `needs no raised
        floor`, the opposite of the refusal Apply gives.
        """
        source = self.app()
        picker = source[source.index("function unitRow()"):]
        picker = picker[:picker.index("function wireComponentPickers")]
        self.assertIn("o.suits", picker,
                      "the picker decides for itself whether a unit suits")
        self.assertIn(".why", picker,
                      "the picker does not show the server's own reason")


class DecimalSeparatorTest(unittest.TestCase):
    """One decimal separator, one place that decides it (ADR-083)."""

    FORMS = ("racks.js", "equipment.js", "components.js", "app.js")

    def test_no_form_uses_a_number_input(self):
        """`type="number"` in a comma-locale browser reads `0,8` as the empty
        string, so the figure an engineer typed is dropped on the keystroke --
        silently, which is the worst way for a form to fail."""
        for name in self.FORMS:
            with self.subTest(page=name):
                self.assertNotIn(
                    'type="number"', (WEB / name).read_text().replace(
                        "`type=\"number\"`", ""),
                    f"{name} has a field a comma keyboard cannot fill",
                )

    def test_a_decimal_field_asks_for_a_decimal_keypad(self):
        """Text alone would give a phone the full alphabet."""
        for name in self.FORMS:
            source = (WEB / name).read_text()
            with self.subTest(page=name):
                self.assertEqual(
                    source.count('inputmode="decimal"'),
                    source.count('type="text" inputmode="decimal"'),
                    f"{name} has a decimal field that is not a text field",
                )

    def test_every_form_parses_through_the_one_helper(self):
        """The point of the module: when the separator is settled across the
        tool it changes in one file, not in eleven page scripts."""
        for name in ("racks.js", "equipment.js", "components.js"):
            with self.subTest(page=name):
                self.assertRegex(
                    (WEB / name).read_text(),
                    r"import \{[^}]*\bnum\b[^}]*\} from '\./decimal\.js'",
                )

    def test_the_helper_takes_both_separators_and_writes_a_dot(self):
        source = (WEB / "decimal.js").read_text()
        self.assertIn("replace(',', '.')", source)
        self.assertIn("export const num", source)
        self.assertIn("export const dec", source)


class BlockKeyWithAListTest(unittest.TestCase):
    """A key's value may be a list at the key's own indent (ADR-088).

    YAML allows it and `yaml.safe_dump` writes it, so any case that has been
    round-tripped through one looks like this:

        racks:
          row:
          - width: 0.6
          - width: 0.6
            load_kw: 0

    The editor treated "the block under a key" as "the lines indented deeper
    than the key", which those items are not. Removing the block took the key
    and left the list; the orphans then read as a list where a key was
    expected. The rack page rewrites that block by removing it and writing it
    again, so the removal was half of every save -- and no case with a typical
    row could be saved at all.
    """

    from aicfd import yamledit as Y

    SAME_INDENT = (
        "racks:\n"
        "  per_row: 15\n"
        "  row:\n"
        "  - width: 0.6\n"
        "  - width: 0.6\n"
        "    load_kw: 0\n"
        "  airflow_cfm_per_kw: 158\n"
    ).split("\n")

    DEEPER = (
        "racks:\n"
        "  per_row: 15\n"
        "  row:\n"
        "    - {width: 0.6}\n"
        "    - {width: 0.8, load_kw: 32}\n"
        "  airflow_cfm_per_kw: 158\n"
    ).split("\n")

    def parsed(self, lines):
        import yaml as Y
        return Y.safe_load("\n".join(lines))

    def test_removing_a_key_takes_its_list_with_it(self):
        for style, lines in (("same indent", self.SAME_INDENT), ("deeper", self.DEEPER)):
            with self.subTest(style=style):
                work = list(lines)
                self.Y.set_map(work, ["racks", "row"], {})
                spec = self.parsed(work)          # raises if the list is orphaned
                self.assertNotIn("row", spec["racks"])
                self.assertEqual(spec["racks"]["per_row"], 15)
                self.assertEqual(spec["racks"]["airflow_cfm_per_kw"], 158)

    def test_replacing_a_list_takes_the_whole_item(self):
        """An item is not a line: `- width: 0.6` followed by `  load_kw: 0` is
        one position, and stopping at the continuation cut the list in half."""
        work = list(self.SAME_INDENT)
        self.Y.replace_list(work, ["racks", "row"], ["    - {width: 0.8}"])
        spec = self.parsed(work)
        self.assertEqual(spec["racks"]["row"], [{"width": 0.8}])
        self.assertEqual(spec["racks"]["airflow_cfm_per_kw"], 158)

    def test_a_key_with_nothing_after_the_colon_is_found(self):
        """`  row:` ends at the colon. Whether the caller split the file with
        `split("\\n")` or `splitlines(True)` must not decide that."""
        for keep in (False, True):
            text = "racks:\n  per_row: 15\n  row:\n  - width: 0.6\n"
            lines = text.splitlines(True) if keep else text.split("\n")
            with self.subTest(keepends=keep):
                self.assertIsNotNone(self.Y.find(lines, ["racks", "row"]))
