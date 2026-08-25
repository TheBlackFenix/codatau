from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from app.models.file_upload import FileUpload
from app.models.dataset_version import DatasetVersion


def _login(auth):
    auth.register()
    auth.login()


def _csv_bytes():
    return b'category,amount\nA,10\nB,20\nA,30\n'


def _cleaning_csv_bytes():
    return (
        b'email,amount\n'
        b'ana@example.com,10\n'
        b'invalid-email,20\n'
        b'leo@example.org,30\n'
        b'leo@example.org,30\n'
    )


def test_csv_flow_from_upload_to_download(app, client, auth):
    _login(auth)

    upload = client.post(
        '/files/upload',
        data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
        content_type='multipart/form-data',
    )

    assert upload.status_code == 302
    assert upload.headers['Location'].endswith('/files/results/1')
    with client.session_transaction() as browser_session:
        assert browser_session['active_file_id'] == 1
    assert client.get('/files/results/1').status_code == 200
    assert client.get('/dashboard').status_code == 200
    assert client.get('/files/insights').status_code == 200
    assert client.get('/reports/').status_code == 200
    assert client.get('/files/cleaning').headers['Location'].endswith('/files/cleaning/1')

    download = client.get('/reports/download/1')
    assert download.status_code == 200
    assert download.mimetype == 'text/csv'
    assert b'category,amount' in download.data

    profile = client.get('/files/profile/1')
    assert profile.status_code == 200
    assert profile.json['row_count'] == 3
    assert profile.json['column_count'] == 2
    assert len(profile.json['source_sha256']) == 64
    assert profile.json['profile_version'] == '1.2'
    assert profile.json['cleaning_plan']['status'] == 'proposed'

    with app.app_context():
        record = FileUpload.query.one()
        assert record.row_count == 3
        assert record.column_count == 2


def test_cleaning_navigation_without_files_explains_next_step(client, auth):
    _login(auth)

    response = client.get('/files/cleaning', follow_redirects=True)

    assert response.status_code == 200
    assert 'Carga un archivo antes de iniciar una limpieza'.encode() in response.data


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


def test_semicolon_csv_upload_is_detected(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'category;amount\nA;10\nB;20\n'), 'sales.csv')},
        content_type='multipart/form-data',
    )

    assert response.status_code == 302
    with app.app_context():
        record = FileUpload.query.one()
        assert record.row_count == 2
        assert record.column_count == 2


def test_invalid_excel_explains_how_to_fix_it(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'not an excel workbook'), 'broken.xlsx')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'no corresponde a un archivo Excel moderno'.encode() in response.data
    assert 'Guardar como'.encode() in response.data


def test_malformed_csv_explains_the_structure_problem(app, client, auth):
    _login(auth)

    response = client.post(
        '/files/upload',
        data={'file': (BytesIO(b'name,amount\n"unterminated,10\n'), 'broken.csv')},
        content_type='multipart/form-data',
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert 'comillas sin cerrar'.encode() in response.data
    assert 'CSV UTF-8'.encode() in response.data


def test_unexpected_processing_error_reports_stage_and_reference(app, client, auth):
    _login(auth)

    with patch(
        'app.services.dataset_pipeline.DatasetPipeline.ingest_dataframe',
        side_effect=RuntimeError('internal detail'),
    ):
        response = client.post(
            '/files/upload',
            data={'file': (BytesIO(_csv_bytes()), 'sales.csv')},
            content_type='multipart/form-data',
            follow_redirects=True,
        )

    assert response.status_code == 200
    assert 'creación del perfil analítico'.encode() in response.data
    assert 'Referencia:'.encode() in response.data
    assert b'internal detail' not in response.data


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
    assert client.get('/files/cleaning/1').status_code == 404


def test_cleaning_preview_apply_and_revert_version(app, client, auth):
    _login(auth)
    client.post(
        '/files/upload',
        data={'file': (BytesIO(_cleaning_csv_bytes()), 'contacts.csv')},
        content_type='multipart/form-data',
    )

    plan_page = client.get('/files/cleaning/1')
    assert plan_page.status_code == 200
    assert b'email:validate_email' in plan_page.data
    assert b'dataset:remove_exact_duplicates' in plan_page.data

    dashboard = client.get('/dashboard')
    insights = client.get('/files/insights')
    files_page = client.get('/files/upload')
    assert b'/files/cleaning/1' in dashboard.data
    assert b'Revisar y limpiar datos' in insights.data
    assert b'title="Limpiar datos"' in files_page.data

    selected = [
        'email:validate_email',
        'dataset:remove_exact_duplicates',
    ]
    preview = client.post(
        '/files/cleaning/1/preview',
        data={'operation_ids': selected},
    )
    assert preview.status_code == 200
    assert b'Vista previa de limpieza' in preview.data
    assert b'invalid-email' in preview.data

    applied = client.post(
        '/files/cleaning/1/apply',
        data={'operation_ids': selected},
        follow_redirects=True,
    )
    assert applied.status_code == 200
    assert 'Versi\u00f3n 1 creada'.encode() in applied.data

    with app.app_context():
        record = FileUpload.query.one()
        version = DatasetVersion.query.one()
        assert record.active_version.id == version.id
        assert record.row_count == 2
        assert version.metrics['quarantined_rows'] == 1
        assert version.metrics['duplicates_removed'] == 1
        assert Path(
            app.config['ANALYTICS_FOLDER'],
            version.quarantine_filename,
        ).exists()
        version_id = version.id

    quarantine_download = client.get(f'/files/cleaning/1/quarantine/{version_id}')
    assert quarantine_download.status_code == 200
    assert b'invalid-email' in quarantine_download.data
    assert b'__quarantine_reason' in quarantine_download.data

    cleaned_download = client.get('/reports/download/1')
    assert cleaned_download.data.count(b'leo@example.org') == 1
    assert b'invalid-email' not in cleaned_download.data

    reverted = client.post(
        '/files/cleaning/1/activate/0',
        follow_redirects=True,
    )
    assert reverted.status_code == 200
    assert 'versi\u00f3n original procesada'.encode() in reverted.data
    baseline_download = client.get('/reports/download/1')
    assert baseline_download.data.count(b'leo@example.org') == 2
    assert b'invalid-email' in baseline_download.data

    client.post(f'/files/cleaning/1/activate/{version_id}')
    with app.app_context():
        assert FileUpload.query.one().active_version.version_number == 1
        analytics_folder = Path(app.config['ANALYTICS_FOLDER'])

    client.post('/files/delete/1')
    with app.app_context():
        assert DatasetVersion.query.count() == 0
    assert list(analytics_folder.iterdir()) == []


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
