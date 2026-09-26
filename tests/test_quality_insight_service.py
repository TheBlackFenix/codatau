from types import SimpleNamespace

from app.services.quality_insight_service import QualityInsightService


def _profile():
    return {
        'cleaning_plan': {
            'operations': [
                {
                    'id': 'email:handle_missing',
                    'column': 'email',
                    'operation': 'handle_missing',
                },
                {
                    'id': 'nombre:handle_missing',
                    'column': 'nombre',
                    'operation': 'handle_missing',
                },
                {
                    'id': 'alto:validate_range:positive',
                    'column': 'alto',
                    'operation': 'validate_range',
                },
                {
                    'id': 'dataset:remove_exact_duplicates',
                    'column': None,
                    'operation': 'remove_exact_duplicates',
                },
            ]
        }
    }


def _decision(operation_id, choice):
    return SimpleNamespace(
        operation_id=operation_id,
        choice=choice,
        is_active=True,
    )


def test_quality_insights_report_pending_partial_and_respected_decisions():
    insights = [
        {'code': 'null_values', 'column': None, 'message': 'Hay nulos.'},
        {'code': 'negative_values', 'column': 'alto', 'message': 'Hay negativos.'},
        {'code': 'duplicate_rows', 'column': None, 'message': 'Hay duplicados.'},
        {'code': 'small_dataset', 'column': None, 'message': 'Pocas filas.'},
    ]
    decisions = [
        _decision('email:handle_missing', 'keep'),
        _decision('alto:validate_range:positive', 'keep'),
        _decision('dataset:remove_exact_duplicates', 'apply'),
    ]

    decorated = QualityInsightService.decorate(insights, _profile(), decisions)
    by_code = {item['code']: item for item in decorated}

    assert by_code['null_values']['status'] == 'partial'
    assert by_code['null_values']['resolved_count'] == 1
    assert by_code['null_values']['operation_count'] == 2
    assert by_code['negative_values']['status'] == 'kept'
    assert by_code['negative_values']['status_label'] == (
        'Conservado por tu decisión'
    )
    assert by_code['duplicate_rows']['status'] == 'managed'
    assert by_code['small_dataset']['status'] == 'informational'


def test_quality_insight_without_decisions_remains_actionable_and_pending():
    decorated = QualityInsightService.decorate(
        [{'code': 'negative_values', 'column': 'alto', 'message': 'Hay negativos.'}],
        _profile(),
        [],
    )

    assert decorated[0]['actionable'] is True
    assert decorated[0]['status'] == 'pending'
    assert decorated[0]['operation_ids'] == [
        'alto:validate_range:positive'
    ]
