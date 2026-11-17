"""Synthetic feasibility prototype only; not pinned or wired to the runtime.

This module does not prove a caller cannot bypass the passage contract. Runtime
identity must remain in the real cache key until such a bypass is prevented.
"""
import hashlib
import json


def passage(record, decode):
    if type(record) is not dict or type(record.get('record_id')) is not str:
        raise ValueError('record')
    text = decode(record['stored_text'])
    if type(text) is not str:
        raise ValueError('decoded_text')
    return 'passage: ' + text[:2000]


def record_keys(record, decode):
    data = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                      allow_nan=False).encode('utf-8')
    content = passage(record, decode).encode('utf-8')
    return hashlib.sha256(data).hexdigest(), hashlib.sha256(content).hexdigest()


def pair_vectors(records, vectors, decode):
    if type(records) is not tuple or type(vectors) is not tuple or len(records) != len(vectors):
        raise ValueError('pairs')
    result = []
    seen = set()
    for record, vector in zip(records, vectors):
        keys = record_keys(record, decode)
        if keys[0] in seen or type(vector) is not bytes or len(vector) != 1536:
            raise ValueError('pairs')
        seen.add(keys[0])
        result.append((*keys, vector))
    return tuple(result)
