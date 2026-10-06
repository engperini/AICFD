# bin/dwg2dxf

`dwg2dxf` is the DWG-to-DXF converter of **LibreDWG** (GNU GPL v3), a build of
its master branch (the 0.13 release fails on recent AutoCAD files). Its source
is at https://github.com/LibreDWG/libredwg -- build it from there if you want
your own binary, or if this one does not run on your platform (it is a Linux
x86-64 executable).

It is only the first step: the pipeline reads the DXF it writes. A DXF exported
from AutoCAD (2018 format) is accepted with `--dxf` and needs no converter.
