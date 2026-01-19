def classFactory(iface):
    from .main_plugin import RemonterLeTempsPlugin
    return RemonterLeTempsPlugin(iface)