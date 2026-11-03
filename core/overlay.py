# -*- coding: utf-8 -*-
"""
overlay.py — sobreposição do imóvel com as camadas abertas no projeto.

Duas decisões que mudam em relação à v1:

1. As bases são as CAMADAS DO PROJETO, não caminhos de arquivo
   configurados um a um. Você monta o projeto como já monta e o plugin
   cruza com o que estiver ali.

2. A área é medida com QgsDistanceArea em modo elipsoidal, não
   reprojetando para UTM. Isso elimina de vez o cálculo manual de zona
   (que produzia EPSG inexistente quando a geometria chegava num CRS
   projetado) e dá resultado correto para imóveis que cruzam zonas.
   Como consequência, a área em hectares é geodésica — pode divergir
   em frações de porcento de uma medida feita numa projeção específica,
   o que é esperado e normalmente mais exato.

Usa só a API do QGIS: nada de geopandas, shapely ou fiona.
"""

import logging

from qgis.core import (
    Qgis, QgsCoordinateTransform, QgsDistanceArea, QgsFeatureRequest, QgsProject,
)


log = logging.getLogger("GeoCAR_Suite")

# Campos preferidos para nomear a feição sobreposta, em ordem. Como as
# bases vêm de fontes diferentes (ICMBio, FUNAI, INCRA...), não há um
# padrão — procuramos o primeiro que existir.
CAMPOS_NOME = [
    "nome", "name", "nome_uc", "nm_uc", "nomobjetouc", "denominacao",
    "terrai_nom", "nome_proje", "nm_projeto", "nm_comunid", "comunidade",
    "num_auto", "nr_auto", "nome_imovel", "municipio", "nm_mun", "classe",
    "ano", "descricao", "titulo", "label",
]


def medidor_para(crs) -> QgsDistanceArea:
    """
    Medidor elipsoidal para geometrias NO CRS INFORMADO.

    O CRS tem de ser o da geometria que será medida, não o do projeto.
    Usar o do projeto é uma armadilha real: ao receber a primeira
    camada, o QGIS adota o CRS dela como CRS do projeto — então a
    mesma medição dava resultado diferente conforme fosse feita antes
    ou depois de a camada entrar no mapa, e dava ZERO quando o projeto
    estava em Web Mercator e a geometria em graus. Pior: como área do
    imóvel e área sobreposta erravam pelo mesmo fator, o PERCENTUAL
    saía correto e a falha passava despercebida.
    """
    med = QgsDistanceArea()
    proj = QgsProject.instance()
    elipsoide = proj.ellipsoid() or "WGS84"
    if elipsoide in ("", "NONE"):
        elipsoide = "WGS84"
    med.setSourceCrs(crs, proj.transformContext())
    med.setEllipsoid(elipsoide)
    return med


def _area_ha(medidor: QgsDistanceArea, geom) -> float:
    """Área da geometria em hectares, convertida pela própria API."""
    bruta = medidor.measureArea(geom)
    m2 = medidor.convertAreaMeasurement(bruta, Qgis.AreaUnit.SquareMeters)
    return m2 / 10_000.0


def _nome_da_feicao(feicao, campos_nomes) -> str:
    for preferido in CAMPOS_NOME:
        for real in campos_nomes:
            if real.lower() == preferido:
                valor = feicao[real]
                if valor not in (None, ""):
                    return str(valor).strip()
    return ""


def calcular(camada_imovel, camadas_base=None, ignorar=(), limite_nomes=5,
             progresso=None):
    """
    Cruza o imóvel com as camadas informadas (ou com todas as camadas
    vetoriais do projeto, fora as ignoradas).

    Retorna lista de dicts, ordenada por área decrescente:
        {"camada", "area_ha", "percentual", "feicoes", "nomes"}

    `progresso(pct, texto)` é opcional — usado pela QgsTask para
    alimentar a barra sem travar a interface.
    """
    from .layers import camadas_poligonais_do_projeto

    ignorar = set(ignorar) | {camada_imovel.id(), camada_imovel.name()}
    if camadas_base is None:
        camadas_base = camadas_poligonais_do_projeto(ignorar=ignorar)
    else:
        camadas_base = [c for c in camadas_base
                        if c.id() not in ignorar and c.name() not in ignorar]

    if not camadas_base:
        return []

    ctx = QgsProject.instance().transformContext()
    crs_imovel = camada_imovel.crs()
    medidor = medidor_para(crs_imovel)

    # Geometria do imóvel, unificada (pode vir em várias feições).
    geoms = [f.geometry() for f in camada_imovel.getFeatures() if f.hasGeometry()]
    if not geoms:
        return []
    geom_imovel = geoms[0]
    for g in geoms[1:]:
        geom_imovel = geom_imovel.combine(g)
    if not geom_imovel.isGeosValid():
        geom_imovel = geom_imovel.makeValid()

    area_imovel = _area_ha(medidor, geom_imovel)

    resultados = []
    total = len(camadas_base)

    for i, base in enumerate(camadas_base):
        if progresso:
            progresso(int(i / total * 100), f"Cruzando com {base.name()}…")

        try:
            # Leva o imóvel para o CRS da base — é mais barato que
            # reprojetar a base inteira, e permite usar o índice
            # espacial dela pelo retângulo envolvente.
            geom_consulta = _reprojetar(geom_imovel, crs_imovel,
                                                    base.crs(), ctx)
            if geom_consulta is None:
                continue

            req = QgsFeatureRequest().setFilterRect(geom_consulta.boundingBox())
            campos_nomes = [c.name() for c in base.fields()]

            area_total = 0.0
            n_feicoes = 0
            nomes = []

            for feicao in base.getFeatures(req):
                if not feicao.hasGeometry():
                    continue
                g = feicao.geometry()
                if not g.intersects(geom_consulta):
                    continue
                inter = g.intersection(geom_consulta)
                if inter.isEmpty():
                    continue
                # Descarta interseções degeneradas (linha/ponto no
                # encosto de dois polígonos que só se tocam).
                if inter.type() != Qgis.GeometryType.Polygon:
                    continue

                # Mede no CRS do imóvel, para todas as bases caírem na
                # mesma referência de medida.
                inter_medida = _reprojetar(inter, base.crs(),
                                                       crs_imovel, ctx)
                if inter_medida is None:
                    continue

                area = _area_ha(medidor, inter_medida)
                if area <= 0:
                    continue

                area_total += area
                n_feicoes += 1
                nome = _nome_da_feicao(feicao, campos_nomes)
                if nome and nome not in nomes:
                    nomes.append(nome)

            if n_feicoes == 0 or area_total <= 0:
                continue

            resultados.append({
                "camada": base.name(),
                "area_ha": round(area_total, 4),
                "percentual": round(area_total / area_imovel * 100, 2)
                if area_imovel else 0.0,
                "feicoes": n_feicoes,
                "nomes": nomes[:limite_nomes],
            })

        except Exception as e:
            log.warning("Falha ao cruzar com '%s': %s", base.name(), e)
            continue

    resultados.sort(key=lambda r: r["area_ha"], reverse=True)
    if progresso:
        progresso(100, "Sobreposição concluída.")
    return resultados


def _reprojetar(geom, crs_origem, crs_destino, ctx):
    """
    Cópia da geometria no CRS de destino, ou None se a transformação
    falhar. É uma CÓPIA de propósito: QgsGeometry.transform() altera a
    geometria no lugar, então reaproveitar a do imóvel a cada base iria
    acumulando transformações e produzindo área errada em silêncio.
    """
    if crs_origem == crs_destino:
        return geom
    copia = type(geom)(geom)
    tr = QgsCoordinateTransform(crs_origem, crs_destino, ctx)
    if copia.transform(tr) != 0:
        return None
    return copia


def area_do_imovel_ha(camada_imovel) -> float:
    """Área total do imóvel em hectares, medida da mesma forma que as
    sobreposições — para que os percentuais fechem."""
    medidor = medidor_para(camada_imovel.crs())
    geoms = [f.geometry() for f in camada_imovel.getFeatures() if f.hasGeometry()]
    if not geoms:
        return 0.0
    geom = geoms[0]
    for g in geoms[1:]:
        geom = geom.combine(g)
    return round(_area_ha(medidor, geom), 4)
