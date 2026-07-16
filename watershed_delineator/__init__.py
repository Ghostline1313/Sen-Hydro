# -*- coding: utf-8 -*-
"""
Plugin QGIS - Sen Hydro - Délimitation de Bassins Versants
Créé par : Adiouma FALL - Géo Sénégal 2026

Utilité :
Ce plugin permet à tout utilisateur QGIS de délimiter en un clic
le bassin versant en amont d'un point (exutoire) directement dans
son projet SIG, sans passer par le site web mghydro.com. Il est
particulièrement utile pour les études hydrologiques, la gestion
des ressources en eau, la planification d'ouvrages hydrauliques,
l'analyse des risques d'inondation et les travaux universitaires
en géographie/hydrologie, notamment en Afrique de l'Ouest.
"""

def classFactory(iface):
    from .watershed_delineator import WatershedDelineatorPlugin
    return WatershedDelineatorPlugin(iface)
