"""Reviewed AI value mappings for cleaning drafts."""
from alembic import op
import sqlalchemy as sa
revision = '0006_value_proposals'
down_revision = '0005_cleaning_treatments'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('cleaning_configurations', sa.Column('ai_review', sa.JSON(), nullable=False, server_default='{}'))

def downgrade():
    with op.batch_alter_table('cleaning_configurations') as batch:
        batch.drop_column('ai_review')
