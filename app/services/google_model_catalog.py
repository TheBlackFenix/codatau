"""Server-only discovery of text generation models; never sends uploaded data."""
import hashlib
import json
import re
import time
from threading import Lock
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

class GoogleModelCatalog:
    def __init__(self):
        self.entries = {}
        self.lock = Lock()

    @staticmethod
    def supported(config):
        return (config.get('AI_PROVIDER') == 'openai_compatible'
                and urlparse(config.get('AI_BASE_URL', '')).hostname == 'generativelanguage.googleapis.com'
                and bool(config.get('AI_API_KEY')))

    @staticmethod
    def discover(key):
        models, token, seen = {}, None, set()
        for _ in range(20):
            query = {'pageSize': 1000}
            if token:
                query['pageToken'] = token
            request = Request('https://generativelanguage.googleapis.com/v1beta/models?' + urlencode(query),
                              headers={'x-goog-api-key': key})
            with urlopen(request, timeout=8) as response:
                data = json.load(response)
            for item in data.get('models', []):
                name = item.get('name', '').removeprefix('models/')
                if ('generateContent' in item.get('supportedGenerationMethods', [])
                        and re.fullmatch(r'gemini-[a-zA-Z0-9._-]+', name)
                        and not any(word in name.lower() for word in ('image', 'audio', 'tts', 'live', 'robotics', 'computer-use', 'nano-banana', 'transcribe'))):
                    models[name] = {'id': name, 'label': item.get('displayName', name)[:120]}
            token = data.get('nextPageToken')
            if not token:
                return sorted(models.values(), key=lambda item: item['id'])
            if token in seen:
                raise ValueError('Repeated page')
            seen.add(token)
        raise ValueError('Too many catalog pages')

    def get(self, config, refresh=False):
        default = config.get('AI_MODEL', '')
        fallback = [{'id': default, 'label': default}] if default else []
        if not self.supported(config):
            return fallback, False
        fingerprint = hashlib.sha256(config['AI_API_KEY'].encode()).hexdigest()
        with self.lock:
            cached = self.entries.get(fingerprint)
            if cached and not refresh and time.monotonic() < cached['expires']:
                return cached['models'], cached['error']
            try:
                models = self.discover(config['AI_API_KEY'])
                if not models:
                    raise ValueError('Empty catalog')
                error = False
            except Exception:
                # Provider errors can include credentials: do not log raw errors.
                models = cached['models'] if cached else fallback
                error = True
            self.entries[fingerprint] = {'models': models, 'error': error,
                                         'expires': time.monotonic() + (60 if error else 900)}
            return models, error
