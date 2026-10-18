from copy import deepcopy
from decimal import Decimal
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
from app.services.numeric_normalization_service import NumericNormalizationService as Numbers
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.value_proposal_service import ValueProposalService as Proposals
from app.services.wizard_execution_service import WizardExecutionService as Executor


PREF = {'type': 'decimal', 'decimal_separator': '.', 'numeric_rules': Numbers.VERSION, 'number_source': 'auto'}


@pytest.mark.parametrize('value,expected,rule', [
    (' 42 ', '42', 'numeric_text'), ('42.0', '42', 'numeric_text'),
    ('3,5', '3.5', 'localized_number'), ('1.234,56', '1234.56', 'localized_number'),
    ('1,234.56', '1234.56', 'localized_number'), ('1.2e3', '1200', 'scientific'),
    ('2E-2', '0.02', 'scientific'), ('dos', '2', 'spanish_integer'),
    ('DOS', '2', 'spanish_integer'), ('veinticinco', '25', 'spanish_integer'),
    ('ciento veinte', '120', 'spanish_integer'), ('menos dos', '-2', 'spanish_integer'),
    ('cero', '0', 'spanish_integer'), ('mil doscientos treinta y cuatro', '1234', 'spanish_integer')])
def test_deterministic_rules(value, expected, rule):
    result = Numbers.inspect(value, PREF)
    assert result.status == 'valid'
    assert result.value == Decimal(expected)
    assert result.rule == rule


@pytest.mark.parametrize('value', ['dos o tres', 'unos veinte', 'dos y tres', 'uno dos',
    'dos unidades', '2 unidades', '15%', '$200', '2 kg', 'NaN', 'inf', '1e999',
    '1e100000', '12,34.56', '1,23,456', '3..5', True, 'tercero'])
def test_no_partial_extraction_or_unconfigured_semantics(value):
    assert Numbers.inspect(value, PREF).status == 'invalid'


@pytest.mark.parametrize('value', ['1,234', '1.234', '-1,234'])
def test_ambiguity_is_not_guessed(value):
    assert Numbers.inspect(value, PREF).status == 'ambiguous'


def test_confirmed_source_overrides_output_reference_and_mixed_formats_are_rejected():
    comma = {**PREF, 'number_source': 'comma'}
    assert Numbers.inspect('1.234', comma).value == 1234
    assert Numbers.inspect('1.234', comma).rule == 'localized_number'
    assert Numbers.inspect('3.5', comma).status == 'invalid'
    assert Numbers.inspect('1,234', comma).value == Decimal('1.234')
    point = {**PREF, 'number_source': 'point'}
    assert Numbers.inspect('1,234', point).value == 1234
    assert Numbers.inspect('3,5', point).status == 'invalid'


def test_exact_integers_and_decimal_precision_are_not_rounded():
    integer = {**PREF, 'type': 'integer'}
    assert Numbers.convert('9007199254740993', integer) == 9007199254740993
    assert Numbers.convert('42.0', integer) == 42
    with pytest.raises(ValueError):
        Numbers.convert('42.7', integer)
    assert Numbers.convert('0.1234567890123456789', {**PREF, 'number_source': 'point'}) == Decimal('0.1234567890123456789')


def test_absolute_preserves_long_integer_magnitude(app):
    original = '-123456789012345678901234567890123456789'
    pref = {**PREF, 'type': 'integer', 'negative_policy': 'deny'}
    frame = pd.DataFrame({'quantity': [original]})
    issue = Treatments.issues(frame, {'quantity': pref})[0]
    config = SimpleNamespace(columns={'quantity': pref}, duplicates={'mode': 'none'}, ai_review={},
        treatments={'decisions': {issue['id']: {'action': 'absolute'}}})
    with app.app_context():
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='a.csv'), config, {}, app.config)
    assert result.cleaned.iloc[0, 0] == original[1:]


def test_ai_output_uses_target_not_source_format_and_preserves_recognized_magnitudes():
    pref = {**PREF, 'number_source': 'comma'}
    candidate = {'id': 'a', 'original': '3,5', 'target': pref}
    proposal = {'id': 'a', 'status': 'replace', 'value': '3.5', 'reason': 'Propuesta de prueba.'}
    # Grammar check: output is validated in target notation, not as source grouping.
    assert Proposals.validate({'proposals': [proposal]}, [candidate])['proposals'][0]['value'] == '3.5'
    candidate['original'] = 'dos'
    with pytest.raises(ValueError, match='Changed magnitude'):
        Proposals.validate({'proposals': [proposal]}, [candidate])
    candidate['original'] = '1.234'
    proposal['value'] = '1234'
    assert Proposals.validate({'proposals': [proposal]}, [candidate])['proposals'][0]['value'] == '1234'


def test_shared_diagnosis_separates_conversion_bounds_sign_ambiguity_and_invalid():
    pref = {**PREF, 'type': 'integer', 'negative_policy': 'deny', 'minimum': '0', 'maximum': '200'}
    frame = pd.DataFrame({'quantity': ['dos', 'doscientos uno', 'menos dos', '3.5', '1,234', 'dos o tres', None]})
    issues = Treatments.issues(frame, {'quantity': pref}, include_rows=True)
    assert {i['scenario']: i['rows'] for i in issues} == {
        'missing': [6], 'invalid': [3, 5], 'number_ambiguous': [4],
        'negative': [2], 'bounds': [1], 'numeric_convertible': [0]}
    for issue in issues:
        if issue['scenario'] != 'invalid' and issue['scenario'] != 'missing':
            with pytest.raises(ValueError):
                Treatments.parse({'action_'+i['id']: 'ai' if i == issue else 'keep' for i in issues}, issues)


def test_legacy_preferences_are_unchanged_and_catalog_is_persisted_explicitly():
    assert Numbers.inspect('dos', {'type': 'integer'}).status == 'invalid'
    assert Numbers.inspect('3,5', {'type': 'decimal', 'decimal_separator': '.'}).status == 'invalid'
    cards = [{'index': 0, 'name': 'quantity'}]
    pref = Columns.parse({'column_0': 'integer', 'column_0_numeric_rules': Numbers.VERSION,
                          'column_0_number_source': 'comma'}, cards)['quantity']
    assert pref['number_source'] == 'comma' and pref['numeric_rules'] == Numbers.VERSION
    for bad in [{'column_0_numeric_rules': 'invented'}, {'column_0_numeric_rules': Numbers.VERSION, 'column_0_number_source': 'invented'}]:
        with pytest.raises(ValueError):
            Columns.parse({'column_0': 'integer', **bad}, cards)


def test_single_approval_handles_thousand_values_without_provider_and_keep_is_respected(app):
    frame = pd.DataFrame({'quantity': ['dos'] * 1000 + ['3,5', '1,234', 'dos o tres']})
    issues = Treatments.issues(frame, {'quantity': PREF})
    config = SimpleNamespace(columns={'quantity': PREF}, duplicates={'mode': 'none'}, ai_review={},
        treatments=Treatments.parse({'action_'+i['id']: 'normalize_number' if i['scenario']=='numeric_convertible' else 'keep' for i in issues}, issues))
    before = frame.copy(deep=True)
    assert Proposals.candidates(frame, config, {}) == []
    with app.app_context(), patch('app.services.ai_providers.AIProviderFactory.create', side_effect=AssertionError('No AI calls')):
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), config, {}, app.config)
        assert result.cleaned['quantity'].iloc[:1000].tolist() == ['2.0'] * 1000
        assert result.cleaned['quantity'].iloc[1000] == '3.5'
        assert result.cleaned['quantity'].iloc[1001] == '1,234'
        assert result.cleaned['quantity'].iloc[1002] == 'dos o tres'
        assert sum(c['reason'].startswith('Regla sin IA:') for c in result.changes) == 1001
        kept = deepcopy(config)
        for issue in issues: kept.treatments['decisions'][issue['id']] = {'action': 'keep'}
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='source.csv'), kept, {}, app.config)
        assert result.cleaned['quantity'].iloc[0] == 'dos'
    pd.testing.assert_frame_equal(frame, before)


def test_numeric_wizard_range_snapshot_preview_and_export(app, client, auth):
    auth.register(); auth.login()
    original = b'id,quantity\nA,dos\nB,ciento veinte\nC,doscientos uno\n'
    client.post('/files/upload', data={'file': (BytesIO(original), 'quantity.csv')})
    root = '/files/cleaning/1/'
    class Inputs(HTMLParser):
        def __init__(self): super().__init__(); self.data = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('type') == 'hidden':
                self.data[attrs['name']] = attrs.get('value', '')
    def tokens(step):
        response = client.get(root+step); assert response.status_code == 200
        parser = Inputs(); parser.feed(response.text); return parser.data
    client.post(root+'columns', data={**tokens('columns'), 'column_0': 'identifier', 'column_1': 'integer',
        'column_1_numeric_rules': Numbers.VERSION, 'column_1_number_source': 'auto',
        'column_1_minimum': '0', 'column_1_maximum': '200', 'column_1_negative': 'deny'})
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode': 'none'})
    with app.app_context():
        columns = deepcopy(CleaningConfiguration.query.one().columns)
    frame = pd.DataFrame({'id': ['A', 'B', 'C'], 'quantity': ['dos', 'ciento veinte', 'doscientos uno']})
    issues = Treatments.issues(frame, columns)
    assert {i['scenario'] for i in issues} == {'bounds', 'numeric_convertible'}
    assert 'Números reconocidos por reglas' in client.get(root+'treatments').text
    payload = {**tokens('treatments'), **{'action_'+i['id']: 'quarantine' if i['scenario']=='bounds' else 'normalize_number' for i in issues}}
    client.post(root+'treatments', data=payload)
    assert 'Regla sin IA:' in client.get(root+'wizard-preview').text
    with patch('app.services.ai_providers.AIProviderFactory.create', side_effect=AssertionError('No AI calls')):
        response = client.post(root+'wizard-preview', data=tokens('wizard-preview'))
        assert '/wizard-result/' in response.location
    with app.app_context():
        version = DatasetVersion.query.one()
        assert version.metrics['after_rows'] == 2 and version.metrics['quarantined_rows'] == 1
        assert version.operations[0]['columns']['quantity']['maximum'] == '200'
        assert version.operations[0]['columns']['quantity']['numeric_rules'] == Numbers.VERSION
        version_id = version.id
    with ZipFile(BytesIO(client.get(root+f'export/{version_id}').data)) as archive:
        csv = archive.read('datos_limpios.csv').decode('utf-8-sig')
        assert 'A,2' in csv and 'B,120' in csv
        assert 'doscientos uno' in archive.read('cuarentena.csv').decode('utf-8-sig')
