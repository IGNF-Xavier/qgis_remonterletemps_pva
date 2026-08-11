# -*- coding: utf-8 -*-
"""Classe principale du plugin."""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

from .compat import RIGHT_DOCK
from .dock import RltDock

PLUGIN_DIR = os.path.dirname(__file__)
MENU = u"&Remonter le Temps (PVA IGN)"


class RltPlugin(object):

    def __init__(self, iface):
        self.iface = iface
        self.dock = None
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(PLUGIN_DIR, "icons", "rlt.svg"))
        self.action = QAction(icon, u"Remonter le Temps - PVA IGN",
                              self.iface.mainWindow())
        self.action.setCheckable(True)
        self.action.triggered.connect(self.toggle)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU, self.action)

    def unload(self):
        if self.dock is not None:
            self.iface.removeDockWidget(self.dock)
            self.dock.deleteLater()
            self.dock = None
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginRasterMenu(MENU, self.action)
            self.action = None

    def toggle(self, checked):
        if self.dock is None:
            self.dock = RltDock(self.iface, self.iface.mainWindow())
            self.iface.addDockWidget(RIGHT_DOCK, self.dock)
            self.dock.visibilityChanged.connect(self._sync)
        self.dock.setVisible(checked)

    def _sync(self, visible):
        if self.action is not None:
            self.action.setChecked(visible)
