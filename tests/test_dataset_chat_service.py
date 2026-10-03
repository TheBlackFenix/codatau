from copy import deepcopy
from io import BytesIO
from unittest.mock import patch

import pandas as pd
import pytest

from app.extensions import db
from app.models.ai_analysis_run import AIAnalysisRun
from app.models.dataset_version import DatasetVersion
from app.models.file_upload import FileUpload
from app.services.ai_providers import AIProviderResult
from app.services.dataset_chat_service import DatasetChatService as Chat
from app.services.structured_ai_service import StructuredAIService
from app.services.ai_providers import AIProviderFactory


def frame():
    return pd.DataFrame({'valor': [100., 200., 250., None],
                         'fecha': ['2026-07-01', '2026-08-01', '2026-08-15', 'no-fecha'],
                         'ciudad': ['A', 'B', 'B', 'A'], 'id': [1, 2, 3, 4]})


def plan(kind='compare'):
    return {'kind': kind, 'metric': 'valor', 'aggregation': 'sum', 'group_by': None,
            'date_column': 'fecha' if kind == 'compare' else None, 'granularity': 'month', 'filters': [],
            'periods': [{'label': 'Julio', 'start': '2026-07-01', 'end': '2026-08-01'},
                        {'label': 'Agosto', 'start': '2026-08-01', 'end': '2026-09-01'}] if kind == 'compare' else [],
            'clarification': ''}


def test_comparison_uses_all_rows_and_reports_exclusions():
    data = frame()
    validated = Chat.validate(plan(), Chat.catalog(data, {}), 'Variación de valor 2026', [])
    result = Chat.execute(data, {}, validated)
    assert result['evidence']['variation'] == 350
    assert result['evidence']['variation_percentage'] == 350
    assert result['evidence']['dataset_rows'] == 4
    assert result['evidence']['date_rows_excluded'] == 1
    assert result['evidence']['rows'][1]['rows'] == 2


def test_zero_baseline_and_absent_period_are_not_invented():
    data = frame()
    data.loc[0, 'valor'] = 0
    result = Chat.execute(data, {}, plan())
    assert result['evidence']['variation'] == 450
    assert result['evidence']['variation_percentage'] is None
    assert 'base es cero' in result['answer']
    result = Chat.execute(data.iloc[1:], {}, plan())
    assert result['evidence']['variation'] is None
    assert 'No hay datos suficientes' in result['answer']


def test_multiple_years_require_explicit_clarification():
    data = frame()
    data.loc[3, 'fecha'] = '2025-07-03'
    validated = Chat.validate(plan(), Chat.catalog(data, {}), 'Variación entre julio y agosto', [])
    assert validated['kind'] == 'clarify'
    assert Chat.execute(data, {}, validated)['evidence'] is None


def test_ambiguous_date_strings_require_etl_configuration():
    data = frame()
    data['fecha'] = ['01/07/2026', '02/08/2026', '03/08/2026', 'no-fecha']
    assert 'fecha' not in Chat.date_series(data, {})
    with pytest.raises(ValueError, match='date column'):
        Chat.validate(plan(), Chat.catalog(data, {}), '2026', [])


def test_changing_provider_endpoint_or_version_invalidates_cache():
    from types import SimpleNamespace
    config = {'AI_PROVIDER': 'openai_compatible', 'AI_MODEL': 'same', 'AI_BASE_URL': 'http://localhost/one'}
    record = SimpleNamespace(active_stored_filename='one.csv')
    original = StructuredAIService.fingerprint(record, AIProviderFactory.configuration_from_app(config), Chat.PURPOSE, Chat.VERSION, {})
    config['AI_BASE_URL'] = 'http://localhost/two'
    assert original != StructuredAIService.fingerprint(record, AIProviderFactory.configuration_from_app(config), Chat.PURPOSE, Chat.VERSION, {})
    config['AI_BASE_URL'] = 'http://localhost/one'
    record.active_stored_filename = 'two.csv'
    assert original != StructuredAIService.fingerprint(record, AIProviderFactory.configuration_from_app(config), Chat.PURPOSE, Chat.VERSION, {})


@pytest.mark.parametrize('alteration', [
    {'metric': 'inventado'}, {'metric': 'id'}, {'aggregation': 'eval'},
    {'granularity': 'second'}, {'sql': 'DROP TABLE dataset'},
    {'filters': [{'column': 'valor', 'operator': 'eq', 'value': 'NaN'}]},
    {'periods': [{'label': 'x', 'start': '2026-01-01', 'end': '2026-01-01'}]},
])
def test_invalid_queries_never_execute(alteration):
    candidate = {**plan(), **alteration}
    with pytest.raises((ValueError, TypeError)):
        Chat.validate(candidate, Chat.catalog(frame(), {}), '2026', [])


def test_filters_are_parameters_and_identifiers_are_quoted():
    data = frame().rename(columns={'ciudad': 'ciudad"extra'})
    candidate = plan('aggregate')
    candidate['filters'] = [{'column': 'ciudad"extra', 'operator': 'eq', 'value': "A' OR 1=1 --"}]
    Chat.validate(candidate, Chat.catalog(data, {}), 'Consulta', [])
    assert Chat.execute(data, {}, candidate)['evidence']['rows'][0]['rows'] == 0
    candidate['filters'][0]['value'] = 'B'
    assert Chat.execute(data, {}, candidate)['evidence']['rows'][0]['value'] == 450


def test_group_counts_distinct_and_datetime_buckets():
    candidate = plan('group')
    candidate.update(metric=None, aggregation='count', group_by='ciudad')
    assert [item['value'] for item in Chat.execute(frame(), {}, candidate)['evidence']['rows']] == [2, 2]
    candidate.update(metric='id', aggregation='unique_count', group_by=None, date_column='fecha')
    result = Chat.execute(frame(), {}, candidate)
    assert len(result['evidence']['rows']) == 2
    assert result['evidence']['rows'][0]['value'] == 2


def test_grouping_a_date_field_never_uses_raw_seconds():
    data = frame()
    data['fecha'] = ['2026-08-01 10:01:20', '2026-08-01 10:55:59', '2026-08-01 11:03:22', None]
    candidate = {**plan('group'), 'group_by': 'fecha', 'granularity': 'hour', 'metric': None, 'aggregation': 'count'}
    validated = Chat.validate(candidate, Chat.catalog(data, {}), 'Registros por hora', [])
    assert validated['group_by'] is None
    result = Chat.execute(data, {}, validated)
    assert len(result['evidence']['rows']) == 2
    assert result['evidence']['rows'][0]['value'] == 2
    assert all(':00:00' in row['label'] for row in result['evidence']['rows'])


def test_multiple_sales_measures_require_clarification():
    data = frame().rename(columns={'valor': 'valor_total'})
    data['valor_comercial'] = 100
    candidate = {**plan(), 'metric': 'valor_total'}
    assert Chat.validate(candidate, Chat.catalog(data, {}), 'Variación de ventas 2026', [])['kind'] == 'clarify'
    assert Chat.validate(candidate, Chat.catalog(data, {}), 'Variación de valor total 2026', [])['kind'] == 'compare'


class ChatProvider:
    def __init__(self, data=None):
        self.data = data or plan()
        self.calls = []

    def generate_json(self, instructions, payload, schema):
        assert 'español' in instructions
        assert 'sample' not in payload
        assert 'preview' not in payload
        self.calls.append(payload)
        return AIProviderResult(data=deepcopy(self.data), input_tokens=40, output_tokens=20)


def upload(client, auth):
    auth.register()
    auth.login()
    client.post('/files/upload', data={'file': (BytesIO(frame().to_csv(index=False).encode()), 'ventas.csv')})


def test_chat_is_cached_persistent_owned_and_version_scoped(app, client, auth):
    upload(client, auth)
    app.config.update(AI_PROVIDER='openai_compatible', AI_MODEL='fake', AI_BASE_URL='http://localhost/v1')
    state = client.get('/dashboard/files/1/chat').json
    payload = {'question': 'Variación de valor entre julio y agosto de 2026', 'version': state['version']}
    provider = ChatProvider()
    with patch('app.services.ai_providers.AIProviderFactory.create', return_value=provider):
        first = client.post('/dashboard/files/1/chat', json=payload)
        second = client.post('/dashboard/files/1/chat', json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json['evidence']['variation'] == 350
    assert len(provider.calls) == 1
    assert len(client.get('/dashboard/files/1/chat').json['messages']) == 1
    with app.app_context():
        record = db.session.get(FileUpload, 1)
        version = DatasetVersion(created_by=record.user_id, file_id=record.id, version_number=1,
                                 stored_filename='new-version.csv',
                                 is_active=True, operations=[], metrics={})
        db.session.add(version)
        db.session.commit()
    assert client.post('/dashboard/files/1/chat', json=payload).status_code == 409
    assert client.get('/dashboard/files/1/chat').json['messages'] == []
    auth.logout()
    auth.register('other', 'other@example.com')
    auth.login('other@example.com')
    assert client.get('/dashboard/files/1/chat').status_code == 404
    assert client.post('/dashboard/files/1/chat', json=payload).status_code == 404


def test_chat_audits_invalid_provider_response_without_leaking_it(app, client, auth):
    upload(client, auth)
    app.config.update(AI_PROVIDER='openai_compatible', AI_MODEL='fake', AI_BASE_URL='http://localhost/v1')
    version = client.get('/dashboard/files/1/chat').json['version']
    with patch('app.services.ai_providers.AIProviderFactory.create', return_value=ChatProvider({'sql': 'secret'})):
        result = client.post('/dashboard/files/1/chat', json={'question': 'Total', 'version': version})
    assert result.status_code == 502
    assert 'secret' not in str(result.json)
    with app.app_context():
        run = AIAnalysisRun.query.filter_by(purpose=Chat.PURPOSE).one()
        assert run.status == 'error'
        assert run.error_code == 'invalid_analysis_output'


def test_chat_rejects_oversized_and_malformed_questions(app, client, auth):
    upload(client, auth)
    version = client.get('/dashboard/files/1/chat').json['version']
    for question in ['', 'x' * 1001, ['bad']]:
        assert client.post('/dashboard/files/1/chat', json={'question': question, 'version': version}).status_code == 400
    assert client.post('/dashboard/files/1/chat', json=['not an object']).status_code == 400
