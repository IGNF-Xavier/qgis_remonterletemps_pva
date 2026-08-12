# Remonter le Temps – PVA IGN

Extension QGIS pour exploiter les photographies aériennes anciennes de l'IGN :
tableau d'assemblage, choix des clichés, téléchargement, découpe du cadre de
scan et calage sur le terrain — jusqu'à l'orthorectification sur MNT.

![licence](https://img.shields.io/badge/licence-GPL--3.0-blue)
![QGIS](https://img.shields.io/badge/QGIS-%E2%89%A5%203.28-green)
![statut](https://img.shields.io/badge/statut-exp%C3%A9rimental-orange)

> ## ⚠️ Statut : expérimental
>
> Cette extension n'a **jamais été exécutée dans un vrai QGIS** ni confrontée
> aux serveurs de l'IGN. Elle a été écrite à partir de la documentation de la
> Géoplateforme et du code source de [IGNF/Pompei](https://github.com/IGNF/Pompei) ;
> seules les briques géométriques (ajustement d'emprise, résection spatiale
> DLT, calage métrique, corrélation de phase) ont été validées numériquement
> sur des cas synthétiques.
>
> Attendez-vous à des ajustements au premier lancement, en particulier sur
> l'interface et sur les noms d'attributs renvoyés par le WFS. Les résultats
> de calage doivent être **vérifiés visuellement** avant tout usage sérieux :
> ce plugin ne remplace pas une chaîne photogrammétrique, et aucune de ses
> sorties ne constitue une mesure opposable. `experimental=True` est
> positionné dans `metadata.txt`, QGIS affichera donc un avertissement à
> l'installation.

Charge le **tableau d'assemblage** des photographies aériennes anciennes de l'IGN
(le même que celui de `remonterletemps.ign.fr`), permet de choisir les clichés,
puis les télécharge, **découpe le cadre du scan** et les **cale sur le terrain**.

## Ce que fait le site, et où le plugin va chercher les données

`remonterletemps.ign.fr` est une interface au-dessus de la Géoplateforme. Les
trois services réellement utilisés (identifiés depuis le code source officiel
de l'IGN, [IGNF/Pompei](https://github.com/IGNF/Pompei)) :

| Usage | Service |
|---|---|
| Tableau d'assemblage des **missions** (chantiers) | WFS `pva:dataset` sur `https://data.geopf.fr/wfs` |
| Tableau d'assemblage des **clichés** d'une mission | WFS `pva:image` |
| Scan brut du cliché | `https://data.geopf.fr/telechargement/download/pva/<mission>/<cliche>.tif` |
| Ortho actuelle (référence de calage) | WMS-R `ORTHOIMAGERY.ORTHOPHOTOS` |
| MNT RGE ALTI (orthorectification) | WMS-R `ELEVATION.ELEVATIONGRIDCOVERAGE.HIGHRES` + API altimétrie |

Les emprises WFS sont diffusées en EPSG:3857. Aucune clé d'API n'est nécessaire.

## Installation

**Depuis une release** : télécharger `remonter_le_temps-<version>.zip`, puis
QGIS → *Extensions* → *Installer depuis un ZIP*. Le bouton apparaît dans la
barre d'outils et dans le menu *Raster*.

**Depuis les sources** :

```bash
git clone <url-du-depot>
cd remonter-le-temps-qgis
make zip        # -> dist/remonter_le_temps-<version>.zip
# ou, pour installer directement dans le profil QGIS :
make install
```

**Dépendances : rien à installer manuellement.** Le plugin fonctionne dès
l'installation. Le panneau affiche en haut le moteur de recalage disponible :

- **OpenCV présent** → appariement AKAZE + RANSAC : précis, invariant à la
  rotation et à l'échelle. C'est le mode recommandé.
- **OpenCV absent** → repli automatique sur une corrélation de phase par
  tuiles, écrite en numpy pur (déjà livré avec QGIS). Aucune installation,
  mais l'appariement suppose que le niveau 1 a déjà mis le cliché à peu près
  en place, et il ne corrige qu'une translation par tuile.

Le bouton **Installer OpenCV** télécharge `opencv-python-headless` via pip
**dans le sous-dossier `libs/` de l'extension** — l'installation Python de
QGIS n'est pas modifiée, et *Retirer* suffit à revenir en arrière. Le dossier
`libs/` est ajouté à `sys.path` au démarrage du plugin.

Pourquoi ne pas livrer OpenCV directement dans le ZIP ? Parce qu'il faudrait
une roue binaire par système d'exploitation **et** par version de Python
(environ 90 Mo chacune) : le ZIP dépasserait le gigaoctet et resterait fragile.
L'installation à la demande est la pratique courante des extensions QGIS qui
ont des dépendances lourdes.

## Utilisation

L'interface est en deux onglets : **Recherche**, puis **Panier et
traitement** — les clichés se cochent et se traitent au même endroit.

1. Zoomer sur la zone voulue.
2. **Scanner l'emprise actuelle** avec une plage d'années → couche orange
   `PVA – Missions`, et la liste déroulante des missions se remplit.
3. Choisir une mission → **Charger les clichés** → couche bleue
   `PVA – Clichés`, c'est le tableau d'assemblage.
4. **Tracer un rectangle** sur la carte : les clichés couvrant la zone
   partent dans le panier. Plusieurs missions pouvant être chargées en même
   temps, la liste **« Sélection dans »** indique et permet de choisir la
   couche qui sera lue — c'est elle qui compte, pas la couche active du
   gestionnaire de couches. Sans couche chargée, le rectangle interroge
   directement le WFS.
5. Dans l'onglet **Panier**, l'arborescence groupe par année puis par mission.
   Chaque cliché affiche les coordonnées de son centre et son angle
   d'orientation ; l'infobulle donne toutes les métadonnées du WFS. Décocher
   ce qui ne sert pas, puis **Nettoyer décochés**.
6. **Aperçu du cliché** lit une version décimée du scan *directement sur le
   serveur* (GDAL `/vsicurl/`, lectures par plages HTTP) et l'affiche calée au
   niveau 1 : quelques Mo au lieu des centaines de Mo du fichier complet. Le
   curseur d'opacité permet de le comparer au fond de carte. Le bouton
   *Ouvrir la fiche sur remonterletemps.ign.fr* pointe le cliché sur le site
   officiel.
7. Si un cliché tombe de travers, réglez sa **rotation** (0/90/180/270°) ou
   cochez **Miroir** dans le panier : l'aperçu se recalcule aussitôt, sans
   retélécharger, et le réglage est mémorisé pour le traitement. L'orientation
   varie d'un cliché à l'autre au sein d'une même mission, chaque entrée garde
   donc la sienne.
8. Sous le panier, déplier **Traitement des clichés cochés** pour régler mode
   de découpe, niveau de calage, CRS, résolution et dossier de sortie, puis
   lancer le traitement — le bouton indique combien de clichés sont cochés.

Dans le gestionnaire de couches, tout est rangé sous un groupe
**Remonter le Temps**, avec un sous-groupe par mission : l'emprise, le tableau
d'assemblage, les centres, les aperçus et les rasters calés d'une même mission
restent ensemble.

Sortie :

```
<dossier>/01_scans_bruts/       scans .tif d'origine + métadonnées .json
<dossier>/02_cliches_decoupes/  clichés sans cadre
<dossier>/03_cliches_cales/     GeoTIFF calés (…_cale.tif / …_ortho.tif)
```

## Découpe des bords

Les scans argentiques comportent un pourtour de film, les repères de fond de
chambre et parfois un bandeau (numéro de mission, horloge, niveau à bulle).
Selon les campagnes ce pourtour est noir, gris ou clair : se fier à la
luminosité échoue une fois sur deux. Le mode *automatique* cherche donc le plus
long bloc contigu de lignes et de colonnes à forte **énergie de gradient** — la
zone photographiée se distingue par sa texture, le pourtour est lisse — puis
retranche une marge de sécurité (2 % par défaut) pour éliminer les repères qui
mordent sur l'image. En cas d'échec, l'ancien critère de luminosité prend le
relais, et le mode *marge fixe* reste disponible pour les cas atypiques.

Vérifiez le résultat : si des repères noirs subsistent aux coins du cliché
calé, la découpe n'a pas fonctionné et le calage en pâtira.

## Les trois niveaux de calage

**Niveau 1 — géométrie de prise de vue (calage métrique).** Plutôt que d'étirer
le cliché sur l'emprise approximative du tableau d'assemblage, le plugin
reconstruit la transformation :

- le **pas de numérisation** vient des tags TIFF du scan
  (`XResolution` + `ResolutionUnit`, en gérant le cas pouce et le cas
  centimètre) : 721 ppp donnent 35,2 µm ;
- multiplié par la **largeur en pixels**, il donne le format de plaque, qui est
  confronté aux formats normalisés (24×18, 18×18, 23×23…) — un écart signale
  une métadonnée douteuse ;
- multiplié par l'**échelle** du cliché, il donne la taille pixel au sol :
  35,2 µm × 11361 = 0,400 m/px ;
- le **centre** du cliché fixe la position, l'**angle du nord** fixe la
  rotation, et le décalage de découpe est pris en compte pour que le point
  principal reste au centre du scan brut.

La convention de signe de l'angle n'étant documentée nulle part, elle est levée
automatiquement en confrontant l'attribut à l'orientation grossière de
l'emprise. Si une pièce manque (tag de résolution absent, échelle non fournie),
le plugin retombe sur l'ajustement des 4 coins sur l'emprise IGN. L'image est correctement
orientée et à la bonne échelle, mais l'emprise IGN est elle-même approximative
(les PVA sont documentées comme « approximativement placées ») : compter
plusieurs dizaines de mètres d'écart. Instantané, aucune donnée externe.

**Niveau 2 — recalage sur l'ortho actuelle.** Un extrait de l'ortho IGN
courante est téléchargé sur l'emprise, puis apparié au cliché ancien : par
descripteurs AKAZE + homographie RANSAC si OpenCV est disponible, sinon par
corrélation de phase sur une grille de tuiles (numpy seul). Les inliers fournissent une grille de
points d'appui, appliquée en transformation polynomiale (ordre 1 ou 2). Cela
détermine aussi l'orientation du cliché (0/90/180/270°) sans métadonnée. C'est
le mode par défaut, et il supprime l'essentiel de l'erreur planimétrique.

**Niveau 3 — orthorectification sur MNT.** Les points d'appui du niveau 2 sont
complétés par leur altitude RGE ALTI, puis une **résection spatiale** (DLT à 11
paramètres) reconstruit la matrice de projection du cliché. Chaque pixel de
sortie est ensuite reprojeté à travers le MNT. C'est le seul niveau qui corrige
le **devers dû au relief** — indispensable en zone accidentée, superflu en
plaine.

## Limites, en toute franchise

- **Aucun test en conditions réelles** : le code n'a pas tourné dans QGIS ni
  contre les serveurs IGN. Les vérifications faites sont des tests numériques
  sur données synthétiques, pas des tests d'intégration.
- Sans OpenCV, l'apparieur de secours ne gère que des translations locales :
  il rattrape un décalage, pas une erreur d'orientation. Si le niveau 1 place
  le cliché avec 90° d'écart, corrigez d'abord avec le champ *Rotation ×90*.
- La distorsion de l'objectif n'est pas modélisée et la résection utilise un
  modèle projectif, pas les repères de fond de chambre. En terrain très
  accidenté, l'erreur résiduelle peut rester métrique.
- Le niveau 3 traite chaque cliché isolément : pas d'aérotriangulation, donc
  pas de cohérence garantie entre clichés voisins, pas de MNS produit.
- Pour une chaîne photogrammétrique complète (repères de fond de chambre,
  aérotriangulation, MNS, égalisation radiométrique), l'outil officiel de l'IGN
  est [Pompei](https://github.com/IGNF/Pompei) (MicMac). Ce plugin vise
  l'exploitation rapide de quelques clichés dans QGIS, pas la production.
- Les scans font couramment 100 à 500 Mo une fois décompressés : prévoir la
  place disque et traiter par lots raisonnables.

## Dépannage

**`AttributeError: 'NoneType' object has no attribute 'write'`** — sous Windows,
QGIS démarre sans console : `sys.stderr` vaut `None`, et la bibliothèque qui
essayait d'afficher un avertissement échoue à l'écrire. Le message que vous
voyez n'est donc pas la cause, seulement le symptôme. Depuis la 1.3.1 le plugin
redirige ces flux vers le journal des messages de QGIS (*Vue → Panneaux →
Journal des messages*, onglet « Remonter le temps »), où le message réel
s'affiche.

Cette erreur peut survenir **dès l'installation du ZIP**, avant toute
utilisation : QGIS charge l'extension immédiatement, et celle-ci importe numpy
et GDAL. Le conflit préexistait donc sur le poste ; l'extension n'a fait que le
révéler. Depuis la 1.3.2, ce cas charge une extension de repli qui affiche le
diagnostic au lieu d'une trace tronquée.

La cause la plus fréquente est **deux copies de numpy** : celle de QGIS et une
autre installée dans le site utilisateur
(`%AppData%\Roaming\Python\Python3xx\site-packages`), qui passe en premier
sur `sys.path`. Une extension C compilée contre l'une et exécutée avec l'autre
déclenche exactement ce message. Le bouton **Diagnostic** du panneau les liste.
Pour corriger, depuis l'*OSGeo4W Shell* :

```
python -m pip uninstall numpy          # retire la copie du site utilisateur
python -c "import numpy; print(numpy.__version__, numpy.__file__)"
```

Le chemin affiché doit pointer dans l'installation QGIS, pas dans `Roaming`.

## Licence des données

Photographies aériennes IGN : licence ouverte / réutilisation libre, mention
« © IGN » recommandée.

## Développement

```
.
├── remonter_le_temps/      code de l'extension (c'est ce dossier qui est zippé)
│   ├── __init__.py         classFactory + bootstrap de sys.path
│   ├── plugin.py           action de barre d'outils, cycle de vie
│   ├── dock.py             panneau, tâches d'arrière-plan, couches mémoire
│   ├── ign_api.py          WFS pva, téléchargement, WMS ortho/MNT, RGE ALTI
│   ├── crop.py             détection et découpe du cadre de scan
│   ├── georef.py           géométrie, appariement, résection, ortho MNT
│   ├── pipeline.py         enchaînement par cliché
│   ├── deps.py             installation confinée d'OpenCV
│   └── icons/
├── scripts/build_zip.py    fabrique le ZIP installable
├── .github/workflows/      construction et publication sur tag
└── Makefile
```

Commandes utiles :

```bash
make zip        # produit dist/remonter_le_temps-<version>.zip
make install    # copie l'extension dans le profil QGIS local
make lint       # vérification syntaxique
```

Pour publier une version : bumper `version=` dans
`remonter_le_temps/metadata.txt`, compléter `CHANGELOG.md`, puis

```bash
git tag v1.0.0 && git push --tags
```

La CI construit le ZIP et l'attache à la release GitHub.

### Conventions

- Code compatible Python 3.9+ (versions de QGIS LTR encore répandues).
- Aucune dépendance obligatoire hors de ce que QGIS embarque
  (PyQGIS, GDAL, numpy). OpenCV reste strictement optionnel.
- Tout appel réseau se fait dans une `QgsTask`, jamais dans le fil de l'IHM.

## Licence

GPL-3.0-only, comme QGIS. Voir `LICENSE`.

## Crédits

Les points d'entrée des services PVA de la Géoplateforme ont été identifiés à
partir de [IGNF/Pompei](https://github.com/IGNF/Pompei) (IGN, GPL-3.0), qui
reste la référence pour la production photogrammétrique sérieuse.

