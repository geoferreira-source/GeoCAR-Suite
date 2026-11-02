# -*- coding: utf-8 -*-
"""
semas_pa.py — portal autenticado da SEMAS-PA (apenas Pará).

O que esta fonte traz além do WFS nacional:
  • busca por CPF/CNPJ do proprietário (o serviço aberto não publica
    titular, por proteção de dado pessoal);
  • demonstrativo em PDF.

AUTENTICAÇÃO — mudança importante em relação à v1
-------------------------------------------------
Na v1 o login era feito abrindo o Chrome via Selenium/CDP para
interceptar o Bearer token. Era a parte mais frágil do plugin: quebrava
quando o Chrome atualizava, exigia chromedriver compatível e travava o
QGIS enquanto rodava.

Aqui o token é simplesmente COLADO pelo usuário (DevTools → Network →
qualquer requisição do portal → cabeçalho authorization) e guardado no
QSettings. É um Bearer de sessão: expira sozinho em cerca de uma hora,
e o plugin avisa quando isso acontece.

Nada aqui importa Selenium. O núcleo funciona sem ele, e o WFS
nacional — que não precisa de token nenhum — continua atendendo o Pará
para a consulta por código.
"""

import logging
import re
import time

import requests

from .. import config

log = logging.getLogger("GeoCAR_Suite")

BASE = "https://portal-servicos-sistemas.semas.pa.gov.br"
URL_PORTAL = BASE + "/consulta-mapa"
URL_BUSCAR_CAR = (BASE + "/api/1/servicedesk-embedded-servicos-sistemas"
                  "/_classId/649086b520338013be9c0e2d/buscarCAR")
URL_GEOJSON = (BASE + "/api/1/servicedesk-embedded-servicos-sistemas"
               "/_classId/65689c680c1eb452fcd3d01b"
               "/consultaBuscarGeojsonDaAreaDoImovel")

CABECALHOS = {
    "Content-Type": "application/json",
    "Accept": "*/*",
    "Accept-Language": "pt",
    "Origin": BASE,
    "Referer": URL_PORTAL,
    # Cabeçalho próprio do portal: sem ele a API recusa a chamada.
    "x-explorer-account-token": "atos-autorizativos",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/153.0.0.0 Safari/537.36"),
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
}

VALIDADE_TOKEN_S = 3600


# ─────────────────────────────────────────────────────────
# TOKEN
# ─────────────────────────────────────────────────────────

def definir_token(token: str) -> tuple:
    """Guarda o token colado pelo usuário. Aceita com ou sem o
    prefixo 'Bearer '."""
    token = (token or "").strip()
    token = re.sub(r"^Bearer\s+", "", token, flags=re.IGNORECASE).strip()
    if not token:
        config.set("semas_token", "")
        config.set("semas_token_em", 0)
        return False, "Token vazio."
    if len(token) < 40:
        return False, "Isso não parece um token (muito curto)."
    config.set("semas_token", token)
    config.set("semas_token_em", int(time.time()))
    return True, "Token salvo."


def limpar_token() -> None:
    config.set("semas_token", "")
    config.set("semas_token_em", 0)


def status_token() -> dict:
    token = config.get("semas_token") or ""
    obtido = int(config.get("semas_token_em") or 0)
    idade = int(time.time()) - obtido if obtido else None
    return {
        "tem_token": bool(token),
        "idade_s": idade,
        "provavelmente_expirado": bool(token and idade is not None
                                       and idade > VALIDADE_TOKEN_S),
    }


def _sessao():
    token = config.get("semas_token") or ""
    if not token:
        return None
    s = requests.Session()
    s.headers.update(CABECALHOS)
    s.headers["authorization"] = f"Bearer {token}"
    return s


def _sem_token():
    return (False, "Sem token da SEMAS-PA. Cole um token na aba "
                   "Configurações (DevTools → Network → cabeçalho "
                   "authorization de qualquer chamada do portal).")


def _trata_401():
    return (False, "O token da SEMAS-PA expirou ou foi recusado. "
                   "Pegue um novo no portal e cole na aba Configurações.")


# ─────────────────────────────────────────────────────────
# CONSULTAS
# ─────────────────────────────────────────────────────────

def _lista_de(dados):
    """A API varia a chave onde devolve a lista conforme o endpoint."""
    if isinstance(dados, list):
        return dados
    if not isinstance(dados, dict):
        return []
    for chave in ("items", "data", "content", "lista", "registros", "results"):
        if isinstance(dados.get(chave), list):
            return dados[chave]
    for valor in dados.values():
        if isinstance(valor, list) and valor:
            return valor
    return []


def _mascarar(digitos: str) -> str:
    """
    Aplica a máscara oficial ao CPF/CNPJ.

    Isto não é cosmético: o portal compara a string literal e envia o
    documento COM pontuação ("106.360.303-00"). Mandando só dígitos, a
    API responde 200 OK com lista vazia — consulta válida cujo filtro
    nunca casa. Era a causa de a busca por CPF parecer "não encontrado".
    """
    d = re.sub(r"\D", "", digitos or "")
    if len(d) == 11:
        return f"{d[0:3]}.{d[3:6]}.{d[6:9]}-{d[9:11]}"
    if len(d) == 14:
        return f"{d[0:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:14]}"
    return d


def buscar_por_cpf(cpf_cnpj: str, timeout: int = 30, max_paginas: int = 50):
    """
    Lista os imóveis vinculados a um CPF/CNPJ no Pará.

    Retorna (True, [{codigo_car, municipio, situacao, responsavel}, ...])
    ou (False, "mensagem").
    """
    digitos = re.sub(r"\D", "", cpf_cnpj or "")
    if len(digitos) not in (11, 14):
        return False, "Informe 11 dígitos (CPF) ou 14 (CNPJ)."

    sessao = _sessao()
    if sessao is None:
        return _sem_token()

    documento = _mascarar(digitos)
    por_pagina = 50
    achados = {}

    for pagina in range(max_paginas):
        corpo = {
            "codigoCar": None, "cpfCadastrante": None,
            "cpfCnpjProprietario": documento,
            "itemsPerPage": por_pagina, "municipio": "", "nomeImovel": None,
            "page": pagina, "precisaRecibo": False, "protocolo": None,
        }
        try:
            r = sessao.post(URL_BUSCAR_CAR, json=corpo, timeout=timeout)
            if r.status_code in (401, 403):
                return _trata_401()
            r.raise_for_status()
            itens = _lista_de(r.json())
        except requests.exceptions.Timeout:
            return False, f"Tempo esgotado ({timeout}s) no portal da SEMAS-PA."
        except requests.exceptions.ConnectionError:
            return False, "Sem conexão com o portal da SEMAS-PA."
        except Exception as e:
            return False, f"Erro ao consultar a SEMAS-PA: {e}"

        if not itens:
            break

        for item in itens:
            if not isinstance(item, dict):
                continue
            cod = (item.get("codigoCar") or "").strip().upper()
            if cod and cod not in achados:
                achados[cod] = {
                    "codigo_car": cod,
                    "municipio": item.get("municipio")
                    or item.get("nomeMunicipio") or "",
                    "situacao": item.get("situacao")
                    or item.get("statusCar") or "",
                    "responsavel": item.get("nomeProprietario")
                    or item.get("proprietario") or "",
                }

        if len(itens) < por_pagina:
            break

    if not achados:
        return False, ("Nenhum imóvel encontrado para esse CPF/CNPJ no "
                       "Pará. Se o titular tem CAR aqui, o token pode ter "
                       "expirado — gere um novo no portal.")
    return True, list(achados.values())


def buscar_imovel(codigo_car: str, timeout: int = 30):
    """
    Busca o imóvel pelo código no portal da SEMAS-PA.

    Mantido por trazer o nome do responsável, que o WFS não publica.
    Sem token, devolve a falha — e o roteador cai para o WFS nacional,
    que atende o Pará igualmente para consulta por código.
    """
    from ..carcode import normalizar

    codigo_car = normalizar(codigo_car)
    sessao = _sessao()
    if sessao is None:
        return _sem_token()

    corpo = {
        "codigoCar": codigo_car, "cpfCadastrante": None,
        "cpfCnpjProprietario": None, "itemsPerPage": 10, "municipio": "",
        "nomeImovel": None, "page": 0, "precisaRecibo": False,
        "protocolo": None,
    }
    try:
        r = sessao.post(URL_BUSCAR_CAR, json=corpo, timeout=timeout)
        if r.status_code in (401, 403):
            return _trata_401()
        r.raise_for_status()
        itens = _lista_de(r.json())
    except requests.exceptions.Timeout:
        return False, f"Tempo esgotado ({timeout}s) no portal da SEMAS-PA."
    except requests.exceptions.ConnectionError:
        return False, "Sem conexão com o portal da SEMAS-PA."
    except Exception as e:
        return False, f"Erro ao consultar a SEMAS-PA: {e}"

    if not itens:
        return False, f"Imóvel {codigo_car} não encontrado na SEMAS-PA."

    item = itens[0]
    id_interno = item.get("id") or item.get("_id") or item.get("idCar")

    geojson = None
    if id_interno:
        try:
            rg = sessao.post(URL_GEOJSON, json={"idCar": id_interno},
                             timeout=timeout)
            if rg.ok:
                geojson = rg.json()
        except Exception as e:
            log.warning("Geometria não veio da SEMAS-PA: %s", e)

    return True, {
        "codigo_car": codigo_car,
        "geojson": geojson,
        "municipio": item.get("municipio") or item.get("nomeMunicipio") or "",
        "area_ha": item.get("area") or item.get("areaImovel"),
        "situacao": item.get("situacao") or item.get("statusCar") or "",
        "responsavel": item.get("nomeProprietario")
        or item.get("proprietario") or "",
        "fonte": "SEMAS-PA",
    }
