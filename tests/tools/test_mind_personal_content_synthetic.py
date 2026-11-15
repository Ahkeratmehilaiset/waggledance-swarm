"""Synthetic FI/EN regression for a narrow personal-content rule, not complete PII detection."""
import pytest

import test_wd_mind_content_query_readonly as fixture
from tools import wd_mind_content_query_readonly as reader


@pytest.mark.parametrize('case,text,options,expected', [
    ('health_en', 'The operator has a serious illness', {}, False),
    ('health_fi', 'Operaattorilla on vakava sairaus', {}, False),
    ('address_en', 'Mira lives at 7 Birch Way', {}, False),
    ('address_fi', 'Henkilo Mira asuu osoitteessa Mantytie 7', {}, False),
    ('address_fi_unicode', 'Henkilö Mira asuu osoitteessa Mäntytie 7', {}, False),
    ('review', 'RCO review discussed a parser fix', {}, True),
    ('operator_work', 'The operator approved the RCO review', {}, True),
    ('technical_health', 'The operator tests the disease classifier', {}, True),
    ('technical_health_owned', 'The operator has an illness classifier', {}, True),
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
    assert (text in actual) is expected, case
