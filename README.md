Test plugin RemonterleTemps PVA pour QGIS
# GPF - Remonter Le Temps PVA (Plugin QGIS)

Ce plugin QGIS permet de rechercher, prévisualiser et télécharger des **Photos Aériennes (PVA)** historiques issues de la Géoplateforme (IGN - France) directement dans votre canevas de carte.

## 🚀 Fonctionnalités

- **Scan de missions** : Recherche automatique des missions disponibles sur l'emprise actuelle de votre carte.
- **Sélection spatiale** : Tracez un rectangle pour identifier précisément les clichés couvrant votre zone d'intérêt.
- **Panier intelligent** :
  - Organisation par Mission (1er niveau) puis par Cliché.
  - **Légende dynamique** sous chaque cliché affichant les coordonnées du centre et l'angle d'orientation.
- **Calage chirurgical** : 
  - Prévisualisation instantanée avec **correction automatique de 180°** (pour compenser l'orientation tête-bêche classique des données IGN).
  - Rotation matricielle autour du centre du cliché pour une superposition parfaite sur le fond de plan.
- **Export en lot** : Téléchargement des fichiers `.tif` originaux et de leurs métadonnées `.json`.

## 🛠️ Installation

1. Localisez votre dossier de plugins QGIS :
   - Windows : `%AppData%\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
   - Linux/Mac : `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins`
2. Créez un dossier nommé `remonter_temps_pva`.
3. Copiez le script Python principal dans ce dossier.
4. Redémarrez QGIS ou utilisez le plugin *Plugin Reloader*.

## 📖 Utilisation

1. **Scanner** : Cliquez sur "Scanner l'emprise actuelle" pour lister les années et missions disponibles.
2. **Filtrer** : Sélectionnez une année puis une mission spécifique. Une couche de tuiles vectorielles s'affiche pour montrer l'emprise globale de la mission.
3. **Sélectionner** : Cliquez sur "Tracer un rectangle" et entourez la zone souhaitée sur la carte. Les clichés correspondants s'ajoutent au panier.
4. **Prévisualiser** : Cliquez sur l'icône **œil** 👁️ dans le panier pour afficher le cliché redressé à 180°. Ajustez l'opacité avec le curseur si besoin.
5. **Nettoyer** : Décochez les photos inutiles et cliquez sur "Nettoyer décochés".
6. **Télécharger** : Cliquez sur le bouton vert pour exporter les fichiers dans le dossier de votre choix.

## ⚙️ Spécificités Techniques

- **Compatibilité** : A priori conçu pour fonctionner indifféremment sous **PyQt5** et **PyQt6**.

## 📜 Licence

Le plugin est distribué sous les termes de la licence GPL-3.0 license 
