# Sen Hydro - Watershed Delineation

QGIS plugin to delineate the upstream watershed of any point clicked on the
map, using the open-source [`delineator`](https://github.com/mheberger/delineator)
Python package (MERIT-Hydro / HydroSHEDS data).

## Features
- One-click watershed (upstream drainage basin) delineation
- Configurable options: precision, simplification, smoothing, stream
  thresholds, search distance
- Custom styling (colors, opacity, line width) for watershed / rivers / outlet
- On-screen watershed report (area, perimeter, river length), exportable to text
- Optional clipping of results to Senegal's official commune boundaries
  (`Limites_communes` layer)

## Installation
Download the latest release ZIP and install it via
`QGIS > Plugins > Manage and Install Plugins > Install from ZIP`.

Requires the `delineator` Python package. The plugin runs delineation in an
isolated Python environment (subprocess) to avoid numpy/GDAL version
conflicts with QGIS's own Python.

## Author
Adiouma FALL - Geo Senegal
mouridefalltouba@gmail.com

## License
Plugin code: MIT.
Uses the `delineator` package (MIT License, Matthew Heberger) and
MERIT-Hydro / MERIT-Basins / HydroSHEDS data.
