# -*- coding: utf-8 -*-
"""
busca.py — aba "Buscar": encontrar imóveis sem ter o código.

Existe porque o caso real mais comum não é "tenho o código do CAR na
mão", e sim "sei de quem é" ou "sei onde fica". Os dois caminhos já
existiam no núcleo e não tinham nenhuma porta na interface.

Alcance dos dois modos é bem diferente, e a interface diz isso na
cara em vez de deixar o usuário descobrir pelo erro:

  • CPF/CNPJ — só Pará, e só com token da SEMAS-PA. O serviço nacional
    aberto não publica titular (dado pessoal), então não há como fazer
    isso nas outras UFs.
  • Município — qualquer UF, sem login. É a saída quando não se tem
    nem o código nem o token.

Achado o imóvel, "Consultar selecionado" joga o código na aba
Consulta e dispara a consulta de verdade — esta aba só localiza.
"""

import logging

from qgis.core import QgsApplication
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QFormLayout, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import carcode, config, export, tasks, texto
from ..core.sources import semas_pa

log = logging.getLogger("GeoCAR_Suite")

COL_CODIGO, COL_MUNICIPIO, COL_AREA, COL_SITUACAO = 0, 1, 2, 3


class AbaBusca(QWidget):
    """Emite `consultar_codigo` quando o usuário escolhe um imóvel."""

    consultar_codigo = pyqtSignal(str)

    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._tarefa = None
        self._resultados = []
        self._montar()
        self._ao_trocar_modo()

    # ── interface ────────────────────────────────────────
    def _montar(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        gb = QGroupBox("Como procurar")
        gv = QVBoxLayout(gb)

        self.rb_municipio = QRadioButton("Por município — qualquer UF, sem login")
        self.rb_cpf = QRadioButton("Por CPF/CNPJ do titular — só Pará, exige token")
        self.rb_municipio.setChecked(True)
        self.rb_municipio.toggled.connect(self._ao_trocar_modo)
        gv.addWidget(self.rb_municipio)
        gv.addWidget(self.rb_cpf)

        # ── município ──
        self.form_municipio = QWidget()
        fm = QFormLayout(self.form_municipio)
        fm.setContentsMargins(20, 4, 0, 0)
        self.combo_uf = QComboBox()
        self.combo_uf.addItems(sorted(carcode.UFS))
        self.combo_uf.setCurrentText("PA")
        self.combo_uf.setMaximumWidth(80)
        self.campo_municipio = QLineEdit()
        self.campo_municipio.setPlaceholderText("Nome do município (parcial serve)")
        self.campo_municipio.returnPressed.connect(self.buscar)
        fm.addRow("UF:", self.combo_uf)
        fm.addRow("Município:", self.campo_municipio)
        gv.addWidget(self.form_municipio)

        # ── CPF/CNPJ ──
        self.form_cpf = QWidget()
        fc = QFormLayout(self.form_cpf)
        fc.setContentsMargins(20, 4, 0, 0)
        self.campo_cpf = QLineEdit()
        self.campo_cpf.setPlaceholderText("CPF (11 dígitos) ou CNPJ (14)")
        self.campo_cpf.returnPressed.connect(self.buscar)
        fc.addRow("CPF/CNPJ:", self.campo_cpf)
        self.lbl_token = QLabel()
        self.lbl_token.setWordWrap(True)
        fc.addRow("", self.lbl_token)
        gv.addWidget(self.form_cpf)

        v.addWidget(gb)

        h = QHBoxLayout()
        self.btn = QPushButton("Buscar")
        self.btn.setDefault(True)
        self.btn.clicked.connect(self.buscar)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setEnabled(False)
        self.btn_cancelar.clicked.connect(self._cancelar)
        h.addWidget(self.btn)
        h.addWidget(self.btn_cancelar)
        h.addStretch()
        v.addLayout(h)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        v.addWidget(self.barra)

        self.status = QLabel("Escolha como procurar e informe os dados.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.tabela = QTableWidget(0, 4)
        self.tabela.setHorizontalHeaderLabels(
            ["Código do CAR", "Município", "Área (ha)", "Situação"]
        )
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabela.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.setSortingEnabled(True)
        cab = self.tabela.horizontalHeader()
        cab.setSectionResizeMode(COL_CODIGO, QHeaderView.ResizeMode.Stretch)
        for c in (COL_MUNICIPIO, COL_AREA, COL_SITUACAO):
            cab.setSectionResizeMode(c, QHeaderView.ResizeMode.ResizeToContents)
        # Clique duplo é o gesto natural para "quero esse".
        self.tabela.itemDoubleClicked.connect(lambda _: self._consultar_selecionado())
        self.tabela.itemSelectionChanged.connect(self._ao_selecionar)
        v.addWidget(self.tabela, 1)

        hb = QHBoxLayout()
        self.btn_consultar = QPushButton("Consultar selecionado")
        self.btn_consultar.setEnabled(False)
        self.btn_consultar.clicked.connect(self._consultar_selecionado)
        self.btn_exportar = QPushButton("Exportar lista…")
        self.btn_exportar.setEnabled(False)
        self.btn_exportar.clicked.connect(self._exportar)
        hb.addWidget(self.btn_consultar)
        hb.addWidget(self.btn_exportar)
        hb.addStretch()
        v.addLayout(hb)

    def _ao_trocar_modo(self):
        por_municipio = self.rb_municipio.isChecked()
        self.form_municipio.setVisible(por_municipio)
        self.form_cpf.setVisible(not por_municipio)
        if not por_municipio:
            self._atualizar_aviso_token()
        # A mensagem anterior fala do outro modo — mantê-la na tela
        # faz parecer que o modo recém-escolhido já falhou.
        self.status.setText("Informe os dados e clique em Buscar.")

    def _atualizar_aviso_token(self):
        s = semas_pa.status_token()
        if not s["tem_token"]:
            self.lbl_token.setText(
                "⚠ Sem token da SEMAS-PA — esta busca não vai funcionar. "
                "Cole um token na aba Configurações."
            )
        elif s["provavelmente_expirado"]:
            self.lbl_token.setText(
                "⚠ O token salvo provavelmente expirou. Se a busca não "
                "retornar nada, gere um novo no portal."
            )
        else:
            self.lbl_token.setText("Token da SEMAS-PA disponível.")

    # ── busca ────────────────────────────────────────────
    def buscar(self):
        por_municipio = self.rb_municipio.isChecked()

        if por_municipio:
            municipio = self.campo_municipio.text().strip()
            if not municipio:
                self.status.setText("Informe o município.")
                return
            tarefa = tasks.BuscaTask(
                "municipio", timeout=config.get("timeout"),
                uf=self.combo_uf.currentText(), municipio=municipio,
            )
            alvo = f"{municipio} ({self.combo_uf.currentText()})"
        else:
            documento = self.campo_cpf.text().strip()
            if not documento:
                self.status.setText("Informe o CPF ou CNPJ.")
                return
            tarefa = tasks.BuscaTask(
                "cpf", timeout=config.get("timeout"), cpf_cnpj=documento,
            )
            alvo = "o CPF/CNPJ informado"

        self.tabela.setRowCount(0)
        self._resultados = []
        self.btn.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.btn_consultar.setEnabled(False)
        self.btn_exportar.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)
        self.status.setText(f"Procurando imóveis de {alvo}…")

        self._tarefa = tarefa
        tarefa.taskCompleted.connect(self._ok)
        tarefa.taskTerminated.connect(self._falhou)
        QgsApplication.taskManager().addTask(tarefa)

    def _ok(self):
        tarefa, self._tarefa = self._tarefa, None
        self._encerrar_barra()
        resultados = tarefa.resultados if tarefa else []
        truncado = bool(getattr(tarefa, "truncado", False))
        self._resultados = resultados

        self.tabela.setSortingEnabled(False)
        self.tabela.setRowCount(0)
        for r in resultados:
            i = self.tabela.rowCount()
            self.tabela.insertRow(i)
            self.tabela.setItem(i, COL_CODIGO, QTableWidgetItem(r.get("codigo_car", "")))
            self.tabela.setItem(i, COL_MUNICIPIO, QTableWidgetItem(r.get("municipio", "")))

            area = r.get("area_ha")
            item_area = QTableWidgetItem()
            if area not in (None, ""):
                try:
                    valor = float(area)
                    item_area.setData(Qt.ItemDataRole.DisplayRole, valor)
                    item_area.setText(texto.num(valor))
                except (TypeError, ValueError):
                    item_area.setText(str(area))
            else:
                item_area.setText("—")
            item_area.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.tabela.setItem(i, COL_AREA, item_area)

            situacao = r.get("situacao") or r.get("condicao") or "—"
            self.tabela.setItem(i, COL_SITUACAO, QTableWidgetItem(str(situacao)))
        self.tabela.setSortingEnabled(True)

        self.btn_exportar.setEnabled(bool(resultados))
        if resultados:
            self.tabela.selectRow(0)
            if truncado:
                aviso = (f"⚠ Mostrando os primeiros {len(resultados)} — o "
                         "município tem mais. Refine o nome ou use o código.")
            else:
                aviso = f"✓ {len(resultados)} imóvel(is)."
            self.status.setText(
                aviso + " Escolha um e clique em “Consultar selecionado” "
                "(ou dê um clique duplo)."
            )
        else:
            self.status.setText("Nenhum imóvel encontrado.")

    def _falhou(self):
        tarefa, self._tarefa = self._tarefa, None
        self._encerrar_barra()
        motivo = (tarefa.erro if tarefa else None) or "Busca cancelada."
        self.status.setText(f"✗ {motivo}")

    def _encerrar_barra(self):
        self.btn.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setRange(0, 100)
        self.barra.setVisible(False)

    def _cancelar(self):
        if self._tarefa:
            self._tarefa.cancel()
            self.status.setText("Cancelando…")

    # ── resultado ────────────────────────────────────────
    def _ao_selecionar(self):
        self.btn_consultar.setEnabled(bool(self.tabela.selectionModel().selectedRows()))

    def _codigo_selecionado(self) -> str:
        linhas = self.tabela.selectionModel().selectedRows()
        if not linhas:
            return ""
        item = self.tabela.item(linhas[0].row(), COL_CODIGO)
        return item.text().strip() if item else ""

    def _consultar_selecionado(self):
        codigo = self._codigo_selecionado()
        if codigo:
            self.consultar_codigo.emit(codigo)

    def _exportar(self):
        if not self._resultados:
            return
        from qgis.PyQt.QtWidgets import QFileDialog
        base = config.get("pasta_saida") or ""
        sugestao = f"{base}/busca_car.xlsx" if base else "busca_car.xlsx"
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar lista", sugestao,
            "Planilha (*.xlsx);;GeoPackage (*.gpkg)",
        )
        if not caminho:
            return
        colunas = ["codigo_car", "municipio", "area_ha", "situacao"]
        linhas = [{c: r.get(c, "") for c in colunas} for r in self._resultados]
        ok, res = export.exportar_tabela(
            linhas, colunas, caminho,
            "gpkg" if caminho.lower().endswith(".gpkg") else "xlsx", "busca",
        )
        if ok:
            self.status.setText(f"✓ Arquivo gravado: {res}")
        else:
            QMessageBox.warning(self, "GeoCAR Suite", f"Não consegui gravar:\n{res}")
