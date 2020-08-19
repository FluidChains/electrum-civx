#!/usr/bin/env python
#
# Electrum - lightweight Bitcoin client
# Copyright (C) 2014 Thomas Voegtlin
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

from functools import partial

import calendar
import datetime
import time
import signal
import copy
import json

from http.client import CannotSendRequest

from xmlrpc.client import ServerProxy

from PyQt5.QtCore import Qt, QObject, pyqtSignal
from PyQt5.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout, QTextEdit, QGridLayout, QLineEdit

from electrum_exos import util, keystore, ecc, crypto
from electrum_exos import transaction
from electrum_exos.bip32 import BIP32Node
from electrum_exos.plugin import BasePlugin, hook, run_hook

from electrum_exos.i18n import _
from electrum_exos.wallet import Multisig_Wallet
from electrum_exos.util import bh2u, bfh

from electrum_exos.gui.qt.transaction_dialog import show_transaction, show_transaction_timeout, TxDialogTimeout
from electrum_exos.gui.qt.transaction_wait_dialog import show_timeout_wait_dialog, TimeoutWaitDialog
from electrum_exos.gui.qt.util import WaitingDialog, EnterButton, Buttons, WindowModalDialog, CloseButton, OkButton, read_QIcon

from . import server

import sys
import traceback

WAIT_TIME = 60 * 10 

class Listener(util.DaemonThread):

    def __init__(self, parent):
        util.DaemonThread.__init__(self)
        self.daemon = True
        self.parent = parent
        self.received = set()
        self.keyhashes = []
        self.wallet_hash = None

    def set_keyhashes(self, keyhashes):
        self.keyhashes = keyhashes

    def set_wallet_hash(self, wallet_hash):
        self.wallet_hash = wallet_hash

    def clear(self, keyhash):
        self.received.remove(keyhash)

    def run(self):
        while self.running:
            if not self.keyhashes:
                time.sleep(2)
                continue
            for keyhash in self.keyhashes:
                if keyhash in self.received:
                    continue
                try:
                    lock = server.lock
                    if lock:
                        continue
                    data = server.get(self.wallet_hash)
                except Exception as e:
                    self.logger.info("cannot contact cosigner pool")
                    time.sleep(30)
                    continue
                if data:
                    self.received.add(keyhash)
                    self.logger.info(f"received data for {keyhash}")
                    self.parent.obj.cosigner_receive_signal.emit(
                        keyhash, data)
            # poll every 30 seconds
            time.sleep(30)


class QReceiveSignalObject(QObject):
    cosigner_receive_signal = pyqtSignal(object, object)


class Plugin(BasePlugin):

    def __init__(self, parent, config, name):
        BasePlugin.__init__(self, parent, config, name)
        self.listener = None
        self.obj = QReceiveSignalObject()
        self.obj.cosigner_receive_signal.connect(self.on_receive)
        self.keys = []
        self.cosigner_list = []
        self._init_qt_received = False
        self.signed = []

    @hook
    def init_qt(self, gui):
        if self._init_qt_received:  # only need/want the first signal
            return
        self._init_qt_received = True
        for window in gui.windows:
            self.on_new_window(window)

    @hook
    def on_new_window(self, window):
        self.update(window)

    @hook
    def on_close_window(self, window):
        self.update(window)

    def requires_settings(self):
        return True

    def settings_widget(self, window):
        return EnterButton(_('Settings'),
                           partial(self.calibration_dialog, window))

    def calibration_dialog(self, window):
        d = WindowModalDialog(window, _("Cosigner Pool Settings"))

        d.setMinimumSize(150, 100)

        vbox = QVBoxLayout(d)

        purge = QPushButton(_("Purge Transactions"))
        purge.clicked.connect(partial(self.purge_transaction, window))
        purge.setIcon(read_QIcon("warning.png"))
        vbox.addWidget(purge)

        vbox.addWidget(QLabel(_('Wallet Owner:')))
        grid = QGridLayout()
        vbox.addLayout(grid)

        grid.addWidget(QLabel(_('Name:')), 0, 0)
        name = QLineEdit()
        name.setText(self.config.get('wallet_owner', ''))
        grid.addWidget(name, 0, 1)

        sync = QPushButton(_("Sync Name"))
        sync.clicked.connect(partial(self.sync_name, name, window))
        vbox.addWidget(sync)

        status = QPushButton(_("Tx. Status"))
        status.clicked.connect(partial(self.tx_status_dialog, window))
        vbox.addWidget(status)

        vbox.addStretch()
        vbox.addSpacing(13)
        vbox.addLayout(Buttons(CloseButton(d), OkButton(d)))

        if not d.exec_():
            return

    def purge_transaction(self, window):
        if not window.question(_("Purging you transactions will erase the current transaction") + '\n' +
                        _("Are you sure you want to purge your transaction?")):
            return
        
        server.delete(self.wallet_hash)
        del server.lock
        
        window.show_message(_("Your transactions have been purged."))

    def sync_name(self, name, window):
        self.config.set_key('wallet_owner', name.text())
        if self.config.get('wallet_owner', ''):
            for key, _hash, window in self.keys:
                server.put(_hash+'_name', self.config.get('wallet_owner', ''))
        for key, _hash, window in self.keys:
            if self.config.get('wallet_owner', '') == server.get(_hash+'_name'):
                window.show_message(_("Your name has been synced"))
            else:
                window.show_message(_("Failed to sync name with cosigner pool"))


    def tx_status_dialog(self, window):
        d = WindowModalDialog(window, _("Transaction Status"))

        d.setMinimumSize(600, 300)

        vbox = QVBoxLayout(d)

        status_header = 'No transaction in progress'
        status = ''

        data = server.get(self.wallet_hash)
        loads = json.loads(data) if data else {}
        lock = server.lock

        locked_by = lock.get('xpub') if lock else None
        signed_by = loads.get('signed', [])

        for _hash in server.cosigners():
            name = server.get(_hash+'_name')
            if not name or len(name) < 1:
                name = _hash[0:10] + '...' + _hash[-1:-5:-1]

            signed = True if _hash in signed_by else False
            signing = True if _hash == locked_by else False

            if signed or signing:
                status_header = 'Transaction in progress'
            if signed:
                message = 'Signed'
            elif signing: 
                message = 'Signing'
            else:
                message = 'Not signed'

            status += f'<br><br> {name}: <b>{message}</b>'

        for _hash in server.xpub():
            signed = True if _hash in signed_by else False
            signing = True if _hash == locked_by else False
            if signed or signing:
                status_header = 'Transaction in progress'
            if signed:
                message = 'Signed'
            elif signing: 
                message = 'Signing'
            else:
                message = 'Not signed'
            status += f'<br><br> You: <b>{message}</b>'

        self.tx_status = QLabel()
        vbox.addWidget(self.tx_status)
        self.tx_status.setTextFormat(Qt.RichText)
        self.tx_status.setText(_("<b>Transaction Status</b>") + ': ' + status_header + status)
        self.tx_status.show()

        vbox.addStretch()
        vbox.addSpacing(13)
        vbox.addLayout(Buttons(CloseButton(d)))
        
        if not d.exec_():
            return

    def is_available(self):
        return True

    def update(self, window):
        wallet = window.wallet
        if type(wallet) != Multisig_Wallet:
            return
        if self.listener is None:
            self.logger.info("starting listener")
            self.listener = Listener(self)
            self.listener.start()
        elif self.listener:
            self.logger.info("shutting down listener")
            self.listener.stop()
            self.listener = None
        self.keys = []
        self.cosigner_list = []
        for key, keystore in wallet.keystores.items():
            xpub = keystore.get_master_public_key()
            pubkey = BIP32Node.from_xkey(xpub).eckey.get_public_key_bytes(compressed=True)
            _hash = bh2u(crypto.sha256d(pubkey))
            if not keystore.is_watching_only():
                self.keys.append((key, _hash, window))
                server.xpub(_hash)
            else:
                self.cosigner_list.append((window, xpub, pubkey, _hash))
                server.cosigners().append(_hash)
        self.wallet_hash = server.wallet_hash()
        if self.listener:
            self.listener.set_keyhashes([t[1] for t in self.keys])
            self.listener.set_wallet_hash(self.wallet_hash)

    @hook
    def transaction_dialog(self, d):
        d.cosigner_send_button = b = QPushButton(_("Send to cosigner"))
        b.clicked.connect(lambda: self.do_send(d.tx))
        d.buttons.insert(0, b)
        self.transaction_dialog_update(d)

    @hook
    def transaction_dialog_update(self, d):
        if d.tx.is_complete() or d.wallet.can_sign(d.tx):
            d.cosigner_send_button.hide()
            return
        for window, xpub, K, _hash in self.cosigner_list:
            if window.wallet == d.wallet and self.cosigner_can_sign(d.tx, xpub):
                d.cosigner_send_button.show()
                break
        else:
            d.cosigner_send_button.hide()

    def cosigner_can_sign(self, tx, cosigner_xpub):
        from electrum.keystore import is_xpubkey, parse_xpubkey
        xpub_set = set([])
        for txin in tx.inputs():
            for x_pubkey in txin['x_pubkeys']:
                if is_xpubkey(x_pubkey):
                    xpub, s = parse_xpubkey(x_pubkey)
                    xpub_set.add(xpub)
        return cosigner_xpub in xpub_set

    def do_send(self, tx):
        def on_success(result):
            window.show_message(_("Your transaction was sent to the cosigning pool.") + '\n' +
                                _("Open your cosigner wallet to retrieve it."))
        def on_failure(exc_info):
            e = exc_info[1]
            try: self.logger.error("on_failure", exc_info=exc_info)
            except OSError: pass
            window.show_error(_("Failed to send transaction to cosigning pool") + ':\n' + str(e))

        buffer = {'signed': [], 'txs': {}}
        some_window = None
        # construct messages
        for window, xpub, K, _hash in self.cosigner_list:
            if not self.cosigner_can_sign(tx, xpub):
                continue
            some_window = window
            raw_tx_bytes = bfh(str(tx))
            public_key = ecc.ECPubkey(K)
            message = public_key.encrypt_message(raw_tx_bytes).decode('ascii')
            buffer['txs'].update({_hash : message})
        if not buffer:
            return
        # construct signed
        buffer['signed'] = self.signed
        for key, _hash, window in self.keys:
            buffer['signed'].append(_hash)

        # send message
        def send_messages_task():
            server.put(self.wallet_hash, json.dumps(buffer))

        msg = _('Sending transaction to cosigning pool...')
        WaitingDialog(window, msg, send_messages_task, on_success, on_failure)

    def on_receive(self, keyhash, data):
        self.logger.info(f"signal arrived for {keyhash}")
        for key, _hash, window in self.keys:
            if _hash == keyhash:
                break
        else:
            self.logger.info("keyhash not found")
            return

        # convert data from string to json
        data = json.loads(data)

        signed = data.get('signed')
        txs = data.get('txs')

        # invalid JSON structure
        if not signed or not txs:
            self.logger.info("cosigner data malformed, missing entry")
            return
        
        # check if user has signed
        if keyhash in signed:
            self.logger.info("user has already signed")
            return

        self.signed = signed
        message = txs[keyhash]

        wallet = window.wallet
        if isinstance(wallet.keystore, keystore.Hardware_KeyStore):
            window.show_warning(_('An encrypted transaction was retrieved from cosigning pool.') + '\n' +
                                _('However, hardware wallets do not support message decryption, '
                                  'which makes them not compatible with the current design of cosigner pool.'))
            return
        elif wallet.has_keystore_encryption():
            password = window.password_dialog(_('An encrypted transaction was retrieved from cosigning pool.') + '\n' +
                                              _('Please enter your password to decrypt it.'))
            if not password:
                return
        else:
            password = None
            if not window.question(_("An encrypted transaction was retrieved from cosigning pool.") + '\n' +
                                   _("Do you want to open it now?")):
                return

        xprv = wallet.keystore.get_master_private_key(password)
        if not xprv:
            return
        try:
            privkey = BIP32Node.from_xkey(xprv).eckey
            message = bh2u(privkey.decrypt_message(message))
        except Exception as e:
            self.logger.exception('')
            window.show_error(_('Error decrypting message') + ':\n' + str(e))
            return

        tx = transaction.Transaction(message)

        def calculate_wait_time(expire):
            # calculate wait time
            server_time = server.get_current_time()
            mins, secs = 0, 0
            if server_time is not None:
                wait_time = int((WAIT_TIME - (int(server_time) - int(expire))))
                mins, secs = divmod(wait_time, 60)
            return '{:02d}:{:02d}'.format(mins, secs)

        # check if lock has been placed for wallet
        expire = server.lock
        if expire:
            # calculate wait time based on lock expiry and server time
            timeformat = calculate_wait_time(expire)
            # display pop up
            window.show_warning(_("A cosigner is currently signing the transaction.") + '\n' +
                                _("Please wait {} until the signing has concluded.".format(timeformat)))

            show_timeout_wait_dialog(tx, window, prompt_if_unsaved=True)
            return
        else:
            buffer = {'timestamp' : '', 'xpub' : ''}
            buffer['timestamp'] = str(server.get_current_time())
            buffer['xpub'] = keyhash
            server.lock = buffer
            time_until_expired = '10 minutes'

        window.show_warning(_("You have {} to conclude signing after which the dialog will".format(time_until_expired)) + '\n' +
                            _("automatically close."))
            
        self.listener.clear(keyhash)
        show_transaction_timeout(tx, window, prompt_if_unsaved=True)