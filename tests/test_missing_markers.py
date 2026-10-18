from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd
import pytest

from app.extensions import db
from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.services.data_service import DataService
from app.services.missing_value_service import MissingValueService as Missing
from app.services.column_type_service import ColumnTypeService as Columns
from app.services.text_normalization_service import TextNormalizationService as Texts
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.duplicate_configuration_service import DuplicateConfigurationService as Duplicates
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.value_proposal_service import ValueProposalService as Proposals


def markers(*values, ignore_case=False):
    return {'version': Missing.VERSION, 'values': list(values), 'ignore_case': ignore_case}


def test_exact_literal_scoped_matching_is_opt_in():
    pref = {'type': 'text', 'missing_values': markers('N/A', 'sin dato')}
    assert Missing.matches(' N/A ', pref)
    assert not Missing.matches('n/a', pref)
    assert not Missing.matches('N/A parcial', pref)
    assert not Missing.matches('N/A', {'type': 'text'})
    assert not Missing.matches(0, {'type': 'integer', 'missing_values': markers('0')})
    assert Missing.matches('0', {'type': 'text', 'missing_values': markers('0')})
    pref['missing_values']['ignore_case'] = True
    assert Missing.matches('SIN DATO', pref)
    assert Missing.matches('n/a', pref)


def test_parse_limits_literal_values_and_no_default_dictionary():
    cards = [{'index': 0, 'name': 'amount'}]
    assert Columns.parse({'column_0': 'text'}, cards) == {'amount': {'type': 'text'}}
    parsed = Columns.parse({'column_0': 'integer', 'column_0_missing_markers': ' sin dato \nN/A\nN/A\n',
                            'column_0_missing_ignore_case': '1'}, cards)['amount']
    assert parsed['missing_values'] == markers('sin dato', 'N/A', ignore_case=True)
    assert Missing.parse({'x_missing_markers': '.*'}, 'x')['values'] == ['.*']
    for value in ['a'*101, '\n'.join(str(i) for i in range(21)), 'a'*2021]:
        with pytest.raises(ValueError): Missing.parse({'x_missing_markers': value}, 'x')
    with pytest.raises(ValueError): Missing.parse({'x_missing_markers': 'NA', 'x_missing_ignore_case': 'yes'}, 'x')


def test_csv_and_excel_ingestion_preserve_literal_na_and_only_empty_cells_are_null(tmp_path):
    frame = pd.DataFrame({'id': ['A', 'B', 'C', 'D', 'E'], 'value': ['N/A', 'NULL', 'NA', '0', None]})
    csv = tmp_path / 'markers.csv'; frame.to_csv(csv, index=False)
    excel = tmp_path / 'markers.xlsx'; frame.to_excel(excel, index=False)
    for path in (csv, excel):
        loaded = DataService.read_file(path)
        assert loaded['value'].iloc[:4].tolist() == ['N/A', 'NULL', 'NA', '0']
        assert pd.isna(loaded['value'].iloc[4])


def test_markers_do_not_overlap_invalid_text_or_numeric_proposals():
    prefs = {'quantity': {'type': 'integer', 'missing_values': markers('sin dato')},
             'name': {'type': 'text', 'text_rules': {'version': Texts.VERSION, 'trim': True}, 'missing_values': markers('N/A')}}
    frame = pd.DataFrame({'quantity': ['sin dato', 'bad', None], 'name': [' N/A ', ' José ', None]})
    issues = Treatments.issues(frame, prefs)
    assert {(i['column'], i['scenario']): i['count'] for i in issues} == {
        ('quantity','missing_marker'): 1, ('quantity','invalid'): 1, ('quantity','missing'): 1,
        ('name','missing_marker'): 1, ('name','text_normalizable'): 1, ('name','missing'): 1}
    assert Texts.summaries(frame, prefs)[0]['count'] == 1
    for action in ['ai', 'normalize_text', 'normalize_number']:
        with pytest.raises(ValueError):
            Treatments.parse({'action_a': action}, [{'id': 'a', 'scenario': 'missing_marker'}])


@pytest.mark.parametrize('action,expected,quarantined', [('keep', 'sin dato', 0), ('null', None, 0), ('replace', 2, 0), ('quarantine', None, 1)])
def test_marker_treatments_keep_replace_null_and_quarantine(app, action, expected, quarantined):
    frame = pd.DataFrame({'quantity': ['sin dato', '3']})
    prefs = {'quantity': {'type': 'integer', 'missing_values': markers('sin dato')}}
    issue = Treatments.issues(frame, prefs)[0]
    config = SimpleNamespace(columns=prefs, duplicates={'mode': 'none'}, ai_review={},
        treatments={'decisions': {issue['id']: {'action': action, **({'replacement':'2'} if action=='replace' else {})}}})
    assert Proposals.candidates(frame, config, {}) == []
    with app.app_context(), patch('app.services.ai_providers.AIProviderFactory.create', side_effect=AssertionError('No AI')):
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config)
    assert result.metrics['quarantined_rows'] == quarantined
    if action == 'null': assert pd.isna(result.cleaned.iloc[0, 0])
    elif action != 'quarantine': assert result.cleaned.iloc[0, 0] == expected
    else: assert result.quarantine.iloc[0, 0] == 'sin dato'
    assert frame.iloc[0, 0] == 'sin dato'


def test_marker_keys_are_protected_from_deduplication_even_when_kept():
    frame = pd.DataFrame({'id': ['N/A', 'N/A', 'A', 'A']})
    result = Duplicates.preview(frame, {'mode': 'keys', 'keys': ['id'], 'keep': 'first'},
                                {'id': {'type': 'identifier', 'missing_values': markers('N/A')}}, include_indices=True)
    assert result['incomplete'] == 2
    assert result['indices'] == [3]


def test_wizard_marker_snapshot_export_and_configuration_invalidation(app, client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'id,quantity\nA,N/A\nB,3\nC,NULL\n'), 'markers.csv')})
    root = '/files/cleaning/1/'
    class Inputs(HTMLParser):
        def __init__(self): super().__init__(); self.data = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag=='input' and attrs.get('type')=='hidden': self.data[attrs['name']] = attrs.get('value', '')
    def tokens(step):
        response = client.get(root+step); assert response.status_code == 200
        parser = Inputs(); parser.feed(response.text); return parser.data
    fields = {'column_0':'identifier', 'column_1':'integer', 'column_1_missing_markers':'N/A\nNULL'}
    client.post(root+'columns', data={**tokens('columns'), **fields})
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode':'none'})
    with app.app_context():
        config = CleaningConfiguration.query.one()
        config.treatments = {'decisions': {}}; config.ai_review = {'fingerprint':'old'}
        db.session.commit()
    client.post(root+'columns', data={**tokens('columns'), **fields, 'column_1_missing_ignore_case':'1'})
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert config.duplicates == config.treatments == config.ai_review == {}
        prefs = deepcopy(config.columns)
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode':'none'})
    frame = pd.DataFrame({'id':['A','B','C'], 'quantity':['N/A','3','NULL']})
    issues = Treatments.issues(frame, prefs)
    assert len(issues) == 1 and issues[0]['count'] == 2
    page = client.get(root+'treatments').text
    assert 'Textos que definiste como datos ausentes' in page
    assert 'Pedir una propuesta a la IA' not in page
    client.post(root+'treatments', data={**tokens('treatments'), 'action_'+issues[0]['id']:'null'})
    response = client.post(root+'wizard-preview', data=tokens('wizard-preview'))
    assert '/wizard-result/' in response.location
    with app.app_context():
        version = DatasetVersion.query.one(); version_id = version.id
        assert version.operations[0]['columns']['quantity']['missing_values']['values'] == ['N/A','NULL']
        assert {c['before'] for c in version.operations[0]['changes'] if c['after'] is None} == {'N/A','NULL'}
    with ZipFile(BytesIO(client.get(root+f'export/{version_id}').data)) as archive:
        assert 'A,' in archive.read('datos_limpios.csv').decode('utf-8-sig')
