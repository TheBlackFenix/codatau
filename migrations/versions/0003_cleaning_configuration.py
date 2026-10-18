"""Persistent column type preferences, without transforming source data."""
from alembic import op
import sqlalchemy as sa

revision = '0003_cleaning_configuration'
down_revision = '0002_pipeline_and_ai_tables'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('cleaning_configurations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('file_id', sa.Integer(), sa.ForeignKey('file_uploads.id'), nullable=False, unique=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('source_version', sa.String(255), nullable=False),
        sa.Column('columns', sa.JSON(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False))


def downgrade():
    op.drop_table('cleaning_configurations')
