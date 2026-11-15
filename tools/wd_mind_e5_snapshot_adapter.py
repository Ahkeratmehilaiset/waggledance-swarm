# SPDX-License-Identifier: BUSL-1.1
"""Opt-in data-format adapter for a caller-supplied supervised E5 client.

This module neither starts a client nor attaches itself to snapshot/query flows.
The caller owns the trusted client, source and binding selection, lifecycle,
serializer and policy. Hashes and response echoes are consistency checks, not
proof of model execution, client identity, sender authority or absence of races.
In particular, IPC sub-batches here are not source checkpoints: the existing
producer verifies its frozen source after its whole B16 callback returns.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import struct

from tools import wd_mind_content_query_readonly as reader

_ROW = struct.Struct('<384f')
_BATCH = 4
_MAX_PASSAGES = 16


class SnapshotAdapterRefused(ValueError):
    """Fixed refusal without caller text or provider exception details."""


def _run(operation):
    try:
        return operation()
    except Exception:
        pass
    raise SnapshotAdapterRefused('mind_e5_snapshot_adapter_refused') from None


class _Adapter:
    __slots__ = ('_client', '_binding_sha256')

    def __init__(self, client, binding_sha256):
        self._client = client
        self._binding_sha256 = binding_sha256

    def _request(self, operation, texts):
        nonce = secrets.token_hex(32)
        field = 'passages' if operation == 'refresh' else 'query'
        payload = {'schema': 'wd.mind-supervised-e5-request.v1', 'operation': operation,
                   'nonce': nonce, 'encoder_binding_sha256': self._binding_sha256,
                   field: list(texts) if operation == 'refresh' else texts[0]}
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                         separators=(',', ':'), allow_nan=False).encode('utf-8')
        digest = hashlib.sha256(raw).hexdigest()
        payload['request_sha256'] = digest
        response = self._client.encode_supervised(payload)
        if (type(response) is not dict or set(response) != {'nonce', 'request_sha256', 'vector_bytes'}
                or type(response['nonce']) is not str or not hmac.compare_digest(response['nonce'], nonce)
                or type(response['request_sha256']) is not str
                or not hmac.compare_digest(response['request_sha256'], digest)):
            raise ValueError('binding')
        vectors = response['vector_bytes']
        if type(vectors) is not bytes or len(vectors) != len(texts) * _ROW.size:
            raise ValueError('vector_bytes')
        for offset in range(0, len(vectors), _ROW.size):
            reader._q4_vector(_ROW.unpack_from(vectors, offset))
        return vectors

    def passage(self, texts):
        return _run(lambda: self._passage(texts))

    def _passage(self, texts):
        if (type(texts) not in (tuple, list) or not 1 <= len(texts) <= _MAX_PASSAGES
                or any(type(text) is not str or not text.startswith('passage: ')
                       or not text[9:].strip() or len(text) > 2009 or '\x00' in text for text in texts)):
            raise ValueError('passage')
        frozen = tuple(texts)
        chunks = []
        for start in range(0, len(frozen), _BATCH):
            chunks.append(self._request('refresh', frozen[start:start + _BATCH]))
        return b''.join(chunks)

    def query(self, text):
        return _run(lambda: self._query(text))

    def _query(self, text):
        if type(text) is not str or not 0 < len(text) <= 4000 or '\x00' in text or not text[:2000].strip():
            raise ValueError('query')
        raw = self._request('query', ('query: ' + text[:2000],))
        return _ROW.unpack(raw)


def create_adapter(*, enabled=False, client=None, encoder_binding_bytes=None, encoder_binding_sha256=None,
                   index_binding_bytes=None, index_binding_sha256=None):
    """Build only an adapter; callers must independently establish trust and own all I/O."""
    def create():
        if enabled is not True or client is None or isinstance(client, type):
            raise ValueError('disabled')
        encoder = reader._pinned(encoder_binding_bytes, encoder_binding_sha256)
        index = reader._pinned(index_binding_bytes, index_binding_sha256)
        reader._q4_bindings(encoder, index)
        if not callable(getattr(client, 'encode_supervised', None)):
            raise ValueError('client')
        return _Adapter(client, encoder_binding_sha256)
    return _run(create)
