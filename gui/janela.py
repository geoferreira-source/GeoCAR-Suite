# -*- coding: utf-8 -*-
"""
janela.py — a janela do plugin.

Janela independente, não um painel acoplado ao QGIS. Isso significa:
entrada própria na barra de tarefas, pode ser minimizada sozinha,
arrastada para um segundo monitor e ficar ATRÁS do QGIS quando você
clica no mapa.

Por que sem pai (parent=None)
-----------------------------
No Qt, uma janela com pai é "transitória": fica sempre acima do pai e
não ganha botão próprio na barra de tarefas. Como o pedido é ter uma
janela de verdade — tipicamente para deixá-la no outro monitor —, ela
nasce sem pai. O preço é que a limpeza passa a ser nossa: o plugin
fecha e destrói a janela no unload(), senão ela sobraria aberta ao
desativar o plugin ou ao sair do QGIS.

Estado preservado ao fechar
---------------------------
Fechar só esconde; o objeto continua vivo no plugin. Então o imóvel
consultado, a tabela de sobreposições e o que estiver digitado
continuam lá quando a janela é reaberta. Fechar sem querer (ou com
Esc) não custa trabalho perdido.
"""

import logging
import os
import re

from qgis.PyQt.QtCore import QByteArray, QSettings, Qt, pyqtSignal
from qgis.PyQt.QtGui import QIcon, QPixmap
from qgis.PyQt.QtWidgets import (
    QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QTabWidget,
    QVBoxLayout,
)

from .busca import AbaBusca
from .cadastro import AbaCadastro
from .configuracoes import AbaConfiguracoes
from .consulta import AbaConsulta
from .lote import AbaLote

log = logging.getLogger("GeoCAR_Suite")

CHAVE_GEOMETRIA = "GeoCAR_Suite/geometria_janela"
TAMANHO_PADRAO = (860, 720)

RAIZ_PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PASTA_RECURSOS = os.path.join(RAIZ_PLUGIN, "resources")

# A arte do wordmark existe em duas versões porque a original é
# verde-escura: sobre o tema escuro do QGIS ela praticamente some. A
# variante clara preserva a matiz e a hierarquia da marca (o "CAR"
# continua sendo o que salta), só remapeia a luminância.
LOGO_TEMA_CLARO = os.path.join(PASTA_RECURSOS, "logo_tema_claro.png")
LOGO_TEMA_ESCURO = os.path.join(PASTA_RECURSOS, "logo_tema_escuro.png")

# Emblema (a ilustração do imóvel com a pilha de camadas). Fica no
# cabeçalho porque é onde ele aparece grande o bastante para ser lido:
# a mesma arte a 24px na barra de ferramentas vira uma mancha verde.
EMBLEMA = os.path.join(PASTA_RECURSOS, "emblema.png")

LOGO_ESCALA = 2.0   # arte em 2x, para telas HiDPI


def versao_do_plugin() -> str:
    """Lê a versão do metadata.txt — evita manter o número repetido no
    código e desencontrado do que o QGIS mostra."""
    try:
        with open(os.path.join(RAIZ_PLUGIN, "metadata.txt"), encoding="utf-8") as f:
            m = re.search(r"^version=(.+)$", f.read(), re.MULTILINE)
        return m.group(1).strip() if m else ""
    except Exception:
        return ""


class JanelaGeoCAR(QDialog):
    """Janela principal. Emite `fechou` para o plugin desmarcar o botão
    da barra de ferramentas."""

    fechou = pyqtSignal()

    def __init__(self, iface, icone: str = None):
        # Sem pai, de propósito — ver o cabeçalho do módulo.
        super().__init__(None)
        self.iface = iface

        self.setObjectName("GeoCARSuiteJanela")
        self.setWindowTitle("GeoCAR Suite")
        if icone:
            self.setWindowIcon(QIcon(icone))

        # QDialog nasce como caixa de diálogo: sem minimizar/maximizar.
        # Declarar os botões a transforma numa janela comum.
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowMinimizeButtonHint
            | Qt.WindowType.WindowMaximizeButtonHint
            | Qt.WindowType.WindowCloseButtonHint
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        cabecalho = self._montar_cabecalho()
        if cabecalho is not None:
            layout.addWidget(cabecalho)

        self.abas = QTabWidget()
        self.aba_consulta = AbaConsulta(iface)
        self.aba_busca = AbaBusca(iface)
        self.aba_lote = AbaLote(iface)
        self.aba_cadastro = AbaCadastro(iface)
        self.aba_config = AbaConfiguracoes(iface)

        self.abas.addTab(self.aba_consulta, "Consulta")
        self.abas.addTab(self.aba_busca, "Buscar")
        self.abas.addTab(self.aba_lote, "Lote")
        self.abas.addTab(self.aba_cadastro, "Cadastro")
        self.abas.addTab(self.aba_config, "Configurações")

        # Achou na busca, consulta na aba certa: a aba Buscar só
        # localiza o código; quem baixa geometria e cruza é a Consulta.
        self.aba_busca.consultar_codigo.connect(self._consultar_da_busca)
        # A lista de camadas do projeto muda enquanto o usuário
        # trabalha; relemos ao abrir a aba em vez de manter um sinal
        # ligado no projeto inteiro.
        self.abas.currentChanged.connect(self._ao_trocar_aba)
        layout.addWidget(self.abas)

        self.resize(*TAMANHO_PADRAO)
        self._restaurar_geometria()

    # ── cabeçalho ────────────────────────────────────────
    def _tema_escuro(self) -> bool:
        """Segue o tema ativo do QGIS em vez de uma preferência
        própria: o usuário troca de tema e o logo acompanha."""
        try:
            return self.palette().color(self.backgroundRole()).lightness() < 128
        except Exception:
            return False

    def _montar_cabecalho(self):
        """Faixa com o logotipo à esquerda e a versão à direita.

        A versão fica aqui de propósito: é a primeira coisa perguntada
        quando algo não funciona, e assim não exige abrir o gerenciador
        de complementos para descobrir.
        """
        def rotulo_com_arte(caminho):
            """QLabel com a arte, ou None se o arquivo não estiver lá."""
            if not os.path.exists(caminho):
                return None
            pix = QPixmap(caminho)
            if pix.isNull():
                return None
            # Sem isto a arte 2x apareceria com o dobro do tamanho.
            pix.setDevicePixelRatio(LOGO_ESCALA)
            rot = QLabel()
            rot.setPixmap(pix)
            return rot

        # O emblema tem cores próprias e borda branca: funciona nos dois
        # temas sem variante. Só o logotipo escrito precisa trocar.
        emblema = rotulo_com_arte(EMBLEMA)
        marca = rotulo_com_arte(
            LOGO_TEMA_ESCURO if self._tema_escuro() else LOGO_TEMA_CLARO
        )
        if emblema is None and marca is None:
            return None   # sem arte nenhuma, a janela abre sem faixa

        faixa = QFrame()
        faixa.setObjectName("GeoCARCabecalho")
        faixa.setFrameShape(QFrame.Shape.NoFrame)
        linha = QHBoxLayout(faixa)
        linha.setContentsMargins(12, 8, 12, 8)
        linha.setSpacing(12)

        if emblema is not None:
            emblema.setAccessibleName("GeoCAR Suite")
            linha.addWidget(emblema)
        if marca is not None:
            marca.setAccessibleName("GeoCAR Suite")
            linha.addWidget(marca)
        linha.addStretch()

        versao = versao_do_plugin()
        if versao:
            rotulo = QLabel(f"v{versao}")
            rotulo.setEnabled(False)   # tom apagado, sem cor fixa no código
            linha.addWidget(rotulo, 0, Qt.AlignmentFlag.AlignBottom)

        # Separador de 1px: encosta a faixa nas abas sem precisar
        # pintar fundo, que brigaria com o tema.
        risco = QFrame()
        risco.setFrameShape(QFrame.Shape.HLine)
        risco.setFrameShadow(QFrame.Shadow.Sunken)

        caixa = QFrame()
        v = QVBoxLayout(caixa)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(faixa)
        v.addWidget(risco)
        return caixa

    # ── ciclo de vida ────────────────────────────────────
    def abrir(self):
        """Mostra a janela e traz para a frente, mesmo que esteja
        minimizada ou atrás do QGIS."""
        self.show()
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()

    def done(self, resultado):
        """
        Chamado tanto pelo X quanto pelo Esc (QDialog converte os dois
        em done()). É aqui que a geometria é guardada e o plugin é
        avisado — fazer isso só no closeEvent deixaria o Esc de fora.
        """
        self._salvar_geometria()
        super().done(resultado)
        self.fechou.emit()

    # ── geometria entre sessões ──────────────────────────
    def _salvar_geometria(self):
        try:
            QSettings().setValue(CHAVE_GEOMETRIA, self.saveGeometry())
        except Exception as e:
            # preferência cosmética: nunca vale travar por isso
            log.debug("Não consegui salvar a geometria da janela: %s", e)

    def _restaurar_geometria(self):
        try:
            geo = QSettings().value(CHAVE_GEOMETRIA)
            if isinstance(geo, QByteArray) and not geo.isEmpty():
                self.restoreGeometry(geo)
                self._garantir_visivel()
        except Exception as e:
            log.debug("Não consegui restaurar a geometria da janela: %s", e)

    def _garantir_visivel(self):
        """
        Se a janela foi fechada num monitor que não existe mais, a
        geometria salva a colocaria fora da tela — e ela ficaria
        invisível, parecendo que o plugin não abriu. Nesse caso,
        centraliza no monitor principal.
        """
        try:
            moldura = self.frameGeometry()
            for tela in QApplication.screens():
                if tela.availableGeometry().intersects(moldura):
                    return
            tela = QApplication.primaryScreen()
            if tela:
                self.resize(*TAMANHO_PADRAO)
                centro = tela.availableGeometry().center()
                moldura = self.frameGeometry()
                moldura.moveCenter(centro)
                self.move(moldura.topLeft())
        except Exception as e:
            log.debug("Não consegui centralizar a janela: %s", e)

    # ── abas ─────────────────────────────────────────────
    def _ao_trocar_aba(self, indice):
        w = self.abas.widget(indice)
        if w is self.aba_config:
            self.aba_config.recarregar()
        elif w is self.aba_cadastro:
            # As camadas do projeto mudam enquanto se desenha o imóvel;
            # a aba relê a lista sempre que volta ao primeiro plano.
            self.aba_cadastro.recarregar()
        elif w is self.aba_busca:
            # O token pode ter sido colado desde a última vez.
            self.aba_busca._atualizar_aviso_token()

    def _consultar_da_busca(self, codigo: str):
        self.abas.setCurrentWidget(self.aba_consulta)
        self.aba_consulta.campo.setText(codigo)
        self.aba_consulta.consultar()
