"""The export text an engineer reads before running: source, grid, every
quantisation, every correction, counts, heights, omissions, questions."""
from __future__ import annotations


def write(m: dict, counts: dict, path: str, source_file: str, fails: list[str]) -> str:
    H = m["heights"]
    log = m["log"]
    n_racks = sum(len(r["racks"]) for r in m["rows"])
    lines = []
    A = lines.append
    A(f"AICFD geometry export -- {m['name']}")
    A("=" * 72)
    A(f"source        {source_file}")
    if m["source"]:
        A(f"drawing       {m['source']}")
    A(f"grid          every coordinate on {m['cell'][0]} x {m['cell'][1]} x {m['cell'][2]} m")
    A(f"origin        drawing ({m['origin'][0]:.3f}, {m['origin'][1]:.3f}) is the model's (0, 0); z = 0 on the slab under the plenum"
      + ("; the drawing was transposed (x <-> y)" if m["transposed"] else ""))
    A("")
    A("HEIGHTS (m over the slab)")
    A(f"  {'deck ' + format(H['deck'], '.2f') if m.get('has_deck', True) else 'no raised floor'}   rack top {H['rack_top']:.2f}   unit top {H['unit_top']:.2f}   false ceiling {H['ceiling']:.2f}   slab {H['slab']:.2f}")
    A(f"  containment: {m.get('containment', 'cold').upper()} aisle; units: {m.get('unit_kind', 'downflow')}")
    conf = set(m.get("heights_confirmed", []))
    A("  " + ", ".join(f"{k} {m['config']['heights'][k]} m ({'confirmed' if k in conf else 'REFERENCE, unconfirmed'})"
                      for k in ("plenum", "rack", "unit", "ceiling", "hall")))
    A("")
    A("COUNTS")
    A(f"  envelope {m['envelope'][2]:.1f} x {m['envelope'][3]:.1f} m; hall x {m['hall'][0]:.1f}..{m['hall'][2]:.1f}; "
      + ", ".join(f"gallery {g['id']} {g['rect'][2] - g['rect'][0]:.1f} m" for g in m["galleries"]))
    A(f"  {n_racks} cabinets in {len(m['rows'])} rows ({m['loads']['zero']} at zero load); {m['loads']['total_kw']:.2f} kW to the air"
      + (f" of {m['loads']['it_kw']:.2f} kW IT ({m['loads']['liquid']} liquid-cooled)" if m['loads'].get('liquid') else "")
      + f", {m['loads']['default_kw']} kW where the drawing gives none")
    for r in m["rows"]:
        A(f"    row {r['id']:>3}: {len(r['racks']):2d} cabinets, front {'+y' if r['front'] > 0 else '-y'}, "
          f"x {r['span'][0]:.1f}..{r['span'][1]:.1f}, y {r['band'][0]:.1f}..{r['band'][1]:.1f}, "
          f"{sum(k['load_kw'] for k in r['racks']):.1f} kW")
    A(f"  {len(m['cold_aisles'])} contained cold aisles: " + "; ".join(
        f"{c['id']} {c['band'][1] - c['band'][0]:.1f} m between {'/'.join(c['rows'])}{' (alone)' if c['lone'] else ''}, {c['tiles_across']}x{c['tiles_along']} tiles"
        for c in m["cold_aisles"]))
    if m.get("hot_aisles"):
        A(f"  {len(m['hot_aisles'])} contained hot aisles: " + "; ".join(
            f"{h['id']} {h['band'][1] - h['band'][0]:.1f} m between {'/'.join(h['rows'])}{' (alone)' if h['lone'] else ''}, {h['grilles'].get('count', 0)} grilles in the ceiling"
            for h in m["hot_aisles"]))
    A(f"  {len(m['hot_bands'])} hot bands: " + "; ".join(
        f"{h['id']} {h['band'][1] - h['band'][0]:.1f} m {'between ' + '/'.join(h['rows']) if h['paired'] else 'behind ' + h['rows'][0]}, "
        + f"{h['grilles'].get('count', 0)} grilles (as cold aisle {h['grilles'].get('from')}) against {h['grilles'].get('against')}"
        for h in m["hot_bands"]))
    A(f"  {len(m['units'])} cooling units: " + ", ".join(f"{u['id']} ({u['gallery']})" for u in m["units"]))
    for p in m.get("plenums", []):
        A(f"  supply plenum {p['side']}: {p['depth']:.1f} m between the units' leaf at x = {p['divider_at']:.1f} and the hall's wall at "
          f"x = {p['wall_at']:.1f}; {p.get('grilles', 0)} supply grilles {p.get('grille_size', (0, 0))[0]:g} x {p.get('grille_size', (0, 0))[1]:g} m in the wall ({p.get('how', '')})")
    A(f"  cage: {len(m['cage']['panels'])} panel(s), {m['cage']['construction']}, deck to ceiling")
    A(f"  containment: {len(m['lids'])} lids, {len(m.get('chimneys', []))} chimney panels, {len(m['doors'])} doors, {len(m['closures'])} end panels, {len(m.get('blanks', []))} blank panels on the row lines")
    A("  solids written: " + ", ".join(f"{v} {k}" for k, v in sorted(counts.items())))
    A("")
    alerts = log.get("alerts", [])
    A(f"GRID ALERTS ({len(alerts)}) -- the mesh cell changed the layout; read before running")
    for a in alerts:
        A(f"  ! {a}")
    if not alerts:
        A("  none: every row keeps its drawn length and alignment within the tolerance")
    A("")
    A(f"CORRECTIONS APPLIED ({len(log['corrections'])})")
    for c in log["corrections"]:
        A(f"  - {c}")
    A("")
    A(f"QUANTISATIONS OVER 1 mm ({len(log['quantisations'])})")
    for q in log["quantisations"]:
        A(f"  - {q}")
    A("")
    A(f"LEFT OUT ({len(log['omissions'])})")
    for o in log["omissions"]:
        A(f"  - {o}")
    A("")
    A(f"NOTES ({len(log['notes'])})")
    for n in log["notes"]:
        A(f"  - {n}")
    A("")
    A(f"OPEN QUESTIONS ({len(log['questions'])}) -- answer in answers.yaml and re-run")
    for q in log["questions"]:
        A(f"  - {q}")
    A("")
    A("SELF-CHECK " + ("PASSED" if not fails else f"FAILED ({len(fails)})"))
    for f in fails:
        A(f"  x {f}")
    text = "\n".join(lines) + "\n"
    with open(path, "w") as f:
        f.write(text)
    return text
