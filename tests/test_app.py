import pytest

from app import create_app


def test_home_and_protected_route(client):
    assert client.get('/').status_code == 200

    response = client.get('/dashboard')
    assert response.status_code == 302
    assert '/auth/login' in response.headers['Location']


def test_factory_applies_overrides_and_creates_upload_folder(tmp_path):
    upload_folder = tmp_path / 'custom-uploads'
    app = create_app(
        'testing',
        {'SECRET_KEY': 'override', 'UPLOAD_FOLDER': str(upload_folder)},
    )

    assert app.config['SECRET_KEY'] == 'override'
    assert upload_folder.is_dir()


def test_factory_rejects_unknown_configuration():
    with pytest.raises(ValueError, match='Configuración desconocida'):
        create_app('does-not-exist')


def test_production_requires_secret_and_database():
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app(
            'production',
            {'SECRET_KEY': None, 'SQLALCHEMY_DATABASE_URI': None},
        )
