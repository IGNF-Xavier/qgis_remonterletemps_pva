import requests, os, json
from qgis.core import (QgsProcessing, QgsProcessingAlgorithm, 
                       QgsProcessingParameterString, QgsProcessingParameterFileDestination,
                       QgsProcessingParameterExtent)

class ScanMissionsAlgo(QgsProcessingAlgorithm):
    EXTENT = 'EXTENT'
    RESULTS = 'RESULTS'

    def name(self): return 'scan_missions_pva'
    def displayName(self): return 'Scanner Missions PVA (WFS)'
    def group(self): return 'PVA'
    def groupId(self): return 'pva'
    
    def createInstance(self): return ScanMissionsAlgo()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterExtent(self.EXTENT, 'Zone de recherche'))

    def processAlgorithm(self, parameters, context, feedback):
        extent = self.parameterAsExtent(parameters, self.EXTENT, context)
        url = "https://data.geopf.fr/wfs"
        params = {
            'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
            'typeName': 'pva:dataset', 'outputFormat': 'application/json',
            'propertyName': 'date_mission,oblique,couleur,resolution,support,dataset_identifier',
            'bbox': f"{extent.yMinimum()},{extent.xMinimum()},{extent.yMaximum()},{extent.xMaximum()},urn:ogc:def:crs:EPSG::4326"
        }
        res = requests.get(url, params=params, timeout=15).json()
        return {self.RESULTS: json.dumps(res)}

class DownloadPVAAlgo(QgsProcessingAlgorithm):
    URLS = 'URLS'
    METADATA = 'METADATA'
    FOLDER = 'FOLDER'

    def name(self): return 'download_pva_algo'
    def displayName(self): return 'Téléchargement PVA et Métadonnées'
    def group(self): return 'PVA'
    def groupId(self): return 'pva'
    
    def createInstance(self): return DownloadPVAAlgo()
    
    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterString(self.URLS, 'URLs des TIF (séparées par ;)'))
        self.addParameter(QgsProcessingParameterString(self.METADATA, 'Métadonnées JSON', optional=True))
        self.addParameter(QgsProcessingParameterFileDestination(self.FOLDER, 'Dossier de destination', fileFilter='Dossier'))

    def processAlgorithm(self, parameters, context, feedback):
        urls = self.parameterAsString(parameters, self.URLS, context).split(';')
        folder = self.parameterAsString(parameters, self.FOLDER, context)
        meta_json = self.parameterAsString(parameters, self.METADATA, context)
        metadata_dict = json.loads(meta_json) if meta_json else {}

        if not os.path.exists(folder): os.makedirs(folder)

        total = len(urls)
        for i, url in enumerate(urls):
            if feedback.isCanceled(): break
            filename = url.split('/')[-1]
            dest = os.path.join(folder, filename)
            try:
                r = requests.get(url, stream=True, timeout=30)
                if r.status_code == 200:
                    with open(dest, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
                    
                    base_name = os.path.splitext(filename)[0]
                    if base_name in metadata_dict:
                        meta_path = os.path.join(folder, f"{base_name}_metadata.txt")
                        with open(meta_path, 'w', encoding='utf-8') as f_meta:
                            f_meta.write(json.dumps(metadata_dict[base_name], indent=4, ensure_ascii=False))
            except Exception as e:
                feedback.reportError(f"Erreur sur {filename}: {str(e)}")
            feedback.setProgress(int((i + 1) / total * 100))
        return {self.FOLDER: folder}