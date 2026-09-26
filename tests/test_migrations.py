import sqlalchemy as sa

from app import create_app
from app.extensions import db
from app.models.ai_insight import AIInsight
from app.models.file_upload import FileUpload
from app.models.user import User


EXPECTED_TABLES = {
    'ai_analysis_runs',
    'ai_insights',
    'alembic_version',
    'cleaning_decisions',
    'dashboard_configurations',
    'dataset_versions',
    'file_uploads',
    'users',
}


def _migration_app(tmp_path, name):
    return create_app(
        'testing',
        {
            'AUTO_CREATE_DATABASE': False,
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{tmp_path / name}',
            'UPLOAD_FOLDER': str(tmp_path / f'{name}-uploads'),
            'ANALYTICS_FOLDER': str(tmp_path / f'{name}-artifacts'),
        },
    )


def test_migrations_create_fresh_schema_and_match_models(tmp_path):
    app = _migration_app(tmp_path, 'fresh.db')

    result = app.test_cli_runner().invoke(args=['db', 'upgrade'])

    assert result.exit_code == 0, result.output
    with app.app_context():
        assert set(sa.inspect(db.engine).get_table_names()) == EXPECTED_TABLES
    check = app.test_cli_runner().invoke(args=['db', 'check'])
    assert check.exit_code == 0, check.output


def test_migrations_adopt_legacy_schema_without_losing_data(tmp_path):
    app = _migration_app(tmp_path, 'legacy.db')
    with app.app_context():
        db.metadata.create_all(
            db.engine,
            tables=[User.__table__, FileUpload.__table__, AIInsight.__table__],
        )
        with db.engine.begin() as connection:
            connection.execute(
                User.__table__.insert().values(
                    id=1,
                    username='legacy',
                    email='legacy@example.com',
                    password_hash='existing-hash',
                    is_active=True,
                )
            )

    result = app.test_cli_runner().invoke(args=['db', 'upgrade'])

    assert result.exit_code == 0, result.output
    with app.app_context():
        inspector = sa.inspect(db.engine)
        assert set(inspector.get_table_names()) == EXPECTED_TABLES
        assert db.session.get(User, 1).email == 'legacy@example.com'
        revision = db.session.execute(
            sa.text('SELECT version_num FROM alembic_version')
        ).scalar_one()
        assert revision == '0002_pipeline_and_ai_tables'
