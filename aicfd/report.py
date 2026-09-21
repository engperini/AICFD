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

from datetime import date
from pathlib import Path

from aicfd.figures import (
    Export, ashrae, capacity, convergence, geometry, plan, rack_map, section,
    units,
)

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


def title_of(case: str) -> str:
    """A case slug as a title: ``hall-double-gallery`` -> ``Hall Double
    Gallery``. Replaced by ``--title`` when the room has a real name."""
    return " ".join(word.capitalize() for word in case.replace("_", "-").split("-"))


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
        # The room as modelled, dimensioned, before any field is quoted.
        "geo_a": geometry(export, figures / "geo-a.png", 0,
                          "A · Transverse section — through a pod"),
        "geo_b": geometry(export, figures / "geo-b.png", 1,
                          "B · Longitudinal section — through the hot aisle"),
        "geo_c": geometry(export, figures / "geo-c.png", 2,
                          "C · Plan — at rack height"),
        "geo_zoom": geometry(export, figures / "geo-zoom.png", 2,
                             "C (detail) — every cabinet, its name and its load",
                             zoom=_zoom_span(export)),
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
        ("5", "Conclusions", "Findings and what they do and do not support"),
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
          "tops, return grilles and the fan walls.")
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
         "Return and supply air, air flow, net sensible capacity, electrical "
         "input, and the chilled water the unit was selected at."),
        ("2 — the selection is checked",
         "Air flow times the temperature difference has to carry the stated "
         "capacity. A selection whose own numbers disagree is refused before "
         "anything is solved."),
        ("3 — the coil is built",
         "A chilled-water coil is a counterflow heat exchanger: what it "
         "transfers is its effectiveness times the air's capacity rate times "
         "the difference between the return air and the entering water. The "
         "selection fixes the effectiveness at that point, and the "
         "effectiveness fixes the conductance."),
        ("4 — the curve is reviewed",
         "The coil answers at any return air temperature and any air flow, "
         "which is the curve printed in section 3. The engineer keeps it, or "
         "replaces it with the manufacturer's own capacity curve."),
        ("5 — the model runs",
         "From here each unit is a machine the solution talks to, as "
         "section 1.5 describes."),
    ], widths=[4.0, 12.0],
        note="One selection is what the method needs. A manufacturer who "
             "states the unit's own division of resistance between air and "
             "water replaces the default in step 3.")

    _heading(doc, "1.5  Solving the room and the units together", 2)
    _para(doc,
          "The supply air temperature is an output. A fan wall delivers what "
          "its coil gives it, from the air the room returns, so the room and "
          "the machines are solved as one problem.")
    _table(doc, ["Step", "What happens"], [
        ("1 — the room is solved",
         "A segment of the flow solution runs at the current supply air "
         "temperature."),
        ("2 — each unit is read",
         "Every unit's mixed-mean return temperature and mass flow are taken "
         "from the solved field, unit by unit."),
        ("3 — each coil answers",
         "That return goes through that unit's coil. Its water valve modulates "
         "to hold the setpoint; at full water the supply air follows the "
         "return."),
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
    _para(doc,
          "A run is accepted on physical grounds. Eleven identities the "
          "solution has to satisfy are evaluated on the converged field: mass "
          "in against mass out, the heat the return air carries against the "
          "load the racks release, the pressure drop across the racks against "
          "the resistance they were given, the air each unit draws against "
          "what it supplies, and seven more. Section 4.1 lists them with the "
          "numbers that produced each verdict, and all of them pass before a "
          "temperature in this document is quoted.")
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
        f"row segments and {len(model['fans'])} fan wall units, against an "
        f"installed IT load of {_num(kpis['total_load_w'] / 1000, 0)} kW.",
    ])

    _heading(doc, "Objectives", 2)
    _bullets(doc, [
        "Establish the temperature of the air entering each rack, and check it "
        "against the ASHRAE class A1 recommended range of 18 °C to 27 °C.",
        "Confirm that the air loop closes: that the fan walls move the mass they "
        "are given, that nothing leaks through a wall or reverses through an "
        "intake, and that the return air carries the installed load.",
        "Quantify the resistance the room presents to the units, against the "
        "external static pressure their datasheet offers.",
        "Size the plant against the design office's rules — installed capacity "
        "in kilowatts and installed airflow at the design CFM/kW — before any "
        "CFD result is quoted.",
    ])

    _heading(doc, "Basis of design — fan wall selection point", 2)
    unit = export.equipment
    if unit:
        _para(doc,
              f"The cooling plant is {len(model['fans'])} × "
              f"{unit.family} {unit.model}. The quantities below are that "
              f"machine's own manufacturer selection. Section 3 gives the "
              f"conditions it was taken at and the capacity the unit has "
              f"across the range of return air temperatures this hall "
              f"produces.",
              size=9.5, colour=SECOND)
    _table(doc, ["Quantity", "Value"], [
        ("Unit", f"{unit.family} {unit.model}".strip() if unit else "not named"),
        ("Units installed", f"{len(model['fans'])}"),
        ("Airflow per unit", f"{_num(kpis['supply_flow_m3h'] / max(1, len(model['fans'])), 0)} m³/h"),
        ("Total airflow to the room", f"{_num(kpis['supply_flow_m3h'], 0)} m³/h"),
        ("Supply air temperature", f"{_num(kpis['supply_temp_c'], 1)} °C"),
        ("Net sensible capacity per unit",
         f"{_num(fan.get('unit_capacity_kw'), 1)} kW"
         if fan.get("unit_capacity_kw") else "not given"),
        ("Electrical input per unit",
         f"{_num(fan.get('unit_power_kw'), 1)} kW"
         if fan.get("unit_power_kw") else "not given"),
        ("External static pressure, at the rated airflow",
         f"{_num(fan.get('fan_static_pa'), 0)} Pa"
         if fan.get("fan_static_pa") else "not given"),
        ("Static pressure available at the modelled airflow",
         f"{_num(kpis.get('fan_static_pa'), 0)} Pa"
         + (" (interpolated on the unit's P-Q curve)"
            if fan.get("fan_curve") else " (the datasheet point)")
         if kpis.get("fan_static_pa") else "not given"),
        ("Site elevation", f"{_num(site.get('altitude_m'), 0)} m"),
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
        ("Rise across the most loaded unit", f"{_num(kpis.get('fan_rise_pa'), 1)} Pa",
         f"of the {_num(kpis.get('fan_static_pa'), 0)} Pa the unit can produce at "
         "this airflow"
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
              "Each row is the whole stream crossing that surface, not a "
              "probe in it: the temperature is the mixing cup, weighted by "
              "what each part of the surface carries, and the range is what "
              "the air at it actually spans. A room whose cabinets carry "
              "different loads has no single temperature at any station, so "
              "the range is part of the reading and not an error bar.",
              size=9, colour=SECOND)
        _table(doc,
               ["Station", "Mixed", "Range", "Flow", "Face velocity"],
               [(f"{s['label']}",
                 f"{_num(s.get('temp_c'), 2)} °C",
                 (f"{_num(s.get('low_c'), 1)} – {_num(s.get('high_c'), 1)} °C"
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
             "not a result and must not be quoted; see section 4.1."
    )
    _para(doc, verdict, bold=True, colour=INK if export.payload["valid"] else BAD)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9, colour="B07000")


def _selection_rows(unit) -> list[tuple[str, str]]:
    """The chilled-water and air conditions a unit's table was selected at.

    They belong beside the capacity because they are what makes it true. The
    same coil at 12 °C entering water is a different machine as far as any
    number in this report is concerned.
    """
    if not unit or not unit.selection:
        return []
    labels = (
        ("entering_water_c", "Entering chilled water", "°C", 1),
        ("leaving_water_c", "Leaving chilled water", "°C", 1),
        ("entering_air_rh", "Entering air relative humidity", "%", 0),
    )
    return [(label, f"{_num(unit.selection[key], digits)} {suffix}")
            for key, label, suffix, digits in labels
            if unit.selection.get(key) is not None]


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
    _para(doc,
          f"The room is derived from the case specification, not drawn: every "
          f"dimension below follows from the equipment sizes, the aisle widths "
          f"and the clearances the engineer typed. The arrangement is "
          f"{arrangement}, with hot-aisle containment and a ceiling-plenum "
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
        ("Contained hot aisles", f"{len(model['hot_aisles'])} in plan"
                                 + (f", {len(model['hot_aisles']) * len(blocks)} "
                                    "separate containment volumes" if len(blocks) > 1 else "")),
        ("Cold aisles", f"{len(model['cold_aisles'])}"),
        ("Fan wall units", f"{len(model['fans'])}"),
    ]
    _table(doc, ["Feature", "As built in the model"], rows, widths=[7.0, 9.0])

    _heading(doc, "Computational mesh", 2)
    cell = model["cell_size"]
    _para(doc,
          f"A single structured hexahedral block of {kpis['cells']:,} cells, "
          f"{_num(cell[0], 2)} × {_num(cell[1], 2)} × {_num(cell[2], 2)} m, with "
          f"every internal surface cut into it afterwards as a two-sided baffle: "
          f"the containment panels, the false ceiling, the row ends and tops, the "
          f"return grilles and the fan walls. The room's air loop closes inside "
          f"the box, so none of those surfaces is a domain boundary.")
    across, through, tall = _cells_per_rack(export)
    _para(doc,
          "The cell sizes differ by axis on purpose. In plan the mesh is sized "
          f"on the cabinet: {_cells(across)} across a rack's face and "
          f"{_num(through, 0)} through its depth, which is what decides how well "
          "the porous zone reproduces its own pressure curve. In the vertical it "
          "is fine enough that the false ceiling, the fan wall top and the rack "
          f"tops land on cell faces ({_cells(tall)} up a cabinet) — a "
          "plane that falls mid-cell produces a ragged surface that leaks "
          "silently.")
    _para(doc, _resolution_verdict(across, through), bold=True)
    if model.get("warnings"):
        _para(doc, "Dimensions the mesh snapped to its nearest cell face:",
              size=9, colour=SECOND, space_after=2)
        _bullets(doc, model["warnings"])

    _heading(doc, "Boundary conditions and models", 2)
    _table(doc, ["Feature", "How it is modelled"], [
        ("Fan wall", "A pair of patches on the same internal faces: air leaves the "
                     "gallery through the intake and re-enters the cold aisle "
                     "through the supply. Both are set by MASS flow, not volume — "
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
                 "across a rack is a result and not an input."),
        ("Blanking panel", "A solid, adiabatic wall on the cabinets' own face, "
                           "closing the position across the full height of the "
                           "row. No air crosses it. The volume behind it is left "
                           "open to the contained aisle, which is what the space "
                           "behind a blanking plate is; the sealed-envelope "
                           "check confirms it carries zero flow."),
        ("Cabinet at zero load", "NOT a blanking panel. It is the same porous "
                                 "zone with the same resistance as its "
                                 "neighbours and no heat source: an empty "
                                 "cabinet still breathes and still costs the fan "
                                 "what the row costs. The two are different "
                                 "things and the case says which each position "
                                 "is."),
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
                        "changes no flow and is not a surface in the mesh; its "
                        "pressure is added to the fan duty from its loss "
                        "coefficient and the check says so."),
        ("Containment, false ceiling, row ends", "Two-sided adiabatic wall "
                                                 "baffles. A row not closed on "
                                                 "five sides leaks most of its "
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
    per_unit = kpis["supply_flow_m3h"] / max(1, len(model["fans"]))
    _table(doc, ["Step", "Value"], [
        ("1 — the unit's airflow, from the datasheet", f"{_num(per_unit, 0)} m³/h"),
        ("2 — units installed", f"{len(model['fans'])}"),
        ("3 — total delivered to the room", f"{_num(kpis['supply_flow_m3h'], 0)} m³/h"),
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
        ("Cabinet widths", f"{len(widths)}",
         ", ".join(f"{w:g} m" for w in widths)),
    ], widths=[5.0, 2.4, 8.6])

    for key, caption in (
        ("geo_a", "Figure A — transverse section. The gallery, the units, the "
                  "aisles and the contained volume, dimensioned."),
        ("geo_b", "Figure B — longitudinal section through the hot aisle, "
                  "dimensioned."),
        ("geo_c", "Figure C — plan at rack height. Each distinct part is "
                  "dimensioned once, where it first occurs; the hall repeats."),
        ("geo_zoom", "Figure C (detail) — one pod of one block, every cabinet "
                     "with its name and the load it carries."),
    ):
        if drawn.get(key):
            _figure(doc, drawn[key], caption)


def _surfaces_section(doc, export: Export) -> None:
    """Every perforated surface this case uses, with the number that decides it.

    One table, because K is the whole of the physics: the jump each of these
    imposes is ½·K·ρ·u², and a reader checking the plant against a catalogue
    needs to see which component each surface is and what it costs -- not be
    told that grilles exist.
    """
    roles = export.payload["model"].get("components") or []
    surfaces = [r for r in roles if r.get("kind") != "load" and r.get("applied")]
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
    _heading(doc, "How the units are controlled", 2)
    _para(doc,
          (f"The {units} units are NETWORKED: they run as one plant. Each pass "
           "of the coupled loop finds the warmest return any unit sees and "
           "controls every unit to it, so they deliver the same supply "
           "temperature and the plant is judged by the unit that has the "
           "hardest job. This is what a real BMS does with a fan-wall array, "
           "and it is what `fanwall.control: team` in the case asks for."
           if team else
           f"The {units} units run INDEPENDENTLY: each controls to the return "
           "air reaching its own intake, so a unit fed warmer air works harder "
           "and they do not deliver the same supply temperature. Set "
           "`fanwall.control: team` to run them as one networked plant "
           "instead."))
    _para(doc,
          "Either way every unit is given the same MASS flow. What the control "
          "changes is the water side — how much each coil is asked to transfer "
          "— not the air each unit moves.",
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
         "typically about 2 % of the IT load — is not included."),
        ("containment-panel",
         "Containment is modelled as perfect: the panels are solid walls in the "
         "mesh and their leakage figure is not read. Real containment leaks, "
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
            "obstructs an aisle is not in the geometry. The room is the "
            "cabinets, the aisles, the containment and the plant."
        )
    # A DIRECT-EXPANSION PLANT IS A LIMITATION OF THE MODEL, not a design
    # criterion the plant misses -- so it is read here, once, rather than
    # arriving in the alerts card on every run (ADR-097, ADR-098).
    kpis = export.payload.get("kpis") or {}
    if kpis.get("rated_return_c"):
        limits.append(
            f"{kpis.get('unit_model')} is a direct-expansion unit. Its "
            f"capacity follows the refrigerant circuit, the compressors' "
            f"staging and the outdoor air its condenser rejects into, none of "
            f"which is modelled here: the coil this software fits is a "
            f"chilled-water one. So this result is the room at the unit's "
            f"RATED point — {_num(kpis.get('rated_nscc_kw'), 1)} kW at "
            f"{_num(kpis.get('rated_return_c'), 1)} °C return — with the "
            f"supply temperature held there rather than re-solved against the "
            f"return the room produces, and the capacity quoted in this "
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
                "supported; a single rack's intake is not, to better than "
                "1 to 2 K.")
    if plan < 4:
        return ("At this resolution the flow around a cabinet is resolved well "
                "enough for the ranking of racks, the aisle-to-aisle "
                "temperatures and the hall-scale pressure field. A single "
                "rack's intake carries roughly 1 K of uncertainty.")
    return ("At this resolution the cabinet and the aisle around it are "
            "resolved, so a single rack's intake is supported as well as the "
            "ranking and the hall-scale fields. What remains is the modelling, "
            "not the mesh: the limitations in section 6 are what bound this "
            "result.")


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
              "fan array, anchored to the unit's selected external static "
              "pressure. It decides the uncontrolled operating point alone; "
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
        ("Resistance on the air side",
         f"{split} % (water side {100 - split} %)"
         if split is not None else "not in this export"),
        ("Air flow in this hall, against the design selection",
         f"{share} %" if share else "—"),
    ]
    _table(doc, ["Property of the coil", "Value"], rows, widths=[8.0, 8.0],
           note="The resistance split is the usual one for a finned "
                "chilled-water coil, and it is an input where the "
                "manufacturer states the unit's own.")


# --- 3 results ----------------------------------------------------------------


def _results(doc, export: Export, drawn: dict) -> None:
    kpis = export.kpis
    model = export.model
    zones = sorted(kpis["zones"], key=lambda z: -(z["inlet_top_c"] or -999))

    _heading(doc, "4  Results", 1)

    _heading(doc, "4.1  Verification — the checks the field has to pass", 2)
    _para(doc,
          "A steady solver's residuals say how much the last iteration moved, "
          "not whether the answer means anything. Every run is therefore judged "
          "against identities the physics has to satisfy. All of them have to "
          "pass before a temperature is quoted.")
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
              f"satisfies its balances while a volume is still filling is not "
              f"a steady answer, which is why this is measured separately from "
              f"the residuals.", size=9, colour=SECOND)

    _heading(doc, "4.3  Temperature field", 2)
    _para(doc, "All maps share one fixed colour band, 10 °C to 40 °C in 2,5 K "
               "contours, with the ASHRAE limits marked on the bar. The band is "
               "the same in every map, so a temperature carries the same colour "
               "throughout.")
    _figure(doc, drawn["plan_mid"],
            "Plan at rack mid-height — the plane that governs the intake "
            "condition of the IT equipment. Each outlined rectangle is one rack; "
            "the heavy lines are the containment and the dividing walls; the "
            "blue bars in the dividing walls are the fan wall units.")
    _figure(doc, drawn["plan_top"],
            "Plan just below the top of the racks, at the mouth of the contained "
            "hot aisles — where recirculating or leaking air arrives first.")
    _figure(doc, drawn["plan_plenum"],
            "Plan inside the return plenum, above the false ceiling. "
            + ("One volume across the whole hall, feeding every gallery."
               if len(model.get("galleries") or [1]) > 1
               else "Collecting from every hot aisle on its way to the gallery."))
    _figure(doc, drawn["cross"],
            "Section across the hall, through a rack block: cold aisle, rack row, "
            "contained hot aisle and the chimney up to the false ceiling.")
    _figure(doc, drawn["long_cold"],
            "Section along the hall, through a cold aisle: the supply air "
            "leaving the units, the length it has to travel, and the mechanical "
            "gallery behind each dividing wall.")
    _figure(doc, drawn["long_hot"],
            "The same section through a contained hot aisle. The containment is "
            "doing its job when this plane is hot from floor to ceiling. A "
            "containment leak shows first in the cold plane above.")

    _heading(doc, "4.4  Rack intake temperature", 2)
    _figure(doc, drawn["racks"],
            "Every rack coloured by the temperature of the air it breathes, "
            "measured at the top of the rack. The scale here is FITTED to this "
            "hall, which spreads the racks across the full range and ranks "
            "them; on the fixed band of the maps above, every rack in a healthy "
            "hall falls inside one 2,5 K step. Read this figure for the ranking "
            "and the figure below for the absolute judgement.")
    _table(doc, ["Rack", "Row", "Intake, top of rack", "Intake, face mean",
                 "Exhaust", "Rise", "ASHRAE"],
           [(z["name"], z["row"], f"{_num(z['inlet_top_c'], 2)} °C",
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

    _heading(doc, "4.5  Unit by unit", 2)
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
    headers.append("Rise")
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
                   "plant has reserve."
                   if available else
                   " The capacity a coil actually has at the return temperature "
                   "it receives differs from that, and this run named no unit "
                   "to read it from — see section 6."))


_CHECK_MEANING = {
    "mass_balance": "the fan walls supplying and drawing different masses",
    "sealed_envelope": "any wall or baffle passing air",
    "no_backflow": "air reversing through a fan intake",
    "energy_closure": "the return air not carrying the installed load",
    "return_path": "a volume still filling — the plenum, usually",
    "rack_resistance": "the porous zones not delivering the pressure drop given",
    "grille_resistance": "the same, for the ceiling grilles",
    "plenum_resistance": "the same, for the supply plenum's grilles",
    "floor_resistance": "the same, for a raised floor's perforated plates",
    "fan_capacity": "the room costing more than the unit's datasheet offers",
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
    if heats and rating:
        over = [f for f in fans if (f.get("heat_kw") or 0) > rating]
        findings.append(
            f"The plant removes {_num(sum(heats), 0)} kW. "
            + (f"No unit exceeds its catalogue rating of {_num(rating, 1)} kW; "
               f"the most loaded is at {_num(max(heats) / rating * 100, 0)} % of it."
               if not over else
               f"{len(over)} unit(s) exceed the catalogue rating of "
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
            f". {outside} unit(s) are drawing more than the coil can give at "
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
        findings.append(
            f"The room costs the most loaded unit {_num(kpis['fan_rise_pa'], 1)} Pa "
            f"of the {_num(kpis['fan_static_pa'], 0)} Pa the unit can produce at "
            f"the airflow it is moving "
            f"({_num(kpis['fan_rise_pa'] / kpis['fan_static_pa'] * 100, 0)} %), "
            f"and the least loaded {_num(kpis.get('fan_rise_min_pa'), 1)} Pa. "
            "That is the resistance of the room, not of the coil and filters "
            "inside the machine, which the unit's external static pressure "
            "already accounts for."
        )
    findings.append(
        f"The energy balance closes at "
        f"{_num((kpis['energy_closure'] or 0) * 100, 1)} %: the heat the return "
        f"air carries out matches the load the racks put in. Every physical "
        f"check "
        + ("passes, so the temperatures above may be quoted."
           if export.payload["valid"]
           else "does NOT pass; see section 4.1 before using any number here.")
    )
    _bullets(doc, findings)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9.5, colour="B07000")


def _annex(doc, export: Export) -> None:
    """Every rack position, for a reader looking one up.

    Section 4.4 ranks the hall and shows the twelve that decide whether it
    passes. A reader asking what one particular rack breathes needs the rest,
    and a table that lives in the document travels with it.
    """
    zones = export.kpis.get("zones") or []
    if len(zones) <= 12:
        return  # section 4.4 already carries them all
    ordered = sorted(zones, key=lambda z: (str(z.get("row") or ""),
                                           str(z.get("position") or ""),
                                           str(z.get("name") or "")))
    doc.add_page_break()
    _heading(doc, "Annex A  Rack intake temperature, every position", 1)
    _para(doc,
          f"All {len(zones)} rack positions, by row. Section 4.4 ranks them "
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
            "Trust the ranking of racks and the hall-scale fields, not a single "
            "rack's intake to better than "
            + ("1 to 2 K." if plan_cells < 2 else "about 1 K.")
        )
    _bullets(doc, limits)
    _para(doc,
          "The case specification, the generated OpenFOAM case, the solver log "
          "and the exported result together reproduce this document exactly. "
          "Nothing in it was typed in by hand.",
          size=9, colour=MUTED, italic=True)
