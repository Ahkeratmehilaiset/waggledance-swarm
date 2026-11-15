# SPDX-License-Identifier: BUSL-1.1
"""Opt-in retained float32 retrieval; index equivalence is not asserted.

Caller owns trusted inputs, source binding and provider lifecycle.
"""
import hashlib
import hmac
import json
import math
import re
import struct
from collections import Counter
from tools import wd_mind_content_query_readonly as r

def _cell_controls(cell_id, item, bound, memory, records, *, complete_limits=None):
    complete = complete_limits is not None
    artifact_limit = (r._Q4_BOUNDED_V3_ARTIFACT_BYTES
                      if memory["scope"] == r._ALL_TASK_ROUTE_SCOPE else r._Q4_ARTIFACT_BYTES)
    if complete:
        wanted = sum(1 for record in records.values() if r._Q4_PLACEMENT.get(record["meta"]["type"]) == cell_id) * 2
        if (type(complete_limits) is not dict
                or set(complete_limits) != {"count", "meta", "provenance", "index", "vectors"}
                or any(type(value) is not int or value <= 0 for value in complete_limits.values())
                or complete_limits["count"] != wanted or wanted <= 0
                or complete_limits["vectors"] != wanted * r._Q4_DIM * 4
                or complete_limits["index"] != complete_limits["vectors"] + 65536):
            raise ValueError("invalid_complete_limits")
        artifact_limit = max(complete_limits["meta"], complete_limits["provenance"])
    if (type(item) is not dict or set(item) != {"pointer", "log", "artifacts"}
            or type(bound) is not dict or type(bound.get("generation")) is not int
            or bound["generation"] < 0 or not r._hex(bound.get("receipt_sha256"))
            or not set(bound) <= {"generation", "seq", "receipt_sha256", "count"}):
        raise ValueError("invalid_cell")
    pointer_bytes, log_bytes, data = item["pointer"], item["log"], item["artifacts"]
    if (type(pointer_bytes) is not bytes or not 0 < len(pointer_bytes) <= 65536
            or type(log_bytes) is not bytes or not 0 < len(log_bytes) <= r._Q4_ARTIFACT_BYTES
            or type(data) is not dict
            or set(data) != {"receipt", "meta", "provenance", "vectors"}
            
            or any(type(raw) is not bytes for raw in data.values())):
        raise ValueError("invalid_cell_bytes")
    data = dict(data)  # Capture immutable bytes before any provider is invoked.
    pointer = json.loads(pointer_bytes, object_pairs_hook=r._unique_pairs, parse_constant=r._bad_constant)
    if (type(pointer) is not dict or set(pointer) != {"generation", "seq", "receipt_sha256"}
            or type(pointer["generation"]) is not int or pointer["generation"] != bound["generation"]
            or type(pointer["seq"]) is not int or pointer["seq"] < 1
            or pointer["receipt_sha256"] != bound["receipt_sha256"]):
        raise ValueError("invalid_cell_pointer")
    if "seq" in bound and (type(bound["seq"]) is not int or bound["seq"] != pointer["seq"]):
        raise ValueError("cell_sequence_mismatch")
    lines = log_bytes.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if not lines or len(lines) > 100000:
        raise ValueError("invalid_cell_log")
    previous = "0" * 64
    found = None
    for number, raw in enumerate(lines, 1):
        line = json.loads(raw, object_pairs_hook=r._unique_pairs, parse_constant=r._bad_constant)
        if (type(line) is not dict or line.get("schema") != "wd.bridge-mind-hex-cell-log.v1"
                or type(line.get("seq")) is not int or line["seq"] != number
                or line.get("prev_line_sha256") != previous):
            raise ValueError("invalid_cell_log")
        previous = r._sha(raw)
        if number == pointer["seq"]:
            found = line
    if (found is None or type(found.get("generation")) is not int
            or found["generation"] != pointer["generation"]
            or found.get("receipt_sha256") != pointer["receipt_sha256"]):
        raise ValueError("cell_log_binding_mismatch")
    receipt = r._pinned(data["receipt"], bound["receipt_sha256"], limit=1024 * 1024)
    content = receipt.get("content") if type(receipt) is dict else None
    if (type(receipt) is not dict or receipt.get("schema") != "wd.bridge-mind-hex-cell.v1"
            or receipt.get("scope") != memory["scope"] or type(content) is not dict
            or receipt.get("content_sha256") != r._sha(r._canonical(content))
            or content.get("cell_id") != cell_id or type(content.get("count")) is not int
            or not 0 < content["count"] <= (complete_limits["count"] if complete else
                r._Q4_BOUNDED_V3_MAX_ROWS if memory["scope"] == r._ALL_TASK_ROUTE_SCOPE else r._Q4_MAX_ROWS)
            or content["count"] % 2
            or type(content.get("dim")) is not int or content["dim"] != r._Q4_DIM
            or content.get("encoder") != memory["encoder"]
            or content.get("projection_schema") != memory["projection_schema"]
            or content.get("projection_sha256") != memory["projection_sha256"]
            or not r._hex(content.get("ids_sha256")) or not r._hex(content.get("pairs_sha256"))
            or type(content.get("files")) is not dict):
        raise ValueError("invalid_cell_receipt")
    if "count" in bound and (type(bound["count"]) is not int or bound["count"] != content["count"]):
        raise ValueError("cell_count_mismatch")
    if content.get("cutoff_utc") != memory.get("cutoff_utc"):
        raise ValueError("cell_cutoff_mismatch")
    files = content["files"]
    if (not {"index", "meta", "provenance"} <= set(files)
            or not set(files) <= {"index", "meta", "provenance", "vectors"}
            or any(not r._hex(pin) for pin in files.values())
            or "vectors" not in files):
        raise ValueError("invalid_cell_artifacts")
    for name in ("meta", "provenance", "vectors"):
        if name in data:
            if name not in files or not hmac.compare_digest(r._sha(data[name]), files[name]):
                raise ValueError("cell_artifact_mismatch")
            if name in ("meta", "provenance") and len(data[name]) > artifact_limit:
                raise ValueError("cell_artifact_over_budget")
    count = content["count"]
    if complete and (count != complete_limits["count"]
                     or len(data["meta"]) != complete_limits["meta"]
                     or len(data["provenance"]) != complete_limits["provenance"]
                     or "vectors" not in data or len(data["vectors"]) != complete_limits["vectors"]):
        raise ValueError("complete_cell_size_mismatch")
    if memory["scope"] == r._ALL_TASK_ROUTE_SCOPE:
        # Valid JSONL has at most one CRLF (or CR/LF) separator per row.
        # Check before splitlines can allocate a list for millions of blanks.
        if data["provenance"].count(b"\n") + data["provenance"].count(b"\r") > 2 * count:
            raise ValueError("cell_json_line_budget")
        for name in ("meta", "provenance"):
            r._q4_json_budget(data[name], 16 + r._Q4_V3_JSON_NODES_PER_ROW * count)
    meta = r._pinned(data["meta"], files["meta"], limit=artifact_limit)
    if (type(meta) is not dict or set(meta) != {"dim", "doc_ids", "texts", "metadata"}
            or type(meta["dim"]) is not int or meta["dim"] != r._Q4_DIM
            or any(type(meta[name]) is not list or len(meta[name]) != count
                   for name in ("doc_ids", "texts", "metadata"))
            or any(type(value) is not str for value in meta["doc_ids"])
            or len(set(meta["doc_ids"])) != count
            or content["ids_sha256"] != r._sha("\n".join(meta["doc_ids"]).encode())
            or any(type(value) is not dict for value in meta["metadata"])):
        raise ValueError("invalid_cell_meta")
    raw_rows = data["provenance"].splitlines()
    if len(raw_rows) != count:
        raise ValueError("invalid_cell_rows")
    rows, seen = [], set()
    cutoff = r._utc(content.get("cutoff_utc"))
    for position, raw in enumerate(raw_rows):
        if memory["scope"] == r._ALL_TASK_ROUTE_SCOPE:
            # Match the preflight's encoding; never let the parser auto-detect
            # a different encoding from these immutable bytes.
            raw = raw.decode("utf-8")
        row = json.loads(raw, object_pairs_hook=r._unique_pairs, parse_constant=r._bad_constant)
        provenance = row.get("provenance") if type(row) is dict else None
        if (type(row) is not dict or type(provenance) is not dict or row.get("kind") != "event"
                or row.get("cell_id") != cell_id or row.get("record_id") != meta["doc_ids"][position]
                or r._utc(row.get("time_utc")) >= cutoff):
            raise ValueError("invalid_cell_row")
        record_id, view = provenance.get("record"), provenance.get("view")
        record = records.get(record_id) if type(record_id) is str else None
        if (record is None or view not in ("w", "d") or (record_id, view) in seen
                or row["record_id"] != record_id + "#" + view
                or row["time_utc"] != record["time_utc"]
                or r._Q4_PLACEMENT.get(record["meta"]["type"]) != cell_id
                or meta["texts"][position] != r.decode_text(record["stored_text"])
                or provenance != dict(record["provenance"], record=record_id, view=view,
                                      ref_kind="sha256_of_projected_metadata", canonical_event=False)):
            raise ValueError("cell_source_binding_mismatch")
        seen.add((record_id, view))
        rows.append((record_id, view))
    if any((record_id, other) not in seen for record_id, _ in seen for other in ("w", "d")):
        raise ValueError("cell_view_missing")
    if "vectors" in data and len(data["vectors"]) != count * r._Q4_DIM * 4:
        raise ValueError("invalid_retained_vectors")
    return data, content, meta, rows


class _CheckedQueryCache:
    """Reuse one validated provider result for both source-pinned rankers."""
    __slots__ = ('_encoder', '_checkpoint', '_text', '_vector')

    def __init__(self, encoder, checkpoint):
        self._encoder, self._checkpoint = encoder, checkpoint
        self._text, self._vector = None, None

    def query(self, text):
        if self._vector is None:
            raw = self._encoder.query(text)
            self._checkpoint()
            self._vector = r._q4_vector(raw)
            self._text = text
        elif text != self._text:
            raise ValueError('query_changed')
        return self._vector


def _fresh_records(records, when, maximum_age):
    """Use the beginning of each source-verified minute as the age upper bound."""
    fresh = {}
    for key, record in records.items():
        stamp = record['time_utc']
        if type(stamp) is not str or not re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:00(?:\.0{1,6})?Z', stamp):
            raise ValueError('invalid_event_minute')
        floor = r._utc(stamp)
        delta = when - floor
        micros = (delta.days * 86400 + delta.seconds) * 1000000 + delta.microseconds
        upper = (micros + 999999) // 1000000
        if (micros >= 0 and upper <= maximum_age and upper < 900
                and r._utc(record['available_utc']) <= when):
            fresh[key] = record
    return fresh


def _small_fresh_candidates(records, entries, encoder, query, when):
    """Opt-in candidate-only fallback for 1–3 fresh records, never a Q4 answer."""
    if not records:
        return []
    texts = {key: r.decode_text(record['stored_text']) for key, record in records.items()}
    eligible = []
    for key in sorted(records):
        provenance = records[key]['provenance']
        if (r._utc(provenance['available_utc']) >= when - r.dt.timedelta(seconds=1)
                or r._q4_self_match(query, texts[key])
                or ('valid_until_utc' in provenance and when >= r._utc(provenance['valid_until_utc']))):
            continue
        eligible.append(key)
    if not eligible:
        return []
    vector = r._q4_vector(encoder.query(query))
    scores = {view: {key: sum(a * b for a, b in zip(struct.unpack('<384d', entries[key][view]), vector))
                     for key in eligible} for view in ('w', 'd')}
    ranks = {view: {key: i for i, key in enumerate(
        sorted(eligible, key=lambda key: (-scores[view][key], key)), 1)} for view in ('w', 'd')}
    tokens = {key: Counter(r._Q4_TOKEN.findall(texts[key].lower())) for key in sorted(records)}
    lengths = {key: sum(value.values()) for key, value in tokens.items()}
    average = sum(lengths.values()) / len(records)
    lexical = {key: 0.0 for key in eligible}
    for token in sorted(set(r._Q4_TOKEN.findall(query.lower()))):
        frequency = sum(token in tokens[key] for key in records)
        if not frequency:
            continue
        idf = math.log(1.0 + (len(records) - frequency + 0.5) / (frequency + 0.5))
        for key in eligible:
            count = tokens[key][token]
            if count:
                norm = count + 1.2 * (1.0 - 0.75 + 0.75 * lengths[key] / average)
                lexical[key] += idf * count * 2.2 / norm
    lexical_rank = {key: i for i, key in enumerate(sorted(eligible, key=lambda k: (-lexical[k], k)), 1)}
    candidates = [key for key in eligible if all(scores[view][key] >= 0.55 for view in ('w', 'd'))]
    return sorted(candidates, key=lambda key: (
        -(sum(1.0 / (60 + ranks[view][key]) for view in ('w', 'd'))
          + (1.0 / (60 + lexical_rank[key]) if lexical[key] > 0.0 else 0.0)), key))[:3]


def _fresh_eligible_count(records, query, when):
    return sum(1 for record in records.values()
        if r._utc(record['provenance']['available_utc']) < when - r.dt.timedelta(seconds=1)
        and not r._q4_self_match(query, r.decode_text(record['stored_text']))
        and ('valid_until_utc' not in record['provenance']
             or when < r._utc(record['provenance']['valid_until_utc'])))


def _query(values):
    expected_keys = r._Q4_SOURCE_KEYS | {
        'enabled', 'descriptor_bytes', 'descriptor_sha256', 'memory_bytes', 'memory_sha256',
        'encoder_sha256', 'runtime_revision', 'encoder_binding_bytes', 'encoder_binding_sha256',
        'encoder_provider', 'cell_snapshots', 'query_bytes'}
    if set(values) != expected_keys or values['enabled'] is not True:
        raise ValueError('invalid_inputs')
    controls = {key: values[key] for key in expected_keys - r._Q4_SOURCE_KEYS
                if key not in ('encoder_provider', 'cell_snapshots')}
    if type(values['cell_snapshots']) is not dict:
        raise ValueError('invalid_cells')
    frozen_cells = {}
    for name, item in values['cell_snapshots'].items():
        if type(item) is not dict or type(item.get('artifacts')) is not dict:
            raise ValueError('invalid_cells')
        frozen_cells[name] = dict(item, artifacts=dict(item['artifacts']))
    snap = {key: values[key] for key in r._Q4_SOURCE_KEYS}
    if type(snap.get('messages')) is not dict:
        raise ValueError('invalid_snapshot')
    snap['messages'] = dict(snap['messages'])
    projection, _, report = r._verified_complete(snap)
    descriptor = r._pinned(values['descriptor_bytes'], values['descriptor_sha256'])
    memory = r._pinned(values['memory_bytes'], values['memory_sha256'], limit=1024 * 1024)
    binding = r._pinned(values['encoder_binding_bytes'], values['encoder_binding_sha256'])
    identity = r._q4_identity(descriptor.get('encoder') if type(descriptor) is dict else None)
    policy = {'prefix': 'query: ', 'truncate_chars': 2000, 'normalize': True, 'dtype': 'float32', 'dim': 384}
    if (set(descriptor) != {'schema', 'scope', 'memory_sha256', 'dim', 'encoder'}
            or descriptor['schema'] != 'wd.bridge-mind-q4-memory-descriptor.v3'
            or descriptor['scope'] != r._ALL_TASK_ROUTE_SCOPE
            or descriptor['memory_sha256'] != values['memory_sha256']
            or type(descriptor['dim']) is not int or descriptor['dim'] != 384
            or not r._hex(values['encoder_sha256'])
            or r._sha(r._canonical(identity)) != values['encoder_sha256']
            or values['runtime_revision'] != identity['revision']
            or type(binding) is not dict
            or set(binding) != {'schema', 'encoder', 'code_sha256', 'query_policy'}
            or binding['schema'] != 'wd.mind-readonly-encoder-binding.v1'
            or binding['encoder'] != identity or not r._hex(binding['code_sha256'])
            or binding['query_policy'] != policy
            or type(binding['query_policy'].get('normalize')) is not bool
            or type(binding['query_policy'].get('dim')) is not int
            or type(binding['query_policy'].get('truncate_chars')) is not int):
        raise ValueError('invalid_descriptor_or_binding')
    if (type(memory) is not dict
            or set(memory) != {'schema','authority','scope','dim','encoder','projection_schema',
                               'projection_sha256','cutoff_utc','cells','records','views','independent_views'}
            or memory['schema'] != 'wd.bridge-mind-hex-memory-step2.v1'
            or memory['authority'] != 'none' or memory['scope'] != descriptor['scope']
            or type(memory['dim']) is not int or memory['dim'] != 384
            or memory['encoder'] != identity or memory['projection_schema'] != r._ALL_TASK_COMBINED_SCHEMA
            or memory['projection_sha256'] != snap['projection_sha256']
            or type(memory['cells']) is not dict or not memory['cells']
            or type(values['cell_snapshots']) is not dict
            or set(values['cell_snapshots']) != set(memory['cells'])):
        raise ValueError('invalid_memory')
    records_list = projection['messages']['records']
    records = {item['record_id']: item for item in records_list}
    if (not records or len(records) != len(records_list)
            or type(memory['records']) is not int or memory['records'] != len(records)
            or memory['views'] != ['w', 'd'] or memory['independent_views'] is not False
            or memory['cutoff_utc'] != max(item['available_utc'] for item in records_list)):
        raise ValueError('invalid_records')
    limits = r._q4_complete_source_limits(records)
    if set(limits) != set(memory['cells']):
        raise ValueError('incomplete_cells')
    entries = {}
    for cell in sorted(limits):
        data, content, meta, rows = _cell_controls(cell, values['cell_snapshots'][cell],
                                                   memory['cells'][cell], memory, records,
                                                   complete_limits=limits[cell])
        if meta['metadata'] != [{} for _ in rows]:
            raise ValueError('metadata_mismatch')
        pairs = hashlib.sha256()
        for i, (record_id, view) in enumerate(rows):
            raw = data['vectors'][i * 1536:(i + 1) * 1536]
            vector = r._q4_vector(struct.unpack('<384f', raw))
            if struct.pack('<384f', *vector) != raw:
                raise ValueError('noncanonical_float32')
            pairs.update(meta['doc_ids'][i].encode() + b'\0' + raw)
            if view in entries.setdefault(record_id, {}):
                raise ValueError('duplicate_view')
            entries[record_id][view] = struct.pack('<384d', *vector)
        if not hmac.compare_digest(pairs.hexdigest(), content['pairs_sha256']):
            raise ValueError('pairs_mismatch')
    if set(entries) != set(records) or any(set(views) != {'w', 'd'} for views in entries.values()):
        raise ValueError('incomplete_views')
    raw = values['query_bytes']
    if type(raw) is not bytes or not 0 < len(raw) <= 65536:
        raise ValueError('invalid_query')
    query = json.loads(raw, object_pairs_hook=r._unique_pairs, parse_constant=r._bad_constant)
    if (type(query) is not dict or set(query) not in ({'situation', 'time'}, {'situation', 'time', 'max_age_seconds'})
            or type(query['situation']) is not str or not query['situation'].strip()
            or len(query['situation']) > 4000):
        raise ValueError('invalid_query')
    fresh_mode = 'max_age_seconds' in query
    if fresh_mode and (type(query['max_age_seconds']) is not int or not 1 <= query['max_age_seconds'] <= 900):
        raise ValueError('invalid_age_bound')
    encoder = values['encoder_provider']
    r._q4_provider_identity(encoder, binding, 'query')
    def checkpoint():
        if any(values[key] != snap[key] for key in r._Q4_SOURCE_KEYS):
            raise ValueError('snapshot_changed')
        if (any(values[key] != expected for key, expected in controls.items())
                or values['cell_snapshots'] != frozen_cells):
            raise ValueError('controls_changed')
        if r._verified_complete(snap)[2] != report:
            raise ValueError('source_changed')
    checkpoint()
    checked_encoder = _CheckedQueryCache(encoder, checkpoint)
    when = r._utc(query['time'])
    ranking_records = _fresh_records(records, when, query['max_age_seconds']) if fresh_mode else records
    selected = r._q4_rank(ranking_records, entries, checked_encoder, query['situation'], when)
    candidate = not selected
    if candidate:
        if fresh_mode and _fresh_eligible_count(ranking_records, query['situation'], when) <= 3:
            selected = _small_fresh_candidates(ranking_records, entries, checked_encoder, query['situation'], when)
        else:
            selected = r._q4_complete_semantic_candidates(ranking_records, entries, checked_encoder,
                                                           query['situation'], when)
    hits = [{'ref_sha256': r._sha(records[key]['provenance']['event_ref'].encode()),
             'doc_id_sha256': r._sha(key.encode()), 'ref_kind': 'sha256_of_projected_metadata',
             'canonical_event': False} for key in selected]
    resolved = r.resolve_content_results(hits, query['time'], _complete=True, **snap)
    r._q4_provider_identity(encoder, binding, 'query')
    checkpoint()
    output = {'schema':'wd.mind-retained-f32-query.v1', 'status':'ANSWER' if resolved else 'ABSTAIN',
              'rule':'q4-rule-v1.2-canonical-f32', 'results':resolved, 'authority':'none',
              'index_match':'NOT_APPLICABLE', 'publisher_authentication':'UNKNOWN',
              'sender_authentication':'UNKNOWN', 'provider_authentication':'UNKNOWN',
              'source_filter_attestation':'UNKNOWN', 'freshness':'UNKNOWN', 'same_user_aba':'UNKNOWN',
              'binding': {key: values[key] for key in ('descriptor_sha256','memory_sha256',
                  'projection_sha256','encoder_sha256','encoder_binding_sha256','policy_sha256',
                  'roster_sha256','decision_sha256','approved_tasks_sha256')}}
    if candidate and resolved:
        output['status'] = 'SEMANTIC_CANDIDATE'
        output['rule'] = 'q4-complete-semantic-candidate-v1'
        output['relevance'] = 'UNVERIFIED'
    if len(r._canonical(output)) > 1024 * 1024:
        raise ValueError('output_over_budget')
    return output


def query_retained_content_snapshot(**inputs):
    """Return only a complete, re-derived result or a fixed-token refusal."""
    return r._provider_result(lambda: _query(inputs))


