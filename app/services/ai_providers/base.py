from dataclasses import dataclass
from typing import Protocol


class AIProviderError(RuntimeError):
    def __init__(self, code, user_message, detail=None):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.detail = detail


@dataclass(frozen=True)
class ProviderConfiguration:
    provider: str
    model: str
    api_key: str
    base_url: str
    response_mode: str = 'json_schema'
    timeout_seconds: int = 30
    max_output_tokens: int = 1200


@dataclass(frozen=True)
class AIProviderResult:
    data: dict
    provider_request_id: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class StructuredAIProvider(Protocol):
    configuration: ProviderConfiguration

    def generate_json(self, instructions, payload, schema): ...
