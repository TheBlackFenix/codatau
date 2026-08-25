from io import BytesIO
from pathlib import Path

import pandas as pd

from app.models.file_upload import FileUpload


def _login(auth):
    auth.register()
    auth.login()


def _csv_bytes():
    return b'category,amount\nA,10\nB,20\nA,30\n'


def test_csv_flow_from_upload_to_download(app, client, auth):
    _login(auth)

    upload = client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    assert upload.status_code == 302
    assert upload.headers['Location'].endswith('/files/results/1')
    assert client.get('/files/results/1').status_code == 200
    assert client.get('/dashboard').status_code == 200
    assert client.get('/files/insights').status_code == 200
    assert client.get('/reports/').status_code == 200

    download = client.get('/reports/download/1')
    assert download.status_code == 200
    assert download.mimetype == 'text/csv'
    assert b'category,amount' in download.data

    profile = client.get('/files/profile/1')
    assert profile.status_code == 200
    assert profile.json['row_count'] == 3
    assert profile.json['column_count'] == 2
    assert len(profile.json['source_sha256']) == 64

    with app.app_context():
        record = FileUpload.query.one()
        assert record.row_count == 3
        assert record.column_count == 2


def test_xlsx_upload(app, client, auth):
    _login(auth)
    workbook = BytesIO()
    pd.DataFrame({'category': ['A', 'B'], 'amount': [1, 2]}).to_excel(
        workbook,
        index=False,
    )
    workbook.seek(0)

    response = client.post(
        '/files/upload',
        data={'file': (workbook, 'sales.xlsx')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    with app.app_context():
        assert FileUpload.query.one().file_type == 'xlsx'


def test_download_uses_parquet_when_original_is_unavailable(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    with app.app_context():
        record = FileUpload.query.one()
        Path(app.config['UPLOAD_FOLDER'], record.filename).unlink()

    response = client.get('/reports/download/1')

    assert response.status_code == 200
    assert b'category,amount' in response.data


def test_empty_csv_is_rejected(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'category,amount\n'), 'empty.csv')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'El archivo está vacío'.encode() in response.data
    with app.app_context():
        assert FileUpload.query.count() == 0


def test_upload_respects_configured_size_limit(app, client, auth):
    _login(auth)
    app.config['MAX_CONTENT_LENGTH'] = 128

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'x' * 1024), 'large.csv')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    assert response.headers['Location'].endswith('/files/upload')


def test_user_cannot_access_another_users_file(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )
    auth.logout()
    auth.register(username='other', email='other@example.com')
    auth.login(email='other@example.com')

    assert client.get('/files/results/1').status_code == 404
    assert client.get('/reports/download/1').status_code == 404
    assert client.get('/files/profile/1').status_code == 404


def test_delete_removes_database_record_and_file(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    with app.app_context():
        path = app.config['UPLOAD_FOLDER'] + '/' + FileUpload.query.one().filename
        analytics_folder = app.config['ANALYTICS_FOLDER']

    response = client.post('/files/delete/1')

    assert response.status_code == 302
    with app.app_context():
        assert FileUpload.query.count() == 0
    assert not Path(path).exists()
    assert list(Path(analytics_folder).iterdir()) == []
