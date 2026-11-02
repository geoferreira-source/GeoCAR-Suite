# -*- coding: utf-8 -*-
"""
router.py — escolhe a fonte de dados a partir da UF do código.

Regra, para consulta por CÓDIGO:
  • Qualquer UF → WFS nacional (sem token, cobre o Brasil inteiro).
  • PA → tenta a SEMAS-PA primeiro SE houver token, porque ela traz o
    nome do responsável; se falhar por qualquer motivo (sem token,
    token expirado, portal fora do ar), cai para o WFS nacional em vez
    de devolver erro. Degradar é melhor que travar: o usuário recebe o
    imóvel, só sem o campo extra.

Para busca por CPF/CNPJ, só a SEMAS-PA atende, e só no Pará — ver a
nota em buscar_por_cpf().
"""

import logging

from .. import carcode
from . import semas_pa, wfs

log = logging.getLogger("GeoCAR_Suite")

UFS_COM_PORTAL = {"PA"}


def buscar_imovel(codigo_car: str, timeout: int = 30):
    """(True, dados) ou (False, mensagem). Ver wfs.buscar_imovel para
    o formato de `dados`."""
    ok, motivo = carcode.validar(codigo_car)
    if not ok:
        return False, motivo

    uf = carcode.uf_de(codigo_car)

    if uf in UFS_COM_PORTAL and semas_pa.status_token()["tem_token"]:
        ok, dados = semas_pa.buscar_imovel(codigo_car, timeout)
        if ok and (dados.get("geojson") or {}).get("geometry"):
            return True, dados
        # Sem geometria utilizável, a SEMAS-PA não resolve a consulta.
        log.info("SEMAS-PA não resolveu %s (%s); usando o WFS nacional.",
                 codigo_car, dados if not ok else "sem geometria")
        ok_wfs, dados_wfs = wfs.buscar_imovel(codigo_car, timeout)
        if ok_wfs and ok:
            # Aproveita o responsável, que só o portal publica.
            if dados.get("responsavel"):
                dados_wfs["responsavel"] = dados["responsavel"]
                dados_wfs["fonte"] = "SICAR nacional (WFS) + SEMAS-PA"
        return ok_wfs, dados_wfs

    return wfs.buscar_imovel(codigo_car, timeout)


def buscar_por_cpf(cpf_cnpj: str, uf: str = "PA", timeout: int = 30):
    """
    Busca imóveis por titular.

    Só funciona no Pará, via portal autenticado. Não é limitação do
    plugin: o WFS nacional publica apenas atributos do imóvel
    (cod_imovel, num_area, municipio, ind_status) e nenhum campo de
    titular, porque dado pessoal não vai para o serviço aberto. A
    plataforma federal (Meu Imóvel Rural) é autosserviço — o titular
    entra com a conta gov.br e vê os imóveis dele —, não uma API de
    consulta de terceiros.
    """
    uf = (uf or "PA").strip().upper()
    if uf not in UFS_COM_PORTAL:
        return False, (
            f"Busca por CPF/CNPJ não existe para {uf}.\n\n"
            "Só o portal estadual autenticado do Pará publica o vínculo "
            "entre titular e imóvel. O serviço nacional aberto do SICAR "
            "traz apenas dados do imóvel, sem CPF ou nome de "
            "proprietário.\n\n"
            "Alternativa em qualquer estado: listar os imóveis de um "
            "município e filtrar a partir dali."
        )
    return semas_pa.buscar_por_cpf(cpf_cnpj, timeout)
