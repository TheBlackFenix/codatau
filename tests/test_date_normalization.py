from io import BytesIO
from html.parser import HTMLParser
from types import SimpleNamespace
from zipfile import ZipFile
from unittest.mock import patch

import pandas as pd
import pytest

from app.services.date_normalization_service import DateNormalizationService as Dates
from app.services.cleaning_treatment_service import CleaningTreatmentService as Treatments
from app.services.wizard_execution_service import WizardExecutionService as Executor
from app.services.duplicate_configuration_service import DuplicateConfigurationService as Duplicates
from app.services.value_proposal_service import ValueProposalService as Proposals
from app.models.dataset_version import DatasetVersion


PREF = {'type': 'date', 'date_format': 'dmy', 'date_source': 'auto'}


@pytest.mark.parametrize('value,expected', [
    ('2026-10-18', '2026-10-18'), ('18/10/2026', '2026-10-18'),
    ('10/18/2026', '2026-10-18'), ('18.10.2026', '2026-10-18'),
    ('18-10-2026', '2026-10-18'), ('04/04/2026', '2026-04-04'),
    (' 2026-10-18 ', '2026-10-18'), ('2024-02-29', '2024-02-29')])
def test_clear_dates_do_not_require_ai_or_individual_decisions(value, expected):
    assert Dates.convert(value, PREF).strftime('%Y-%m-%d') == expected


@pytest.mark.parametrize('value', ['31/02/2026', '2026-02-29', '2026-13-01', '25/25/2026',
                                  'ayer', 45000, True, '03/04/26', '2026-10-18junk'])
def test_invalid_dates_are_not_invented(value):
    assert Dates.inspect(value, PREF)[0] == 'invalid'
    with pytest.raises(ValueError):
        Dates.convert(value, PREF)


def test_source_order_is_independent_of_output_format_and_legacy_is_preserved():
    assert Dates.inspect('03/04/2026', PREF)[0] == 'ambiguous'
    assert Dates.convert('03/04/2026', {**PREF, 'date_source': 'dmy'}).month == 4
    assert Dates.convert('03/04/2026', {**PREF, 'date_source': 'mdy'}).month == 3
    assert Dates.convert('03/04/2026', {'type': 'date', 'date_format': 'dmy'}).month == 4
    for output in ['iso', 'mdy', 'dmy']:
        assert Dates.convert('2026-10-18', {**PREF, 'date_format': output}).day == 18


def test_preserve_time_precision_and_display_without_mutating_typed_dates():
    stamp = Dates.convert('2026-10-18T15:22:33.123456789', PREF)
    assert Dates.display(stamp, PREF) == '18/10/2026 15:22:33.123456789'
    assert stamp.nanosecond == 789
    frame = pd.DataFrame({'date': [stamp]})
    formatted = Dates.format_dataframe(frame, {'date': PREF})
    assert formatted.iloc[0, 0] == '18/10/2026 15:22:33.123456789'
    assert frame.iloc[0, 0] == stamp
    assert Dates.display('31/02/2026', PREF) == '31/02/2026'
    assert Dates.display('03/04/2026', PREF) == '03/04/2026'
    assert Dates.display('2026-10-18 15:22:33', PREF) == '18/10/2026 15:22:33'


def test_one_group_order_resolves_thousand_ambiguous_dates_without_ai(app):
    frame = pd.DataFrame({'date': ['03/04/2026'] * 1000 + ['2026-10-18', '31/02/2026']})
    columns = {'date': PREF}
    issues = Treatments.issues(frame, columns)
    assert {i['scenario']: i['count'] for i in issues} == {'date_ambiguous': 1000, 'invalid': 1}
    config = SimpleNamespace(columns=columns, duplicates={'mode': 'none'}, ai_review={},
        treatments=Treatments.parse({'action_'+i['id']: 'date_dmy' if i['scenario']=='date_ambiguous' else 'quarantine' for i in issues}, issues))
    assert Proposals.candidates(frame, config, {}) == []
    with app.app_context():
        result = Executor.build(frame, SimpleNamespace(active_stored_filename='dates.csv'), config, {}, app.config)
    assert len(result.cleaned) == 1001
    assert result.cleaned['date'].iloc[0] == pd.Timestamp('2026-04-03')
    assert result.cleaned['date'].iloc[1000] == pd.Timestamp('2026-10-18')
    assert result.metrics['quarantined_rows'] == 1
    assert frame.iloc[0, 0] == '03/04/2026'
    assert sum(c['reason'] == 'Orden de día y mes confirmado para el grupo' for c in result.changes) == 1000


def test_date_actions_cannot_be_forged_or_sent_to_ai():
    invalid = {'id': 'a', 'scenario': 'invalid', 'type': 'date'}
    for action in ['ai', 'date_dmy', 'date_mdy']:
        with pytest.raises(ValueError):
            Treatments.parse({'action_a': action}, [invalid])


def test_duplicates_latest_uses_same_date_interpretation():
    frame = pd.DataFrame({'id': ['A', 'A'], 'date': ['2026-10-18', '19/10/2026']})
    result = Duplicates.preview(frame, {'mode': 'keys', 'keys': ['id'], 'keep': 'latest', 'date_column': 'date'},
                                {'id': {'type': 'text'}, 'date': PREF}, include_indices=True)
    assert result['indices'] == [0]
    assert result['invalid_dates'] == 0


def test_date_wizard_preview_version_and_both_downloads(app, client, auth):
    auth.register(); auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'id,date\nA,2026-10-18\nB,03/04/2026\nC,31/02/2026\n'), 'dates.csv')})
    root = '/files/cleaning/1/'
    class Inputs(HTMLParser):
        def __init__(self): super().__init__(); self.data = {}
        def handle_starttag(self, tag, attrs):
            attrs = dict(attrs)
            if tag == 'input' and attrs.get('type') == 'hidden':
                self.data[attrs['name']] = attrs.get('value', '')
    def tokens(step):
        response = client.get(root + step)
        assert response.status_code == 200
        parser = Inputs(); parser.feed(response.text)
        return parser.data
    client.post(root+'columns', data={**tokens('columns'), 'column_0': 'identifier', 'column_1': 'date',
                                    'column_1_date': 'dmy', 'column_1_date_source': 'auto'})
    client.post(root+'duplicates', data={**tokens('duplicates'), 'mode': 'none'})
    page = client.get(root+'treatments').text
    assert '18/10/2026' in page
    assert 'Interpretar este grupo como día/mes/año' in page
    assert 'Pedir una propuesta a la IA' not in page
    frame = pd.DataFrame({'id': ['A', 'B', 'C'], 'date': ['2026-10-18', '03/04/2026', '31/02/2026']})
    issues = Treatments.issues(frame, {'id': {'type': 'identifier'}, 'date': PREF})
    client.post(root+'treatments', data={**tokens('treatments'), **{
        'action_'+i['id']: 'date_dmy' if i['scenario']=='date_ambiguous' else 'quarantine' for i in issues}})
    assert '18/10/2026' in client.get(root+'wizard-preview').text
    confirmation = tokens('wizard-preview')
    with patch.object(Executor, 'RULE_VERSION', 'newer-rules'):
        client.post(root+'wizard-preview', data=confirmation)
    with app.app_context():
        assert DatasetVersion.query.count() == 0
    client.post(root+'wizard-preview', data=confirmation)
    with app.app_context():
        version = DatasetVersion.query.one()
        assert version.operations[0]['columns']['date']['date_source'] == 'auto'
        version_id = version.id
    package = client.get(root+f'export/{version_id}')
    assert package.status_code == 200
    with ZipFile(BytesIO(package.data)) as archive:
        csv = archive.read('datos_limpios.csv').decode('utf-8-sig')
        assert '18/10/2026' in csv and '03/04/2026' in csv
    csv = client.get('/reports/download/1')
    assert csv.status_code == 200
    assert '18/10/2026' in csv.data.decode('utf-8-sig')
