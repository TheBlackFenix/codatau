"""Duplicate criteria for cleaning drafts."""
from alembic import op
import sqlalchemy as sa
revision = '0004_duplicate_preferences'
down_revision = '0003_cleaning_configuration'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('cleaning_configurations', sa.Column('duplicates', sa.JSON(), nullable=False, server_default='{}'))

def downgrade():
    with op.batch_alter_table('cleaning_configurations') as batch:
        batch.drop_column('duplicates')
