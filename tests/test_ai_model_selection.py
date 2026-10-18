from unittest.mock import patch

from app.services.ai_providers import AIProviderFactory
from app.services.google_model_catalog import GoogleModelCatalog


def test_catalog_filters_and_paginates_without_credentials_in_url():
    calls = []
    class Response:
        def __init__(self, body): self.body = body
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self):
            import json
            return json.dumps(self.body).encode()
    def fetch(request, timeout):
        calls.append(request)
        assert 'secret' not in request.full_url
        if len(calls) == 1:
            return Response({'models': [
                {'name': 'models/gemini-test', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/gemini-image', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']},
            ], 'nextPageToken': 'next'})
        return Response({'models': [{'name': 'models/gemini-other', 'supportedGenerationMethods': ['generateContent']}]})
    with patch('app.services.google_model_catalog.urlopen', side_effect=fetch):
        assert [item['id'] for item in GoogleModelCatalog.discover('secret')] == ['gemini-other', 'gemini-test']
    assert len(calls) == 2


def test_selection_is_user_scoped_and_does_not_change_global_config(app, client, auth):
    app.config.update(AI_MODEL='gemini-default')
    app.add_url_rule('/_test_model', view_func=lambda: AIProviderFactory.configuration_from_app(app.config).model)
    auth.register(); auth.login()
    options = ([{'id': 'gemini-default', 'label': 'Default'}, {'id': 'gemini-new', 'label': 'New'}], False)
    with patch.object(app.extensions['google_model_catalog'], 'get', return_value=options):
        assert client.post('/settings/ai-model', data={'model': 'gemini-new'}).status_code == 302
        with client.session_transaction() as state:
            assert state['ai_model_choice']['model'] == 'gemini-new'
        assert client.get('/_test_model').text == 'gemini-new'
        assert app.config['AI_MODEL'] == 'gemini-default'
        assert client.post('/settings/ai-model', data={'model': 'invented'}).status_code == 400
        auth.logout(); auth.register('second', 'second@example.com'); auth.login('second@example.com')
        assert client.get('/_test_model').text == 'gemini-default'


def test_failed_refresh_preserves_previous_catalog():
    catalog = GoogleModelCatalog()
    config = {'AI_PROVIDER': 'openai_compatible', 'AI_API_KEY': 'secret',
              'AI_BASE_URL': 'https://generativelanguage.googleapis.com/v1beta/openai/', 'AI_MODEL': 'gemini-default'}
    with patch.object(catalog, 'discover', return_value=[{'id': 'gemini-new', 'label': 'New'}]):
        assert catalog.get(config)[1] is False
    with patch.object(catalog, 'discover', side_effect=RuntimeError('secret')):
        models, failed = catalog.get(config, refresh=True)
    assert failed and models[0]['id'] == 'gemini-new'


def test_selection_requires_auth_and_prevents_external_redirect(app, client, auth):
    assert client.post('/settings/ai-model', data={'model': 'x'}).status_code == 302
    auth.register(); auth.login()
    with patch.object(app.extensions['google_model_catalog'], 'get', return_value=([{'id': 'x', 'label': 'X'}], False)):
        result = client.post('/settings/ai-model', data={'model': 'x', 'return_to': '//evil.test'})
        assert result.location == '/dashboard'


def test_selector_can_be_disabled_and_ignores_stored_choice(app, client, auth):
    app.add_url_rule('/_test_model', view_func=lambda: AIProviderFactory.configuration_from_app(app.config).model)
    auth.register(); auth.login()
    app.config.update(AI_MODEL='default', AI_MODEL_SELECTOR_ENABLED=False)
    with client.session_transaction() as state:
        state['ai_model_choice'] = {'user_id': 1, 'model': 'expensive'}
    assert client.post('/settings/ai-model', data={'model': 'expensive'}).status_code == 404
    assert client.get('/_test_model').text == 'default'
    assert 'Modelo de IA · Pruebas' not in client.get('/dashboard').text


def test_selection_is_csrf_protected(app, client, auth):
    auth.register(); auth.login()
    app.config['WTF_CSRF_ENABLED'] = True
    assert client.post('/settings/ai-model', data={'model': 'x'}).status_code == 400
