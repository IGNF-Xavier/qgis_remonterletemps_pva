# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le
versionnement sémantique.

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
