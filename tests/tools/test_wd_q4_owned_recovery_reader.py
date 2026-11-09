import base64
import hashlib
import json

import pytest

from tools import wd_q4_owned_recovery_reader as reader


def request():
    values = (b'{"synthetic":"tuple"}', b'{"synthetic":"receipt"}')
    return {'schema': 'wd.q4-owned-reader.v1', **{
        name + suffix: value for name, raw in zip(('tuple', 'receipt'), values)
        for suffix, value in (('_b64', base64.b64encode(raw).decode()),
                              ('_sha256', hashlib.sha256(raw).hexdigest()))}}


def test_reader_returns_only_bound_hashes():
    data = request()
    assert reader.inspect(data) == {'status': 'VERIFIED', 'tuple_sha256': data['tuple_sha256'],
                                    'receipt_sha256': data['receipt_sha256']}


@pytest.mark.parametrize('mutation', ['hash', 'extra', 'invalid', 'oversize'])
def test_reader_refuses_invalid_input(mutation):
    data = request()
    if mutation == 'hash': data['tuple_sha256'] = 'f' * 64
    if mutation == 'extra': data['path'] = 'unused'
    if mutation == 'invalid': data['receipt_b64'] = '!'
    if mutation == 'oversize': data['tuple_b64'] = base64.b64encode(b'x' * 65537).decode()
    with pytest.raises(ValueError): reader.inspect(data)
