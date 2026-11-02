# -*- coding: utf-8 -*-
"""GeoCAR Suite — ponto de entrada do plugin QGIS."""


def classFactory(iface):  # noqa: N802 (nome exigido pelo QGIS)
    from .plugin import GeoCARSuite
    return GeoCARSuite(iface)
