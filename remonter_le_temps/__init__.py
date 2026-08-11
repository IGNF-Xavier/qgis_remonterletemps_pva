# -*- coding: utf-8 -*-
"""Remonter le Temps - PVA IGN : plugin QGIS."""


def classFactory(iface):
    # QGIS demarre sans console sous Windows : sys.stderr peut valoir None, et
    # la moindre ecriture d'avertissement leverait alors une AttributeError qui
    # masquerait le message reel. On securise les flux avant tout autre import.
    from . import deps
    deps.ensure_stdio()
    # rend importables les paquets installes dans <extension>/libs
    deps.ensure_path()

    # numpy et GDAL ne sont importes qu'ici : si l'environnement Python est
    # incoherent, on charge une extension de repli qui explique le probleme
    # au lieu de laisser remonter une trace illisible.
    try:
        from .plugin import RltPlugin
        return RltPlugin(iface)
    except Exception:  # noqa: BLE001
        import traceback
        from .plugin_broken import BrokenPlugin
        return BrokenPlugin(iface, traceback.format_exc())
