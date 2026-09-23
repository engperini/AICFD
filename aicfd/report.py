"""The Word deliverable: a technical report built from a solved export.

An engineering study is finished when someone who was not in the room can
read it, disagree with it and check it. `results/<case>/report.md` already
carries every number; this turns the same export into the document that
actually gets circulated -- with the geometry, the boundary conditions, the
verification, the field maps and the per-unit tables in the order a reviewer
reads them.

Two rules hold throughout:

* **every number comes from the export.** Nothing here computes physics, and
  nothing is typed in. If a quantity is not in `viewer.json`, the document
  says the tool does not produce it rather than estimating it.
* **the limits are stated where the result is**, not in a footnote. A coarse
  conceptual mesh that ranks racks correctly and carries 1 to 2 K on any one
  of them is a useful instrument and a misleading one, depending entirely on
  whether the reader was told which it is.

`python-docx` is imported late and is the only dependency outside the runtime
minimum; `aicfd run`, `aicfd post` and the checks never need it.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from aicfd.figures import (
    Export, ashrae, capacity, convergence, geometry, plan, rack_map, section,
    units,
)
# `CRAC` at the start of a label stays `CRAC`; `str.capitalize` makes it
# `Crac`, which is a different machine's name as far as a reader is concerned.
from aicfd.model import as_a_label

ACCENT = "2F6F6A"
INK = "0B0B0B"
SECOND = "52514E"
MUTED = "898781"
BAD = "D03B3B"
GOOD = "0CA30C"


def _docx():
    try:
        import docx  # noqa: F401
    except ModuleNotFoundError as error:  # pragma: no cover - environment
        raise SystemExit(
            "The Word report needs python-docx:\n"
            "    pip install python-docx matplotlib\n"
            "Everything else in AICFD runs without them."
        ) from error
    return docx


# --- document furniture -------------------------------------------------------


def _shade(cell, colour: str) -> None:
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement

    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:fill"), colour)
    cell._tc.get_or_add_tcPr().append(shd)


def _rgb(colour: str):
    from docx.shared import RGBColor

    return RGBColor.from_string(colour)


def _run(paragraph, text: str, *, bold=False, size=None, colour=None, italic=False):
    from docx.shared import Pt

    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    if size:
        run.font.size = Pt(size)
    run.font.color.rgb = _rgb(colour or INK)
    return run


def _para(doc, text="", *, size=10, colour=None, bold=False, italic=False,
          space_after=6, align=None):
    from docx.shared import Pt

    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_after = Pt(space_after)
    if align is not None:
        paragraph.alignment = align
    if text:
        _run(paragraph, text, bold=bold, size=size, colour=colour, italic=italic)
    return paragraph


def _heading(doc, text: str, level: int = 1) -> None:
    from docx.shared import Pt

    sizes = {0: 22, 1: 15, 2: 11.5}
    paragraph = doc.add_paragraph()
    paragraph.paragraph_format.space_before = Pt(16 if level < 2 else 12)
    paragraph.paragraph_format.space_after = Pt(4)
    run = _run(paragraph, text, bold=level >= 1, size=sizes.get(level, 11),
               colour=ACCENT if level == 2 else INK)
    run.font.name = "Georgia" if level < 2 else "DejaVu Sans"
    if level == 1:
        doc.add_paragraph().paragraph_format.space_after = Pt(0)


def _bullets(doc, items) -> None:
    from docx.shared import Pt

    for item in items:
        paragraph = doc.add_paragraph(style="List Bullet")
        paragraph.paragraph_format.space_after = Pt(3)
        _run(paragraph, item, size=10)


def _table(doc, headers, rows, widths=None, note=None) -> None:
    """A table with a dark header band, the way a datasheet prints one."""
    from docx.shared import Pt, Cm

    table = doc.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    for cell, text in zip(table.rows[0].cells, headers):
        _shade(cell, ACCENT)
        cell.paragraphs[0].paragraph_format.space_after = Pt(2)
        _run(cell.paragraphs[0], str(text), bold=True, size=8.5, colour="FFFFFF")
    for values in rows:
        cells = table.add_row().cells
        for i, (cell, value) in enumerate(zip(cells, values)):
            cell.paragraphs[0].paragraph_format.space_after = Pt(2)
            text = "" if value is None else str(value)
            colour = INK
            if text.startswith("FAIL"):
                colour = BAD
            elif text.startswith("PASS"):
                colour = GOOD
            _run(cell.paragraphs[0], text, size=8.5, colour=colour,
                 bold=(i == 0 and len(headers) == 2))
    if widths:
        for row in table.rows:
            for cell, width in zip(row.cells, widths):
                cell.width = Cm(width)
    if note:
        _para(doc, note, size=8, colour=MUTED, space_after=10)
    else:
        _para(doc, space_after=6)


def _figure(doc, path: Path, caption: str, width_cm: float = 16.0) -> None:
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm

    paragraph = doc.add_paragraph()
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.add_run().add_picture(str(path), width=Cm(width_cm))
    _para(doc, caption, size=8, colour=MUTED, align=WD_ALIGN_PARAGRAPH.CENTER,
          space_after=12)


def _num(value, decimals=1, dash="—"):
    if value is None:
        return dash
    return f"{value:,.{decimals}f}"


# --- the report ---------------------------------------------------------------


#: Words a case slug carries that are not words: the machine kinds, the units
#: and the abbreviations an engineer writes in capitals. `str.capitalize`
#: made "Crah" and "1mw" of them, on the cover.
_TITLE_WORDS = {
    "crac": "CRAC", "crah": "CRAH", "fanwall": "Fan Wall", "fw": "FW",
    "dx": "DX", "cw": "CW", "cac": "CAC", "hac": "HAC", "pod": "POD",
    "dh": "DH", "mw": "MW", "kw": "kW", "ups": "UPS", "pdu": "PDU",
    "it": "IT", "hvac": "HVAC", "ashrae": "ASHRAE",
}


def title_of(case: str) -> str:
    """A case slug as a title: ``hall-double-gallery`` -> ``Hall Double
    Gallery``, ``hall-cage-1mw-crah`` -> ``Hall Cage 1 MW CRAH``. Replaced
    by ``--title`` when the room has a real name."""
    words = []
    for word in case.replace("_", "-").split("-"):
        number = re.match(r"^(\d+(?:[.,]\d+)?)([a-z]+)$", word)
        if number and number.group(2) in _TITLE_WORDS:
            words.append(f"{number.group(1)} {_TITLE_WORDS[number.group(2)]}")
        else:
            words.append(_TITLE_WORDS.get(word, word.capitalize()))
    return " ".join(words)


def build(results_dir: str | Path, out_path: str | Path,
          client: str | None = None, author: str | None = None,
          title: str | None = None, ramp: str | None = None) -> Path:
    """Write the Word report for one exported result.

    ``ramp`` names one of `palette.OPTIONAL_RAMPS` and repaints the field
    figures in it. The page offers the same choice and sends it with the
    cover, so a document downloaded off a page showing the spectrum comes out
    in the spectrum -- the alternative is a reader holding a screen and a
    document that disagree about what warm looks like (ADR-101).
    """
    docx = _docx()
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    export = Export(results_dir)
    # The figures belong to the document, not to the export they were read
    # from: writing them beside the source would have this command modify a
    # result -- and, once the worked results moved to `reference/`, modify a
    # file the repository tracks (ADR-032).
    out = Path(out_path)
    figures = out.parent / "figures"
    figures.mkdir(parents=True, exist_ok=True)
    drawn = _draw(export, figures, ramp)

    doc = docx.Document()
    style = doc.styles["Normal"]
    style.font.name = "DejaVu Sans"
    style.font.size = Pt(10)
    style.paragraph_format.space_after = Pt(6)
    for section_ in doc.sections:
        section_.top_margin = Cm(2.2)
        section_.bottom_margin = Cm(2.2)
        section_.left_margin = Cm(2.4)
        section_.right_margin = Cm(2.4)

    _cover(doc, export, client, author, title or title_of(export.payload["case"]))
    doc.add_page_break()
    _contents(doc)
    doc.add_page_break()
    _introduction(doc)
    doc.add_page_break()
    _summary(doc, export, drawn)
    doc.add_page_break()
    _methodology(doc, export, drawn)
    doc.add_page_break()
    _results(doc, export, drawn)
    doc.add_page_break()
    _conclusions(doc, export)
    _limits(doc, export)
    _annex(doc, export)

    doc.save(str(out))
    return out


def _containment_of(export: Export) -> str | None:
    """`"cold"`, `"hot"` or None, read off the panels that were BUILT.

    One answer for the whole document. The methodology already read it this
    way; the geometry table said "Contained hot aisles" and the figure
    captions said "contained hot aisle" on a hall whose cold aisles are the
    contained ones (ADR-100, ADR-112).
    """
    if any(p["name"].startswith("containment_lid")
           for p in export.panels("containment_lid")):
        return "cold"
    if any(p["name"].startswith("containment_wall")
           for p in export.panels("containment_wall")):
        return "hot"
    return None


def _aisle_rows(export: Export, model: dict, blocks) -> list[tuple[str, str]]:
    """The hot and cold aisle counts, with "contained" on the one that is."""
    which = _containment_of(export)
    rows = []
    for kind, key in (("hot", "hot_aisles"), ("cold", "cold_aisles")):
        count = len(model.get(key) or [])
        if which == kind:
            rows.append((f"Contained {kind} aisles", f"{count} in plan"
                         + (f", {count * len(blocks)} separate containment "
                            f"volumes" if len(blocks) > 1 else "")))
        else:
            rows.append((f"{kind.capitalize()} aisles", f"{count}"))
    return rows


def _widths_as_specified(model: dict, built) -> str:
    """AS SPECIFIED, AND AS MESHED. The built width is the mesh's, and a
    basis-of-design table that says 0,9 m of an 800 mm cabinet is wrong to
    the reader who specified it."""
    spec = ((model.get("spec") or {}).get("racks") or {})
    size = spec.get("size")
    stated = float(size[0]) if isinstance(size, (list, tuple)) and size else None
    if stated is not None and len(built) == 1 and abs(built[0] - stated) > 1e-6:
        return f"{stated:g} m as specified, meshed as {built[0]:g} m"
    return ", ".join(f"{w:g} m" for w in built)


def _units(count: int) -> str:
    """`unit` or `units`. A report that says "2 unit(s)" was written by a
    program and reads like one."""
    return "unit" if count == 1 else "units"


def _grouped_warnings(warnings) -> list[str]:
    """One line per distinct snap, naming every plane that took it.

    A hall with seven contained aisles produces the same sentence about the
    same 3,200 m plane seven times over -- once for each lid, and again for
    the rack top, the row ends and the containment doors. Thirty-three bullets
    of which four are distinct is not a list of findings; a reader skims it and
    stops reading the section. Identical snaps are collapsed to the plane they
    all landed on, with their subjects named, and repeated observations about
    different aisles are collapsed the same way.

    Nothing is dropped or reworded: every subject and every predicate here is
    the one the builder wrote.
    """
    groups: dict[str, list[str]] = {}
    order: list[str] = []
    # A predicate that reads on from its subject ("rows F2/F3 FACE the same
    # aisle") takes no colon; one that names a plane ("CRAC width: 2.553 m
    # falls between") keeps the builder's own.
    continues: set[str] = set()
    for line in warnings:
        subject, sep, predicate = line.partition(": ")
        if not sep:
            subject, predicate = "", line
        # "rows F2 and F3 face the same ..." -- the pair is the subject of the
        # observation, not part of it, so the four aisles that share a finding
        # share a bullet.
        rows = re.match(r"^rows (\S+) and (\S+) (.*)$", predicate)
        if rows:
            subject = f"{subject}: rows {rows.group(1)}/{rows.group(2)}".lstrip(": ")
            predicate = rows.group(3)
            continues.add(predicate)
        if predicate not in groups:
            groups[predicate] = []
            order.append(predicate)
        if subject and subject not in groups[predicate]:
            groups[predicate].append(subject)
    out = []
    for predicate in order:
        subjects = groups[predicate]
        if not subjects:
            out.append(predicate)
            continue
        joint = " " if predicate in continues else ": "
        if len(subjects) == 1:
            out.append(f"{subjects[0]}{joint}{predicate}")
        else:
            out.append(
                f"{_and(_without_repeated_head(subjects))}{joint}{predicate}")
    return out


def _without_repeated_head(subjects: list[str]) -> list[str]:
    """`floor plates: rows F2/F3, rows F6/F7` -- not the head four times over."""
    heads = {s.split(": ")[0] for s in subjects if ": " in s}
    if len(heads) != 1 or not all(": " in s for s in subjects):
        return subjects
    head = heads.pop()
    return [f"{head}: {subjects[0].split(': ', 1)[1]}"] + [
        s.split(": ", 1)[1] for s in subjects[1:]]


def _and(items: list[str]) -> str:
    """`a, b and c` -- a list an engineer would write, not `['a', 'b', 'c']`."""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _hall_span(export: Export) -> tuple[float, float, float, float]:
    """The whole room, galleries and all, with a margin for the labels.

    What the basis of design is read against is the LAYOUT drawing, and the
    reader is checking rows, aisles and where the plant stands -- not one
    cabinet's name (ADR-116).
    """
    model = export.model
    lo = model["domain"]["lo"] if "domain" in model else [0.0, 0.0, 0.0]
    hi = model["domain"]["hi"] if "domain" in model else list(export.size)
    margin = max(hi[0] - lo[0], hi[1] - lo[1]) * 0.015
    return (lo[0] - margin, hi[0] + margin, lo[1] - margin, hi[1] + margin)


def _zoom_span(export: Export) -> tuple[float, float, float, float]:
    """One pod of one block: five metres of row, and the pod it belongs to.

    Enough cabinets to read the pattern -- a wide one, a blanked position, a
    zero -- and few enough that the names fit. Cut on both axes, because five
    metres of row across the full width of a hall is a strip nobody can read
    a name off.
    """
    model = export.model
    blocks = model.get("blocks") or []
    lo_h, hi_h = blocks[0] if blocks else (0.0, export.size[0])
    hi_h = min(hi_h, lo_h + 5.0)
    rows = model.get("rows") or []
    # DISTINCT bands. A hall of two blocks has two rows on the same band, so
    # taking the first two rows gave the same band twice and the detail showed
    # one row where it meant to show a pod.
    distinct = sorted({tuple(r["band"]) for r in rows})
    if len(distinct) >= 2:
        bands = distinct[:2]
        lo_v, hi_v = bands[0][0], bands[1][1]
        margin = (hi_v - lo_v) * 0.08
        return (lo_h, hi_h, max(0.0, lo_v - margin), hi_v + margin)
    return (lo_h, hi_h, 0.0, export.size[1])


def _draw(export: Export, figures: Path, ramp: str | None = None) -> dict:
    """Render every figure the document embeds."""
    model = export.model
    racks = export.racks
    rack_top = max(r["hi"][2] for r in racks) if racks else 2.0
    blocks = model.get("blocks") or [[model["hall"]["lo"][0], model["hall"]["hi"][0]]]
    hot = model["hot_aisles"][len(model["hot_aisles"]) // 2]
    cold = model["cold_aisles"][len(model["cold_aisles"]) // 2]
    block = blocks[len(blocks) // 2]
    return {
        # The room in detail: which cabinet is which, and what it carries.
        # The three dimensioned drawings that used to stand beside it were
        # removed -- unreadable, and the model page draws the room properly
        # (ADR-102).
        # THE WHOLE DATA HALL, the way the model page draws it in plan. One
        # pod at rack height answered a question nobody had -- the basis of
        # design is where a reader checks the LAYOUT against their drawing,
        # and for that they need the room (ADR-116).
        "geo_hall": geometry(export, figures / "geo-hall.png", 2,
                             "The data hall in plan",
                             zoom=_hall_span(export)),
        "plan_mid": plan(export, figures / "plan-rack-mid.png", rack_top / 2,
                         f"Temperature at z = {rack_top / 2:.2f} m — rack mid-height",
                         ramp),
        "plan_top": plan(export, figures / "plan-rack-top.png", rack_top - 0.2,
                         f"Temperature at z = {rack_top - 0.2:.2f} m — top of the racks",
                         ramp),
        "plan_plenum": plan(export, figures / "plan-plenum.png",
                            (model["ceiling_z"] + export.size[2]) / 2,
                            "Temperature inside the return plenum", ramp),
        "cross": section(export, figures / "section-across.png", 0,
                         (block[0] + block[1]) / 2,
                         f"Section across the hall at x = {(block[0] + block[1]) / 2:.2f} m",
                         "y — across the hall (m)", ramp),
        "long_cold": section(export, figures / "section-along-cold.png", 1,
                             (cold[0] + cold[1]) / 2,
                             f"Section along the hall at y = "
                             f"{(cold[0] + cold[1]) / 2:.2f} m — through a cold aisle",
                             "x — along the hall (m)", ramp),
        "long_hot": section(export, figures / "section-along-hot.png", 1,
                            (hot[0] + hot[1]) / 2,
                            f"Section along the hall at y = "
                            f"{(hot[0] + hot[1]) / 2:.2f} m — through a contained "
                            "hot aisle",
                            "x — along the hall (m)", ramp),
        # WHERE THE AIR GOES AND WHAT IT COSTS. A study is read for three
        # fields and only the first was ever drawn (ADR-116). The speed maps
        # answer "is the aisle fed"; the pressure maps show the plenum, the
        # drop across the cabinets and what the units have to produce.
        "plan_speed": plan(export, figures / "plan-speed.png", rack_top / 2,
                           f"Air speed at z = {rack_top / 2:.2f} m — rack "
                           f"mid-height", ramp, field="speed"),
        "cross_speed": section(export, figures / "section-speed.png", 0,
                               (block[0] + block[1]) / 2,
                               f"Air speed across the hall at x = "
                               f"{(block[0] + block[1]) / 2:.2f} m",
                               "y — across the hall (m)", ramp, field="speed"),
        "plan_pressure": plan(export, figures / "plan-pressure.png",
                              rack_top / 2,
                              f"Static pressure at z = {rack_top / 2:.2f} m — "
                              f"rack mid-height", ramp, field="P"),
        "cross_pressure": section(export, figures / "section-pressure.png", 0,
                                  (block[0] + block[1]) / 2,
                                  f"Static pressure across the hall at x = "
                                  f"{(block[0] + block[1]) / 2:.2f} m",
                                  "y — across the hall (m)", ramp, field="P"),
        "racks": rack_map(export, figures / "rack-intake.png", ramp=ramp),
        "units": units(export, figures / "units.png"),
        "ashrae": ashrae(export, figures / "ashrae.png"),
        "convergence": convergence(export, figures / "convergence.png"),
        "capacity": capacity(export, figures / "capacity.png"),
    }


def _cover(doc, export: Export, client, author, title_text: str) -> None:
    from docx.shared import Pt

    _para(doc, space_after=90)
    _para(doc, "AICFD", size=13, bold=True, colour=ACCENT, space_after=2)
    _para(doc, "AI-ASSISTED CFD FOR DATA CENTRE COOLING", size=7.5,
          colour=MUTED, space_after=60)
    _para(doc, "TECHNICAL REPORT · STEADY-STATE ANALYSIS", size=8,
          bold=True, colour=ACCENT, space_after=6)
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(30)
    run = _run(title, f"CFD Analysis of {title_text}", size=26, colour=INK)
    run.font.name = "Georgia"
    if client:
        _para(doc, client, size=13, space_after=4)
    if author:
        _para(doc, author, size=10, colour=SECOND, space_after=2)
    _para(doc, date.today().strftime("%B %Y"), size=10, colour=MUTED, space_after=40)
    unit = export.equipment
    if unit:
        # Named on the cover because every capacity number inside is that
        # machine's, read off the coil its selection characterises. A reader
        # who disagrees with the unit can stop here.
        _para(doc,
              f"Cooling plant · {len(export.model['fans'])} × "
              f"{unit.family} {unit.model}".strip(),
              size=9, colour=SECOND, space_after=4)
    _para(doc,
          f"Case {export.payload['case']} · solved to iteration "
          f"{export.payload['time']} · "
          f"{export.kpis['cells']:,} cells · "
          f"{'all checks passed' if export.payload['valid'] else 'CHECKS FAILED'}",
          size=8, colour=MUTED)


def _contents(doc) -> None:
    _heading(doc, "Contents", 1)
    for number, name, note in (
        ("1", "Introduction",
         "The method: scope, solver, room, cooling unit, coupled solve, "
         "acceptance"),
        ("2", "Summary", "Scope, objectives, basis of design and headline results"),
        ("3", "Methodology",
         "Geometry, mesh, models, boundary conditions and the cooling unit"),
        ("4", "Results", "Verification, temperature and airflow fields, unit by unit"),
        ("5", "Conclusions", "Findings, and what each one supports"),
        ("6", "Limitations",
         "Where the model represents the room differently from the room"),
        ("A", "Annex", "Rack intake temperature, every position"),
    ):
        paragraph = doc.add_paragraph()
        _run(paragraph, f"{number}   ", bold=True, size=11, colour=ACCENT)
        _run(paragraph, name, bold=True, size=11)
        _run(paragraph, f"\n      {note}", size=8.5, colour=MUTED)


def _introduction(doc) -> None:
    """The method the software uses, printed identically in every report.

    Fixed text, and deliberately free of this study's numbers: it describes
    how AICFD models a data hall, so a reader meets the method before meeting
    any result that rests on it. The study begins at section 2.
    """
    _heading(doc, "1  Introduction", 1)
    _heading(doc, "1.1  Scope", 2)
    _para(doc,
          "This is a steady-state CFD analysis of a data hall at full load "
          "with every cooling unit in service — the operating point that "
          "governs sizing, layout and containment decisions. It establishes:")
    _bullets(doc, [
        "the temperature of the air entering every rack, against the ASHRAE "
        "class A1 recommended band;",
        "that the air loop closes — the units move the mass they are given, "
        "the envelope holds, and the return air carries the installed load;",
        "what each cooling unit delivers at the air the room presents to it;",
        "what the room costs the units in static pressure, against what their "
        "fans produce at the airflow they move.",
    ])

    _heading(doc, "1.2  The solver", 2)
    _para(doc,
          "The flow is solved with OpenFOAM v1912 using buoyantSimpleFoam, its "
          "steady solver for buoyant turbulent flow. The case specification, "
          "the generated case, the solver log and the exported result are "
          "delivered with this report.")
    _table(doc, ["What is solved", "How"], [
        ("Mass and momentum",
         "Steady compressible flow, pressure–velocity coupling by the SIMPLE "
         "algorithm. The pressure variable is p_rgh — static pressure less the "
         "hydrostatic column — which resolves buoyancy at room scale."),
        ("Energy",
         "Solved as enthalpy, with density following temperature through the "
         "perfect gas law at the site's operating pressure. Air at 34 °C is "
         "4 % lighter than at 22 °C, and that difference drives the plumes and "
         "the stratification in the aisles."),
        ("Turbulence",
         "Steady RANS with the k-epsilon model and buoyancy terms active. It "
         "resolves the mean field: aisle-to-aisle temperatures, the ranking of "
         "racks, and the hall-scale pressure field."),
        ("Buoyancy",
         "Gravity is active. Hot air rises out of a contained aisle, and "
         "containment leakage appears as warm air arriving above the racks."),
    ], widths=[4.0, 12.0],
        note="Air speeds in a data hall stay under 2 % of the speed of sound, "
             "so compressibility enters through the density's dependence on "
             "temperature.")

    _heading(doc, "1.3  The room", 2)
    _para(doc,
          "The geometry follows from the case specification: equipment sizes, "
          "aisle widths and clearances. It is meshed as a single structured "
          "hexahedral block, with every internal surface cut into it as a "
          "two-sided baffle — containment panels, false ceiling, row ends and "
          "tops, return grilles and the cooling units.")
    _bullets(doc, [
        "Racks are porous zones carrying their own resistance curve and their "
        "own heat.",
        "Return grilles are porous, at the loss coefficient from their "
        "datasheet and the face velocity they see.",
        "Containment panels are zero-thickness baffles.",
        "The air loop closes inside the domain: air leaves each unit's intake "
        "and re-enters the hall through its supply.",
    ])

    _heading(doc, "1.4  The cooling unit, and what the engineer enters", 2)
    _para(doc,
          "A unit is described by one manufacturer selection. The engineer "
          "enters it as issued, the software checks it against itself — the "
          "stated air flow carrying air between the stated temperatures has to "
          "give the stated capacity — and builds the unit's coil from it.")
    _table(doc, ["Step", "What happens"], [
        ("1 — the selection is entered",
         "Return and supply air, air flow, net sensible capacity and "
         "electrical input. A chilled-water unit adds the water it was "
         "selected at; a direct-expansion unit adds its total capacity "
         "beside its sensible one, which is what fixes the coil surface."),
        ("2 — the selection is checked",
         "Air flow times the temperature difference has to carry the stated "
         "capacity. A selection whose own numbers disagree is refused before "
         "anything is solved."),
        ("3 — the coil is built",
         "A coil is a heat exchanger: what it transfers is its effectiveness "
         "times the air's capacity rate times the difference between the "
         "return air and the cold side, and the selection fixes the "
         "effectiveness at that one point, which fixes the conductance. In a "
         "CHILLED-WATER unit the cold side is the entering water, and the "
         "coil is a counterflow heat exchanger. In a DIRECT-EXPANSION unit "
         "the refrigerant boils, so it holds its temperature and the cold "
         "side is "
         "the coil surface — the apparatus dew point that the selection's own "
         "split between sensible and total capacity implies. Section 2 says "
         "which of the two this plant is."),
        ("4 — the curve is reviewed",
         "The coil answers at any return air temperature and any air flow, "
         "which is the curve printed in section 3. The engineer keeps it, or "
         "replaces it with the manufacturer's own capacity curve."),
        ("5 — the model runs",
         "From here each unit is a machine the solution talks to, as "
         "section 1.5 describes."),
    ], widths=[4.0, 12.0],
        note="One selection is what the method needs. A manufacturer who "
             "states the unit's own division of resistance between the air "
             "side and the cold side replaces the default in step 3.")

    _heading(doc, "1.5  Solving the room and the units together", 2)
    _para(doc,
          "The supply air temperature is an output. A cooling unit delivers "
          "what its coil gives it, from the air the room returns, so the room "
          "and the machines are solved as one problem.")
    _table(doc, ["Step", "What happens"], [
        ("1 — the room is solved",
         "A segment of the flow solution runs at the current supply air "
         "temperature."),
        ("2 — each unit is read",
         "Every unit's mixed-mean return temperature and mass flow are taken "
         "from the solved field, unit by unit."),
        ("3 — each coil answers",
         "That return goes through that unit's coil. What the unit modulates "
         "holds the setpoint — a chilled-water unit's valve, a "
         "direct-expansion unit's compressors — and once that is at its limit "
         "the supply air follows the return."),
        ("4 — the field continues",
         "Each answer is written back as that unit's supply temperature and "
         "the solution carries on from the field already there."),
        ("5 — the loop closes",
         "It ends when no unit's supply air temperature shifts by more than "
         "0.02 K between segments, which is convergence of the room and the "
         "machines together."),
    ], widths=[4.0, 12.0],
        note="The room fixes its own temperature rise — load over mass flow — "
             "so a change in supply moves the return one for one and the coil "
             "passes back the fraction its effectiveness leaves. Two or three "
             "segments reach the fixed point.")

    _heading(doc, "1.6  Acceptance", 2)
    # THE RULE, NOT THIS RUN'S VERDICT. Section 1 is the method and prints the
    # same words every time, so it cannot say how many checks applied or
    # whether they passed: it said "Eleven identities" above a table of
    # thirteen, and "all of them pass before a temperature in this document is
    # quoted" on the front of a report whose cover said CHECKS FAILED. What
    # belongs here is the rule the tool works to; the verdict belongs where
    # the numbers are -- the cover, section 2, section 4.1 and section 5
    # (ADR-123).
    _para(doc,
          "A run is accepted on physical grounds. Up to fourteen identities "
          "the solution has to satisfy are evaluated on the converged field: "
          "mass in against mass out, the heat the return air carries against "
          "the load the racks release, the pressure drop across the racks "
          "against the resistance they were given, the air each unit draws "
          "against what it supplies, and the rest of the list in section 4.1, "
          "which carries the numbers that produced each verdict. Which of "
          "them apply follows from the arrangement — a hall with no raised "
          "floor has no floor plates to judge.")
    _para(doc,
          "A run is accepted only when every check that applies passes. The "
          "cover page carries this run's verdict in one line and section 4.1 "
          "carries it check by check; where they say a check failed, sections 4 "
          "and 5 are diagnostic material until it is cleared.")
    _para(doc,
          "Four stations on the air loop — the units' supply, the cabinets' "
          "intakes, the ceiling the contained aisles discharge through, and "
          "the units' return — are recorded while the field settles, and the "
          "run is accepted once they have stopped moving. Each is the "
          "mixing-cup temperature of the whole stream crossing it, reported "
          "with the range that stream spans.")


# --- 2 summary ----------------------------------------------------------------


def _summary(doc, export: Export, drawn: dict) -> None:
    kpis = export.kpis
    model = export.model
    fan = model.get("operating") or {}
    zones = kpis["zones"]
    warmest = max(zones, key=lambda z: z["inlet_top_c"] or -999)
    hvac = kpis.get("hvac") or {}
    site = model.get("site") or {}

    _heading(doc, "2  Summary", 1)
    _heading(doc, "Overview", 2)
    _bullets(doc, [
        f"This report presents the modelling approach and the results of a "
        f"steady-state CFD simulation of the {export.payload['case']} data hall "
        f"under the IT load described in its case specification.",
        "The simulation resolves the temperature and airflow fields through the "
        "room, to identify where the air entering the IT equipment is warmest "
        "and to establish whether the cooling plant removes the heat the "
        "equipment releases.",
        f"The hall carries {len(zones)} rack positions in {len(model['rows'])} "
        f"row segments and {len(model['fans'])} {export.naming['noun']} units, "
        f"against an "
        f"installed IT load of {_num(kpis['total_load_w'] / 1000, 0)} kW.",
    ])

    _heading(doc, "Objectives", 2)
    _bullets(doc, [
        "Establish the temperature of the air entering each rack, and check it "
        "against the ASHRAE class A1 recommended range of 18 °C to 27 °C.",
        f"Confirm that the air loop closes: that the {export.naming['plural']} "
        "move the mass they are given, that every wall holds and every intake "
        "draws forward, and "
        "reverses through an "
        "intake, and that the return air carries the installed load.",
        "Quantify the resistance the room presents to the units, against the "
        "external static pressure their datasheet offers.",
        "Size the plant against the design office's rules — installed capacity "
        "in kilowatts and installed airflow at the design CFM/kW — before any "
        "CFD result is quoted.",
    ])

    _heading(doc, f"Basis of design — {export.naming['noun']} selection point", 2)
    unit = export.equipment
    if unit:
        _para(doc,
              f"The cooling plant is {len(model['fans'])} × "
              f"{unit.family} {unit.model}. The quantities below are that "
              f"machine's own manufacturer selection, with the supply "
              f"temperature this run produced beside the one it was selected "
              f"at. Section 3 gives the "
              f"conditions it was taken at and the capacity the unit has "
              f"across the range of return air temperatures this hall "
              f"produces.",
              size=9.5, colour=SECOND)
    _table(doc, ["Quantity", "Value"], [
        ("Unit", f"{unit.family} {unit.model}".strip() if unit else "not named"),
        ("Units installed", f"{len(model['fans'])}"),
        ("Airflow per unit", f"{_num(kpis['supply_flow_m3h'] / max(1, len(model['fans'])), 0)} m³/h"),
        ("Total airflow to the room", f"{_num(kpis['supply_flow_m3h'], 0)} m³/h"),
        # TWO DIFFERENT TEMPERATURES, and this table used to print the solved
        # one under a heading that says "the machine's own manufacturer
        # selection" -- so section 2 said 23,2 degC and section 3's selection
        # table said 18,8 degC, of the same machine, on the same plant.
        *((("Supply air temperature, at the selection point",
            f"{_num((unit.design or {}).get('supply_c'), 1)} °C"),)
          if unit and (unit.design or {}).get("supply_c") is not None else ()),
        ("Supply air temperature delivered in this run",
         f"{_num(kpis['supply_temp_c'], 1)} °C"
         + (" (solved)"
            if unit and (unit.design or {}).get("supply_c") is not None else "")),
        ("Net sensible capacity per unit",
         f"{_num(fan.get('unit_capacity_kw'), 1)} kW"
         if fan.get("unit_capacity_kw") else "not given"),
        ("Electrical input per unit",
         f"{_num(fan.get('unit_power_kw'), 1)} kW"
         if fan.get("unit_power_kw") else "not given"),
        ("External static pressure, at the rated airflow",
         f"{_num(fan.get('fan_static_pa'), 0)} Pa"
         if fan.get("fan_static_pa") else "not given"),
        # A P-Q CURVE IS THE FAN AT FULL SPEED. A unit selected at 50 Pa with
        # its fans at 67 % has 604 Pa on its curve at the same airflow, and a
        # row that printed both without saying so read as a contradiction.
        ("Static pressure available at the modelled airflow, fans at full speed"
         if fan.get("fan_curve") else
         "Static pressure available at the modelled airflow",
         f"{_num(kpis.get('fan_static_pa'), 0)} Pa"
         + (" (interpolated on the unit's P-Q curve)"
            if fan.get("fan_curve") else " (the datasheet point)")
         if kpis.get("fan_static_pa") else "not given"),
        # TWO ELEVATIONS, and no label saying so: this said 0 m and the unit
        # table said "selected at 25 m", of the same plant. One is where the
        # hall is and fixes the air density the solve runs at; the other is
        # where the manufacturer's selection was taken.
        ("Site elevation, this hall",
         f"{_num(site.get('altitude_m'), 0)} m"
         + (f" (the selection was taken at "
            f"{_num((unit.selection or {}).get('elevation_m'), 0)} m)"
            if unit and (unit.selection or {}).get("elevation_m") is not None
            and abs((unit.selection or {}).get("elevation_m", 0)
                    - (site.get("altitude_m") or 0)) > 1 else "")),
        ("Operating pressure",
         f"{_num(site['pressure_pa'] / 1000, 1)} kPa" if "pressure_pa" in site else "—"),
        ("Supply air density", f"{_num(site.get('rho'), 3)} kg/m³"),
        *_selection_rows(unit),
    ], widths=[8.0, 8.0],
        note="The solve runs at the site's operating pressure, so the airflow and "
             "the mass flow agree with the unit's selection at that elevation."
             )

    _layout_section(doc, export, drawn)

    _heading(doc, "Results summary", 2)
    rows = [
        ("Warmest rack intake (top of rack)", f"{_num(warmest['inlet_top_c'], 2)} °C",
         f"rack {warmest['name']}, against the 27 °C ASHRAE recommended limit"),
        ("Mixed return air temperature", f"{_num(kpis['return_temp_c'], 2)} °C",
         f"{_num(kpis['bulk_delta_t_k'], 2)} K above the supply"),
        ("Energy closure", f"{_num((kpis['energy_closure'] or 0) * 100, 1)} %",
         "heat carried out by the return air, against the installed load"),
        # THE SAME NUMBER SECTION 4.1 AND SECTION 5 QUOTE. This row used to
        # carry the whole loop against the unit's external static, which reads
        # 227 % on a hall whose units are at 54 % -- a headline contradicting
        # the conclusion four pages later (ADR-115).
        ("Room resistance, most loaded unit",
         f"{_num(kpis.get('room_static_pa', kpis.get('fan_rise_pa')), 1)} Pa",
         f"of the {_num(kpis.get('fan_static_pa'), 0)} Pa external static the "
         + ("fans offer at full speed at this airflow" if fan.get("fan_curve")
            else "unit offers")
         + f"; the whole loop is {_num(kpis.get('fan_rise_pa'), 1)} Pa"
         if kpis.get("fan_static_pa") else "no datasheet pressure given"),
    ]
    if hvac:
        # A unit's capacity is optional in a spec; its airflow is not. Where
        # the datasheet capacity was not given the row says so rather than
        # printing a ratio computed from nothing.
        capacity = hvac.get("capacity_ratio")
        airflow = hvac.get("airflow_ratio")
        rows.append((
            "Plant against the design rules",
            (f"{_num(capacity * 100, 0)} % capacity"
             if capacity is not None else "capacity not given")
            + " · "
            + (f"{_num(airflow * 100, 0)} % airflow"
               if airflow is not None else "airflow not given"),
            "installed against the load in kW and against the racks' demand at "
            f"{_num(hvac.get('cfm_per_kw'), 0)} CFM/kW",
        ))
    _table(doc, ["Quantity", "Value", "Against what"], rows, widths=[6.0, 3.6, 6.4])

    stations = kpis.get("stations") or []
    if stations:
        _heading(doc, "The air loop, station by station", 3)
        _para(doc,
              "Each row is the whole stream crossing that surface: the "
              "temperature is the mixing cup, weighted by "
              "what each part of the surface carries, and the range is what "
              "the air at it actually spans. A room whose cabinets carry "
              "different loads has a different temperature at every point of "
              "a station, and the range is that spread.",
              size=9, colour=SECOND)
        _table(doc,
               ["Station", "Mixed", "Range", "Flow", "Face velocity"],
               [(f"{s['label']}",
                 f"{_num(s.get('temp_c'), 2)} °C",
                 # THE SAME PRECISION AS THE MIXED VALUE BESIDE IT. At one
                 # decimal the supply read "23,25 degC, range 23,2 - 23,2",
                 # a mean outside its own range.
                 (f"{_num(s.get('low_c'), 2)} – {_num(s.get('high_c'), 2)} °C"
                  if s.get("low_c") is not None else "—"),
                 (f"{s['flow_m3h']:,.0f} m³/h".replace(",", " ")
                  if s.get("flow_m3h") is not None else "—"),
                 (f"{_num(s.get('speed_ms'), 2)} m/s"
                  if s.get("speed_ms") is not None else "—"))
                for s in stations],
               widths=[4.0, 2.6, 3.6, 3.0, 2.8])

    verdict = (
        "All physical checks pass. The numbers below may be quoted."
        if export.payload["valid"]
        else "ONE OR MORE PHYSICAL CHECKS FAILED. The temperatures below are "
             "diagnostic material; section 4.1 names the check that failed."
    )
    _para(doc, verdict, bold=True, colour=INK if export.payload["valid"] else BAD)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9, colour="B07000")


def _selection_rows(unit) -> list[tuple[str, str]]:
    """The conditions a unit's table was selected at.

    They belong beside the capacity because they are what makes it true. The
    same coil at 12 °C entering water is a different machine as far as any
    number in this report is concerned -- and so is the same CRAC on a
    37,6 °C day (ADR-103).
    """
    if not unit or not unit.selection:
        return []
    labels = (
        ("entering_water_c", "Entering chilled water", "°C", 1),
        ("leaving_water_c", "Leaving chilled water", "°C", 1),
        ("outside_air_c", "Outdoor air at the condenser", "°C", 1),
        ("entering_air_rh", "Entering air relative humidity", "%", 0),
    )
    rows = [(label, f"{_num(unit.selection[key], digits)} {suffix}")
            for key, label, suffix, digits in labels
            if unit.selection.get(key) is not None]
    if unit.cooling == "dx":
        design = unit.design or {}
        if unit.selection.get("refrigerant"):
            rows.append(("Refrigerant", str(unit.selection["refrigerant"])))
        # WHAT THE MACHINE COSTS TO RUN. A chilled-water unit's compressor is
        # in a chiller somewhere else and this report cannot see it; a CRAC's
        # is inside the box, so the same table can say what the cooling costs.
        # Neither number reaches the air in the room (ADR-073).
        power = [design.get(k) for k in ("compressor_kw", "condenser_kw")]
        if design.get("compressor_kw"):
            rows.append((
                "Electrical input at the rated point",
                f"{_num(sum(v for v in power if v) + (design.get('power_kw') or 0), 1)} kW"
                f" — {_num(design.get('power_kw'), 1)} kW of fans, "
                f"{_num(design.get('compressor_kw'), 1)} kW of compressors"
                + (f", {_num(design.get('condenser_kw'), 1)} kW at the condenser"
                   if design.get("condenser_kw") else "")))
        if design.get("heat_rejection_kw"):
            rows.append(("Heat rejected outdoors",
                         f"{_num(design['heat_rejection_kw'], 1)} kW"))
    return rows


# --- 2 methodology ------------------------------------------------------------


def _methodology(doc, export: Export, drawn: dict) -> None:
    model = export.model
    kpis = export.kpis
    spec = model.get("spec") or {}
    galleries = model.get("galleries") or [model["gallery"]]
    blocks = model.get("blocks") or []

    _heading(doc, "3  Methodology", 1)
    _heading(doc, "Geometry", 2)
    arrangement = (
        f"a mechanical gallery at each end of the hall, "
        f"{len(model['fans'])} units shared between them"
        if len(galleries) > 1
        else "a single mechanical gallery along one side"
    )
    raised = model.get("floor_height")
    # WHICH AISLE IS CONTAINED, read off what was built rather than assumed.
    # Every report said "hot-aisle containment", including this hall's, whose
    # cold aisles are the contained ones -- the arrangement the whole air loop
    # follows from (ADR-100, ADR-112).
    which = _containment_of(export)
    contained = f"{which}-aisle containment" if which else "no containment"
    _para(doc,
          f"The room is derived from the case specification: every "
          f"dimension below follows from the equipment sizes, the aisle widths "
          f"and the clearances the engineer typed. The arrangement is "
          f"{arrangement}, with {contained} and a ceiling-plenum "
          f"return.")
    if raised:
        # The supply side is the half that differs, and a reader checking a
        # drawing against this document needs to be told which one (ADR-076).
        _para(doc,
              f"The room stands on an access floor {raised:.2f} m deep, and "
              f"the space under it is the supply plenum. The units are "
              f"downflow: each draws through its top face, from the mechanical "
              f"gallery, and discharges through its bottom face into the "
              f"plenum below the deck. The air crosses into the hall through a "
              f"woven mesh in the dividing wall below the floor -- the same "
              f"opening, in the same wall, as the one above the false ceiling "
              f"that carries the return -- and reaches the cold aisle through "
              f"perforated plates set in the floor in front of the cabinets. "
              f"Every dimension of the room above the deck is unchanged by "
              f"this; the building is taller by the depth of the floor.")
        _para(doc,
              "The plates are what balances one aisle against another, so they "
              "are reported with their measured pressure drop and their face "
              "velocity beside the drop their datasheet asks for. No limit is "
              "put on that velocity: what is high for one hall is ordinary in "
              "another, and the reader knows which they have.")
    if not raised:
        _para(doc,
              f"The units stand in the mechanical galler"
              f"{'ies' if len(galleries) > 1 else 'y'} and blow through the "
              f"dividing wall into the room, so the room itself is the cold "
              f"side: the air crosses the woven mesh in the wall, fills the "
              f"space between the rows, enters the cabinets through their "
              f"fronts and leaves them into the "
              + ("contained hot aisles, which discharge through the ceiling "
                 "grilles into the return plenum." if _containment_of(export) == "hot"
                 else "hot aisles and rises to the ceiling grilles."))
    if len(galleries) > 1:
        _para(doc,
              "The false ceiling covers the whole hall and stops at each "
              "dividing wall, so the return plenum above it is one continuous "
              "volume: it collects from every hot aisle and opens into both "
              "galleries. Two supplies, one return — which is what allows a "
              "block whose own gallery is short of capacity to be drawn on by "
              "the other.")
    rows = [
        ("Hall, internal", f"{_num(model['hall']['hi'][0] - model['hall']['lo'][0])} × "
                           f"{_num(model['domain']['hi'][1])} m"),
        ("Floor to slab", f"{_num(model['domain']['hi'][2])} m"),
        ("False ceiling", f"{_num(model['ceiling_z'])} m"),
        ("Mechanical galleries", f"{len(galleries)} × "
                                 f"{_num(galleries[0]['hi'][0] - galleries[0]['lo'][0])} m deep"),
        ("Rack rows", f"{len(model['rows'])} row segments"
                      + (f" — {len(model['rows']) // max(1, len(blocks))} rows "
                         f"in {len(blocks)} blocks" if len(blocks) > 1 else "")),
        ("Rack positions", f"{len(export.racks)}"),
        *(_aisle_rows(export, model, blocks)),
        *([("Customer cage",
            f"{model['cage']} around {len(model.get('cage_racks') or [])} "
            f"cabinets"
            + (f", K = {_num(model.get('cage_k'), 2)} each way"
               if model.get("cage_k") else ""))]
          if model.get("cage") else []),
        (f"{as_a_label(export.naming['noun'])} units", f"{len(model['fans'])}"),
    ]
    _table(doc, ["Feature", "As built in the model"], rows, widths=[7.0, 9.0])

    _heading(doc, "Computational mesh", 2)
    cell = model["cell_size"]
    _para(doc,
          f"A single structured hexahedral block of {kpis['cells']:,} cells, "
          f"{_num(cell[0], 2)} × {_num(cell[1], 2)} × {_num(cell[2], 2)} m, with "
          f"every internal surface cut into it afterwards as a two-sided baffle: "
          f"the containment panels, the false ceiling, the row ends and tops, the "
          f"return grilles and the {export.naming['plural']}. The room's air "
          f"loop closes inside "
          f"the box, so every one of those surfaces is internal to the domain.")
    across, through, tall = _cells_per_rack(export)
    _para(doc,
          "The cell sizes differ by axis on purpose. In plan the mesh is sized "
          f"on the cabinet: {_cells(across)} across a rack's face and "
          f"{_num(through, 0)} through its depth, which is what decides how well "
          "the porous zone reproduces its own pressure curve. In the vertical "
          f"every plane — the false ceiling, the {export.naming['noun']} top, "
          f"the rack tops ({_cells(tall)} up a cabinet) — is put on a cell "
          "face, because a plane that falls mid-cell produces a ragged surface "
          "that leaks silently; where that moved a plane, the list below says "
          "by how much.")
    _para(doc, _resolution_verdict(across, through), bold=True)
    if model.get("warnings"):
        _para(doc, "Dimensions the mesh snapped to its nearest cell face:",
              size=9, colour=SECOND, space_after=2)
        _bullets(doc, _grouped_warnings(model["warnings"]))

    _heading(doc, "Boundary conditions and models", 2)
    _table(doc, ["Feature", "How it is modelled"], [
        (as_a_label(export.naming["noun"]),
         "A pair of patches on the same internal faces: air leaves the "
         "gallery through the intake and re-enters the cold aisle "
         "through the supply. Both are set by MASS flow — "
         "the air leaving is warmer and thinner than the air arriving, "
         "and in a closed loop a 1 % mismatch has nowhere to go."),
        ("Rack", "A Darcy–Forchheimer cell zone with a volumetric enthalpy "
                 "source. The momentum sink along the airflow axis is "
                 "S = −(µ·d·u + ½·ρ·f·|u|·u); d is set to a nominal value and "
                 "the whole of the cabinet's rated drop is carried by the "
                 "inertial term, f = 2·Δp / (ρ·u_rated²·L), so the resistance "
                 "scales as the square of the velocity as a perforated door "
                 "does. Across the other two axes the coefficients are raised "
                 "by orders of magnitude, which is the cabinet's side panels "
                 "and top. The rack has no fan: what passes through it is an "
                 "outcome of the room's pressure field, so the temperature rise "
                 "across a rack is a result of the solve."),
        ("Blanking panel", "A solid, adiabatic wall on the cabinets' own face, "
                           "closing the position across the full height of the "
                           "row. No air crosses it. The volume behind it is left "
                           "open to the contained aisle, which is what the space "
                           "behind a blanking plate is; the sealed-envelope "
                           "check confirms it carries zero flow."),
        ("Cabinet at zero load", "The same porous "
                                 "zone with the same resistance as its "
                                 "neighbours and no heat source: an empty "
                                 "cabinet still breathes and still costs the fan "
                                 "what the row costs. A blanking panel is the row "
                                 "above; the case says which each position is."),
        ("Perforated surface", "Every grille, mesh and plate is a cyclic pair on "
                               "the same internal faces, carrying a pressure "
                               "jump Δp = ½·K·ρ·u_n², where u_n is the velocity "
                               "normal to the face and K comes from the "
                               "component's datasheet — or, where the datasheet "
                               "gives none, from its free area by Idelchik's "
                               "thin-plate relation. The jump is imposed face by "
                               "face, so a surface the air reaches unevenly "
                               "costs more than its rated face velocity says, "
                               "and section 4.1 measures that against the K it "
                               "was given."),
        ("Supply plenum", "Where the case has one: the wall into the hall is "
                          "doubled, the units stay in the outer leaf, and the "
                          "cavity between the leaves is pressurised. The inner "
                          "leaf carries the supply grilles, as cyclic pairs like "
                          "the return ones. The hall is longer by the depth of "
                          "the cavity, so the clearances are unchanged. Without "
                          "a plenum the unit blows through a single wall into "
                          "the aisle it faces."),
        ("Supply mesh", "The alternative for a hall already built: the same "
                        "13 x 13 mm woven mesh as on the return, across the "
                        "opening the units blow through, and no plenum. It is "
                        "a uniform resistance in series with the units, so it "
                        "leaves the flow unchanged and enters the model as "
                        "pressure added to the fan duty from its loss "
                        "coefficient, and the check says so."),
        ("Containment, false ceiling, row ends", "Two-sided adiabatic wall "
                                                 "baffles. Closing a row on "
                                                 "five sides is what keeps its air in the aisle; open on any side it leaks most of its "
                                                 "air sideways."),
        ("Building envelope", "No-slip, adiabatic. Conservative for sizing the "
                              "plant: no heat is assumed to leave through it."),
        ("Turbulence", "Steady RANS, k-epsilon, with buoyancy "
                       "(buoyantSimpleFoam, OpenFOAM v1912)."),
    ], widths=[4.6, 11.4])

    _surfaces_section(doc, export)
    _control_section(doc, export)
    _unit_section(doc, export, drawn)

    _heading(doc, "How the airflow per unit is obtained", 2)
    # THE STATED AIRFLOW, not the measured one divided back: a table whose
    # step 3 is step 1 times step 2 has to multiply out, and 14 x 31.550 is
    # 441.700, not the 441.699 the field's mass flow rounds to.
    units = max(1, len(model["fans"]))
    per_unit = ((model.get("operating") or {}).get("unit_airflow_m3h")
                or kpis["supply_flow_m3h"] / units)
    selected = ((export.equipment.design or {}).get("airflow_m3h")
                if export.equipment else None)
    # BY VOLUME HERE, BY MASS IN THE COIL. The coil sees capacity rate, and
    # this hall's air at sea level is denser than the sheet's at altitude, so
    # 54 % of the selection's m3/h is 61 % of its kg/s. Both are true and a
    # reader meeting the two numbers unlabelled has found a contradiction.
    mass_share = kpis.get("coil_air_share_pct")
    step_one = ("1 — the unit's airflow, from the datasheet"
                if not selected or abs(per_unit - selected) < 1
                else f"1 — the unit's airflow as operated, "
                     f"{per_unit / selected * 100:.0f} % by volume of the "
                     f"{_num(selected, 0)} m³/h it was selected at"
                     + (f" ({mass_share:.0f} % by mass: this hall's air is "
                        f"denser than at the selection's altitude)"
                        if mass_share is not None
                        and abs(mass_share - per_unit / selected * 100) > 1
                        else ""))
    _table(doc, ["Step", "Value"], [
        (step_one, f"{_num(per_unit, 0)} m³/h"),
        ("2 — units installed", f"{units}"),
        ("3 — total delivered to the room", f"{_num(per_unit * units, 0)} m³/h"),
        ("4 — at the supply density",
         f"{_num((model.get('site') or {}).get('rho'), 3)} kg/m³"),
        ("5 — mass flow imposed at each unit",
         f"{_num(per_unit / 3600 * (model.get('site') or {}).get('rho'), 2)} kg/s"
         if (model.get("site") or {}).get("rho") else "—"),
    ], widths=[9.0, 7.0],
        note="Every unit is given the same duty. Section 6 covers a plant "
             "whose units sit on a pressure boundary and share the flow "
             "unevenly.")


def _layout_section(doc, export: Export, drawn: dict) -> None:
    """What is in the room and where, before any result is quoted.

    The plant is only half a basis of design. A reader checking this study
    against a layout drawing needs the room dimensioned and the cabinets named
    -- which position carries what -- because that is what decides where the
    heat is and how evenly the units load (ADR-054).
    """
    model = export.payload["model"]
    racks = export.racks
    blanks = [p for p in model.get("panels", []) if p["name"].startswith("blank")]
    loads = sorted({round(r.get("load_w", 0) / 1000, 2) for r in racks})
    zero = sum(1 for r in racks if not r.get("load_w"))
    widths = sorted({round(r["hi"][0] - r["lo"][0], 2) for r in racks})
    rows = model.get("rows") or []
    per_row = len(rows[0]["racks"]) if rows else 0

    _heading(doc, "Basis of design — rack distribution", 2)
    _para(doc,
          f"{len(racks)} cabinet positions stand in {len(rows)} rows of "
          f"{per_row}"
          + (f", in {len(model.get('blocks') or [])} blocks along the row"
             if len(model.get("blocks") or []) > 1 else "")
          + f". Each row is built from one typical row, so every row of the "
          f"hall carries the same pattern of widths and loads; a position that "
          f"disagrees with it says so on its own.",
          size=9.5, colour=SECOND)
    _cage_split(doc, export)
    _table(doc, ["What stands in the row", "How many", "Detail"], [
        ("Cabinets carrying load", f"{len(racks) - zero}",
         "loads of " + ", ".join(f"{v:g} kW" for v in loads if v) + " installed"
         if any(loads) else "—"),
        ("Cabinets at zero load", f"{zero}",
         "in the model as cabinets: the same resistance as their neighbours, "
         "no heat source. An empty cabinet still breathes and still costs the "
         "fan what the row costs."),
        ("Blanking panels", f"{len(blanks)}",
         "solid plates across the row. No air crosses them, which the "
         "sealed-envelope check confirms."
         if blanks else "none in this layout"),
        ("Cabinet widths", f"{len(widths)}", _widths_as_specified(model, widths)),
    ], widths=[5.0, 2.4, 8.6])

    if drawn.get("geo_hall"):
        _figure(doc, drawn["geo_hall"],
                "The data hall in plan, at rack height: every row and every "
                "cabinet, the customer cage where the hall has one, and the "
                f"{export.naming['plural']} in their galleries. The same "
                "drawing the model page shows, to scale.")


def _cage_verdict(doc, zones: list[dict], caged: set) -> None:
    """The warmest cabinet on each side of the fence.

    The cage is the customer's room and the rest is everybody else's. They are
    judged separately because they are let separately, and a hall-wide worst
    case says nothing about which of the two is in trouble (ADR-102).
    """
    if not caged:
        return
    inside = [z for z in zones if z["name"] in caged and z.get("inlet_top_c")]
    outside = [z for z in zones if z["name"] not in caged and z.get("inlet_top_c")]
    if not inside or not outside:
        return
    worst_in = max(inside, key=lambda z: z["inlet_top_c"])
    worst_out = max(outside, key=lambda z: z["inlet_top_c"])
    over = sum(1 for z in inside
               if (z.get("ashrae") or {}).get("verdict", "").startswith("above"))
    _para(doc,
          f"Inside the customer cage the warmest intake is "
          f"{_num(worst_in['inlet_top_c'], 1)} °C, at {worst_in['name']}"
          + (f" — {over} of the {len(inside)} cabinets in the cage are above "
             f"the ASHRAE recommended envelope"
             if over else f", and all {len(inside)} cabinets in the cage are "
                          f"inside the ASHRAE recommended envelope")
          + f". In the rest of the hall it is "
            f"{_num(worst_out['inlet_top_c'], 1)} °C, at {worst_out['name']}.",
          size=9.5)


def _cage_split(doc, export: Export) -> None:
    """What is inside the customer cage and what is in the rest of the hall.

    A hall with a cage is TWO rooms: one customer's, at their density, and
    everybody else's. Every number a reader wants -- how many cabinets, how
    much load, how much per cabinet -- is different on the two sides of that
    fence, and a table that totals the hall says neither (ADR-102). Absent,
    with no extra words, where the hall has no cage.
    """
    caged = [r for r in export.racks if r.get("in_cage")]
    if not caged:
        return
    outside = [r for r in export.racks if not r.get("in_cage")]
    construction = export.model.get("cage") or "mesh"
    k = export.model.get("cage_k")

    def density(racks) -> str:
        loaded = [r for r in racks if r.get("load_w")]
        if not loaded:
            return "—"
        kw = sum(r["load_w"] for r in loaded) / 1000
        return (f"{_num(kw / len(loaded), 2)} kW per loaded cabinet"
                + (f", {len(racks) - len(loaded)} carrying nothing"
                   if len(racks) - len(loaded) else ""))

    _heading(doc, "Basis of design — the customer cage and the rest of the hall", 2)
    _para(doc,
          f"One customer's rows are fenced off inside the hall, in "
          f"{construction}"
          + (f" of loss coefficient K = {_num(k, 2)}" if k and construction == "mesh"
             else "")
          + ". The two sides are different rooms and this study reports them "
            "as such: the cage is drawn on every plan, the cabinets inside it "
            "are named in the detail figure, and section 4 ranks every "
            "cabinet of both.",
          size=9.5, colour=SECOND)
    _table(doc, ["", "Positions", "Installed load", "Density"], [
        ("Inside the cage", f"{len(caged)}",
         f"{_num(sum(r['load_w'] for r in caged) / 1000, 1)} kW", density(caged)),
        ("Rest of the data hall", f"{len(outside)}",
         f"{_num(sum(r['load_w'] for r in outside) / 1000, 1)} kW",
         density(outside)),
        ("The room", f"{len(export.racks)}",
         f"{_num(sum(r['load_w'] for r in export.racks) / 1000, 1)} kW",
         density(export.racks)),
    ], widths=[4.6, 2.4, 3.4, 5.6],
        note="A cabinet carrying nothing is still in the model: it stands in "
             "the row, it has the resistance of its neighbours and the air "
             "still has to get round it.")


def _surfaces_section(doc, export: Export) -> None:
    """Every perforated surface this case uses, with the number that decides it.

    One table, because K is the whole of the physics: the jump each of these
    imposes is ½·K·ρ·u², and a reader checking the plant against a catalogue
    needs to see which component each surface is and what it costs -- not be
    told that grilles exist.
    """
    model = export.payload["model"]
    roles = model.get("components") or []
    # ONLY THE SURFACES THIS ROOM HAS. The library marks a component
    # `applied` when a case may use it, and a fan-wall hall with no floor and
    # no plenum listed "Raised floor plates" and "Plenum supply grilles" in a
    # table headed "in the model".
    built = {
        "floor_tile": bool(model.get("floor_height")),
        "supply_grille": bool(model.get("plenum_depth_m")),
        "cage": bool(model.get("cage")),
    }
    surfaces = [r for r in roles if r.get("kind") != "load" and r.get("applied")
                and built.get(r.get("role"), True)]
    if not surfaces:
        return
    _heading(doc, "Perforated surfaces in the model", 2)
    _table(doc, ["Where", "Component", "Free area", "K", "Face velocity"], [
        (role.get("label", role.get("role", "")),
         next((o["name"] for o in role.get("options", [])
               if o["id"] == role.get("chosen")), role.get("chosen", "—")),
         f"{_num((role.get('free_area') or 0) * 100, 0)} %",
         _num(role.get("k"), 2),
         _face_velocity(export, role.get("role")))
        for role in surfaces
    ], widths=[4.0, 5.0, 2.2, 2.0, 2.8],
        note="K refers to the velocity through the GROSS face, which is how the "
             "model applies it and how section 4.1 measures it. A component "
             "whose datasheet gives a loss coefficient uses it; otherwise K "
             "follows from the free area.")


#: Which measured face velocity belongs to which role, where the result has one.
_ROLE_VELOCITY = {
    "supply_grille": "supply_face_velocity_ms",
    "floor_tile": "floor_face_velocity_ms",
}


def _face_velocity(export: Export, role: str | None) -> str:
    key = _ROLE_VELOCITY.get(role or "")
    value = export.kpis.get(key) if key else None
    if value is None and role == "ceiling_return":
        # The aisle-exit station IS the ceiling grilles: the same face,
        # measured the same way.
        value = next((s.get("speed_ms") for s in export.kpis.get("stations") or []
                      if s.get("name") == "aisle_exit" or s.get("label") == "Aisle exit"),
                     None)
    return f"{_num(value, 2)} m/s" if value is not None else "—"


def _control_section(doc, export: Export) -> None:
    """How the units decide what to deliver, and what that means for the result.

    A plant of eight units is not one unit eight times. Whether they run to
    their own return or to the worst return any of them sees changes which unit
    saturates first, and a report that shows per-unit capacity has to say which
    of the two produced it (ADR-064).
    """
    model = export.payload["model"]
    units = len(model.get("fans") or [])
    if units < 2:
        return
    team = str(model.get("fan_control") or "independent").strip().lower() == "team"
    # WHAT A NETWORKED PLANT SHARES IS NOT THE SAME THING IN THE TWO MACHINES
    # (ADR-117). A chilled-water plant shares the VALVE POSITION: the unit
    # with the hardest job sets how far open every valve is, and each unit
    # then delivers what its own coil gives at its own return -- so they do
    # NOT deliver the same supply temperature. A direct-expansion plant
    # shares the SETPOINT: every unit is controlled to the supply the worst
    # one can make, so they do. This paragraph said the second of every
    # plant, which on a CRAH or a fan-wall hall described a control that was
    # not running.
    cold = export.kpis.get("coil_model") or {}
    dx = cold.get("kind") == "dx"
    noun = as_a_label(export.naming["noun"])
    _heading(doc, "How the units are controlled", 2)
    if team and dx:
        how = (f"The {units} units are NETWORKED on one supply air setpoint: "
               f"every unit holds the same {_num(export.kpis.get('coil_supply_setpoint_c'), 1)} °C "
               f"with its own compressors, and a unit whose return is too warm "
               f"for its compressors delivers the coldest air it can, so its "
               f"supply follows its return while the rest hold the setpoint. "
               f"In a steady field that is also what each unit does on its "
               f"own; the network's work — staging and fan coordination — is "
               f"dynamic, so `fanwall.control: team` and `independent` give "
               f"the same steady answer for a supply-controlled {noun} plant.")
    elif team:
        how = (f"The {units} units are NETWORKED: they run as one plant. Each "
               f"pass of the coupled loop finds the warmest return any unit "
               f"sees, opens the water valve as far as THAT unit needs, and "
               f"gives every unit the same valve position. Each unit then "
               f"delivers what its own coil gives at its own return — so a "
               f"unit fed cooler air delivers cooler air, each at its own supply "
               f"temperature; what they share is the water. "
               f"This is what a real BMS does with a chilled-water plant, and "
               f"it is what `fanwall.control: team` in the case asks for.")
    else:
        how = (f"The {units} units run INDEPENDENTLY: each controls to the "
               f"return air reaching its own intake, so a unit fed warmer air "
               f"works harder and each delivers its own supply "
               f"temperature. Set `fanwall.control: team` to run them as one "
               f"networked plant instead.")
    _para(doc, how)
    # WHAT THE CONTROL MOVES is not the same machinery in the two plants,
    # and naming the water side of a direct-expansion unit describes a pipe
    # that is not there (ADR-111).
    side = ("how hard the compressors work"
            if cold.get("kind") == "dx" else "the water side")
    _para(doc,
          f"Either way every unit is given the same MASS flow. What the "
          f"control changes is {side} — how much each coil is asked to "
          f"transfer — and the air each unit moves stays the same.",
          size=9, colour=SECOND)


def _model_limits(export: Export) -> list[str]:
    """The ways THIS run's model departs from the room, asked of the model.

    Each entry is conditional on what the case actually uses, so a limitation
    is printed when it applies and is absent when it does not -- which is the
    only way a list like this stays true as the tool grows (ADR-089).
    """
    from aicfd import components as library

    limits = []
    for component_id, sentence in (
        ("pdu-distribution-loss",
         "Heat released outside the racks — PDU and other ancillary losses, "
         "typically about 2 % of the IT load — lies outside the model."),
        ("containment-panel",
         "Containment is modelled as perfect: the panels are solid walls in the "
         "mesh, sealed, and their leakage figure stays unused. Real containment leaks, "
         "and the leak is what decides the top-of-rack temperature in a "
         "marginal design."),
    ):
        try:
            if not library.load(component_id).applied:
                limits.append(sentence)
        except Exception:  # noqa: BLE001 -- a library a case does not use
            continue
    model = export.payload["model"]
    if not model.get("floor_height"):
        limits.append(
            "Cable management, containment framing and anything else that "
            "obstructs an aisle lies outside the geometry, which is the "
            "cabinets, the aisles, the containment and the plant."
        )
    # WHAT A DIRECT-EXPANSION PLANT STILL DOES NOT ANSWER. Its evaporator is
    # modelled (ADR-103); its CONDENSER is not, and the difference belongs
    # here, once, rather than in the alerts card on every run (ADR-098).
    kpis = export.payload.get("kpis") or {}
    coil = kpis.get("coil_model") or {}
    if coil.get("kind") == "dx":
        ambient = coil.get("rated_ambient_c")
        limits.append(
            f"{kpis.get('unit_model')} is a direct-expansion unit and its "
            f"EVAPORATOR is modelled: the capacities here are its coil at the "
            f"air each unit received, measured from the "
            f"{_num(coil.get('adp_c'), 1)} °C coil surface its selection "
            f"implies. Its CONDENSING side enters through one number: capacity follows the "
            f"outdoor air the condenser rejects into, and this result holds "
            f"that at the selection's own"
            + (f" {_num(ambient, 1)} °C" if ambient is not None else " value")
            + " — a hotter day gives less, a cooler one more, and one "
            "selection cannot say how much. The compressors are taken as "
            "modulating continuously; a staged machine cycles about this."
        )
        for assumption in coil.get("assumptions") or []:
            limits.append(
                f"The coil fit for {kpis.get('unit_model')} assumed that "
                f"{assumption}."
            )
    elif coil.get("kind") == "chilled_water":
        # THE SAME SENTENCE THE DX PLANT GETS, for the half of a chilled-water
        # plant this study cannot see. The condenser's counterpart is the
        # chiller: the coil answers at any return, and it does so on water
        # held at the selection's entering temperature and flow, which is a
        # plant somewhere else that this report assumes can deliver them.
        limits.append(
            f"{kpis.get('unit_model')} is a chilled-water unit and its COIL "
            f"is modelled: the capacities here are that coil at the air each "
            f"unit received, on water entering at "
            f"{_num(coil.get('water_c'), 1)} °C"
            + (f" and at most {_num(coil.get('water_max_m3h'), 1)} m³/h per "
               f"unit" if coil.get("water_max_m3h") else "")
            + ". The CHILLED-WATER PLANT enters through those two numbers: the chiller, the pumps and "
            "the distribution are taken to hold that water whatever the "
            "coils draw, and a unit past its selection asks the water to "
            "leave warmer than the plant was sized for — section 5 says "
            "where that happens. The valve is taken as modulating "
            "continuously."
        )
    elif kpis.get("rated_return_c") and kpis.get("coil_problem"):
        limits.append(
            f"{kpis.get('unit_model')} is carried at its plate figure: "
            f"{kpis.get('coil_problem')}. So this result is the room at the "
            f"unit's RATED point — {_num(kpis.get('rated_nscc_kw'), 1)} kW at "
            f"{_num(kpis.get('rated_return_c'), 1)} °C return — with the "
            f"supply temperature held there, and the capacity quoted in this "
            f"report is that plate figure."
        )
    return limits


def _cells(n: float) -> str:
    """`1 cell`, `6 cells`. A report that says "1 cells" has been generated
    rather than written, and a reader can tell."""
    return f"{_num(n, 0)} cell" + ("" if round(n) == 1 else "s")


def _cells_per_rack(export: Export) -> tuple[float, float, float]:
    """How many cells a cabinet is, per axis: across its face, through its
    depth, and up it.

    Computed rather than asserted. The report used to say "in plan the cell is
    one rack wide" and quote a fixed 1 to 2 K of uncertainty, which was written
    for one mesh and printed for every other: a hall run at 0,10 x 0,20 m has
    SIX cells across a 0,6 m cabinet and six through its depth, and the
    sentence was telling its reader the opposite (ADR-089).
    """
    cell = export.payload["model"]["cell_size"]
    racks = export.racks
    if not racks:
        return (0.0, 0.0, 0.0)
    lo, hi = racks[0]["lo"], racks[0]["hi"]
    return tuple((hi[a] - lo[a]) / cell[a] for a in range(3))  # type: ignore[return-value]


#: What a plan resolution is good for. The bands are the ones the worked
#: comparisons in `docs/experiments` support: a coarse-against-fine run on the
#: POD agreed within 0,2 K and 3% at three cells across a cabinet, and the
#: hall-scale fields agree well below that while a single intake does not.
def _resolution_verdict(across: float, through: float) -> str:
    plan = min(across, through)
    if plan < 2:
        return ("At this resolution a cabinet is a single cell in plan. The "
                "ranking of racks and the hall-scale pressure field are "
                "supported; a single rack's intake carries 1 to 2 K of "
                "uncertainty.")
    if plan < 4:
        return ("At this resolution the flow around a cabinet is resolved well "
                "enough for the ranking of racks, the aisle-to-aisle "
                "temperatures and the hall-scale pressure field. A single "
                "rack's intake carries roughly 1 K of uncertainty.")
    return ("At this resolution the cabinet and the aisle around it are "
            "resolved, so a single rack's intake is supported as well as the "
            "ranking and the hall-scale fields. What bounds this result is the "
            "modelling, and the limitations in section 6 say where.")


def _unit_section(doc, export: Export, drawn: dict) -> None:
    """The machine the plant is made of, and what it can actually do.

    A fan wall's datasheet prints one capacity, true at the one return air
    temperature the unit was selected for. A room almost never returns air at
    that temperature, and a chilled-water coil transfers more when the air
    reaching it is warmer. Judging a plant against the catalogue figure
    therefore judges it against something the plant will not do, so this
    report judges it against the coil that selection characterises, at the
    return air each unit was found to receive (ADR-036, ADR-042).
    """
    unit = export.equipment
    if unit is None:
        return
    _heading(doc, "The cooling unit", 2)
    facts = [
        ("Manufacturer and family", unit.family or "—"),
        ("Model", unit.model),
        ("Units installed", f"{len(export.model['fans'])}"),
        ("Unit dimensions (w × d × h)",
         " × ".join(f"{v:.2f}" for v in unit.size) + " m"),
    ]
    if unit.weight_kg:
        facts.append(("Operating weight", f"{_num(unit.weight_kg, 0)} kg"))
    fans = unit.fans or {}
    if fans.get("count"):
        facts.append(("Fans per unit",
                      f"{fans['count']} × {fans.get('type', 'fan')}"))
    if fans.get("module"):
        facts.append(("Fan module", str(fans["module"])))
    if fans.get("modulation") is not None:
        facts.append(("Fan modulation at the selection",
                      f"{_num(fans['modulation'], 1)} %"))
    if unit.selection.get("elevation_m") is not None:
        facts.append(("Selected at site elevation",
                      f"{_num(unit.selection['elevation_m'], 0)} m"))
    if unit.selection.get("esp_pa") is not None:
        facts.append(("Selected at external static pressure",
                      f"{_num(unit.selection['esp_pa'], 0)} Pa"))
    facts.extend(_selection_rows(unit))
    _table(doc, ["Quantity", "Value"], facts, widths=[8.0, 8.0])

    design = unit.design or {}
    if design.get("return_c") is not None:
        _heading(doc, "The design selection", 2)
        _para(doc,
              "The duty this plant was bought on, as the manufacturer issued "
              "it. Every capacity in section 4 is quoted at the return "
              "temperature each unit was found to receive and at the air flow "
              "it was found to be moving, from the coil this selection "
              "characterises.",
              size=9.5)
        _table(doc,
               ["Return air", "Net sensible", "Airflow", "Power input",
                "Supply air"],
               [(f"{_num(design['return_c'], 1)} °C",
                 f"{_num(design['nscc_kw'], 1)} kW",
                 f"{_num(design['airflow_m3h'], 0)} m³/h",
                 f"{_num(design.get('power_kw'), 1)} kW",
                 f"{_num(design['supply_c'], 1)} °C")],
               widths=[3.2, 3.2, 3.4, 3.2, 3.0])
    if drawn.get("capacity"):
        _figure(doc, drawn["capacity"],
                "Net sensible capacity against the air the unit receives. The "
                "solid line is the coil at the air flow it was selected for, "
                "with the design selection on it; the dashed line is the same "
                "coil at the air flow this hall gives it, with each unit "
                "marked where it ran.")
    _coil_section(doc, export)
    if not (unit.curve or {}).get("measured"):
        _para(doc,
              "The pressure–flow curve used to find the static pressure "
              "available at the modelled airflow is representative of an EC "
              "fan array at full speed, anchored to the one point the unit's "
              "sheet gives. It decides the uncontrolled operating point alone; "
              "every capacity and temperature in this report is independent "
              "of it.",
              size=9, colour=MUTED, italic=True)


def _coil_section(doc, export: Export) -> None:
    """How a capacity at an unselected condition was obtained.

    Every margin in section 5 rests on this, so the method belongs in the
    document rather than inside the software. A reader who disagrees with it
    can see what was assumed and check the residual themselves.
    """
    coil = export.kpis.get("coil_model") or {}
    if not coil:
        return
    share = export.kpis.get("coil_air_share_pct")
    if coil.get("kind") == "dx":
        _para(doc,
              "The design selection above characterises the unit's "
              "EVAPORATOR. A direct-expansion coil is the same heat exchanger "
              "as the chilled-water one section 1.4 sets out, with one side "
              "boiling: the refrigerant holds its temperature while it "
              "changes phase, so the coil is measured from the surface "
              "temperature — the apparatus dew point — that its own split "
              "between sensible and total capacity implies. That coil gives "
              "this unit's capacity at every condition this hall produced; "
              "the compressors modulate to hold the supply temperature until "
              "the compressors are at full duty.",
              size=9.5)
        rows = [
            ("Design return air",
             f"{_num(coil.get('design_return_c'), 1)} °C"),
            ("Coil surface (apparatus dew point)",
             f"{_num(coil.get('adp_c'), 1)} °C"),
            ("Air reaching the surface (contact factor)",
             f"{coil.get('contact_factor_pct')} %"),
            ("Outdoor air the capacity is held at",
             f"{_num(coil.get('rated_ambient_c'), 1)} °C"
             if coil.get("rated_ambient_c") is not None else "not in this export"),
            ("Air flow in this hall, against the design selection",
             f"{share} %" if share else "—"),
        ]
        _table(doc, ["Property of the evaporator", "Value"], rows,
               widths=[8.0, 8.0],
               note="Capacity is held at the outdoor air above, which is the "
                    "whole of the condensing side in this model. Section 6 says what that means.")
        for assumption in coil.get("assumptions") or []:
            _para(doc, f"Assumed, because the selection does not print it: "
                       f"{assumption}.", size=9, colour=MUTED, italic=True)
        return
    _para(doc,
          "The design selection above characterises the counterflow coil that "
          "section 1.4 sets out, and that coil gives this unit's capacity at "
          "every condition this hall produced. Its properties follow.",
          size=9.5)
    # `.get`, not `[...]`. An export written by an earlier version of the
    # tool carries an earlier coil payload, and a report that raises on a
    # field it does not find cannot be emitted for a result somebody already
    # has -- which is the one thing a report generator must never do. A
    # missing figure says "not in this export", visibly (ADR-089).
    split = coil.get("air_split_pct")
    rows = [
        ("Design return air",
         f"{_num(coil['design_return_c'], 1)} °C"
         if coil.get("design_return_c") is not None else "not in this export"),
        # THE SAME TWO ROWS THE DX TABLE HAS for its condenser: what the
        # capacity is held at, and what the plant is assumed to deliver.
        ("Entering water the capacity is held at",
         f"{_num(coil.get('water_c'), 1)} °C"
         if coil.get("water_c") is not None else "not in this export"),
        ("Water flow at full valve",
         f"{_num(coil.get('water_max_m3h'), 1)} m³/h per unit"
         if coil.get("water_max_m3h") else "not in this export"),
        ("Resistance on the air side",
         f"{split} % (water side {100 - split} %)"
         if split is not None else "not in this export"),
        ("Air flow in this hall, against the design selection",
         f"{share} %" if share else "—"),
    ]
    _table(doc, ["Property of the coil", "Value"], rows, widths=[8.0, 8.0],
           note="The water is held at the entering temperature above and the "
                "valve modulates the flow up to the figure given, which is the "
                "whole of the chilled-water plant in this model. The resistance split is the "
                "usual one for a finned coil of this kind, and it is an input "
                "where the manufacturer states the unit's own. Section 6 says "
                "what that means.")


# --- 3 results ----------------------------------------------------------------


def _results(doc, export: Export, drawn: dict) -> None:
    kpis = export.kpis
    model = export.model
    zones = sorted(kpis["zones"], key=lambda z: -(z["inlet_top_c"] or -999))

    _heading(doc, "4  Results", 1)

    _heading(doc, "4.1  Verification — the checks the field has to pass", 2)
    _para(doc,
          "A steady solver's residuals say how much the last iteration moved, "
          "and whether the answer means anything is a separate question. Every run is therefore judged "
          "against identities the physics has to satisfy. All of them have to "
          "pass before a temperature is quoted.")
    if not export.payload.get("valid"):
        _para(doc,
              "In this run some fail. Every row marked FAIL below is a "
              "statement about the whole field.",
              bold=True, colour=BAD)
    _table(doc, ["Check", "Result", "What it catches"],
           [(c["name"],
             ("PASS" if c["passed"] else "FAIL") + (
                 f" — {c['detail']}" if c.get("detail") else ""),
             _CHECK_MEANING.get(c["name"], ""))
            for c in export.payload["checks"]],
           widths=[3.4, 6.8, 5.8])

    _heading(doc, "4.2  Convergence", 2)
    _figure(doc, drawn["convergence"],
            "Left: initial residuals per iteration. Right: the four stations "
            "of the air loop — supply, rack intake, aisle exit and unit "
            "return — recorded while the field settled, each drawn as its "
            "mixing-cup temperature with the range the air spans shaded "
            "behind it.")
    if kpis.get("drift_k") is not None:
        _para(doc,
              f"The largest move any station made between the last "
              f"two samples was {_num(kpis['drift_k'], 3)} K. A field that "
              f"satisfies its balances while a volume is still filling is still "
              f"moving, which is why this is measured separately from "
              f"the residuals.", size=9, colour=SECOND)

    # TWO CONVERGENCES, and a report that showed only the first. The residuals
    # and the stations say the FLOW settled; they say nothing about whether the
    # units and the room agreed in the end, which is the other half of a
    # coupled solve and the half that decides whether the supply temperature in
    # this document is the plant's answer or the last guess before the pass cap
    # (ADR-123).
    coupling = kpis.get("coupling")
    if coupling and coupling.get("off"):
        _para(doc,
              "The room was solved at the supply air temperature the case states: "
              "this case turns the coupling off (`solver.couple: false`), so "
              "the machines were never asked whether they can make it. The "
              "`coil_closure` check in section 4.1 is what says whether they "
              "can.",
              size=9, colour=SECOND)
    elif coupling:
        passes = coupling.get("passes") or 0
        moved = coupling.get("moved_k")
        closed = coupling.get("converged")
        _para(doc,
              f"The room and the units were solved together. The loop took "
              f"{passes} pass{'es' if passes != 1 else ''}"
              + (f", and on the last one no unit's supply air temperature "
                 f"moved more than {_num(moved, 3)} K"
                 if moved is not None else "")
              + (f" — inside the {_num(coupling.get('tolerance_k'), 2)} K it "
                 f"closes on, so the supply temperature in this report is what "
                 f"this plant produces at the return this room gives it."
                 if closed else
                 (f". THE LOOP STAYED OPEN: it was stopped because the supply "
                  f"air kept moving by about {_num(moved, 2)} K every pass "
                  f"instead of settling — the plant's control is chasing its "
                  f"own return, and the field carries the last value it "
                  f"reached." if coupling.get("diverged") else
                  f". THE LOOP STAYED OPEN: that is the safety limit of "
                  f"{coupling.get('limit')} passes, and the supply air was "
                  f"still moving when it was reached. The plant is swinging "
                  f"between two answers and the field carries one of them.")),
              size=9, colour=SECOND if closed else BAD,
              bold=not closed)

    _heading(doc, "4.3  Temperature field", 2)
    _para(doc, "All maps share one fixed colour band, 10 °C to 40 °C in 2,5 K "
               "contours, with the ASHRAE limits marked on the bar. The band is "
               "the same in every map, so a temperature carries the same colour "
               "throughout.")
    _figure(doc, drawn["plan_mid"],
            "Plan at rack mid-height — the plane that governs the intake "
            "condition of the IT equipment. Each outlined rectangle is one rack "
            "and each row carries its name and the cabinets in it; the heavy "
            "lines are the containment and the dividing walls; the violet "
            "lines are the customer cage where the hall has one; the blue bars "
            f"are the {export.naming['plural']}, tagged "
            f"{export.unit_tag(0)} onwards.")
    which = _containment_of(export)
    _figure(doc, drawn["plan_top"],
            "Plan just below the top of the racks, "
            + ("at the mouth of the contained hot aisles — where recirculating "
               "or leaking air arrives first."
               if which == "hot" else
               "over the lids of the contained cold aisles — where the hot room "
               "above them would show first in a leak."
               if which == "cold" else
               "at the mouth of the hot aisles — where recirculating air "
               "arrives first."))
    _figure(doc, drawn["plan_plenum"],
            "Plan inside the return plenum, above the false ceiling. "
            + ("One volume across the whole hall, feeding every gallery."
               if len(model.get("galleries") or [1]) > 1
               else "Collecting from every hot aisle on its way to the gallery."))
    _figure(doc, drawn["cross"],
            "Section across the hall, through a rack block: "
            + ("cold aisle, rack row, contained hot aisle and the chimney up "
               "to the false ceiling." if which == "hot" else
               "contained cold aisle under its lid, rack row, and the open hot "
               "aisle rising to the ceiling grilles." if which == "cold" else
               "cold aisle, rack row and hot aisle."))
    _figure(doc, drawn["long_cold"],
            "Section along the hall, through a cold aisle: the supply air "
            "leaving the units, the length it has to travel, and the mechanical "
            "gallery behind each dividing wall.")
    _figure(doc, drawn["long_hot"],
            "The same section through a contained hot aisle. The containment is "
            "doing its job when this plane is hot from floor to ceiling. A "
            "containment leak shows first in the cold plane above."
            if which == "hot" else
            "The same section through a hot aisle, which in this hall is the "
            "open room: it is hot from the rack tops to the ceiling grilles, "
            "and the contained cold aisle in the section above stays cold "
            "under its lid. A containment leak shows first as warm air in "
            "that cold plane." if which == "cold" else
            "The same section through a hot aisle.")

    _heading(doc, "4.4  Air speed and static pressure", 2)
    _para(doc,
          "The other two fields the solution carries. Both are fitted to this "
          "run — the ends of each bar are the 1st and 99th percentile of the "
          "whole field, so the room is readable and the unit's own discharge, "
          "which is an order above anything in the room, sits past the "
          "arrowhead.")
    _figure(doc, drawn["plan_speed"],
            "Air speed in plan at rack mid-height. This is the plane the "
            "cabinets breathe from: an aisle starved of supply shows "
            "here as still air in front of a row.")
    _figure(doc, drawn["cross_speed"],
            "Air speed across the hall. The supply leaves the plenum through "
            "the floor plates, crosses the cabinets and rises out of the "
            "aisle — the three places a velocity is worth reading.")
    _figure(doc, drawn["plan_pressure"],
            "Static pressure in plan at rack mid-height, relative to the "
            "reference cell. A contained aisle stands above the room it sits "
            "in, and the difference is what drives air through the cabinets.")
    _figure(doc, drawn["cross_pressure"],
            ("Static pressure across the hall. The supply plenum is the "
             if model.get("floor_height") else
             "Static pressure across the hall. The gallery side of the "
             "dividing wall is the ")
            + "highest pressure in the room and the gradient across each rack "
            "row is the drop the porous zone delivers — the same number "
            "`rack_resistance` checks in section 4.1.")

    _heading(doc, "4.5  Rack intake temperature", 2)
    _figure(doc, drawn["racks"],
            "Every rack coloured by the temperature of the air it breathes, "
            "measured at the top of the rack. The scale here is FITTED to this "
            "hall, which spreads the racks across the full range and ranks "
            "them; on the fixed band of the maps above, every rack in a healthy "
            "hall falls inside one 2,5 K step. Read this figure for the ranking "
            "and the figure below for the absolute judgement.")
    # WHICH ROOM EACH CABINET IS IN. A hall with a customer cage is two
    # rooms, and the question a reader has of this table is whose cabinet is
    # warmest -- so the column is there when there is a cage and absent when
    # there is not (ADR-102).
    caged = {r["name"] for r in export.racks if r.get("in_cage")}
    _cage_verdict(doc, zones, caged)
    _table(doc, ["Rack", "Row", *(["Where"] if caged else []),
                 "Intake, top of rack", "Intake, face mean",
                 "Exhaust", "Rise", "ASHRAE"],
           [(z["name"], z["row"],
             *(["cage" if z["name"] in caged else "hall"] if caged else []),
             f"{_num(z['inlet_top_c'], 2)} °C",
             f"{_num(z['inlet_temp_c'], 2)} °C", f"{_num(z['peak_temp_c'], 2)} °C",
             f"{_num((z['peak_temp_c'] or 0) - (z['inlet_temp_c'] or 0), 2)} K",
             (z.get("ashrae") or {}).get("verdict", ""))
            for z in zones[:12]],
           note="The twelve warmest of "
                f"{len(zones)} rack positions, by the air arriving at the top of "
                "the rack."
                + (" Annex A carries every position."
                   if len(zones) > 12 else ""))
    _figure(doc, drawn["ashrae"],
            "Distribution of rack intake temperature against the ASHRAE class A1 "
            "envelope.")

    _heading(doc, "4.6  Unit by unit", 2)
    if drawn.get("units"):
        _figure(doc, drawn["units"],
                "Return air temperature and heat removed, unit by unit. The "
                "units are numbered along the gallery wall, in gallery order.")
    fans = kpis.get("fans", [])
    rating = (model.get("operating") or {}).get("unit_capacity_kw")
    # 'Of available' is the column that decides whether the plant has reserve:
    # the rating is what the machine was sold as, the available capacity is
    # what it has at the air this hall gave it (ADR-036).
    available = any(f.get("available_kw") for f in fans)
    headers = ["Unit", "Return air", "Mass flow", "Heat removed", "Of the rating"]
    widths = [1.8, 2.6, 2.4, 2.6, 2.4, 2.6]
    if available:
        headers.append("Available")
        headers.append("Of available")
        widths = [1.5, 2.2, 2.0, 2.2, 2.0, 2.2, 2.2, 1.7]
    # NOT "Rise": next to four temperature columns a bare "Rise" in pascals
    # reads as a temperature rise. This is the static pressure the unit has to
    # produce around the whole loop, cabinets included.
    headers.append("Loop static")
    _table(doc, headers,
           [(f["name"].replace("fan", ""),
             f"{_num(f.get('return_temp_c'), 2)} °C",
             f"{_num(f.get('intake_kg_s'), 1)} kg/s",
             f"{_num(f.get('heat_kw'), 0)} kW",
             f"{_num((f.get('heat_kw') or 0) / rating * 100, 0)} %" if rating else "—",
             *((f"{_num(f.get('available_kw'), 0)} kW",
                f"{_num(f.get('of_available_pct'), 0)} %") if available else ()),
             f"{_num(f.get('rise_pa'), 1)} Pa")
            for f in fans],
           widths=widths,
           note="Heat removed is the enthalpy the air carries out of each unit, "
                "mass flow × cp × (return − supply). 'Of the rating' compares it "
                "with the CATALOGUE net sensible capacity, at the unit's "
                "selection point."
                + (" 'Available' is what the coil transfers at the return "
                   "air temperature in the column beside it and the air flow "
                   "this unit is moving, from the coil in section 3 — and 'Of "
                   "available' is the one of the two that says whether this "
                   "plant has reserve. 'Loop static' is the pressure that "
                   "unit produces around the whole loop; the part of it that "
                   "its external static pressure has to cover is the loop "
                   "less the cabinets' own drop, in the summary table and in "
                   "section 4.1."
                   if available else
                   " The capacity a coil actually has at the return temperature "
                   "it receives differs from that, and this run named no unit "
                   "to read it from — see section 6."))


_CHECK_MEANING = {
    "mass_balance": "the units supplying and drawing different masses",
    "sealed_envelope": "any wall or baffle passing air",
    "no_backflow": "air reversing through a fan intake",
    "energy_closure": "return air carrying less than the installed load",
    "return_path": "a volume still filling — the plenum, usually",
    "rack_resistance": "porous zones delivering a different drop from the one given",
    "grille_resistance": "the same, for the ceiling grilles",
    "plenum_resistance": "the same, for the supply plenum's grilles",
    "floor_resistance": "the same, for a raised floor's perforated plates",
    "fan_capacity": "the room costing more than the unit's datasheet offers",
    "coil_closure": "a field solved with supply air the plant cannot make",
    "settled": "a field still moving between samples",
    "ashrae_inlet": "a rack breathing air above the recommended band",
    "plausible_velocity": "a velocity field no fan or buoyancy could produce",
}


# --- 4 and 5 ------------------------------------------------------------------


def _conclusions(doc, export: Export) -> None:
    kpis = export.kpis
    model = export.model
    zones = kpis["zones"]
    warmest = max(zones, key=lambda z: z["inlet_top_c"] or -999)
    margin = 27.0 - (warmest["inlet_top_c"] or 0)
    fans = kpis.get("fans", [])
    returns = [f.get("return_temp_c") for f in fans if f.get("return_temp_c") is not None]
    spread = (max(returns) - min(returns)) if returns else None
    heats = [f.get("heat_kw") for f in fans if f.get("heat_kw") is not None]
    rating = (model.get("operating") or {}).get("unit_capacity_kw")

    _heading(doc, "5  Conclusions", 1)
    # THE VERDICT FIRST. A reader who reads section 5 alone -- and on a report
    # of this length most do -- used to get four paragraphs of findings and
    # meet "every physical check does NOT pass" in the fifth. If the numbers
    # below are not results, that is the first thing the section has to say
    # (ADR-123).
    if not export.payload.get("valid"):
        _para(doc,
              "THIS SECTION IS DIAGNOSTIC MATERIAL. One or more of the physical checks in "
              "section 4.1 failed, and until they are cleared nothing in this "
              "section may be quoted. It is set out below so that the failure "
              "can be diagnosed.",
              bold=True, colour=BAD)
    findings = []
    if margin >= 0:
        findings.append(
            f"The air delivered to the IT equipment is within the ASHRAE class A1 "
            f"recommended range. The warmest rack intake is "
            f"{_num(warmest['inlet_top_c'], 2)} °C at rack {warmest['name']}, "
            f"measured at the top of the rack, leaving {_num(margin, 2)} K of "
            f"margin against the 27 °C recommended limit."
        )
    else:
        findings.append(
            f"The warmest rack intake, {_num(warmest['inlet_top_c'], 2)} °C at "
            f"rack {warmest['name']}, is ABOVE the 27 °C ASHRAE recommended "
            f"limit by {_num(-margin, 2)} K. The containment is not holding at "
            f"that position."
        )
    if spread is not None:
        findings.append(
            f"The cooling plant is thermally {'uniform' if spread < 2 else 'uneven'} "
            f"in this configuration: the return air temperature spans "
            f"{_num(spread, 2)} K across the {len(fans)} units, from "
            f"{_num(min(returns), 2)} °C to {_num(max(returns), 2)} °C."
        )
    # CABINETS THE ROOM STARVES. The racks have no fans (ADR-013): what
    # passes through one is what the row's pressure difference drives, so a
    # 20 kW cabinet between 4,7 kW neighbours gets much the same air they do
    # and rises by far more than its design temperature difference. That is
    # the finding a rack-level report exists to make -- the commercial tools
    # call it an airflow deficit -- and this document printed the rise in an
    # annex column and drew no conclusion from it.
    design_dt = (model.get("operating") or {}).get("design_delta_t_k")
    if design_dt:
        rises = [(z["name"], (z.get("peak_temp_c") or 0) - (z.get("inlet_temp_c") or 0))
                 for z in zones if z.get("peak_temp_c") is not None
                 and z.get("inlet_temp_c") is not None and z.get("load_w")]
        starved = [(n, r) for n, r in rises if r > 2 * design_dt]
        if starved:
            worst = max(starved, key=lambda x: x[1])
            findings.append(
                f"{len(starved)} cabinet{'s' if len(starved) != 1 else ''} "
                f"{'rise' if len(starved) != 1 else 'rises'} by more than twice "
                f"the {_num(design_dt, 1)} K the design airflow of "
                f"{_num((kpis.get('hvac') or {}).get('cfm_per_kw'), 0)} CFM/kW "
                f"implies — the worst is {worst[0]}, at {_num(worst[1], 1)} K. "
                f"A cabinet has no fan in this model: it draws what the "
                f"pressure across its row gives it, so a heavily loaded "
                f"cabinet among lightly loaded neighbours gets much the same "
                f"air they do and runs that much hotter. In the room, its own "
                f"fans would pull harder and the deficit would show as "
                f"recirculation at its intake instead. Either way it is the "
                f"cabinet to look at first."
            )
    if heats and rating:
        over = [f for f in fans if (f.get("heat_kw") or 0) > rating]
        findings.append(
            f"The plant removes {_num(sum(heats), 0)} kW. "
            + (f"No unit exceeds its catalogue rating of {_num(rating, 1)} kW; "
               f"the most loaded is at {_num(max(heats) / rating * 100, 0)} % of it."
               if not over else
               f"{len(over)} {_units(len(over))} exceed the catalogue rating of "
               f"{_num(rating, 1)} kW, the worst at "
               f"{_num(max(heats) / rating * 100, 0)} %.")
        )
    # The finding that matters more than the one above it: the catalogue
    # figure is what the plant was bought as, this is what it has (ADR-036).
    if kpis.get("available_kw"):
        used = kpis["utilisation_pct"]
        outside = kpis.get("units_over_capacity") or 0
        removed = sum(heats) if heats else kpis.get("recovered_kw")
        sentence = (
            f"Against the capacity the coils actually have at the return air "
            f"this hall produces, the plant is at {_num(used, 1)} % — "
            f"{_num(removed, 0)} kW removed of "
            f"{_num(kpis['available_kw'], 0)} kW available from "
            f"{len(fans)} × {kpis['unit_model']}"
        )
        catalogue = kpis.get("catalogue_kw")
        if catalogue and removed:
            sentence += (
                f". The catalogue sums to {_num(catalogue, 0)} kW, which reads "
                f"as {_num(removed / catalogue * 100, 1)} % loaded; the "
                f"{_num(used, 1)} % is the figure that governs what happens "
                f"when a unit is lost"
            )
        sentence += (
            f". {outside} {_units(outside)} {'is' if outside == 1 else 'are'} "
            f"drawing more than the coil can give at "
            f"their own return temperature."
            if outside else
            ". No unit is drawing more than its coil can give at the return "
            "temperature it receives."
        )
        findings.append(sentence)
    water, design = (kpis.get("coil_water_out_c"),
                     kpis.get("coil_water_out_design_c"))
    if water and design and water > design + 0.5:
        findings.append(
            f"At the air they are receiving the coils transfer more than the "
            f"water side was sized for: the heat above would take the water "
            f"out at {_num(water, 1)} °C against the {_num(design, 0)} °C of "
            f"the selection. The heat exchanger really does that at this "
            f"return — a coil's capacity is ε × C_air × (T_return − T_water) "
            f"and grows with the return — but whether the chiller, the pump "
            f"and the valve can hold the design water flow at that rise is a "
            f"question outside this study."
        )
    if kpis.get("fan_rise_pa") and kpis.get("fan_static_pa"):
        # THE SAME ARITHMETIC AS THE CHECK, or the conclusion contradicts
        # section 4.1: the cabinets' drop is their own fans' work, and a
        # unit's external static is what it offers the room outside itself
        # (ADR-115).
        cabinets = kpis.get("rack_drop_pa") or 0.0
        room = kpis.get("room_static_pa")
        if room is None:
            room = max(kpis["fan_rise_pa"] - cabinets, 0.0)
        findings.append(
            f"The room outside the cabinets costs the most loaded unit "
            f"{_num(room, 1)} Pa of the {_num(kpis['fan_static_pa'], 0)} Pa the "
            f"unit can produce at the airflow it is moving "
            f"({_num(room / kpis['fan_static_pa'] * 100, 0)} %)"
            + (f" — the loop costs {_num(kpis['fan_rise_pa'], 1)} Pa and the "
               f"cabinets' own fans carry {_num(cabinets, 1)} Pa of it"
               if cabinets else "")
            + f", and the least loaded unit {_num(kpis.get('fan_rise_min_pa'), 1)} Pa "
            f"of loop. "
            "That is the resistance of the room; the coil and filters inside "
            "the machine are already inside the unit's external static "
            "pressure."
        )
    # A SENTENCE THAT SAYS "matches" WHILE QUOTING 106 % is a sentence that
    # contradicts its own number. The heat the return carries can only exceed
    # the load in a field still settling (ADR-115).
    closure = (kpis["energy_closure"] or 0) * 100
    findings.append(
        f"The energy balance closes at {_num(closure, 1)} %: the heat the "
        f"return air carries out "
        + ("matches the load the racks put in. "
           if abs(closure - 100) <= 2
           else f"is {_num(abs(closure - 100), 1)} % "
                f"{'above' if closure > 100 else 'below'} the load the racks "
                f"put in, which a steady field cannot be — this one is still "
                f"settling. ")
        # "Every physical check does NOT pass" reads as "each one fails",
        # which is not what it meant, and the paragraph at the head of this
        # section already carries the verdict. On a run that passed, the
        # sentence is worth having here.
        + ("Every physical check passes, so the temperatures above may be "
           "quoted." if export.payload["valid"] else
           "Section 4.1 names the check this run fails.")
    )
    _bullets(doc, findings)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9.5, colour="B07000")


def _annex(doc, export: Export) -> None:
    """Every rack position, for a reader looking one up.

    Section 4.5 ranks the hall and shows the twelve that decide whether it
    passes. A reader asking what one particular rack breathes needs the rest,
    and a table that lives in the document travels with it.
    """
    zones = export.kpis.get("zones") or []
    if len(zones) <= 12:
        return  # section 4.5 already carries them all
    ordered = sorted(zones, key=lambda z: (str(z.get("row") or ""),
                                           str(z.get("position") or ""),
                                           str(z.get("name") or "")))
    doc.add_page_break()
    _heading(doc, "Annex A  Rack intake temperature, every position", 1)
    _para(doc,
          f"All {len(zones)} rack positions, by row. Section 4.5 ranks them "
          f"and shows the twelve warmest.",
          size=9.5, colour=SECOND)
    _table(doc, ["Rack", "Row", "Intake, top of rack", "Intake, face mean",
                 "Exhaust", "Rise", "ASHRAE"],
           [(z["name"], z["row"], f"{_num(z['inlet_top_c'], 2)} °C",
             f"{_num(z['inlet_temp_c'], 2)} °C", f"{_num(z['peak_temp_c'], 2)} °C",
             f"{_num((z['peak_temp_c'] or 0) - (z['inlet_temp_c'] or 0), 2)} K",
             "ok" if (z.get("ashrae") or {}).get("within_recommended")
             else (z.get("ashrae") or {}).get("verdict", ""))
            for z in ordered],
           widths=[2.6, 1.6, 2.8, 2.8, 2.4, 1.8, 2.0])


def _limits(doc, export: Export) -> None:
    """What this model represents differently from the room it stands for.

    Only that. A CFD study carries numerical uncertainty and covers the
    scenario it was given, which is true of every CFD study and is not this
    report's finding. What belongs here is where AICFD's representation of
    *this* room departs from the room.
    """
    _heading(doc, "6  Limitations", 1)
    _para(doc,
          "These results are valid for the boundary conditions listed in "
          "section 3. Where the model represents the room differently from "
          "the room:")
    kpis = export.kpis
    unit = export.equipment
    limits = []
    if not kpis.get("available_kw"):
        limits.append(
            "Capacity is compared against the unit's catalogue figure, which "
            "holds at the return temperature the unit was selected for. A "
            "design selection for the unit gives its coil, and with it the "
            "capacity at the return air this room produces."
        )
    # WHAT IS STILL TRUE OF THIS RUN, not what was true when the list was
    # written. "One load per rack" stayed on it for as long as the per-position
    # load map, the blanking panel and the zero-load cabinet had been shipping,
    # so the report told an engineer their layout was not represented while the
    # solver was using it. A limitation that describes the code has to be asked
    # of the code (ADR-089).
    limits += _model_limits(export)
    across, through, _tall = _cells_per_rack(export)
    plan_cells = min(across, through)
    if plan_cells < 4:
        limits.append(
            f"A conceptual-design mesh: {_cells(plan_cells)} across a cabinet "
            "in plan. "
            "Trust the ranking of racks and the hall-scale fields; a single "
            "rack's intake carries "
            + ("1 to 2 K" if plan_cells < 2 else "about 1 K")
            + " of uncertainty."
        )
    _bullets(doc, limits)
    _para(doc,
          "The case specification, the generated OpenFOAM case, the solver log "
          "and the exported result together reproduce this document exactly. "
          "Nothing in it was typed in by hand.",
          size=9, colour=MUTED, italic=True)
