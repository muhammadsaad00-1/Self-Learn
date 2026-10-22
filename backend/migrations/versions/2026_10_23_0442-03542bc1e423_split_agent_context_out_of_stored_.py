"""split agent context out of stored messages

Data fix. Before 1b889dccf150, chat workflows stored the agents' prompt ("Earlier in this
chat (context only): ... Customer's new message: ...") as the customer's message, and
ticket titles started with the raw category ("failed_payment: ..."). Split the context into
`input.context` and strip the prefix so people see what the customer wrote.

Revision ID: 03542bc1e423
Revises: 1b889dccf150
Create Date: 2026-10-23 04:42:12.538898

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "03542bc1e423"
down_revision: str | Sequence[str] | None = "1b889dccf150"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MARKER = "Earlier in this chat (context only):\n"
SPLIT = "\n\nCustomer's new message:\n"


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            UPDATE workflows
            SET input = input
                || jsonb_build_object(
                    'message', split_part(input->>'message', :split, 2),
                    'context', substr(split_part(input->>'message', :split, 1), length(:marker) + 1)
                )
            WHERE input->>'message' LIKE :marker || '%' AND strpos(input->>'message', :split) > 0
            """
        ).bindparams(marker=MARKER, split=SPLIT)
    )
    op.execute(r"UPDATE tickets SET title = regexp_replace(title, '^[a-z_]+: ', '')")


def downgrade() -> None:
    pass  # lossless split: both parts are kept (input.message + input.context)
