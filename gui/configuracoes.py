# -*- coding: utf-8 -*-
"""
configuracoes.py — aba de preferências.

Note o que NÃO está aqui: caminho de base de sobreposição. As bases
são as camadas abertas no projeto, então o que resta configurar é
quais camadas IGNORAR no cruzamento (bases de referência, imagens de
fundo, rascunhos) — o inverso do que era antes, e bem menos trabalho.
"""

import logging

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QPushButton, QSpinBox,
    QVBoxLayout, QWidget,
)

from ..core import config, layers
from ..core.sources import semas_pa

log = logging.getLogger("GeoCAR_Suite")


class AbaConfiguracoes(QWidget):
    def __init__(self, iface, parent=None):
        super().__init__(parent)
        self.iface = iface
        self._montar()
        self.recarregar()

    def _montar(self):
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        # Geral
        gb = QGroupBox("Geral")
        f = QFormLayout(gb)
        self.sp_timeout = QSpinBox()
        self.sp_timeout.setRange(5, 300)
        self.sp_timeout.setSuffix(" s")
        self.sp_timeout.valueChanged.connect(
            lambda x: config.set("timeout", x)
        )
        f.addRow("Tempo limite:", self.sp_timeout)

        self.chk_carregar = QCheckBox("Carregar o imóvel no projeto")
        self.chk_carregar.toggled.connect(
            lambda x: config.set("carregar_no_projeto", x)
        )
        f.addRow(self.chk_carregar)

        self.chk_zoom = QCheckBox("Enquadrar o mapa no imóvel")
        self.chk_zoom.toggled.connect(
            lambda x: config.set("zoom_apos_consulta", x)
        )
        f.addRow(self.chk_zoom)

        self.chk_simbologia = QCheckBox("Aplicar simbologia do plugin na camada")
        self.chk_simbologia.setToolTip(
            "Contorno âmbar e preenchimento translúcido, para o imóvel se "
            "destacar sem esconder o que está embaixo. Desmarque se você "
            "usa um estilo próprio."
        )
        self.chk_simbologia.toggled.connect(
            lambda x: config.set("simbologia_automatica", x)
        )
        f.addRow(self.chk_simbologia)

        h = QHBoxLayout()
        self.campo_saida = QLineEdit()
        self.campo_saida.setPlaceholderText("Pasta sugerida ao exportar")
        self.campo_saida.editingFinished.connect(
            lambda: config.set("pasta_saida", self.campo_saida.text().strip())
        )
        btn = QPushButton("…")
        btn.setMaximumWidth(36)
        btn.clicked.connect(self._escolher_pasta)
        h.addWidget(self.campo_saida, 1)
        h.addWidget(btn)
        f.addRow("Pasta de saída:", h)
        v.addWidget(gb)

        # Camadas ignoradas
        gb2 = QGroupBox("Camadas fora da sobreposição")
        v2 = QVBoxLayout(gb2)
        dica = QLabel(
            "Marque o que não deve entrar no cruzamento — imagens de "
            "fundo, rascunhos, camadas de referência. O resto das "
            "camadas vetoriais do projeto é cruzado automaticamente."
        )
        dica.setWordWrap(True)
        v2.addWidget(dica)

        self.lista = QListWidget()
        self.lista.setMaximumHeight(160)
        self.lista.itemChanged.connect(self._salvar_ignoradas)
        v2.addWidget(self.lista)

        btn_rec = QPushButton("Atualizar lista de camadas")
        btn_rec.clicked.connect(self._carregar_camadas)
        v2.addWidget(btn_rec)
        v.addWidget(gb2)

        # Cache
        gbc = QGroupBox("Cache de consultas")
        vc = QVBoxLayout(gbc)
        dicac = QLabel(
            "Guarda as consultas já feitas e reaproveita quando o mesmo "
            "código voltar — o ganho aparece sobretudo no lote, quando a "
            "mesma planilha é reprocessada. A geometria do CAR muda em "
            "escala de meses; quando precisar do dado do dia, use "
            "<i>Reconsultar</i> na aba Consulta."
        )
        dicac.setWordWrap(True)
        dicac.setTextFormat(Qt.TextFormat.RichText)
        vc.addWidget(dicac)

        fc = QFormLayout()
        self.chk_cache = QCheckBox("Usar cache")
        self.chk_cache.toggled.connect(self._alternar_cache)
        fc.addRow(self.chk_cache)

        self.sp_cache = QSpinBox()
        self.sp_cache.setRange(1, 365)
        self.sp_cache.setSuffix(" dias")
        self.sp_cache.valueChanged.connect(
            lambda x: config.set("cache_validade_dias", x)
        )
        fc.addRow("Validade:", self.sp_cache)
        vc.addLayout(fc)

        hc = QHBoxLayout()
        self.lbl_cache = QLabel()
        self.lbl_cache.setWordWrap(True)
        btn_limpar_cache = QPushButton("Limpar cache")
        btn_limpar_cache.clicked.connect(self._limpar_cache)
        hc.addWidget(self.lbl_cache, 1)
        hc.addWidget(btn_limpar_cache)
        vc.addLayout(hc)
        v.addWidget(gbc)

        # SEMAS-PA
        gb3 = QGroupBox("SEMAS-PA (opcional — só para o Pará)")
        v3 = QVBoxLayout(gb3)
        dica3 = QLabel(
            "O plugin funciona sem isto: o SICAR nacional atende todas as "
            "UFs, inclusive o Pará. O token só acrescenta busca por "
            "CPF/CNPJ e o nome do responsável.\n\n"
            "Para obter: abra o portal da SEMAS-PA, tecle F12 → Network, "
            "faça qualquer consulta e copie o cabeçalho <i>authorization</i>."
        )
        dica3.setWordWrap(True)
        dica3.setTextFormat(Qt.TextFormat.RichText)
        v3.addWidget(dica3)

        h3 = QHBoxLayout()
        self.campo_token = QLineEdit()
        self.campo_token.setPlaceholderText("Cole o token aqui")
        self.campo_token.setEchoMode(QLineEdit.EchoMode.Password)
        btn_salvar = QPushButton("Salvar")
        btn_salvar.clicked.connect(self._salvar_token)
        btn_limpar = QPushButton("Limpar")
        btn_limpar.clicked.connect(self._limpar_token)
        h3.addWidget(self.campo_token, 1)
        h3.addWidget(btn_salvar)
        h3.addWidget(btn_limpar)
        v3.addLayout(h3)

        self.lbl_token = QLabel()
        self.lbl_token.setWordWrap(True)
        v3.addWidget(self.lbl_token)
        v.addWidget(gb3)

        v.addStretch()

    # ── ações ────────────────────────────────────────────
    def recarregar(self):
        self.sp_timeout.setValue(config.get("timeout"))
        self.chk_carregar.setChecked(config.get("carregar_no_projeto"))
        self.chk_zoom.setChecked(config.get("zoom_apos_consulta"))
        self.chk_simbologia.setChecked(config.get("simbologia_automatica"))
        self.campo_saida.setText(config.get("pasta_saida") or "")
        self.chk_cache.setChecked(config.get("usar_cache"))
        self.sp_cache.setValue(config.get("cache_validade_dias"))
        self.sp_cache.setEnabled(self.chk_cache.isChecked())
        self._status_cache()
        self._carregar_camadas()
        self._status_token()

    # ── cache ────────────────────────────────────────────
    def _alternar_cache(self, ligado):
        config.set("usar_cache", ligado)
        self.sp_cache.setEnabled(ligado)

    def _limpar_cache(self):
        from ..core import cache
        if not cache.limpar():
            self.lbl_cache.setText("✗ Não consegui limpar o cache.")
            return
        self._status_cache()

    def _status_cache(self):
        from ..core import cache
        e = cache.estatisticas()
        if not e["itens"]:
            self.lbl_cache.setText("Cache vazio.")
            return
        texto = f"{e['itens']} imóvel(is) guardado(s) · {e['tamanho_kb']:.0f} KB"
        if e["mais_antigo_dias"] is not None:
            texto += f" · mais antigo com {e['mais_antigo_dias']:.0f} dia(s)"
        self.lbl_cache.setText(texto)

    def _escolher_pasta(self):
        pasta = QFileDialog.getExistingDirectory(
            self, "Pasta de saída", self.campo_saida.text().strip() or ""
        )
        if pasta:
            self.campo_saida.setText(pasta)
            config.set("pasta_saida", pasta)

    def _carregar_camadas(self):
        ignoradas = set(config.camadas_ignoradas())
        self.lista.blockSignals(True)
        self.lista.clear()
        for camada in layers.camadas_poligonais_do_projeto():
            item = QListWidgetItem(camada.name())
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if camada.name() in ignoradas else Qt.CheckState.Unchecked
            )
            self.lista.addItem(item)
        self.lista.blockSignals(False)
        if self.lista.count() == 0:
            self.lista.addItem("(nenhuma camada vetorial aberta no projeto)")

    def _salvar_ignoradas(self, _item=None):
        nomes = []
        for i in range(self.lista.count()):
            item = self.lista.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                nomes.append(item.text())
        config.definir_camadas_ignoradas(nomes)

    def _salvar_token(self):
        ok, msg = semas_pa.definir_token(self.campo_token.text())
        self.campo_token.clear()
        self.lbl_token.setText(("✓ " if ok else "✗ ") + msg)
        if ok:
            self._status_token()

    def _limpar_token(self):
        semas_pa.limpar_token()
        self.campo_token.clear()
        self._status_token()

    def _status_token(self):
        s = semas_pa.status_token()
        if not s["tem_token"]:
            self.lbl_token.setText("Sem token — consultas usam o SICAR nacional.")
        elif s["provavelmente_expirado"]:
            self.lbl_token.setText(
                "⚠ Token salvo, mas provavelmente expirado "
                f"({s['idade_s'] // 60} min). Gere um novo se a busca falhar."
            )
        else:
            self.lbl_token.setText(
                f"✓ Token salvo há {max(0, s['idade_s'] or 0) // 60} min."
            )
