"""minidb: a small SQL query engine over in-memory tables."""

from .api import query
from .errors import QueryError

__all__ = ["query", "QueryError"]
