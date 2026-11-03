# -*- coding: utf-8 -*-
"""
export.py — grava camadas e tabelas em arquivo.

Tudo via QgsVectorFileWriter (OGR, embutido no QGIS). Isso cobre SHP,
GPKG e XLSX sem precisar de openpyxl, pandas ou geopandas.

Cuidado específico do Shapefile: o formato guarda nomes de campo no
DBF, limitados a 10 caracteres. Em vez de deixar o OGR truncar (o que
gera colisão silenciosa — "area_ha" e "area_hab" viram o mesmo campo),
renomeamos explicitamente antes de gravar.
"""

import logging
from pathlib import Path

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsFeature, QgsField, QgsFields,
    QgsProject, QgsVectorFileWriter, QgsVectorLayer, QgsWkbTypes,
)
from qgis.PyQt.QtCore import QMetaType

log = logging.getLogger("GeoCAR_Suite")

FORMATOS = {
    "shp":  ("ESRI Shapefile", ".shp"),
    "gpkg": ("GPKG", ".gpkg"),
    "xlsx": ("XLSX", ".xlsx"),
}

# Nomes longos → nome curto para o DBF do Shapefile.
ABREVIACOES = {
    "codigo_car":  "cod_car",
    "municipio":   "municipio",
    "area_ha":     "area_ha",
    "situacao":    "situacao",
    "responsavel": "respons",
    "fonte":       "fonte",
    "percentual":  "perc",
    "feicoes":     "n_feic",
    "camada":      "camada",
    "nomes":       "nomes",
}


def _abreviar(nome: str, usados: set) -> str:
    curto = ABREVIACOES.get(nome, nome)[:10]
    if curto not in usados:
        usados.add(curto)
        return curto
    # Desempate numerado, preservando o limite de 10.
    for i in range(1, 100):
        sufixo = str(i)
        tentativa = curto[:10 - len(sufixo)] + sufixo
        if tentativa not in usados:
            usados.add(tentativa)
            return tentativa
    usados.add(curto)
    return curto


def _opcoes(formato: str, nome_camada: str):
    driver, _ = FORMATOS[formato]
    op = QgsVectorFileWriter.SaveVectorOptions()
    op.driverName = driver
    op.fileEncoding = "UTF-8"
    if nome_camada:
        op.layerName = nome_camada
    return op


def exportar_camada(camada: QgsVectorLayer, caminho: str, formato: str = None,
                    nome_camada: str = None, crs_destino: str = None):
    """
    Grava a camada em arquivo. Retorna (True, caminho) ou (False, erro).
    Se `formato` vier None, deduz pela extensão do caminho.

    `crs_destino` ("EPSG:4674", por exemplo) reprojeta na gravação. O
    pacote do cadastro depende disso: o SICAR espera SIRGAS 2000
    geográficas, e mandar o arquivo em UTM é rejeição na importação.
    """
    caminho = str(caminho)
    if formato is None:
        ext = Path(caminho).suffix.lower().lstrip(".")
        formato = ext if ext in FORMATOS else "gpkg"
    if formato not in FORMATOS:
        return False, f"Formato não suportado: {formato}"

    _, ext_certa = FORMATOS[formato]
    if not caminho.lower().endswith(ext_certa):
        caminho += ext_certa

    fonte = camada
    if formato == "shp":
        fonte = _camada_com_nomes_curtos(camada)

    try:
        Path(caminho).parent.mkdir(parents=True, exist_ok=True)
        contexto = QgsProject.instance().transformContext()
        opcoes = _opcoes(formato, nome_camada or camada.name())
        if crs_destino:
            alvo = QgsCoordinateReferenceSystem(crs_destino)
            if alvo.isValid() and alvo.authid() != camada.crs().authid():
                from qgis.core import QgsCoordinateTransform
                opcoes.ct = QgsCoordinateTransform(camada.crs(), alvo, contexto)
        resultado = QgsVectorFileWriter.writeAsVectorFormatV3(
            fonte, caminho, contexto, opcoes,
        )
        # A API devolve (código, mensagem) nas versões recentes e um
        # código simples nas antigas — tratamos as duas formas.
        codigo = resultado[0] if isinstance(resultado, tuple) else resultado
        mensagem = resultado[1] if isinstance(resultado, tuple) and len(resultado) > 1 else ""
        if codigo != QgsVectorFileWriter.WriterError.NoError:
            return False, f"Erro ao gravar ({codigo}): {mensagem}"
    except Exception as e:
        return False, str(e)

    log.info("Exportado: %s", caminho)
    return True, caminho


def _camada_com_nomes_curtos(camada: QgsVectorLayer) -> QgsVectorLayer:
    """Cópia em memória com os campos já renomeados para caber no DBF."""
    # Uma camada sem geometria (tabela de sobreposições, resultado do
    # lote) tem wkbType NoGeometry, e "NoGeometry?crs=..." não cria
    # camada válida — o provedor de memória espera "None" nesse caso.
    if camada.geometryType() == Qgis.GeometryType.Null:
        uri = "None"
    else:
        uri = f"{QgsWkbTypes.displayString(camada.wkbType())}?crs={camada.crs().authid()}"
    copia = QgsVectorLayer(uri, camada.name(), "memory")
    prov = copia.dataProvider()

    usados = set()
    novos = []
    for campo in camada.fields():
        # Copiar o campo (em vez de remontar com campo.type()) preserva
        # tipo, comprimento e precisão — e campo.type() não existe mais
        # no Qt6.
        novo = QgsField(campo)
        novo.setName(_abreviar(campo.name(), usados))
        novos.append(novo)
    prov.addAttributes(novos)
    copia.updateFields()

    feicoes = []
    for f in camada.getFeatures():
        nova = QgsFeature(copia.fields())
        nova.setGeometry(f.geometry())
        nova.setAttributes(f.attributes())
        feicoes.append(nova)
    prov.addFeatures(feicoes)
    copia.updateExtents()
    return copia


def exportar_tabela(linhas: list, colunas: list, caminho: str,
                    formato: str = "xlsx", nome_camada: str = "resultado"):
    """
    Grava uma tabela sem geometria (sobreposições, resultado do lote).

    `linhas` é uma lista de dicts; `colunas` é a lista de chaves, na
    ordem em que devem sair.
    """
    if not linhas:
        return False, "Nada para exportar."

    campos = QgsFields()
    amostra = linhas[0]
    for col in colunas:
        valor = amostra.get(col)
        if isinstance(valor, bool):
            tipo = QMetaType.Type.QString
        elif isinstance(valor, int):
            tipo = QMetaType.Type.Int
        elif isinstance(valor, float):
            tipo = QMetaType.Type.Double
        else:
            tipo = QMetaType.Type.QString
        campos.append(QgsField(col, tipo))

    camada = QgsVectorLayer("None", nome_camada, "memory")
    prov = camada.dataProvider()
    prov.addAttributes(campos)
    camada.updateFields()

    feicoes = []
    for linha in linhas:
        f = QgsFeature(camada.fields())
        valores = []
        for col in colunas:
            v = linha.get(col)
            valores.append("; ".join(str(x) for x in v) if isinstance(v, (list, tuple))
                           else v)
        f.setAttributes(valores)
        feicoes.append(f)
    prov.addFeatures(feicoes)

    return exportar_camada(camada, caminho, formato, nome_camada)


def ler_codigos(caminho: str, coluna: str = None):
    """
    Lê códigos CAR de uma planilha (xlsx/csv) para o processamento em
    lote, via OGR — sem pandas nem openpyxl.

    Se `coluna` não for informada, procura a primeira cujo nome lembre
    código do CAR; se não achar, usa a primeira coluna.
    Retorna (True, [códigos]) ou (False, mensagem).
    """
    caminho = str(caminho)
    if not Path(caminho).exists():
        return False, f"Arquivo não encontrado: {caminho}"

    camada = QgsVectorLayer(caminho, "planilha", "ogr")
    if not camada.isValid():
        return False, ("Não consegui abrir a planilha. Formatos aceitos: "
                       ".xlsx, .csv, .ods.")

    nomes = [c.name() for c in camada.fields()]
    if not nomes:
        return False, "A planilha não tem colunas legíveis."

    if coluna and coluna in nomes:
        alvo = coluna
    else:
        alvo = None
        for n in nomes:
            baixo = n.lower().replace(" ", "").replace("_", "")
            if "car" in baixo or "codigo" in baixo or "código" in baixo:
                alvo = n
                break
        alvo = alvo or nomes[0]

    from . import carcode

    codigos, vistos = [], set()
    for f in camada.getFeatures():
        valor = f[alvo]
        if valor in (None, ""):
            continue
        cod = carcode.normalizar(str(valor))
        if cod and cod not in vistos:
            vistos.add(cod)
            codigos.append(cod)

    if not codigos:
        return False, f"Nenhum código encontrado na coluna '{alvo}'."
    return True, codigos
