from qgis.core import QgsProcessingProvider
from qgis.PyQt.QtGui import QIcon
from .algorithms.pva_processing import ScanMissionsAlgo, DownloadPVAAlgo

class PVAProvider(QgsProcessingProvider):
    def __init__(self):
        super().__init__()

    def loadAlgorithms(self):
        self.addAlgorithm(ScanMissionsAlgo())
        self.addAlgorithm(DownloadPVAAlgo())

    def id(self): return 'remonter_temps'
    def name(self): return 'Remonter le Temps PVA'
    def icon(self): return QIcon()