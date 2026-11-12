"""Aggregate persisted queue evidence without projecting worker liveness."""

from datetime import datetime

from sqlalchemy import Connection, case, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models import SpeakingExecutionJob, SpeechTranscriptionJob, WritingExecutionJob
from app.operations.readiness import is_database_unavailable
from app.operations.telemetry import Failure, Signal, record

JOB_MODELS: dict[
    str, type[SpeechTranscriptionJob] | type[WritingExecutionJob] | type[SpeakingExecutionJob]
] = {
    "transcription": SpeechTranscriptionJob,
    "writing": WritingExecutionJob,
    "speaking": SpeakingExecutionJob,
}
JOB_STATES = ("QUEUED", "CLAIMED", "RETRYING", "LEASE_EXPIRED", "SUCCEEDED", "FAILED", "CANCELLED")


def worker_queue_counts(
    session: Session, measured_at: datetime
) -> dict[str, dict[str, int]] | None:
    result: dict[str, dict[str, int]] = {}
    try:
        # Use an independent connection so the bound does not affect the authenticated request.
        bind = session.get_bind()
        engine = bind.engine if isinstance(bind, Connection) else bind
        with engine.connect() as connection:
            if connection.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL statement_timeout = '750ms'"))
            for label, job in JOB_MODELS.items():
                state = case(
                    ((job.status == "RUNNING") & (job.lease_expires_at > measured_at), "CLAIMED"),
                    (job.status == "RUNNING", "LEASE_EXPIRED"),
                    (job.status == "RETRYABLE", "RETRYING"),
                    (job.status.in_(("READY", "COMPLETED")), "SUCCEEDED"),
                    else_=job.status,
                )
                rows = connection.execute(
                    select(
                        state,
                        func.count(),
                        func.count().filter(job.failure_category == "RETRY_EXHAUSTED"),
                    ).group_by(state)
                )
                counts = dict.fromkeys(JOB_STATES, 0)
                for status, count, exhausted in rows:
                    assert isinstance(status, str) and isinstance(count, int)
                    counts[status] = count
                    if exhausted:
                        record(Signal.WORKER_RETRY, Failure.RETRY_EXHAUSTED)
                if counts["LEASE_EXPIRED"]:
                    record(Signal.WORKER_LEASE, Failure.STALE_LEASE)
                result[label] = counts
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) != "57014" and not is_database_unavailable(exc):
            raise
        return None
    return result
