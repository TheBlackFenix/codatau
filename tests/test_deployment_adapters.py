from io import BytesIO
from pathlib import Path

import pandas as pd
import pytest
from werkzeug.datastructures import FileStorage

from app.services.database_config import database_uri
from app.services.dataset_pipeline import DatasetPipeline
from app.services.storage_service import RemoteStorageService


class ObjectMemory:
    def __init__(self):
        self.objects = {}

    def exists(self, key):
        return key in self.objects

    def upload(self, key, path, overwrite):
        if not overwrite and key in self.objects:
            raise FileExistsError(key)
        self.objects[key] = path.read_bytes()

    def download(self, key, destination):
        if key not in self.objects:
            return False
        destination.write_bytes(self.objects[key])
        return True

    def delete(self, key):
        self.objects.pop(key, None)


def test_remote_version_recovers_in_fresh_workspace(tmp_path):
    backend = ObjectMemory()
    originals = RemoteStorageService(tmp_path / 'first/uploads', backend, 'demo/uploads')
    source = originals.save(FileStorage(stream=BytesIO(b'amount\n10\n20\n')), 'source.csv')
    first = DatasetPipeline(tmp_path / 'first/artifacts', storage=RemoteStorageService(tmp_path / 'first/artifacts', backend, 'demo/artifacts'))
    first.ingest_dataframe(pd.DataFrame({'amount': [10, 20]}), 'source.csv', source)
    # New workspace models a restart / another instance with an empty local disk.
    fresh = DatasetPipeline(tmp_path / 'second', storage=RemoteStorageService(tmp_path / 'second', backend, 'demo/artifacts'))
    assert fresh.load_dataframe('source.csv')['amount'].sum() == 30
    assert fresh.load_profile('source.csv')['row_count'] == 2
    with pytest.raises(ValueError):
        originals.path_for('../outside')
    with pytest.raises(FileExistsError):
        originals.save(FileStorage(stream=BytesIO(b'changed')), 'source.csv')
    assert backend.objects['demo/uploads/source.csv'] == b'amount\n10\n20\n'
    fresh.remove_artifacts('source.csv')
    assert list(backend.objects) == ['demo/uploads/source.csv']


@pytest.mark.parametrize('prefix', ['postgres://', 'postgresql://', 'postgresql+psycopg://'])
def test_postgresql_url_normalization(prefix, tmp_path):
    uri = database_uri({'DATABASE_PROVIDER': 'postgresql', 'DATABASE_URL': prefix + 'user:p%40ss@host/db?sslmode=require'}, tmp_path)
    assert uri == 'postgresql+psycopg://user:p%40ss@host/db?sslmode=require'


def test_provider_mismatch_fails(tmp_path):
    with pytest.raises(ValueError, match='no coincide'):
        database_uri({'DATABASE_PROVIDER': 'sqlite', 'DATABASE_URL': 'postgresql://u:p@host/db'}, tmp_path)
    with pytest.raises(ValueError, match='requiere'):
        database_uri({'DATABASE_PROVIDER': 'postgresql'}, tmp_path)


def test_sqlite_default(tmp_path):
    assert database_uri({'DATABASE_PROVIDER': 'sqlite'}, tmp_path) == 'sqlite:///' + str(tmp_path / 'instance/pymes_ai.db')


def test_remote_workspace_removed_after_request(app, tmp_path, monkeypatch):
    from app.services import storage_service
    backend = ObjectMemory()
    monkeypatch.setattr(storage_service, 'S3Backend', lambda _: backend)
    app.config.update(STORAGE_PROVIDER='s3', S3_BUCKET='test')
    with app.app_context():
        storage = storage_service.storage_for('uploads')
        path = storage.save(FileStorage(stream=BytesIO(b'data')), 'file.csv')
        assert path.exists()
    assert not path.exists()
