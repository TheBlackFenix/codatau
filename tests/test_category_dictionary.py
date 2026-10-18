from copy import deepcopy
from html.parser import HTMLParser
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from zipfile import ZipFile

import pandas as pd
import pytest

from app.models.cleaning_configuration import CleaningConfiguration
from app.models.dataset_version import DatasetVersion
from app.services.column_type_service import ColumnTypeService as Columns
from app.services.category_dictionary_service import CategoryDictionaryService as Categories
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.text_normalization_service import TextNormalizationService as Texts
from app.services.missing_value_service import MissingValueService as Missing
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.value_proposal_service import ValueProposalService as Proposals


def pref(mapping=None):
    preference = {'type': 'text'}
    if mapping:
        preference['category_dictionary'] = Categories.validate(mapping, preference)
    return preference


def test_available_values_frequencies_and_exact_matching():
    preference = pref({'Pend.': 'Pendiente', 'PENDIENTE': 'Pendiente'})
    series = pd.Series(['Pend.', 'Pend.', 'PENDIENTE', 'pendiente', None, '', 1, 'x'*201, 'N/A'])
    values, excluded = Categories.values(series, preference)
    assert next(item['count'] for item in values if item['value']=='Pend.') == 2
    assert excluded == 4
    assert Categories.replacement('Pend.', preference) == 'Pendiente'
    assert Categories.replacement('pendiente', preference) is None
    assert Categories.replacement('Pend. parcial', preference) is None
    assert Categories.replacement('Cancelado', preference) is None
    assert Categories.replacement('Pendiente', preference) is None
    assert Categories.replacement('Pend.', {**preference, 'type': 'identifier'}) is None


@pytest.mark.parametrize('mapping', [
    {'A':'B', 'B':'C'}, {'A':'B', 'B':'A'}, {'A':''}, {'':'B'}, {'A':'x'*201},
    {'A':1}, {str(i):'target' for i in range(501)}])
def test_invalid_chains_cycles_and_limits_are_rejected(mapping):
    with pytest.raises(ValueError): Categories.validate(mapping, {'type':'text'})


def test_normalized_keys_and_targets_respect_text_and_missing_settings():
    preference = {'type':'text', 'text_rules': {'version':Texts.VERSION, 'trim':True, 'case':'upper'},
                  'missing_values': {'version':Missing.VERSION, 'values':['SIN DATO'], 'ignore_case':True}}
    for mapping in [{'pend.':'PENDIENTE'}, {'PEND.':'Pendiente'}, {'SIN DATO':'PENDIENTE'}, {'PEND.':'SIN DATO'}]:
        with pytest.raises(ValueError): Categories.validate(mapping, preference)
    preference['category_dictionary'] = Categories.validate({'PEND.':'PENDIENTE'}, preference)
    values, _ = Categories.values(pd.Series([' pend. ', 'PEND.', ' sin dato ']), preference)
    assert values == [{'id':Categories.key('PEND.'), 'value':'PEND.', 'count':2}]
    frame = pd.DataFrame({'status':[' pend. ', 'PEND.', ' otro ', ' sin dato ']})
    issues = Treatments.issues(frame, {'status':preference}, include_rows=True)
    assert {i['scenario']:i['rows'] for i in issues} == {
        'missing_marker':[3], 'text_normalizable':[2], 'category_equivalence':[0,1]}
    assert Texts.summaries(frame, {'status':preference})[0]['count'] == 1
    assert Categories.replacement(' pend. ', preference) == 'PENDIENTE'


def test_add_only_accepts_found_values_and_updates_without_mutating_input():
    preference = pref()
    available, _ = Categories.values(pd.Series(['Pend.', 'PENDIENTE', 'Pendiente']), preference)
    original = Categories.validate({'Pend.':'Pendiente'}, preference)
    result = Categories.add(original, [Categories.key('PENDIENTE')], 'Pendiente', available, preference)
    assert result['mapping'] == {'Pend.':'Pendiente', 'PENDIENTE':'Pendiente'}
    assert original['mapping'] == {'Pend.':'Pendiente'}
    for selected, target in [([], 'Pendiente'), (['invented'], 'Pendiente'), ([Categories.key('Pendiente')], 'Pendiente')]:
        with pytest.raises(ValueError): Categories.add(original, selected, target, available, preference)


def test_column_save_preserves_dictionary_unless_text_missing_or_type_changes():
    preference = pref({'Pend.':'Pendiente'})
    cards = [{'index':0, 'name':'status', 'choice':preference}]
    assert Columns.parse({'column_0':'text'}, cards)['status'] == preference
    assert 'category_dictionary' not in Columns.parse({'column_0':'text', 'column_0_text_trim':'1'}, cards)['status']
    assert 'category_dictionary' not in Columns.parse({'column_0':'text', 'column_0_missing_markers':'N/A'}, cards)['status']
    assert 'category_dictionary' not in Columns.parse({'column_0':'identifier'}, cards)['status']


def test_one_group_approval_covers_thousand_rows_and_keep_is_respected(app):
    frame = pd.DataFrame({'status':['Pend.']*1000 + ['Cancelado','Devuelto']})
    preferences = {'status':pref({'Pend.':'Pendiente'})}
    issue = Treatments.issues(frame, preferences)[0]
    config = SimpleNamespace(columns=preferences, duplicates={'mode':'none'}, ai_review={},
        treatments={'decisions': {issue['id']:{'action':'apply_categories'}}})
    assert Proposals.candidates(frame, config, {}) == []
    with app.app_context(), patch('app.services.ai_providers.AIProviderFactory.create', side_effect=AssertionError('No AI')):
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config)
        assert result.cleaned['status'].iloc[:1000].tolist() == ['Pendiente']*1000
        assert result.cleaned['status'].iloc[-2:].tolist() == ['Cancelado','Devuelto']
        assert len(result.changes) == 1000
        config.treatments['decisions'][issue['id']]['action'] = 'keep'
        assert Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config).cleaned.iloc[0,0] == 'Pend.'
    assert frame.iloc[0,0] == 'Pend.'
    with pytest.raises(ValueError): Treatments.parse({'action_a':'apply_categories'}, [{'id':'a','scenario':'invalid'}])
    with pytest.raises(ValueError): Treatments.parse({'action_a':'ai'}, [{'id':'a','scenario':'category_equivalence'}])


def test_text_rules_and_dictionary_compose_without_repeated_suggestions(app):
    preference = {'type':'text', 'text_rules': {'version':Texts.VERSION, 'trim':True, 'case':'upper'}}
    preference['category_dictionary'] = Categories.validate({'PEND.':'PENDIENTE'}, preference)
    frame = pd.DataFrame({'status':[' pend. ', ' Pendiente ']})
    issues = Treatments.issues(frame, {'status':preference})
    config = SimpleNamespace(columns={'status':preference}, duplicates={'mode':'none'}, ai_review={},
        treatments={'decisions':{i['id']:{'action':'apply_categories' if i['scenario']=='category_equivalence' else 'normalize_text'} for i in issues}})
    with app.app_context(): result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config)
    assert result.cleaned['status'].tolist() == ['PENDIENTE','PENDIENTE']
    assert Treatments.issues(result.cleaned, {'status':preference}) == []


class Inputs(HTMLParser):
    def __init__(self): super().__init__(); self.data = {}
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag=='input' and attrs.get('type')=='hidden': self.data[attrs['name']] = attrs.get('value','')


def setup(client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file':(BytesIO(b'id,status\nA,Pend.\nA,PENDIENTE\nB,Cancelado\n'), 'status.csv')})
    root = '/files/cleaning/1/'
    def tokens(step):
        response = client.get(root+step); assert response.status_code == 200
        parser = Inputs(); parser.feed(response.text); return parser.data
    client.post(root+'columns', data={**tokens('columns'), 'column_0':'identifier', 'column_1':'text'})
    return root, tokens


def test_dictionary_route_version_export_duplicate_order_and_persistence(app, client, auth):
    root, tokens = setup(client, auth)
    route = root+'categories/1'
    assert 'Configurar equivalencias de categorías' in client.get(root+'columns').text
    old = tokens('categories/1')
    selected = [Categories.key('Pend.'), Categories.key('PENDIENTE')]
    assert client.post(route, data={**old, 'action':'add', 'selected':selected, 'target':'Pendiente'}).status_code == 302
    # A stale tab cannot remove the saved mapping.
    client.post(route, data={**old, 'action':'remove', 'group':Categories.key('Pendiente')})
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert len(config.columns['status']['category_dictionary']['mapping']) == 2
        assert DatasetVersion.query.count() == 0
        preferences = deepcopy(config.columns)
    auth.logout(); auth.login()
    assert 'Pendiente' in client.get(route).text
    # Saving types without changes must not erase the dictionary.
    client.post(root+'columns', data={**tokens('columns'), 'column_0':'identifier', 'column_1':'text'})
    with app.app_context(): assert CleaningConfiguration.query.one().columns['status']['category_dictionary']
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode':'all', 'keep':'first'})
    issues = Treatments.issues(pd.DataFrame({'id':['A','A','B'], 'status':['Pend.','PENDIENTE','Cancelado']}), preferences)
    assert len(issues) == 1
    assert 'Equivalencias guardadas: ejemplos' in client.get(root+'treatments').text
    client.post(root+'treatments', data={**tokens('treatments'), 'action_'+issues[0]['id']:'apply_categories'})
    assert 'Diccionario de equivalencias confirmado' in client.get(root+'wizard-preview').text
    response = client.post(root+'wizard-preview', data=tokens('wizard-preview'))
    assert '/wizard-result/' in response.location
    with app.app_context():
        version = DatasetVersion.query.one(); version_id = version.id
        assert version.metrics['duplicates_removed'] == 1 and version.metrics['after_rows'] == 2
        assert version.operations[0]['columns']['status']['category_dictionary']['mapping'] == {'Pend.':'Pendiente','PENDIENTE':'Pendiente'}
    with ZipFile(BytesIO(client.get(root+f'export/{version_id}').data)) as archive:
        assert 'A,Pendiente' in archive.read('datos_limpios.csv').decode('utf-8-sig')


def test_remove_invalidation_owner_type_and_csrf_guards(app, client, auth):
    root, tokens = setup(client, auth)
    route = root+'categories/1'
    client.post(route, data={**tokens('categories/1'), 'action':'add', 'selected':[Categories.key('Pend.')], 'target':'Pendiente'})
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode':'none'})
    client.post(route, data={**tokens('categories/1'), 'action':'remove', 'group':Categories.key('Pendiente')})
    with app.app_context():
        config = CleaningConfiguration.query.one()
        assert 'category_dictionary' not in config.columns['status']
        assert config.duplicates == config.treatments == config.ai_review == {}
    assert client.get(root+'categories/0').status_code == 404
    assert client.get(root+'categories/99').status_code == 404
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post(route, data={'action':'add'}).status_code == 400
    app.config['WTF_CSRF_ENABLED'] = False
    auth.logout(); auth.register('other','other@example.com'); auth.login('other@example.com')
    assert client.get(route).status_code == 404
    assert client.post(route, data={'action':'add'}).status_code == 404


def test_search_pagination_and_unknown_selection_are_safe(app, client, auth):
    auth.register(); auth.login()
    csv = 'status\n'+'\n'.join(f'value_{i:03}' for i in range(65))+'\n'
    client.post('/files/upload', data={'file':(BytesIO(csv.encode()), 'many.csv')})
    root = '/files/cleaning/1/'
    parser = Inputs(); parser.feed(client.get(root+'columns').text)
    client.post(root+'columns', data={**parser.data, 'column_0':'text'})
    route = root+'categories/0'
    response = client.get(route+'?page=2')
    assert response.status_code == 200 and 'Página 2 de 2' in response.text
    assert 'value_064' in response.text and 'value_000' not in response.text
    assert 'value_005' in client.get(route+'?q=value_005').text
    assert 'No hay valores disponibles' in client.get(route+'?q=missing').text
    parser = Inputs(); parser.feed(client.get(route).text)
    response = client.post(route, data={**parser.data, 'action':'add', 'selected':['forged'], 'target':'value'})
    assert response.status_code == 200
    with app.app_context(): assert 'category_dictionary' not in CleaningConfiguration.query.one().columns['status']
