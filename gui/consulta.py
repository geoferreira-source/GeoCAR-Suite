# -*- coding: utf-8 -*-
"""
consulta.py — aba de consulta de um imóvel.

Fluxo: código → QgsTask (rede) → camada no projeto → sobreposição com
as camadas abertas → tabela + exportação.
"""

import logging

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import config, export, layers, overlay, tasks, texto

log = logging.getLogger("GeoCAR_Suite")


class AbaConsulta(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._tarefa = None
        self._camada = None
        self._dados = None
        self._sobreposicoes = []
        self._montar()

    # ── interface ────────────────────────────────────────
    def _montar(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # Entrada
        gb = QGroupBox("Imóvel")
        gv = QVBoxLayout(gb)
        h = QHBoxLayout()
        self.campo = QLineEdit()
        self.campo.setPlaceholderText("UF-0000000-IDENTIFICADOR")
        self.campo.setClearButtonEnabled(True)
        self.campo.returnPressed.connect(self.consultar)
        self.btn = QPushButton("Consultar")
        self.btn.setDefault(True)
        self.btn.clicked.connect(self.consultar)
        # Só aparece quando o resultado veio do cache: sem resposta
        # antiga na tela, "reconsultar" não quer dizer nada. É a saída
        # para quem precisa do dado do dia sem limpar o cache inteiro.
        self.btn_atualizar = QPushButton("Reconsultar")
        self.btn_atualizar.setToolTip(
            "Descarta a cópia guardada deste imóvel e busca de novo na fonte."
        )
        self.btn_atualizar.setVisible(False)
        self.btn_atualizar.clicked.connect(self._reconsultar)
        h.addWidget(self.campo, 1)
        h.addWidget(self.btn)
        h.addWidget(self.btn_atualizar)
        gv.addLayout(h)

        self.chk_sobrep = QCheckBox("Cruzar com as camadas abertas no projeto")
        self.chk_sobrep.setChecked(config.get("calcular_sobreposicao"))
        self.chk_sobrep.setToolTip(
            "A sobreposição usa as camadas vetoriais já carregadas no QGIS."
        )
        gv.addWidget(self.chk_sobrep)
        v.addWidget(gb)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        v.addWidget(self.barra)

        self.status = QLabel("Informe um código do CAR.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        # Resumo
        self.gb_resumo = QGroupBox("Resultado")
        rv = QVBoxLayout(self.gb_resumo)
        self.lbl_resumo = QLabel("—")
        self.lbl_resumo.setWordWrap(True)
        self.lbl_resumo.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        rv.addWidget(self.lbl_resumo)

        self.tabela = QTableWidget(0, 4)
        self.tabela.setHorizontalHeaderLabels(
            ["Camada", "Área (ha)", "% do imóvel", "Feições"]
        )
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.verticalHeader().setVisible(False)
        cab = self.tabela.horizontalHeader()
        cab.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for c in (1, 2, 3):
            cab.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        rv.addWidget(self.tabela, 1)

        hb = QHBoxLayout()
        self.btn_exp_geo = QPushButton("Exportar imóvel…")
        self.btn_exp_geo.setEnabled(False)
        self.btn_exp_geo.clicked.connect(self._exportar_imovel)
        self.btn_exp_tab = QPushButton("Exportar sobreposições…")
        self.btn_exp_tab.setEnabled(False)
        self.btn_exp_tab.clicked.connect(self._exportar_sobreposicoes)
        hb.addWidget(self.btn_exp_geo)
        hb.addWidget(self.btn_exp_tab)
        hb.addStretch()
        rv.addLayout(hb)

        v.addWidget(self.gb_resumo, 1)

    # ── consulta ─────────────────────────────────────────
    def consultar(self):
        codigo = self.campo.text().strip()
        if not codigo:
            self.status.setText("Informe um código do CAR.")
            return

        from ..core import carcode
        ok, motivo = carcode.validar(codigo)
        if not ok:
            self.status.setText(f"✗ {motivo}")
            return

        self._limpar()
        self.btn.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)   # indeterminada enquanto está na rede
        self.status.setText(f"Consultando {carcode.normalizar(codigo)}…")

        self._tarefa = tasks.ConsultaTask(
            codigo, timeout=config.get("timeout"),
            com_sobreposicao=self.chk_sobrep.isChecked(),
        )
        self._tarefa.taskCompleted.connect(self._ok)
        self._tarefa.taskTerminated.connect(self._falhou)
        QgsApplication.taskManager().addTask(self._tarefa)

    def _ok(self):
        tarefa, self._tarefa = self._tarefa, None
        self.btn.setEnabled(True)
        self.barra.setRange(0, 100)
        self.barra.setValue(100)
        self.barra.setVisible(False)

        dados = tarefa.dados or {}
        self._dados = dados

        camada = layers.criar_camada_imovel(dados)
        if camada is None:
            self.status.setText(
                "✗ A consulta respondeu, mas sem geometria utilizável "
                "para este imóvel."
            )
            return

        self._camada = camada
        if config.get("carregar_no_projeto"):
            layers.adicionar_ao_projeto(
                camada, self.iface,
                dar_zoom=config.get("zoom_apos_consulta"),
                com_simbologia=config.get("simbologia_automatica"),
            )

        area = overlay.area_do_imovel_ha(camada)
        partes = [
            f"<b>{dados.get('codigo_car','—')}</b>",
            f"Município: {dados.get('municipio') or '—'}",
            f"Área: {texto.ha(area)}",
            f"Situação: {dados.get('situacao') or '—'}",
        ]
        # "condicao" é texto livre do SICAR ("Em análise", "Analisado
        # sem pendências"…) e diz mais que a sigla de status.
        if dados.get("condicao"):
            partes.append(f"Condição: {dados['condicao']}")
        if dados.get("responsavel"):
            partes.append(f"Responsável: {dados['responsavel']}")

        # Proveniência explícita. Dado do cache é legítimo — o CAR muda
        # em meses — mas o usuário tem de poder saber, porque é ele que
        # decide se aquela idade serve para o laudo que está escrevendo.
        fonte = dados.get("fonte", "—")
        idade = dados.get("_cache_idade_s")
        if idade is not None:
            from ..core import cache
            fonte += f" · do cache, consultado {cache.descrever_idade(idade)}"
        partes.append(f"<i>Fonte: {fonte}</i>")
        self.lbl_resumo.setText("<br>".join(partes))

        self.btn_exp_geo.setEnabled(True)
        self.btn_atualizar.setVisible(idade is not None)
        self.status.setText(
            "✓ Imóvel localizado (do cache)." if idade is not None
            else "✓ Imóvel localizado."
        )

        if self.chk_sobrep.isChecked():
            self._calcular_sobreposicao()

    def _calcular_sobreposicao(self):
        self.status.setText("Cruzando com as camadas do projeto…")
        QgsApplication.processEvents()
        try:
            resultados = tasks.calcular_sobreposicao(
                self._camada, ignorar=config.camadas_ignoradas()
            )
        except Exception as e:
            log.exception("Falha na sobreposição")
            self.status.setText(f"✗ Falha na sobreposição: {e}")
            return

        self._sobreposicoes = resultados
        self.tabela.setRowCount(0)
        for r in resultados:
            i = self.tabela.rowCount()
            self.tabela.insertRow(i)
            item = QTableWidgetItem(r["camada"])
            if r["nomes"]:
                item.setToolTip("\n".join(r["nomes"]))
            self.tabela.setItem(i, 0, item)
            self.tabela.setItem(i, 1, QTableWidgetItem(texto.num(r["area_ha"])))
            self.tabela.setItem(i, 2, QTableWidgetItem(f"{r['percentual']:.2f}%"))
            self.tabela.setItem(i, 3, QTableWidgetItem(str(r["feicoes"])))

        self.btn_exp_tab.setEnabled(bool(resultados))
        if resultados:
            self.status.setText(f"✓ {len(resultados)} camada(s) com sobreposição.")
        else:
            self.status.setText(
                "✓ Imóvel localizado. Nenhuma sobreposição com as camadas "
                "abertas (ou não há camadas vetoriais no projeto)."
            )

    def _falhou(self):
        tarefa, self._tarefa = self._tarefa, None
        self.btn.setEnabled(True)
        self.barra.setVisible(False)
        motivo = (tarefa.erro if tarefa else None) or "Consulta cancelada."
        self.status.setText(f"✗ {motivo}")

    def _reconsultar(self):
        """Apaga só este imóvel do cache e consulta de novo."""
        from ..core import cache
        cache.remover(self.campo.text().strip())
        self.consultar()

    def _limpar(self):
        self._camada = None
        self._dados = None
        self._sobreposicoes = []
        self.lbl_resumo.setText("—")
        self.tabela.setRowCount(0)
        self.btn_exp_geo.setEnabled(False)
        self.btn_exp_tab.setEnabled(False)
        self.btn_atualizar.setVisible(False)

    # ── exportação ───────────────────────────────────────
    def _pasta_inicial(self, nome):
        base = config.get("pasta_saida") or ""
        return f"{base}/{nome}" if base else nome

    def _exportar_imovel(self):
        if self._camada is None:
            return
        codigo = (self._dados or {}).get("codigo_car", "imovel")
        caminho, filtro = QFileDialog.getSaveFileName(
            self, "Exportar imóvel", self._pasta_inicial(f"{codigo}.gpkg"),
            "GeoPackage (*.gpkg);;Shapefile (*.shp)",
        )
        if not caminho:
            return
        formato = "shp" if "shp" in filtro.lower() else "gpkg"
        ok, res = export.exportar_camada(self._camada, caminho, formato)
        self._avisar(ok, res)

    def _exportar_sobreposicoes(self):
        if not self._sobreposicoes:
            return
        codigo = (self._dados or {}).get("codigo_car", "imovel")
        caminho, filtro = QFileDialog.getSaveFileName(
            self, "Exportar sobreposições",
            self._pasta_inicial(f"{codigo}_sobreposicoes.xlsx"),
            "Planilha (*.xlsx);;GeoPackage (*.gpkg)",
        )
        if not caminho:
            return
        formato = "gpkg" if "gpkg" in filtro.lower() else "xlsx"
        linhas = [dict(r, codigo_car=codigo) for r in self._sobreposicoes]
        ok, res = export.exportar_tabela(
            linhas,
            ["codigo_car", "camada", "area_ha", "percentual", "feicoes", "nomes"],
            caminho, formato, "sobreposicoes",
        )
        self._avisar(ok, res)

    def _avisar(self, ok, res):
        if ok:
            self.status.setText(f"✓ Arquivo gravado: {res}")
        else:
            QMessageBox.warning(self, "GeoCAR Suite", f"Não consegui gravar:\n{res}")
