from datetime import datetime

from sqlalchemy import Select, and_, desc, func, or_, select
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
        installation_id: str | None = None,
        interface_id: str | None = None,
        before: tuple[datetime, int] | None = None,
        include_total: bool = True,
    ) -> tuple[list[Reading], int | None]:
        filters = [Reading.registered_device_id == registered_device_id]
        if start is not None:
            filters.append(Reading.received_at >= start)
        if end is not None:
            filters.append(Reading.received_at <= end)
        if channel is not None:
            filters.append(Reading.channel == channel)
        if installation_id is not None:
            filters.append(Reading.installation_id == installation_id)
        if interface_id is not None:
            filters.append(Reading.interface_id == interface_id)
        if before is not None:
            before_at, before_id = before
            filters.append(or_(Reading.received_at < before_at, and_(Reading.received_at == before_at, Reading.id < before_id)))
        statement: Select = select(Reading).where(*filters)
        total = (self.session.scalar(select(func.count()).select_from(statement.subquery())) or 0) if include_total else None
        rows = list(self.session.scalars(statement.order_by(desc(Reading.received_at), desc(Reading.id)).offset(offset).limit(limit)))
        return rows, total
