from electrum_exos.i18n import _

from xmlrpc.client import ServerProxy
from .proto.rpc import Cosigner

fullname = _('Cosigner Pool')
description = ' '.join([
    _("This plugin facilitates the use of multi-signatures wallets."),
    _("It sends and receives partially signed transactions from/to your cosigner wallet."),
    _("Transactions are encrypted and stored on a remote server.")
])
#requires_wallet_type = ['2of2', '2of3']
available_for = ['qt']

server = Cosigner('cosigner.exos.to', '443')
