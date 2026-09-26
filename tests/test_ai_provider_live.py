import json
import os

import pytest

from app import create_app
from app.services.ai_cleaning_service import AICleaningService
from app.services.ai_providers import AIProviderError, AIProviderFactory


@pytest.mark.skipif(
    os.environ.get('RUN_LIVE_AI_TEST') != '1',
    reason='La prueba en vivo consume la API y debe habilitarse explícitamente.',
)
def test_configured_provider_returns_a_valid_cleaning_recommendation():
    """Opt-in smoke test for the provider configured in the local environment."""
    application = create_app()
    assert AIProviderFactory.is_configured(application.config)

    candidate = {
        'operation_id': 'amount:cast_type',
        'column': 'amount',
        'physical_type': 'VARCHAR',
        'semantic_type': 'number',
        'semantic_confidence': 0.8,
        'null_count': 0,
        'approx_unique': 3,
        'evidence': {'number_count': 2},
        'operation': 'cast_type',
        'affected_rows': 3,
        'reason': 'Un valor rompe el patrón numérico predominante.',
        'current_parameters': {
            'target_type': 'DOUBLE',
            'decimal_separator': '.',
        },
        'allowed_response_parameters': {
            'decimal_separator': ['.', ','],
        },
        'redacted_samples': ['10.5', '22.1', 'error'],
    }
    provider = AIProviderFactory.create(application.config)
    try:
        result = provider.generate_json(
            AICleaningService.INSTRUCTIONS,
            {
                'analysis_version': 'live-smoke-test',
                'dataset': {
                    'row_count': 3,
                    'candidate_count': 1,
                    'candidates': [candidate],
                },
            },
            AICleaningService.OUTPUT_SCHEMA,
        )
    except AIProviderError as error:
        pytest.fail(
            f'{error.code}: {error.user_message} Detalle: {error.detail}',
            pytrace=False,
        )

    try:
        suggestions = AICleaningService.validate_result(
            result.data,
            [candidate],
        )
    except ValueError as error:
        pytest.fail(
            f'Respuesta rechazada: {error}. JSON: '
            f'{json.dumps(result.data, ensure_ascii=False)}',
            pytrace=False,
        )
    assert len(suggestions) == 1
    assert suggestions[0]['operation_id'] == candidate['operation_id']
