"""hallkit -- a data hall drawing (DWG/DXF) to the named-solid STL and sidecar
YAML that AICFD reads as a finished geometry, plus a verification plan.

Stages, each a module:

    dxfread   the drawing as rectangles and texts, whatever layer they are on
    extract   which rectangle is the hall, a gallery, a rack, a unit, a cold
              aisle, the cage; which text is a tag or a load
    model     the typed model: rows and fronts, aisles, lids, doors, tiles,
              return grilles, walls, meshes, units -- on the reference
              heights, quantised to the mesh cell, every correction logged
    stl       the STL writer (boxes and panels, axis-aligned, named)
    sidecar   the case file without the room-building keys
    plan      the verification PNG
    dxfout    the treated DXF, on CFD_* layers in the drawing's coordinates
    viewstl   the STL to look at in another program (plates, no hall box), one file per type
    render    a z-buffer render of the view model: the 3D preview PNG
    sections  the section views PNG
    check     the contract's consistency rules, run on the written files
    report    the export text
"""
