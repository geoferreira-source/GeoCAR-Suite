# -*- coding: utf-8 -*-
"""
layers.py — converte o GeoJSON da consulta numa camada do QGIS.

Usa QgsJsonUtils, da própria API do QGIS, em vez de geopandas/fiona.
Isso evita gravar arquivo temporário e, principalmente, evita a
dependência externa que causava o conflito de proj.db no Python do
QGIS no Windows.

Toda camada criada aqui é em memória e declarada em EPSG:4326, que é
o CRS em que tanto o WFS do SICAR quanto o portal da SEMAS-PA
devolvem geometria.
"""

import logging

from qgis.core import (
    QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields,
    QgsFillSymbol, QgsJsonUtils, QgsProject, QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer, QgsSingleSymbolRenderer, QgsUnitTypes,
    QgsVectorLayer,
)
from qgis.PyQt.QtCore import Qt, QVariant
from qgis.PyQt.QtGui import QColor

log = logging.getLogger("GeoCAR_Suite")

CRS_FONTE = "EPSG:4326"

# Atributos que a camada do imóvel sempre carrega, na ordem.
CAMPOS = [
    ("codigo_car", QVariant.String),
    ("municipio", QVariant.String),
    ("area_ha", QVariant.Double),
    ("situacao", QVariant.String),
    ("condicao", QVariant.String),
    ("responsavel", QVariant.String),
    ("fonte", QVariant.String),
]


def _campos() -> QgsFields:
    campos = QgsFields()
    for nome, tipo in CAMPOS:
        campos.append(QgsField(nome, tipo))
    return campos


def _geometrias_do_geojson(geojson: dict):
    """
    Extrai as geometrias de um GeoJSON, seja ele Feature,
    FeatureCollection ou geometria pura — as três formas aparecem,
    dependendo da fonte.
    """
    import json

    if not geojson:
        return []

    if isinstance(geojson, dict) and geojson.get("type") not in (
        "Feature", "FeatureCollection"
    ):
        # Geometria pura: embrulha como Feature para o QgsJsonUtils.
        geojson = {"type": "Feature", "geometry": geojson, "properties": {}}

    texto = json.dumps(geojson)
    feicoes = QgsJsonUtils.stringToFeatureList(texto, QgsFields())
    return [f.geometry() for f in feicoes if f.hasGeometry()]


def criar_camada_imovel(dados: dict, nome: str = None) -> QgsVectorLayer:
    """
    Monta a camada do imóvel a partir do dicionário devolvido pelo
    roteador. Devolve None se não houver geometria utilizável.
    """
    geoms = _geometrias_do_geojson(dados.get("geojson"))
    if not geoms:
        log.warning("Consulta sem geometria para %s", dados.get("codigo_car"))
        return None

    codigo = dados.get("codigo_car") or "imóvel"
    nome = nome or f"CAR {codigo}"

    # O tipo real vem da geometria recebida (polígono ou multipolígono).
    tipo = "MultiPolygon" if geoms[0].isMultipart() else "Polygon"
    camada = QgsVectorLayer(f"{tipo}?crs={CRS_FONTE}", nome, "memory")
    if not camada.isValid():
        return None

    prov = camada.dataProvider()
    prov.addAttributes([QgsField(n, t) for n, t in CAMPOS])
    camada.updateFields()

    area = dados.get("area_ha")
    try:
        area = float(area) if area not in (None, "") else None
    except (TypeError, ValueError):
        area = None

    feicoes = []
    for geom in geoms:
        f = QgsFeature(camada.fields())
        f.setGeometry(geom)
        f.setAttributes([
            codigo,
            dados.get("municipio") or "",
            area,
            dados.get("situacao") or "",
            dados.get("condicao") or "",
            dados.get("responsavel") or "",
            dados.get("fonte") or "",
        ])
        feicoes.append(f)

    prov.addFeatures(feicoes)
    camada.updateExtents()
    return camada


# ── simbologia ───────────────────────────────────────────
# Decisões de cor, que não são gosto:
#
# • Preenchimento quase transparente (18%). O imóvel é o RECORTE de
#   análise, não o assunto: o que interessa é o que está embaixo dele
#   (desmatamento, APP, embargo). Preenchimento opaco esconderia
#   exatamente a informação que motivou a consulta.
# • Contorno em duas passadas — uma linha escura larga e uma âmbar
#   fina por cima. É o truque de "casing" usado em cartografia viária:
#   a linha clara se lê sobre floresta e sombra, a escura se lê sobre
#   solo exposto e imagem clara. Uma cor só sempre perde num dos dois.
# • Âmbar, e não verde. Verde sobre imagem de satélite da Amazônia
#   desaparece; âmbar/laranja não ocorre em cobertura natural.
COR_PREENCHIMENTO = QColor(255, 193, 7, 46)     # âmbar a ~18% de alfa
COR_CONTORNO_BASE = QColor(38, 26, 0, 215)      # marrom quase preto
COR_CONTORNO_TOPO = QColor(255, 196, 0, 255)    # âmbar saturado
LARGURA_BASE_MM = 1.10
LARGURA_TOPO_MM = 0.46


def _mm(camada_simbolo):
    try:
        camada_simbolo.setWidthUnit(QgsUnitTypes.RenderMillimeters)
    except Exception:
        pass
    return camada_simbolo


def _camadas_do_simbolo():
    """As três camadas do símbolo, sempre novas.

    Nunca reaproveite as mesmas instâncias entre tentativas: ao receber
    uma camada de símbolo, o QGIS assume a posse dela e a destrói junto
    com o símbolo — reutilizar depois é acesso a memória liberada.
    """
    fundo = QgsSimpleFillSymbolLayer(COR_PREENCHIMENTO)
    # O contorno vem das camadas de linha; o do próprio preenchimento
    # ficaria entre elas e sujaria a borda.
    fundo.setStrokeStyle(Qt.PenStyle.NoPen)

    base = _mm(QgsSimpleLineSymbolLayer(COR_CONTORNO_BASE))
    base.setWidth(LARGURA_BASE_MM)
    topo = _mm(QgsSimpleLineSymbolLayer(COR_CONTORNO_TOPO))
    topo.setWidth(LARGURA_TOPO_MM)
    for linha in (base, topo):
        try:
            linha.setPenJoinStyle(Qt.PenJoinStyle.RoundJoin)
            linha.setPenCapStyle(Qt.PenCapStyle.RoundCap)
        except Exception:
            pass
    return fundo, base, topo


def simbolo_imovel() -> QgsFillSymbol:
    """Símbolo de preenchimento do imóvel (fundo + contorno em duas
    passadas). Exposto para que exportação e legenda usem o mesmo."""
    try:
        return QgsFillSymbol(list(_camadas_do_simbolo()))
    except Exception:
        # Construtor com lista indisponível: monta na mão.
        fundo, base, topo = _camadas_do_simbolo()
        simbolo = QgsFillSymbol()
        simbolo.changeSymbolLayer(0, fundo)
        simbolo.appendSymbolLayer(base)
        simbolo.appendSymbolLayer(topo)
        return simbolo


def aplicar_simbologia(camada: QgsVectorLayer) -> bool:
    """
    Veste a camada do imóvel.

    Sem isto ela entra com o cinza aleatório que o QGIS sorteia e, num
    projeto com dez camadas, o usuário não acha o imóvel que acabou de
    consultar. Falha em silêncio: estilo não vale travar uma consulta.
    """
    if camada is None:
        return False
    try:
        camada.setRenderer(QgsSingleSymbolRenderer(simbolo_imovel()))
        camada.triggerRepaint()
        return True
    except Exception as e:
        log.warning("Não consegui aplicar a simbologia: %s", e)
        return False


def adicionar_ao_projeto(camada: QgsVectorLayer, iface=None,
                         dar_zoom: bool = True,
                         com_simbologia: bool = True) -> QgsVectorLayer:
    """Adiciona ao projeto e, opcionalmente, enquadra o mapa nela."""
    if com_simbologia:
        aplicar_simbologia(camada)
    QgsProject.instance().addMapLayer(camada)
    if dar_zoom and iface is not None:
        try:
            canvas = iface.mapCanvas()
            canvas.setExtent(
                canvas.mapSettings().layerExtentToOutputExtent(
                    camada, camada.extent()
                )
            )
            canvas.refresh()
        except Exception as e:
            log.warning("Não consegui dar zoom na camada: %s", e)
    return camada


def camadas_poligonais_do_projeto(ignorar=()):
    """
    Camadas vetoriais do projeto que podem servir de base de
    sobreposição: qualquer vetor com geometria, exceto as ignoradas.

    É isto que substitui a configuração manual de caminho por base: o
    plugin cruza com o que já está aberto no QGIS.
    """
    from qgis.core import QgsWkbTypes

    ignorar = {str(n) for n in ignorar}
    resultado = []
    for camada in QgsProject.instance().mapLayers().values():
        if not isinstance(camada, QgsVectorLayer) or not camada.isValid():
            continue
        if camada.name() in ignorar or camada.id() in ignorar:
            continue
        if camada.geometryType() == QgsWkbTypes.NullGeometry:
            continue
        resultado.append(camada)
    return resultado
