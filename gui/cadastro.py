# -*- coding: utf-8 -*-
"""
cadastro.py — aba de apoio ao cadastro de um imóvel novo.

A aba é deliberadamente honesta sobre o limite: ela não envia nada. O
SICAR não tem API de escrita, então o cadastro continua sendo feito no
módulo oficial. O que esta aba faz é a parte que consome tempo e volta
como pendência — medir, validar, cruzar com quem já está cadastrado e
entregar os arquivos prontos.

Fluxo: escolher as camadas do projeto por tema → Validar → Conferir
vizinhança → Exportar pacote / Memorial.
"""

import logging

from qgis.core import (
    QgsApplication, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsProject,
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QProgressBar, QPushButton, QScrollArea, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from ..core import cadastro, carcode, config, export, layers, tasks, texto

log = logging.getLogger("GeoCAR_Suite")

SEM_CAMADA = "— não usar —"
SEM_UF = "— escolha —"

# Cor por nível. O fundo é um tinte translúcido e o texto fica com a cor
# do tema: assim a linha se lê tanto no tema claro quanto no escuro.
# Pintar explicitamente não é preciosismo — deixado por conta do tema, o
# relatório ganhava fundo vermelho em linhas que ninguém mandou pintar,
# e num tema claro daria vermelho sobre vermelho.
CORES = {
    "erro":  (192, 57, 43),
    "aviso": (184, 134, 11),
    "ok":    (46, 125, 50),
}
ALFA_FUNDO = 52
SIMBOLO = {"erro": "✗", "aviso": "⚠", "ok": "✓"}


class AbaCadastro(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._combos = {}
        self._tarefa = None
        self._vizinhos = {}     # {codigo: QgsGeometry}
        self._resumo = {}
        self._montar()
        self.recarregar()

    # ── interface ────────────────────────────────────────
    def _montar(self):
        fora = QVBoxLayout(self)
        fora.setContentsMargins(0, 0, 0, 0)

        rolagem = QScrollArea()
        rolagem.setWidgetResizable(True)
        rolagem.setFrameShape(QScrollArea.Shape.NoFrame)
        miolo = QWidget()
        v = QVBoxLayout(miolo)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        rolagem.setWidget(miolo)
        fora.addWidget(rolagem)

        nota = QLabel(
            "Esta aba <b>não envia</b> o cadastro — o SICAR só aceita pelo "
            "módulo oficial. Ela prepara o que vai ser enviado: mede, "
            "valida, confere a vizinhança e gera os arquivos."
        )
        nota.setWordWrap(True)
        nota.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(nota)

        # Temas
        gb = QGroupBox("Camadas do projeto, por tema")
        f = QFormLayout(gb)
        for chave, rotulo, obrigatorio in cadastro.TEMAS:
            combo = QComboBox()
            combo.setMinimumWidth(260)
            self._combos[chave] = combo
            f.addRow(rotulo + (" *" if obrigatorio else ""), combo)
        btn_rec = QPushButton("Atualizar lista de camadas")
        btn_rec.clicked.connect(self.recarregar)
        f.addRow(btn_rec)
        v.addWidget(gb)

        # Parâmetros
        gb2 = QGroupBox("Parâmetros")
        f2 = QFormLayout(gb2)

        self.cb_uf = QComboBox()
        # Primeiro item vazio de propósito: sem ele a lista abre em "AC"
        # e o plugin passa a afirmar um estado que ninguém escolheu —
        # a consulta ao SICAR sairia na UF errada sem nenhum aviso.
        self.cb_uf.addItem(SEM_UF)
        self.cb_uf.addItems(sorted(carcode.UFS))
        f2.addRow("UF:", self.cb_uf)

        self.campo_municipio = QLineEdit()
        self.campo_municipio.setPlaceholderText("Para deduzir o módulo fiscal")
        f2.addRow("Município:", self.campo_municipio)

        self.cb_regime = QComboBox()
        for chave, rotulo, pct in cadastro.REGIMES_RL:
            self.cb_regime.addItem(f"{rotulo} — {pct}%", chave)
        self.cb_regime.setToolTip(
            "Percentual mínimo de Reserva Legal do art. 12 da Lei 12.651/2012."
        )
        f2.addRow("Regime da Reserva Legal:", self.cb_regime)

        h = QHBoxLayout()
        self.sp_modulo = QDoubleSpinBox()
        self.sp_modulo.setRange(0.0, 200.0)
        self.sp_modulo.setDecimals(2)
        self.sp_modulo.setSuffix(" ha")
        self.sp_modulo.setSpecialValueText("não informado")
        self.sp_modulo.setToolTip(
            "Área de 1 módulo fiscal no município. Deduzido do próprio "
            "SICAR pelo botão ao lado."
        )
        h.addWidget(self.sp_modulo, 1)
        f2.addRow("Módulo fiscal:", h)
        v.addWidget(gb2)

        # Ações
        ha_ = QHBoxLayout()
        self.btn_validar = QPushButton("Validar")
        self.btn_validar.setDefault(True)
        self.btn_validar.clicked.connect(self.validar)
        self.btn_vizinhos = QPushButton("Conferir vizinhança no SICAR")
        self.btn_vizinhos.clicked.connect(self.conferir_vizinhanca)
        ha_.addWidget(self.btn_validar)
        ha_.addWidget(self.btn_vizinhos)
        ha_.addStretch()
        v.addLayout(ha_)

        self.barra = QProgressBar()
        self.barra.setVisible(False)
        v.addWidget(self.barra)

        self.status = QLabel("Escolha ao menos a camada do perímetro.")
        self.status.setWordWrap(True)
        v.addWidget(self.status)

        self.lbl_resumo = QLabel("—")
        self.lbl_resumo.setWordWrap(True)
        self.lbl_resumo.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_resumo.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse)
        v.addWidget(self.lbl_resumo)

        self.tabela = QTableWidget(0, 3)
        self.tabela.setHorizontalHeaderLabels(["", "Tema", "Constatação"])
        self.tabela.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Sem cores alternadas: quem distingue as linhas aqui é o nível.
        self.tabela.setAlternatingRowColors(False)
        self.tabela.setWordWrap(True)
        self.tabela.verticalHeader().setVisible(False)
        self.tabela.setMinimumHeight(220)
        cab = self.tabela.horizontalHeader()
        cab.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        cab.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        v.addWidget(self.tabela, 1)

        hb = QHBoxLayout()
        self.btn_mapa = QPushButton("Vizinhos no mapa")
        self.btn_mapa.setEnabled(False)
        self.btn_mapa.setToolTip(
            "Carrega os CARs vizinhos como camada, para corrigir o desenho "
            "olhando para eles."
        )
        self.btn_mapa.clicked.connect(self._vizinhos_no_mapa)
        self.btn_pacote = QPushButton("Exportar pacote…")
        self.btn_pacote.setToolTip(
            "Um shapefile por tema, em SIRGAS 2000 (EPSG:4674)."
        )
        self.btn_pacote.clicked.connect(self._exportar_pacote)
        self.btn_memorial = QPushButton("Memorial descritivo…")
        self.btn_memorial.clicked.connect(self._exportar_memorial)
        hb.addWidget(self.btn_mapa)
        hb.addWidget(self.btn_pacote)
        hb.addWidget(self.btn_memorial)
        hb.addStretch()
        v.addLayout(hb)

    # ── dados ────────────────────────────────────────────
    def recarregar(self):
        """Relê as camadas do projeto preservando as escolhas já feitas."""
        disponiveis = layers.camadas_poligonais_do_projeto()
        nomes = [c.name() for c in disponiveis]
        for chave, combo in self._combos.items():
            atual = combo.currentText()
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(SEM_CAMADA)
            combo.addItems(nomes)
            if atual in nomes:
                combo.setCurrentText(atual)
            else:
                palpite = self._palpitar(chave, nomes)
                if palpite:
                    combo.setCurrentText(palpite)
            combo.blockSignals(False)
        self._preencher_local()

    # Palavras que costumam aparecer no nome da camada de cada tema.
    # A ORDEM é significativa: da pista mais específica para a mais
    # genérica. Sem isso o palpite é por ordem das camadas no projeto, e
    # num projeto real "Limite de processamento" era escolhido como
    # perímetro do imóvel só por conter "limite" — um palpite errado que
    # parece certo é pior que nenhum.
    PISTAS = {
        "area_imovel":      ("area_imovel", "área do imóvel", "area do imovel",
                             "perímetro", "perimetro", "imóvel", "imovel",
                             "limite do", "limite"),
        "reserva_legal":    ("reserva legal", "reserva_legal", "(arl)", "reserva"),
        "app":              ("app", "preservação permanente", "preservacao permanente"),
        "vegetacao_nativa": ("remanescente", "vegetação nativa", "vegetacao nativa",
                             "(rvn)", "nativa"),
        "area_consolidada": ("consolidad", "antrópic", "antropic", "(ac)"),
        "area_pousio":      ("pousio",),
        "uso_restrito":     ("uso restrito", "restrito"),
        "servidao":         ("servidão", "servidao"),
        "hidrografia":      ("hidrografia", "drenagem", "curso d", "hidro"),
        "nascente":         ("nascente", "olho d"),
    }

    def _palpitar(self, chave, nomes):
        """A camada que casa com a pista mais específica, não a primeira
        que casa com qualquer pista."""
        pistas = self.PISTAS.get(chave, ())
        melhor, melhor_peso = None, len(pistas)
        for nome in nomes:
            baixo = nome.lower()
            for peso, pista in enumerate(pistas):
                if pista in baixo:
                    if peso < melhor_peso:
                        melhor, melhor_peso = nome, peso
                    break
        return melhor

    def _preencher_local(self):
        """UF e município a partir do perímetro, quando ele veio de uma
        consulta e já traz os atributos."""
        camada = self._camada("area_imovel")
        if camada is None:
            return
        nomes = [c.name() for c in camada.fields()]
        try:
            f = next(camada.getFeatures())
        except StopIteration:
            return
        if "codigo_car" in nomes:
            uf = carcode.uf_de(str(f["codigo_car"] or ""))
            if uf:
                self.cb_uf.setCurrentText(uf)
                self.cb_regime.setCurrentIndex(
                    max(0, self.cb_regime.findData(cadastro.regime_sugerido(uf))))
        if "municipio" in nomes and not self.campo_municipio.text().strip():
            self.campo_municipio.setText(str(f["municipio"] or ""))

    def _camada(self, chave):
        combo = self._combos.get(chave)
        if combo is None or combo.currentText() == SEM_CAMADA:
            return None
        achadas = QgsProject.instance().mapLayersByName(combo.currentText())
        return achadas[0] if achadas else None

    def _mapeamento(self):
        mapa = {}
        for chave, _, _ in cadastro.TEMAS:
            camada = self._camada(chave)
            if camada is not None:
                mapa[chave] = camada
        return mapa

    # ── validação ────────────────────────────────────────
    def validar(self):
        mapa = self._mapeamento()
        if "area_imovel" not in mapa:
            self.status.setText("✗ Escolha a camada do perímetro do imóvel.")
            return

        modulo = self.sp_modulo.value() or None
        resumo, achados = cadastro.validar(
            mapa, regime=self.cb_regime.currentData(), modulo_fiscal_ha=modulo)
        self._resumo = resumo

        self.tabela.setRowCount(0)
        for a in achados:
            self._linha(a["nivel"], a["tema"], a["mensagem"])

        self._mostrar_resumo(resumo)
        erros = sum(1 for a in achados if a["nivel"] == "erro")
        avisos = sum(1 for a in achados if a["nivel"] == "aviso")
        if erros:
            self.status.setText(
                f"✗ {erros} impedimento(s) e {avisos} ponto(s) de atenção.")
        else:
            self.status.setText(
                f"✓ Sem impedimentos geométricos. {avisos} ponto(s) de atenção.")

    def _mostrar_resumo(self, resumo):
        partes = [f"Área do imóvel: <b>{texto.ha(resumo.get('area_ha'))}</b>"]
        mf = resumo.get("modulos_fiscais")
        if mf:
            partes.append(f"{texto.num(mf)} módulos fiscais")
            # O recorte de 4 MF decide regra de APP consolidada e isenção
            # de recomposição — é a informação que muda o cadastro.
            partes.append("pequena propriedade (até 4 MF)" if mf <= 4
                          else "acima de 4 módulos fiscais")
        for chave, rotulo, _ in cadastro.TEMAS:
            if chave == "area_imovel":
                continue
            a = resumo.get("areas", {}).get(chave)
            if a:
                partes.append(f"{rotulo}: {texto.ha(a)}")
        self.lbl_resumo.setText(" · ".join(partes))

    def _linha(self, nivel, tema, mensagem):
        from qgis.PyQt.QtGui import QColor

        rgb = CORES.get(nivel)
        fundo = QColor(*rgb, ALFA_FUNDO) if rgb else None
        forte = QColor(*rgb) if rgb else None

        i = self.tabela.rowCount()
        self.tabela.insertRow(i)
        for coluna, valor in enumerate((SIMBOLO.get(nivel, ""), tema, mensagem)):
            item = QTableWidgetItem(valor)
            item.setToolTip(mensagem if coluna == 2 else nivel)
            if fundo is not None:
                item.setBackground(fundo)
            if coluna == 0 and forte is not None:
                item.setForeground(forte)
            self.tabela.setItem(i, coluna, item)
        self.tabela.resizeRowToContents(i)

    # ── vizinhança ───────────────────────────────────────
    def conferir_vizinhanca(self):
        camada = self._camada("area_imovel")
        if camada is None:
            self.status.setText("✗ Escolha a camada do perímetro do imóvel.")
            return

        if self.cb_uf.currentText() == SEM_UF:
            self.status.setText(
                "✗ Escolha a UF — a camada do SICAR é por estado.")
            return

        caixa = self._caixa_em_graus(camada)
        if caixa is None:
            self.status.setText("✗ Não consegui obter o retângulo do perímetro.")
            return

        self.btn_vizinhos.setEnabled(False)
        self.barra.setVisible(True)
        self.barra.setRange(0, 0)
        self.status.setText("Consultando o SICAR…")

        self._tarefa = tasks.VizinhancaTask(
            self.cb_uf.currentText(), *caixa,
            municipio=self.campo_municipio.text().strip(),
            excluir=self._codigo_do_perimetro(camada),
            timeout=config.get("timeout"),
        )
        self._tarefa.taskCompleted.connect(self._vizinhanca_pronta)
        self._tarefa.taskTerminated.connect(self._vizinhanca_falhou)
        QgsApplication.taskManager().addTask(self._tarefa)

    def _caixa_em_graus(self, camada):
        """Retângulo do perímetro em EPSG:4326 — a busca do WFS é em graus."""
        try:
            e = camada.extent()
            destino = QgsCoordinateReferenceSystem("EPSG:4326")
            if camada.crs().authid() != destino.authid():
                tr = QgsCoordinateTransform(
                    camada.crs(), destino,
                    QgsProject.instance().transformContext())
                e = tr.transformBoundingBox(e)
            return e.xMinimum(), e.yMinimum(), e.xMaximum(), e.yMaximum()
        except Exception as e:
            log.warning("Retângulo do perímetro: %s", e)
            return None

    def _codigo_do_perimetro(self, camada):
        """Se o perímetro veio de uma consulta, não faz sentido acusá-lo
        de sobrepor a si mesmo."""
        if "codigo_car" not in [c.name() for c in camada.fields()]:
            return None
        try:
            return str(next(camada.getFeatures())["codigo_car"] or "") or None
        except StopIteration:
            return None

    def _vizinhanca_pronta(self):
        tarefa, self._tarefa = self._tarefa, None
        self.btn_vizinhos.setEnabled(True)
        self.barra.setVisible(False)

        if tarefa.modulo_fiscal:
            self.sp_modulo.setValue(tarefa.modulo_fiscal)
        elif tarefa.aviso_modulo:
            self._linha("aviso", "Módulo fiscal", tarefa.aviso_modulo)

        self._vizinhos = cadastro.geometrias_de_feicoes(tarefa.vizinhos)
        self.btn_mapa.setEnabled(bool(self._vizinhos))

        crs = QgsCoordinateReferenceSystem(cadastro.CRS_CADASTRO)
        camada = self._camada("area_imovel")
        perimetro = cadastro._dissolver(camada, crs)
        if perimetro is None:
            self.status.setText("✗ Perímetro sem geometria.")
            return

        area_imovel = (self._resumo.get("area_ha")
                       or cadastro._area_ha(perimetro, crs))
        medidos = cadastro.medir_vizinhanca(perimetro, self._vizinhos, area_imovel)

        invasores = [m for m in medidos if not m["apenas_encosta"]]
        encostam = [m for m in medidos if m["apenas_encosta"]]

        for m in invasores:
            self._linha(
                "erro", "Vizinhança",
                f"Sobrepõe o CAR {m['codigo']} em {texto.ha(m['area_ha'])} "
                f"({texto.num(m['percentual'])}% do imóvel).")
        if encostam:
            self._linha(
                "ok", "Vizinhança",
                f"{len(encostam)} CAR(s) apenas confrontante(s), sem invasão: "
                + ", ".join(m["codigo"] for m in encostam[:6])
                + ("…" if len(encostam) > 6 else ""))
        if not medidos:
            self._linha("ok", "Vizinhança",
                        f"{len(self._vizinhos)} CAR(s) na redondeza, nenhum "
                        f"tocando o perímetro.")
        if tarefa.truncado:
            self._linha("aviso", "Vizinhança",
                        "A lista do SICAR foi cortada no limite — pode haver "
                        "mais vizinhos do que os mostrados.")

        self.status.setText(
            f"✓ {len(self._vizinhos)} CAR(s) no retângulo · "
            f"{len(invasores)} com sobreposição real.")

    def _vizinhanca_falhou(self):
        tarefa, self._tarefa = self._tarefa, None
        self.btn_vizinhos.setEnabled(True)
        self.barra.setVisible(False)
        motivo = (tarefa.erro if tarefa else None) or "Consulta cancelada."
        self.status.setText(f"✗ {motivo}")

    def _vizinhos_no_mapa(self):
        camada = cadastro.camada_de_vizinhos(self._vizinhos)
        if camada is None:
            return
        QgsProject.instance().addMapLayer(camada)
        self.status.setText("✓ Vizinhos carregados como camada.")

    # ── saídas ───────────────────────────────────────────
    def _pasta_inicial(self):
        return config.get("pasta_saida") or ""

    def _exportar_pacote(self):
        mapa = self._mapeamento()
        if "area_imovel" not in mapa:
            self.status.setText("✗ Escolha ao menos a camada do perímetro.")
            return
        pasta = QFileDialog.getExistingDirectory(
            self, "Pasta para o pacote de shapefiles", self._pasta_inicial())
        if not pasta:
            return

        prefixo = self._codigo_do_perimetro(mapa["area_imovel"]) or ""
        ok, rel = cadastro.exportar_pacote(mapa, pasta, prefixo)
        for rotulo, erro in rel["falhas"]:
            self._linha("erro", rotulo, f"Não gravou: {erro}")
        if ok:
            self.status.setText(
                f"✓ {len(rel['gravados'])} shapefile(s) em {pasta} "
                f"(SIRGAS 2000, EPSG:4674).")
        else:
            QMessageBox.warning(self, "GeoCAR Suite",
                                "Não consegui gravar o pacote.")

    def _exportar_memorial(self):
        camada = self._camada("area_imovel")
        if camada is None:
            self.status.setText("✗ Escolha a camada do perímetro do imóvel.")
            return

        self.status.setText("Calculando o memorial…")
        QgsApplication.processEvents()
        ok, res = cadastro.memorial(camada, vizinhos=self._vizinhos)
        if not ok:
            self.status.setText(f"✗ {res}")
            return

        prefixo = self._codigo_do_perimetro(camada) or "memorial"
        base = self._pasta_inicial()
        sugestao = f"{base}/{cadastro._limpar_nome(prefixo)}_memorial.xlsx" \
            if base else f"{cadastro._limpar_nome(prefixo)}_memorial.xlsx"
        caminho, filtro = QFileDialog.getSaveFileName(
            self, "Memorial descritivo", sugestao,
            "Planilha (*.xlsx);;CSV (*.csv)")
        if not caminho:
            self.status.setText("Memorial cancelado.")
            return

        formato = "xlsx" if "xlsx" in filtro.lower() else "csv"
        if formato == "csv":
            gravou, saida = self._memorial_csv(caminho, res)
        else:
            gravou, saida = export.exportar_tabela(
                res["linhas"], cadastro.COLUNAS_MEMORIAL, caminho, "xlsx",
                "memorial")

        if not gravou:
            QMessageBox.warning(self, "GeoCAR Suite",
                                f"Não consegui gravar:\n{saida}")
            return

        fechamento = res["fechamento_m"]
        self._linha(
            "ok" if fechamento < 0.01 else "aviso", "Memorial",
            f"{len(res['linhas'])} vértices · perímetro "
            f"{texto.num(res['perimetro_m'])} m · {res['crs']} · "
            f"erro de fechamento {texto.num(fechamento, 4)} m.")
        self.status.setText(f"✓ Memorial gravado: {saida}")

    def _memorial_csv(self, caminho, res):
        """CSV com cabeçalho e ';' — é o que o Excel brasileiro abre sem
        pedir assistente de importação."""
        import csv
        if not caminho.lower().endswith(".csv"):
            caminho += ".csv"
        try:
            with open(caminho, "w", encoding="utf-8-sig", newline="") as fh:
                w = csv.writer(fh, delimiter=";")
                w.writerow(cadastro.COLUNAS_MEMORIAL)
                for linha in res["linhas"]:
                    w.writerow([linha.get(c, "")
                                for c in cadastro.COLUNAS_MEMORIAL])
            return True, caminho
        except Exception as e:
            return False, str(e)
