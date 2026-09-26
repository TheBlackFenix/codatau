import pytest

from app.extensions import db
from app.models.ai_analysis_run import AIAnalysisRun
from app.models.file_upload import FileUpload
from app.models.user import User
from app.services.ai_providers import AIProviderResult
from app.services.dataset_context_service import DatasetContextService


class FakeProvider:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def generate_json(self, instructions, payload, schema):
        self.calls.append((instructions, payload, schema))
        return AIProviderResult(
            data=self.data,
            provider_request_id='context-request',
            input_tokens=140,
            output_tokens=48,
        )


def _config(**overrides):
    config = {
        'AI_PROVIDER': 'openai_compatible',
        'AI_MODEL': 'context-model',
        'AI_API_KEY': '',
        'AI_BASE_URL': 'http://provider.test/v1',
        'AI_RESPONSE_MODE': 'json_schema',
        'AI_TIMEOUT_SECONDS': 5,
        'AI_MAX_OUTPUT_TOKENS': 500,
        'AI_CONTEXT_SAMPLE_ROWS': 5,
        'AI_CONTEXT_MAX_COLUMNS': 80,
    }
    config.update(overrides)
    return config


def _profile():
    return {
        'source_sha256': 'b' * 64,
        'row_count': 1000,
        'column_count': 4,
        'columns': [
            {
                'name': 'numero_guia',
                'type': 'BIGINT',
                'null_ratio': 0,
                'approx_unique': 1000,
                'semantic': {'type': 'identifier', 'confidence': 0.98},
            },
            {
                'name': 'nombre',
                'type': 'VARCHAR',
                'null_ratio': 0.02,
                'approx_unique': 900,
                'semantic': {'type': 'text', 'confidence': 0.85},
            },
            {
                'name': 'correo',
                'type': 'VARCHAR',
                'null_ratio': 0.04,
                'approx_unique': 850,
                'semantic': {'type': 'email', 'confidence': 0.9},
            },
            {
                'name': 'largo',
                'type': 'DOUBLE',
                'null_ratio': 0,
                'approx_unique': 980,
                'numeric': {'min': -10.0, 'max': 200.0, 'mean': 50.0},
                'semantic': {'type': 'number', 'confidence': 0.98},
            },
        ],
        'sample': [
            {
                'numero_guia': 700194502537,
                'nombre': 'Laura Díaz',
                'correo': 'laura@example.com',
                'largo': -10.0,
            },
            {
                'numero_guia': 700195121690,
                'nombre': 'Ana Rodríguez',
                'correo': 'correo_invalido.com',
                'largo': 20.5,
            },
        ],
    }


def _record():
    user = User(username='owner', email='owner@example.com')
    user.set_password('secret1')
    record = FileUpload(
        owner=user,
        filename='stored.csv',
        original_name='envios.csv',
        file_type='csv',
    )
    db.session.add_all([user, record])
    db.session.commit()
    return user, record


def _valid_result():
    return {
        'domain': 'Envíos y mensajería',
        'description': 'Registros de paquetes, contacto y dimensiones físicas.',
        'confidence': 0.94,
        'column_roles': [
            {
                'column': 'numero_guia',
                'role': 'identifier',
                'description': 'Identificador único del envío.',
                'aggregate': False,
                'suggested_constraints': ['unique', 'not_empty'],
            },
            {
                'column': 'largo',
                'role': 'dimension',
                'description': 'Dimensión física longitudinal del paquete.',
                'aggregate': True,
                'suggested_constraints': ['positive'],
            },
        ],
    }


def test_request_context_is_compact_representative_and_redacted():
    context = DatasetContextService.build_request_context(_profile(), _config())

    assert context['row_count'] == 1000
    assert context['sample_columns'] == [
        'numero_guia',
        'nombre',
        'correo',
        'largo',
    ]
    assert context['sample_rows'][0] == [
        '<redacted:identifier:length=12>',
        '<redacted:personal>',
        '<masked:email>',
        '-10.0',
    ]
    assert context['headers'][3]['numeric_range']['min'] == -10.0
    serialized = str(context)
    assert 'Laura' not in serialized
    assert 'laura@example.com' not in serialized


def test_context_is_validated_audited_and_cached(app):
    provider = FakeProvider(_valid_result())
    with app.app_context():
        user, record = _record()
        first = DatasetContextService.analyze(
            record,
            _profile(),
            user.id,
            _config(),
            provider=provider,
        )
        db.session.commit()
        second = DatasetContextService.analyze(
            record,
            _profile(),
            user.id,
            _config(),
            provider=provider,
        )

        assert first.cached is False
        assert second.cached is True
        assert first.context['domain'] == 'Envíos y mensajería'
        assert first.context['column_roles'][1]['suggested_constraints'] == [
            'positive'
        ]
        assert len(provider.calls) == 1
        run = AIAnalysisRun.query.one()
        assert run.purpose == 'dataset_context'
        assert run.status == 'success'
        assert run.input_tokens == 140
        assert run.output_tokens == 48


@pytest.mark.parametrize(
    'mutation',
    [
        lambda result: result['column_roles'][0].update(column='inventada'),
        lambda result: result['column_roles'][1].update(column='numero_guia'),
        lambda result: result['column_roles'][1].update(
            suggested_constraints=['execute_sql']
        ),
    ],
)
def test_context_rejects_invented_columns_duplicates_and_rules(mutation):
    result = _valid_result()
    mutation(result)

    with pytest.raises(ValueError):
        DatasetContextService.validate_result(result, _profile())
