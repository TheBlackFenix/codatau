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

    def publish(self, key):
        return self.path_for(key)


class RemoteStorageService(LocalStorageService):
    """Request-scoped staging; durable objects live exclusively in the provider."""

    def __init__(self, root, backend, prefix):
        super().__init__(root)
        self.backend = backend
        self.prefix = prefix.strip('/')

    def object_key(self, key):
        super().path_for(key)  # Validate before any remote operation.
        if not key or '\\' in key or Path(key).is_absolute():
            raise ValueError('Clave de almacenamiento inválida.')
        return f'{self.prefix}/{key}' if self.prefix else key

    def path_for(self, key):
        path = super().path_for(key)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
            try:
                if self.backend.download(self.object_key(key), temporary):
                    os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        return path

    def save(self, file_storage, key):
        if self.exists(key):
            raise FileExistsError(f'El objeto ya existe: {key}')
        path = super().save(file_storage, key)
        try:
            self.backend.upload(self.object_key(key), path, overwrite=False)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path

    def publish(self, key):
        path = super().path_for(key)
        self.backend.upload(self.object_key(key), path, overwrite=True)
        return path

    def exists(self, key):
        return self.backend.exists(self.object_key(key))

    def delete(self, key):
        self.backend.delete(self.object_key(key))
        super().path_for(key).unlink(missing_ok=True)


class S3Backend:
    def __init__(self, settings):
        import boto3
        from botocore.config import Config
        self.bucket = settings['S3_BUCKET']
        self.client = boto3.client(
            's3', endpoint_url=settings.get('S3_ENDPOINT_URL') or None,
            region_name=settings.get('S3_REGION') or 'us-east-1',
            aws_access_key_id=settings.get('S3_ACCESS_KEY_ID') or None,
            aws_secret_access_key=settings.get('S3_SECRET_ACCESS_KEY') or None,
            config=Config(s3={'addressing_style': settings.get('S3_ADDRESSING_STYLE', 'path')},
                          connect_timeout=10, read_timeout=60, retries={'max_attempts': 3}),
        )

    @staticmethod
    def missing(error):
        return error.response['Error']['Code'] in {'404', 'NoSuchKey', 'NotFound'}

    def exists(self, key):
        from botocore.exceptions import ClientError
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as error:
            if self.missing(error):
                return False
            raise

    def download(self, key, destination):
        from botocore.exceptions import ClientError
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if self.missing(error):
                return False
            raise
        stream = response['Body']
        try:
            with destination.open('wb') as output:
                for chunk in stream.iter_chunks(chunk_size=1024 * 1024):
                    output.write(chunk)
        finally:
            stream.close()
        return True

    def upload(self, key, source, overwrite):
        kwargs = {} if overwrite else {'IfNoneMatch': '*'}
        with source.open('rb') as data:
            self.client.put_object(Bucket=self.bucket, Key=key, Body=data, **kwargs)

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=key)


class AzureBlobBackend:
    def __init__(self, settings):
        from azure.storage.blob import BlobServiceClient
        if settings.get('AZURE_STORAGE_CONNECTION_STRING'):
            service = BlobServiceClient.from_connection_string(settings['AZURE_STORAGE_CONNECTION_STRING'])
        else:
            from azure.identity import DefaultAzureCredential
            service = BlobServiceClient(settings['AZURE_STORAGE_ACCOUNT_URL'], credential=DefaultAzureCredential())
        self.container = service.get_container_client(settings['AZURE_STORAGE_CONTAINER'])

    def exists(self, key):
        return self.container.get_blob_client(key).exists()

    def download(self, key, destination):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            stream = self.container.download_blob(key)
        except ResourceNotFoundError:
            return False
        with destination.open('wb') as output:
            stream.readinto(output)
        return True

    def upload(self, key, source, overwrite):
        with source.open('rb') as data:
            self.container.upload_blob(name=key, data=data, overwrite=overwrite)

    def delete(self, key):
        from azure.core.exceptions import ResourceNotFoundError
        try:
            self.container.delete_blob(key)
        except ResourceNotFoundError:
            pass


def init_storage(app):
    provider = app.config['STORAGE_PROVIDER']
    if provider not in {'local', 's3', 'azure'}:
        raise ValueError('STORAGE_PROVIDER debe ser local, s3 o azure.')
    required = {'s3': ['S3_BUCKET'], 'azure': ['AZURE_STORAGE_CONTAINER']}.get(provider, [])
    if provider == 'azure' and not app.config.get('AZURE_STORAGE_CONNECTION_STRING'):
        required.append('AZURE_STORAGE_ACCOUNT_URL')
    if any(not app.config.get(name) for name in required):
        raise ValueError('Falta configurar el almacenamiento: ' + ', '.join(required))

    @app.teardown_appcontext
    def cleanup_storage(_error):
        from flask import g
        workspace = g.pop('_storage_workspace', None)
        if workspace:
            workspace.cleanup()


def storage_for(kind):
    from flask import current_app, g
    from tempfile import TemporaryDirectory
    if kind not in {'uploads', 'artifacts'}:
        raise ValueError('Tipo de almacenamiento inválido.')
    settings = current_app.config
    if settings['STORAGE_PROVIDER'] == 'local':
        return LocalStorageService(settings['UPLOAD_FOLDER' if kind == 'uploads' else 'ANALYTICS_FOLDER'])
    if '_storage_workspace' not in g:
        g._storage_workspace = TemporaryDirectory(prefix='codatau-')
        g._storage_backend = S3Backend(settings) if settings['STORAGE_PROVIDER'] == 's3' else AzureBlobBackend(settings)
    prefix = '/'.join(part for part in (settings.get('STORAGE_PREFIX', '').strip('/'), kind) if part)
    return RemoteStorageService(Path(g._storage_workspace.name) / kind, g._storage_backend, prefix)
