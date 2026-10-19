import hashlib
import importlib.util
import subprocess
from pathlib import Path
from zipfile import ZipFile

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'documentos' / 'scripts' / 'empaquetar.py'
SPEC = importlib.util.spec_from_file_location('delivery_package', SCRIPT)
delivery = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(delivery)


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def repository(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    git(root, 'init')
    git(root, 'config', 'user.name', 'Delivery Test')
    git(root, 'config', 'user.email', 'delivery@example.invalid')
    (root / 'run.py').write_text('print("demo")\n', encoding='utf-8')
    (root / '.env.example').write_text('AI_PROVIDER=disabled\n', encoding='utf-8')
    git(root, 'add', 'run.py', '.env.example')
    git(root, 'commit', '-m', 'Synthetic source snapshot')
    return root


def test_package_contains_committed_source_and_provenance_not_local_data(repository, tmp_path):
    (repository / '.env').write_text('PRIVATE_TEST_VALUE=not-a-real-secret', encoding='utf-8')
    (repository / 'uploads').mkdir()
    (repository / 'uploads' / 'personal.csv').write_text('not included', encoding='utf-8')
    destination = tmp_path / 'delivery.zip'
    commit, digest = delivery.package(repository, destination)
    assert commit == git(repository, 'rev-parse', 'HEAD')
    assert digest == hashlib.sha256(destination.read_bytes()).hexdigest()
    assert destination.with_name('delivery.zip.sha256').read_text().startswith(digest)
    with ZipFile(destination) as archive:
        files = {item.filename for item in archive.infolist() if not item.is_dir()}
        assert files == {'codatau/run.py', 'codatau/.env.example', 'codatau/ENTREGA.txt'}
        assert commit in archive.read('codatau/ENTREGA.txt').decode()
        assert archive.testzip() is None


@pytest.mark.parametrize('private_path', ['.env', 'instance/private.db', 'uploads/personal.csv'])
def test_package_rejects_accidentally_versioned_private_files(repository, tmp_path, private_path):
    source = repository / private_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('synthetic test data only', encoding='utf-8')
    git(repository, 'add', private_path)
    git(repository, 'commit', '-m', 'Synthetic forbidden fixture')
    destination = tmp_path / 'blocked.zip'
    with pytest.raises(ValueError):
        delivery.package(repository, destination)
    assert not destination.exists()


def test_package_requires_committed_changes(repository, tmp_path):
    (repository / 'run.py').write_text('print("changed")', encoding='utf-8')
    with pytest.raises(ValueError, match='sin commit'):
        delivery.package(repository, tmp_path / 'dirty.zip')


def test_package_does_not_overwrite_an_existing_delivery(repository, tmp_path):
    destination = tmp_path / 'existing.zip'
    destination.write_bytes(b'existing delivery')
    with pytest.raises(FileExistsError):
        delivery.package(repository, destination)
    assert destination.read_bytes() == b'existing delivery'


def test_package_rejects_unknown_reference(repository, tmp_path):
    destination = tmp_path / 'unknown.zip'
    with pytest.raises(RuntimeError):
        delivery.package(repository, destination, 'missing-reference')
    assert not destination.exists()
