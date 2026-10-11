"""Registry of every domain module's ORM models.

Importing this module guarantees `Base.metadata` knows every table. Alembic autogenerate and
the schema-drift test depend on it, so add each new module's models here.
"""

from agentflow.audit import models as audit
from agentflow.db import Base
from agentflow.identity import models as identity

__all__ = ["Base", "audit", "identity"]
