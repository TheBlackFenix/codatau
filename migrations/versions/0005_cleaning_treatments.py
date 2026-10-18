"""Treatment preferences for draft cleaning."""
from alembic import op
import sqlalchemy as sa
revision = '0005_cleaning_treatments'
down_revision = '0004_duplicate_preferences'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('cleaning_configurations', sa.Column('treatments', sa.JSON(), nullable=False, server_default='{}'))

def downgrade():
    with op.batch_alter_table('cleaning_configurations') as batch:
        batch.drop_column('treatments')
