"""Resolve database providers using SQLAlchemy's existing dialect adapters."""
from sqlalchemy.engine import make_url


def database_uri(settings, base_dir):
    provider = settings.get('DATABASE_PROVIDER', 'auto').lower()
    if provider not in {'auto', 'sqlite', 'postgresql'}:
        raise ValueError('DATABASE_PROVIDER debe ser auto, sqlite o postgresql.')
    value = settings.get('DATABASE_URL')
    if not value:
        if provider == 'postgresql':
            raise ValueError('PostgreSQL requiere DATABASE_URL.')
        return 'sqlite:///' + str(base_dir / 'instance' / 'pymes_ai.db')
    if value.startswith('postgres://'):
        value = 'postgresql://' + value[len('postgres://'):]
    url = make_url(value)
    dialect = url.get_backend_name()
    if dialect not in {'sqlite', 'postgresql'}:
        raise ValueError('Solo se admite SQLite o PostgreSQL.')
    if provider != 'auto' and provider != dialect:
        raise ValueError('DATABASE_PROVIDER no coincide con DATABASE_URL.')
    if dialect == 'postgresql':
        url = url.set(drivername='postgresql+psycopg')
    return url.render_as_string(hide_password=False)
