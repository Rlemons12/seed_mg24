import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.models import Reading, VibrationWindow

logger = logging.getLogger(__name__)


class TelemetryRetentionService:
    def __init__(self, session_factory: sessionmaker[Session], retention_days: int | None, batch_size: int = 1000) -> None:
        self.session_factory = session_factory
        self.retention_days = retention_days
        self.batch_size = batch_size

    def cleanup_batch(self, now: datetime | None = None) -> int:
        if self.retention_days is None:
            return 0
        cutoff = (now or datetime.now(UTC)) - timedelta(days=self.retention_days)
        with self.session_factory() as session:
            vibration_ids = list(session.scalars(select(VibrationWindow.id).where(
                VibrationWindow.observed_at < cutoff).order_by(VibrationWindow.id).limit(self.batch_size)))
            if vibration_ids:
                session.execute(delete(VibrationWindow).where(VibrationWindow.id.in_(vibration_ids)))
                session.commit()
                logger.info("Deleted %d vibration windows older than %s", len(vibration_ids), cutoff.isoformat())
                return len(vibration_ids)
            ids = list(session.scalars(select(Reading.id).where(Reading.received_at < cutoff).order_by(Reading.id).limit(self.batch_size)))
            if not ids:
                return 0
            session.execute(delete(Reading).where(Reading.id.in_(ids)))
            session.commit()
            logger.info("Deleted %d telemetry readings older than %s", len(ids), cutoff.isoformat())
            return len(ids)
