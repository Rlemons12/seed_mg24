from datetime import datetime

from sqlalchemy import Select, desc, func, select
from sqlalchemy.orm import Session

from gateway.app.models import Reading


class ReadingRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_many(self, readings: list[Reading]) -> list[Reading]:
        self.session.add_all(readings)
        self.session.commit()
        return readings

    def latest(self, registered_device_id: int) -> list[Reading]:
        latest_ids = (
            select(func.max(Reading.id).label("id"))
            .where(Reading.registered_device_id == registered_device_id)
            .group_by(Reading.channel)
            .subquery()
        )
        return list(self.session.scalars(select(Reading).join(latest_ids, Reading.id == latest_ids.c.id).order_by(Reading.channel)))

    def history(
        self,
        registered_device_id: int,
        *,
        offset: int,
        limit: int,
        start: datetime | None = None,
        end: datetime | None = None,
        channel: str | None = None,
    ) -> tuple[list[Reading], int]:
        filters = [Reading.registered_device_id == registered_device_id]
        if start is not None:
            filters.append(Reading.received_at >= start)
        if end is not None:
            filters.append(Reading.received_at <= end)
        if channel is not None:
            filters.append(Reading.channel == channel)
        statement: Select = select(Reading).where(*filters)
        total = self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0
        rows = list(self.session.scalars(statement.order_by(desc(Reading.received_at), desc(Reading.id)).offset(offset).limit(limit)))
        return rows, total
