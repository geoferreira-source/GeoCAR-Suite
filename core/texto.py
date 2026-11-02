# -*- coding: utf-8 -*-
"""
texto.py — formatação de número no padrão brasileiro.

Existe por causa de um bug que estava espalhado pelo plugin: o idioma
`f"{v:,.2f}".replace(",", ".")`. A intenção era trocar o separador de
milhar, mas a troca é cega e atinge tudo:

    1234.5  →  "1,234.50"  →  "1.234.50"   ← milhar e decimal iguais
    "5.30% do imóvel, abaixo de…"          → vírgula da FRASE virou ponto

Abaixo de mil hectares ninguém percebe; acima, o número fica ambíguo
justamente nos imóveis grandes, que é onde ele importa.
"""


def num(valor, casas: int = 2) -> str:
    """1234.5 → '1.234,50'. Milhar com ponto, decimal com vírgula."""
    if valor is None:
        return "—"
    try:
        bruto = f"{float(valor):,.{casas}f}"
    except (TypeError, ValueError):
        return str(valor)
    # Troca em dois passos, com marcador, para o ponto que vira vírgula
    # não ser revertido pela troca seguinte.
    return bruto.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def ha(valor, casas: int = 2) -> str:
    """'1.234,50 ha'."""
    return f"{num(valor, casas)} ha"
