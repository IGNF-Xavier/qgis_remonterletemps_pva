# -*- coding: utf-8 -*-
"""Remonter le Temps - PVA IGN : plugin QGIS."""


def classFactory(iface):
    # rend importables les paquets installes dans <extension>/libs
    from . import deps
    deps.ensure_path()
    from .plugin import RltPlugin
    return RltPlugin(iface)
