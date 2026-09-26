from app.models.user import User


def test_register_normalizes_email(app, auth):
    response = auth.register(username='  Usuario  ', email='TESTER@EXAMPLE.COM')

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/auth/login')
    with app.app_context():
        user = User.query.one()
        assert user.username == 'Usuario'
        assert user.email == 'tester@example.com'
        assert user.check_password('secret1')


def test_login_accepts_internal_next_url(auth):
    auth.register()

    response = auth.login(next_url='/dashboard')

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/dashboard')


def test_login_rejects_external_next_url(auth):
    auth.register()

    response = auth.login(next_url='https://example.com/phish')

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/auth/bienvenida')


def test_logout_only_accepts_post(client, auth):
    auth.register()
    auth.login()

    assert client.get('/auth/logout').status_code == 405
    assert auth.logout().status_code == 302
    assert client.get('/dashboard').status_code == 302


def test_duplicate_email_is_rejected(client, auth):
    auth.register()

    response = client.post(
        '/auth/register',
        data={
            'username': 'another',
            'email': 'TESTER@example.com',
            'password': 'secret1',
            'password2': 'secret1',
        },
    )

    assert response.status_code == 200
    assert 'Ese correo ya está registrado'.encode() in response.data
