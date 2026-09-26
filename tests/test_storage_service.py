from io import BytesIO

import pytest
from werkzeug.datastructures import FileStorage

from app.services.storage_service import LocalStorageService


def test_local_storage_saves_and_deletes_objects(tmp_path):
    storage = LocalStorageService(tmp_path / 'uploads')
    uploaded = FileStorage(stream=BytesIO(b'content'), filename='source.csv')

    path = storage.save(uploaded, 'unique.csv')

    assert path.read_bytes() == b'content'
    assert storage.exists('unique.csv')
    storage.delete('unique.csv')
    assert not storage.exists('unique.csv')


def test_local_storage_rejects_path_traversal(tmp_path):
    storage = LocalStorageService(tmp_path / 'uploads')

    with pytest.raises(ValueError, match='carpeta permitida'):
        storage.path_for('../outside.csv')
