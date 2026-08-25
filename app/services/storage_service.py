import os
import uuid
from pathlib import Path


class LocalStorageService:
    """Filesystem-backed storage with an object-storage-compatible key API."""

    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, key):
        path = (self.root / key).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError('La clave de almacenamiento sale de la carpeta permitida.')
        return path

    def save(self, file_storage, key):
        destination = self.path_for(key)
        if destination.exists():
            raise FileExistsError(f'El objeto ya existe: {key}')

        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
        try:
            file_storage.save(temporary)
            os.replace(temporary, destination)
        finally:
            if temporary.exists():
                temporary.unlink()
        return destination

    def delete(self, key):
        path = self.path_for(key)
        if path.exists():
            path.unlink()

    def exists(self, key):
        return self.path_for(key).exists()
