import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.services.ai_providers.base import AIProviderError


def post_json(url, headers, payload, timeout):
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', **headers},
        method='POST',
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except HTTPError as error:
        detail = error.read().decode('utf-8', errors='replace')[:1000]
        raise AIProviderError(
            'provider_http_error',
            'El proveedor de IA rechazó la solicitud. Revisa el modelo y la credencial.',
            detail,
        ) from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, 'reason', error)
        raise AIProviderError(
            'provider_unavailable',
            'No fue posible conectar con el proveedor de IA.',
            str(reason),
        ) from error
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise AIProviderError(
            'invalid_provider_response',
            'El proveedor de IA devolvió una respuesta que no se pudo interpretar.',
            str(error),
        ) from error
