import requests, os, json, math
# --- IMPORTS COMPATIBLES PYQT5 / PYQT6 ---
from qgis.PyQt.QtWidgets import (QDockWidget, QWidget, QVBoxLayout, QComboBox, 
                                 QPushButton, QLabel, QTreeWidget, QTreeWidgetItem, 
                                 QHBoxLayout, QSlider, QFileDialog, QProgressBar)
from qgis.PyQt.QtCore import Qt, QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest 

from qgis.core import (QgsProject, QgsVectorTileLayer, QgsVectorLayer, QgsFeature, 
                       QgsGeometry, QgsCoordinateTransform, QgsCoordinateReferenceSystem, 
                       QgsPointXY, QgsFillSymbol, QgsApplication, QgsRasterLayer, 
                       QgsCategorizedSymbolRenderer, QgsRendererCategory, QgsRectangle,
                       QgsNetworkAccessManager)
from qgis.gui import QgsMapToolExtent, QgsRubberBand
import processing

# --- OUTIL DE SÉLECTION RECTANGULAIRE ---
class MvtSelectionTool(QgsMapToolExtent):
    def __init__(self, canvas, callback):
        super().__init__(canvas)
        self.callback = callback
    def canvasReleaseEvent(self, event):
        ext = self.extent()
        super().canvasReleaseEvent(event)
        if not ext.isEmpty(): self.callback(ext)
        for item in self.canvas().scene().items():
            if isinstance(item, QgsRubberBand): item.reset()

# --- CLASSE PRINCIPALE ---
class RemonterLeTempsDockWidget(QDockWidget):
    def __init__(self, iface):
        super().__init__("GPF - RemonterLeTemps PVA")
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.missions_cache = {}
        self.active_tool = None
        self.dl_queue = []
        self.current_folder = ""
        QgsProject.instance().setCrs(QgsCoordinateReferenceSystem("EPSG:3857"))
        self.init_ui()

    def init_ui(self):
        container = QWidget(); layout = QVBoxLayout(container)
        
        layout.addWidget(QLabel("<b>1. Missions</b>"))
        self.btn_scan = QPushButton("Scanner l'emprise actuelle")
        self.btn_scan.clicked.connect(self.run_scan_processing)
        layout.addWidget(self.btn_scan)
        
        h_layout = QHBoxLayout()
        self.combo_annee = QComboBox(); self.combo_annee.addItem("--- Année ---")
        self.combo_annee.currentIndexChanged.connect(self.update_missions_list)
        self.combo_mission = QComboBox(); self.combo_mission.addItem("--- Mission ---")
        self.combo_mission.currentIndexChanged.connect(self.on_mission_change)
        h_layout.addWidget(self.combo_annee); h_layout.addWidget(self.combo_mission)
        layout.addLayout(h_layout)

        layout.addWidget(QLabel("<b>2. Sélection sur carte</b>"))
        self.btn_sel = QPushButton("Tracer un rectangle de sélection")
        self.btn_sel.setCheckable(True); self.btn_sel.clicked.connect(self.toggle_draw_tool)
        layout.addWidget(self.btn_sel)

        layout.addWidget(QLabel("<b>3. Panier</b>"))
        self.btn_toggle_all = QPushButton("Tout Cocher / Décocher")
        self.btn_toggle_all.clicked.connect(self.toggle_all_items)
        layout.addWidget(self.btn_toggle_all)

        self.tree_panier = QTreeWidget(); self.tree_panier.setColumnCount(3)
        self.tree_panier.setHeaderLabels(["Mission / Cliché / Détails", "N° Cliché", "Vue"])
        self.tree_panier.setColumnWidth(0, 300)
        self.tree_panier.itemChanged.connect(self.on_item_checked)
        self.tree_panier.itemClicked.connect(self.handle_eye_click)
        layout.addWidget(self.tree_panier)

        btn_layout = QHBoxLayout()
        self.btn_remove = QPushButton("Nettoyer décochés")
        self.btn_remove.clicked.connect(self.remove_unchecked_items)
        self.btn_reset = QPushButton("Réinitialiser Tout")
        self.btn_reset.clicked.connect(self.reset_all)
        btn_layout.addWidget(self.btn_remove); btn_layout.addWidget(self.btn_reset)
        layout.addLayout(btn_layout)

        layout.addWidget(QLabel("<b>4. Export & Avancement</b>"))
        self.progress_bar = QProgressBar(); self.progress_bar.setValue(0); self.progress_bar.hide()
        layout.addWidget(self.progress_bar)
        
        self.btn_dl = QPushButton("TÉLÉCHARGER LE PANIER (+ MÉTA)")
        self.btn_dl.setStyleSheet("background-color: #2e7d32; color: white; font-weight: bold; padding: 5px;")
        self.btn_dl.clicked.connect(self.start_async_download)
        layout.addWidget(self.btn_dl)
        
        self.slider_opacity = QSlider(Qt.Horizontal); self.slider_opacity.setRange(0, 100); self.slider_opacity.setValue(100)
        self.slider_opacity.valueChanged.connect(self.change_previsu_opacity)
        layout.addWidget(QLabel("Opacité :")); layout.addWidget(self.slider_opacity)
        self.setWidget(container)

    def load_preview(self, item, did, iid, meta):
        temp_dir = os.path.join(os.getenv('TEMP', '/tmp'), "ign_pva")
        if not os.path.exists(temp_dir): os.makedirs(temp_dir)
        try:
            angle_deg = float(meta.get('orientation', 0)) + 180
            angle_rad = math.radians(angle_deg)
            lyr_panier = self.setup_selection_layer()
            feat_geom = None
            for feat in lyr_panier.getFeatures():
                if feat['id'] == iid:
                    feat_geom = feat.geometry()
                    break
            if not feat_geom: return
            center = feat_geom.centroid().asPoint()

            for ext in [".tif", ".jpg", ".jp2"]:
                url = f"https://data.geopf.fr/telechargement/download/pva/{did}/{iid}{ext}"
                r = requests.get(url, stream=True, timeout=5)
                if r.status_code == 200:
                    img_p = os.path.join(temp_dir, f"{iid}{ext}")
                    with open(img_p, 'wb') as f: f.write(r.content)
                    tmp_lyr = QgsRasterLayer(img_p, "temp")
                    if not tmp_lyr.isValid(): continue
                    px_w, px_h = tmp_lyr.width(), tmp_lyr.height()
                    res = max(feat_geom.boundingBox().width() / px_w, feat_geom.boundingBox().height() / px_h)
                    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)
                    b, c, e, f = cos_a * res, sin_a * res, sin_a * res, -cos_a * res
                    x0 = center.x() - (px_w/2 * b + px_h/2 * c)
                    y0 = center.y() - (px_w/2 * e + px_h/2 * f)

                    vrt_p = img_p + ".vrt"
                    with open(vrt_p, "w") as f_v:
                        f_v.write(f'<VRTDataset rasterXSize="{px_w}" rasterYSize="{px_h}"><SRS>EPSG:3857</SRS>')
                        f_v.write(f'<GeoTransform>{x0}, {b}, {c}, {y0}, {e}, {f}</GeoTransform>')
                        for b_idx in range(1, 4):
                            f_v.write(f'<VRTRasterBand dataType="Byte" band="{b_idx}"><SimpleSource><SourceFilename>{img_p}</SourceFilename></SimpleSource></VRTRasterBand>')
                        f_v.write('</VRTDataset>')
                    rl = QgsRasterLayer(vrt_p, f"Vue - {iid}")
                    if rl.isValid():
                        QgsProject.instance().addMapLayer(rl)
                        rl.setOpacity(self.slider_opacity.value()/100.0)
                    break
        except Exception as e:
            self.iface.messageBar().pushMessage("Erreur", str(e), level=1)

    def add_to_basket(self, features, yr, m_key):
        lyr = self.setup_selection_layer()
        root = self.tree_panier.invisibleRootItem()
        p_m = next((root.child(i) for i in range(root.childCount()) if root.child(i).text(0) == m_key), None) or QTreeWidgetItem(root, [m_key, "", ""])
        p_m.setCheckState(0, Qt.Checked)
        p_m.setData(0, Qt.UserRole + 4, "mission")
        
        for f in features:
            p = f['properties']; iid = p['image_identifier']
            if any(p_m.child(j).text(0) == iid for j in range(p_m.childCount())): continue
            
            feat = QgsFeature(lyr.fields()); feat.setAttributes([iid, 1])
            geom = QgsGeometry.fromPolygonXY([[QgsPointXY(pt[0], pt[1]) for pt in f['geometry']['coordinates'][0]]])
            feat.setGeometry(geom); lyr.dataProvider().addFeatures([feat])
            
            # --- NIVEAU 2 : LE CLICHÉ ---
            child = QTreeWidgetItem(p_m, [iid, f"Cliché n°{p.get('numero_image', '?')}", ""])
            child.setCheckState(0, Qt.Checked)
            child.setData(0, Qt.UserRole + 1, p)
            child.setData(0, Qt.UserRole + 2, p['dataset_identifier'])
            child.setData(0, Qt.UserRole + 4, "cliche")
            child.setData(2, Qt.UserRole + 1, False)
            child.setIcon(2, QgsApplication.getThemeIcon("/mActionHideAllLayers.svg"))
            
            # --- NIVEAU 3 : LÉGENDE / MÉTADONNÉES ---
            center_pt = geom.centroid().asPoint()
            orient = p.get('orientation', '0')
            info_text = f"Coord. Centre : X={round(center_pt.x(), 2)} Y={round(center_pt.y(), 2)} | Orient: {orient}°"
            legend = QTreeWidgetItem(child, [info_text, "", ""])
            legend.setFlags(legend.flags() & ~Qt.ItemIsUserCheckable) # Pas de case à cocher pour la légende
            legend.setData(0, Qt.UserRole + 4, "legend")
            
        p_m.setExpanded(True)
        lyr.triggerRepaint(); self.canvas.refresh()

    def toggle_all_items(self):
        root = self.tree_panier.invisibleRootItem()
        for i in range(root.childCount()):
            m = root.child(i)
            target = Qt.Unchecked if m.checkState(0) == Qt.Checked else Qt.Checked
            m.setCheckState(0, target)
            for j in range(m.childCount()): m.child(j).setCheckState(0, target)

    def remove_unchecked_items(self):
        lyr = self.setup_selection_layer(); root = self.tree_panier.invisibleRootItem(); lyr.startEditing()
        i = 0
        while i < root.childCount():
            ms = root.child(i); k = 0
            while k < ms.childCount():
                c = ms.child(k)
                if c.data(0, Qt.UserRole + 4) == "cliche" and c.checkState(0) == Qt.Unchecked:
                    lyr.deleteFeatures([f.id() for f in lyr.getFeatures() if f['id'] == c.text(0)])
                    QgsProject.instance().removeMapLayers([l.id() for l in QgsProject.instance().mapLayers().values() if l.name() == f"Vue - {c.text(0)}"])
                    ms.removeChild(c)
                else: k += 1
            if ms.childCount() == 0: root.removeChild(ms)
            else: i += 1
        lyr.commitChanges(); self.canvas.refresh()

    def handle_eye_click(self, item, column):
        if column == 2 and item.data(0, Qt.UserRole + 4) == "cliche":
            iid, did, meta, is_vis = item.text(0), item.data(0, Qt.UserRole + 2), item.data(0, Qt.UserRole + 1), item.data(2, Qt.UserRole + 1)
            if not is_vis: self.load_preview(item, did, iid, meta); item.setIcon(2, QgsApplication.getThemeIcon("/mActionShowAllLayers.svg"))
            else: QgsProject.instance().removeMapLayers([l.id() for l in QgsProject.instance().mapLayers().values() if l.name() == f"Vue - {iid}"]); item.setIcon(2, QgsApplication.getThemeIcon("/mActionHideAllLayers.svg"))
            item.setData(2, Qt.UserRole + 1, not is_vis)

    def on_item_checked(self, item, column):
        if column == 0:
            lyr = self.setup_selection_layer()
            role = item.data(0, Qt.UserRole + 4)
            if role == "cliche":
                for f in lyr.getFeatures():
                    if f['id'] == item.text(0): 
                        lyr.startEditing(); f['visible'] = 1 if item.checkState(0) == Qt.Checked else 0
                        lyr.updateFeature(f); lyr.commitChanges()
            elif role == "mission":
                state = item.checkState(0)
                for i in range(item.childCount()): 
                    child = item.child(i)
                    if child.data(0, Qt.UserRole + 4) == "cliche": child.setCheckState(0, state)
            lyr.triggerRepaint()

    def run_scan_processing(self):
        try:
            xf = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem("EPSG:4326"), QgsProject.instance())
            res = processing.run("remonter_temps:scan_missions_pva", {'EXTENT': xf.transformBoundingBox(self.canvas.extent())})
            data = json.loads(res['RESULTS']); self.missions_cache = {}
            for f in data.get('features', []):
                p = f['properties']; yr = p['date_mission'][:4]; key = f"{p['date_mission']} [{'Oblique' if p.get('oblique') else 'Nadir'}]"
                if yr not in self.missions_cache: self.missions_cache[yr] = {}
                if key not in self.missions_cache[yr]: self.missions_cache[yr][key] = p
                if 'ids' not in self.missions_cache[yr][key]: self.missions_cache[yr][key]['ids'] = []
                self.missions_cache[yr][key]['ids'].append(p['dataset_identifier'])
            self.combo_annee.clear(); self.combo_annee.addItems(["--- Année ---"] + sorted(self.missions_cache.keys(), reverse=True))
        except: pass

    def update_missions_list(self):
        y = self.combo_annee.currentText(); self.combo_mission.clear(); self.combo_mission.addItem("--- Mission ---")
        if y in self.missions_cache:
            for k in sorted(self.missions_cache[y].keys(), reverse=True): self.combo_mission.addItem(k, self.missions_cache[y][k]['ids'])

    def on_mission_change(self):
        mids = self.combo_mission.currentData()
        if not mids: return
        QgsProject.instance().removeMapLayers([l.id() for l in QgsProject.instance().mapLayers().values() if "Aperçu -" in l.name()])
        v = QgsVectorTileLayer(f"type=xyz&url=https://data.geopf.fr/vector-tms/1.0.0/pva.image/{{z}}/{{x}}/{{y}}.pbf?filter=dataset_identifier%20IN%20({','.join([f'\'{m}\'' for m in mids])})", f"Aperçu - {self.combo_mission.currentText()}")
        if v.isValid(): QgsProject.instance().addMapLayer(v)

    def toggle_draw_tool(self, checked):
        if checked: self.active_tool = MvtSelectionTool(self.canvas, self.fetch_cliches); self.canvas.setMapTool(self.active_tool)
        else: self.canvas.unsetMapTool(self.active_tool)

    def fetch_cliches(self, rect):
        self.btn_sel.setChecked(False); self.canvas.unsetMapTool(self.active_tool)
        mids = self.combo_mission.currentData(); yr = self.combo_annee.currentText(); m_key = self.combo_mission.currentText()
        if not mids: return
        bbox = QgsCoordinateTransform(self.canvas.mapSettings().destinationCrs(), QgsCoordinateReferenceSystem("EPSG:3857"), QgsProject.instance()).transformBoundingBox(rect)
        found = []
        for mid in mids:
            params = {'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature', 'typeName': 'pva:image', 'outputFormat': 'application/json', 'srsName': 'EPSG:3857', 'cql_filter': f"dataset_identifier='{mid}' AND BBOX(geom, {bbox.xMinimum()},{bbox.yMinimum()},{bbox.xMaximum()},{bbox.yMaximum()},'EPSG:3857')"}
            try:
                r = requests.get("https://data.geopf.fr/wfs", params=params, timeout=10)
                if r.status_code == 200: found.extend(r.json().get('features', []))
            except: pass
        self.add_to_basket(found, yr, m_key)

    def setup_selection_layer(self):
        l = QgsProject.instance().mapLayersByName("Panier de photos")
        if l: return l[0]
        lyr = QgsVectorLayer("Polygon?crs=EPSG:3857&field=id:string(100)&field=visible:int", "Panier de photos", "memory")
        sym1 = QgsFillSymbol.createSimple({'color': '255,255,0,60', 'outline_color': '255,0,0,255'})
        sym0 = QgsFillSymbol.createSimple({'color': '0,0,0,0', 'outline_color': '0,0,0,0'})
        lyr.setRenderer(QgsCategorizedSymbolRenderer("visible", [QgsRendererCategory(1, sym1, "Visible"), QgsRendererCategory(0, sym0, "Masqué")]))
        QgsProject.instance().addMapLayer(lyr); return lyr

    def reset_all(self):
        self.tree_panier.clear()
        QgsProject.instance().removeMapLayers([l.id() for l in QgsProject.instance().mapLayers().values() if any(x in l.name() for x in ["Aperçu -", "Vue -", "Panier de photos"])])
        self.canvas.refresh()

    def start_async_download(self):
        folder = QFileDialog.getExistingDirectory(self, "Dossier d'export")
        if not folder: return
        self.current_folder = folder; self.dl_queue = []
        root = self.tree_panier.invisibleRootItem()
        for i in range(root.childCount()):
            ms = root.child(i)
            for k in range(ms.childCount()):
                c = ms.child(k)
                if c.data(0, Qt.UserRole + 4) == "cliche" and c.checkState(0) == Qt.Checked:
                    self.dl_queue.append({'iid': c.text(0), 'did': c.data(0, Qt.UserRole + 2), 'meta': c.data(0, Qt.UserRole + 1)})
        if self.dl_queue:
            self.progress_bar.setMaximum(len(self.dl_queue)); self.progress_bar.setValue(0); self.progress_bar.show()
            self.process_next_download()

    def process_next_download(self):
        if not self.dl_queue:
            self.progress_bar.hide(); self.iface.messageBar().pushMessage("Export", "Terminé", level=0)
            return
        item = self.dl_queue.pop(0)
        with open(os.path.join(self.current_folder, f"{item['iid']}_meta.json"), 'w') as f: json.dump(item['meta'], f)
        url = QUrl(f"https://data.geopf.fr/telechargement/download/pva/{item['did']}/{item['iid']}.tif")
        req = QNetworkRequest(url)
        self.reply = QgsNetworkAccessManager.instance().get(req)
        self.reply.finished.connect(lambda: self.on_download_finished(item['iid']))

    def on_download_finished(self, iid):
        if self.reply.error() == 0:
            with open(os.path.join(self.current_folder, f"{iid}.tif"), 'wb') as f: f.write(self.reply.readAll())
        self.progress_bar.setValue(self.progress_bar.value() + 1); self.process_next_download()

    def change_previsu_opacity(self, v):
        for lyr in QgsProject.instance().mapLayers().values():
            if lyr.name().startswith("Vue -"):
                lyr.setOpacity(v / 100.0); lyr.triggerRepaint()