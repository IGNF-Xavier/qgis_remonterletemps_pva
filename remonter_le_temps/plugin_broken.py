# -*- coding: utf-8 -*-
"""
Extension de repli, chargee quand l'import du plugin echoue.

Elle n'importe ni numpy ni GDAL : son seul role est d'afficher un diagnostic
lisible plutot que de laisser QGIS montrer une trace tronquee. Cela evite le
cas classique ou la vraie cause (conflit de versions de numpy) est masquee par
l'erreur secondaire survenue en tentant de l'ecrire sur un flux absent.
"""

import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction, QMessageBox

from qgis.core import Qgis

PLUGIN_DIR = os.path.dirname(__file__)
MENU = u"&Remonter le Temps (PVA IGN)"


class BrokenPlugin(object):
    """Plugin degrade : n'expose qu'un rapport d'erreur."""

    def __init__(self, iface, trace):
        self.iface = iface
        self.trace = trace
        self.action = None

    def initGui(self):
        icon = QIcon(os.path.join(PLUGIN_DIR, "icons", "rlt.svg"))
        self.action = QAction(icon, u"Remonter le Temps - diagnostic",
                              self.iface.mainWindow())
        self.action.triggered.connect(self.show_report)
        self.iface.addToolBarIcon(self.action)
        self.iface.addPluginToRasterMenu(MENU, self.action)
        self.iface.messageBar().pushMessage(
            u"Remonter le temps",
            u"L'extension n'a pas pu se charger : cliquez sur son icone pour "
            u"le diagnostic.",
            level=Qgis.Critical, duration=12)

    def unload(self):
        if self.action is not None:
            self.iface.removeToolBarIcon(self.action)
            self.iface.removePluginRasterMenu(MENU, self.action)
            self.action = None

    def report(self):
        try:
            from . import deps
            diag = deps.diagnose()
        except Exception as exc:  # noqa: BLE001
            diag = u"Diagnostic indisponible : %s" % exc
        return (
            u"L'extension n'a pas pu demarrer.\n\n"
            u"Dans la quasi-totalite des cas, la cause est un conflit entre "
            u"deux installations de numpy : QGIS embarque la sienne, et une "
            u"autre presente dans le site utilisateur passe avant sur "
            u"sys.path. Les modules compiles contre l'une (GDAL, scipy) "
            u"refusent alors de s'executer avec l'autre.\n\n"
            u"Correction, depuis l'OSGeo4W Shell :\n"
            u"    python -m pip uninstall numpy\n"
            u"    python -c \"import numpy; print(numpy.__file__)\"\n"
            u"Le chemin affiche doit se trouver dans l'installation QGIS.\n\n"
            u"%s\n\n--- Trace d'origine ---\n%s" % (diag, self.trace))

    def show_report(self):
        box = QMessageBox(self.iface.mainWindow())
        box.setIcon(QMessageBox.Warning)
        box.setWindowTitle(u"Remonter le Temps - diagnostic")
        box.setText(u"L'extension n'a pas pu se charger.")
        box.setDetailedText(self.report())
        box.exec_() if hasattr(box, "exec_") else box.exec()
