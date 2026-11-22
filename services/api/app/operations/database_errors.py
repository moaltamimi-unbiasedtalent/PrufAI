"""Safe database failures at assessment HTTP boundaries."""

import json
import logging
import uuid
from typing import Literal

from fastapi import HTTPException
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from app.operations.readiness import is_database_unavailable

logger = logging.getLogger("prufai.operations")


def database_http_error(
    error: Exception, *, namespace: Literal["RUNTIME", "SCORING", "RESULT_RELEASE"]
) -> HTTPException | None:
    if not isinstance(error, SQLAlchemyError):
        return None
    status, category = 500, "INTERNAL_ERROR"
    # Only fixed categories leave this boundary, never str(error), SQL, parameters
    # or driver diagnostics. Unknown database/programming errors are not outages.
    if isinstance(error, DBAPIError):
        sqlstate = getattr(error.orig, "sqlstate", None)
        if sqlstate in {"40P01", "40001", "55P03"}:
            status, category = 503, "DATABASE_CONFLICT"
        elif sqlstate == "57014":
            status, category = 503, "DATABASE_TIMEOUT"
        elif is_database_unavailable(error):
            status, category = 503, "TEMPORARILY_UNAVAILABLE"
    request_id = str(uuid.uuid4())
    # A server-generated correlation ID links the response to useful, bounded
    # diagnostics without reflecting caller-controlled data or exception strings.
    logger.warning(
        "%s",
        json.dumps(
            {
                "event": "database_request_failed",
                "operation": namespace,
                "failure_category": category,
                "request_id": request_id,
                "status_code": status,
            },
            separators=(",", ":"),
        ),
    )
    return HTTPException(
        status_code=status,
        detail=f"{namespace}.{category}",
        headers={"X-Request-ID": request_id, "Cache-Control": "no-store"},
    )
