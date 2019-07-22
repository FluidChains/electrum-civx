#!/usr/bin/env python
#
# EXOS-Electrum - lightweight Bitcoin client
# Copyright (C) 2012 karim.boucher@fluidchains.com
#
# Permission is hereby granted, free of charge, to any person
# obtaining a copy of this software and associated documentation files
# (the "Software"), to deal in the Software without restriction,
# including without limitation the rights to use, copy, modify, merge,
# publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so,
# subject to the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS
# BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN
# ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
# CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

import sys
import datetime
import copy
import traceback

from PyQt5.QtCore import QTimer

from PyQt5.QtWidgets import (QDialog, QLabel, QPushButton, QHBoxLayout, QVBoxLayout,
                             QTextEdit)

from electrum_exos import bip32, crypto
from electrum_exos.i18n import _
from electrum_exos.plugin import run_hook
from electrum_exos.util import bh2u
from electrum_exos.transaction import SerializationError
from electrum_exos.wallet import Multisig_Wallet

from electrum_exos.plugins.cosigner_pool import server	

from .util import (MessageBoxMixin, MONOSPACE_FONT, Buttons, ButtonsLineEdit)

dialogs = []

DURATION_INT = 60 * 10 

def show_timeout_wait_dialog(tx, parent, desc=None, prompt_if_unsaved=False):
    try:
        d = TimeoutWaitDialog(tx, parent, desc, prompt_if_unsaved)
    except SerializationError as e:
        traceback.print_exc(file=sys.stderr)
        parent.show_critical(_("EXOS-Electrum was unable to deserialize the transaction:") + "\n" + str(e))
    else:
        dialogs.append(d)
        d.show()
        return d

class TimeoutWaitDialog(QDialog, MessageBoxMixin):

    def __init__(self, tx, parent, desc, prompt_if_unsaved):
        '''Transactions in the wallet will show their description.
        Pass desc to give a description for txs not yet in the wallet.
        '''
        # We want to be a top-level window
        QDialog.__init__(self, parent=None)
        
        self.tx = tx = copy.deepcopy(tx)  # type: Transaction
        try:
            self.tx.deserialize()
        except BaseException as e:
            raise SerializationError(e)
        self.main_window = parent
        self.wallet = parent.wallet
        self.prompt_if_unsaved = prompt_if_unsaved
        self.saved = False
        self.desc = desc
        self.locks = {}

        # Set timeout flag 
        self.timed_out = False

        self.main_window = parent
        self.wallet = parent.wallet
        
        # store the keyhash and cosigners for current wallet
        self.keyhashes = set()
        self.cosigner_list = set()
        if type(self.wallet) == Multisig_Wallet:
            for key, keystore in self.wallet.keystores.items():
                xpub = keystore.get_master_public_key()
                K = bip32.deserialize_xpub(xpub)[-1]
                _hash = bh2u(crypto.sha256d(K))
                if not keystore.is_watching_only():
                    self.keyhashes.add(_hash)
                else:
                    self.cosigner_list.add(_hash)
                    self.locks[_hash] = server.get(_hash+'_lock')

        self.setMinimumWidth(200)
        self.setWindowTitle(_("Information"))

        vbox = QVBoxLayout()
        self.setLayout(vbox)
        self.warning = QLabel()
        vbox.addWidget(self.warning)
        warning_text = (_('A transaction with the following information is currently being signed')+'\n'+
                    _('by a cosigner. A notification will appear within 30 seconds of either signing') +'\n'+
                    _('or the transaction window expiring.'))
        self.warning.setText(warning_text)
        self.tx_desc = QLabel()
        vbox.addWidget(self.tx_desc)
        self.status_label = QLabel()
        vbox.addWidget(self.status_label)
        self.date_label = QLabel()
        vbox.addWidget(self.date_label)
        self.amount_label = QLabel()
        vbox.addWidget(self.amount_label)
        self.size_label = QLabel()
        vbox.addWidget(self.size_label)
        self.fee_label = QLabel()
        vbox.addWidget(self.fee_label)

        self.cancel_button = b = QPushButton(_("Close"))
        b.clicked.connect(self.close)
        b.setDefault(True)

        # Action buttons
        self.buttons = [self.cancel_button]

        # Add label for countdown timer
        self.time_out_label = QLabel()
        vbox.addWidget(self.time_out_label)

        run_hook('transaction_dialog', self)

        hbox = QHBoxLayout()
        hbox.addLayout(Buttons(*self.buttons))
        vbox.addLayout(hbox)

        for _hash, expire in self.locks.items():
            if expire:
                # Set time left to desired duration 
                self.time_left_int = int(DURATION_INT - (int(server.get_current_time()) - int(expire)))
        
        self.timer_start()
        self.update()

    def timer_start(self):

        self.my_qtimer = QTimer(self)
        self.my_qtimer.timeout.connect(self.timer_timeout)
        self.my_qtimer.start(1000)

        self.update()

    def timer_timeout(self):
        self.time_left_int -= 1

        if self.time_left_int == 0 and self.isVisible():
            self.timed_out = True
            self.close()

        self.update()

    def closeEvent(self, event):
        if (self.time_left_int <= 0):
            event.accept()
            try:
                dialogs.remove(self)
                return
            except ValueError:
                pass  # was not in list already

        if (self.prompt_if_unsaved and not self.saved
                and not self.question(_('Are you sure you want to close the waiting dialog?') +'\n'+
                                    _('Please restart your wallet to display again.'), title=_("Warning"))):
            event.ignore()
        else:
            event.accept()
            try:
                dialogs.remove(self)

            except ValueError:
                pass  # was not in list already

    def reject(self):
        # Override escape-key to close normally (and invoke closeEvent)
        self.close()

    def update(self):
        if self.time_left_int % 10 == 0:
            lock_present = False
            for _hash in self.cosigner_list:
                lock = server.get(_hash+'_lock')
                if lock:
                    lock_present = True
            if not lock_present:
                self.time_left_int = 0
                self.close()

        desc = None
        base_unit = self.main_window.base_unit()
        format_amount = self.main_window.format_amount
        tx_hash, status, label, can_broadcast, can_rbf, amount, fee, height, conf, timestamp, exp_n = self.wallet.get_tx_info(self.tx)
        size = self.tx.estimated_size()
        can_sign = not self.tx.is_complete() and \
            (self.wallet.can_sign(self.tx) or bool(self.main_window.tx_external_keypairs))
        if desc is None:
            self.tx_desc.hide()
        else:
            self.tx_desc.setText(_("Description") + ': ' + desc)
            self.tx_desc.show()
        self.status_label.setText(_('Status:') + ' ' + status)

        if timestamp:
            time_str = datetime.datetime.fromtimestamp(timestamp).isoformat(' ')[:-3]
            self.date_label.setText(_("Date: {}").format(time_str))
            self.date_label.show()
        elif exp_n:
            text = '%.2f MB'%(exp_n/1000000)
            self.date_label.setText(_('Position in mempool: {} from tip').format(text))
            self.date_label.show()
        else:
            self.date_label.hide()
        if amount is None:
            amount_str = _("Transaction unrelated to your wallet")
        elif amount > 0:
            amount_str = _("Amount received:") + ' %s'% format_amount(amount) + ' ' + base_unit
        else:
            amount_str = _("Amount sent:") + ' %s'% format_amount(-amount) + ' ' + base_unit
        size_str = _("Size:") + ' %d bytes'% size
        fee_str = _("Fee") + ': %s' % (format_amount(fee) + ' ' + base_unit if fee is not None else _('unknown'))
        if fee is not None:
            fee_rate = fee/size*1000
            fee_str += '  ( %s ) ' % self.main_window.format_fee_rate(fee_rate)
            confirm_rate = 99999
            if fee_rate > confirm_rate:
                fee_str += ' - ' + _('Warning') + ': ' + _("high fee") + '!'
        self.amount_label.setText(amount_str)
        self.fee_label.setText(fee_str)
        self.size_label.setText(size_str)

        # Set label for countdown timer and on update
        mins, secs = divmod(self.time_left_int, 60)
        timeformat = 'Time left: {:02d}:{:02d}'.format(mins, secs)
        #countdown = _("Time left") + ': %s' % (str(self.time_left_int))
        self.time_out_label.setText(timeformat)

        run_hook('transaction_dialog_update', self)