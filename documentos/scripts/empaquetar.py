"""Export a committed source snapshot, never the local environment or user data."""
import argparse
import hashlib
import io
import subprocess
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from zipfile import ZIP_DEFLATED, ZipFile


EXCLUDED_DIRECTORIES = {
    '.git', 'venv', '.venv', 'instance', 'uploads', 'artifacts', 'output', 'tmp',
    '.tmp', '__pycache__', '.pytest_cache', '.pytest-tmp', '.ruff_cache',
    '.codex', '.agents', '.aws', '.codex-finalizer', 'node_modules',
}
SENSITIVE_EXTENSIONS = {'.db', '.sqlite', '.sqlite3', '.parquet', '.pem', '.key', '.pfx', '.p12'}


def git(root, *arguments):
    result = subprocess.run(['git', '-C', str(root), *arguments], capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('No se pudo completar la operacion Git. Revisa la referencia y el repositorio.')
    return result.stdout


def validate_member(name):
    path = PurePosixPath(name)
    if path.is_absolute() or '..' in path.parts or '\\' in name:
        raise ValueError('El paquete contiene una ruta no permitida.')
    if any(part.casefold() in EXCLUDED_DIRECTORIES for part in path.parts):
        raise ValueError('Hay archivos de trabajo o de usuarios versionados; revisalos antes de entregar.')
    name_lower = path.name.casefold()
    if (name_lower == '.env' or (name_lower.startswith('.env.') and name_lower != '.env.example')
            or path.suffix.lower() in SENSITIVE_EXTENSIONS):
        raise ValueError('Hay un archivo de configuracion privada o datos versionado; no se genero la entrega.')


def package(root, destination, reference='HEAD'):
    root = Path(root).resolve()
    destination = Path(destination).resolve()
    if git(root, 'status', '--porcelain', '--untracked-files=no').strip():
        raise ValueError('Hay cambios sin commit. Guardalos antes de generar una entrega reproducible.')
    commit = git(root, 'rev-parse', '--verify', reference + '^{commit}').decode().strip()
    source = git(root, 'archive', '--format=zip', '--prefix=codatau/', commit)
    checksum_path = destination.with_name(destination.name + '.sha256')
    if destination.exists() or checksum_path.exists():
        raise FileExistsError('El ZIP o su comprobante ya existen. Elige otro nombre; no se sobrescriben entregas.')
    with ZipFile(io.BytesIO(source)) as snapshot:
        for item in snapshot.infolist():
            validate_member(item.filename)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(destination, mode='x', compression=ZIP_DEFLATED) as archive:
            for item in snapshot.infolist():
                archive.writestr(item, snapshot.read(item.filename))
            archive.writestr('codatau/ENTREGA.txt', (
                'Entrega de codigo fuente de CoDataU\n'
                'Repositorio: https://github.com/TheBlackFenix/codatau\n'
                f'Commit: {commit}\n'
                f'Generado (UTC): {datetime.now(timezone.utc).isoformat()}\n'
                'Incluye los archivos versionados de esa revision.\n'
                'No incluye .git, historial, credenciales, entorno virtual ni datos de usuarios.\n'
                'Instalacion: documentos/2.7_codigo_fuente_e_instalacion.md\n'
                'Este paquete de fuentes no es un respaldo de una instalacion con datos.\n'
            ).encode('utf-8'))
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    with checksum_path.open('x', encoding='utf-8') as handle:
        handle.write(f'{digest}  {destination.name}\n')
    return commit, digest


def main():
    parser = argparse.ArgumentParser(description='Genera el ZIP del codigo guardado en Git, sin datos locales.')
    parser.add_argument('--referencia', default='HEAD', help='Commit o rama que se entregara; por defecto HEAD.')
    parser.add_argument('--destino', help='Ruta de un ZIP nuevo; no sobrescribe archivos existentes.')
    arguments = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')
    destination = Path(arguments.destino) if arguments.destino else root / 'output' / 'entregas' / f'codatau-fuentes-{stamp}.zip'
    try:
        commit, digest = package(root, destination, arguments.referencia)
    except (RuntimeError, ValueError, FileExistsError, OSError) as error:
        parser.exit(1, f'No se genero la entrega: {error}\n')
    print(f'ZIP: {destination.resolve()}\nCommit: {commit}\nSHA-256: {digest}')


if __name__ == '__main__':
    main()
