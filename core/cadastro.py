# -*- coding: utf-8 -*-
"""
cadastro.py — apoio à montagem de um CAR novo.

O que este módulo NÃO faz, e não tem como fazer: efetivar o cadastro. O
SICAR não tem API de escrita; o cadastro só é aceito pelo módulo oficial.
O que dá para fazer — e é onde o trabalho realmente está — é tudo que vem
antes: medir, validar, cruzar com a vizinhança e entregar os arquivos
prontos para importar.

Quatro frentes:

1. VALIDAÇÃO    geometria válida, contenção dos temas no perímetro,
                Reserva Legal acima do mínimo do regime, CRS, vértices.
2. VIZINHANÇA   módulo fiscal do município e sobreposição com CARs já
                cadastrados em volta (ver sources/wfs.listar_por_bbox).
3. PACOTE       um shapefile por tema, todos em SIRGAS 2000 geográficas
                (EPSG:4674), que é o sistema do cadastro.
4. MEMORIAL     vértices, azimutes e distâncias em UTM SIRGAS 2000.

Tudo com a API do QGIS — nada de geopandas ou shapely.
"""

import logging
import math
from statistics import median

from qgis.core import (
    Qgis, QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsGeometry,
    QgsProject, QgsVectorLayer,
)

from . import overlay, texto

log = logging.getLogger("GeoCAR_Suite")

# CRS do cadastro: SIRGAS 2000 em coordenadas geográficas. É o sistema
# oficial do SICAR; qualquer outra coisa é reprojetada na exportação.
CRS_CADASTRO = "EPSG:4674"

# Temas do cadastro: (chave, rótulo, obrigatório).
# A chave vira o nome do arquivo no pacote.
TEMAS = [
    ("area_imovel",      "Área do imóvel (perímetro)",          True),
    ("reserva_legal",    "Reserva Legal",                       False),
    ("app",              "Área de Preservação Permanente",      False),
    ("vegetacao_nativa", "Remanescente de vegetação nativa",    False),
    ("area_consolidada", "Área consolidada (uso antrópico)",    False),
    ("area_pousio",      "Área de pousio",                      False),
    ("uso_restrito",     "Área de uso restrito",                False),
    ("servidao",         "Servidão administrativa",             False),
    ("hidrografia",      "Hidrografia",                         False),
    ("nascente",         "Nascentes e olhos d'água",            False),
]
ROTULOS = {chave: rotulo for chave, rotulo, _ in TEMAS}
CHAVE_PERIMETRO = "area_imovel"

# Percentual mínimo de Reserva Legal (Lei 12.651/2012, art. 12).
REGIMES_RL = [
    ("amazonia_floresta", "Amazônia Legal — área de floresta",  80),
    ("amazonia_cerrado",  "Amazônia Legal — área de cerrado",   35),
    ("amazonia_campos",   "Amazônia Legal — campos gerais",     20),
    ("demais",            "Demais regiões do país",             20),
]
PERCENTUAL_RL = {chave: pct for chave, _, pct in REGIMES_RL}

UFS_AMAZONIA_LEGAL = {"AC", "AP", "AM", "MA", "MT", "PA", "RO", "RR", "TO"}

# Acima disto o módulo de cadastro fica lento e às vezes recusa o
# arquivo. Não é erro — é aviso para simplificar antes de enviar.
VERTICES_DEMAIS = 2000

# Área fora do perímetro abaixo da qual não vale acusar: é ruído de
# digitalização, não extravasamento real.
TOLERANCIA_HA = 0.01


def regime_sugerido(uf: str) -> str:
    """
    Palpite inicial pelo estado. O Maranhão fica parcialmente dentro da
    Amazônia Legal, e dentro dela há floresta, cerrado e campos — por
    isso é sugestão, e a decisão final continua sendo do usuário.
    """
    return ("amazonia_floresta"
            if (uf or "").strip().upper() in UFS_AMAZONIA_LEGAL
            else "demais")


# ── CRS ──────────────────────────────────────────────────
def crs_utm_sirgas(lon: float, lat: float):
    """
    UTM SIRGAS 2000 da zona onde está o ponto, para medir distância e
    azimute do memorial.

    Não confie na aritmética de código EPSG: os blocos norte e sul do
    SIRGAS 2000 se encavalam (EPSG:31976 é a zona 22N, não a 16S), e um
    código "quase certo" produz coordenada plausível e errada — foi
    exatamente assim que a v1 gerou zona inexistente. Por isso aqui o
    candidato só é aceito depois de conferido pela descrição; se não
    bater, monta-se o CRS por PROJ, que é exato (SIRGAS 2000 usa o
    elipsoide GRS80, geocêntrico).
    """
    zona = int((lon + 180) / 6) + 1
    zona = max(1, min(60, zona))
    sul = lat < 0
    esperado = f"zone {zona}{'S' if sul else 'N'}"

    candidato = (31960 + zona) if sul else (31954 + zona)
    crs = QgsCoordinateReferenceSystem(f"EPSG:{candidato}")
    if crs.isValid() and esperado.lower() in (crs.description() or "").lower():
        return crs, crs.authid()

    proj = (f"+proj=utm +zone={zona} {'+south ' if sul else ''}"
            f"+ellps=GRS80 +towgs84=0,0,0,0,0,0,0 +units=m +no_defs")
    crs = QgsCoordinateReferenceSystem.fromProj(proj)
    return crs, f"UTM {zona}{'S' if sul else 'N'} / SIRGAS 2000"


def _transformar(geom: QgsGeometry, origem, destino) -> QgsGeometry:
    """Cópia reprojetada. A cópia importa: transform() altera no lugar,
    e devolver a original transformada corrompe a camada de origem."""
    if origem.authid() == destino.authid():
        return QgsGeometry(geom)
    copia = QgsGeometry(geom)
    copia.transform(QgsCoordinateTransform(
        origem, destino, QgsProject.instance().transformContext()))
    return copia


def _dissolver(camada: QgsVectorLayer, crs_destino=None):
    """
    Une todas as feições do tema numa geometria só.

    Dissolver antes de medir não é detalhe: dois polígonos de APP que se
    sobrepõem somariam a área duas vezes, e a validação aprovaria uma
    Reserva Legal que na verdade não alcança o mínimo.
    """
    if camada is None:
        return None
    geoms = []
    destino = crs_destino or camada.crs()
    for f in camada.getFeatures():
        g = f.geometry()
        if g is None or g.isEmpty():
            continue
        geoms.append(_transformar(g, camada.crs(), destino))
    if not geoms:
        return None
    if len(geoms) == 1:
        return geoms[0]
    try:
        return QgsGeometry.unaryUnion(geoms)
    except Exception:
        uniao = geoms[0]
        for g in geoms[1:]:
            uniao = uniao.combine(g)
        return uniao


def _area_ha(geom: QgsGeometry, crs) -> float:
    if geom is None or geom.isEmpty():
        return 0.0
    med = overlay.medidor_para(crs)
    bruta = med.measureArea(geom)
    return med.convertAreaMeasurement(bruta, Qgis.AreaUnit.SquareMeters) / 10_000.0


def _contar_vertices(geom: QgsGeometry) -> int:
    try:
        return sum(1 for _ in geom.vertices())
    except Exception:
        return 0


def _problemas_de_geometria(geom: QgsGeometry):
    """Lista textual dos defeitos encontrados pelo validador do GEOS."""
    problemas = []
    try:
        for erro in geom.validateGeometry():
            texto = erro.what() if hasattr(erro, "what") else str(erro)
            if texto and texto not in problemas:
                problemas.append(texto)
    except Exception:
        if not geom.isGeosValid():
            problemas.append("geometria inválida segundo o GEOS")
    return problemas


# ── validação ────────────────────────────────────────────
def _achado(nivel, tema, mensagem):
    return {"nivel": nivel, "tema": tema, "mensagem": mensagem}


def validar(mapeamento: dict, regime: str = "demais",
            modulo_fiscal_ha: float = None):
    """
    Confere o conjunto antes de o cadastro ser enviado.

    `mapeamento` é {chave_do_tema: QgsVectorLayer}. Devolve
    (resumo, achados):
      resumo  — {"area_ha", "modulos_fiscais", "areas": {tema: ha},
                 "percentual_rl", "minimo_rl"}
      achados — lista de {"nivel": erro|aviso|ok, "tema", "mensagem"}
    """
    achados = []
    resumo = {"area_ha": 0.0, "modulos_fiscais": None, "areas": {},
              "percentual_rl": None, "minimo_rl": PERCENTUAL_RL.get(regime, 20)}

    crs = QgsCoordinateReferenceSystem(CRS_CADASTRO)
    perimetro_camada = mapeamento.get(CHAVE_PERIMETRO)
    if perimetro_camada is None:
        achados.append(_achado(
            "erro", ROTULOS[CHAVE_PERIMETRO],
            "Sem perímetro não há o que validar — escolha a camada do "
            "limite do imóvel."))
        return resumo, achados

    # ── o perímetro ──
    rotulo = ROTULOS[CHAVE_PERIMETRO]
    n = perimetro_camada.featureCount()
    if n == 0:
        achados.append(_achado("erro", rotulo, "A camada do perímetro está vazia."))
        return resumo, achados
    if n > 1:
        achados.append(_achado(
            "erro", rotulo,
            f"A camada tem {n} feições. O cadastro é de UM imóvel: deixe "
            f"só o polígono dele, ou dissolva as partes antes."))

    if perimetro_camada.geometryType() != Qgis.GeometryType.Polygon:
        achados.append(_achado(
            "erro", rotulo,
            "O perímetro precisa ser polígono — a camada escolhida é de "
            + ("linha." if perimetro_camada.geometryType() ==
               Qgis.GeometryType.Line else "ponto.")))
        return resumo, achados

    perimetro = _dissolver(perimetro_camada, crs)
    if perimetro is None:
        achados.append(_achado("erro", rotulo, "Não há geometria utilizável na camada."))
        return resumo, achados

    for p in _problemas_de_geometria(perimetro):
        achados.append(_achado("erro", rotulo, f"Geometria inválida: {p}."))

    autoridade = perimetro_camada.crs()
    if not autoridade.isValid():
        achados.append(_achado(
            "erro", rotulo, "A camada está sem CRS definido — defina antes de exportar."))
    elif autoridade.authid() not in (CRS_CADASTRO, "EPSG:4326"):
        achados.append(_achado(
            "aviso", rotulo,
            f"A camada está em {autoridade.authid()}; será reprojetada "
            f"para {CRS_CADASTRO} (SIRGAS 2000) na exportação."))

    partes = perimetro.asGeometryCollection()
    if len(partes) > 1:
        achados.append(_achado(
            "aviso", rotulo,
            f"O perímetro tem {len(partes)} partes separadas. O SICAR "
            f"aceita, mas costuma exigir justificativa de imóvel "
            f"descontínuo."))

    vertices = _contar_vertices(perimetro)
    if vertices > VERTICES_DEMAIS:
        achados.append(_achado(
            "aviso", rotulo,
            f"{vertices} vértices. Acima de ~{VERTICES_DEMAIS} o módulo de "
            f"cadastro fica lento; vale simplificar antes de enviar."))

    buracos = _contar_buracos(perimetro)
    if buracos:
        achados.append(_achado(
            "aviso", rotulo,
            f"O perímetro tem {buracos} anel(éis) interno(s) — confirme se "
            f"é um vazio real (enclave) e não falha de digitalização."))

    area_imovel = _area_ha(perimetro, crs)
    resumo["area_ha"] = area_imovel
    resumo["areas"][CHAVE_PERIMETRO] = area_imovel
    if modulo_fiscal_ha:
        resumo["modulos_fiscais"] = area_imovel / modulo_fiscal_ha

    # ── os demais temas ──
    for chave, rot, _obrig in TEMAS:
        if chave == CHAVE_PERIMETRO:
            continue
        camada = mapeamento.get(chave)
        if camada is None:
            continue

        geom = _dissolver(camada, crs)
        if geom is None:
            achados.append(_achado("aviso", rot, "Camada escolhida, mas sem geometria."))
            continue

        for p in _problemas_de_geometria(geom):
            achados.append(_achado("erro", rot, f"Geometria inválida: {p}."))

        if geom.type() == Qgis.GeometryType.Polygon:
            resumo["areas"][chave] = _area_ha(geom, crs)

        # Contenção: tudo que o cadastro declara tem de estar dentro do
        # imóvel. É a pendência mais comum e a mais fácil de evitar.
        try:
            fora = geom.difference(perimetro)
        except Exception:
            fora = None
        if fora is not None and not fora.isEmpty():
            if geom.type() == Qgis.GeometryType.Polygon:
                area_fora = _area_ha(fora, crs)
                if area_fora > TOLERANCIA_HA:
                    achados.append(_achado(
                        "erro", rot,
                        f"{texto.ha(area_fora)} fora do perímetro do imóvel."))
            else:
                achados.append(_achado(
                    "aviso", rot, "Há trechos fora do perímetro do imóvel."))

    # ── Reserva Legal ──
    area_rl = resumo["areas"].get("reserva_legal")
    minimo = resumo["minimo_rl"]
    if area_rl is not None and area_imovel > 0:
        pct = area_rl / area_imovel * 100
        resumo["percentual_rl"] = pct
        rot = ROTULOS["reserva_legal"]
        if pct + 0.05 < minimo:
            falta = (minimo / 100 * area_imovel) - area_rl
            achados.append(_achado(
                "erro", rot,
                f"{texto.num(pct)}% do imóvel, abaixo dos {minimo}% exigidos "
                f"neste regime — faltam {texto.ha(falta)}."))
        else:
            achados.append(_achado(
                "ok", rot, f"{pct:.2f}% do imóvel (mínimo do regime: {minimo}%)."))
    elif mapeamento.get(CHAVE_PERIMETRO) is not None:
        achados.append(_achado(
            "aviso", ROTULOS["reserva_legal"],
            "Nenhuma camada de Reserva Legal escolhida — o cadastro a exige "
            "salvo nas exceções do art. 68 da Lei 12.651/2012."))

    if not any(a["nivel"] == "erro" for a in achados):
        achados.insert(0, _achado(
            "ok", "Conjunto", "Nenhum impedimento encontrado nas checagens "
                              "geométricas."))
    return resumo, achados


def _contar_buracos(geom: QgsGeometry) -> int:
    total = 0
    try:
        if geom.isMultipart():
            for poly in geom.asMultiPolygon():
                total += max(0, len(poly) - 1)
        else:
            total += max(0, len(geom.asPolygon()) - 1)
    except Exception:
        return 0
    return total


# ── módulo fiscal ────────────────────────────────────────
def modulo_fiscal_do_municipio(uf: str, municipio: str, timeout: int = 40,
                               amostra: int = 30):
    """
    Módulo fiscal do município, em hectares.

    Derivado do próprio SICAR, não de tabela embutida: o WFS publica,
    por imóvel, a área e o número de módulos fiscais correspondente, e a
    razão entre os dois É o módulo fiscal do município. Pega-se a
    mediana de uma amostra para não depender de um registro com área
    arredondada.

    A vantagem sobre embutir a tabela do INCRA é que o valor vem da
    mesma base que vai julgar o cadastro — e não envelhece aqui dentro.
    """
    from .sources import wfs

    uf = (uf or "").strip().upper()
    municipio = (municipio or "").strip()
    if not uf or not municipio:
        return False, "Informe a UF e o município."

    ok, dados = wfs._consultar(
        uf, f"municipio ILIKE '%{municipio}%'", timeout, limite=amostra)
    if not ok:
        return False, dados

    razoes = []
    for f in (dados.get("features") or []):
        p = f.get("properties") or {}
        try:
            area, mf = float(p.get("area")), float(p.get("m_fiscal"))
        except (TypeError, ValueError):
            continue
        if area > 0 and mf > 0:
            razoes.append(area / mf)

    if len(razoes) < 3:
        return False, (f"Não consegui amostra suficiente em '{municipio}' "
                       f"({uf}) para deduzir o módulo fiscal.")
    return True, round(median(razoes), 2)


# ── memorial descritivo ──────────────────────────────────
def _dms(graus: float) -> str:
    g = int(graus)
    m_ = (graus - g) * 60
    m = int(m_)
    s = (m_ - m) * 60
    return f"{g:03d}°{m:02d}'{s:05.2f}\""


def memorial(camada_perimetro: QgsVectorLayer, vizinhos: dict = None,
             tolerancia_m: float = 5.0):
    """
    Vértices, azimutes e distâncias do perímetro.

    Medido em UTM SIRGAS 2000, porque memorial descritivo trabalha com
    azimute e distância PLANOS — é o que o cartório e o georreferenciamento
    esperam. A área continua sendo a geodésica, que é a do cadastro.

    `vizinhos` é opcional: {rótulo: QgsGeometry em EPSG:4674}. Havendo,
    cada segmento recebe o confrontante cujo polígono encosta nele.

    Devolve (True, {"crs", "linhas", "fechamento_m", "perimetro_m"}) ou
    (False, mensagem).
    """
    if camada_perimetro is None:
        return False, "Escolha a camada do perímetro."

    crs_geo = QgsCoordinateReferenceSystem(CRS_CADASTRO)
    geom = _dissolver(camada_perimetro, crs_geo)
    if geom is None:
        return False, "A camada do perímetro não tem geometria."

    centro = geom.centroid().asPoint()
    crs_utm, rotulo_crs = crs_utm_sirgas(centro.x(), centro.y())
    if not crs_utm.isValid():
        return False, "Não consegui montar a projeção UTM para este local."

    plano = _transformar(geom, crs_geo, crs_utm)
    try:
        aneis = ([p[0] for p in plano.asMultiPolygon()] if plano.isMultipart()
                 else [plano.asPolygon()[0]])
    except Exception:
        return False, "Não consegui ler os vértices do perímetro."

    viz_utm = {}
    for nome, g in (vizinhos or {}).items():
        try:
            viz_utm[nome] = _transformar(g, crs_geo, crs_utm)
        except Exception as e:
            log.debug("Vizinho %s ignorado (falha ao reprojetar): %s", nome, e)
            continue

    linhas = []
    perimetro_total = 0.0
    for i_anel, pontos in enumerate(aneis):
        # O anel fechado repete o primeiro ponto no fim; tirá-lo evita
        # um segmento de comprimento zero na última linha.
        if len(pontos) > 1 and pontos[0] == pontos[-1]:
            pontos = pontos[:-1]
        total = len(pontos)
        if total < 3:
            continue
        for i, p in enumerate(pontos):
            q = pontos[(i + 1) % total]
            de, dn = q.x() - p.x(), q.y() - p.y()
            distancia = math.hypot(de, dn)
            perimetro_total += distancia
            azimute = math.degrees(math.atan2(de, dn)) % 360.0
            linhas.append({
                "parte": i_anel + 1,
                "vertice": f"V-{i + 1:04d}",
                "este_m": round(p.x(), 3),
                "norte_m": round(p.y(), 3),
                "proximo": f"V-{(i + 2) if i + 1 < total else 1:04d}",
                "azimute": _dms(azimute),
                "azimute_graus": round(azimute, 4),
                "distancia_m": round(distancia, 3),
                "confrontante": _confrontante(p, q, viz_utm, tolerancia_m),
            })

    if not linhas:
        return False, "O perímetro não tem vértices suficientes."

    return True, {
        "crs": rotulo_crs,
        "linhas": linhas,
        "perimetro_m": round(perimetro_total, 3),
        # Soma vetorial dos lados: num polígono fechado tem de dar zero.
        # Qualquer coisa acima de milímetros indica anel que não fecha.
        "fechamento_m": round(_fechamento(linhas), 4),
    }


def _fechamento(linhas) -> float:
    de = sum(ln["distancia_m"] * math.sin(math.radians(ln["azimute_graus"]))
             for ln in linhas)
    dn = sum(ln["distancia_m"] * math.cos(math.radians(ln["azimute_graus"]))
             for ln in linhas)
    return math.hypot(de, dn)


def _confrontante(p, q, vizinhos_utm: dict, tolerancia_m: float) -> str:
    """Quem encosta no segmento p→q. Testa o ponto médio deslocado para
    fora, com folga, e devolve o vizinho de maior contato."""
    if not vizinhos_utm:
        return ""
    from qgis.core import QgsPointXY
    meio = QgsPointXY((p.x() + q.x()) / 2, (p.y() + q.y()) / 2)
    try:
        sonda = QgsGeometry.fromPointXY(meio).buffer(tolerancia_m, 8)
    except Exception:
        return ""
    melhor, melhor_area = "", 0.0
    for nome, g in vizinhos_utm.items():
        try:
            inter = sonda.intersection(g)
            if inter and not inter.isEmpty():
                a = inter.area()
                if a > melhor_area:
                    melhor, melhor_area = nome, a
        except Exception as e:
            log.debug("Confrontante %s ignorado (falha na interseção): %s",
                      nome, e)
            continue
    return melhor


COLUNAS_MEMORIAL = ["parte", "vertice", "este_m", "norte_m", "proximo",
                    "azimute", "distancia_m", "confrontante"]


# ── vizinhança ───────────────────────────────────────────
def geometrias_de_feicoes(feicoes):
    """
    {código: QgsGeometry} a partir das feições cruas do WFS.

    O retângulo pega vizinhos que só passam perto; quem de fato encosta
    ou invade é decidido aqui, medindo a interseção de verdade.
    """
    import json

    from qgis.core import QgsFields, QgsJsonUtils

    saida = {}
    for feat in feicoes or []:
        props = feat.get("properties") or {}
        codigo = props.get("cod_imovel") or "?"
        try:
            fs = QgsJsonUtils.stringToFeatureList(json.dumps(feat), QgsFields())
        except Exception as e:
            log.debug("Feição %s ignorada (GeoJSON ilegível): %s", codigo, e)
            continue
        if fs and fs[0].hasGeometry():
            saida[codigo] = fs[0].geometry()
    return saida


def medir_vizinhanca(perimetro_geom, vizinhos: dict, area_imovel_ha: float):
    """
    Para cada vizinho: área sobreposta e o que isso significa.

    Encostar é normal — confrontante existe. INVADIR não é: sobreposição
    com CAR ativo é das pendências que mais atrasam cadastro, e o jeito
    barato de descobrir é antes de enviar.
    """
    crs = QgsCoordinateReferenceSystem(CRS_CADASTRO)
    resultado = []
    for codigo, geom in (vizinhos or {}).items():
        try:
            inter = perimetro_geom.intersection(geom)
        except Exception as e:
            log.debug("Vizinho %s ignorado (falha na interseção): %s",
                      codigo, e)
            continue
        if inter is None or inter.isEmpty():
            continue
        # Interseção que não é área (dois polígonos que só se tocam pela
        # borda devolvem linha ou ponto) é confrontância, não invasão.
        if inter.type() != Qgis.GeometryType.Polygon:
            resultado.append({"codigo": codigo, "area_ha": 0.0,
                              "percentual": 0.0, "apenas_encosta": True})
            continue
        area = _area_ha(inter, crs)
        if area <= TOLERANCIA_HA:
            resultado.append({"codigo": codigo, "area_ha": area,
                              "percentual": 0.0, "apenas_encosta": True})
            continue
        resultado.append({
            "codigo": codigo,
            "area_ha": area,
            "percentual": (area / area_imovel_ha * 100) if area_imovel_ha else 0.0,
            "apenas_encosta": False,
        })
    resultado.sort(key=lambda r: r["area_ha"], reverse=True)
    return resultado


def camada_de_vizinhos(vizinhos: dict, nome="CAR vizinhos"):
    """Camada em memória com os vizinhos, para corrigir o desenho
    olhando para eles em vez de para uma lista de códigos."""
    from qgis.core import QgsFeature, QgsField, QgsVectorLayer
    from qgis.PyQt.QtCore import QMetaType

    if not vizinhos:
        return None
    camada = QgsVectorLayer(f"MultiPolygon?crs={CRS_CADASTRO}", nome, "memory")
    prov = camada.dataProvider()
    prov.addAttributes([QgsField("codigo_car", QMetaType.Type.QString)])
    camada.updateFields()
    feicoes = []
    for codigo, geom in vizinhos.items():
        f = QgsFeature(camada.fields())
        f.setGeometry(geom)
        f.setAttributes([codigo])
        feicoes.append(f)
    prov.addFeatures(feicoes)
    camada.updateExtents()
    return camada


# ── pacote de shapefiles ─────────────────────────────────
def exportar_pacote(mapeamento: dict, pasta: str, prefixo: str = ""):
    """
    Um shapefile por tema, todos em SIRGAS 2000 geográficas.

    Devolve (ok, {"gravados": [(tema, caminho)], "falhas": [(tema, erro)]}).
    """
    from pathlib import Path

    from . import export

    pasta = Path(str(pasta))
    try:
        pasta.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return False, {"gravados": [], "falhas": [("pasta", str(e))]}

    prefixo = _limpar_nome(prefixo)
    gravados, falhas = [], []
    for chave, rotulo, _ in TEMAS:
        camada = mapeamento.get(chave)
        if camada is None:
            continue
        nome = f"{prefixo}_{chave}" if prefixo else chave
        destino = pasta / f"{nome}.shp"
        ok, res = export.exportar_camada(
            camada, str(destino), "shp", nome_camada=nome,
            crs_destino=CRS_CADASTRO,
        )
        (gravados if ok else falhas).append((rotulo, res))

    return bool(gravados), {"gravados": gravados, "falhas": falhas}


def _limpar_nome(texto: str) -> str:
    """Nome de arquivo a partir do código do CAR, sem o que o Windows
    recusa."""
    import re
    return re.sub(r"[^A-Za-z0-9_-]+", "_", (texto or "").strip()).strip("_")[:60]
