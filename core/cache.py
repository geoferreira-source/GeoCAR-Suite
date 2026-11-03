# -*- coding: utf-8 -*-
"""
cache.py — guarda consultas já feitas, para não repetir rede.

Dado do CAR muda devagar (uma retificação leva meses), então reconsultar
o mesmo imóvel na mesma semana é desperdício — e no lote, onde a mesma
planilha costuma ser reprocessada, é a diferença entre segundos e
minutos.

Onde fica o arquivo
-------------------
No diretório do PERFIL do QGIS, não na pasta do plugin. A pasta do
plugin é substituída a cada atualização: um cache ali seria apagado
junto, e o usuário pagaria a rede de novo sem entender por quê.

SQLite da biblioteca padrão — nenhuma dependência nova.

Falhar em silêncio é proposital
-------------------------------
Cache é otimização, nunca requisito. Se o banco estiver corrompido, o
disco cheio ou sem permissão, todas as funções daqui devolvem "não
tenho" e a consulta segue pela rede. Em nenhuma hipótese uma falha de
cache impede o usuário de trabalhar.
"""

import json
import logging
import os
import sqlite3
import time

log = logging.getLogger("GeoCAR_Suite")

NOME_ARQUIVO = "geocar_suite_cache.sqlite"


def caminho_banco() -> str:
    """Arquivo do cache dentro do perfil ativo do QGIS."""
    try:
        from qgis.core import QgsApplication
        base = QgsApplication.qgisSettingsDirPath()
    except Exception:
        base = os.path.expanduser("~")
    pasta = os.path.join(base, "geocar_suite")
    try:
        os.makedirs(pasta, exist_ok=True)
    except Exception:
        return os.path.join(os.path.expanduser("~"), NOME_ARQUIVO)
    return os.path.join(pasta, NOME_ARQUIVO)


def _conectar():
    con = sqlite3.connect(caminho_banco(), timeout=5)
    con.execute(
        """CREATE TABLE IF NOT EXISTS imoveis (
               codigo   TEXT PRIMARY KEY,
               salvo_em REAL NOT NULL,
               dados    TEXT NOT NULL
           )"""
    )
    return con


def obter(codigo: str, validade_dias: int = 30):
    """
    Devolve (dados, idade_em_segundos) se houver entrada ainda válida,
    ou (None, None). Entrada vencida é tratada como ausente.
    """
    from . import carcode
    codigo = carcode.normalizar(codigo)
    if not codigo:
        return None, None
    try:
        with _conectar() as con:
            linha = con.execute(
                "SELECT salvo_em, dados FROM imoveis WHERE codigo = ?", (codigo,)
            ).fetchone()
        if not linha:
            return None, None
        salvo_em, bruto = linha
        idade = time.time() - salvo_em
        if idade > validade_dias * 86400:
            return None, None
        return json.loads(bruto), idade
    except Exception as e:
        log.debug("Cache indisponível na leitura: %s", e)
        return None, None


def guardar(codigo: str, dados: dict) -> bool:
    from . import carcode
    codigo = carcode.normalizar(codigo)
    if not codigo or not isinstance(dados, dict):
        return False
    try:
        # Marcas de execução não são dado do imóvel e não vão para o
        # banco — senão a idade de uma consulta antiga seria regravada
        # como se fosse nova.
        limpo = {k: v for k, v in dados.items() if not k.startswith("_")}
        with _conectar() as con:
            con.execute(
                "INSERT OR REPLACE INTO imoveis (codigo, salvo_em, dados) "
                "VALUES (?, ?, ?)",
                (codigo, time.time(), json.dumps(limpo, ensure_ascii=False)),
            )
        return True
    except Exception as e:
        log.debug("Cache indisponível na escrita: %s", e)
        return False


def remover(codigo: str) -> bool:
    from . import carcode
    try:
        with _conectar() as con:
            con.execute("DELETE FROM imoveis WHERE codigo = ?",
                        (carcode.normalizar(codigo),))
        return True
    except Exception:
        return False


def limpar() -> bool:
    try:
        with _conectar() as con:
            con.execute("DELETE FROM imoveis")
        try:
            with _conectar() as con:
                con.execute("VACUUM")   # devolve o espaço ao disco
        except Exception as e:
            # Compactar é só um bônus: os dados já foram apagados.
            log.debug("VACUUM do cache falhou (sem impacto): %s", e)
        return True
    except Exception as e:
        log.debug("Não consegui limpar o cache: %s", e)
        return False


def estatisticas() -> dict:
    """{"itens", "tamanho_kb", "mais_antigo_dias"} — tudo zerado se o
    cache não estiver acessível."""
    vazio = {"itens": 0, "tamanho_kb": 0.0, "mais_antigo_dias": None}
    try:
        with _conectar() as con:
            n, antigo = con.execute(
                "SELECT COUNT(*), MIN(salvo_em) FROM imoveis"
            ).fetchone()
        tamanho = os.path.getsize(caminho_banco()) / 1024 if os.path.exists(
            caminho_banco()) else 0
        return {
            "itens": n or 0,
            "tamanho_kb": round(tamanho, 1),
            "mais_antigo_dias": round((time.time() - antigo) / 86400, 1)
            if antigo else None,
        }
    except Exception:
        return vazio


def descrever_idade(segundos: float) -> str:
    """'há 3 min', 'há 2 dias' — para a interface dizer de quando é o
    dado que está mostrando."""
    if segundos is None:
        return ""
    if segundos < 90:
        return "agora há pouco"
    if segundos < 5400:
        return f"há {int(segundos // 60)} min"
    if segundos < 172800:
        return f"há {int(segundos // 3600)} h"
    return f"há {int(segundos // 86400)} dias"
