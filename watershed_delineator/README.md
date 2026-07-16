# Sen Hydro — Délimitation de Bassins Versants (Plugin QGIS)

**Auteur :** Adiouma FALL - Géo Sénégal 2026
**Basé sur :** package Python open-source [`delineator`](https://github.com/mheberger/delineator) (Matthew Heberger, licence MIT) — données MERIT-Hydro / HydroSHEDS.

## Fonctionnalité

Un clic sur la carte QGIS suffit pour délimiter automatiquement :
- le bassin versant en amont du point cliqué (l'exutoire),
- le réseau hydrographique associé,
- le point exutoire lui-même.

Les trois résultats sont chargés directement comme couches vectorielles dans le projet QGIS courant.

## Installation

### 1. Installer le plugin dans QGIS

1. Repérez votre dossier de plugins QGIS :
   - Windows : `C:\Users\<votre_nom>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux/Mac : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Copiez-y le dossier `watershed_delineator` tel quel (ou utilisez QGIS > Extensions > Installer depuis un ZIP en pointant vers `watershed_delineator.zip`).
3. Dans QGIS : menu **Extensions > Gérer et installer les extensions > Installées**, cochez la case pour activer « Sen Hydro - Délimitation de Bassins Versants ».
4. L'icône apparaît dans la barre d'outils et dans le menu **Sen Hydro**.

### 2. Installer la dépendance Python `delineator`

Le plugin nécessite le package Python `delineator`. Ouvrez la console **OSGeo4W Shell** (Windows) ou un terminal (Linux/Mac) où se trouve l'interpréteur Python utilisé par QGIS, puis exécutez :

```bash
pip install delineator
```

Ou, depuis la console Python intégrée de QGIS :

```python
import subprocess, sys
subprocess.check_call([sys.executable, "-m", "pip", "install", "delineator"])
```

> Si `pip` n'est pas directement accessible, utilisez le chemin complet vers `python-qgis.bat` (Windows) ou l'interpréteur Python fourni avec votre installation QGIS (`OSGeo4W\bin\python3.exe` par exemple).

Au premier lancement, `delineator` peut télécharger certains jeux de données globaux MERIT-Hydro/HydroSHEDS nécessaires au calcul — une connexion internet est requise.

## Utilisation

1. Cliquez sur l'icône « Sen Hydro - Délimiter un bassin versant » dans la barre d'outils (ou activez-la depuis le menu **Sen Hydro**).
2. Cliquez n'importe où sur la carte : le point cliqué sera considéré comme l'exutoire.
3. Patientez pendant le calcul (selon la connexion et la taille du bassin, cela peut prendre quelques secondes à quelques minutes).
4. Trois nouvelles couches apparaissent dans le projet : `Bassin versant`, `Réseau hydrographique`, `Exutoire`.

## Licence

Ce plugin s'appuie sur le package `delineator` (MIT License, Matthew Heberger). Le code du plugin lui-même est distribué librement par Adiouma FALL - Géo Sénégal 2026.
