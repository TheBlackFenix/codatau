import pytest

from app import create_app
from app.extensions import db


@pytest.fixture()
def app(tmp_path):
    upload_folder = tmp_path / 'uploads'
    analytics_folder = tmp_path / 'artifacts'
    application = create_app(
        'testing',
        {
            'SECRET_KEY': 'test-secret-key',
            'UPLOAD_FOLDER': str(upload_folder),
            'ANALYTICS_FOLDER': str(analytics_folder),
        },
    )

    yield application

    with application.app_context():
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def auth(client):
    class AuthActions:
        def register(self, username='tester', email='tester@example.com', password='secret1'):
            return client.post(
                '/auth/register',
                data={
                    'username': username,
                    'email': email,
                    'password': password,
                    'password2': password,
                },
            )

        def login(self, email='tester@example.com', password='secret1', next_url=None):
            path = '/auth/login'
            if next_url:
                path = f'{path}?next={next_url}'
            return client.post(path, data={'email': email, 'password': password})

        def logout(self):
            return client.post('/auth/logout')

    return AuthActions()
