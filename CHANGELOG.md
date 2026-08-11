# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le
versionnement sémantique.

## [1.3.0] - 2026-08-11

### Ajouté
- **Calage métrique** : la taille pixel au sol est déduite des tags TIFF de
  résolution du scan (avec gestion de `ResolutionUnit` pouce/centimètre) et de
  l'échelle du cliché, au lieu d'être estimée sur l'emprise approximative.
  L'image est posée sur son centre, orientée par l'angle du nord.
- Contrôle du format de plaque déduit (24×18, 18×18, 23×23…) : un format non
  standard signale une métadonnée douteuse.
- Levée automatique de l'ambiguïté de convention sur l'angle d'orientation, en
  confrontant l'attribut IGN à l'orientation grossière de l'emprise.
- Prise en compte du décalage de découpe : le point principal reste au centre
  du scan brut même si le rognage est asymétrique.

## [1.2.0] - 2026-08-11

### Ajouté
- Aperçu par lecture partielle du scan distant via GDAL `/vsicurl/` : seule
  une version décimée est lue, au lieu de rapatrier plusieurs centaines de Mo.
- Exploitation de l'attribut « orientation du nord » du tableau d'assemblage
  pour déterminer le quart de tour du cliché, avec case d'inversion si la
  convention diffère.
- Bouton « Ouvrir la fiche sur remonterletemps.ign.fr » (permalien
  lon/lat/année/mission).

### Modifié
- L'aperçu ne déclenche plus un téléchargement complet.

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
