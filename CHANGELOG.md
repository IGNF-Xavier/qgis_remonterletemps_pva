# Journal des modifications

Le format suit [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/) et le
versionnement sémantique.

## [1.10.0] - 2026-08-12

### Modifié
- **Les onglets Panier et Traitement fusionnent** en un seul, « Panier et
  traitement » : on cochait les clichés dans l'un pour valider leur traitement
  dans l'autre, sans jamais voir les deux ensemble. Les réglages de traitement
  sont désormais sous le panier, dans un bloc dépliable, et le bouton de
  lancement est toujours visible.
- Le bouton de traitement indique en continu le nombre de clichés cochés et se
  désactive quand il n'y en a aucun.
- Libellés clarifiés entre les réglages propres à un cliché (rotation, miroir)
  et leurs équivalents globaux appliqués à toute la sélection.

## [1.9.0] - 2026-08-12

### Ajouté
- Liste **« Sélection dans »** : la couche interrogée par le rectangle est
  choisie explicitement, et non plus devinée. Elle prime sur la couche active
  du gestionnaire de couches, de sorte que ce qui est affiché correspond
  toujours à ce qui est lu.
- Activer l'outil rectangle rappelle dans le journal quelle couche sera lue.
- Sans couche de clichés chargée, une boîte de dialogue explique la marche à
  suivre et propose d'interroger directement le WFS.
- La liste se met à jour automatiquement quand des couches sont ajoutées ou
  supprimées du projet.

## [1.8.1] - 2026-08-11

### Corrigé
- **Sélection au rectangle sans effet** : depuis le regroupement par mission,
  plusieurs couches de clichés coexistent et le plugin interrogeait la
  première trouvée — souvent celle d'une mission précédente, située
  ailleurs — d'où un panier vide. La dernière couche chargée est désormais
  mémorisée et utilisée.
- Extraction de l'anneau extérieur unifiée et tolérante aux géométries
  multipolygones, partout où une emprise est lue.
- Si aucun clichés de la couche ne tombe dans le rectangle, le WFS est
  interrogé automatiquement au lieu de renvoyer « 0 cliché ajouté ».
- Messages explicites indiquant combien de clichés étaient déjà au panier ou
  ont été ignorés faute d'identifiant.

## [1.8.0] - 2026-08-11

### Ajouté
- Toutes les couches sont rangées dans un groupe **Remonter le Temps**, avec
  un sous-groupe éponyme par mission : emprise, clichés, centres, aperçus et
  rasters calés d'une même mission se retrouvent ensemble. La mission d'un
  raster produit est retrouvée via son fichier `.json` de métadonnées.
- Les groupes de missions précédentes se replient à l'ouverture d'une nouvelle.

## [1.7.0] - 2026-08-11

### Ajouté
- **Rotation et miroir par cliché**, réglés dans le panier, mémorisés dans
  l'entrée et réutilisés tels quels au téléchargement et au calage.
  L'orientation n'est pas constante au sein d'une même mission : un réglage
  global ne suffisait pas.
- Les deux réglages suivent le cliché sélectionné dans l'arborescence et le
  libellé indique ceux qui ne sont pas au réglage par défaut.
- Modifier la rotation **rafraîchit l'aperçu immédiatement**.
- Mise en cache de l'aperçu décimé : changer la rotation ne retélécharge plus
  le scan, le recalcul est quasi instantané.

### Modifié
- La correction de rotation de l'onglet Traitement devient une correction
  globale, cumulée avec le réglage propre à chaque cliché.

## [1.6.0] - 2026-08-11

### Corrigé
- **Couche « Centres clichés » jamais créée** : `QgsPalLayerSettings.OverPoint`
  ne résout plus vers le bon enum sous QGIS 3.40 et levait un `TypeError` qui
  interrompait la fonction avant l'ajout de la couche. L'enum est résolu selon
  la version, et l'étiquetage est isolé : il ne peut plus empêcher la couche
  d'exister.
- **Aperçu nécessitant deux clics** après suppression manuelle de la couche :
  le plugin croyait l'aperçu encore actif et consommait le premier clic à le
  « retirer ». L'existence de la couche est désormais vérifiée.
- La correction de rotation était ignorée par le calage métrique : elle
  s'applique maintenant dans tous les modes, en s'ajoutant à l'angle du nord.

### Ajouté
- Correction de rotation 0/90/180/270° et bascule **Miroir** (scan numérisé
  côté émulsion), disponibles pour l'aperçu comme pour le traitement complet.

## [1.5.0] - 2026-08-11

### Corrigé
- **Un échec du recalage ne fait plus perdre le cliché.** Une erreur au niveau
  2 ou 3 renvoyait l'exception jusqu'à la tâche, qui abandonnait le cliché :
  « 0 raster produit » alors que le calage de niveau 1 existait déjà. Les
  raffinements sont désormais isolés et le meilleur résultat obtenu est
  conservé.
- **`cv2` sans `AKAZE_create`** : certaines roues d'OpenCV n'embarquent pas
  AKAZE. Le détecteur est choisi parmi AKAZE, ORB, BRISK, SIFT et KAZE selon
  ce qui est réellement disponible, avec la norme de distance correspondante.
- **Échelle absente du WFS** : au lieu de retomber sur l'ajustement des quatre
  coins, l'échelle est déduite de l'emprise et le calage conserve le centre du
  cliché et l'angle d'orientation **continu** (272° et non 90° arrondi).

### Ajouté
- Détection des scans **en miroir** : le recalage teste l'image et son
  symétrique, et signale le cas dans le journal.
- Rapport OpenCV dans le diagnostic (version, chemin, détecteurs présents).

### Modifié
- Centres des clichés en pastilles jaunes cerclées de noir, numéros en gras
  avec halo blanc.

## [1.4.0] - 2026-08-11

### Corrigé
- **Découpe du cadre** : la détection reposait sur la luminosité et ne voyait
  rien quand le pourtour du scan est gris ou clair, laissant les repères de
  fond de chambre dans l'image et faussant le calage. Elle s'appuie désormais
  sur l'énergie de gradient (la zone photographiée se distingue par sa
  texture, pas par sa luminosité), avec repli sur l'ancien critère.

### Ajouté
- Contrôle de cohérence du calage métrique contre l'emprise du tableau
  d'assemblage : un écart de centre ou d'échelle aberrant fait basculer sur
  l'ajustement d'emprise et l'écart est journalisé.
- Couche **Emprise mission** mise en évidence dès qu'une mission est choisie.
- Couche **Centres clichés**, étiquetée par numéro de cliché.

### Modifié
- Le panier s'ouvre replié par année, avec le nombre de clichés par année et
  par mission.

## [1.3.2] - 2026-08-11

### Corrigé
- L'échec d'import de numpy ou de GDAL ne remonte plus une trace illisible :
  une extension de repli se charge et affiche un diagnostic explicite avec la
  marche à suivre. numpy et GDAL ne sont plus importés qu'après la
  sécurisation des flux standards.

### Ajouté
- Contrôle de compatibilité numpy / bindings GDAL (`check_numpy_abi`), inclus
  dans le rapport de diagnostic.

## [1.3.1] - 2026-08-11

### Corrigé
- `sys.stdout` / `sys.stderr` valant `None` sous Windows (QGIS démarre sans
  console) sont remplacés par un flux qui écrit dans le journal des messages.
  Sans cela, toute bibliothèque tentant d'émettre un avertissement provoquait
  `AttributeError: 'NoneType' object has no attribute 'write'`, en masquant le
  message d'origine.
- `libs/` est ajouté en **fin** de `sys.path` et non plus en tête, pour ne
  jamais masquer les paquets de QGIS (numpy en particulier).
- L'installation d'OpenCV utilise `--no-deps` et `PYTHONNOUSERSITE=1` : pip
  n'installe plus sa propre copie de numpy à côté de celle de QGIS.

### Ajouté
- Bouton **Diagnostic** : liste les copies de numpy présentes sur `sys.path`,
  le contenu de `libs/`, l'emplacement de cv2 et du site utilisateur.

## [1.3.0] - 2026-08-11

### Documentation
- Statut **expérimental** annoncé explicitement en tête du README : le code
  n'a pas été exécuté dans QGIS ni contre les serveurs IGN, seules les briques
  géométriques sont validées numériquement.

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
