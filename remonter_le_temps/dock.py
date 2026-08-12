# -*- coding: utf-8 -*-
"""Panneau lateral du plugin."""

import copy
import json
import os
import traceback

from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QColor, QDesktopServices
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFileDialog,
    QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPlainTextEdit,
    QPushButton, QSlider, QSpinBox, QTabWidget, QTreeWidget,
    QTreeWidgetItem, QVBoxLayout, QWidget)

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsFillSymbol, QgsGeometry, QgsMarkerSymbol, QgsPalLayerSettings,
    QgsPointXY, QgsProject, QgsRasterLayer, QgsRectangle, QgsTask,
    QgsTextBufferSettings, QgsTextFormat, QgsApplication, QgsVectorLayer,
    QgsVectorLayerSimpleLabeling)
from qgis.gui import QgsFileWidget, QgsMapToolExtent, QgsProjectionSelectionWidget

from . import deps, ign_api, pipeline
from .compat import (CHECKED, HORIZONTAL, ITEM_AUTOTRISTATE, ITEM_CHECKABLE,
                     ITEM_ENABLED, ITEM_SELECTABLE, MB_CANCEL, MB_NO, MB_YES,
                     PARTIAL, UNCHECKED, USER_ROLE)

ORIENT_KEYS = ("orientation", "orientation_nord", "azimut", "azimuth",
               "cap", "angle", "rotation")
DATE_KEYS = ("date_cliche", "date_mission", "date")


# ==========================================================================
# utilitaires metadonnees
# ==========================================================================
def prop(props, keys, default=None):
    for key in keys:
        if props.get(key) not in (None, ""):
            return props[key]
    return default


def year_of(props):
    date = prop(props, DATE_KEYS, "")
    text = str(date)[:4]
    return text if text.isdigit() else u"annee inconnue"


def centroid_of(ring):
    xs = [p[0] for p in ring[:-1]] or [p[0] for p in ring]
    ys = [p[1] for p in ring[:-1]] or [p[1] for p in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# ==========================================================================
# taches d'arriere-plan
# ==========================================================================
class FetchTask(QgsTask):
    def __init__(self, kind, bbox, year_min=None, year_max=None, dataset=None):
        QgsTask.__init__(self, u"Remonter le temps : %s" % kind, QgsTask.CanCancel)
        self.kind = kind
        self.bbox = bbox
        self.year_min = year_min
        self.year_max = year_max
        self.dataset = dataset
        self.data = None
        self.error = None

    def run(self):
        try:
            kw = dict(progress=self.setProgress, is_canceled=self.isCanceled)
            if self.kind == "missions":
                self.data = ign_api.fetch_missions(
                    self.bbox, self.year_min, self.year_max, **kw)
            else:
                self.data = ign_api.fetch_cliches(self.bbox, self.dataset, **kw)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = u"%s\n%s" % (exc, traceback.format_exc())
            return False


class InstallTask(QgsTask):
    message = pyqtSignal(str)

    def __init__(self):
        QgsTask.__init__(self, u"Installation d'OpenCV", QgsTask.CanCancel)
        self.ok = False
        self.detail = ""
        self.error = None

    def run(self):
        try:
            self.setProgress(10)
            self.ok, self.detail = deps.install_opencv(feedback=self.message.emit)
            self.setProgress(100)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = u"%s\n%s" % (exc, traceback.format_exc())
            return False


class PreviewTask(QgsTask):
    message = pyqtSignal(str)

    def __init__(self, props, ring, options):
        QgsTask.__init__(self, u"Remonter le temps : apercu", QgsTask.CanCancel)
        self.props = props
        self.ring = ring
        self.options = options
        self.result = None
        self.error = None

    def run(self):
        try:
            self.setProgress(10)
            self.result = pipeline.make_preview(
                self.props, self.ring, self.options, feedback=self.message.emit)
            self.setProgress(100)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = u"%s\n%s" % (exc, traceback.format_exc())
            return False


class ProcessTask(QgsTask):
    message = pyqtSignal(str)

    def __init__(self, items, options):
        QgsTask.__init__(self, u"Remonter le temps : traitement des cliches",
                         QgsTask.CanCancel)
        self.items = items
        self.options = options
        self.results = []
        self.error = None

    def run(self):
        try:
            total = len(self.items)
            for i, item in enumerate(self.items):
                props, ring = item[0], item[1]
                options = copy.copy(self.options)
                # reglage propre au cliche, cumule avec la correction globale
                options.extra_rotation_deg = (
                    self.options.extra_rotation_deg +
                    (float(item[2]) if len(item) > 2 else 0.0)) % 360.0
                if len(item) > 3 and item[3]:
                    options.mirror = not self.options.mirror
                if self.isCanceled():
                    return False
                self.message.emit(u"[%d/%d] %s"
                                  % (i + 1, total, props["image_identifier"]))
                try:
                    out = pipeline.process_cliche(
                        props, ring, options,
                        feedback=self.message.emit,
                        is_canceled=self.isCanceled)
                    if out:
                        self.results.append(out)
                except Exception as exc:  # noqa: BLE001
                    self.message.emit(u"  ECHEC : %s" % exc)
                self.setProgress(100.0 * (i + 1) / total)
            return True
        except Exception as exc:  # noqa: BLE001
            self.error = u"%s\n%s" % (exc, traceback.format_exc())
            return False


# ==========================================================================
# panneau
# ==========================================================================
class RltDock(QDockWidget):

    def __init__(self, iface, parent=None):
        QDockWidget.__init__(self, u"Remonter le Temps - PVA IGN", parent)
        self.iface = iface
        self.setObjectName("RltDock")
        self._task = None
        self._basket = {}          # ident -> {"props":..., "ring":...}
        self._missions = {}        # dataset_identifier -> entite GeoJSON
        self._cliches_layer_id = None   # couche de cliches active
        self._previews = {}        # ident -> id de couche raster
        self._rect_tool = None
        self._prev_tool = None
        self._build_ui()

    # ------------------------------------------------------------------ UI
    def _build_ui(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._tab_search(), u"Recherche")
        self.tabs.addTab(self._tab_basket(), u"Panier et traitement")
        lay.addWidget(self.tabs, 1)

        self.progress = QProgressBar()
        lay.addWidget(self.progress)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(90)
        lay.addWidget(self.log)

        self.setWidget(root)
        self.refresh_deps()
        self.refresh_target_combo()
        try:
            QgsProject.instance().layersAdded.connect(
                lambda *_: self.refresh_target_combo())
            QgsProject.instance().layersRemoved.connect(
                lambda *_: self.refresh_target_combo())
        except Exception:  # noqa: BLE001
            pass

    def _tab_search(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        gdep = QGroupBox(u"Moteur de recalage")
        vdep = QVBoxLayout(gdep)
        self.lbl_dep = QLabel()
        self.lbl_dep.setWordWrap(True)
        vdep.addWidget(self.lbl_dep)
        hdep = QHBoxLayout()
        self.btn_install = QPushButton(u"Installer OpenCV")
        self.btn_install.clicked.connect(self.install_opencv)
        hdep.addWidget(self.btn_install)
        self.btn_uninstall = QPushButton(u"Retirer")
        self.btn_uninstall.clicked.connect(self.uninstall_opencv)
        hdep.addWidget(self.btn_uninstall)
        self.btn_diag = QPushButton(u"Diagnostic")
        self.btn_diag.setToolTip(u"Verifie l'environnement Python : doublons de "
                                 u"numpy, site utilisateur, flux standards.")
        self.btn_diag.clicked.connect(self.run_diagnostic)
        hdep.addWidget(self.btn_diag)
        vdep.addLayout(hdep)
        lay.addWidget(gdep)

        g1 = QGroupBox(u"Tableau d'assemblage")
        f1 = QFormLayout(g1)
        self.year_min = QSpinBox()
        self.year_min.setRange(1900, 2100)
        self.year_min.setValue(1930)
        self.year_max = QSpinBox()
        self.year_max.setRange(1900, 2100)
        self.year_max.setValue(1975)
        hy = QHBoxLayout()
        hy.addWidget(self.year_min)
        hy.addWidget(QLabel(u"a"))
        hy.addWidget(self.year_max)
        f1.addRow(u"Periode", hy)

        self.btn_missions = QPushButton(u"Scanner l'emprise actuelle (missions)")
        self.btn_missions.clicked.connect(self.load_missions)
        f1.addRow(self.btn_missions)

        self.cmb_mission = QComboBox()
        self.cmb_mission.setMinimumContentsLength(18)
        f1.addRow(u"Mission", self.cmb_mission)

        self.btn_cliches = QPushButton(u"Charger les cliches de la mission")
        self.btn_cliches.clicked.connect(self.load_cliches)
        f1.addRow(self.btn_cliches)

        self.btn_cliches_all = QPushButton(u"...ou tous les cliches de l'emprise")
        self.btn_cliches_all.clicked.connect(lambda: self.load_cliches(True))
        f1.addRow(self.btn_cliches_all)

        self.cmb_target = QComboBox()
        self.cmb_target.setToolTip(
            u"Couche interrogee par la selection au rectangle. Plusieurs "
            u"missions peuvent etre chargees simultanement : c'est celle-ci "
            u"qui sera lue, quelle que soit la couche active dans le "
            u"gestionnaire de couches.")
        self.cmb_target.currentIndexChanged.connect(self._target_changed)
        f1.addRow(u"Selection dans", self.cmb_target)

        self.btn_rect = QPushButton(u"Tracer un rectangle -> panier")
        self.btn_rect.setCheckable(True)
        self.btn_rect.clicked.connect(self.toggle_rect_tool)
        f1.addRow(self.btn_rect)

        self.lbl_target = QLabel()
        self.lbl_target.setWordWrap(True)
        f1.addRow(self.lbl_target)
        lay.addWidget(g1)

        lay.addStretch(1)
        return page

    def _tab_basket(self):
        page = QWidget()
        lay = QVBoxLayout(page)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([u"Annee / mission / cliche"])
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self._item_changed)
        self.tree.currentItemChanged.connect(self._sync_orientation_widgets)
        lay.addWidget(self.tree, 1)

        h1 = QHBoxLayout()
        self.btn_preview = QPushButton(u"Apercu du cliche selectionne")
        self.btn_preview.clicked.connect(self.preview_current)
        h1.addWidget(self.btn_preview)
        self.btn_clean = QPushButton(u"Nettoyer decoches")
        self.btn_clean.clicked.connect(self.clean_unchecked)
        h1.addWidget(self.btn_clean)
        self.btn_empty = QPushButton(u"Vider")
        self.btn_empty.clicked.connect(self.empty_basket)
        h1.addWidget(self.btn_empty)
        lay.addLayout(h1)

        self.btn_web = QPushButton(u"Ouvrir la fiche sur remonterletemps.ign.fr")
        self.btn_web.clicked.connect(self.open_in_rlt)
        lay.addWidget(self.btn_web)

        h2 = QHBoxLayout()
        h2.addWidget(QLabel(u"Opacite"))
        self.sld_opacity = QSlider(HORIZONTAL)
        self.sld_opacity.setRange(0, 100)
        self.sld_opacity.setValue(100)
        self.sld_opacity.valueChanged.connect(self._apply_opacity)
        h2.addWidget(self.sld_opacity, 1)
        h2.addWidget(QLabel(u"Rotation"))
        self.cmb_rot_preview = QComboBox()
        self.cmb_rot_preview.addItems([u"0 deg", u"90 deg", u"180 deg",
                                       u"270 deg"])
        self.cmb_rot_preview.setToolTip(
            u"Rotation propre au cliche selectionne, memorisee dans le panier "
            u"et reutilisee au telechargement. L'orientation varie d'un cliche "
            u"a l'autre au sein d'une meme mission.")
        self.cmb_rot_preview.currentIndexChanged.connect(self._orientation_changed)
        h2.addWidget(self.cmb_rot_preview)
        self.chk_mirror = QCheckBox(u"Miroir")
        self.chk_mirror.setToolTip(u"Certains scans sont numerises cote "
                                   u"emulsion : aucune rotation ne les fera "
                                   u"coincider, seule la symetrie le peut.")
        self.chk_mirror.toggled.connect(self._orientation_changed)
        h2.addWidget(self.chk_mirror)
        lay.addLayout(h2)

        # Les reglages de traitement vivent sous le panier : on coche des
        # cliches et on lance leur traitement sans changer d'onglet.
        # Les reglages sont ranges dans un panneau escamotable place SOUS le
        # panier. Il est masque d'un bloc (et non widget par widget) pour que
        # la place soit reellement rendue au panier quand il est replie.
        self.btn_settings = QPushButton(u"\u25b8  Reglages de traitement")
        self.btn_settings.setCheckable(True)
        self.btn_settings.setToolTip(
            u"Decoupe, niveau de calage, projection, dossier de sortie.")
        self.btn_settings.toggled.connect(self._toggle_settings)
        lay.addWidget(self.btn_settings)

        self.settings_panel = QWidget()
        self.settings_panel.setLayout(self._process_form())
        self.settings_panel.setVisible(False)
        lay.addWidget(self.settings_panel)

        self.btn_run = QPushButton(u"Telecharger et traiter les cliches coches")
        self.btn_run.clicked.connect(self.run_processing)
        lay.addWidget(self.btn_run)
        return page

    def _toggle_settings(self, checked):
        self.settings_panel.setVisible(checked)
        self.btn_settings.setText(
            (u"\u25be  Reglages de traitement" if checked
             else u"\u25b8  Reglages de traitement"))

    def _process_form(self):
        """Reglages de traitement, integres sous le panier."""
        f2 = QFormLayout()
        f2.setContentsMargins(4, 4, 4, 4)
        f2.setLabelAlignment(Qt.AlignLeft)
        try:
            f2.setRowWrapPolicy(QFormLayout.WrapLongRows)
            f2.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        except Exception:  # noqa: BLE001
            pass

        self.cmb_crop = QComboBox()
        self.cmb_crop.addItems([u"Aucune",
                                u"Automatique (detection du cadre)",
                                u"Marge fixe"])
        self.cmb_crop.setCurrentIndex(1)
        f2.addRow(u"Bords du cliche", self.cmb_crop)

        self.spn_margin = QDoubleSpinBox()
        self.spn_margin.setRange(0.0, 25.0)
        self.spn_margin.setValue(1.5)
        self.spn_margin.setSuffix(u" %")
        f2.addRow(u"Marge de securite", self.spn_margin)

        self.cmb_level = QComboBox()
        self.cmb_level.addItems([
            u"1 - Emprise IGN (rapide, approx.)",
            u"2 - Recalage auto sur ortho actuelle",
            u"3 - Orthorectification sur MNT RGE ALTI",
        ])
        self.cmb_level.setCurrentIndex(1)
        f2.addRow(u"Calage", self.cmb_level)

        self.crs_widget = QgsProjectionSelectionWidget()
        self.crs_widget.setCrs(QgsCoordinateReferenceSystem("EPSG:2154"))
        f2.addRow(u"CRS de sortie", self.crs_widget)

        self.spn_res = QDoubleSpinBox()
        self.spn_res.setRange(0.0, 50.0)
        self.spn_res.setDecimals(2)
        self.spn_res.setSpecialValueText(u"auto")
        self.spn_res.setSuffix(u" m/px")
        f2.addRow(u"Resolution", self.spn_res)

        self.chk_metric = QCheckBox(u"Calage metrique (pas de scan x echelle)")
        self.chk_metric.setChecked(True)
        self.chk_metric.setToolTip(
            u"Deduit la taille pixel au sol des tags de resolution du scan et "
            u"de l'echelle du cliche, et place l'image sur son centre plutot "
            u"que sur l'emprise approximative du tableau d'assemblage.")
        f2.addRow(self.chk_metric)

        self.chk_orient = QCheckBox(u"Utiliser l'orientation du nord de l'IGN")
        self.chk_orient.setChecked(True)
        self.chk_orient.setToolTip(
            u"Le tableau d'assemblage fournit l'angle du nord de chaque "
            u"cliche : il determine le quart de tour a appliquer, sans avoir "
            u"a le deviner.")
        f2.addRow(self.chk_orient)

        self.chk_orient_inv = QCheckBox(u"Inverser le sens de l'orientation")
        f2.addRow(self.chk_orient_inv)

        self.spn_rot = QSpinBox()
        self.spn_rot.setRange(0, 3)
        f2.addRow(u"Rotation x90 (si pas d'orientation)", self.spn_rot)

        self.cmb_extra = QComboBox()
        self.cmb_extra.addItems([u"0 deg", u"90 deg", u"180 deg", u"270 deg"])
        self.cmb_extra.setToolTip(
            u"Correction appliquee a TOUS les cliches coches, en plus de la "
            u"rotation propre a chacun reglee juste au-dessus. Utile quand "
            u"toute une mission est de travers de la meme facon.")
        f2.addRow(u"Rotation : correction globale", self.cmb_extra)

        self.chk_mirror_run = QCheckBox(
            u"Miroir sur tous les cliches (cote emulsion)")
        f2.addRow(self.chk_mirror_run)

        self.out_dir = QgsFileWidget()
        self.out_dir.setStorageMode(QgsFileWidget.GetDirectory)
        try:
            self.out_dir.setFilePath(self._default_outdir())
        except Exception:  # noqa: BLE001
            pass
        f2.addRow(u"Dossier de sortie", self.out_dir)

        self.chk_json = QCheckBox(u"Exporter les metadonnees .json")
        self.chk_json.setChecked(True)
        f2.addRow(self.chk_json)

        self.chk_add = QCheckBox(u"Ajouter les resultats au projet")
        self.chk_add.setChecked(True)
        f2.addRow(self.chk_add)
        return f2

    # --------------------------------------------------------- dependances
    def refresh_deps(self):
        if deps.have_cv2():
            self.lbl_dep.setText(
                u"<b style='color:#2a7'>OpenCV %s detecte</b> - appariement "
                u"AKAZE + RANSAC." % deps.cv2_version())
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(os.path.isdir(deps.LIBS_DIR))
        else:
            self.lbl_dep.setText(
                u"<b style='color:#c60'>OpenCV absent</b> - repli sur la "
                u"correlation de phase (numpy). Installation confinee au "
                u"dossier de l'extension.")
            self.btn_install.setEnabled(True)
            self.btn_uninstall.setEnabled(False)

    def install_opencv(self):
        self.say(u"Installation d'OpenCV dans %s ..." % deps.LIBS_DIR)
        task = InstallTask()
        task.message.connect(self.say)
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._install_done(task))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def _install_done(self, task):
        self._busy(False)
        self.say(task.detail)
        self.refresh_deps()
        self.iface.messageBar().pushMessage(
            u"Remonter le temps", task.detail.splitlines()[0],
            level=Qgis.Success if task.ok else Qgis.Warning, duration=8)

    def run_diagnostic(self):
        self.say(deps.diagnose())
        ok, msg = deps.check_numpy_abi()
        if not ok:
            self.iface.messageBar().pushMessage(
                u"Remonter le temps", msg.splitlines()[0],
                level=Qgis.Critical, duration=12)
        self.tabs.setCurrentIndex(0)

    def uninstall_opencv(self):
        deps.uninstall_opencv()
        self.say(u"Dossier libs supprime. Redemarrez QGIS pour liberer OpenCV.")
        self.refresh_deps()

    # -------------------------------------------------------------- helpers
    def say(self, msg):
        self.log.appendPlainText(msg)

    def canvas_bbox_3857(self, rect=None):
        canvas = self.iface.mapCanvas()
        extent = rect if rect is not None else canvas.extent()
        src = canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem("EPSG:3857")
        if src != dst:
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            extent = tr.transformBoundingBox(extent)
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())

    def _busy(self, state):
        for b in (self.btn_missions, self.btn_cliches, self.btn_cliches_all,
                  self.btn_run, self.btn_install, self.btn_uninstall,
                  self.btn_preview, self.btn_web):
            b.setEnabled(not state)
        if not state:
            self.refresh_deps()

    def _start(self, task):
        self._busy(True)
        self.progress.setValue(0)
        self._task = task
        QgsApplication.taskManager().addTask(task)

    def _failed(self, task):
        self._busy(False)
        self.say(u"ERREUR : %s" % (task.error or u"Tache annulee."))

    # ------------------------------------------------------- couches memoire
    @staticmethod
    def _ring_of_geometry(geom):
        """Anneau exterieur d'une geometrie QGIS, polygone ou multipolygone."""
        if geom is None or geom.isEmpty():
            return None
        poly = geom.asPolygon()
        if not poly:
            multi = geom.asMultiPolygon()
            if not multi:
                return None
            poly = multi[0]
        if not poly:
            return None
        return [(p.x(), p.y()) for p in poly[0]]

    @staticmethod
    def _ring_of(gj):
        geom = gj.get("geometry") or {}
        coords = geom.get("coordinates")
        if not coords:
            return None
        if geom.get("type") == "Polygon":
            return [(float(c[0]), float(c[1])) for c in coords[0]]
        return [(float(c[0]), float(c[1])) for c in coords[0][0]]

    @staticmethod
    def _props_of(gj):
        props = dict(gj.get("properties") or {})
        ident = (gj.get("id") or "").split(".", 1)[-1]
        props.setdefault("image_identifier", ident)
        return props

    def _make_layer(self, name, features, color, rlt_type, mission=None):
        """Couche memoire dont les champs sont deduits des donnees WFS."""
        keys, numeric = [], set()
        for gj in features[:200]:
            for k, v in (gj.get("properties") or {}).items():
                if k not in keys:
                    keys.append(k)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    numeric.add(k)
                elif v is not None:
                    numeric.discard(k)
        for extra in ("image_identifier", "dataset_identifier"):
            if extra not in keys:
                keys.append(extra)

        uri = "Polygon?crs=EPSG:3857"
        for k in keys:
            uri += "&field=%s:%s" % (k, "double" if k in numeric else "string")
        layer = QgsVectorLayer(uri, name, "memory")

        feats = []
        for gj in features:
            ring = self._ring_of(gj)
            if not ring:
                continue
            props = self._props_of(gj)
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPolygonXY(
                [[QgsPointXY(x, y) for x, y in ring]]))
            for k in keys:
                feat.setAttribute(k, props.get(k))
            feats.append(feat)
        layer.dataProvider().addFeatures(feats)
        layer.updateExtents()

        layer.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "0,0,0,0", "outline_color": color, "outline_width": "0.5"}))
        layer.setCustomProperty("rlt_type", rlt_type)
        self._add_layer(layer, mission)
        return layer

    # ------------------------------------------------------- groupes de couches
    ROOT_GROUP = u"Remonter le Temps"

    def _group_for(self, mission=None):
        """
        Groupe de destination dans le gestionnaire de couches.

        Tout ce qui concerne une mission va dans un sous-groupe eponyme, sous
        un groupe racine commun. Sans mission (recherche sur emprise), on
        s'arrete au groupe racine.
        """
        root = QgsProject.instance().layerTreeRoot()
        parent = root.findGroup(self.ROOT_GROUP)
        if parent is None:
            parent = root.insertGroup(0, self.ROOT_GROUP)
        if not mission:
            return parent
        group = parent.findGroup(mission)
        if group is None:
            group = parent.addGroup(mission)
            # les missions deja traitees se replient pour garder l'arbre lisible
            for other in parent.findGroups():
                if other is not group:
                    other.setExpanded(False)
        return group

    def _add_layer(self, layer, mission=None):
        """Ajoute la couche au projet en la rangeant dans le bon groupe."""
        project = QgsProject.instance()
        try:
            group = self._group_for(mission)
            project.addMapLayer(layer, False)   # pas d'insertion a la racine
            group.insertLayer(0, layer)
        except Exception as exc:  # noqa: BLE001
            # le regroupement ne doit jamais empecher la couche d'apparaitre
            self.say(u"  (regroupement impossible : %s)" % exc)
            project.addMapLayer(layer)
        return layer

    @staticmethod
    def _mission_of_path(path):
        """Retrouve la mission d'un raster produit, via son .json de metadonnees."""
        img_id = os.path.splitext(os.path.basename(path))[0]
        for suffix in ("_cale", "_ortho", "_apercu"):
            if img_id.endswith(suffix):
                img_id = img_id[:-len(suffix)]
                break
        base = os.path.dirname(os.path.dirname(path))
        meta = os.path.join(base, "01_scans_bruts", "%s.json" % img_id)
        try:
            with open(meta, encoding="utf-8") as fh:
                return json.load(fh).get("dataset_identifier")
        except Exception:  # noqa: BLE001
            return None

    def refresh_target_combo(self):
        """Recense les couches de cliches presentes dans le projet."""
        current = self.cmb_target.currentData()
        layers = [l for l in QgsProject.instance().mapLayers().values()
                  if l.customProperty("rlt_type") == "cliches"]
        layers.sort(key=lambda l: l.name())

        self._refreshing_target = True
        try:
            self.cmb_target.clear()
            for layer in layers:
                self.cmb_target.addItem(layer.name(), layer.id())
            target = current or self._cliches_layer_id
            index = self.cmb_target.findData(target)
            if index < 0 and self.cmb_target.count():
                index = self.cmb_target.count() - 1
            if index >= 0:
                self.cmb_target.setCurrentIndex(index)
                self._cliches_layer_id = self.cmb_target.currentData()
        finally:
            self._refreshing_target = False

        if not layers:
            self.lbl_target.setText(
                u"<i>Aucune couche de cliches chargee. Scannez l'emprise, "
                u"choisissez une mission, puis chargez ses cliches : le "
                u"rectangle interrogera cette couche.</i>")
            self.btn_rect.setEnabled(True)   # le repli WFS reste possible
        else:
            self.lbl_target.setText(u"")
        return layers

    def _target_changed(self, *_args):
        if getattr(self, "_refreshing_target", False):
            return
        layer_id = self.cmb_target.currentData()
        if layer_id:
            self._cliches_layer_id = layer_id
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is not None:
                self.say(u"Selection au rectangle : couche \"%s\"."
                         % layer.name())

    def _find_layer(self, rlt_type):
        """
        Couche de travail pour un type donne.

        Depuis le regroupement par mission, plusieurs couches de cliches
        coexistent : prendre la premiere venue selectionnerait celle d'une
        autre mission, geographiquement ailleurs, et la selection ne
        retournerait rien. On privilegie donc la derniere chargee, puis la
        couche active.
        """
        project = QgsProject.instance()
        if rlt_type == "cliches":
            # le choix explicite du panneau prime sur la couche active : c'est
            # lui qui est affiche a l'utilisateur, il ne doit pas etre contredit
            chosen = self.cmb_target.currentData() or self._cliches_layer_id
            if chosen:
                layer = project.mapLayer(chosen)
                if layer is not None:
                    return layer
        cur = self.iface.activeLayer()
        if (isinstance(cur, QgsVectorLayer) and
                cur.customProperty("rlt_type") == rlt_type):
            return cur
        for lyr in project.mapLayers().values():
            if lyr.customProperty("rlt_type") == rlt_type:
                return lyr
        return None

    # ------------------------------------------------------------- recherche
    def load_missions(self):
        self.say(u"Scan des missions sur l'emprise courante...")
        task = FetchTask("missions", self.canvas_bbox_3857(),
                         self.year_min.value(), self.year_max.value())
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._missions_done(task))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def _missions_done(self, task):
        self._busy(False)
        feats = task.data.get("features", [])
        if not feats:
            self.say(u"Aucune mission sur cette emprise / periode.")
            return
        self._make_layer(u"PVA - Missions %d-%d" % (self.year_min.value(),
                                                    self.year_max.value()),
                         feats, "255,140,0,255", "missions")
        self.cmb_mission.clear()
        self._missions = {}
        rows = []
        for gj in feats:
            props = self._props_of(gj)
            ident = props.get("dataset_identifier") or props["image_identifier"]
            self._missions[ident] = gj
            rows.append((year_of(props), ident))
        for year, ident in sorted(set(rows)):
            self.cmb_mission.addItem(u"%s  %s" % (year, ident), ident)
        self.say(u"%d mission(s). Choisissez-en une, puis chargez ses cliches."
                 % len(feats))

    def load_cliches(self, all_missions=False):
        dataset = None
        if not all_missions:
            dataset = self.cmb_mission.currentData()
            if not dataset:
                QMessageBox.warning(self, u"Mission",
                                    u"Scannez d'abord l'emprise, puis choisissez "
                                    u"une mission.")
                return
            self.say(u"Chargement du tableau d'assemblage de %s..." % dataset)
            self._show_mission_extent(dataset)
        else:
            self.say(u"Chargement de tous les cliches de l'emprise...")
        task = FetchTask("cliches", self.canvas_bbox_3857(), dataset=dataset)
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._cliches_done(task, dataset))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def _show_mission_extent(self, dataset):
        """Emprise de la mission choisie, en evidence."""
        gj = self._missions.get(dataset)
        if gj is None:
            return
        name = u"PVA - Emprise mission %s" % dataset
        for lyr in list(QgsProject.instance().mapLayers().values()):
            if lyr.customProperty("rlt_type") == "emprise":
                QgsProject.instance().removeMapLayer(lyr.id())
        layer = self._make_layer(name, [gj], "227,26,28,255", "emprise",
                                 mission=dataset)
        layer.renderer().setSymbol(QgsFillSymbol.createSimple({
            "color": "227,26,28,25", "outline_color": "227,26,28,255",
            "outline_width": "1.2"}))
        layer.triggerRepaint()

    def _make_centres_layer(self, feats, dataset):
        """Centres des cliches, avec le numero en etiquette."""
        uri = "Point?crs=EPSG:3857&field=image_identifier:string&field=numero:string"
        layer = QgsVectorLayer(uri, u"PVA - Centres cliches %s"
                               % (dataset or u"emprise"), "memory")
        rows = []
        for gj in feats:
            ring = self._ring_of(gj)
            if not ring:
                continue
            props = self._props_of(gj)
            cx, cy = centroid_of(ring)
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(cx, cy)))
            feat.setAttribute("image_identifier", props.get("image_identifier"))
            label = props.get("numero")
            if label in (None, ""):
                label = str(props.get("image_identifier", ""))[-4:]
            feat.setAttribute("numero", str(label))
            rows.append(feat)
        layer.dataProvider().addFeatures(rows)
        layer.updateExtents()
        layer.renderer().setSymbol(QgsMarkerSymbol.createSimple({
            "name": "circle", "color": "255,235,0,255",
            "outline_color": "0,0,0,255", "outline_width": "0.4",
            "size": "3.4"}))

        # L'etiquetage ne doit jamais empecher la couche d'exister : selon la
        # version de QGIS, les enums de placement ont change de classe.
        try:
            settings = QgsPalLayerSettings()
            settings.fieldName = "numero"
            placement = None
            for owner, attr in ((Qgis, "LabelPlacement"),):
                holder = getattr(owner, attr, None)
                if holder is not None and hasattr(holder, "OverPoint"):
                    placement = holder.OverPoint
                    break
            if placement is None:
                placement = getattr(QgsPalLayerSettings, "OverPoint", None)
            if placement is not None:
                settings.placement = placement
            settings.yOffset = 1.5
            settings.xOffset = 1.5

            fmt = QgsTextFormat()
            fmt.setSize(11)
            font = fmt.font()
            font.setBold(True)
            fmt.setFont(font)
            fmt.setColor(QColor(0, 0, 0))
            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1.2)
            buf.setColor(QColor(255, 255, 255))
            fmt.setBuffer(buf)
            settings.setFormat(fmt)
            layer.setLabeling(QgsVectorLayerSimpleLabeling(settings))
            layer.setLabelsEnabled(True)
        except Exception as exc:  # noqa: BLE001
            self.say(u"  (etiquettes des centres desactivees : %s)" % exc)

        layer.setCustomProperty("rlt_type", "centres")
        self._add_layer(layer, dataset)
        return layer

    def _cliches_done(self, task, dataset):
        self._busy(False)
        feats = task.data.get("features", [])
        if not feats:
            self.say(u"Aucun cliche trouve.")
            return
        layer = self._make_layer(u"PVA - Cliches %s" % (dataset or u"emprise"),
                                 feats, "0,120,255,255", "cliches",
                                 mission=dataset)
        self._cliches_layer_id = layer.id()
        self.refresh_target_combo()
        self._make_centres_layer(feats, dataset)
        self.say(u"%d cliche(s) affiche(s). Tracez un rectangle pour remplir "
                 u"le panier." % len(feats))

    # -------------------------------------------------- selection rectangle
    def toggle_rect_tool(self, checked):
        canvas = self.iface.mapCanvas()
        if not checked:
            if self._prev_tool is not None:
                canvas.setMapTool(self._prev_tool)
            return

        layers = self.refresh_target_combo()
        if not layers:
            rep = QMessageBox.question(
                self, u"Aucune couche de cliches",
                u"Aucune couche de cliches n'est chargee.\n\n"
                u"Le plus sur est de scanner l'emprise, de choisir une "
                u"mission, puis de cliquer sur \"Charger les cliches de la "
                u"mission\" : le rectangle lira cette couche.\n\n"
                u"Tracer quand meme le rectangle ? Les cliches seront alors "
                u"demandes directement au WFS, toutes missions confondues.",
                MB_YES | MB_NO)
            if rep != MB_YES:
                self.btn_rect.setChecked(False)
                return
        else:
            layer = QgsProject.instance().mapLayer(self._cliches_layer_id)
            if layer is not None:
                self.say(u"Rectangle applique a la couche \"%s\" (modifiable "
                         u"dans la liste \"Selection dans\")." % layer.name())
        if self._rect_tool is None:
            self._rect_tool = QgsMapToolExtent(canvas)
            self._rect_tool.extentChanged.connect(self._rect_drawn)
        self._prev_tool = canvas.mapTool()
        canvas.setMapTool(self._rect_tool)
        self.say(u"Tracez un rectangle sur la carte.")

    def _rect_drawn(self, rect):
        self.btn_rect.setChecked(False)
        self.toggle_rect_tool(False)
        if rect is None or rect.isEmpty():
            return
        bbox = self.canvas_bbox_3857(QgsRectangle(rect))
        layer = self._find_layer("cliches")
        if layer is None:
            self.say(u"Aucune couche de cliches : interrogation du WFS sur le "
                     u"rectangle...")
            task = FetchTask("cliches", bbox)
            task.taskCompleted.connect(lambda: self._rect_from_wfs(task))
            task.taskTerminated.connect(lambda: self._failed(task))
            self._start(task)
            return

        sel = QgsGeometry.fromRect(QgsRectangle(*bbox))
        added, touched, skipped = 0, 0, 0
        for feat in layer.getFeatures():
            geom = feat.geometry()
            if geom is None or not geom.intersects(sel):
                continue
            touched += 1
            ring = self._ring_of_geometry(geom)
            if ring is None:
                skipped += 1
                continue
            props = {f.name(): feat[f.name()] for f in layer.fields()}
            props = {k: (None if v is None or (hasattr(v, "isNull") and v.isNull())
                         else v) for k, v in props.items()}
            if not props.get("image_identifier"):
                skipped += 1
                continue
            added += self._add_to_basket(props, ring)

        if touched == 0:
            # la couche affichee ne couvre peut-etre pas la zone : on demande
            # directement au WFS plutot que de laisser l'utilisateur devant un
            # panier vide sans explication
            self.say(u"Aucun cliche de la couche \"%s\" dans ce rectangle : "
                     u"interrogation du WFS..." % layer.name())
            task = FetchTask("cliches", bbox)
            task.taskCompleted.connect(lambda: self._rect_from_wfs(task))
            task.taskTerminated.connect(lambda: self._failed(task))
            self._start(task)
            return

        msg = u"%d cliche(s) ajoute(s) au panier" % added
        if added == 0 and touched:
            msg += u" (%d deja presents)" % (touched - skipped)
        if skipped:
            msg += u" - %d ignore(s) faute d'identifiant ou de geometrie" % skipped
        self.say(msg + u".")
        self.rebuild_tree()
        self.tabs.setCurrentIndex(1)

    def _rect_from_wfs(self, task):
        self._busy(False)
        feats = task.data.get("features", [])
        added = 0
        for gj in feats:
            ring = self._ring_of(gj)
            if ring:
                added += self._add_to_basket(self._props_of(gj), ring)
        if not feats:
            self.say(u"Le WFS ne renvoie aucun cliche sur ce rectangle. "
                     u"Elargissez la zone ou verifiez la periode.")
            return
        self.say(u"%d cliche(s) ajoute(s) au panier (sur %d trouve(s))."
                 % (added, len(feats)))
        self.rebuild_tree()
        self.tabs.setCurrentIndex(1)

    # ----------------------------------------------------------------- panier
    def _add_to_basket(self, props, ring):
        ident = props.get("image_identifier")
        if not ident or ident in self._basket:
            return 0
        self._basket[ident] = {"props": props, "ring": ring, "checked": True,
                               "rotation": 0, "mirror": False}
        return 1

    def rebuild_tree(self):
        self.tree.blockSignals(True)
        self.tree.clear()
        groups = {}
        for ident, entry in sorted(self._basket.items()):
            props = entry["props"]
            year = year_of(props)
            mission = props.get("dataset_identifier") or u"mission inconnue"
            node_y = groups.get(year)
            if node_y is None:
                node_y = QTreeWidgetItem(self.tree, [year])
                node_y.setData(0, USER_ROLE + 1, "year")
                node_y.setFlags(ITEM_ENABLED | ITEM_SELECTABLE |
                                ITEM_CHECKABLE | ITEM_AUTOTRISTATE)
                node_y.setCheckState(0, CHECKED)
                node_y.setExpanded(False)      # replie par annee
                groups[year] = node_y
            key = (year, mission)
            node_m = groups.get(key)
            if node_m is None:
                node_m = QTreeWidgetItem(node_y, [mission])
                node_m.setFlags(ITEM_ENABLED | ITEM_SELECTABLE |
                                ITEM_CHECKABLE | ITEM_AUTOTRISTATE)
                node_m.setCheckState(0, CHECKED)
                node_m.setExpanded(False)
                groups[key] = node_m

            cx, cy = centroid_of(entry["ring"])
            orient = prop(props, ORIENT_KEYS)
            legend = u"centre %.0f / %.0f (3857)" % (cx, cy)
            if orient is not None:
                try:
                    legend += u" - orientation %.0f deg" % float(orient)
                except (TypeError, ValueError):
                    legend += u" - orientation %s" % orient
            rot = entry.get("rotation", 0)
            if rot:
                legend += u" - ROTATION %d deg" % rot
            if entry.get("mirror"):
                legend += u" - MIROIR"
            item = QTreeWidgetItem(node_m, [u"%s\n    %s" % (ident, legend)])
            item.setFlags(ITEM_ENABLED | ITEM_SELECTABLE | ITEM_CHECKABLE)
            item.setCheckState(0, CHECKED if entry["checked"] else UNCHECKED)
            item.setData(0, USER_ROLE, ident)
            item.setToolTip(0, u"\n".join(
                u"%s : %s" % (k, v) for k, v in sorted(props.items())
                if v not in (None, "")))
        # suffixe le nombre de cliches sur chaque niveau replie
        for i in range(self.tree.topLevelItemCount()):
            node_y = self.tree.topLevelItem(i)
            total = 0
            for j in range(node_y.childCount()):
                node_m = node_y.child(j)
                total += node_m.childCount()
                node_m.setText(0, u"%s  (%d)"
                               % (node_m.text(0), node_m.childCount()))
            node_y.setText(0, u"%s  -  %d cliche(s)" % (node_y.text(0), total))

        self.tree.blockSignals(False)
        self.tree.setHeaderLabels([u"Panier (%d cliches)" % len(self._basket)])
        self._refresh_run_button()

    def _item_changed(self, item, _column):
        ident = item.data(0, USER_ROLE)
        if ident and ident in self._basket:
            self._basket[ident]["checked"] = item.checkState(0) == CHECKED
        self._refresh_run_button()

    def _refresh_run_button(self):
        """Le bouton annonce combien de cliches seront traites."""
        if not hasattr(self, "btn_run"):
            return
        count = sum(1 for v in self._basket.values() if v.get("checked"))
        self.btn_run.setText(
            u"Telecharger et traiter %d cliche(s) coche(s)" % count
            if count else u"Aucun cliche coche")
        self.btn_run.setEnabled(count > 0)

    def _sync_orientation_widgets(self, *_args):
        """Recharge rotation et miroir du cliche courant dans les widgets."""
        ident = self._current_ident()
        entry = self._basket.get(ident) if ident else None
        self._syncing = True
        try:
            self.cmb_rot_preview.setCurrentIndex(
                int((entry or {}).get("rotation", 0) / 90) % 4)
            self.chk_mirror.setChecked(bool((entry or {}).get("mirror", False)))
            self.cmb_rot_preview.setEnabled(entry is not None)
            self.chk_mirror.setEnabled(entry is not None)
        finally:
            self._syncing = False

    def _orientation_changed(self, *_args):
        """Enregistre le reglage sur le cliche courant et rafraichit l'apercu."""
        if getattr(self, "_syncing", False):
            return
        ident = self._current_ident()
        if not ident or ident not in self._basket:
            return
        entry = self._basket[ident]
        entry["rotation"] = 90 * self.cmb_rot_preview.currentIndex()
        entry["mirror"] = self.chk_mirror.isChecked()
        self._refresh_item_label(ident)
        # si un apercu est affiche, on le recalcule immediatement : le scan est
        # deja en cache, l'operation est quasi instantanee
        layer_id = self._previews.get(ident)
        if layer_id and QgsProject.instance().mapLayer(layer_id) is not None:
            self._remove_preview(ident)
            self.preview_current()

    def _refresh_item_label(self, ident):
        entry = self._basket.get(ident)
        item = self._find_item(ident)
        if entry is None or item is None:
            return
        cx, cy = centroid_of(entry["ring"])
        orient = prop(entry["props"], ORIENT_KEYS)
        legend = u"centre %.0f / %.0f (3857)" % (cx, cy)
        if orient is not None:
            try:
                legend += u" - orientation %.0f deg" % float(orient)
            except (TypeError, ValueError):
                legend += u" - orientation %s" % orient
        if entry.get("rotation"):
            legend += u" - ROTATION %d deg" % entry["rotation"]
        if entry.get("mirror"):
            legend += u" - MIROIR"
        self.tree.blockSignals(True)
        item.setText(0, u"%s\n    %s" % (ident, legend))
        self.tree.blockSignals(False)

    def _find_item(self, ident):
        for i in range(self.tree.topLevelItemCount()):
            node_y = self.tree.topLevelItem(i)
            for j in range(node_y.childCount()):
                node_m = node_y.child(j)
                for k in range(node_m.childCount()):
                    item = node_m.child(k)
                    if item.data(0, USER_ROLE) == ident:
                        return item
        return None

    def _current_ident(self):
        item = self.tree.currentItem()
        if item is None:
            return None
        return item.data(0, USER_ROLE)

    def clean_unchecked(self):
        gone = [k for k, v in self._basket.items() if not v["checked"]]
        for k in gone:
            self._remove_preview(k)
            del self._basket[k]
        self.say(u"%d cliche(s) retire(s) du panier." % len(gone))
        self.rebuild_tree()

    def empty_basket(self):
        for ident in list(self._basket):
            self._remove_preview(ident)
        self._basket.clear()
        self.rebuild_tree()

    # ----------------------------------------------------------------- apercu
    def preview_current(self):
        ident = self._current_ident()
        if not ident:
            QMessageBox.information(self, u"Apercu",
                                    u"Selectionnez un cliche dans le panier.")
            return
        # si la couche a ete supprimee a la main dans le gestionnaire, on ne
        # doit pas consommer le clic a "retirer" un apercu qui n'existe plus
        layer_id = self._previews.get(ident)
        if layer_id and QgsProject.instance().mapLayer(layer_id) is None:
            self._previews.pop(ident, None)
            layer_id = None
        if layer_id:
            self._remove_preview(ident)
            self.say(u"Apercu retire : %s" % ident)
            return
        outdir = self._ensure_outdir()
        if not outdir:
            return

        entry = self._basket[ident]
        opt = self._options(outdir)
        opt.extra_rotation_deg = float(entry.get("rotation", 0))
        opt.mirror = bool(entry.get("mirror", False))

        self.say(u"Apercu de %s (lecture partielle du scan distant)..." % ident)
        task = PreviewTask(entry["props"], entry["ring"], opt)
        task.message.connect(self.say)
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._preview_done(task, ident))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def open_in_rlt(self):
        ident = self._current_ident()
        if not ident:
            QMessageBox.information(self, u"Fiche IGN",
                                    u"Selectionnez un cliche dans le panier.")
            return
        entry = self._basket[ident]
        props = entry["props"]
        cx, cy = centroid_of(entry["ring"])
        tr = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:3857"),
                                    QgsCoordinateReferenceSystem("EPSG:4326"),
                                    QgsProject.instance())
        pt = tr.transform(QgsPointXY(cx, cy))
        url = ign_api.rlt_permalink(pt.x(), pt.y(), year_of(props),
                                    props.get("dataset_identifier"))
        QDesktopServices.openUrl(QUrl(url))
        self.say(u"Ouverture de %s" % url)

    def _preview_done(self, task, ident):
        self._busy(False)
        if not task.result:
            self.say(u"Apercu indisponible.")
            return
        layer = QgsRasterLayer(task.result, u"apercu %s" % ident)
        if not layer.isValid():
            self.say(u"Raster d'apercu invalide.")
            return
        mission = (self._basket.get(ident, {}).get("props", {})
                   .get("dataset_identifier"))
        self._add_layer(layer, mission)
        self._previews[ident] = layer.id()
        self._apply_opacity(self.sld_opacity.value())

    def _remove_preview(self, ident):
        layer_id = self._previews.pop(ident, None)
        if layer_id:
            QgsProject.instance().removeMapLayer(layer_id)

    def _apply_opacity(self, value):
        for layer_id in self._previews.values():
            layer = QgsProject.instance().mapLayer(layer_id)
            if layer is None:
                continue
            layer.renderer().setOpacity(value / 100.0)
            layer.triggerRepaint()

    # ------------------------------------------------------------ traitement
    def _default_outdir(self):
        base = os.path.join(os.path.expanduser("~"), "Documents")
        if not os.path.isdir(base):
            base = os.path.expanduser("~")
        return os.path.join(base, "RemonterLeTemps")

    def _ensure_outdir(self):
        """
        Dossier de sortie, sans imposer de detour par les reglages.

        Un dossier par defaut est propose des le depart ; on ne sollicite
        l'utilisateur que s'il l'a efface volontairement.
        """
        outdir = self.out_dir.filePath()
        if outdir:
            try:
                os.makedirs(outdir, exist_ok=True)
                return outdir
            except Exception as exc:  # noqa: BLE001
                QMessageBox.warning(self, u"Sortie",
                                    u"Dossier inutilisable :\n%s" % exc)
                return None

        suggestion = self._default_outdir()
        rep = QMessageBox.question(
            self, u"Dossier de sortie",
            u"Les scans et les cliches cales seront enregistres dans :\n\n%s"
            u"\n\nUtiliser ce dossier ? (Non : en choisir un autre)"
            % suggestion, MB_YES | MB_NO | MB_CANCEL)
        if rep == MB_CANCEL:
            return None
        if rep == MB_NO:
            chosen = QFileDialog.getExistingDirectory(
                self, u"Dossier de sortie", suggestion)
            if not chosen:
                return None
            suggestion = chosen
        try:
            os.makedirs(suggestion, exist_ok=True)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, u"Sortie",
                                u"Dossier inutilisable :\n%s" % exc)
            return None
        self.out_dir.setFilePath(suggestion)
        self.say(u"Dossier de sortie : %s" % suggestion)
        return suggestion

    def _options(self, outdir):
        # le formulaire est construit avec le panier : s'il manquait, mieux
        # vaut un message clair qu'un AttributeError
        if not hasattr(self, "cmb_level"):
            raise RuntimeError(u"Panneau de traitement non initialise.")
        opt = pipeline.Options()
        opt.outdir = outdir
        opt.out_crs = self.crs_widget.crs().authid() or "EPSG:2154"
        opt.level = self.cmb_level.currentIndex()
        opt.crop_mode = self.cmb_crop.currentIndex()
        opt.crop_margin = self.spn_margin.value()
        opt.fixed_margin = self.spn_margin.value() if opt.crop_mode == 2 else 8.0
        opt.resolution = self.spn_res.value()
        opt.rotation_steps = self.spn_rot.value()
        opt.write_json = self.chk_json.isChecked()
        opt.use_metric = self.chk_metric.isChecked()
        opt.extra_rotation_deg = 90.0 * self.cmb_extra.currentIndex()
        opt.mirror = self.chk_mirror_run.isChecked()
        opt.use_orientation = self.chk_orient.isChecked()
        opt.invert_orientation = self.chk_orient_inv.isChecked()
        return opt

    def run_processing(self):
        items = [(v["props"], v["ring"], v.get("rotation", 0),
                  bool(v.get("mirror", False)))
                 for v in self._basket.values() if v["checked"]]
        if not items:
            QMessageBox.warning(self, u"Panier",
                                u"Aucun cliche coche dans le panier.")
            return
        outdir = self._ensure_outdir()
        if not outdir:
            return
        if len(items) > 30:
            rep = QMessageBox.question(
                self, u"Volume",
                u"%d cliches vont etre telecharges et traites. Continuer ?"
                % len(items), MB_YES | MB_NO)
            if rep != MB_YES:
                return
        if self.cmb_level.currentIndex() >= 1 and not deps.have_cv2():
            rep = QMessageBox.question(
                self, u"OpenCV",
                u"Le recalage automatique est bien plus fiable avec OpenCV.\n\n"
                u"L'installer maintenant dans le dossier de l'extension ?\n"
                u"(Non = repli sur la correlation de phase en numpy)",
                MB_YES | MB_NO | MB_CANCEL)
            if rep == MB_CANCEL:
                return
            if rep == MB_YES:
                self.install_opencv()
                return

        self.say(u"--- Traitement de %d cliche(s) ---" % len(items))
        task = ProcessTask(items, self._options(outdir))
        task.message.connect(self.say)
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._process_done(task))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def _process_done(self, task):
        self._busy(False)
        self.say(u"Termine : %d raster(s) produit(s)." % len(task.results))
        if self.chk_add.isChecked():
            for path in task.results:
                name = os.path.splitext(os.path.basename(path))[0]
                layer = QgsRasterLayer(path, name)
                if layer.isValid():
                    self._add_layer(layer, self._mission_of_path(path))
        self.iface.messageBar().pushMessage(
            u"Remonter le temps", u"%d cliche(s) cale(s)." % len(task.results),
            level=Qgis.Success, duration=6)

    # ------------------------------------------------------------- fermeture
    def closeEvent(self, event):
        if self.btn_rect.isChecked():
            self.toggle_rect_tool(False)
        QDockWidget.closeEvent(self, event)
