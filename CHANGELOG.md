# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le
versionnement sémantique.

## [1.1.0] - 2026-08-11

### Ajouté
- Onglets Recherche / Panier / Traitement.
- Outil « Tracer un rectangle » pour remplir le panier depuis la carte
  (interroge le WFS si aucune couche de clichés n'est chargée).
- Panier arborescent Année → Mission → Cliché, cases à cocher en cascade,
  légende affichant le centre et l'angle d'orientation, infobulle listant
  toutes les métadonnées du WFS.
- Aperçu d'un cliché (calage niveau 1), curseur d'opacité et bascule 180°.
- Boutons « Nettoyer décochés » et « Vider ».
- Export d'un fichier `.json` de métadonnées à côté de chaque scan.
- Champs des couches mémoire déduits dynamiquement des attributs WFS.
- Compatibilité PyQt5 / PyQt6 (`compat.py`).

## [1.0.0] - 2026-08-10

### Ajouté
- Chargement du tableau d'assemblage des missions (`pva:dataset`) et des
  clichés (`pva:image`) depuis le WFS de la Géoplateforme.
- Téléchargement des scans argentiques via l'API de téléchargement IGN.
- Détection et découpe automatique du cadre de fond de chambre, ou marge fixe.
- Calage niveau 1 : ajustement sur l'emprise IGN du tableau d'assemblage.
- Calage niveau 2 : recalage automatique sur l'ortho IGN actuelle
  (AKAZE + RANSAC avec OpenCV, corrélation de phase en numpy sinon).
- Calage niveau 3 : orthorectification sur MNT RGE ALTI par résection
  spatiale (DLT 11 paramètres).
- Installation optionnelle d'OpenCV en un clic, confinée à `libs/`.
