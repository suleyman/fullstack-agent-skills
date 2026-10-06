"""Validate the reserved-within-capacity check added NOT VALID in 0005.

A separate migration, and therefore a separate transaction: the brief lock taken by ADD CONSTRAINT
in 0005 is released before this scan starts. VALIDATE CONSTRAINT takes SHARE UPDATE EXCLUSIVE, so
reads and writes on ticket_types continue while existing rows are checked.

Revision ID: 0006_validate_ticket_reservation_check
Revises: 0005_add_ticket_reservations
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0006_validate_ticket_reservation_check"
down_revision: str | None = "0005_add_ticket_reservations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE ticket_types VALIDATE CONSTRAINT ck_ticket_types_reserved_within_capacity")


def downgrade() -> None:
    # A validated constraint can't be marked NOT VALID again. 0005's downgrade drops it.
    pass
