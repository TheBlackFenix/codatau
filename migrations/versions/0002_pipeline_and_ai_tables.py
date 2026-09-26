"""Add cleaning, dashboard, versioning, and AI audit tables.

Revision ID: 0002_pipeline_and_ai_tables
Revises: 0001_legacy_schema_baseline
Create Date: 2026-09-25

"""
from alembic import op
import sqlalchemy as sa


revision = '0002_pipeline_and_ai_tables'
down_revision = '0001_legacy_schema_baseline'
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def _has_index(table_name, index_name):
    if not _has_table(table_name):
        return False
    return index_name in {
        index['name']
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
    }


def upgrade():
    if not _has_table('dataset_versions'):
        op.create_table(
            'dataset_versions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=False),
            sa.Column('created_by', sa.Integer(), nullable=False),
            sa.Column('version_number', sa.Integer(), nullable=False),
            sa.Column('stored_filename', sa.String(length=255), nullable=False),
            sa.Column('quarantine_filename', sa.String(length=255), nullable=True),
            sa.Column('operations', sa.JSON(), nullable=False),
            sa.Column('metrics', sa.JSON(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['created_by'], ['users.id']),
            sa.ForeignKeyConstraint(['file_id'], ['file_uploads.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('file_id', 'version_number', name='uq_dataset_version'),
            sa.UniqueConstraint('stored_filename'),
        )
    if not _has_index('dataset_versions', 'ix_dataset_versions_file_id'):
        op.create_index(
            'ix_dataset_versions_file_id',
            'dataset_versions',
            ['file_id'],
            unique=False,
        )

    if not _has_table('cleaning_decisions'):
        op.create_table(
            'cleaning_decisions',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=False),
            sa.Column('decided_by', sa.Integer(), nullable=False),
            sa.Column('operation_id', sa.String(length=500), nullable=False),
            sa.Column('operation', sa.String(length=80), nullable=False),
            sa.Column('column_name', sa.String(length=500), nullable=True),
            sa.Column('choice', sa.String(length=20), nullable=False),
            sa.Column('parameters', sa.JSON(), nullable=False),
            sa.Column('affected_rows', sa.Integer(), nullable=False),
            sa.Column('reason', sa.Text(), nullable=True),
            sa.Column('applied_version_number', sa.Integer(), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['decided_by'], ['users.id']),
            sa.ForeignKeyConstraint(['file_id'], ['file_uploads.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'file_id',
                'operation_id',
                name='uq_cleaning_decision_operation',
            ),
        )
    if not _has_index('cleaning_decisions', 'ix_cleaning_decisions_file_id'):
        op.create_index(
            'ix_cleaning_decisions_file_id',
            'cleaning_decisions',
            ['file_id'],
            unique=False,
        )

    if not _has_table('dashboard_configurations'):
        op.create_table(
            'dashboard_configurations',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=False),
            sa.Column('metrics', sa.JSON(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['file_id'], ['file_uploads.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'user_id',
                'file_id',
                name='uq_dashboard_configuration_user_file',
            ),
        )

    if not _has_table('ai_analysis_runs'):
        op.create_table(
            'ai_analysis_runs',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=False),
            sa.Column('purpose', sa.String(length=50), nullable=False),
            sa.Column('provider', sa.String(length=50), nullable=False),
            sa.Column('model', sa.String(length=120), nullable=False),
            sa.Column('status', sa.String(length=20), nullable=False),
            sa.Column('request_fingerprint', sa.String(length=64), nullable=False),
            sa.Column('candidate_count', sa.Integer(), nullable=False),
            sa.Column('input_tokens', sa.Integer(), nullable=True),
            sa.Column('output_tokens', sa.Integer(), nullable=True),
            sa.Column('provider_request_id', sa.String(length=160), nullable=True),
            sa.Column('result', sa.JSON(), nullable=True),
            sa.Column('error_code', sa.String(length=80), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['file_id'], ['file_uploads.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )
    if not _has_index('ai_analysis_runs', 'ix_ai_analysis_runs_request_fingerprint'):
        op.create_index(
            'ix_ai_analysis_runs_request_fingerprint',
            'ai_analysis_runs',
            ['request_fingerprint'],
            unique=False,
        )


def downgrade():
    # Existing installations may have created these tables before Alembic was
    # introduced. The adoption migration must never guess that they are safe to
    # delete. Future revisions can provide normal reversible downgrades.
    pass
