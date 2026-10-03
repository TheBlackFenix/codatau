from app.services.ai_prompts import SPANISH_OUTPUT, UNTRUSTED_DATA
from app.services.dashboard_service import DashboardService
from app.services.structured_ai_service import StructuredAIService


class AIDashboardService:
    PURPOSE = 'dashboard_recommendations'
    VERSION = '1-es'
    SCHEMA = {
        'type': 'object', 'additionalProperties': False,
        'required': ['metrics', 'explanation'],
        'properties': {
            'explanation': {'type': 'string'},
            'metrics': {'type': 'array', 'maxItems': 5, 'items': {
                'type': 'object', 'additionalProperties': False,
                'required': ['column', 'aggregation', 'reason'],
                'properties': {
                    'column': {'type': 'string'},
                    'aggregation': {'type': 'string', 'enum': list(DashboardService.AGGREGATION_LABELS)},
                    'reason': {'type': 'string'},
                },
            }},
        },
    }
    INSTRUCTIONS = SPANISH_OUTPUT + UNTRUSTED_DATA + (
        'Selecciona hasta cinco métricas distintas útiles para el negocio del archivo. '
        'Usa solo el catálogo y agregaciones compatibles suministrados. Prefiere '
        'diversidad: total, promedio, volumen, valores únicos o completitud. '
        'No sumes ni promedies identificadores, teléfonos, códigos ni dimensiones '
        'físicas sin sentido de negocio. No inventes cinco si solo hay menos útiles. '
        'Explica brevemente la elección de cada métrica y el conjunto.'
    )

    @staticmethod
    def payload(summary, context):
        catalog = summary.get('metric_catalog', summary['numeric_summary'])
        return {
            'context': context or {}, 'rows': summary['rows'],
            'catalog': [
                {'column': column, 'role': stats['role'],
                 'available_aggregations': [name for name in DashboardService.AGGREGATION_LABELS if name in stats],
                 'completeness': stats['completeness']}
                for column, stats in catalog.items()
            ],
        }

    @classmethod
    def validate(cls, data, summary, context):
        if not isinstance(data, dict) or set(data) != {'metrics', 'explanation'}:
            raise ValueError('Invalid dashboard response')
        if not isinstance(data['explanation'], str) or not data['explanation'].strip():
            raise ValueError('Missing explanation')
        metrics = data['metrics']
        if not isinstance(metrics, list) or not 0 < len(metrics) <= 5:
            raise ValueError('Expected one to five metrics')
        roles = {item['column']: item for item in (context or {}).get('column_roles', [])}
        for metric in metrics:
            if not isinstance(metric, dict) or set(metric) != {'column', 'aggregation', 'reason'}:
                raise ValueError('Invalid metric')
            if not isinstance(metric['reason'], str) or not metric['reason'].strip():
                raise ValueError('Missing reason')
            column = metric['column']
            role = roles.get(column, {})
            if metric['aggregation'] in {'sum', 'mean', 'min', 'max'}:
                if column in summary['identifier_cols'] or (
                    role and (role.get('role') == 'identifier' or not role.get('aggregate'))
                ):
                    raise ValueError('Cannot quantify this field')
        layout = DashboardService.validate_layout(metrics, summary)
        if len(layout) != len(metrics):
            raise ValueError('Duplicate metrics')
        return {'metrics': [
            {**item, 'reason': original['reason'].strip()[:300]}
            for item, original in zip(layout, metrics)
        ], 'explanation': data['explanation'].strip()[:800]}

    @classmethod
    def recommend(cls, record, summary, context, config, provider=None):
        return StructuredAIService.request(
            record, config, cls.PURPOSE, cls.VERSION, cls.INSTRUCTIONS,
            cls.payload(summary, context), cls.SCHEMA,
            lambda data: cls.validate(data, summary, context), provider,
        )

    @classmethod
    def latest(cls, record, summary, context, config):
        return StructuredAIService.cached(
            record, config, cls.PURPOSE, cls.VERSION, cls.payload(summary, context),
        )
