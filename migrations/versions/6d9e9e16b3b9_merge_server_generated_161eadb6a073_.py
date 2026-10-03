"""merge server-generated 161eadb6a073 with v3.4/v3.5 migrations

Revision ID: 6d9e9e16b3b9
Revises: 161eadb6a073, e9a4b86a213f
Create Date: 2026-10-03 13:19:21.431578

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '6d9e9e16b3b9'
down_revision = ('161eadb6a073', 'e9a4b86a213f')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
