# Rack types

One file per cabinet type, as a project's own documents state it. A case names
one as its standard (`racks.type`) or per position in the typical row
(`racks.row[i].type`), and its width, depth and height come from here instead
of being typed into the case (ADR-075).

A type is a CABINET, not a layout: how many stand where, and what each one
carries, is the case's business and the rack page's. What a type states is what
the cabinet is, and where that came from.

`load_kw` is the duty the source document puts on the type where it states one.
It is a default the case is free to override, position by position, because the
same cabinet is racked to different loads in different halls.
