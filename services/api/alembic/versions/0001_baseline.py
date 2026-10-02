"""Establish the empty engineering-baseline migration chain."""

from collections.abc import Sequence
from typing import Union

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No product tables are introduced in the foundation phase."""


def downgrade() -> None:
    """No product tables are introduced in the foundation phase."""
