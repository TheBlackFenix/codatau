import json

from app.services.ai_providers.base import (
    AIProviderError,
    AIProviderResult,
)
from app.services.ai_providers.http import post_json


def _endpoint(base_url, path):
    return f'{base_url.rstrip("/")}/{path.lstrip("/")}'


def _authorization(api_key):
    return {'Authorization': f'Bearer {api_key}'} if api_key else {}


def _load_structured_text(text):
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as error:
        raise AIProviderError(
            'invalid_structured_output',
            'La IA no devolvió la estructura esperada. No se aplicó ningún cambio.',
            str(error),
        ) from error
    if not isinstance(data, dict):
        raise AIProviderError(
            'invalid_structured_output',
            'La IA no devolvió la estructura esperada. No se aplicó ningún cambio.',
        )
    return data


class OpenAIResponsesProvider:
    def __init__(self, configuration, transport=post_json):
        self.configuration = configuration
        self.transport = transport

    def generate_json(self, instructions, payload, schema):
        body = {
            'model': self.configuration.model,
            'instructions': instructions,
            'input': json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
            'text': {
                'format': {
                    'type': 'json_schema',
                    'name': 'codatau_cleaning_analysis',
                    'strict': True,
                    'schema': schema,
                }
            },
            'max_output_tokens': self.configuration.max_output_tokens,
            'store': False,
        }
        response = self.transport(
            _endpoint(self.configuration.base_url, 'responses'),
            _authorization(self.configuration.api_key),
            body,
            self.configuration.timeout_seconds,
        )
        text = response.get('output_text')
        if not text:
            for item in response.get('output', []):
                for content in item.get('content', []):
                    if content.get('type') == 'output_text' and content.get('text'):
                        text = content['text']
                        break
                if text:
                    break
        usage = response.get('usage') or {}
        return AIProviderResult(
            data=_load_structured_text(text),
            provider_request_id=response.get('id'),
            input_tokens=usage.get('input_tokens'),
            output_tokens=usage.get('output_tokens'),
        )


class OpenAICompatibleChatProvider:
    def __init__(self, configuration, transport=post_json):
        self.configuration = configuration
        self.transport = transport

    def generate_json(self, instructions, payload, schema):
        response_format = {'type': 'json_object'}
        if self.configuration.response_mode == 'json_schema':
            response_format = {
                'type': 'json_schema',
                'json_schema': {
                    'name': 'codatau_cleaning_analysis',
                    'strict': True,
                    'schema': schema,
                },
            }
        body = {
            'model': self.configuration.model,
            'messages': [
                {'role': 'system', 'content': instructions},
                {
                    'role': 'user',
                    'content': json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(',', ':'),
                    ),
                },
            ],
            'response_format': response_format,
            'max_tokens': self.configuration.max_output_tokens,
            'temperature': 0,
        }
        response = self.transport(
            _endpoint(self.configuration.base_url, 'chat/completions'),
            _authorization(self.configuration.api_key),
            body,
            self.configuration.timeout_seconds,
        )
        choices = response.get('choices') or []
        text = choices[0].get('message', {}).get('content') if choices else None
        usage = response.get('usage') or {}
        return AIProviderResult(
            data=_load_structured_text(text),
            provider_request_id=response.get('id'),
            input_tokens=usage.get('prompt_tokens'),
            output_tokens=usage.get('completion_tokens'),
        )
