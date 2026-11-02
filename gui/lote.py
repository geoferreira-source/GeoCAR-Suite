# -*- coding: utf-8 -*-
"""
lote.py — aba de processamento em lote a partir de planilha.

A planilha é lida por OGR (xlsx, csv, ods), a rede roda numa QgsTask
cancelável, e as camadas são criadas no callback, na thread principal.
"""

import logging

from qgis.core import QgsApplication, QgsProject
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from ..core import config, export, layers, overlay, tasks, texto

log = logging.getLogger("GeoCAR_Suite")


class AbaLote(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._tarefa = None
        self._codigos = []
        self._resultados = []   # linhas para exportar
        self._montar()

    def _montar(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        gb = QGroupBox("Planilha de códigos")
        gv = QVBoxLayout(gb)

        h = QHBoxLayout()
        self.campo = QLineEdit()
        self.campo.setPlaceholderText("Planilha .xlsx, .csv ou .ods")
        btn_abrir = QPushButton("Procurar…")
        btn_abrir.clicked.connect(self._escolher)
        h.addWidget(self.campo, 1)
        h.addWidget(btn_abrir)
        gv.addLayout(h)

        dica = QLabel(
            "A coluna dos códigos é detectada pelo nome (CAR, código…); "
            "se não houver, a primeira coluna é usada."
        )
        dica.setWordWrap(True)
        gv.addWidget(dica)

        self.chk_agrupar = QCheckBox("Juntar tudo numa camada só")
        self.chk_agrupar.setChecked(True)
        self.chk_agrupar.setToolTip(
            "Desmarcado, cada imóvel entra como uma camada separada — "
            "evite para listas grandes."
        )
        gv.addWidget(self.chk_agrupar)

        self.chk_sobrep = QCheckBox("Cruzar cada imóvel com as camadas do projeto")
        self.chk_sobrep.setChecked(False)
        self.chk_sobrep.setToolTip(
            "Deixa o lote bem mais lento; útil quando o cruzamento é o "
            "objetivo do processamento."
        )
        gv.addWidget(self.chk_sobrep)
        v.addWidget(gb)

        hb = QHBoxLayout()
        self.btn = QPushButton("Processar")
        self.btn.clicked.connect(self.processar)
        self.btn_cancelar = QPushButton("Cancelar")
        self.btn_cancelar.setEnabled(False)
        self.btn_cancelar.clicked.connect(self._cancelar)
        hb.addWidget(self.btn)
        hb.addWidget(self.btn_cancelar)
        hb.addStretch()
        v.addLayout(hb)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        v.addWidget(self.barra)

        self.status = QLabel("Escolha uma planilha com os códigos.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.tabela = QTableWidget(0, 4)
        self.tabela.setHorizontalHeaderLabels(
            ["Código", "Município", "Área (ha)", "Resultado"]
        )
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabela.setAlternatingRowColors(True)
        self.tabela.verticalHeader().setVisible(False)
        cab = self.tabela.horizontalHeader()
        cab.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        cab.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tabela, 1)

        self.btn_exp = QPushButton("Exportar resultado…")
        self.btn_exp.setEnabled(False)
        self.btn_exp.clicked.connect(self._exportar)
        v.addWidget(self.btn_exp)

    def _escolher(self):
        caminho, _ = QFileDialog.getOpenFileName(
            self, "Planilha com os códigos", config.get("pasta_saida") or "",
            "Planilhas (*.xlsx *.csv *.ods);;Todos (*)",
        )
        if caminho:
            self.campo.setText(caminho)

    def processar(self):
        caminho = self.campo.text().strip()
        if not caminho:
            self.status.setText("Escolha a planilha primeiro.")
            return

        ok, res = export.ler_codigos(caminho)
        if not ok:
            self.status.setText(f"✗ {res}")
            return

        self._codigos = res
        self._resultados = []
        self.tabela.setRowCount(0)
        self.btn_exp.setEnabled(False)
        self.btn.setEnabled(False)
        self.btn_cancelar.setEnabled(True)
        self.barra.setVisible(True)
        self.barra.setRange(0, 100)
        self.barra.setValue(0)
        self.status.setText(f"Processando {len(res)} imóvel(is)…")

        self._tarefa = tasks.LoteTask(res, timeout=config.get("timeout"))
        self._tarefa.progressChanged.connect(
            lambda p: self.barra.setValue(int(p))
        )
        self._tarefa.taskCompleted.connect(self._concluido)
        self._tarefa.taskTerminated.connect(self._concluido)
        QgsApplication.taskManager().addTask(self._tarefa)

    def _cancelar(self):
        if self._tarefa:
            self._tarefa.cancel()
            self.status.setText("Cancelando…")

    def _concluido(self):
        tarefa, self._tarefa = self._tarefa, None
        self.btn.setEnabled(True)
        self.btn_cancelar.setEnabled(False)
        self.barra.setVisible(False)
        if tarefa is None:
            return

        sucessos = tarefa.sucessos
        falhas = tarefa.falhas

        camadas = []
        for dados in sucessos:
            camada = layers.criar_camada_imovel(dados)
            if camada is None:
                falhas.append((dados.get("codigo_car", "?"), "sem geometria"))
                continue
            camadas.append((dados, camada))

        estilizar = config.get("simbologia_automatica")
        if self.chk_agrupar.isChecked() and camadas:
            agrupada = self._agrupar([c for _, c in camadas])
            if agrupada is not None:
                if estilizar:
                    layers.aplicar_simbologia(agrupada)
                QgsProject.instance().addMapLayer(agrupada)
        else:
            for _, camada in camadas:
                if estilizar:
                    layers.aplicar_simbologia(camada)
                QgsProject.instance().addMapLayer(camada)

        ignorar = config.camadas_ignoradas()
        for dados, camada in camadas:
            area = overlay.area_do_imovel_ha(camada)
            resumo = "OK"
            if self.chk_sobrep.isChecked():
                try:
                    sobr = overlay.calcular(camada, ignorar=ignorar)
                    resumo = (f"{len(sobr)} sobreposição(ões)"
                              if sobr else "sem sobreposição")
                    for s in sobr:
                        self._resultados.append({
                            "codigo_car": dados.get("codigo_car", ""),
                            "municipio": dados.get("municipio", ""),
                            "area_imovel_ha": area,
                            "camada": s["camada"],
                            "area_sobreposta_ha": s["area_ha"],
                            "percentual": s["percentual"],
                            "nomes": s["nomes"],
                        })
                except Exception as e:
                    resumo = f"falha na sobreposição: {e}"
            if not self.chk_sobrep.isChecked():
                self._resultados.append({
                    "codigo_car": dados.get("codigo_car", ""),
                    "municipio": dados.get("municipio", ""),
                    "area_imovel_ha": area,
                    "situacao": dados.get("situacao", ""),
                    "fonte": dados.get("fonte", ""),
                })
            self._linha(dados.get("codigo_car", "?"),
                        dados.get("municipio", ""), texto.num(area),
                        resumo)

        for codigo, motivo in falhas:
            self._linha(codigo, "", "", f"✗ {motivo}")

        self.btn_exp.setEnabled(bool(self._resultados))
        cancelado = "" if tarefa.sucessos or tarefa.falhas else " (cancelado)"
        do_cache = getattr(tarefa, "do_cache", 0)
        vindos = f", {do_cache} do cache" if do_cache else ""
        self.status.setText(
            f"Concluído{cancelado}: {len(camadas)} com geometria, "
            f"{len(falhas)} com falha{vindos}."
        )

    def _agrupar(self, camadas):
        """Junta as camadas de imóvel numa só, preservando atributos."""
        if not camadas:
            return None
        from qgis.core import QgsFeature, QgsVectorLayer

        modelo = camadas[0]
        junta = QgsVectorLayer(
            f"MultiPolygon?crs={modelo.crs().authid()}", "CAR (lote)", "memory"
        )
        prov = junta.dataProvider()
        prov.addAttributes(modelo.fields())
        junta.updateFields()

        novas = []
        for camada in camadas:
            for f in camada.getFeatures():
                nova = QgsFeature(junta.fields())
                nova.setGeometry(f.geometry())
                nova.setAttributes(f.attributes())
                novas.append(nova)
        prov.addFeatures(novas)
        junta.updateExtents()
        return junta

    def _linha(self, *valores):
        i = self.tabela.rowCount()
        self.tabela.insertRow(i)
        for c, v in enumerate(valores):
            self.tabela.setItem(i, c, QTableWidgetItem(str(v)))

    def _exportar(self):
        if not self._resultados:
            return
        caminho, _ = QFileDialog.getSaveFileName(
            self, "Exportar resultado do lote",
            (config.get("pasta_saida") or "") + "/lote_car.xlsx",
            "Planilha (*.xlsx);;GeoPackage (*.gpkg)",
        )
        if not caminho:
            return
        colunas = list(self._resultados[0].keys())
        ok, res = export.exportar_tabela(
            self._resultados, colunas, caminho,
            "gpkg" if caminho.lower().endswith(".gpkg") else "xlsx", "lote",
        )
        if ok:
            self.status.setText(f"✓ Arquivo gravado: {res}")
        else:
            QMessageBox.warning(self, "GeoCAR Suite", f"Não consegui gravar:\n{res}")
