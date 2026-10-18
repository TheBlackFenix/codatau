from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.services.column_type_service import ColumnTypeService as Columns
from app.services.text_normalization_service import TextNormalizationService as Texts
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.value_proposal_service import ValueProposalService as Proposals


def pref(**options):
    return {'type': 'text', 'text_rules': {'version': Texts.VERSION, **options}}


@pytest.mark.parametrize('value,options,expected', [
    ('  Bogotá  ', {'trim': True}, 'Bogotá'),
    ('A\t   B\nC  D', {'collapse_spaces': True}, 'A B\nC D'),
    ('A\x00\x07\u200b\ufeffB\nC\tD', {'clean_controls': True}, 'AB\nC\tD'),
    ('Jose\u0301', {'nfc': True}, 'José'),
    ('Bogotá', {'case': 'upper'}, 'BOGOTÁ'),
    ('ÉXITO', {'case': 'lower'}, 'éxito'),
    ('\n A \n', {'trim': True}, '\n A \n'),
    ('\u200d\u200c', {'clean_controls': True}, '\u200d\u200c'),
    ('0', {'trim': True}, '0'),
    ('N/A', {'trim': True}, 'N/A'),
])
def test_bounded_rules_are_idempotent_and_do_not_remove_accents(value, options, expected):
    result = Texts.normalize(value, pref(**options))
    assert result == expected
    assert Texts.normalize(result, pref(**options)) == result


def test_rules_are_opt_in_by_type_and_keep_non_strings():
    value = '  José  '
    assert Texts.normalize(value, {'type': 'text'}) == value
    assert Texts.normalize(value, {**pref(trim=True), 'type': 'email'}) == value
    assert Texts.normalize(12, pref(trim=True)) == 12
    assert Texts.normalize(None, pref(trim=True)) is None
    cards = [{'index': 0, 'name': 'name'}]
    assert Columns.parse({'column_0': 'text'}, cards) == {'name': {'type': 'text'}}
    assert 'text_rules' not in Columns.parse({'column_0': 'integer', 'column_0_text_trim': '1'}, cards)['name']
    with pytest.raises(ValueError):
        Columns.parse({'column_0': 'text', 'column_0_text_case': 'title'}, cards)


def test_scenarios_do_not_overlap_and_single_approval_covers_thousand_cells(app):
    frame = pd.DataFrame({'name': ['  José  '] * 1000 + [None, ' ', 'José']})
    preferences = {'name': pref(trim=True)}
    issues = Treatments.issues(frame, preferences, include_rows=True)
    assert {i['scenario']: i['count'] for i in issues} == {'missing': 2, 'text_normalizable': 1000}
    config = SimpleNamespace(columns=preferences, duplicates={'mode': 'none'}, ai_review={},
        treatments={'decisions': {i['id']: {'action': 'normalize_text' if i['scenario']=='text_normalizable' else 'keep'} for i in issues}})
    assert Proposals.candidates(frame, config, {}) == []
    with app.app_context(), patch('app.services.ai_providers.AIProviderFactory.create', side_effect=AssertionError('No AI')):
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config)
        assert result.cleaned['name'].iloc[:1000].tolist() == ['José'] * 1000
        assert result.cleaned['name'].iloc[1001] == ' '
        assert sum(c['reason'] == 'Reglas de texto sin IA confirmadas para el grupo' for c in result.changes) == 1000
        kept = deepcopy(config)
        for decision in kept.treatments['decisions'].values(): decision['action'] = 'keep'
        assert Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), kept, {}, app.config).cleaned.iloc[0, 0] == '  José  '
    assert frame.iloc[0, 0] == '  José  '


def test_empty_result_warning_and_forged_action_rejected():
    summary = Texts.summaries(pd.DataFrame({'name': ['\u200b', '\x07']}), {'name': pref(clean_controls=True)})
    assert summary[0]['empty_count'] == 2
    with pytest.raises(ValueError):
        Treatments.parse({'action_a': 'normalize_text'}, [{'id': 'a', 'scenario': 'invalid'}])
    with pytest.raises(ValueError):
        Treatments.parse({'action_a': 'ai'}, [{'id': 'a', 'scenario': 'text_normalizable'}])


def test_text_wizard_persists_previews_and_deduplicates_after_cleaning(app, client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO('name\n  José  \nJosé\n'.encode()), 'names.csv')})
    root = '/files/cleaning/1/'
    class Inputs(HTMLParser):
        def __init__(self): super().__init__(); self.data = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('type') == 'hidden': self.data[attrs['name']] = attrs.get('value', '')
    def tokens(step):
        response = client.get(root+step); assert response.status_code == 200
        parser = Inputs(); parser.feed(response.text); return parser.data
    client.post(root+'columns', data={**tokens('columns'), 'column_0': 'text', 'column_0_text_trim': '1'})
    with app.app_context():
        assert CleaningConfiguration.query.one().columns['name']['text_rules']['trim'] is True
    assert 'checked> Quitar espacios' in client.get(root+'columns').text
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode': 'keys', 'keys': ['name'], 'keep': 'first'})
    issue = Treatments.issues(pd.DataFrame({'name': ['  José  ', 'José']}), {'name': pref(trim=True)})[0]
    assert 'Propuestas de texto sin IA' in client.get(root+'treatments').text
    client.post(root+'treatments', data={**tokens('treatments'), 'action_'+issue['id']: 'normalize_text'})
    assert 'Reglas de texto sin IA' in client.get(root+'wizard-preview').text
    response = client.post(root+'wizard-preview', data=tokens('wizard-preview'))
    assert '/wizard-result/' in response.location
    with app.app_context():
        version = DatasetVersion.query.one()
        assert version.metrics['duplicates_removed'] == 1
        assert version.operations[0]['columns']['name']['text_rules']['version'] == Texts.VERSION
