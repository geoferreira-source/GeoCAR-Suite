# -*- coding: utf-8 -*-
"""
tasks.py — trabalho pesado fora da thread da interface.

Usa QgsTask (gerenciado pelo QgsApplication.taskManager()) em vez de
QThread: é o mecanismo que o próprio QGIS usa, aparece na barra de
progresso dele e pode ser cancelado pelo usuário.

Regra respeitada aqui: a rede e o cálculo rodam em run(); tudo que
toca o projeto (criar camada, adicionar ao mapa) acontece em
finished(), que o Qt chama de volta na thread principal. Criar camada
na thread secundária é fonte de travamento silencioso no QGIS.

Cache
-----
A decisão de usar cache e por quantos dias é lida no __init__, que roda
na thread da interface. De propósito: QSettings fora da thread
principal é terreno escorregadio, e o valor não muda no meio de uma
consulta de qualquer forma.
"""

import logging

from qgis.core import QgsTask

from . import cache, config, overlay
from .sources import router

log = logging.getLogger("GeoCAR_Suite")


def _buscar(codigo, timeout, usar_cache, validade_dias):
    """
    Consulta um imóvel, pelo cache quando possível.

    Devolve (ok, dados_ou_erro). Quando o dado vem do cache, carrega a
    chave "_cache_idade_s" para a interface poder dizer de quando ele é
    — dado antigo apresentado como recente é pior que dado nenhum.
    """
    if usar_cache:
        guardado, idade = cache.obter(codigo, validade_dias)
        if guardado:
            guardado["_cache_idade_s"] = idade
            return True, guardado

    ok, resultado = router.buscar_imovel(codigo, timeout)
    if ok and usar_cache:
        cache.guardar(codigo, resultado)
    return ok, resultado


class ConsultaTask(QgsTask):
    """Consulta um código e, opcionalmente, calcula a sobreposição."""

    def __init__(self, codigo_car, timeout=30, com_sobreposicao=True,
                 ignorar_camadas=()):
        super().__init__(f"GeoCAR: consultando {codigo_car}", QgsTask.CanCancel)
        self.codigo_car = codigo_car
        self.timeout = timeout
        self.com_sobreposicao = com_sobreposicao
        self.ignorar_camadas = list(ignorar_camadas)
        self.usar_cache = bool(config.get("usar_cache"))
        self.validade_dias = int(config.get("cache_validade_dias"))

        self.dados = None
        self.erro = None
        # A sobreposição precisa da camada pronta, que só pode nascer
        # na thread principal — então guardamos os dados aqui e o
        # cálculo acontece no callback do diálogo.
        self.sobreposicoes = None

    def run(self):
        try:
            self.setProgress(10)
            ok, resultado = _buscar(
                self.codigo_car, self.timeout,
                self.usar_cache, self.validade_dias,
            )
            if self.isCanceled():
                return False
            if not ok:
                self.erro = resultado
                return False
            self.dados = resultado
            self.setProgress(100)
            return True
        except Exception as e:
            log.exception("Falha na consulta")
            self.erro = str(e)
            return False

    def cancel(self):
        log.info("Consulta cancelada: %s", self.codigo_car)
        super().cancel()


class LoteTask(QgsTask):
    """
    Processa uma lista de códigos.

    Só faz rede aqui; a criação das camadas e a sobreposição ficam
    para a thread principal, no callback. Para listas grandes isso
    mantém o QGIS responsivo e permite cancelar no meio.
    """

    def __init__(self, codigos, timeout=30):
        super().__init__(f"GeoCAR: lote ({len(codigos)} imóveis)",
                         QgsTask.CanCancel)
        self.codigos = list(codigos)
        self.timeout = timeout
        self.usar_cache = bool(config.get("usar_cache"))
        self.validade_dias = int(config.get("cache_validade_dias"))
        self.sucessos = []   # list[dict]
        self.falhas = []     # list[(codigo, motivo)]
        # Quantos vieram do cache: é a informação que explica por que o
        # mesmo lote levou 4 minutos ontem e 6 segundos hoje.
        self.do_cache = 0

    def run(self):
        total = len(self.codigos) or 1
        for i, codigo in enumerate(self.codigos):
            if self.isCanceled():
                log.info("Lote cancelado em %s/%s", i, total)
                return False
            try:
                ok, resultado = _buscar(
                    codigo, self.timeout,
                    self.usar_cache, self.validade_dias,
                )
                if ok:
                    if resultado.get("_cache_idade_s") is not None:
                        self.do_cache += 1
                    self.sucessos.append(resultado)
                else:
                    self.falhas.append((codigo, str(resultado)))
            except Exception as e:
                self.falhas.append((codigo, str(e)))
            self.setProgress((i + 1) / total * 100)
        return True


class BuscaTask(QgsTask):
    """
    Procura imóveis quando NÃO se tem o código em mãos.

    Dois modos, com alcances bem diferentes:
      • "cpf"       — por titular, só no Pará e só com token da
                      SEMAS-PA (o serviço aberto não publica titular);
      • "municipio" — pelo WFS nacional, funciona em qualquer UF.

    Devolve a lista em `resultados`; quem consulta de fato é a aba de
    Consulta, a partir do código escolhido.
    """

    def __init__(self, modo, timeout=30, cpf_cnpj="", uf="", municipio="",
                 limite=1000):
        rotulo = (f"GeoCAR: buscando por CPF/CNPJ" if modo == "cpf"
                  else f"GeoCAR: imóveis em {municipio} ({uf})")
        super().__init__(rotulo, QgsTask.CanCancel)
        self.modo = modo
        self.timeout = timeout
        self.cpf_cnpj = cpf_cnpj
        self.uf = uf
        self.municipio = municipio
        self.limite = limite

        self.resultados = []
        self.erro = None
        # O WFS corta no limite pedido. Sem avisar, "1000 imóveis"
        # parece o total do município quando na verdade é o teto.
        self.truncado = False

    def run(self):
        try:
            self.setProgress(10)
            if self.modo == "cpf":
                ok, res = router.buscar_por_cpf(
                    self.cpf_cnpj, uf="PA", timeout=self.timeout
                )
            else:
                from .sources import wfs
                ok, res = wfs.listar_por_municipio(
                    self.uf, self.municipio, timeout=self.timeout,
                    limite=self.limite,
                )
            if self.isCanceled():
                return False
            if not ok:
                self.erro = res
                return False
            self.resultados = res
            self.truncado = (self.modo == "municipio"
                             and len(res) >= self.limite)
            self.setProgress(100)
            return True
        except Exception as e:
            log.exception("Falha na busca")
            self.erro = str(e)
            return False


class VizinhancaTask(QgsTask):
    """
    Duas idas ao SICAR, num clique só: os imóveis já cadastrados dentro
    do retângulo do perímetro novo e uma amostra do município para
    deduzir o módulo fiscal.

    Vai para uma QgsTask porque juntas levam 10–20 s — na thread da
    interface isso é a janela congelada, e o usuário conclui que travou.
    Aqui só se faz rede: montar geometria e medir é com o chamador, na
    thread principal.
    """

    def __init__(self, uf, xmin, ymin, xmax, ymax, municipio="",
                 excluir=None, timeout=40, limite=200):
        super().__init__(f"GeoCAR: vizinhança em {uf}", QgsTask.CanCancel)
        self.uf = uf
        self.caixa = (xmin, ymin, xmax, ymax)
        self.municipio = municipio
        self.excluir = excluir
        self.timeout = timeout
        self.limite = limite

        self.vizinhos = []        # feições GeoJSON cruas
        self.modulo_fiscal = None
        self.erro = None
        self.truncado = False
        self.aviso_modulo = ""

    def run(self):
        from . import cadastro
        from .sources import wfs
        try:
            self.setProgress(10)
            ok, res = wfs.listar_por_bbox(
                self.uf, *self.caixa, timeout=self.timeout,
                limite=self.limite, excluir=self.excluir,
            )
            if self.isCanceled():
                return False
            if not ok:
                self.erro = res
                return False
            self.vizinhos = res
            self.truncado = len(res) >= self.limite
            self.setProgress(60)

            if self.municipio:
                ok, res = cadastro.modulo_fiscal_do_municipio(
                    self.uf, self.municipio, timeout=self.timeout)
                if ok:
                    self.modulo_fiscal = res
                else:
                    self.aviso_modulo = str(res)
            self.setProgress(100)
            return True
        except Exception as e:
            log.exception("Falha ao conferir a vizinhança")
            self.erro = str(e)
            return False


def calcular_sobreposicao(camada_imovel, ignorar=()):
    """
    Sobreposição na thread principal.

    Fica fora de QgsTask de propósito: ler feições de camadas do
    projeto em outra thread não é seguro no QGIS. Na prática é rápido,
    porque o filtro por retângulo envolvente usa o índice espacial das
    camadas abertas.
    """
    return overlay.calcular(camada_imovel, ignorar=ignorar)
