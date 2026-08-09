from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gateway.app.models import ProvisioningAttempt, SensorInstallation, utc_now


class DuplicateInstallationError(ValueError):
    pass


class InstallationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list(self, include_archived: bool = False) -> list[SensorInstallation]:
        statement = select(SensorInstallation).order_by(SensorInstallation.device_id)
        if not include_archived:
            statement = statement.where(SensorInstallation.archived.is_(False))
        return list(self.session.scalars(statement))

    def get(self, installation_id: str) -> SensorInstallation | None:
        return self.session.scalar(select(SensorInstallation).where(SensorInstallation.installation_id == installation_id))

    def get_by_device_id(self, device_id: str) -> SensorInstallation | None:
        return self.session.scalar(select(SensorInstallation).where(SensorInstallation.device_id == device_id))

    def occupied_interface(self, node_id: str, interface_id: str, exclude_installation_id: str | None = None) -> SensorInstallation | None:
        statement = select(SensorInstallation).where(
            SensorInstallation.node_id == node_id,
            SensorInstallation.interface_id == interface_id,
            SensorInstallation.archived.is_(False),
            SensorInstallation.enabled.is_(True),
        )
        if exclude_installation_id:
            statement = statement.where(SensorInstallation.installation_id != exclude_installation_id)
        return self.session.scalar(statement)

    def create(self, **values) -> SensorInstallation:
        installation = SensorInstallation(**values)
        self.session.add(installation)
        try:
            self.session.commit()
        except IntegrityError as exc:
            self.session.rollback()
            raise DuplicateInstallationError("installation_id or device_id is already registered") from exc
        self.session.refresh(installation)
        return installation

    def update(self, installation: SensorInstallation, **values) -> SensorInstallation:
        if "device_id" in values and values["device_id"] != installation.device_id:
            raise ValueError("device_id cannot be changed")
        for key, value in values.items():
            setattr(installation, key, value)
        installation.updated_at = utc_now()
        self.session.commit()
        self.session.refresh(installation)
        return installation

    def add_attempt(self, **values) -> ProvisioningAttempt:
        attempt = ProvisioningAttempt(**values)
        self.session.add(attempt)
        self.session.commit()
        self.session.refresh(attempt)
        return attempt

    def get_attempt(self, transaction_id: str) -> ProvisioningAttempt | None:
        return self.session.scalar(select(ProvisioningAttempt).where(ProvisioningAttempt.transaction_id == transaction_id))

    def update_attempt(self, attempt: ProvisioningAttempt, **values) -> ProvisioningAttempt:
        for key, value in values.items():
            setattr(attempt, key, value)
        attempt.updated_at = utc_now()
        self.session.commit()
        return attempt
