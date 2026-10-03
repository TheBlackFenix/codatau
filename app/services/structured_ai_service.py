import hashlib
import json

from app.extensions import db
from app.models.ai_analysis_run import AIAnalysisRun
from app.services.ai_providers import AIProviderError, AIProviderFactory


class StructuredAIService:
    """Shared auditing and version-scoped cache for structured AI tasks."""

    @staticmethod
    def fingerprint(record, configuration, purpose, version, payload):
        encoded = json.dumps({
            'purpose': purpose,
            'prompt_version': version,
            'dataset_version': record.active_stored_filename,
            'provider': configuration.provider,
            'model': configuration.model,
            'endpoint': configuration.base_url.rstrip('/'),
            'payload': payload,
        }, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
        return hashlib.sha256(encoded.encode('utf-8')).hexdigest()

    @classmethod
    def cached(cls, record, config, purpose, version, payload):
        configuration = AIProviderFactory.configuration_from_app(config)
        fingerprint = cls.fingerprint(record, configuration, purpose, version, payload)
        return AIAnalysisRun.query.filter_by(
            user_id=record.user_id, file_id=record.id, purpose=purpose,
            request_fingerprint=fingerprint, status='success',
        ).order_by(AIAnalysisRun.id.desc()).first()

    @classmethod
    def request(cls, record, config, purpose, version, instructions, payload,
                schema, validate, provider=None):
        cached = cls.cached(record, config, purpose, version, payload)
        if cached:
            return cached
        configuration = AIProviderFactory.configuration_from_app(config)
        provider = provider or AIProviderFactory.create(config)
        run = AIAnalysisRun(
            user_id=record.user_id, file_id=record.id, purpose=purpose,
            provider=configuration.provider, model=configuration.model,
            status='running', candidate_count=0,
            request_fingerprint=cls.fingerprint(record, configuration, purpose, version, payload),
        )
        db.session.add(run)
        try:
            result = provider.generate_json(instructions, payload, schema)
            run.result = validate(result.data)
            run.input_tokens = result.input_tokens
            run.output_tokens = result.output_tokens
            run.provider_request_id = result.provider_request_id
            run.status = 'success'
        except AIProviderError as error:
            run.status = 'error'
            run.error_code = error.code
            raise
        except (ValueError, TypeError, KeyError) as error:
            run.status = 'error'
            run.error_code = 'invalid_analysis_output'
            raise AIProviderError(
                'invalid_analysis_output',
                'La IA devolvió una propuesta inválida. Intenta de nuevo o ajusta la configuración manualmente.',
            ) from error
        return run
