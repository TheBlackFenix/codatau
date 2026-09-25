"""Adopt or create the original CoDataU schema.

Revision ID: 0001_legacy_schema_baseline
Revises:
Create Date: 2026-09-25

"""
from alembic import op
import sqlalchemy as sa


revision = '0001_legacy_schema_baseline'
down_revision = None
branch_labels = None
depends_on = None


def _has_table(name):
    return name in sa.inspect(op.get_bind()).get_table_names()


def upgrade():
    # The first public versions created these tables with db.create_all().
    # Conditional creation lets an existing installation adopt migrations
    # without deleting data, while still bootstrapping an empty database.
    if not _has_table('users'):
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('username', sa.String(length=80), nullable=False),
            sa.Column('email', sa.String(length=120), nullable=False),
            sa.Column('password_hash', sa.String(length=256), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('is_active', sa.Boolean(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('email'),
            sa.UniqueConstraint('username'),
        )

    if not _has_table('file_uploads'):
        op.create_table(
            'file_uploads',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(length=255), nullable=False),
            sa.Column('original_name', sa.String(length=255), nullable=False),
            sa.Column('file_type', sa.String(length=10), nullable=False),
            sa.Column('file_size', sa.Integer(), nullable=True),
            sa.Column('row_count', sa.Integer(), nullable=True),
            sa.Column('column_count', sa.Integer(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=True),
            sa.Column('uploaded_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('processed_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )

    if not _has_table('ai_insights'):
        op.create_table(
            'ai_insights',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('file_id', sa.Integer(), nullable=False),
            sa.Column('insight_type', sa.String(length=50), nullable=False),
            sa.Column('message', sa.Text(), nullable=False),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
            sa.ForeignKeyConstraint(['file_id'], ['file_uploads.id']),
            sa.ForeignKeyConstraint(['user_id'], ['users.id']),
            sa.PrimaryKeyConstraint('id'),
        )


def downgrade():
    # This revision adopts legacy tables that may predate Alembic. Removing them
    # would destroy user accounts and uploaded-file metadata, so it is a safe,
    # intentionally irreversible baseline.
    pass
