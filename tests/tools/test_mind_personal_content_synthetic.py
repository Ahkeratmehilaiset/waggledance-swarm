"""Synthetic FI/EN regression for a narrow personal-content rule, not complete PII detection."""
import pytest

import test_wd_mind_content_query_readonly as fixture
from tools import wd_mind_content_query_readonly as reader


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
