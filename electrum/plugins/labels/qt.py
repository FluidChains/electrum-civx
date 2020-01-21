from functools import partial
import traceback
import sys

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QVBoxLayout)

from electrum_exos.plugin import hook
from electrum_exos.i18n import _
from electrum_exos.gui.qt.util import ThreadedButton, Buttons, EnterButton, WindowModalDialog, OkButton

from .labels import LabelsPlugin


class QLabelsSignalObject(QObject):
    labels_changed_signal = pyqtSignal(object)


class Plugin(LabelsPlugin):

    def __init__(self, *args):
        LabelsPlugin.__init__(self, *args)
        self.obj = QLabelsSignalObject()

    def requires_settings(self):
        return True

    def settings_widget(self, window):

        wallet = window.parent().wallet

        return ThreadedButton("Synchronize",
                            partial(self.synchronize, wallet, True),
                            partial(self.done_processing_success, window),
                            partial(self.done_processing_error, window))

    def synchronize(self, wallet, force):
        self.pull(wallet, force)
        self.push(wallet)

    def on_pulled(self, wallet):
        self.obj.labels_changed_signal.emit(wallet)

    def done_processing_success(self, window, result):
        window.show_message(_('Your labels have been synchronised'))

    def done_processing_error(self, window, exc_info):
        self.logger.error("Error synchronising labels", exc_info=exc_info)
        window.show_message(_('Error synchronising labels') + f':\n{repr(exc_info[1])}')

    @hook
    def load_wallet(self, wallet, window):
        # FIXME if the user just enabled the plugin, this hook won't be called
        # as the wallet is already loaded, and hence the plugin will be in
        # a non-functional state for that window
        self.obj.labels_changed_signal.connect(window.update_tabs)
        self.start_wallet(wallet)

    @hook
    def on_close_window(self, window):
        try:
            self.obj.labels_changed_signal.disconnect(window.update_tabs)
        except TypeError:
            pass  # 'method' object is not connected
        self.stop_wallet(window.wallet)

    @hook
    def init_qt(self, gui):
        for window in gui.windows:
            self.on_new_window(window)

    @hook
    def on_new_window(self, window):
        self.update(window)

    def update(self, window):
        if not self.wallets:
            wallet = window.wallet
            self.load_wallet(wallet, window)
            self.pull(wallet, True)