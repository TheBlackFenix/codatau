"""Real SDK calls to isolated Docker emulators, opt-in only."""
import os
import time
import uuid
from io import BytesIO

import pytest

from app.services.storage_service import S3Backend, AzureBlobBackend
from app.extensions import db
from app.models.file_upload import FileUpload

pytestmark = pytest.mark.skipif(os.environ.get('RUN_STORAGE_INTEGRATION') != '1', reason='Docker storage integration opt-in')


@pytest.mark.parametrize('provider', ['s3', 'azure'])
def test_upload_recover_download_and_delete_remote(app, client, auth, provider):
    settings = {name: os.environ.get(name, '') for name in (
        'S3_ENDPOINT_URL', 'S3_BUCKET', 'S3_ACCESS_KEY_ID', 'S3_SECRET_ACCESS_KEY',
        'AZURE_STORAGE_CONNECTION_STRING', 'AZURE_STORAGE_CONTAINER')}
    settings.update(STORAGE_PROVIDER=provider, STORAGE_PREFIX='tests-' + uuid.uuid4().hex)
    app.config.update(settings)
    backend = S3Backend(app.config) if provider == 's3' else AzureBlobBackend(app.config)
    for attempt in range(30):
        try:
            if provider == 's3':
                backend.client.create_bucket(Bucket=settings['S3_BUCKET'])
            else:
                backend.container.create_container()
            break
        except Exception as error:
            if 'ContainerAlreadyExists' in str(error):
                break
            if attempt == 29:
                raise
            time.sleep(1)
    auth.register()
    auth.login()
    response = client.post('/files/upload', data={'file': (BytesIO(b'category,amount\n A ,10\nB,20\n'), 'demo.csv')}, content_type='multipart/form-data')
    assert response.status_code == 302
    with app.app_context():
        record = FileUpload.query.one()
        file_id, name = record.id, record.filename
        from app.routes.files import _pipeline
        pipeline = _pipeline()
        # Persist a new version and its quarantine using the same publication path
        # as both cleaning flows. Subsequent requests must fetch from the provider.
        import duckdb
        path, _ = pipeline.paths_for('new.csv')
        connection = duckdb.connect()
        try:
            connection.sql("SELECT 20 AS amount").write_parquet(str(path))
            connection.sql("SELECT -1 AS amount").write_parquet(str(pipeline.quarantine_path_for('new.csv')))
        finally:
            connection.close()
        from app.services.storage_service import storage_for
        pipeline.profile_existing('new.csv', storage_for('uploads').path_for(name))
    with app.app_context():
        pipeline = _pipeline()
        assert pipeline.load_dataframe('new.csv')['amount'].tolist() == [20]
        assert pipeline.load_quarantine_dataframe('new.csv')['amount'].tolist() == [-1]
        pipeline.remove_artifacts('new.csv')
        assert pipeline.load_dataframe(name)['amount'].sum() == 30
    assert client.get(f'/files/results/{file_id}').status_code == 200
    assert client.get('/dashboard').status_code == 200
    assert client.get(f'/reports/download/{file_id}').status_code == 200
    applied = client.post(f'/files/cleaning/{file_id}/apply', data={'operation_ids': ['category:trim_text']})
    assert applied.status_code == 302
    with app.app_context():
        record = db.session.get(FileUpload, file_id)
        assert record.active_version is not None
        version_id = record.active_version.id
        assert _pipeline().load_dataframe(record.active_stored_filename)['category'].tolist() == ['A', 'B']
    assert client.get(f'/files/cleaning/{file_id}/export/{version_id}').status_code == 200
    assert client.post(f'/files/delete/{file_id}').status_code == 302
    assert not backend.exists(settings['STORAGE_PREFIX'] + '/uploads/' + name)
