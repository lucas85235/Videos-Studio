"""Base: o banco como estava antes do Alembic (não muda nada).

Revision ID: 0001_base
Revises:
Create Date: 2026-11-15

Bancos antigos (sem a tabela alembic_version) são marcados nesta revisão e depois sobem até a última; bancos
novos, criados já com o esquema atual pelo create_all, são marcados direto na última.
"""

from collections.abc import Sequence

revision: str = "0001_base"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
