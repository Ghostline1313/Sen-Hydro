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
from qgis.PyQt.QtWidgets import QAction, QMessageBox, QFileDialog, QApplication
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
    QgsWkbTypes,
    QgsFeature,
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
        self._senegal_boundary_simplified_4326 = None
        self._senegal_engine = None
        self._worker_proc = None

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
        self._stop_worker_server()

    def _stop_worker_server(self):
        """Arrete proprement le processus worker persistant, s'il tourne."""
        if self._worker_proc is None:
            return
        try:
            if self._worker_proc.poll() is None:
                try:
                    self._worker_proc.stdin.write("SHUTDOWN\n")
                    self._worker_proc.stdin.flush()
                except Exception as write_err:
                    QgsMessageLog.logMessage(
                        "Sen Hydro: echec de l'envoi de SHUTDOWN au worker: %s" % write_err,
                        "Sen Hydro", Qgis.Info,
                    )
                self._worker_proc.wait(timeout=5)
        except Exception as stop_err:
            QgsMessageLog.logMessage(
                "Sen Hydro: arret propre du worker impossible (%s), terminaison forcee." % stop_err,
                "Sen Hydro", Qgis.Info,
            )
            try:
                self._worker_proc.terminate()
            except Exception as term_err:
                QgsMessageLog.logMessage(
                    "Sen Hydro: echec de la terminaison forcee du worker: %s" % term_err,
                    "Sen Hydro", Qgis.Warning,
                )
        self._worker_proc = None

    def _ensure_worker_server(self):
        """
        Demarre le worker en mode serveur persistant si necessaire.
        Retourne True si le serveur est pret a recevoir des requetes.

        Le premier demarrage prend quelques secondes (import du package
        'delineator' et de ses dependances). Les appels suivants reutilisent
        le meme processus et sont donc beaucoup plus rapides.
        """
        if self._worker_proc is not None and self._worker_proc.poll() is None:
            return True

        python_exe = self._get_worker_python()
        worker_script = os.path.join(os.path.dirname(__file__), "delineate_worker.py")

        try:
            # nosec B603 - Liste d'arguments fixe (pas de shell=True, pas de
            # concatenation de chaine). python_exe et worker_script sont des
            # chemins controles par le plugin (interpreteur QGIS/venv dedie
            # et le script delineate_worker.py livre avec le plugin), pas des
            # entrees fournies par un utilisateur distant ou non fiable.
            self._worker_proc = subprocess.Popen(  # nosec B603
                [python_exe, worker_script, "--server"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                shell=False,
            )
        except Exception as e:
            self._worker_proc = None
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur de demarrage",
                "Impossible de demarrer le worker Sen Hydro :\n%s" % e,
            )
            return False

        try:
            ready_line = self._worker_proc.stdout.readline()
        except Exception as e:
            ready_line = ""

        if ready_line.strip() != "READY":
            stderr_out = ""
            try:
                stderr_out = self._worker_proc.stderr.read()
            except Exception as read_err:
                QgsMessageLog.logMessage(
                    "Sen Hydro: impossible de lire stderr du worker apres "
                    "son arret inattendu: %s" % read_err,
                    "Sen Hydro", Qgis.Warning,
                )
            self._worker_proc = None
            if "IMPORT_ERROR" in stderr_out or "No module named" in stderr_out or "ModuleNotFoundError" in stderr_out:
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
                    "Erreur de demarrage du worker",
                    "Le worker Sen Hydro n'a pas demarre correctement :\n\n%s" % stderr_out,
                )
            return False

        return True

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

        status_bar = self.iface.mainWindow().statusBar()
        is_first_start = self._worker_proc is None or self._worker_proc.poll() is not None
        if is_first_start:
            status_bar.showMessage(
                "Sen Hydro : demarrage du moteur de calcul (premiere utilisation, "
                "quelques secondes)...",
            )
            QApplication.processEvents()

        if not self._ensure_worker_server():
            status_bar.clearMessage()
            return

        tmp_dir = tempfile.mkdtemp(prefix="watershed_")
        request = {"lat": lat, "lon": lon, "out_dir": tmp_dir, "config": config}

        status_bar.showMessage("Sen Hydro : calcul du bassin versant en cours...")
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self._worker_proc.stdin.write(json.dumps(request) + "\n")
            self._worker_proc.stdin.flush()
            response_line = self._worker_proc.stdout.readline()
        except Exception as e:
            QApplication.restoreOverrideCursor()
            status_bar.clearMessage()
            self._worker_proc = None
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur de communication",
                "La communication avec le worker Sen Hydro a echoue :\n%s\n\n"
                "Reessayez : le worker va redemarrer automatiquement." % e,
            )
            return
        QApplication.restoreOverrideCursor()
        status_bar.clearMessage()

        if not response_line:
            stderr_out = ""
            try:
                stderr_out = self._worker_proc.stderr.read()
            except Exception as read_err:
                QgsMessageLog.logMessage(
                    "Sen Hydro: impossible de lire stderr du worker apres "
                    "son arret inattendu: %s" % read_err,
                    "Sen Hydro", Qgis.Warning,
                )
            self._worker_proc = None
            if "IMPORT_ERROR" in stderr_out or "No module named" in stderr_out or "ModuleNotFoundError" in stderr_out:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Package manquant",
                    "Le package Python 'delineator' n'est pas installe pour "
                    "l'interpreteur du worker.\n\n"
                    "Plugin developpe par Adiouma FALL - Geo Senegal 2026.",
                )
            else:
                QMessageBox.critical(
                    self.iface.mainWindow(),
                    "Erreur de delimitation",
                    "Le worker Sen Hydro s'est arrete de maniere inattendue :\n\n%s\n\n"
                    "Reessayez : il redemarrera automatiquement." % stderr_out,
                )
            return

        try:
            output = json.loads(response_line)
        except Exception as e:
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur de lecture des resultats",
                "Le calcul semble avoir reussi mais les resultats n'ont pas pu "
                "etre lus :\n%s\n\nSortie brute :\n%s" % (e, response_line),
            )
            return

        if not output.get("ok", True):
            QMessageBox.critical(
                self.iface.mainWindow(),
                "Erreur de delimitation",
                "Une erreur est survenue pendant le calcul du bassin versant :\n\n%s"
                % output.get("error", "erreur inconnue"),
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
                proj = QgsProject.instance()
                for key in list(loaded_layers.keys()):
                    old_layer = loaded_layers[key]
                    clipped_layer = self._clip_layer_to_senegal(old_layer)
                    if clipped_layer is not old_layer:
                        proj.removeMapLayer(old_layer.id())
                        proj.addMapLayer(clipped_layer)
                        if style is not None:
                            self._apply_style(clipped_layer, key, style)
                        loaded_layers[key] = clipped_layer
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
                sorder_idx = layer.fields().indexOf("sorder")
                if sorder_idx >= 0:
                    orders = sorted({
                        f.attribute("sorder") for f in layer.getFeatures()
                        if f.attribute("sorder") is not None
                    })
                    categories = []
                    max_order = max(orders) if orders else 1
                    for order in orders:
                        # Plus l'ordre de Strahler est eleve, plus le trait est
                        # epais : les cours d'eau principaux ressortent mieux.
                        order_width = width * (0.6 + 1.4 * (order / max(max_order, 1)))
                        symbol = QgsLineSymbol.createSimple({})
                        symbol.setColor(color)
                        symbol.setWidth(order_width)
                        categories.append(
                            QgsRendererCategory(
                                order, symbol, "Ordre %s" % order
                            )
                        )
                    layer.setRenderer(
                        QgsCategorizedSymbolRenderer("sorder", categories)
                    )
                else:
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

    def _get_senegal_clip_engine(self):
        """
        Retourne (et met en cache) un couple (geometrie Senegal simplifiee,
        moteur GEOS "prepared") utilise pour des tests d'intersection tres
        rapides, meme avec des milliers d'entites. La frontiere brute du
        Senegal comporte ~55 000 sommets ; une intersection geometrique
        classique contre une geometrie aussi detaillee, repetee pour chaque
        entite (des centaines de troncons de riviere), est lente. La
        simplification (tolerance ~50 m, negligeable a l'echelle d'un
        bassin versant) et la preparation GEOS accelerent cela d'un facteur
        5 a 10.
        """
        if self._senegal_engine is not None:
            return self._senegal_boundary_simplified_4326, self._senegal_engine

        senegal_geom = self._get_senegal_boundary()
        if senegal_geom is None:
            return None, None

        simplified = senegal_geom.simplify(0.0005)  # ~50 m a cette latitude
        if simplified is None or simplified.isEmpty():
            simplified = senegal_geom

        engine = QgsGeometry.createGeometryEngine(simplified.constGet())
        engine.prepareGeometry()

        self._senegal_boundary_simplified_4326 = simplified
        self._senegal_engine = engine
        return simplified, engine

    def _clip_layer_to_senegal(self, layer):
        """
        Retourne une NOUVELLE couche memoire contenant uniquement la partie
        des entites de `layer` situee sur le territoire senegalais.

        On reconstruit une couche memoire plutot que d'editer `layer` en
        place (startEditing/changeGeometry/deleteFeature) : sur des couches
        de plusieurs centaines/milliers d'entites (reseau hydrographique),
        le systeme d'edition/undo de QGIS est beaucoup plus lent que de
        simplement construire une nouvelle couche avec les geometries deja
        decoupees.
        """
        simplified_senegal, engine = self._get_senegal_clip_engine()
        if engine is None:
            return layer

        geom_type_str = QgsWkbTypes.displayString(layer.wkbType())
        mem_layer = QgsVectorLayer(
            "%s?crs=%s" % (geom_type_str, layer.crs().authid()),
            layer.name(),
            "memory",
        )
        mem_provider = mem_layer.dataProvider()
        mem_provider.addAttributes(layer.fields())
        mem_layer.updateFields()

        new_features = []
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or geom.isEmpty():
                continue
            if not engine.intersects(geom.constGet()):
                continue
            clipped = simplified_senegal.intersection(geom)
            if clipped is None or clipped.isEmpty():
                continue
            new_feat = QgsFeature(mem_layer.fields())
            new_feat.setGeometry(clipped)
            new_feat.setAttributes(feat.attributes())
            new_features.append(new_feat)

        mem_provider.addFeatures(new_features)
        mem_layer.updateExtents()
        return mem_layer

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
