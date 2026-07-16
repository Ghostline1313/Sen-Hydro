# -*- coding: utf-8 -*-
"""
Worker Sen Hydro - calculs de delineation dans un processus Python separe
(evite le conflit numpy entre PyQGIS et les dependances de 'delineator').

Auteur : Adiouma FALL - Geo Senegal 2026

Deux modes :

1) Mode "one-shot" (compatibilite / debogage) :
     delineate_worker.py <lat> <lon> <out_dir> <config_json_path>
   Lance un seul calcul puis quitte. C'est le mode historique, mais il
   paie a chaque appel le cout d'import de 'delineator' (~10s), car les
   dependances lourdes (numba, pysheds, scikit-image) sont recompilees/
   rechargees a chaque lancement du processus.

2) Mode "serveur" (utilise par le plugin depuis la v1.5) :
     delineate_worker.py --server
   Importe 'delineator' UNE SEULE FOIS, affiche "READY" sur stdout, puis
   boucle en lisant des requetes JSON (une par ligne) sur stdin et ecrit
   une reponse JSON (une par ligne) sur stdout pour chacune. Le processus
   reste actif entre les clics : les clics suivants sont beaucoup plus
   rapides car l'import couteux n'est paye qu'une fois.
"""
import sys
import os
import json
import warnings
import contextlib

warnings.filterwarnings("ignore", message="GeoSeries.notna")


def _build_config(opts, out_dir, DelineatorConfig):
    return DelineatorConfig(
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
        cache=bool(opts.get("cache", False)),
    )


def _run_one(lat, lon, out_dir, opts, delineate, DelineatorConfig):
    """Execute une delineation et retourne (layers, stats) en dicts serialisables."""
    config = _build_config(opts, out_dir, DelineatorConfig)
    watershed_gdf, rivers_gdf, outlets_gdf = delineate(lat, lon, config)

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
            if "sorder" in rivers_gdf.columns:
                stats["max_stream_order"] = int(rivers_gdf["sorder"].max())
    except Exception as e:
        stats["river_stats_error"] = str(e)

    return layers, stats


def serve():
    """Mode serveur persistant (voir docstring du module)."""
    from delineator import delineate
    from delineator.settings import DelineatorConfig

    # Signal au plugin que les imports sont termines et qu'on est pret.
    print("READY", flush=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        if line == "SHUTDOWN":
            break

        try:
            req = json.loads(line)
            lat = float(req["lat"])
            lon = float(req["lon"])
            out_dir = req["out_dir"]
            opts = req.get("config", {})
            opts = dict(opts)
            opts["cache"] = True  # utile ici : le processus persiste entre les clics

            # 'delineate' peut imprimer des messages de log sur stdout ; on
            # les redirige vers stderr pour ne pas casser le protocole
            # ligne-par-ligne (une reponse JSON = une ligne de stdout).
            with contextlib.redirect_stdout(sys.stderr):
                layers, stats = _run_one(lat, lon, out_dir, opts, delineate, DelineatorConfig)
            print(json.dumps({"ok": True, "layers": layers, "stats": stats}), flush=True)
        except Exception as e:
            print(json.dumps({"ok": False, "error": str(e)}), flush=True)


def main():
    if len(sys.argv) == 2 and sys.argv[1] == "--server":
        serve()
        return

    if len(sys.argv) != 5:
        print(
            "USAGE: delineate_worker.py <lat> <lon> <out_dir> <config_json_path>"
            "\n   OR: delineate_worker.py --server",
            file=sys.stderr,
        )
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
        layers, stats = _run_one(lat, lon, out_dir, opts, delineate, DelineatorConfig)
    except Exception as e:
        print("DELINEATE_ERROR: %s" % e, file=sys.stderr)
        sys.exit(4)

    print(json.dumps({"layers": layers, "stats": stats}))


if __name__ == "__main__":
    main()
