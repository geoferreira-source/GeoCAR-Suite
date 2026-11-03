# -*- coding: utf-8 -*-
"""
config.py — preferências do plugin.

Guardadas no QSettings do próprio QGIS, sob "GeoCAR_Suite/", em vez de
um config.json dentro da pasta do plugin. Motivo: a pasta do plugin é
substituída a cada atualização, então um JSON ali é perdido (ou gera
conflito) toda vez que uma versão nova é instalada. O QSettings
sobrevive à atualização e é o lugar onde o QGIS espera encontrar
configuração de plugin.

O token da SEMAS-PA é guardado aqui também. Não é senha — é um Bearer
de sessão, que expira sozinho — mas ainda assim não é gravado em
nenhum arquivo dentro do plugin, só no perfil do usuário.
"""

from qgis.PyQt.QtCore import QSettings

PREFIXO = "GeoCAR_Suite/"

PADROES = {
    "timeout":            30,
    "carregar_no_projeto": True,
    "zoom_apos_consulta":  True,
    "calcular_sobreposicao": True,
    # Camadas do projeto ignoradas na sobreposição (nomes, separados
    # por ";"). O próprio imóvel consultado entra aqui em tempo de
    # execução para não se cruzar consigo mesmo.
    "ignorar_camadas":     "",
    "pasta_saida":         "",
    # Valores PADRÃO vazios, não credenciais: o token real é digitado
    # pelo usuário e vai para o QSettings. O Bandit confunde o nome da
    # chave com senha embutida (B105) — falso positivo.
    "semas_token":         "",  # nosec B105
    "semas_token_em":      0,   # nosec B105
    # Cache de consultas. 30 dias é conservador para dado de CAR, que
    # muda em escala de meses; quem precisa do dado do dia desmarca.
    "usar_cache":          True,
    "cache_validade_dias": 30,
    # Simbologia própria na camada do imóvel. Desligável porque quem
    # tem estilo padrão de projeto ou .qml próprio não quer o nosso.
    "simbologia_automatica": True,
}


def _s() -> QSettings:
    return QSettings()


def get(chave: str):
    padrao = PADROES.get(chave)
    valor = _s().value(PREFIXO + chave, padrao)
    # QSettings devolve tudo como string em alguns sistemas (Windows):
    # convertemos de volta pelo tipo do padrão.
    if isinstance(padrao, bool):
        if isinstance(valor, str):
            return valor.lower() in ("true", "1", "yes")
        return bool(valor)
    if isinstance(padrao, int):
        try:
            return int(valor)
        except (TypeError, ValueError):
            return padrao
    return valor if valor is not None else padrao


def set(chave: str, valor):  # noqa: A001 (set é claro no contexto)
    _s().setValue(PREFIXO + chave, valor)


def camadas_ignoradas() -> list:
    bruto = get("ignorar_camadas") or ""
    return [n.strip() for n in str(bruto).split(";") if n.strip()]


def definir_camadas_ignoradas(nomes) -> None:
    set("ignorar_camadas", ";".join(nomes))
