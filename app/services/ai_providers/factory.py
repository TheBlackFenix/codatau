from app.services.ai_providers.base import (
    AIProviderError,
    ProviderConfiguration,
)
from app.services.ai_providers.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAIResponsesProvider,
)


class AIProviderFactory:
    PROVIDERS = {
        'openai_responses': OpenAIResponsesProvider,
        'openai_compatible': OpenAICompatibleChatProvider,
    }

    @classmethod
    def configuration_from_app(cls, app_config):
        return ProviderConfiguration(
            provider=str(app_config.get('AI_PROVIDER', 'disabled')).strip().lower(),
            model=str(app_config.get('AI_MODEL', '')).strip(),
            api_key=str(app_config.get('AI_API_KEY', '')).strip(),
            base_url=str(app_config.get('AI_BASE_URL', '')).strip(),
            response_mode=str(app_config.get('AI_RESPONSE_MODE', 'json_schema')).strip(),
            timeout_seconds=int(app_config.get('AI_TIMEOUT_SECONDS', 30)),
            max_output_tokens=int(app_config.get('AI_MAX_OUTPUT_TOKENS', 1200)),
        )

    @classmethod
    def is_configured(cls, app_config):
        configuration = cls.configuration_from_app(app_config)
        return (
            configuration.provider in cls.PROVIDERS
            and bool(configuration.model)
            and bool(configuration.base_url)
        )

    @classmethod
    def create(cls, app_config):
        configuration = cls.configuration_from_app(app_config)
        if configuration.provider == 'disabled':
            raise AIProviderError(
                'provider_disabled',
                'La integración con IA todavía no está configurada.',
            )
        provider_class = cls.PROVIDERS.get(configuration.provider)
        if provider_class is None:
            raise AIProviderError(
                'unknown_provider',
                f'El proveedor “{configuration.provider}” no está soportado.',
            )
        if not configuration.model or not configuration.base_url:
            raise AIProviderError(
                'incomplete_provider_configuration',
                'Completa AI_MODEL y AI_BASE_URL para habilitar la IA.',
            )
        if configuration.response_mode not in {'json_schema', 'json_object'}:
            raise AIProviderError(
                'invalid_response_mode',
                'AI_RESPONSE_MODE debe ser json_schema o json_object.',
            )
        return provider_class(configuration)
