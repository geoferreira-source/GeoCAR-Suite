# -*- coding: utf-8 -*-
"""
plugin.py — registro do plugin no QGIS.

Responsabilidade única: criar a ação no menu/barra, abrir a janela e
limpar tudo no unload. Nenhuma lógica de negócio aqui — isso fica em
core/, e a interface em gui/.
"""

import logging
import os

from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import QAction

log = logging.getLogger("GeoCAR_Suite")

MENU = "&GeoCAR Suite"


class GeoCARSuite:
    def __init__(self, iface):
        self.iface = iface
        self.acao = None
        self.janela = None

    def initGui(self):  # noqa: N802 (nome exigido pelo QGIS)
        self.acao = QAction(
            QIcon(self._icone()), "GeoCAR Suite", self.iface.mainWindow()
        )
        self.acao.setCheckable(True)
        self.acao.setStatusTip("Consultar imóveis do CAR")
        self.acao.triggered.connect(self.alternar)

        self.iface.addToolBarIcon(self.acao)
        self.iface.addPluginToMenu(MENU, self.acao)

    def unload(self):
        # A janela não tem pai (ver gui/janela.py), então o QGIS não a
        # destrói sozinho: se não fecharmos aqui, ela sobra na tela ao
        # desativar o plugin ou ao sair.
        if self.janela is not None:
            try:
                self.janela.fechou.disconnect(self._ao_fechar_janela)
            except (TypeError, RuntimeError):
                pass
            self.janela.close()
            self.janela.deleteLater()
            self.janela = None
        if self.acao is not None:
            self.iface.removePluginMenu(MENU, self.acao)
            self.iface.removeToolBarIcon(self.acao)
            self.acao = None

    def alternar(self, marcado):
        if marcado:
            self._janela().abrir()
        elif self.janela is not None:
            self.janela.close()

    def _janela(self):
        if self.janela is None:
            from .gui.janela import JanelaGeoCAR
            self.janela = JanelaGeoCAR(self.iface, icone=self._icone())
            # Fechar pelo X ou com Esc precisa desmarcar o botão da
            # barra, senão o próximo clique "desliga" algo já fechado.
            self.janela.fechou.connect(self._ao_fechar_janela)
        return self.janela

    def _ao_fechar_janela(self):
        if self.acao is not None:
            self.acao.setChecked(False)

    def _icone(self) -> str:
        caminho = os.path.join(os.path.dirname(__file__), "icon.png")
        return caminho if os.path.exists(caminho) else ""
