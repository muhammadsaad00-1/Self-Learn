"""Registry of every domain module's ORM models.

Importing this module guarantees `Base.metadata` knows every table. Alembic autogenerate and
the schema-drift test depend on it, so add each new module's models here.
"""

from agentflow.approvals import models as approvals
from agentflow.audit import models as audit
from agentflow.db import Base
from agentflow.identity import models as identity
from agentflow.knowledge import models as knowledge
from agentflow.ledger import models as ledger
from agentflow.tools import models as tools
from agentflow.workflow import models as workflow

__all__ = [
    "Base",
    "approvals",
    "audit",
    "identity",
    "knowledge",
    "ledger",
    "tools",
    "workflow",
]
