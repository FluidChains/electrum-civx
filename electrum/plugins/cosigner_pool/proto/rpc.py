import logging
import os
import ssl
import certifi
import hashlib
import itertools
import json

import grpc

from . import cosignerpool_pb2_grpc
from . import cosignerpool_pb2

logger = logging.getLogger(__name__)

class XPubNotSetException(Exception):
    pass

class CosignersNotSetException(Exception):
    pass

class gRPCServer:
    
    def __init__(self, host, port):
        if os.path.exists('/private/etc/ssl/cert.pem'):
            cafile = '/private/etc/ssl/cert.pem'
        else:
            cafile = certifi.where()

        with open(cafile, 'rb') as f:
            trusted_certs = f.read()

        credentials = grpc.ssl_channel_credentials(root_certificates=trusted_certs)
        channel = grpc.secure_channel('{}:{}'.format(host, port), credentials)
        self.stub = cosignerpool_pb2_grpc.CosignerpoolStub(channel)

    def put(self, key, value, expiration=0):
        try:
            resp = self.stub.Put(cosignerpool_pb2.PutRequest(Key=key, Value=value, Expiration=expiration))
            logger.debug(f'gRPC: PUT:: {key}, {value}')
            return bool(resp.Success)
        except grpc.RpcError as e:
            logger.error(f'gRPC: PUT:: {key}, {e.code()} ({e.details()})')
    
    def get(self, key):
        try:
            resp = self.stub.Get(cosignerpool_pb2.GetRequest(Key=key))
            logger.debug(f'gRPC: GET:: {key}, {resp.Value}')
            return str(resp.Value)
        except grpc.RpcError as e:
            logger.error(f'gRPC: GET:: {key}, {e.code()} ({e.details()})')

    def delete(self, key):
        try:
            resp = self.stub.Delete(cosignerpool_pb2.DeleteRequest(Key=key))
            logger.debug(f'gRPC: DEL:: {key}, {resp.Success}')
            return bool(resp.Success)
        except grpc.RpcError as e:
            logger.error(f'gRPC: DEL:: {key}, {e.code()} ({e.details()})')

    def ping(self):
        try:
            resp = self.stub.Ping(cosignerpool_pb2.Empty())
            return response.Message
        except grpc.RpcError as e:
            logger.error(f"gRPC PING: {e.code()} : {e.details()}")
    
    def get_current_time(self):
        try:
            resp = self.stub.GetTime(cosignerpool_pb2.Empty())
            return resp.Timestamp
        except grpc.RpcError as e:
            logger.error(f"gRPC TIME: {e.code()} : {e.details()}")
            
class Cosigner(gRPCServer):

    __lock = None
    __xpub = None
    __cosigners = []
    __wallet_hash = None

    def __init__(self, host, port):
        gRPCServer.__init__(self, host, port)

    @classmethod
    def _lock(cls, lock=None):
        if lock is None:
            return cls.__lock
        cls.__lock = lock
        return cls.__lock

    @classmethod
    def xpub(cls, xpub=None):
        if xpub is None:
            return cls.__xpub
        cls.__xpub = [xpub]
        return cls.__xpub

    @classmethod
    def cosigners(cls, cosigners=None):
        if cosigners is None:
            return cls.__cosigners
        cls.__cosigners = cosigners
        return cls.__cosigners
 
    @classmethod
    def wallet_hash(cls):
        if cls.__wallet_hash is not None:
            return cls.__wallet_hash
        if cls.__xpub is None:
            raise XPubNotSetException("xPub needs to be set")
        if cls.__cosigners is None:
            raise CosignersNotSetException("Cosigners need to be set")
        cls.__wallet_hash = sha1_lists(cls.__xpub, cls.__cosigners)
        logger.info(f'Wallet Hash: {cls.__wallet_hash}')
        print(f'Wallet Hash: {cls.__wallet_hash}')
        return cls.__wallet_hash

    @property
    def lock(self):
        raw = self.get(self.wallet_hash() + '_lock')
        if not raw:
            return None
        return json.loads(raw)

    @lock.setter
    def lock(self, value):
        dumps = json.dumps(value)
        return self.put(self.wallet_hash() + '_lock', dumps)

    @lock.deleter
    def lock(self):
        return self.delete(self.wallet_hash() + '_lock')

def sha1_lists(*args):
    assert all(isinstance(x, list) for x in args)  # check all *args are of type 'list'
    keys = list(itertools.chain(*args))            # combine all *args (list) into 1 'list'
    keys_set = {x for x in keys}                   # create 'set' (all entries are unique)
    set_sorted = sorted(keys_set)                  # sort the 'set'
    s = "|".join(set_sorted).encode('utf-8')       # all keys into 1 'string' ('|' as delim)
    return hashlib.sha1(s).hexdigest()             # calculate sha1 hash
