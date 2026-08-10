# -*- coding: utf-8 -*-
"""Panneau lateral du plugin."""

import os
import traceback

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtWidgets import (
    QCheckBox, QComboBox, QDockWidget, QDoubleSpinBox, QFormLayout, QGroupBox,
    QHBoxLayout, QLabel, QMessageBox, QProgressBar, QPlainTextEdit,
    QPushButton, QSpinBox, QVBoxLayout, QWidget)

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsFeature,
    QgsField, QgsFillSymbol, QgsGeometry, QgsPointXY, QgsProject,
    QgsRasterLayer, QgsTask, QgsApplication, QgsVectorLayer)
from qgis.gui import QgsFileWidget, QgsProjectionSelectionWidget

from qgis.PyQt.QtCore import QVariant

from . import deps, ign_api, pipeline

MISSION_FIELDS = [
    ("dataset_identifier", QVariant.String),
    ("dataset_idta", QVariant.String),
    ("date_mission", QVariant.String),
    ("titre", QVariant.String),
    ("support", QVariant.String),
    ("couleur", QVariant.String),
    ("resolution", QVariant.Double),
    ("focale", QVariant.Double),
]

CLICHE_FIELDS = [
    ("image_identifier", QVariant.String),
    ("dataset_identifier", QVariant.String),
    ("date_cliche", QVariant.String),
    ("numero", QVariant.String),
    ("x", QVariant.Double),
    ("y", QVariant.Double),
]


# ==========================================================================
# taches d'arriere-plan
# ==========================================================================
class FetchTask(QgsTask):
    def __init__(self, kind, bbox, year_min=None, year_max=None, dataset=None):
        QgsTask.__init__(self, u"Remonter le temps : %s" % kind,
                         QgsTask.CanCancel)
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
            for i, (props, ring) in enumerate(self.items):
                if self.isCanceled():
                    return False
                self.message.emit(u"[%d/%d] %s"
                                  % (i + 1, total, props["image_identifier"]))
                try:
                    out = pipeline.process_cliche(
                        props, ring, self.options,
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
        self._build_ui()

    # ---------------------------------------------------------------- UI
    def _build_ui(self):
        root = QWidget()
        lay = QVBoxLayout(root)
        lay.setContentsMargins(6, 6, 6, 6)

        # --- dependances
        g0 = QGroupBox(u"0. Moteur de recalage")
        f0 = QVBoxLayout(g0)
        self.lbl_dep = QLabel()
        self.lbl_dep.setWordWrap(True)
        f0.addWidget(self.lbl_dep)
        hdep = QHBoxLayout()
        self.btn_install = QPushButton(u"Installer OpenCV")
        self.btn_install.clicked.connect(self.install_opencv)
        hdep.addWidget(self.btn_install)
        self.btn_uninstall = QPushButton(u"Retirer")
        self.btn_uninstall.clicked.connect(self.uninstall_opencv)
        hdep.addWidget(self.btn_uninstall)
        f0.addLayout(hdep)
        lay.addWidget(g0)

        # --- tableau d'assemblage
        g1 = QGroupBox(u"1. Tableau d'assemblage")
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

        self.btn_missions = QPushButton(u"Charger les missions (emprise carte)")
        self.btn_missions.clicked.connect(self.load_missions)
        f1.addRow(self.btn_missions)

        self.btn_cliches = QPushButton(u"Charger les cliches des missions selectionnees")
        self.btn_cliches.clicked.connect(self.load_cliches)
        f1.addRow(self.btn_cliches)

        self.btn_cliches_all = QPushButton(u"...ou tous les cliches de l'emprise")
        self.btn_cliches_all.clicked.connect(lambda: self.load_cliches(all_missions=True))
        f1.addRow(self.btn_cliches_all)
        lay.addWidget(g1)

        # --- traitement
        g2 = QGroupBox(u"2. Decoupe et calage")
        f2 = QFormLayout(g2)

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
        self.spn_res.setValue(0.0)
        self.spn_res.setSpecialValueText(u"auto")
        self.spn_res.setSuffix(u" m/px")
        f2.addRow(u"Resolution", self.spn_res)

        self.spn_rot = QSpinBox()
        self.spn_rot.setRange(0, 3)
        f2.addRow(u"Rotation x90 (niveau 1)", self.spn_rot)

        self.out_dir = QgsFileWidget()
        self.out_dir.setStorageMode(QgsFileWidget.GetDirectory)
        f2.addRow(u"Dossier de sortie", self.out_dir)

        self.chk_add = QCheckBox(u"Ajouter les resultats au projet")
        self.chk_add.setChecked(True)
        f2.addRow(self.chk_add)

        self.btn_run = QPushButton(u"Traiter les cliches selectionnes")
        self.btn_run.clicked.connect(self.run_processing)
        f2.addRow(self.btn_run)
        lay.addWidget(g2)

        self.progress = QProgressBar()
        self.progress.setValue(0)
        lay.addWidget(self.progress)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        lay.addWidget(self.log, 1)

        self.setWidget(root)
        self.refresh_deps()

    # ----------------------------------------------------------- dependances
    def refresh_deps(self):
        if deps.have_cv2():
            self.lbl_dep.setText(
                u"<b style='color:#2a7'>OpenCV %s detecte</b> - appariement "
                u"AKAZE + RANSAC (precis, gere la rotation)."
                % deps.cv2_version())
            self.btn_install.setEnabled(False)
            self.btn_uninstall.setEnabled(os.path.isdir(deps.LIBS_DIR))
        else:
            self.lbl_dep.setText(
                u"<b style='color:#c60'>OpenCV absent</b> - repli sur la "
                u"correlation de phase (numpy, fonctionne sans rien installer, "
                u"mais moins robuste). L'installation se fait dans le dossier "
                u"de l'extension, sans toucher a l'installation Python de QGIS.")
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

    def uninstall_opencv(self):
        deps.uninstall_opencv()
        self.say(u"Dossier libs supprime. Redemarrez QGIS pour liberer OpenCV.")
        self.refresh_deps()

    # ------------------------------------------------------------ helpers
    def say(self, msg):
        self.log.appendPlainText(msg)

    def canvas_bbox_3857(self):
        canvas = self.iface.mapCanvas()
        extent = canvas.extent()
        src = canvas.mapSettings().destinationCrs()
        dst = QgsCoordinateReferenceSystem("EPSG:3857")
        if src != dst:
            tr = QgsCoordinateTransform(src, dst, QgsProject.instance())
            extent = tr.transformBoundingBox(extent)
        return (extent.xMinimum(), extent.yMinimum(),
                extent.xMaximum(), extent.yMaximum())

    def _make_layer(self, name, fields, features, color, rlt_type):
        uri = "Polygon?crs=EPSG:3857"
        for fname, ftype in fields:
            uri += "&field=%s:%s" % (
                fname, "double" if ftype == QVariant.Double else "string")
        layer = QgsVectorLayer(uri, name, "memory")
        prov = layer.dataProvider()

        feats = []
        for gj in features:
            geom = gj.get("geometry") or {}
            coords = geom.get("coordinates")
            if not coords:
                continue
            rings = coords[0] if geom.get("type") == "Polygon" else coords[0][0]
            pts = [QgsPointXY(float(c[0]), float(c[1])) for c in rings]
            feat = QgsFeature(layer.fields())
            feat.setGeometry(QgsGeometry.fromPolygonXY([pts]))
            props = gj.get("properties", {})
            ident = (gj.get("id") or "").split(".", 1)[-1]
            for fname, ftype in fields:
                val = props.get(fname)
                if val is None and fname in ("image_identifier",
                                             "dataset_identifier"):
                    val = ident
                feat.setAttribute(fname, val)
            feats.append(feat)

        prov.addFeatures(feats)
        layer.updateExtents()
        sym = QgsFillSymbol.createSimple({
            "color": "0,0,0,0",
            "outline_color": color,
            "outline_width": "0.5",
        })
        layer.renderer().setSymbol(sym)
        layer.setCustomProperty("rlt_type", rlt_type)
        QgsProject.instance().addMapLayer(layer)
        return layer

    def _find_layer(self, rlt_type):
        cur = self.iface.activeLayer()
        if (isinstance(cur, QgsVectorLayer) and
                cur.customProperty("rlt_type") == rlt_type):
            return cur
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.customProperty("rlt_type") == rlt_type:
                return lyr
        return None

    def _busy(self, state):
        for b in (self.btn_missions, self.btn_cliches,
                  self.btn_cliches_all, self.btn_run, self.btn_install,
                  self.btn_uninstall):
            b.setEnabled(not state)
        if not state:
            self.refresh_deps()

    # ------------------------------------------------------------ actions
    def load_missions(self):
        bbox = self.canvas_bbox_3857()
        self.say(u"Recherche des missions sur l'emprise courante...")
        task = FetchTask("missions", bbox,
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
        name = u"PVA - Missions %d-%d" % (self.year_min.value(),
                                          self.year_max.value())
        self._make_layer(name, MISSION_FIELDS, feats, "255,140,0,255", "missions")
        self.say(u"%d mission(s) chargee(s). Selectionnez-en une puis chargez "
                 u"ses cliches." % len(feats))

    def load_cliches(self, all_missions=False):
        bbox = self.canvas_bbox_3857()
        dataset = None
        if not all_missions:
            lyr = self._find_layer("missions")
            if lyr is None:
                QMessageBox.warning(self, u"Missions",
                                    u"Chargez d'abord la couche des missions.")
                return
            sel = lyr.selectedFeatures()
            if not sel:
                QMessageBox.warning(self, u"Missions",
                                    u"Selectionnez une mission dans la couche.")
                return
            dataset = sel[0]["dataset_identifier"]
            self.say(u"Chargement du tableau d'assemblage de la mission %s..."
                     % dataset)
        else:
            self.say(u"Chargement de tous les cliches de l'emprise...")

        task = FetchTask("cliches", bbox, dataset=dataset)
        task.progressChanged.connect(lambda v: self.progress.setValue(int(v)))
        task.taskCompleted.connect(lambda: self._cliches_done(task, dataset))
        task.taskTerminated.connect(lambda: self._failed(task))
        self._start(task)

    def _cliches_done(self, task, dataset):
        self._busy(False)
        feats = task.data.get("features", [])
        if not feats:
            self.say(u"Aucun cliche trouve.")
            return
        name = u"PVA - Cliches %s" % (dataset or u"emprise")
        self._make_layer(name, CLICHE_FIELDS, feats, "0,120,255,255", "cliches")
        self.say(u"%d cliche(s). Selectionnez ceux a traiter, puis lancez "
                 u"le traitement." % len(feats))

    def run_processing(self):
        lyr = self._find_layer("cliches")
        if lyr is None:
            QMessageBox.warning(self, u"Cliches",
                                u"Chargez d'abord une couche de cliches.")
            return
        feats = lyr.selectedFeatures() or list(lyr.getFeatures())
        if not feats:
            QMessageBox.warning(self, u"Cliches", u"Aucun cliche a traiter.")
            return
        outdir = self.out_dir.filePath()
        if not outdir:
            QMessageBox.warning(self, u"Sortie",
                                u"Choisissez un dossier de sortie.")
            return
        if len(feats) > 30:
            rep = QMessageBox.question(
                self, u"Volume",
                u"%d cliches vont etre telecharges et traites. Continuer ?"
                % len(feats))
            if rep != QMessageBox.Yes:
                return

        if self.cmb_level.currentIndex() >= 1 and not deps.have_cv2():
            rep = QMessageBox.question(
                self, u"OpenCV",
                u"Le recalage automatique est bien plus fiable avec OpenCV.\n\n"
                u"L'installer maintenant dans le dossier de l'extension ?\n"
                u"(Non = repli sur la correlation de phase en numpy)",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel)
            if rep == QMessageBox.Cancel:
                return
            if rep == QMessageBox.Yes:
                self.install_opencv()
                return

        opt = pipeline.Options()
        opt.outdir = outdir
        opt.out_crs = self.crs_widget.crs().authid() or "EPSG:2154"
        opt.level = self.cmb_level.currentIndex()
        opt.crop_mode = self.cmb_crop.currentIndex()
        opt.crop_margin = self.spn_margin.value()
        opt.fixed_margin = self.spn_margin.value() if opt.crop_mode == 2 else 8.0
        opt.resolution = self.spn_res.value()
        opt.rotation_steps = self.spn_rot.value()

        items = []
        for f in feats:
            props = {
                "image_identifier": f["image_identifier"],
                "dataset_identifier": f["dataset_identifier"],
            }
            if not props["image_identifier"] or not props["dataset_identifier"]:
                continue
            geom = f.geometry()
            pts = geom.asPolygon()
            if not pts:
                mp = geom.asMultiPolygon()
                if not mp:
                    continue
                pts = mp[0]
            ring = [(p.x(), p.y()) for p in pts[0]]
            items.append((props, ring))

        if not items:
            QMessageBox.warning(self, u"Cliches",
                                u"Attributs manquants sur la selection.")
            return

        self.say(u"--- Traitement de %d cliche(s) ---" % len(items))
        task = ProcessTask(items, opt)
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
                rl = QgsRasterLayer(path, os.path.splitext(os.path.basename(path))[0])
                if rl.isValid():
                    QgsProject.instance().addMapLayer(rl)
        self.iface.messageBar().pushMessage(
            u"Remonter le temps",
            u"%d cliche(s) cale(s)." % len(task.results),
            level=Qgis.Success, duration=6)

    def _failed(self, task):
        self._busy(False)
        msg = task.error or u"Tache annulee."
        self.say(u"ERREUR : %s" % msg)

    def _start(self, task):
        self._busy(True)
        self.progress.setValue(0)
        self._task = task
        QgsApplication.taskManager().addTask(task)
