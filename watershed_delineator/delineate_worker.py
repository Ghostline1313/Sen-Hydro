# -*- coding: utf-8 -*-
"""
Script execute dans un processus Python separe (via subprocess) pour
eviter le conflit numpy entre PyQGIS (numpy 1.26) et les dependances
de delineator (numpy 2.x, incompatible en binaire).

Auteur : Adiouma FALL - Geo Senegal 2026

Usage : delineate_worker.py <lat> <lon> <out_dir> <config_json_path>

Le fichier config_json_path contient les options de calcul choisies
dans le panneau Sen Hydro (precision, simplification, seuils, etc.).
Le script ecrit sur stdout, en derniere ligne, un JSON de la forme :
    {"layers": {"watershed": "...", "rivers": "...", "outlet": "..."},
     "stats": {"area_km2": ..., "perimeter_km": ..., "river_length_km": ...}}
"""
import sys
import os
import json
import warnings

warnings.filterwarnings("ignore", message="GeoSeries.notna")


def main():
    if len(sys.argv) != 5:
        print("USAGE: delineate_worker.py <lat> <lon> <out_dir> <config_json_path>", file=sys.stderr)
        sys.exit(2)

    lat = float(sys.argv[1])
    lon = float(sys.argv[2])
    out_dir = sys.argv[3]
    config_path = sys.argv[4]

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            opts = json.load(f)
    except Exception as e:
        print("CONFIG_ERROR: %s" % e, file=sys.stderr)
        sys.exit(5)

    try:
        from delineator import delineate
        from delineator.settings import DelineatorConfig
    except ImportError as e:
        print("IMPORT_ERROR: %s" % e, file=sys.stderr)
        sys.exit(3)

    try:
        config = DelineatorConfig(
            output_dir=out_dir,
            high_res=bool(opts.get("high_res", True)),
            simplify=bool(opts.get("simplify", False)),
            simplify_tolerance=float(opts.get("simplify_tolerance", 0.10)),
            smooth=bool(opts.get("smooth", False)),
            fill=bool(opts.get("fill", True)),
            fill_area_max=float(opts.get("fill_area_max", 1.0)),
            clean=bool(opts.get("clean", True)),
            num_stream_orders=int(opts.get("num_stream_orders", 4)),
            search_dist=float(opts.get("search_dist", 5.0)),
            stream_threshold_km2=float(opts.get("stream_threshold_km2", 25.0)),
            rivers=bool(opts.get("rivers", True)),
            outlets=bool(opts.get("outlets", True)),
            calc_area=True,
            round_coordinates=True,
        )
    except Exception as e:
        print("CONFIG_BUILD_ERROR: %s" % e, file=sys.stderr)
        sys.exit(6)

    try:
        watershed_gdf, rivers_gdf, outlets_gdf = delineate(lat, lon, config)
    except Exception as e:
        print("DELINEATE_ERROR: %s" % e, file=sys.stderr)
        sys.exit(4)

    layers = {}
    for name, gdf in (
        ("watershed", watershed_gdf),
        ("rivers", rivers_gdf),
        ("outlet", outlets_gdf),
    ):
        if gdf is None or gdf.empty:
            continue
        path = os.path.join(out_dir, name + ".geojson")
        gdf.to_file(path, driver="GeoJSON")
        layers[name] = path

    stats = {}
    try:
        if watershed_gdf is not None and not watershed_gdf.empty:
            if "area" in watershed_gdf.columns:
                stats["area_km2"] = float(watershed_gdf["area"].sum())
            try:
                gdf_m = watershed_gdf.to_crs(watershed_gdf.estimate_utm_crs())
                if "area_km2" not in stats:
                    stats["area_km2"] = float(gdf_m.geometry.area.sum() / 1e6)
                stats["perimeter_km"] = float(gdf_m.geometry.length.sum() / 1000.0)
            except Exception as reproj_err:
                # Non-fatal: area from the "area" column above may already be
                # set. Record the reprojection failure for diagnostics instead
                # of silently discarding it.
                stats["perimeter_stats_error"] = str(reproj_err)
    except Exception as e:
        stats["watershed_stats_error"] = str(e)

    try:
        if rivers_gdf is not None and not rivers_gdf.empty:
            valid_rivers = rivers_gdf[
                ~rivers_gdf.geometry.is_empty
                & rivers_gdf.geometry.notna()
                & rivers_gdf.geometry.is_valid
            ]
            if not valid_rivers.empty:
                gdf_m = valid_rivers.to_crs(valid_rivers.estimate_utm_crs())
                stats["river_length_km"] = float(gdf_m.geometry.length.sum() / 1000.0)
            stats["river_reach_count"] = int(len(rivers_gdf))
    except Exception as e:
        stats["river_stats_error"] = str(e)

    # Derniere ligne de stdout = JSON lu par le plugin
    print(json.dumps({"layers": layers, "stats": stats}))


if __name__ == "__main__":
    main()
