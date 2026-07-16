# -*- coding: utf-8 -*-
"""
Dock widget - Sen Hydro - Delimitation de Bassins Versants
Auteur : Adiouma FALL - Geo Senegal 2026

Panneau lateral inspire des options du site mghydro.com/watersheds :
options de calcul, style d'affichage, et rapport du bassin versant.
"""

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QFormLayout, QGroupBox, QCheckBox,
    QDoubleSpinBox, QSpinBox, QPushButton, QLabel, QTextEdit, QComboBox,
    QSlider, QScrollArea,
)
from qgis.gui import QgsColorButton


class SenHydroDockWidget(QDockWidget):

    reportExportRequested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Sen Hydro - Bassins versants", parent)
        self.setObjectName("SenHydroDockWidget")

        content = QWidget()
        layout = QVBoxLayout(content)

        # --- Mode ---
        mode_box = QGroupBox("Mode")
        mode_layout = QVBoxLayout(mode_box)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Amont - Delimiter le bassin versant")
        mode_layout.addWidget(self.mode_combo)
        mode_note = QLabel(
            "Note : le mode Aval (trace du trajet d'ecoulement) du site "
            "mghydro.com n'est pas expose par le package Python 'delineator' "
            "actuellement installe. Seul le mode amont (bassin versant) est "
            "disponible ici."
        )
        mode_note.setWordWrap(True)
        mode_note.setStyleSheet("color: gray; font-size: 10px;")
        mode_layout.addWidget(mode_note)
        layout.addWidget(mode_box)

        # --- Options de calcul ---
        opt_box = QGroupBox("Options de calcul")
        opt_layout = QFormLayout(opt_box)

        self.chk_high_res = QCheckBox("Haute precision")
        self.chk_high_res.setChecked(True)
        opt_layout.addRow(self.chk_high_res)

        self.chk_simplify = QCheckBox("Simplifier")
        opt_layout.addRow(self.chk_simplify)
        self.spin_simplify_tol = QDoubleSpinBox()
        self.spin_simplify_tol.setRange(0.0, 10.0)
        self.spin_simplify_tol.setSingleStep(0.01)
        self.spin_simplify_tol.setValue(0.10)
        self.spin_simplify_tol.setSuffix(" km")
        opt_layout.addRow("Tolerance simplification", self.spin_simplify_tol)

        self.chk_smooth = QCheckBox("Beautifier (lissage)")
        opt_layout.addRow(self.chk_smooth)

        self.chk_fill = QCheckBox("Combler les trous")
        self.chk_fill.setChecked(True)
        opt_layout.addRow(self.chk_fill)
        self.spin_fill_area = QDoubleSpinBox()
        self.spin_fill_area.setRange(0.0, 1000.0)
        self.spin_fill_area.setValue(1.0)
        self.spin_fill_area.setSuffix(" km2")
        opt_layout.addRow("Surface max des trous", self.spin_fill_area)

        self.chk_clean = QCheckBox("Nettoyer la geometrie")
        self.chk_clean.setChecked(True)
        opt_layout.addRow(self.chk_clean)

        self.spin_stream_orders = QSpinBox()
        self.spin_stream_orders.setRange(1, 9)
        self.spin_stream_orders.setValue(4)
        opt_layout.addRow("Ordres de cours d'eau", self.spin_stream_orders)

        self.spin_search_dist = QDoubleSpinBox()
        self.spin_search_dist.setRange(0.1, 50.0)
        self.spin_search_dist.setValue(5.0)
        self.spin_search_dist.setSuffix(" km")
        opt_layout.addRow("Distance de recherche exutoire", self.spin_search_dist)

        self.spin_stream_threshold = QDoubleSpinBox()
        self.spin_stream_threshold.setRange(1.0, 50000.0)
        self.spin_stream_threshold.setValue(25.0)
        self.spin_stream_threshold.setSuffix(" km2")
        opt_layout.addRow("Seuil de detection cours d'eau", self.spin_stream_threshold)

        layout.addWidget(opt_box)

        # --- Affichage ---
        disp_box = QGroupBox("Afficher")
        disp_layout = QVBoxLayout(disp_box)
        self.chk_show_rivers = QCheckBox("Reseau hydrographique")
        self.chk_show_rivers.setChecked(True)
        self.chk_show_watershed = QCheckBox("Limite du bassin versant")
        self.chk_show_watershed.setChecked(True)
        self.chk_show_outlet = QCheckBox("Point exutoire")
        self.chk_show_outlet.setChecked(True)
        disp_layout.addWidget(self.chk_show_rivers)
        disp_layout.addWidget(self.chk_show_watershed)
        disp_layout.addWidget(self.chk_show_outlet)

        self.chk_outlet_both = QCheckBox(
            "  Afficher les 2 points (clique + ajuste a la riviere)"
        )
        self.chk_outlet_both.setChecked(True)
        outlet_note = QLabel(
            "  Le point exutoire est parfois legerement deplace ('ajuste') "
            "vers le cours d'eau le plus proche pour un calcul plus precis. "
            "Decochez pour ne garder que le point ajuste (utilise pour le calcul)."
        )
        outlet_note.setWordWrap(True)
        outlet_note.setStyleSheet("color: gray; font-size: 10px;")
        disp_layout.addWidget(self.chk_outlet_both)
        disp_layout.addWidget(outlet_note)

        layout.addWidget(disp_box)

        # --- Emprise ---
        extent_box = QGroupBox("Emprise")
        extent_layout = QVBoxLayout(extent_box)
        self.chk_clip_senegal = QCheckBox(
            "Limiter au territoire du Senegal (limites communales)"
        )
        self.chk_clip_senegal.setChecked(True)
        extent_layout.addWidget(self.chk_clip_senegal)
        extent_note = QLabel(
            "Le bassin versant et le reseau hydrographique seront decoupes "
            "selon la frontiere du Senegal (limites communales officielles, "
            "integrees au plugin). Aucune couche a charger manuellement."
        )
        extent_note.setWordWrap(True)
        extent_note.setStyleSheet("color: gray; font-size: 10px;")
        extent_layout.addWidget(extent_note)
        layout.addWidget(extent_box)

        # --- Style ---
        style_box = QGroupBox("Style")
        style_layout = QFormLayout(style_box)

        self.btn_river_color = QgsColorButton()
        self.btn_river_color.setColor(QColor(30, 120, 220))
        style_layout.addRow("Couleur rivieres", self.btn_river_color)

        self.btn_watershed_color = QgsColorButton()
        self.btn_watershed_color.setColor(QColor(46, 125, 50))
        style_layout.addRow("Couleur bassin", self.btn_watershed_color)

        self.slider_opacity = QSlider(Qt.Horizontal)
        self.slider_opacity.setRange(0, 100)
        self.slider_opacity.setValue(35)
        style_layout.addRow("Transparence remplissage (%)", self.slider_opacity)

        self.spin_line_width = QDoubleSpinBox()
        self.spin_line_width.setRange(0.1, 5.0)
        self.spin_line_width.setSingleStep(0.1)
        self.spin_line_width.setValue(0.6)
        self.spin_line_width.setSuffix(" mm")
        style_layout.addRow("Epaisseur des lignes", self.spin_line_width)

        layout.addWidget(style_box)

        # --- Action ---
        self.btn_activate = QPushButton("Cliquer sur la carte pour delimiter")
        self.btn_activate.setCheckable(True)
        layout.addWidget(self.btn_activate)

        # --- Rapport ---
        report_box = QGroupBox("Rapport du bassin versant")
        report_layout = QVBoxLayout(report_box)
        self.report_text = QTextEdit()
        self.report_text.setReadOnly(True)
        self.report_text.setPlaceholderText(
            "Cliquez sur la carte pour generer un bassin versant : "
            "les statistiques s'afficheront ici."
        )
        self.report_text.setMinimumHeight(120)
        report_layout.addWidget(self.report_text)
        self.btn_export_report = QPushButton("Exporter le rapport (.txt)")
        self.btn_export_report.setEnabled(False)
        report_layout.addWidget(self.btn_export_report)
        layout.addWidget(report_box)

        layout.addStretch()
        content.setLayout(layout)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        self.setWidget(scroll)

        self.btn_export_report.clicked.connect(self.reportExportRequested.emit)
        self._last_report_text = ""

    # ------------------------------------------------------------------
    def get_config(self):
        """Retourne un dict pret a etre serialise en JSON pour le worker."""
        return {
            "high_res": self.chk_high_res.isChecked(),
            "simplify": self.chk_simplify.isChecked(),
            "simplify_tolerance": self.spin_simplify_tol.value(),
            "smooth": self.chk_smooth.isChecked(),
            "fill": self.chk_fill.isChecked(),
            "fill_area_max": self.spin_fill_area.value(),
            "clean": self.chk_clean.isChecked(),
            "num_stream_orders": self.spin_stream_orders.value(),
            "search_dist": self.spin_search_dist.value(),
            "stream_threshold_km2": self.spin_stream_threshold.value(),
            "rivers": self.chk_show_rivers.isChecked(),
            "outlets": self.chk_show_outlet.isChecked(),
        }

    def get_style(self):
        return {
            "river_color": self.btn_river_color.color(),
            "watershed_color": self.btn_watershed_color.color(),
            "opacity": self.slider_opacity.value() / 100.0,
            "line_width": self.spin_line_width.value(),
        }

    def show_report(self, stats, lat, lon):
        lines = []
        lines.append("Point exutoire : lat %.5f, lon %.5f" % (lat, lon))
        if stats.get("clipped_outside_senegal"):
            lines.append(
                "Le bassin versant obtenu est entierement hors du territoire "
                "senegalais (Limites_communes) : aucune surface n'a ete conservee."
            )
        if "area_km2" in stats:
            lines.append("Surface du bassin versant : %.2f km2" % stats["area_km2"])
        if "perimeter_km" in stats:
            lines.append("Perimetre : %.2f km" % stats["perimeter_km"])
        if "river_length_km" in stats:
            lines.append("Longueur du reseau hydrographique : %.2f km" % stats["river_length_km"])
        if "river_reach_count" in stats:
            lines.append("Nombre de troncons de riviere : %d" % stats["river_reach_count"])
        if len(lines) == 1:
            lines.append("Aucune statistique supplementaire disponible pour ce bassin.")
        text = "\n".join(lines)
        self.report_text.setPlainText(text)
        self._last_report_text = text
        self.btn_export_report.setEnabled(True)

    def get_report_text(self):
        return self._last_report_text
