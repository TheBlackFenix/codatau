from io import BytesIO
from unittest.mock import patch

import pandas as pd
import pytest

from app.extensions import db
from app.models.ai_analysis_run import AIAnalysisRun
from app.models.dashboard_configuration import DashboardConfiguration
from app.models.file_upload import FileUpload
from app.services.ai_dashboard_service import AIDashboardService
from app.services.ai_providers import AIProviderResult
from app.services.data_service import DataService
from app.services.dashboard_service import DashboardConfigurationError, DashboardService


class MetricsProvider:
    def __init__(self):
        self.calls = 0

    def generate_json(self, instructions, payload, schema):
        self.calls += 1
        assert 'español' in instructions
        assert 'preview' not in payload
        if 'domain' in schema['properties']:
            return AIProviderResult(data={
                'domain': 'Ventas', 'description': 'Ventas por ciudad', 'confidence': 0.9,
                'column_roles': [],
            })
        return AIProviderResult(data={
            'explanation': 'Ventas y diversidad de ciudades.',
            'metrics': [
                {'column': 'valor', 'aggregation': 'sum', 'reason': 'Ingreso total.'},
                {'column': 'ciudad', 'aggregation': 'unique_count', 'reason': 'Cobertura.'},
            ],
        }, input_tokens=30, output_tokens=20)


def test_catalog_supports_text_without_arithmetic():
    summary = DataService.get_summary(pd.DataFrame({'ciudad': ['A', 'B', None]}))
    assert summary['metric_catalog']['ciudad']['row_count'] == 3
    assert summary['metric_catalog']['ciudad']['unique_count'] == 2
    assert DashboardService.validate_layout([{'column': 'ciudad', 'aggregation': 'completeness'}], summary)
    with pytest.raises(DashboardConfigurationError):
        DashboardService.validate_layout([{'column': 'ciudad', 'aggregation': 'sum'}], summary)


def test_recommendation_rejects_identifiers_and_incompatible_metrics():
    summary = DataService.get_summary(pd.DataFrame({'id': [1, 2], 'ciudad': ['A', 'B']}))
    with pytest.raises(ValueError):
        AIDashboardService.validate({'explanation': 'Total.', 'metrics': [
            {'column': 'id', 'aggregation': 'sum', 'reason': 'Total.'},
        ]}, summary, {})
    with pytest.raises(ValueError):
        AIDashboardService.validate({'explanation': 'Total.', 'metrics': [
            {'column': 'ciudad', 'aggregation': 'sum', 'reason': 'Total.'},
        ]}, summary, {})


def test_recommendations_are_cached_and_require_acceptance(app, client, auth):
    auth.register()
    auth.login()
    client.post('/files/upload', data={'file': (BytesIO(b'ciudad,valor\nA,10\nB,20\n'), 'ventas.csv')})
    app.config.update(AI_PROVIDER='openai_compatible', AI_MODEL='fake', AI_BASE_URL='http://localhost/v1')
    provider = MetricsProvider()
    with patch('app.services.ai_providers.AIProviderFactory.create', return_value=provider):
        assert client.post('/dashboard/files/1/recommendations').status_code == 302
        assert client.post('/dashboard/files/1/recommendations').status_code == 302
    assert provider.calls == 2  # One context + one proposal, not two of each.
    with app.app_context():
        assert DashboardConfiguration.query.count() == 0
        run = AIAnalysisRun.query.filter_by(purpose=AIDashboardService.PURPOSE).one()
        run_id = run.id
    response = client.get('/dashboard')
    assert response.status_code == 200
    assert b'Crear dashboard con estas' in response.data
    client.post('/dashboard/files/1/recommendations/apply', data={'run_id': run_id})
    with app.app_context():
        assert len(DashboardConfiguration.query.one().metrics) == 2
        record = db.session.get(FileUpload, 1)
        summary = DataService.get_summary(pd.DataFrame({'ciudad': ['A', 'B'], 'valor': [10, 20]}))
        assert AIDashboardService.latest(record, summary, {'domain': 'Ventas', 'description': 'Ventas por ciudad', 'confidence': 0.9, 'column_roles': []}, app.config)
    auth.logout()
    auth.register('other', 'other@example.com')
    auth.login('other@example.com')
    assert client.post('/dashboard/files/1/recommendations').status_code == 404
