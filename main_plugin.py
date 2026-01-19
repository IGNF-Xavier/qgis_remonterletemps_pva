import os
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.core import QgsApplication
from .main_dialog import RemonterLeTempsDockWidget
from .processing_provider import PVAProvider

class RemonterLeTempsPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.provider = None
        self.dock = None
        self.action = None

    def initGui(self):
        self.provider = PVAProvider()
        QgsApplication.processingRegistry().addProvider(self.provider)

        icon_path = os.path.join(os.path.dirname(__file__), 'icone.png')
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QgsApplication.getThemeIcon("/mActionAllEdits.svg")

        self.action = QAction(icon, "Remonter le Temps PVA", self.iface.mainWindow())
        self.action.setObjectName("RemonterLeTempsAction")
        self.action.triggered.connect(self.run)

        self.iface.addPluginToMenu("&GPF Tools", self.action)
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.provider:
            QgsApplication.processingRegistry().removeProvider(self.provider)
        if self.action:
            self.iface.removePluginMenu("&GPF Tools", self.action)
            self.iface.removeToolBarIcon(self.action)
        if self.dock:
            self.iface.removeDockWidget(self.dock)

    def run(self):
        if self.dock is None:
            self.dock = RemonterLeTempsDockWidget(self.iface)
        
        # Positionnement à GAUCHE
        self.iface.addDockWidget(Qt.LeftDockWidgetArea, self.dock)
        self.dock.show()