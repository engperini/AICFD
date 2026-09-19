"""The report's figures: plan, sections and charts, drawn from an export.

Everything here reads `results/<case>/viewer.json` and `fields.bin` and
nothing else -- the same two files the page reads. A figure in the Word
deliverable and a view on the page are therefore two renderings of one
export, and a number in the document can be found again on screen.

No 3-D. A perspective view of a data hall shows which way the room faces and
hides everything else behind a surface; a plan and two sections, drawn to
scale on a fixed colour band with the ASHRAE limits marked, answer the
questions a reader actually has and can be measured off the page (ADR-024,
ADR-025).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from aicfd import palette

#: Figure ink. The page's light-surface tokens, so the document and the screen
#: are recognisably the same tool.
INK = "#0b0b0b"
SECOND = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
RACK_EDGE = "#6f6e6a"
FAN = "#2a78d6"
GOOD = "#0ca30c"
WARN = "#fab219"
BAD = "#d03b3b"

DPI = 200


def _pyplot():
    """matplotlib, configured once, imported late.

    Late because the tool's core -- model, case, solve, checks -- must import
    and run without it. A plotting library is part of the *deliverable*, not
    part of the physics.
    """
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.edgecolor": MUTED,
            "axes.labelcolor": SECOND,
            "axes.titlesize": 9,
            "axes.titleweight": "bold",
            "axes.titlecolor": INK,
            "text.color": INK,
            "xtick.color": SECOND,
            "ytick.color": SECOND,
            "figure.facecolor": "white",
            "savefig.facecolor": "white",
            "axes.grid": False,
            "axes.prop_cycle": __import__("cycler").cycler(
                color=list(palette.SERIES)
            ),
        }
    )
    return plt


# --- the export ---------------------------------------------------------------


class Export:
    """A solved result, as the page sees it."""

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.payload = json.loads((self.dir / "viewer.json").read_text())
        self.model = self.payload["model"]
        grid = self.payload["grid"]
        self.divisions = tuple(grid["divisions"])
        self.origin = tuple(grid["origin"])
        self.size = tuple(grid["size"])
        raw = np.fromfile(self.dir / "fields.bin", dtype=np.float32)
        nx, ny, nz = self.divisions
        self.fields = {}
        for descriptor in self.payload["fields"]:
            start = descriptor["offset"] // 4
            block = raw[start : start + descriptor["count"]]
            self.fields[descriptor["name"]] = block.reshape((nz, ny, nx))
        self._equipment = False  # not looked up yet; None means "no unit"
        self.fields["speed"] = np.sqrt(
            self.fields["Ux"] ** 2 + self.fields["Uy"] ** 2 + self.fields["Uz"] ** 2
        )

    @property
    def cell(self) -> tuple[float, float, float]:
        return tuple(s / n for s, n in zip(self.size, self.divisions))

    def axis(self, a: int) -> np.ndarray:
        """Cell-centre coordinates along one axis."""
        n = self.divisions[a]
        step = self.size[a] / n
        return self.origin[a] + (np.arange(n) + 0.5) * step

    def edges(self, a: int) -> np.ndarray:
        n = self.divisions[a]
        step = self.size[a] / n
        return self.origin[a] + np.arange(n + 1) * step

    def slice(self, field: str, normal: int, at: float) -> np.ndarray:
        """The field on the plane ``normal = at``, as [v, h] for that view."""
        index = int(np.argmin(abs(self.axis(normal) - at)))
        data = self.fields[field]  # [k, j, i] = [z, y, x]
        if normal == 0:
            return data[:, :, index]  # [z, y]
        if normal == 1:
            return data[:, index, :]  # [z, x]
        return data[index, :, :]  # [y, x]

    @property
    def kpis(self) -> dict:
        return self.payload["kpis"]

    @property
    def racks(self) -> list[dict]:
        return self.payload["geometry"]["zones"]

    @property
    def equipment(self):
        """The unit this run was judged against, as the library holds it now.

        Read at report time rather than frozen into the export, because the
        fields it supplies -- who makes the machine, what it is called, the
        conditions it was selected at -- are the ones a person corrects on the
        equipment page, and a report is expected to carry the corrected name.
        The numbers that decided the result are in the KPIs either way.

        None when the run named no unit, which every result exported before
        the library existed did.
        """
        if self._equipment is not False:
            return self._equipment
        from aicfd import equipment as library

        name = (self.kpis or {}).get("unit_model")
        try:
            self._equipment = library.load(name) if name else None
        except (library.UnknownModel, ValueError):
            self._equipment = None  # renamed or removed since the run
        return self._equipment

    def panels(self, *prefixes: str) -> list[dict]:
        return [
            p
            for p in self.payload["geometry"]["panels"]
            if not prefixes or p["name"].startswith(prefixes)
        ]


# --- field maps ---------------------------------------------------------------


def _temperature_mesh(plt, ax, x_edges, y_edges, values):
    """Paint a temperature field in contour bands on the fixed scale."""
    from matplotlib.colors import BoundaryNorm, ListedColormap

    scale = palette.temperature_scale()
    # Air outside the fixed band saturates at the end of the ramp rather than
    # dropping out of the picture; the bar's arrowheads say so.
    cmap = ListedColormap(scale["colours"]).with_extremes(
        under=scale["colours"][0], over=scale["colours"][-1]
    )
    norm = BoundaryNorm(scale["edges"], cmap.N, clip=True)
    return ax.pcolormesh(x_edges, y_edges, values, cmap=cmap, norm=norm, shading="flat")


def _map_figure(plt, width_in: float, height_in: float):
    """A map axes with a colourbar axes of its own beneath it.

    The bar gets its own row rather than being anchored to the map, because a
    map drawn to equal aspect can end up any height at all, and a bar attached
    to a wide shallow one lands on top of its axis labels.
    """
    fig = plt.figure(figsize=(width_in, height_in + 1.05), layout="constrained")
    fig.get_layout_engine().set(h_pad=0.02, w_pad=0.02, hspace=0.02)
    grid = fig.add_gridspec(2, 1, height_ratios=[max(1.0, height_in / 0.26), 1.0])
    return fig, fig.add_subplot(grid[0]), fig.add_subplot(grid[1])


def _temperature_bar(plt, fig, mesh, cax, label="Air temperature (°C)",
                     scale=None, note=None):
    """The colourbar, with the ASHRAE limits drawn on it.

    The limits go on the bar rather than in the caption because that is where
    the judgement happens: the reader is asking "is this air acceptable", and
    the answer is a position on this bar.
    """
    scale = scale or palette.temperature_scale()
    bar = fig.colorbar(mesh, cax=cax, orientation="horizontal", extend="both")
    every = 2 if len(scale["edges"]) > 10 else 1
    bar.set_ticks(scale["edges"][::every])
    bar.ax.tick_params(labelsize=7, color=MUTED)
    bar.outline.set_edgecolor(MUTED)
    for mark in scale["marks"]:
        bar.ax.axvline(mark, color=INK, linewidth=1.1)
        bar.ax.annotate(
            f"{mark:g}", xy=(mark, 1.0), xycoords=("data", "axes fraction"),
            xytext=(0, 2), textcoords="offset points",
            ha="center", va="bottom", fontsize=6.5, color=INK,
        )
    if note is None:
        note = "ASHRAE recommended 18–27 °C, allowable A1 to 32 °C"
    bar.ax.set_xlabel(f"{label}    ·    {note}", color=SECOND, fontsize=7.5,
                      labelpad=4)
    return bar


def _outline(ax, lo, hi, h, v, **kwargs):
    from matplotlib.patches import Rectangle

    ax.add_patch(
        Rectangle((lo[h], lo[v]), hi[h] - lo[h], hi[v] - lo[v], fill=False, **kwargs)
    )


def plan(export: Export, out: Path, z: float, title: str) -> Path:
    """Temperature in plan at height ``z``, with the room drawn over it.

    The plan turns when the room is longer across than along, for the same
    reason a drawing office turns a sheet: a 21 x 88 m hall printed with its
    long axis up the page is a strip nobody can read. Turning it swaps which
    room axis runs across the page and nothing else -- the labels say which is
    which.
    """
    plt = _pyplot()
    model = export.model
    values = export.slice("T", 2, z)  # [y, x]
    width, depth = export.size[0], export.size[1]
    turned = depth > 1.25 * width
    h, v = (1, 0) if turned else (0, 1)
    if turned:
        values = values.T  # [y, x] -> [x, y]
    span, rise = export.size[h], export.size[v]

    fig, ax, cax = _map_figure(plt, 7.2, min(8.4, 7.2 * rise / span))
    mesh = _temperature_mesh(plt, ax, export.edges(h), export.edges(v), values)

    for rack in export.racks:
        _outline(ax, rack["lo"], rack["hi"], h, v,
                 edgecolor=RACK_EDGE, linewidth=0.25)
    for panel in export.panels("containment_wall", "containment_roofwall",
                               "containment_door"):
        ax.plot([panel["lo"][h], panel["hi"][h]], [panel["lo"][v], panel["hi"][v]],
                color=INK, linewidth=0.7)
    fans = export.panels("fan")
    for x in model.get("dividers", [model["hall"]["lo"][0]]):
        line = ([0, span], [x, x]) if turned else ([x, x], [0, rise])
        ax.plot(*line, color=INK, linewidth=1.4)
    for i, panel in enumerate(fans):
        at, lo, hi = panel["position"], panel["lo"][1], panel["hi"][1]
        line = ([lo, hi], [at, at]) if turned else ([at, at], [lo, hi])
        ax.plot(*line, color=FAN, linewidth=3.2, solid_capstyle="butt")
        if len(fans) <= 20:
            away = -9 * panel.get("sign", 1)  # into the gallery, not the hall
            ax.annotate(
                f"{i + 1:02d}",
                xy=((lo + hi) / 2, at) if turned else (at, (lo + hi) / 2),
                xytext=(0, away) if turned else (away, 0),
                textcoords="offset points", fontsize=5.5, color=SECOND,
                ha="center" if turned else ("left" if away > 0 else "right"),
                va=("bottom" if away > 0 else "top") if turned else "center",
            )

    labels = ("x — along the hall (m)", "y — across the hall (m)")
    ax.set_xlim(0, span)
    ax.set_ylim(0, rise)
    ax.set_aspect("equal")
    ax.set_xlabel(labels[h])
    ax.set_ylabel(labels[v])
    ax.set_title(title, loc="left")
    _temperature_bar(plt, fig, mesh, cax)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def section(export: Export, out: Path, normal: int, at: float, title: str,
            xlabel: str) -> Path:
    """A vertical section: normal 0 looks along the hall, normal 1 across it."""
    plt = _pyplot()
    h = 1 if normal == 0 else 0
    values = export.slice("T", normal, at)  # [z, h]
    span, height = export.size[h], export.size[2]
    fig, ax, cax = _map_figure(plt, 7.2, max(1.3, 7.2 * height / span))
    mesh = _temperature_mesh(plt, ax, export.edges(h), export.edges(2), values)

    for rack in export.racks:
        if rack["lo"][normal] - 1e-6 <= at <= rack["hi"][normal] + 1e-6:
            _outline(ax, rack["lo"], rack["hi"], h, 2,
                     edgecolor=RACK_EDGE, linewidth=0.3)
    ax.plot([export.model["hall"]["lo"][0] if h == 0 else 0,
             export.model["hall"]["hi"][0] if h == 0 else span],
            [export.model["ceiling_z"]] * 2, color=INK, linewidth=1.2)
    if h == 1:
        for x in export.model.get("dividers", []):
            if abs(x - at) < 1e-6:
                pass
    for panel in export.panels("fan"):
        if normal == 0 and abs(panel["position"] - at) > 1e-6:
            continue
        lo, hi = (panel["lo"][h], panel["hi"][h])
        ax.add_patch(
            __import__("matplotlib.patches", fromlist=["Rectangle"]).Rectangle(
                (lo, panel["lo"][2]), hi - lo, panel["hi"][2] - panel["lo"][2],
                fill=False, edgecolor=FAN, linewidth=1.0)
        )
    ax.set_xlim(0, span)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.set_xlabel(xlabel)
    ax.set_ylabel("z — height (m)")
    ax.set_title(title, loc="left")
    _temperature_bar(plt, fig, mesh, cax)
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


# --- per-rack and per-unit ----------------------------------------------------


def rack_map(export: Export, out: Path, metric: str = "inlet_top_c") -> Path:
    """Every rack in plan, coloured by the air it breathes.

    The number the room is judged on, one value per rack, on the same fixed
    band as the field maps -- so a rack that reads warm here reads warm there.
    """
    plt = _pyplot()
    from matplotlib.patches import Rectangle

    by_id = {z["name"]: z for z in export.kpis["zones"]}
    # Fitted, not the fixed band: every rack in a healthy hall falls inside
    # one 2,5 K band of the field scale, and painting them against it would
    # say only that they are all fine. This figure exists to rank them.
    scale = palette.fitted_scale([z.get(metric) for z in export.kpis["zones"]])
    colours = palette.lut("sequential")
    width, depth = export.size[0], export.size[1]
    turned = depth > 1.25 * width
    h, v = (1, 0) if turned else (0, 1)
    span, rise = export.size[h], export.size[v]
    fig, ax, cax = _map_figure(plt, 7.2, min(8.4, 7.2 * rise / span))
    values = []
    for rack in export.racks:
        value = by_id.get(rack["name"], {}).get(metric)
        if value is None:
            continue
        values.append(value)
        t = palette.position(value, scale["min"], scale["max"], scale["center"],
                             scale["step"])
        rgb = colours[min(len(colours) - 1, int(round(t * (len(colours) - 1))))]
        ax.add_patch(
            Rectangle(
                (rack["lo"][h], rack["lo"][v]),
                rack["hi"][h] - rack["lo"][h],
                rack["hi"][v] - rack["lo"][v],
                facecolor=tuple(c / 255 for c in rgb),
                edgecolor="white", linewidth=0.15,
            )
        )
    for x in export.model.get("dividers", []):
        line = ([0, span], [x, x]) if turned else ([x, x], [0, rise])
        ax.plot(*line, color=INK, linewidth=1.2)
    labels = ("x — along the hall (m)", "y — across the hall (m)")
    ax.set_xlim(0, span)
    ax.set_ylim(0, rise)
    ax.set_aspect("equal")
    ax.set_xlabel(labels[h])
    ax.set_ylabel(labels[v])
    ax.set_title("Rack intake temperature, top of rack", loc="left")

    from matplotlib.cm import ScalarMappable
    from matplotlib.colors import BoundaryNorm, ListedColormap

    cmap = ListedColormap(scale["colours"])
    mappable = ScalarMappable(BoundaryNorm(scale["edges"], cmap.N, clip=True), cmap)
    mappable.set_array(np.array(values or [0.0]))
    _temperature_bar(
        plt, fig, mappable, cax, "Rack intake temperature (°C)", scale=scale,
        note=f"scale fitted to this hall, {scale['step']:g} K bands"
             + (" · ASHRAE limits marked" if scale["marks"] else
                " · every rack is inside the ASHRAE recommended band"),
    )
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def units(export: Export, out: Path) -> Path:
    """Return temperature and heat removed, unit by unit.

    Two panels because they answer different questions and the reference study
    this format follows found them close to opposite: the units returning the
    coldest air were the ones nearest their capacity, because they were moving
    the most air. A plant watched on return temperature alone would have
    called those two the least loaded.
    """
    plt = _pyplot()
    fans = [f for f in export.kpis.get("fans", [])]
    if not any(f.get("return_temp_c") is not None for f in fans):
        return None  # an export written before per-unit returns were measured
    names = [f["name"].replace("fan", "") for f in fans]
    index = np.arange(len(fans))
    rating = (export.model.get("operating") or {}).get("unit_capacity_kw")
    sides = export.model.get("fan_sides") or []
    fig, axes = plt.subplots(2, 1, figsize=(7.2, 4.6), sharex=True)

    returns = [f.get("return_temp_c") for f in fans]
    axes[0].plot(index, returns, marker="o", markersize=3.5, linewidth=1.2, color=INK)
    axes[0].set_ylabel("Return air (°C)")
    axes[0].set_title("Return air temperature at each unit", loc="left")
    if any(v is not None for v in returns):
        finite = [v for v in returns if v is not None]
        axes[0].set_ylim(min(finite) - 1.0, max(finite) + 1.0)

    heat = [f.get("heat_kw") or 0.0 for f in fans]
    axes[1].bar(index, heat, width=0.68,
                color=[BAD if rating and v > rating else "#2f6f6a" for v in heat])
    if rating:
        axes[1].axhline(rating, color=INK, linewidth=1.0, linestyle="--")
        axes[1].annotate(f"{rating:g} kW rating", xy=(len(fans) - 0.5, rating),
                         xytext=(0, 3), textcoords="offset points",
                         ha="right", va="bottom", fontsize=6.5, color=SECOND)
    axes[1].set_ylabel("Heat removed (kW)")
    axes[1].set_title("Heat removed by each unit", loc="left")
    axes[1].set_xticks(index)
    axes[1].set_xticklabels(names, fontsize=6.5)
    axes[1].set_xlabel("fan wall")
    # Where the units change gallery, because that is the grouping a reader
    # needs to see: each gallery serves the rack blocks at its own end.
    breaks = [i for i in range(1, len(sides)) if sides[i] != sides[i - 1]]
    for ax in axes:
        for i in breaks:
            ax.axvline(i - 0.5, color=MUTED, linewidth=0.8, linestyle=":")
        ax.grid(axis="y", color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    if breaks:
        for start, end, label in zip([0] + breaks, breaks + [len(sides)],
                                     ("first gallery", "second gallery")):
            axes[0].annotate(label, xy=((start + end - 1) / 2, 0.97),
                             xycoords=("data", "axes fraction"),
                             ha="center", va="top", fontsize=6.5, color=MUTED)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def ashrae(export: Export, out: Path) -> Path:
    """Where every rack's intake sits against the ASHRAE envelope."""
    plt = _pyplot()
    zones = export.kpis["zones"]
    values = [z["inlet_top_c"] for z in zones if z.get("inlet_top_c") is not None]
    fig, ax = plt.subplots(figsize=(7.2, 2.2))
    ax.axvspan(15, 32, color="#f0efec", label="allowable A1 15–32 °C")
    ax.axvspan(18, 27, color="#dfe7e2", label="recommended 18–27 °C")
    ax.hist(values, bins=24, color="#2f6f6a", edgecolor="white", linewidth=0.4)
    ax.axvline(max(values), color=BAD, linewidth=1.2)
    ax.annotate(f"warmest {max(values):.2f} °C", xy=(max(values), 0),
                xytext=(6, 22), textcoords="offset points",
                fontsize=7, color=BAD, ha="left")
    ax.set_xlim(14, 34)
    ax.set_xlabel("Rack intake air temperature (°C)")
    ax.set_ylabel("racks")
    ax.set_title("Rack intake against the ASHRAE envelope", loc="left")
    ax.legend(frameon=False, fontsize=6.5, loc="upper left")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def convergence(export: Export, out: Path) -> Path:
    """Residuals, and the places that were watched while they fell.

    Both, because neither alone settles the question: residuals say how much
    the last iteration moved, and the monitors say whether the answer stopped
    moving (ADR-018).
    """
    plt = _pyplot()
    residuals = export.payload.get("residuals", {})
    history = export.payload.get("sensors", {})
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 2.6))

    iterations = residuals.get("iterations", [])
    # Fixed order, so a residual keeps its colour between runs and between the
    # page and this figure.
    order = ["Ux", "Uy", "Uz", "h", "p_rgh", "k", "epsilon"]
    logged = residuals.get("series") or {}
    names = [n for n in order if n in logged] + [n for n in logged if n not in order]
    for name in names:
        series = logged[name]
        axes[0].semilogy(iterations[: len(series)],
                         [v if v and v > 0 else np.nan for v in series],
                         linewidth=1.0, label=name)
    axes[0].set_xlabel("iteration")
    axes[0].set_ylabel("initial residual")
    axes[0].set_title("Residuals", loc="left")
    axes[0].legend(frameon=False, fontsize=6, ncol=2)

    for place in history.get("groups", []):
        axes[1].plot(history.get("iterations", []), place.get("temp_c", []),
                     linewidth=1.2, label=place.get("label", place.get("name")))
    axes[1].set_xlabel("iteration")
    axes[1].set_ylabel("temperature (°C)")
    axes[1].set_title("Instrumented places", loc="left")
    axes[1].legend(frameon=False, fontsize=6)
    for ax in axes:
        ax.grid(color=GRID, linewidth=0.6)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def capacity(export: Export, out: Path) -> Path | None:
    """The unit's capacity against the air it receives, with this hall on it.

    The point of the figure is the slope. A reader who has only seen the
    datasheet believes the machine has one capacity; the curve shows it moving
    by tens of per cent across a few kelvin, and the marker shows which part
    of it this hall actually asked for. Everything the report says about
    margin is read off this line (ADR-036).
    """
    unit = export.equipment
    if unit is None:
        return None
    plt = _pyplot()
    rows = list(unit.capacity)
    temps = [r["return_c"] for r in rows]
    kw = [r["nscc_kw"] for r in rows]

    fig, ax = plt.subplots(figsize=(7.2, 3.0))
    # The manufacturer's rows are REFERENCE, drawn small and muted: they are a
    # sizing exercise -- the water flow was re-sized at every one of them -- and
    # reading them as an operating curve is the mistake this figure exists to
    # prevent. The line that carries the report's numbers is the modelled one
    # below (ADR-039, ADR-040).
    ax.plot(temps, kw, color=MUTED, linewidth=0.9, marker="o", markersize=3.0,
            markerfacecolor="white", markeredgecolor=MUTED, markeredgewidth=0.9,
            label="manufacturer's sizing selections (reference)", zorder=3)
    # Zero-based, because this is a magnitude: a truncated axis would make a
    # 45 % rise look like a tenfold one.
    ax.set_ylim(0, max(kw) * 1.18)
    fans = export.kpis.get("fans") or []

    # The line the report's numbers are actually read off: the same coil at
    # the air flow THIS hall gives it, which is never the air flow the
    # selections were taken at. The gap between the two lines is the reason a
    # capacity cannot be looked up (ADR-039).
    modelled = _modelled_curve(export, unit, fans, temps)
    if modelled:
        xs, ys, note = modelled
        ax.plot(xs, ys, color=FAN, linewidth=2.0, label=note, zorder=2)
    # Returns the table does not cover still belong on the axis. A figure that
    # silently cropped them would hide the one thing the reader has to know:
    # this hall ran off the end of the machine's characterisation (ADR-036).
    off = [t for t in (export.kpis.get("coil_outside_table_c") or [])]
    # Every unit's own return belongs on the axis whether or not the
    # selections reach it. A figure cropped to the table would hide the one
    # thing the reader has to see: where this hall actually sat (ADR-039).
    here = [f["return_temp_c"] for f in fans if f.get("return_temp_c") is not None]
    left = min([*temps, *off, *here]) - 0.4
    right = max([*temps, *off, *here]) + 0.4
    ax.set_xlim(left, right)

    rating = (export.model.get("operating") or {}).get("unit_capacity_kw")
    if rating:
        ax.axhline(rating, color=MUTED, linewidth=1.0, linestyle=(0, (4, 3)),
                   zorder=1)
        # Labelled at the right-hand end, where the curve has climbed well
        # clear of the line and the text has room.
        ax.annotate(f"catalogue figure quoted in the spec, {rating:,.0f} kW",
                    xy=(right, rating), xytext=(-2, 5), ha="right",
                    textcoords="offset points", fontsize=6.5, color=SECOND)

    if off:
        # Shade whichever side of the table the hall actually fell off. Both
        # are possible -- a hall can return air colder than the coldest
        # selection or hotter than the warmest -- and saying the wrong one
        # would be worse than saying nothing.
        below = [v for v in off if v < min(temps)]
        above = [v for v in off if v > max(temps)]
        if below:
            ax.axvspan(left, min(temps), color="#f4ecec", zorder=0)
            ax.axvline(min(temps), color=BAD, linewidth=1.0,
                       linestyle=(0, (3, 2)), zorder=2)
        if above:
            ax.axvspan(max(temps), right, color="#f4ecec", zorder=0)
            ax.axvline(max(temps), color=BAD, linewidth=1.0,
                       linestyle=(0, (3, 2)), zorder=2)
        for temperature in off:
            ax.axvline(temperature, color=BAD, linewidth=0.6, alpha=0.5,
                       zorder=2)
        side = ("below" if below and not above
                else "above" if above and not below
                else "outside")
        ax.annotate(
            f"this hall returned {min(off):.1f}–{max(off):.1f} °C, {side} the "
            f"selections — capacity not read",
            xy=(min(temps) if below else left, max(kw) * 1.06),
            xytext=(5, 0), ha="left",
            textcoords="offset points", fontsize=6.5, color=BAD)

    operating = [(f.get("return_temp_c"), f.get("available_kw"))
                 for f in fans
                 if f.get("return_temp_c") is not None and f.get("available_kw")]
    if operating:
        ax.scatter([t for t, _ in operating], [c for _, c in operating],
                   s=26, color=BAD, edgecolor="white", linewidth=0.8, zorder=4,
                   label="this hall, unit by unit")
        warmest = max(operating)
        ax.annotate(f"{warmest[1]:,.0f} kW at {warmest[0]:.1f} °C",
                    xy=warmest, xytext=(6, -12), textcoords="offset points",
                    fontsize=7, color=BAD)

    ax.set_xlabel("Return air temperature at the unit (°C)")
    ax.set_ylabel("Net sensible capacity (kW)")
    ax.set_title(f"{unit.family} {unit.model} — capacity against return air",
                 loc="left")
    ax.legend(frameon=False, fontsize=6.5, loc="lower right")
    ax.grid(axis="y", color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    fig.savefig(out, dpi=DPI, bbox_inches="tight")
    plt.close(fig)
    return out


def _modelled_curve(export: Export, unit, fans: list, selection_temps: list):
    """The coil's ceiling against return air, at this hall's own air flow.

    Returns (temperatures, kW, legend) or None where no coil could be fitted.
    Drawn across the whole range the figure shows -- including past the
    selections -- because that is where the hall actually sits and the model
    is the only thing that can say what happens there.
    """
    coil = getattr(unit, "coil", None)
    if coil is None or not fans:
        return None
    flows = [f["intake_kg_s"] for f in fans if f.get("intake_kg_s")]
    if not flows:
        return None
    air = sum(flows) / len(flows) * 1.005  # kW/K, the mass the solve moved
    returns = [f["return_temp_c"] for f in fans if f.get("return_temp_c") is not None]
    low = min([*selection_temps, *returns]) - 0.4
    high = max([*selection_temps, *returns]) + 0.4
    xs = [low + (high - low) * i / 60 for i in range(61)]
    ys = [coil.operate(x, air).ceiling_kw for x in xs]
    share = air / coil.air_fitted
    return xs, ys, (f"this unit's coil, modelled, at the air flow this hall "
                    f"gives it ({share:.0%})")
