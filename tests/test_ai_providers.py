import pytest

from app.services.ai_providers import AIProviderError, ProviderConfiguration
from app.services.ai_providers.factory import AIProviderFactory
from app.services.ai_providers.openai_compatible import (
    OpenAICompatibleChatProvider,
    OpenAIResponsesProvider,
)


SCHEMA = {
    'type': 'object',
    'additionalProperties': False,
    'required': ['suggestions'],
    'properties': {'suggestions': {'type': 'array', 'items': {'type': 'object'}}},
}


def _configuration(provider='openai_responses', response_mode='json_schema'):
    return ProviderConfiguration(
        provider=provider,
        model='test-model',
        api_key='secret-token',
        base_url='https://provider.example/v1/',
        response_mode=response_mode,
        timeout_seconds=9,
        max_output_tokens=321,
    )


def test_responses_provider_uses_strict_schema_without_server_storage():
    captured = {}

    def transport(url, headers, payload, timeout):
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {
            'id': 'resp_123',
            'output_text': '{"suggestions":[]}',
            'usage': {'input_tokens': 40, 'output_tokens': 8},
        }

    result = OpenAIResponsesProvider(_configuration(), transport).generate_json(
        'instructions', {'dataset': 'minimal'}, SCHEMA
    )

    assert captured['url'] == 'https://provider.example/v1/responses'
    assert captured['headers']['Authorization'] == 'Bearer secret-token'
    assert captured['payload']['store'] is False
    assert captured['payload']['text']['format']['strict'] is True
    assert captured['payload']['text']['format']['schema'] == SCHEMA
    assert captured['payload']['max_output_tokens'] == 321
    assert captured['timeout'] == 9
    assert result.data == {'suggestions': []}
    assert result.provider_request_id == 'resp_123'
    assert result.input_tokens == 40
    assert result.output_tokens == 8


def test_openai_compatible_provider_supports_schema_and_json_modes():
    calls = []

    def transport(url, headers, payload, timeout):
        calls.append(payload)
        return {
            'choices': [{'message': {'content': '{"suggestions":[]}'}}],
            'usage': {'prompt_tokens': 20, 'completion_tokens': 4},
        }

    schema_result = OpenAICompatibleChatProvider(
        _configuration('openai_compatible'), transport
    ).generate_json('instructions', {}, SCHEMA)
    json_result = OpenAICompatibleChatProvider(
        _configuration('openai_compatible', 'json_object'), transport
    ).generate_json('instructions', {}, SCHEMA)

    assert calls[0]['response_format']['type'] == 'json_schema'
    assert calls[0]['response_format']['json_schema']['schema'] == SCHEMA
    assert calls[1]['response_format'] == {'type': 'json_object'}
    assert schema_result.input_tokens == 20
    assert json_result.output_tokens == 4


def test_provider_rejects_non_json_structured_output():
    def transport(*_args):
        return {'output_text': 'not-json'}

    provider = OpenAIResponsesProvider(_configuration(), transport)

    with pytest.raises(AIProviderError, match='estructura esperada') as raised:
        provider.generate_json('instructions', {}, SCHEMA)

    assert raised.value.code == 'invalid_structured_output'


def test_factory_is_disabled_by_default_and_validates_provider():
    disabled = {'AI_PROVIDER': 'disabled'}
    unknown = {
        'AI_PROVIDER': 'mystery',
        'AI_MODEL': 'x',
        'AI_BASE_URL': 'http://localhost:9999/v1',
    }

    assert AIProviderFactory.is_configured(disabled) is False
    with pytest.raises(AIProviderError) as disabled_error:
        AIProviderFactory.create(disabled)
    with pytest.raises(AIProviderError) as unknown_error:
        AIProviderFactory.create(unknown)

    assert disabled_error.value.code == 'provider_disabled'
    assert unknown_error.value.code == 'unknown_provider'


@pytest.mark.parametrize('provider_class,token_field,partial,complete', [
    (OpenAICompatibleChatProvider, 'max_tokens',
     {'choices': [{'finish_reason': 'length', 'message': {'content': '{"suggestions":'}}]},
     {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"suggestions":[]}'}}]}),
    (OpenAIResponsesProvider, 'max_output_tokens',
     {'status': 'incomplete', 'incomplete_details': {'reason': 'max_output_tokens'},
      'output_text': '{"suggestions":'},
     {'status': 'completed', 'output_text': '{"suggestions":[]}'}),
])
def test_truncated_output_retries_once_with_bounded_budget_and_combined_usage(
    provider_class, token_field, partial, complete,
):
    calls = []

    def transport(_url, _headers, body, _timeout):
        calls.append(body)
        return {**(partial if len(calls) == 1 else complete),
                'usage': {'input_tokens': 10, 'output_tokens': 20,
                          'prompt_tokens': 10, 'completion_tokens': 20}}

    result = provider_class(_configuration(), transport).generate_json('instructions', {}, SCHEMA)
    assert [body[token_field] for body in calls] == [321, 642]
    assert calls[0]['model'] == calls[1]['model']
    assert result.data == {'suggestions': []}
    assert result.input_tokens == 20
    assert result.output_tokens == 40


def test_repeated_truncation_never_accepts_even_valid_partial_json():
    from dataclasses import replace
    calls = []

    def transport(_url, _headers, body, _timeout):
        calls.append(body)
        return {'choices': [{'finish_reason': 'length', 'message': {'content': '{"suggestions":[]}'}}]}

    config = replace(_configuration('openai_compatible'), max_output_tokens=8000)
    with pytest.raises(AIProviderError) as raised:
        OpenAICompatibleChatProvider(config, transport).generate_json('instructions', {}, SCHEMA)
    assert raised.value.code == 'truncated_structured_output'
    assert [body['max_tokens'] for body in calls] == [8000, 12096]


def test_malformed_output_without_token_exhaustion_is_not_retried():
    calls = []

    def transport(*args):
        calls.append(args)
        return {'choices': [{'finish_reason': 'stop', 'message': {'content': 'not-json'}}]}

    with pytest.raises(AIProviderError) as raised:
        OpenAICompatibleChatProvider(_configuration(), transport).generate_json('instructions', {}, SCHEMA)
    assert raised.value.code == 'invalid_structured_output'
    assert len(calls) == 1
