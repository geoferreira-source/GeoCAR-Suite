# -*- coding: utf-8 -*-
"""
carcode.py — validação e leitura do código de imóvel do CAR.

O código já carrega a UF no prefixo, o que permite rotear a consulta
para a fonte certa sem o usuário informar o estado:

    PA-1501402-A1B2C3D4E5F6...
    ^^ UF       ^^^^^^^ IBGE do município
"""

import re

UFS = {
    "AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG",
    "MS", "MT", "PA", "PB", "PE", "PI", "PR", "RJ", "RN", "RO", "RR",
    "RS", "SC", "SE", "SP", "TO",
}

# UF-<7 dígitos do IBGE>-<identificador alfanumérico>
_PADRAO = re.compile(r"^([A-Z]{2})-(\d{7})-([0-9A-F]+)$", re.IGNORECASE)


def normalizar(codigo: str) -> str:
    """Tira espaços e deixa em maiúsculas. Não valida."""
    return (codigo or "").strip().upper().replace(" ", "")


def validar(codigo: str):
    """
    Retorna (True, "") se o código tem forma válida, ou (False, motivo).

    A validação é só de FORMA — se o imóvel existe, quem diz é a
    consulta. Serve para falhar cedo e com mensagem clara, em vez de
    gastar uma requisição com um código obviamente torto (o que era
    comum no processamento em lote, com planilhas sujas).
    """
    codigo = normalizar(codigo)
    if not codigo:
        return False, "Código vazio."

    m = _PADRAO.match(codigo)
    if not m:
        return False, (
            f"Formato inesperado: '{codigo}'. "
            "O esperado é UF-0000000-IDENTIFICADOR "
            "(ex.: PA-1501402-A1B2C3D4...)."
        )

    uf = m.group(1).upper()
    if uf not in UFS:
        return False, f"UF desconhecida no código: '{uf}'."

    return True, ""


def uf_de(codigo: str) -> str:
    """UF do código, ou string vazia se não der para extrair."""
    codigo = normalizar(codigo)
    m = _PADRAO.match(codigo)
    return m.group(1).upper() if m else (codigo.split("-")[0] if "-" in codigo else "")


def municipio_ibge_de(codigo: str) -> str:
    """Código IBGE do município embutido no código do CAR."""
    m = _PADRAO.match(normalizar(codigo))
    return m.group(2) if m else ""
