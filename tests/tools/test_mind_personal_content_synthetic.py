"""Synthetic FI/EN regression for a narrow personal-content rule, not complete PII detection."""
import pytest

import test_wd_mind_content_query_readonly as fixture
import test_wd_mind_content_snapshot_package as package_fixture
from tools import wd_mind_content_query_readonly as reader
from tools import wd_mind_content_snapshot_package as producer


@pytest.mark.parametrize('text', [
    'claude-rco-1, please review this synthetic parser change.',
    '@claude-rco-2 please review this synthetic parser change.',
    'Dear claude-rco-1: please check this synthetic parser change.',
    'RCO1, please review this synthetic parser change.',
    'CLAUDE-RCO-2: please review this synthetic parser change.',
    'claude-rco-1 please review this synthetic parser change.',
    '\u200b＠ｃｌａｕｄｅ－ｒｃｏ－１ please review this synthetic parser change.',
])
def test_direct_rco_address_is_not_projected(tmp_path, text):
    row = fixture._row(text, task_id='synthetic-direct-address',
                       status='review_requested', to='codex-tools-1')
    projection = reader.rederive_message_projection(**fixture._all_tasks_case(reader, tmp_path, [row]))
    assert projection['messages']['records'] == []
    assert projection['messages']['refused'] == {'rco_address': 1}


@pytest.mark.parametrize('text', [
    'RCO review discussed the synthetic parser fix.',
    'Build note: claude-rco-1 said the synthetic parser passed.',
    'Quoted prior note: "claude-rco-1, please review the synthetic patch."',
    '"RCO1, please review the synthetic patch."',
    'claude-rco-1 said the synthetic parser passed.',
])
def test_review_mentions_and_explicit_quotes_still_project(tmp_path, text):
    row = fixture._row(text, task_id='synthetic-quoted-review',
                       status='review_requested', to='codex-tools-1')
    projection = reader.rederive_message_projection(**fixture._all_tasks_case(reader, tmp_path, [row]))
    records = projection['messages']['records']
    assert len(records) == 1
    assert reader.decode_text(records[0]['stored_text']) == text
    assert records[0]['provenance']['source_event_ref'] == fixture._sha(fixture._raw(row))


@pytest.mark.parametrize('prefix,reason', [
    ('password = synthetic-private-value. ', 'credential'),
    ('Synthetic Private Operator. ', 'identifier'),
    ('sealed synthetic fixture. ', 'sealed'),
])
def test_rco_address_change_preserves_existing_content_screening(prefix, reason):
    text = prefix + 'claude-rco-1, please review the synthetic change.'
    policy = reader.IdentifierPolicy(('synthetic private operator',))
    assert reader._classify_with_policy(text, 4096, policy) == ('DROP', reason)


@pytest.mark.parametrize('suffix,reason', [
    (' password = synthetic-private-value.', 'credential'),
    (' Synthetic Private Operator.', 'identifier'),
    (' sealed synthetic fixture.', 'sealed'),
    (' The operator has a serious illness.', 'personal'),
])
def test_direct_rco_address_preserves_existing_drop_priority(suffix, reason):
    text = 'claude-rco-1, please review this synthetic change.' + suffix
    policy = reader.IdentifierPolicy(('synthetic private operator',))
    assert reader._classify_with_policy(text, 4096, policy) == ('DROP', reason)


@pytest.mark.parametrize('case,text,options,expected', [
    ('health_en', 'The operator has a serious illness', {}, False),
    ('health_en_punctuation', 'The operator has a serious, chronic illness', {}, False),
    ('health_spaced_hyphen', 'The operator has a serious illness - classifier discussion', {}, False),
    ('health_left_spaced_hyphen', 'The operator has a serious illness -classifier discussion', {}, False),
    ('health_right_spaced_hyphen', 'The operator has a serious illness- classifier discussion', {}, False),
    ('health_double_hyphen', 'The operator has a serious illness--classifier discussion', {}, False),
    ('health_en_dash', 'The operator has a serious illness – classifier discussion', {}, False),
    ('health_em_dash', 'The operator has a serious illness — classifier discussion', {}, False),
    ('health_fi', 'Operaattorilla on vakava sairaus', {}, False),
    ('health_fi_punctuation', 'Operaattorilla on vakava, krooninen sairaus', {}, False),
    ('health_fi_past', 'Operaattorilla oli vakava sairaus', {}, False),
    ('address_en', 'Mira lives at 7 Birch Way', {}, False),
    ('address_en_unit', 'Mira lives at 7B Birch Way', {}, False),
    ('address_en_abbreviation', 'Mira lives at 7 Birch Rd', {}, False),
    ('address_fi', 'Henkilo Mira asuu osoitteessa Mantytie 7', {}, False),
    ('address_fi_unicode', 'Henkilö Mira asuu osoitteessa Mäntytie 7', {}, False),
    ('address_fi_unit', 'Mira asuu osoitteessa Mäntytie 7A', {}, False),
    ('review', 'RCO review discussed a parser fix', {}, True),
    ('operator_work', 'The operator approved the RCO review', {}, True),
    ('technical_health', 'The operator tests the disease classifier', {}, True),
    ('technical_health_owned', 'The operator has an illness classifier', {}, True),
    ('technical_health_hyphen', 'RCO review: the operator has an illness-classifier', {}, True),
    ('technical_hyphen_credential', 'The operator has an illness-classifier; password = imaginary-test-value', {}, False),
    ('technical_hyphen_sealed', 'The operator has an illness-classifier and sealed material', {}, False),
    ('technical_hyphen_rco', 'The operator has an illness-classifier', {'to':'claude-rco-2'}, False),
    ('known_person', 'Synthetic Private Operator agreed to meet', {}, False),
    ('credential', 'password = imaginary-test-value', {}, False),
    ('sealed', 'sealed synthetic material', {}, False),
    ('rco_sender', 'Synthetic ordinary note', {'agent':'claude-rco-1',
        'agent_uuid':fixture.ROSTER['claude-rco-1']}, False),
    ('rco_recipient', 'Synthetic ordinary note', {'to':'claude-rco-2'}, False),
])
def test_synthetic_personal_content(case, text, options, expected, tmp_path):
    row = fixture._row(text, task_id='synthetic-policy-task-1', **options)
    source = fixture._all_tasks_case(reader, tmp_path, [row])
    projection = reader.rederive_message_projection(**source)
    actual = [reader.decode_text(record['stored_text']) for record in projection['messages']['records']]
    if case == 'technical_health_hyphen':
        # Existing 16-character token masking remains in force.
        assert actual == ['RCO review: the operator has an token-redacted']
        return
    assert actual == ([text] if expected else []), case


# Invented assertions only. These close three specific gaps, not every medical
# paraphrase, person reference or language. The clinical modifier lists are closed.
HEALTH_CONTEXT_GAPS = [
    'Operator has a disease classifier and a chronic illness.',
    'Operator had an illness-model and a severe disease.',
    'Operator has a diagnosis detector and has an acute illness.',
    'Operator has a disease classifier, and a persistent diagnosis.',
    'Operator had a persistent disease\nmodel: recovery is slow.',
    'Operator had a persistent disease\r\nMODEL : recovery is slow.',
    'Operator had a persistent disease\n\nparser: recovery is slow.',
    'Operator had a persistent disease\u2028detector: recovery is slow.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus.',
    'Operaattorilla oli jo pitkään tutkimuksissa todettu vakava krooninen tauti.',
    'Operaattorilla on jo pitkään tutkimuksissa aiemmin toistuvasti todettu vakava krooninen pitkäaikainen sairaus.',
    'Operator had a\npersistent disease\nmodel: recovery is slow.',
    'Operator had a persistent disease\n\u200bＭＯＤＥＬ： recovery is slow.',
    'Operator has a disease classifier and a chronic illness. Parser review follows.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus; jatko myöhemmin.',
]


_CLINICAL_EN = 'Operator has a disease classifier and a chronic illness'
_CLINICAL_FI = 'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus'
CLINICAL_CONTINUATIONS = [_CLINICAL_EN + suffix for suffix in (
    ', recovery is slow.',
    ', care is ongoing.',
    ' that requires ongoing treatment.',
    ', which requires treatment.',
    ' which needs continued medication.',
    ', but the operator is recovering.',
    ' but cannot work.',
)] + [_CLINICAL_FI + suffix for suffix in (
    ', toipuminen on hidasta.',
    ', toipuminen on kesken.',
    ', joka vaatii jatkuvaa hoitoa.',
    ' joka vaatii lääkitystä.',
    ', mutta operaattori on toipumassa.',
    ' mutta on toipumassa.',
)] + [
    _CLINICAL_EN + ', WHICH NEEDS CONTINUED MEDICATION.',
    _CLINICAL_EN + ' which\nrequires\tcontinued medication.',
]
_CLINICAL_BOUNDARY_CASES = [CLINICAL_CONTINUATIONS[index] for index in (0, 2, 5, 7, 9, 11)]
CLINICAL_TECHNICAL_CONTROLS = [_CLINICAL_EN + suffix for suffix in (
    ', disease, or diagnosis detector.',
    ' or disease classifier.',
    ' and disease models.',
    ' but not a disease model.',
    ' that requires JSON serialization.',
    ' that requires treatment metadata.',
    ', recovery is slow.json.',
    ', which requires treatment.json.',
    ' model, recovery is slow.',
    '-model, recovery is slow.',
)] + [_CLINICAL_FI + suffix for suffix in (
    ', tauti ja diagnoosi -malli.',
    ' ja tauti -luokitin.',
    ', mutta ei tauti -malli.',
    ', joka vaatii JSON-skeeman.',
    ', joka vaatii hoitoa kuvaavan tietomallin.',
    ', toipuminen on hidasta.json.',
    ', joka vaatii hoitoa.json.',
    'malli, toipuminen on hidasta.',
    '-malli, toipuminen on hidasta.',
    ' malli, toipuminen on hidasta.',
)]


@pytest.mark.parametrize('text', HEALTH_CONTEXT_GAPS + CLINICAL_CONTINUATIONS)
def test_health_context_gaps_drop_as_personal(text):
    assert reader._classify_with_policy(text, 4096, reader.IdentifierPolicy(())) == ('DROP', 'personal')


@pytest.mark.parametrize('text', [
    'RCO review: parser remains bounded.',
    'Operator has a disease model for unit tests.',
    'Operator has a disease-model for unit tests.',
    'Operator has a disease model: unit tests passed.',
    'Operator has a disease\nmodel for unit tests.',
    'Operator has a disease classifier and an illness detector.',
    'Operator has a disease classifier and a classifier for an illness.',
    'Operator has a disease classifier and a diagnosis schema.',
    'Operator has a disease classifier and a diagnosis.json.',
    'Operator has a disease classifier and a chronic illness.model.',
    'Operator has a newly deployed classifier for a chronic disease.',
    'Operaattorilla on uusi esimerkki, jonka nimi on krooninen sairaus.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairausmalli.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus-malli.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus malli.',
    'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairaus.json.',
] + CLINICAL_TECHNICAL_CONTROLS)
def test_health_context_keeps_bounded_technical_counterexamples(text):
    outcome, encoded = reader._classify_with_policy(text, 4096, reader.IdentifierPolicy(()))
    assert outcome == 'KEEP'
    assert reader.decode_text(encoded) == ' '.join(text.split())


def test_health_context_keeps_existing_long_compound_token_redaction():
    text = 'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen sairausluokitinmalli.'
    outcome, encoded = reader._classify_with_policy(text, 4096, reader.IdentifierPolicy(()))
    assert outcome == 'KEEP'
    assert reader.decode_text(encoded) == 'Operaattorilla on jo pitkään tutkimuksissa havaittu krooninen token-redacted'


@pytest.mark.parametrize('text', [HEALTH_CONTEXT_GAPS[0], HEALTH_CONTEXT_GAPS[4], HEALTH_CONTEXT_GAPS[8]]
                         + _CLINICAL_BOUNDARY_CASES)
@pytest.mark.parametrize('prefix,reason', [
    ('password = synthetic-private-value. ', 'credential'),
    ('Synthetic Private Operator. ', 'identifier'),
    ('sealed synthetic fixture. ', 'sealed'),
])
def test_health_context_preserves_first_drop_reason(text, prefix, reason):
    policy = reader.IdentifierPolicy(('synthetic private operator',))
    assert reader._classify_with_policy(prefix + text, 4096, policy) == ('DROP', reason)


@pytest.mark.parametrize('text,reason', [
    (None, 'encoding'), ('\ud800', 'encoding'), ('x' * 4097, 'oversize'),
    ('\n\r\n', 'empty'), ('Operator has disease\x00\nmodel: tests', 'encoding'),
])
def test_health_line_context_keeps_input_validation(text, reason):
    assert reader._classify_with_policy(text, 4096, reader.IdentifierPolicy(())) == ('DROP', reason)


@pytest.mark.parametrize('text', [HEALTH_CONTEXT_GAPS[0], HEALTH_CONTEXT_GAPS[4], HEALTH_CONTEXT_GAPS[8],
                                HEALTH_CONTEXT_GAPS[11]] + _CLINICAL_BOUNDARY_CASES)
@pytest.mark.parametrize('with_allowed', [False, True])
def test_health_context_real_source_never_reaches_synthetic_provider(tmp_path, text, with_allowed):
    case = fixture._semantic_case(reader, tmp_path, all_tasks=True)
    # Four eligible records exercise the existing query admission threshold.
    allowed = [f'RCO review {number}: network retry parser remains bounded.' for number in range(4)]
    rows = [fixture._row(text, task_id='synthetic-health-gap')]
    if with_allowed:
        rows.extend(fixture._row(message, task_id=f'synthetic-allowed-review-{number}',
                                 status='review_requested', to='codex-tools-1')
                    for number, message in enumerate(allowed))
    source = fixture._all_tasks_case(reader, tmp_path, rows)
    projection = reader.rederive_message_projection(**source)
    case.update(fixture._snapshot(source, projection))
    codec = package_fixture.Codec(fixture)
    args = package_fixture._arguments(case, codec)
    if not with_allowed:
        with pytest.raises(producer.SnapshotPackageRefused, match='^content_snapshot_package_refused$'):
            producer.package_content_snapshot(**args)
        assert codec.passages == codec.serialized == codec.decoded == []
    else:
        output = producer.package_content_snapshot(**args)
        assert [len(batch) for batch in codec.passages] == [4]
        assert sorted(codec.passages[0]) == sorted('passage: ' + message for message in allowed)
        assert output['memory_bytes'] and codec.serialized and codec.decoded
        result = package_fixture._query(case, output, codec)
        assert result['status'] == 'ANSWER' and result['authority'] == 'none'
        assert result['results']
        assert all(reader.decode_text(hit['stored_text']) in allowed for hit in result['results'])
    records = projection['messages']['records']
    assert sorted(reader.decode_text(record['stored_text']) for record in records) == (sorted(allowed) if with_allowed else [])


@pytest.mark.parametrize('options', [
    {'agent': 'claude-rco-1', 'agent_uuid': fixture.ROSTER['claude-rco-1']},
    {'to': 'claude-rco-2'},
    {'status': 'review_requested', 'to': 'external-consumer'},
])
def test_health_context_keeps_source_review_exclusions(tmp_path, options):
    row = fixture._row('Operator has a disease model: bounded parser review.', **options)
    projection = reader.rederive_message_projection(**fixture._all_tasks_case(reader, tmp_path, [row]))
    assert projection['messages']['records'] == []
