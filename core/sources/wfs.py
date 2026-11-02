# -*- coding: utf-8 -*-
"""
wfs.py — WFS público nacional do SICAR (geoserver.car.gov.br).

Cobre qualquer UF, sem login, sem token, sem captcha. É a fonte padrão
do plugin; a SEMAS-PA só entra onde traz algo a mais (ver semas_pa.py).

Campos publicados (conferidos numa consulta real em out/2026):
cod_imovel, status_imovel, dat_criacao, data_atualizacao, area,
condicao, uf, municipio, cod_municipio_ibge, m_fiscal, tipo_imovel.
Não há dado de proprietário — o serviço aberto não publica dado
pessoal.

ATENÇÃO ao mexer aqui: a v1 do plugin lia "num_area" e "ind_status",
que NÃO existem nesta camada — por isso área e situação vinham
vazias. Os nomes certos são "area" e "status_imovel".
"""

import logging
import ssl

import requests
from requests.adapters import HTTPAdapter

log = logging.getLogger("GeoCAR_Suite")

BASE_WFS = "https://geoserver.car.gov.br/geoserver/sicar/ows"

SITUACAO = {
    "AT": "Ativo", "PE": "Pendente", "SU": "Suspenso",
    "CA": "Cancelado", "RE": "Em Retificação",
}

# Nomes de campo da camada, num só lugar — se o SICAR renomear algo,
# muda aqui e não espalhado pelo módulo.
F_CODIGO = "cod_imovel"
F_AREA = "area"            # em hectares
F_STATUS = "status_imovel"  # sigla: AT, PE, SU, CA, RE
F_CONDICAO = "condicao"     # texto livre: "Em análise", etc.
F_MUNICIPIO = "municipio"
F_TIPO = "tipo_imovel"      # IRU = imóvel rural


class _TLSLegado(HTTPAdapter):
    """
    O geoserver do SICAR roda numa pilha TLS antiga, que o OpenSSL
    moderno recusa com SSLV3_ALERT_HANDSHAKE_FAILURE. Rebaixamos o
    SECLEVEL apenas nesta conexão — montado só no host do geoserver,
    nunca globalmente, para não enfraquecer as outras conexões HTTPS
    do QGIS.
    """

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.set_ciphers("DEFAULT@SECLEVEL=1")
        ctx.options |= 0x4  # OP_LEGACY_SERVER_CONNECT
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def _sessao() -> requests.Session:
    s = requests.Session()
    s.mount("https://geoserver.car.gov.br", _TLSLegado())
    return s


def _camada(uf: str) -> str:
    return f"sicar:sicar_imoveis_{uf.strip().lower()}"


def _consultar(uf: str, cql: str, timeout: int, limite: int = None):
    params = {
        "service": "WFS", "version": "2.0.0", "request": "GetFeature",
        "typeName": _camada(uf), "outputFormat": "application/json",
        "CQL_FILTER": cql,
    }
    if limite:
        params["count"] = limite

    try:
        resp = _sessao().get(BASE_WFS, params=params, timeout=timeout)
        resp.raise_for_status()
        return True, resp.json()
    except requests.exceptions.Timeout:
        return False, f"Tempo esgotado ({timeout}s) ao consultar o SICAR nacional."
    except requests.exceptions.SSLError as e:
        return False, f"Falha de TLS com o geoserver do SICAR: {e}"
    except requests.exceptions.ConnectionError:
        return False, ("Sem conexão com o SICAR nacional "
                       "(geoserver.car.gov.br). Verifique a internet ou o proxy.")
    except requests.exceptions.HTTPError as e:
        return False, f"O SICAR nacional respondeu com erro: {e}"
    except ValueError:
        return False, (f"Resposta do SICAR não é JSON válido — a camada "
                       f"'{_camada(uf)}' pode não existir para {uf.upper()}.")
    except Exception as e:  # rede é imprevisível; a UI precisa de um texto
        return False, str(e)


def buscar_imovel(codigo_car: str, timeout: int = 30):
    """
    Consulta um imóvel pelo código.

    Retorna (True, dados) com:
        {"codigo_car", "geojson", "municipio", "area_ha",
         "situacao", "responsavel", "fonte"}
    ou (False, "mensagem").
    """
    from ..carcode import normalizar, uf_de

    codigo_car = normalizar(codigo_car)
    uf = uf_de(codigo_car)
    if not uf:
        return False, f"Não consegui identificar a UF em '{codigo_car}'."

    ok, dados = _consultar(uf, f"cod_imovel='{codigo_car}'", timeout)
    if not ok:
        return False, dados

    feicoes = dados.get("features") or []
    if not feicoes:
        return False, (f"Imóvel {codigo_car} não encontrado no SICAR nacional "
                       f"(camada de {uf}).")

    f = feicoes[0]
    p = f.get("properties") or {}
    sigla = str(p.get(F_STATUS) or "").strip().upper()

    return True, {
        "codigo_car":  p.get(F_CODIGO) or codigo_car,
        "geojson":     {"type": "Feature", "geometry": f.get("geometry"),
                        "properties": p},
        "municipio":   p.get(F_MUNICIPIO) or "",
        "area_ha":     p.get(F_AREA),
        "situacao":    SITUACAO.get(sigla, sigla),
        "condicao":    p.get(F_CONDICAO) or "",
        "tipo_imovel": p.get(F_TIPO) or "",
        # O serviço aberto não publica titular (dado pessoal).
        "responsavel": "",
        "fonte":       "SICAR nacional (WFS)",
    }


def listar_por_municipio(uf: str, municipio: str, timeout: int = 30, limite: int = 1000):
    """
    Lista os imóveis de um município. Útil quando não se tem o código
    em mãos — é o caminho viável em qualquer UF, já que busca por
    titular não existe neste serviço.
    """
    uf = (uf or "").strip().upper()
    municipio = (municipio or "").strip()
    if not uf or not municipio:
        return False, "Informe a UF e o município."

    ok, dados = _consultar(
        uf, f"municipio ILIKE '%{municipio}%'", timeout, limite=limite
    )
    if not ok:
        return False, dados

    feicoes = dados.get("features") or []
    if not feicoes:
        return False, f"Nenhum imóvel encontrado em '{municipio}' ({uf})."

    resultado = []
    for f in feicoes:
        p = f.get("properties") or {}
        sigla = str(p.get(F_STATUS) or "").strip().upper()
        resultado.append({
            "codigo_car": p.get(F_CODIGO) or "",
            "municipio":  p.get(F_MUNICIPIO) or municipio,
            "area_ha":    p.get(F_AREA),
            "situacao":   SITUACAO.get(sigla, sigla),
            "condicao":   p.get(F_CONDICAO) or "",
        })

    if len(resultado) >= limite:
        log.warning("Lista truncada no limite de %s imóveis.", limite)
    return True, resultado


# Coluna de geometria da camada (conferida por DescribeFeatureType).
F_GEOMETRIA = "geo_area_imovel"


def listar_por_bbox(uf: str, xmin: float, ymin: float, xmax: float,
                    ymax: float, timeout: int = 40, limite: int = 200,
                    excluir: str = None):
    """
    Imóveis já cadastrados dentro de um retângulo (graus, EPSG:4326).

    Serve para achar os confrontantes de um perímetro novo antes de
    cadastrá-lo: sobreposição com CAR existente é motivo clássico de
    pendência, e descobrir isso depois do envio custa meses.

    ARMADILHA: o 'EPSG:4326' no fim do BBOX não é enfeite. Sem ele o
    GeoServer segue a ordem de eixos do WFS 2.0 e lê os números como
    LAT,LON — a consulta volta com zero feições e parece, de forma
    convincente, que não há vizinho nenhum. Testado: mesma caixa, 0
    resultados sem o CRS e 15 com ele.

    Devolve (True, [feições GeoJSON]) — com geometria, para o chamador
    calcular a interseção de verdade aqui, em vez de confiar num
    INTERSECTS remoto que sofre do mesmo problema de eixos.
    """
    uf = (uf or "").strip().upper()
    if not uf:
        return False, "Informe a UF."

    cql = (f"BBOX({F_GEOMETRIA},{xmin:.8f},{ymin:.8f},"
           f"{xmax:.8f},{ymax:.8f},'EPSG:4326')")
    if excluir:
        cql += f" AND {F_CODIGO} <> '{excluir}'"

    ok, dados = _consultar(uf, cql, timeout, limite=limite)
    if not ok:
        return False, dados
    return True, (dados.get("features") or [])
