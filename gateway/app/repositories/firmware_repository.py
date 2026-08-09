from sqlalchemy import select
from sqlalchemy.orm import Session

from gateway.app.models import NodeFirmwareHistory, utc_now


class FirmwareHistoryRepository:
    TRACKED = (
        "sensor_package_version", "firmware_version", "protocol_version", "configuration_schema_version",
        "build_identifier", "git_commit",
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, node_id: str) -> list[NodeFirmwareHistory]:
        statement = (
            select(NodeFirmwareHistory)
            .where(NodeFirmwareHistory.node_id == node_id)
            .order_by(NodeFirmwareHistory.first_seen_at.desc())
        )
        return list(self.session.scalars(statement))

    def record(self, node_id: str, metadata: dict, compatibility_status: str) -> NodeFirmwareHistory:
        latest = self.session.scalar(
            select(NodeFirmwareHistory).where(NodeFirmwareHistory.node_id == node_id).order_by(NodeFirmwareHistory.id.desc())
        )
        values = {key: metadata.get(key) for key in self.TRACKED}
        if latest and all(getattr(latest, key) == value for key, value in values.items()):
            latest.last_seen_at = utc_now()
            latest.compatibility_status = compatibility_status
            self.session.commit()
            return latest
        row = NodeFirmwareHistory(node_id=node_id, compatibility_status=compatibility_status, **values)
        self.session.add(row)
        self.session.commit()
        self.session.refresh(row)
        return row
