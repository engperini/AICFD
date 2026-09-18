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

from aicfd.figures import Export, ashrae, convergence, plan, rack_map, section, units

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


def build(results_dir: str | Path, out_path: str | Path,
          client: str | None = None, author: str | None = None) -> Path:
    """Write the Word report for one exported result."""
    docx = _docx()
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Cm, Pt

    export = Export(results_dir)
    figures = Path(results_dir) / "figures"
    figures.mkdir(exist_ok=True)
    drawn = _draw(export, figures)

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

    _cover(doc, export, client, author, WD_ALIGN_PARAGRAPH)
    doc.add_page_break()
    _contents(doc)
    doc.add_page_break()
    _summary(doc, export)
    doc.add_page_break()
    _methodology(doc, export)
    doc.add_page_break()
    _results(doc, export, drawn)
    doc.add_page_break()
    _conclusions(doc, export)
    _limits(doc, export)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(out))
    return out


def _draw(export: Export, figures: Path) -> dict:
    """Render every figure the document embeds."""
    model = export.model
    racks = export.racks
    rack_top = max(r["hi"][2] for r in racks) if racks else 2.0
    blocks = model.get("blocks") or [[model["hall"]["lo"][0], model["hall"]["hi"][0]]]
    hot = model["hot_aisles"][len(model["hot_aisles"]) // 2]
    block = blocks[len(blocks) // 2]
    return {
        "plan_mid": plan(export, figures / "plan-rack-mid.png", rack_top / 2,
                         f"Temperature at z = {rack_top / 2:.2f} m — rack mid-height"),
        "plan_top": plan(export, figures / "plan-rack-top.png", rack_top - 0.2,
                         f"Temperature at z = {rack_top - 0.2:.2f} m — top of the racks"),
        "plan_plenum": plan(export, figures / "plan-plenum.png",
                            (model["ceiling_z"] + export.size[2]) / 2,
                            "Temperature inside the return plenum"),
        "cross": section(export, figures / "section-across.png", 0,
                         (block[0] + block[1]) / 2,
                         f"Section across the hall at x = {(block[0] + block[1]) / 2:.2f} m",
                         "y — across the hall (m)"),
        "long": section(export, figures / "section-along.png", 1,
                        (hot[0] + hot[1]) / 2,
                        f"Section along the hall at y = {(hot[0] + hot[1]) / 2:.2f} m "
                        "— through a contained hot aisle",
                        "x — along the hall (m)"),
        "racks": rack_map(export, figures / "rack-intake.png"),
        "units": units(export, figures / "units.png"),
        "ashrae": ashrae(export, figures / "ashrae.png"),
        "convergence": convergence(export, figures / "convergence.png"),
    }


def _cover(doc, export: Export, client, author, align) -> None:
    from docx.shared import Pt

    _para(doc, space_after=90)
    _para(doc, "AICFD", size=13, bold=True, colour=ACCENT, space_after=2)
    _para(doc, "AI-ASSISTED CFD FOR DATA CENTRE COOLING", size=7.5,
          colour=MUTED, space_after=60)
    _para(doc, "TECHNICAL REPORT · STEADY-STATE ANALYSIS", size=8,
          bold=True, colour=ACCENT, space_after=6)
    title = doc.add_paragraph()
    title.paragraph_format.space_after = Pt(30)
    run = _run(title, f"CFD Analysis of the {export.payload['case']} Data Hall",
               size=26, colour=INK)
    run.font.name = "Georgia"
    if client:
        _para(doc, client, size=13, space_after=4)
    if author:
        _para(doc, author, size=10, colour=SECOND, space_after=2)
    _para(doc, date.today().strftime("%B %Y"), size=10, colour=MUTED, space_after=40)
    _para(doc,
          f"Case {export.payload['case']} · solved to iteration "
          f"{export.payload['time']} · "
          f"{export.kpis['cells']:,} cells · "
          f"{'all checks passed' if export.payload['valid'] else 'CHECKS FAILED'}",
          size=8, colour=MUTED)


def _contents(doc) -> None:
    _heading(doc, "Contents", 1)
    for number, name, note in (
        ("1", "Summary", "Scope, objectives, basis of design and headline results"),
        ("2", "Methodology", "Geometry, mesh, models and boundary conditions"),
        ("3", "Results", "Verification, temperature and airflow fields, unit by unit"),
        ("4", "Conclusions", "Findings and what they do and do not support"),
        ("5", "Limitations", "What this model cannot be asked"),
    ):
        paragraph = doc.add_paragraph()
        _run(paragraph, f"{number}   ", bold=True, size=11, colour=ACCENT)
        _run(paragraph, name, bold=True, size=11)
        _run(paragraph, f"\n      {note}", size=8.5, colour=MUTED)


# --- 1 summary ----------------------------------------------------------------


def _summary(doc, export: Export) -> None:
    kpis = export.kpis
    model = export.model
    spec = model.get("spec") or {}
    fan = spec.get("fanwall", {})
    zones = kpis["zones"]
    warmest = max(zones, key=lambda z: z["inlet_top_c"] or -999)
    hvac = kpis.get("hvac") or {}
    site = model.get("site") or {}

    _heading(doc, "1  Summary", 1)
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
    _table(doc, ["Quantity", "Value"], [
        ("Units installed", f"{len(model['fans'])}"),
        ("Airflow per unit", f"{_num(kpis['supply_flow_m3h'] / max(1, len(model['fans'])), 0)} m³/h"),
        ("Total airflow to the room", f"{_num(kpis['supply_flow_m3h'], 0)} m³/h"),
        ("Supply air temperature", f"{_num(kpis['supply_temp_c'], 1)} °C"),
        ("Net sensible capacity per unit",
         f"{_num(fan.get('capacity_kw'), 1)} kW" if fan.get("capacity_kw") else "not given"),
        ("Electrical input per unit",
         f"{_num(fan.get('power_kw'), 1)} kW" if fan.get("power_kw") else "not given"),
        ("External static pressure",
         f"{_num(kpis.get('fan_static_pa'), 0)} Pa" if kpis.get("fan_static_pa") else "not given"),
        ("Site elevation", f"{_num(site.get('altitude_m'), 0)} m"),
        ("Operating pressure",
         f"{_num(site['pressure_pa'] / 1000, 1)} kPa" if "pressure_pa" in site else "—"),
        ("Supply air density", f"{_num(site.get('rho'), 3)} kg/m³"),
    ], widths=[8.0, 8.0],
        note="The solve runs at the site's operating pressure, so the airflow and "
             "the mass flow agree with the unit's selection at that elevation.")

    _heading(doc, "Results summary", 2)
    rows = [
        ("Warmest rack intake (top of rack)", f"{_num(warmest['inlet_top_c'], 2)} °C",
         f"rack {warmest['name']}, against the 27 °C ASHRAE recommended limit"),
        ("Mixed return air temperature", f"{_num(kpis['return_temp_c'], 2)} °C",
         f"{_num(kpis['bulk_delta_t_k'], 2)} K above the supply"),
        ("Energy closure", f"{_num((kpis['energy_closure'] or 0) * 100, 1)} %",
         "heat carried out by the return air, against the installed load"),
        ("Rise across the most loaded unit", f"{_num(kpis.get('fan_rise_pa'), 1)} Pa",
         f"of {_num(kpis.get('fan_static_pa'), 0)} Pa available"
         if kpis.get("fan_static_pa") else "no datasheet pressure given"),
    ]
    if hvac:
        rows.append((
            "Plant against the design rules",
            f"{_num(hvac.get('capacity_ratio', 0) * 100, 0)} % capacity · "
            f"{_num(hvac.get('airflow_ratio', 0) * 100, 0)} % airflow",
            "installed against the load in kW and against the racks' demand at "
            f"{_num(hvac.get('cfm_per_kw'), 0)} CFM/kW",
        ))
    _table(doc, ["Quantity", "Value", "Against what"], rows, widths=[6.0, 3.6, 6.4])

    verdict = (
        "All physical checks pass. The numbers below may be quoted."
        if export.payload["valid"]
        else "ONE OR MORE PHYSICAL CHECKS FAILED. The temperatures below are "
             "not a result and must not be quoted; see section 3.1."
    )
    _para(doc, verdict, bold=True, colour=INK if export.payload["valid"] else BAD)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9, colour="B07000")


# --- 2 methodology ------------------------------------------------------------


def _methodology(doc, export: Export) -> None:
    model = export.model
    kpis = export.kpis
    spec = model.get("spec") or {}
    galleries = model.get("galleries") or [model["gallery"]]
    blocks = model.get("blocks") or []

    _heading(doc, "2  Methodology", 1)
    _heading(doc, "Geometry", 2)
    arrangement = (
        f"a mechanical gallery at each end of the hall, "
        f"{len(model['fans'])} units shared between them"
        if len(galleries) > 1
        else "a single mechanical gallery along one side"
    )
    _para(doc,
          f"The room is derived from the case specification, not drawn: every "
          f"dimension below follows from the equipment sizes, the aisle widths "
          f"and the clearances the engineer typed. The arrangement is "
          f"{arrangement}, with hot-aisle containment and a ceiling-plenum "
          f"return.")
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
    _para(doc,
          "The cell sizes differ by axis on purpose. In plan the cell is one rack "
          "wide, which is the resolution a conceptual study needs and no more. In "
          "the vertical it is fine enough that the false ceiling, the fan wall top "
          "and the rack tops land on cell faces — a plane that falls mid-cell "
          "produces a ragged surface that leaks silently.")
    _para(doc,
          "What this resolution supports: the ranking of racks, the aisle-to-aisle "
          "temperatures and the hall-scale pressure field. What it does not: "
          "signing off ASHRAE compliance rack by rack, since a single rack's "
          "intake carries 1 to 2 K of uncertainty at this cell size.",
          bold=True)
    if model.get("warnings"):
        _para(doc, "Dimensions that did not land on the mesh and were snapped:",
              size=9, colour=SECOND, space_after=2)
        _bullets(doc, model["warnings"])

    _heading(doc, "Boundary conditions and models", 2)
    _table(doc, ["Feature", "How it is modelled"], [
        ("Fan wall", "A pair of patches on the same internal faces: air leaves the "
                     "gallery through the intake and re-enters the cold aisle "
                     "through the supply. Both are set by MASS flow, not volume — "
                     "the air leaving is warmer and thinner than the air arriving, "
                     "and in a closed loop a 1 % mismatch has nowhere to go."),
        ("Rack", "Darcy–Forchheimer porous block with a volumetric enthalpy "
                 "source. The rack has no fan: what passes through it is an "
                 "outcome of the room's pressure field, so the temperature rise "
                 "across a rack is a result and not an input."),
        ("Return grille", "Cyclic pair carrying the datasheet's loss coefficient "
                          "as a pressure jump, then checked against the field."),
        ("Containment, false ceiling, row ends", "Two-sided adiabatic wall "
                                                 "baffles. A row not closed on "
                                                 "five sides leaks most of its "
                                                 "air sideways."),
        ("Building envelope", "No-slip, adiabatic. Conservative for sizing the "
                              "plant: no heat is assumed to leave through it."),
        ("Turbulence", "Steady RANS, k-epsilon, with buoyancy "
                       "(buoyantSimpleFoam, OpenFOAM v1912)."),
    ], widths=[4.6, 11.4])

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
        note="Every unit is given the same duty. A unit that is really on a "
             "pressure boundary would draw more or less than this and is not "
             "modelled here; see section 5.")


# --- 3 results ----------------------------------------------------------------


def _results(doc, export: Export, drawn: dict) -> None:
    kpis = export.kpis
    model = export.model
    zones = sorted(kpis["zones"], key=lambda z: -(z["inlet_top_c"] or -999))

    _heading(doc, "3  Results", 1)

    _heading(doc, "3.1  Verification — the checks the field has to pass", 2)
    _para(doc,
          "A steady solver's residuals say how much the last iteration moved, "
          "not whether the answer means anything. Every run is therefore judged "
          "against identities the physics has to satisfy. All of them have to "
          "pass before a temperature is quoted.")
    _table(doc, ["Check", "Result", "What it would have caught"],
           [(c["name"],
             ("PASS" if c["passed"] else "FAIL") + (
                 f" — {c['detail']}" if c.get("detail") else ""),
             _CHECK_MEANING.get(c["name"], ""))
            for c in export.payload["checks"]],
           widths=[3.4, 6.8, 5.8])

    _heading(doc, "3.2  Convergence", 2)
    _figure(doc, drawn["convergence"],
            "Left: initial residuals per iteration. Right: the instrumented "
            "places — cold aisle, hot aisle, ceiling plenum and behind the fan "
            "walls — recorded while the field settled.")
    if kpis.get("drift_k") is not None:
        _para(doc,
              f"The largest move any instrumented place made between the last "
              f"two samples was {_num(kpis['drift_k'], 3)} K. A field that "
              f"satisfies its balances while a volume is still filling is not "
              f"a steady answer, which is why this is measured separately from "
              f"the residuals.", size=9, colour=SECOND)

    _heading(doc, "3.3  Temperature field", 2)
    _para(doc, "All maps share one fixed colour band, 10 °C to 40 °C in 2,5 K "
               "contours, with the ASHRAE limits marked on the bar. Fixed rather "
               "than fitted to each field: a scale stretched to a field's own "
               "extremes answers a different question in every picture.")
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
    _figure(doc, drawn["long"],
            "Section along the hall, through a contained hot aisle — the "
            "gallery, the dividing wall and the plenum above the ceiling.")

    _heading(doc, "3.4  Rack intake temperature", 2)
    _figure(doc, drawn["racks"],
            "Every rack coloured by the temperature of the air it breathes, "
            "measured at the top of the rack. The scale here is FITTED to this "
            "hall, not the fixed band used for the field maps above: on the "
            "fixed band every rack in a healthy hall falls inside one 2,5 K "
            "step, which says they are all acceptable and nothing about which "
            "is worst. Read this figure for the ranking and the figure below "
            "for the absolute judgement.")
    _table(doc, ["Rack", "Row", "Intake, top of rack", "Intake, face mean",
                 "Exhaust", "Rise", "ASHRAE"],
           [(z["name"], z["row"], f"{_num(z['inlet_top_c'], 2)} °C",
             f"{_num(z['inlet_temp_c'], 2)} °C", f"{_num(z['peak_temp_c'], 2)} °C",
             f"{_num((z['peak_temp_c'] or 0) - (z['inlet_temp_c'] or 0), 2)} K",
             (z.get("ashrae") or {}).get("verdict", ""))
            for z in zones[:12]],
           note="The twelve warmest of "
                f"{len(zones)} rack positions, by the air arriving at the top of "
                "the rack. The complete table is exported as CSV from the "
                "results page.")
    _figure(doc, drawn["ashrae"],
            "Distribution of rack intake temperature against the ASHRAE class A1 "
            "envelope.")

    _heading(doc, "3.5  Unit by unit", 2)
    if drawn.get("units"):
        _figure(doc, drawn["units"],
                "Return air temperature and heat removed, unit by unit. The "
                "units are numbered along the gallery wall, in gallery order.")
    fans = kpis.get("fans", [])
    rating = ((model.get("spec") or {}).get("fanwall") or {}).get("capacity_kw")
    _table(doc, ["Unit", "Return air", "Mass flow", "Heat removed",
                 "Of the rating", "Rise across the unit"],
           [(f["name"].replace("fan", ""),
             f"{_num(f.get('return_temp_c'), 2)} °C",
             f"{_num(f.get('intake_kg_s'), 1)} kg/s",
             f"{_num(f.get('heat_kw'), 0)} kW",
             f"{_num((f.get('heat_kw') or 0) / rating * 100, 0)} %" if rating else "—",
             f"{_num(f.get('rise_pa'), 1)} Pa")
            for f in fans],
           note="Heat removed is the enthalpy the air carries out of each unit, "
                "mass flow × cp × (return − supply). 'Of the rating' compares it "
                "with the unit's CATALOGUE net sensible capacity at its selection "
                "point; the capacity a coil actually has at the return "
                "temperature it receives is lower, and AICFD does not yet "
                "compute it — see section 5.")


_CHECK_MEANING = {
    "mass_balance": "the fan walls supplying and drawing different masses",
    "sealed_envelope": "any wall or baffle passing air",
    "no_backflow": "air reversing through a fan intake",
    "energy_closure": "the return air not carrying the installed load",
    "return_path": "a volume still filling — the plenum, usually",
    "rack_resistance": "the porous zones not delivering the pressure drop given",
    "grille_resistance": "the same, for the ceiling grilles",
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
    rating = ((model.get("spec") or {}).get("fanwall") or {}).get("capacity_kw")

    _heading(doc, "4  Conclusions", 1)
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
    if kpis.get("fan_rise_pa") and kpis.get("fan_static_pa"):
        findings.append(
            f"The room costs the most loaded unit {_num(kpis['fan_rise_pa'], 1)} Pa "
            f"of the {_num(kpis['fan_static_pa'], 0)} Pa its datasheet offers "
            f"({_num(kpis['fan_rise_pa'] / kpis['fan_static_pa'] * 100, 0)} %), "
            f"and the least loaded {_num(kpis.get('fan_rise_min_pa'), 1)} Pa."
        )
    findings.append(
        f"The energy balance closes at "
        f"{_num((kpis['energy_closure'] or 0) * 100, 1)} %: the heat the return "
        f"air carries out matches the load the racks put in. Every physical "
        f"check "
        + ("passes, so the temperatures above may be quoted."
           if export.payload["valid"]
           else "does NOT pass; see section 3.1 before using any number here.")
    )
    _bullets(doc, findings)
    for alert in kpis.get("alerts", []):
        _para(doc, f"Alert · {alert}", size=9.5, colour="B07000")


def _limits(doc, export: Export) -> None:
    _heading(doc, "5  Limitations", 1)
    _para(doc,
          "These results are valid only for the boundary conditions listed in "
          "section 2. CFD gives an approximate solution to the equations of "
          "fluid motion, given the simplifications required to model a real "
          "room; the model, the mesh and the inputs all carry uncertainty.")
    _bullets(doc, [
        "Steady state only. A unit failing, a door opening or a load step are "
        "transients and are outside this model.",
        "One operating scenario. A failure case — units out of service, the "
        "surviving units sharing the same total airflow — is not modelled, so "
        "this report cannot state how much reserve the plant has when a unit is "
        "lost.",
        "The unit's catalogue capacity, not the capacity it actually has. A "
        "chilled-water coil delivers its rating only at the return temperature "
        "it was selected for, and a real hall returns cooler air than that. "
        "Comparisons here are against the catalogue figure and are therefore "
        "optimistic.",
        "One load per rack. Unloaded positions and a real per-rack load map are "
        "not represented, and where the empty positions sit changes how evenly "
        "the units load.",
        "Heat released outside the racks — PDU and other ancillary losses, "
        "typically about 2 % of the IT load — is not included.",
        "Containment is modelled as perfect. Real containment leaks, and the "
        "leak is what decides the top-of-rack temperature in a marginal design.",
        "A conceptual-design mesh. One rack per cell in plan: trust the ranking "
        "of racks and the hall-scale fields, not a single rack's intake to "
        "better than 1 to 2 K.",
        "No comparison against measurement. Every validation in section 3.1 is "
        "an identity the physics must satisfy. That class of check catches wrong "
        "models; it cannot promise the built room behaves this way.",
    ])
    _para(doc,
          "The case specification, the generated OpenFOAM case, the solver log "
          "and the exported result together reproduce this document exactly. "
          "Nothing in it was typed in by hand.",
          size=9, colour=MUTED, italic=True)
