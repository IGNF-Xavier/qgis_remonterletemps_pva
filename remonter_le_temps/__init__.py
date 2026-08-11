# -*- coding: utf-8 -*-
"""Remonter le Temps - PVA IGN : plugin QGIS."""


def classFactory(iface):
    # QGIS demarre sans console sous Windows : sys.stderr peut valoir None, et
    # la moindre ecriture d'avertissement leverait alors une AttributeError qui
    # masquerait le message reel. On securise les flux avant toute chose.
    from . import deps
    deps.ensure_stdio()
    # rend importables les paquets installes dans <extension>/libs
    deps.ensure_path()
    from .plugin import RltPlugin
    return RltPlugin(iface)
