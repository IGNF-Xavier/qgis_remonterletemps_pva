import os, json, requests
from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterString, 
                       QgsProcessingParameterFolderDestination, QgsCoordinateReferenceSystem, 
                       QgsCoordinateTransform, QgsProject, QgsPointXY)

class DownloadPvaCalage(QgsProcessingAlgorithm):
    URLS = 'URLS'
    METADATA = 'METADATA'
    FOLDER = 'FOLDER'

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(self.URLS, 'URLs'))
        self.addParameter(QgsProcessingParameterString(self.METADATA, 'Meta JSON'))
        self.addParameter(QgsProcessingParameterFolderDestination(self.FOLDER, 'Dossier'))

    def processAlgorithm(self, parameters, context, feedback):
        urls = self.parameterAsString(parameters, self.URLS, context).split(';')
        all_meta = json.loads(self.parameterAsString(parameters, self.METADATA, context))
        dest = self.parameterAsString(parameters, self.FOLDER, context)
        
        xf = QgsCoordinateTransform(QgsCoordinateReferenceSystem("EPSG:2154"), QgsCoordinateReferenceSystem("EPSG:3857"), QgsProject.instance())

        for i, url in enumerate(urls):
            if feedback.isCanceled(): break
            iid = url.split('/')[-1].split('.')[0]
            m = all_meta.get(iid, {})
            feedback.setProgressText(f"Cliché n°{m.get('numero_image', '???')}...")

            # Téléchargement
            r = requests.get(url, timeout=30)
            if r.status_code == 200:
                p_img = os.path.join(dest, f"{iid}.tif")
                with open(p_img, 'wb') as f: f.write(r.content)
                
                # Calage VRT (Basé sur le centre X,Y)
                res = float(m.get('resolution', 20000)) * 0.000021
                tx, ty = float(m['x']) - (10000*res/2), float(m['y']) + (10000*res/2)
                pt = xf.transform(QgsPointXY(tx, ty))
                with open(os.path.join(dest, f"{iid}.vrt"), 'w') as f_v:
                    f_v.write(f'<VRTDataset rasterXSize="10000" rasterYSize="10000"><SRS>EPSG:3857</SRS><GeoTransform>{pt.x()}, {res}, 0, {pt.y()}, 0, {-res}</GeoTransform><VRTRasterBand dataType="Byte" band="1"><SimpleSource><SourceFilename>{p_img}</SourceFilename></SimpleSource></VRTRasterBand><VRTRasterBand dataType="Byte" band="2"><SimpleSource><SourceFilename>{p_img}</SourceFilename></SimpleSource></VRTRasterBand><VRTRasterBand dataType="Byte" band="3"><SimpleSource><SourceFilename>{p_img}</SourceFilename></SimpleSource></VRTRasterBand></VRTDataset>')

                # Ecriture JSON complet (Cliché + Mission fusionnée)
                with open(os.path.join(dest, f"{iid}_metadata.json"), 'w', encoding='utf-8') as f_m:
                    json.dump(m, f_m, indent=4, ensure_ascii=False)
            
            feedback.setProgress(int((i+1)/len(urls)*100))
        return {self.FOLDER: dest}

    def name(self): return 'download_pva_calage'
    def displayName(self): return 'IGN PVA : Téléchargement et Calage'
    def group(self): return 'IGN'
    def groupId(self): return 'ign'
    def createInstance(self): return DownloadPvaCalage()