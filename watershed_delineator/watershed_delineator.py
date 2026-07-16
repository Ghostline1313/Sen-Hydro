# -*- coding: utf-8 -*-
"""
Plugin QGIS : Sen Hydro - Delimitation de Bassins Versants
------------------------------------------------
Auteur       : Adiouma FALL - Geo Senegal 2026
Base sur     : package Python open-source "delineator"
               (Matthew Heberger, MIT License)
               https://github.com/mheberger/delineator

Interface inspiree du site https://mghydro.com/watersheds : options de
calcul (precision, simplification, seuils), personnalisation du style
d'affichage (couleurs, transparence, epaisseur), et rapport du bassin
versant (surface, perimetre, longueur du reseau), le tout directement
integre dans QGIS.

Note technique :
    "delineator" et ses dependances (rasterio, scikit-image, numpy 2.x)
    ne sont pas compatibles binairement avec numpy 1.26 embarque par
    QGIS. Pour eviter tout conflit, le calcul est delegue a un
    processus Python separe (delineate_worker.py), execute avec le
    meme interpreteur Python que celui utilise pour l'installation pip
    (python3.exe du dossier bin de QGIS). Les resultats sont ecrits en
    GeoJSON sur disque puis charges normalement dans le projet QGIS.

Limitation connue :
    Le mode "Aval - Tracer le trajet d'ecoulement" du site mghydro.com
    n'est pas expose par la fonction delineate() du package Python
    "delineator" actuellement installe (seule la delimitation amont du
    bassin versant est disponible).
"""

import json
import os
import subprocess
import sys
import tempfile

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QFileDialog
from qgis.core import (
    QgsProject,
    QgsVectorLayer,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsSingleSymbolRenderer,
    QgsFillSymbol,
    QgsLineSymbol,
    QgsMarkerSymbol,
    QgsGeometry,
    QgsDistanceArea,
    QgsUnitTypes,
    QgsMessageLog,
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
)
from qgis.gui import QgsMapToolEmitPoint

from .dockwidget import SenHydroDockWidget


LAYER_LABELS = {
    "watershed": "Bassin versant",
    "rivers": "Reseau hydrographique",
    "outlet": "Exutoire",
}


class WatershedDelineatorPlugin:
    """
    Plugin principal.
    Cree par Adiouma FALL - Geo Senegal 2026.
    """

    COMMUNES_LAYER_NAME = "Limites_communes"

    def __init__(self, iface):
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.action = None
        self.map_tool = None
        self.dock = None
        self._senegal_boundary_4326 = None

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon.png")
        self.action = QAction(
            QIcon(icon_path),
            "Sen Hydro - Delimiter un bassin versant (par Adiouma FALL - Geo Senegal)",
            self.iface.mainWindow(),
        )
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle_panel)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToMenu("&Sen Hydro", self.action)

        self.map_tool = QgsMapToolEmitPoint(self.canvas)
        self.map_tool.canvasClicked.connect(self.on_map_click)

        self.dock = SenHydroDockWidget(self.iface.mainWindow())
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.dock)
        self.dock.hide()
        self.dock.btn_activate.toggled.connect(self.toggle_tool)
        self.dock.reportExportRequested.connect(self.export_report)
        self.dock.visibilityChanged.connect(self._on_dock_visibility_changed)

    def unload(self):
        self.iface.removeToolBarIcon(self.action)
        self.iface.removePluginMenu("&Sen Hydro", self.action)
        if self.map_tool is not None:
            self.canvas.unsetMapTool(self.map_tool)
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)

    def toggle_panel(self, checked):
        if self.dock is None:
            return
        self.dock.setVisible(checked)

    def _on_dock_visibility_changed(self, visible):
        if self.action is not None:
            self.action.setChecked(visible)
        if not visible and self.dock is not None:
            self.dock.btn_activate.setChecked(False)

    def toggle_tool(self, checked):
        if checked:
            self.canvas.setMapTool(self.map_tool)
        else:
            self.canvas.unsetMapTool(self.map_tool)

    def _get_worker_python(self):
        """
        Retourne le chemin de l'interpreteur Python a utiliser pour lancer
        le calcul dans un processus separe.

        Priorite : l'environnement virtuel dedie "senhydro_venv" (qui
        contient delineator + numpy 2.x + geopandas de facon isolee, sans
        polluer l'installation Python principale de QGIS). Si ce venv est
        absent, on se rabat sur l'interpreteur QGIS (comportement legacy).
        """
        venv_python = os.path.join(
            os.path.expanduser("~"), "senhydro_venv", "Scripts", "python3.exe"
        )
        if os.name == "nt" and os.path.isfile(venv_python):
            return venv_python

        if os.name == "nt":
            bin_dir = os.path.dirname(sys.executable)
            for name in ("python3.exe", "python.exe"):
                candidate = os.path.join(bin_dir, name)
                if os.path.isfile(candidate):
                    return candidate
        return sys.executable

    def on_map_click(self, point, button):
        source_crs = self.canvas.mapSettings().destinationCrs()
        dest_crs = QgsCoordinateReferenceSystem("EPSG:4326")
        transform = QgsCoordinateTransform(source_crs, dest_crs, QgsProject.instance())
        point_wgs84 = transform.transform(point)
        lon, lat = point_wgs84.x(), point_wgs84.y()

        config = self.dock.get_config() if self.dock is not None else {}

        python_exe = self._get_worker_python()
        worker_script = os.path.join(os.path.dirname(__file__), "delineate_worker.py")
        tmp_dir = tempfile.mkdtemp(prefix="watershed_")
        config_path = os.path.join(tmp_dir, "config.json")
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f)

        try:
            # nosec B603 - Liste d'arguments fixe (pas de shell=True, pas de
            # concatenation de chaine). python_exe et worker_script sont des
            # chemins controles par le plugin (interpreteur QGIS/venv dedie
            # et le script delineate_worker.py livre avec le plugin), pas des
            # entrees fournies par un utilisateur distant ou non fiable.
            # lat/lon sont de simples coordonnees numeriques issues du clic
            # sur la carte.
            result = subprocess.run(  # nosec B603
                [python_exe, worker_script, str(lat), str(lon), tmp_dir, config_path],
                capture_output=True,
                text=True,
                timeout=900,  # premier calcul dans une zone = telechargement possible
                shell=False,
            )
        except subprocess.TimeoutExpired:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Calcul trop long",
                "Le calcul depasse 15 minutes (probablement un telechargement "
                "de donnees volumineux pour cette zone). Reessayez : les "
                "donnees deja telechargees seront reutilisees et ce sera "
                "plus rapide la prochaine fois.",
            )
            return
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur d'execution",
                "Impossible de lancer le calcul du bassin versant :\n%s" % e,
            )
            return

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if "IMPORT_ERROR" in stderr or "No module named" in stderr:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Package manquant",
                    "Le package Python 'delineator' n'est pas installe pour "
                    "l'interpreteur :\n%s\n\n"
                    "Ouvrez la console OSGeo4W/QGIS Python et executez :\n"
                    "    \"%s\" -m pip install delineator\n\n"
                    "Plugin developpe par Adiouma FALL - Geo Senegal 2026." % (
                        python_exe, python_exe
                    ),
                )
            else:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Erreur de delimitation",
                    "Une erreur est survenue pendant le calcul du bassin versant :\n\n%s" % stderr,
                )
            return

        try:
            output = json.loads((result.stdout or "").strip().splitlines()[-1])
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur de lecture des resultats",
                "Le calcul semble avoir reussi mais les resultats n'ont pas pu "
                "etre lus :\n%s\n\nSortie brute :\n%s" % (e, result.stdout),
            )
            return

        paths = output.get("layers", {})
        stats = output.get("stats", {})

        if not paths:
            QMessageBox.warning(
                self.iface.mainWindow(),
                "Aucun resultat",
                "Aucune couche n'a ete generee pour ce point. Essayez un autre "
                "emplacement, de preference sur ou pres d'un cours d'eau.",
            )
            return

        style = self.dock.get_style() if self.dock is not None else None
        show_watershed = self.dock.chk_show_watershed.isChecked() if self.dock is not None else True
        outlet_both = self.dock.chk_outlet_both.isChecked() if self.dock is not None else True
        clip_senegal = self.dock.chk_clip_senegal.isChecked() if self.dock is not None else False

        loaded_layers = {}
        for key, path in paths.items():
            if key == "watershed" and not show_watershed:
                continue
            layer = self._add_layer(path, LAYER_LABELS.get(key, key))
            if layer is not None:
                if key == "outlet" and not outlet_both:
                    self._keep_only_snapped_outlet(layer)
                loaded_layers[key] = layer
                if style is not None:
                    self._apply_style(layer, key, style)

        clip_warning = None
        if clip_senegal:
            senegal_geom = self._get_senegal_boundary()
            if senegal_geom is None:
                clip_warning = (
                    "La couche '%s' est introuvable (ou vide) dans le projet : "
                    "le decoupage aux limites du Senegal a ete ignore." % self.COMMUNES_LAYER_NAME
                )
            else:
                for key, layer in loaded_layers.items():
                    self._clip_layer_to_senegal(layer, senegal_geom)
                # Les stats du worker sont calculees avant decoupage : on les
                # recalcule sur la geometrie effectivement affichee.
                if "watershed" in loaded_layers:
                    stats = self._recompute_watershed_stats(loaded_layers["watershed"], stats)
                if "rivers" in loaded_layers:
                    stats = self._recompute_river_stats(loaded_layers["rivers"], stats)

        if self.dock is not None:
            self.dock.show_report(stats, lat, lon)

        if clip_warning:
            QMessageBox.information(self.iface.mainWindow(), "Decoupage Senegal", clip_warning)

        self.canvas.refresh()

    def _keep_only_snapped_outlet(self, layer):
        """
        Ne conserve que le point exutoire 'snapped' (ajuste a la riviere),
        qui est celui reellement utilise pour le calcul du bassin versant.
        """
        type_idx = layer.fields().indexOf("type")
        if type_idx < 0:
            return
        layer.startEditing()
        to_delete = [
            f.id() for f in layer.getFeatures()
            if str(f.attribute("type")).lower() != "snapped"
        ]
        for fid in to_delete:
            layer.deleteFeature(fid)
        layer.commitChanges()
        layer.updateExtents()

    def _add_layer(self, path, layer_name):
        layer = QgsVectorLayer(path, layer_name, "ogr")
        if layer.isValid():
            QgsProject.instance().addMapLayer(layer)
            return layer
        QMessageBox.warning(
            self.iface.mainWindow(),
            "Couche invalide",
            "Impossible de charger la couche : %s" % layer_name,
        )
        return None

    def _apply_style(self, layer, key, style):
        opacity = style.get("opacity", 0.35)
        width = style.get("line_width", 0.6)

        try:
            if key == "watershed":
                color = QColor(style["watershed_color"])
                symbol = QgsFillSymbol.createSimple({})
                symbol.setColor(color)
                symbol.setOpacity(opacity)
                if symbol.symbolLayerCount() > 0:
                    symbol.symbolLayer(0).setStrokeColor(color)
                    symbol.symbolLayer(0).setStrokeWidth(width)
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            elif key == "rivers":
                color = QColor(style["river_color"])
                symbol = QgsLineSymbol.createSimple({})
                symbol.setColor(color)
                symbol.setWidth(width)
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
            elif key == "outlet":
                color = QColor(style["watershed_color"])
                type_idx = layer.fields().indexOf("type")
                if type_idx >= 0:
                    # Point "demande" (clic) : marqueur creux (contour seul)
                    requested_symbol = QgsMarkerSymbol.createSimple({})
                    requested_symbol.setColor(QColor(255, 255, 255, 0))
                    if requested_symbol.symbolLayerCount() > 0:
                        requested_symbol.symbolLayer(0).setStrokeColor(color)
                        requested_symbol.symbolLayer(0).setStrokeWidth(0.8)
                    requested_symbol.setSize(3)

                    # Point "ajuste a la riviere" : marqueur plein
                    snapped_symbol = QgsMarkerSymbol.createSimple({})
                    snapped_symbol.setColor(color)
                    snapped_symbol.setSize(3)

                    categories = [
                        QgsRendererCategory(
                            "requested", requested_symbol, "Point demande (clic)"
                        ),
                        QgsRendererCategory(
                            "snapped", snapped_symbol, "Point ajuste a la riviere"
                        ),
                    ]
                    layer.setRenderer(
                        QgsCategorizedSymbolRenderer("type", categories)
                    )
                else:
                    symbol = QgsMarkerSymbol.createSimple({})
                    symbol.setColor(color)
                    symbol.setSize(3)
                    layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        except Exception as style_err:
            # Le style est une amelioration cosmetique : en cas d'echec,
            # la couche reste affichee avec le style par defaut de QGIS.
            # On journalise plutot que d'ignorer silencieusement l'erreur.
            QgsMessageLog.logMessage(
                "Sen Hydro: echec de l'application du style pour '%s': %s"
                % (key, style_err),
                "Sen Hydro",
                Qgis.Warning,
            )

        layer.triggerRepaint()

    def _get_senegal_boundary(self):
        """
        Retourne (et met en cache) la geometrie de la frontiere du Senegal,
        en EPSG:4326 (meme CRS que les couches produites par 'delineator').

        Deux sources sont essayees, dans cet ordre :
        1. Le fichier embarque dans le plugin (data/senegal_boundary.gpkg),
           genere une fois pour toutes a partir des limites communales
           officielles. Fonctionne sans configuration, dans n'importe quel
           projet QGIS.
        2. A defaut (fichier absent/corrompu), une couche nommee
           'Limites_communes' presente dans le projet ouvert, dissoute a la
           volee (comportement historique, conserve en secours).
        """
        if self._senegal_boundary_4326 is not None:
            return self._senegal_boundary_4326

        # --- Source 1 : fichier embarque dans le plugin ---
        bundled_path = os.path.join(
            os.path.dirname(__file__), "data", "senegal_boundary.gpkg"
        )
        if os.path.isfile(bundled_path):
            bundled_layer = QgsVectorLayer(bundled_path, "senegal_boundary", "ogr")
            if bundled_layer.isValid() and bundled_layer.featureCount() > 0:
                feat = next(bundled_layer.getFeatures(), None)
                if feat is not None and feat.geometry() is not None and not feat.geometry().isEmpty():
                    self._senegal_boundary_4326 = QgsGeometry(feat.geometry())
                    return self._senegal_boundary_4326

        # --- Source 2 (secours) : couche 'Limites_communes' du projet ---
        communes_layer = None
        for layer in QgsProject.instance().mapLayers().values():
            if layer.name() == self.COMMUNES_LAYER_NAME:
                communes_layer = layer
                break

        if communes_layer is None:
            return None

        geoms = [
            f.geometry() for f in communes_layer.getFeatures()
            if f.geometry() is not None and not f.geometry().isEmpty()
        ]
        if not geoms:
            return None

        union_geom = QgsGeometry.unaryUnion(geoms)
        if union_geom is None or union_geom.isEmpty():
            return None

        transform = QgsCoordinateTransform(
            communes_layer.crs(),
            QgsCoordinateReferenceSystem("EPSG:4326"),
            QgsProject.instance(),
        )
        union_geom.transform(transform)

        self._senegal_boundary_4326 = union_geom
        return union_geom

    def _clip_layer_to_senegal(self, layer, senegal_geom):
        """
        Decoupe en place (edition + commit) les entites de `layer` selon
        `senegal_geom`. Les entites entierement hors Senegal sont supprimees.
        """
        layer.startEditing()
        to_delete = []
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if not geom.intersects(senegal_geom):
                to_delete.append(feat.id())
                continue
            clipped = geom.intersection(senegal_geom)
            if clipped is None or clipped.isEmpty():
                to_delete.append(feat.id())
                continue
            layer.changeGeometry(feat.id(), clipped)
        for fid in to_delete:
            layer.deleteFeature(fid)
        layer.commitChanges()
        layer.updateExtents()
        layer.triggerRepaint()

    def _make_distance_area(self):
        da = QgsDistanceArea()
        da.setEllipsoid("WGS84")
        da.setSourceCrs(
            QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance().transformContext()
        )
        return da

    def _recompute_watershed_stats(self, layer, stats):
        da = self._make_distance_area()
        total_area_km2 = 0.0
        total_perimeter_km = 0.0
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            total_area_km2 += da.measureArea(geom) / 1_000_000.0
            total_perimeter_km += da.measurePerimeter(geom) / 1000.0
        stats = dict(stats)
        if layer.featureCount() > 0:
            stats["area_km2"] = total_area_km2
            stats["perimeter_km"] = total_perimeter_km
        else:
            stats.pop("area_km2", None)
            stats.pop("perimeter_km", None)
            stats["clipped_outside_senegal"] = True
        return stats

    def _recompute_river_stats(self, layer, stats):
        da = self._make_distance_area()
        total_length_km = 0.0
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            total_length_km += da.measureLength(geom) / 1000.0
        stats = dict(stats)
        stats["river_reach_count"] = layer.featureCount()
        if layer.featureCount() > 0:
            stats["river_length_km"] = total_length_km
        else:
            stats.pop("river_length_km", None)
        return stats

    def export_report(self):
        if self.dock is None:
            return
        text = self.dock.get_report_text()
        if not text:
            return
        path, _ = QFileDialog.getSaveFileName(
            self.iface.mainWindow(),
            "Exporter le rapport",
            "rapport_bassin_versant.txt",
            "Fichiers texte (*.txt)",
        )
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        QMessageBox.information(
            self.iface.mainWindow(),
            "Rapport exporte",
            "Le rapport a ete enregistre :\n%s" % path,
        )
