from collections.abc import Generator
from uuid import UUID, uuid4, uuid5

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from gateway.app.config import Settings


class Base(DeclarativeBase):
    pass


def create_database_engine(settings: Settings) -> Engine:
    settings.ensure_runtime_directories()
    connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
    engine = create_engine(settings.database_url, connect_args=connect_args)
    if settings.database_url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA busy_timeout=5000")
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def initialize_database(engine: Engine, configured_gateway_id: str | None = None) -> str:
    from gateway.app import models  # noqa: F401

    Base.metadata.create_all(engine)
    gateway_id = ensure_gateway_identity(engine, configured_gateway_id)
    migrate_existing_database(engine)
    return gateway_id


def ensure_gateway_identity(engine: Engine, configured_gateway_id: str | None = None) -> str:
    """Return the durable gateway UUID, creating it once inside the local database."""
    if configured_gateway_id is not None:
        configured_gateway_id = str(UUID(configured_gateway_id))
    with engine.begin() as connection:
        existing = connection.scalar(text("SELECT gateway_id FROM gateway_identity WHERE id = 1"))
        if existing:
            if configured_gateway_id and configured_gateway_id != existing:
                raise ValueError("configured gateway_id does not match the identity persisted in this database")
            return str(existing)
        gateway_id = configured_gateway_id or str(uuid4())
        connection.execute(
            text("INSERT INTO gateway_identity (id, gateway_id, created_at) VALUES (1, :gateway_id, CURRENT_TIMESTAMP)"),
            {"gateway_id": gateway_id},
        )
        return gateway_id


def migrate_existing_database(engine: Engine) -> None:
    """Idempotent compatibility migration for databases created by the first gateway phase."""
    if not engine.url.drivername.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "readings" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("readings")}
        additions = {
            "installation_id": "VARCHAR(64)",
            "interface_id": "VARCHAR(64)",
            "gateway_id": "VARCHAR(36)",
            "reading_uuid": "VARCHAR(36)",
            "measured_at": "DATETIME",
            "sensor_boot_id": "VARCHAR(16)",
            "sample_count": "INTEGER",
        }
        with engine.begin() as connection:
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE readings ADD COLUMN {name} {sql_type}"))
            all_columns = columns | additions.keys()
            indexes = {
                "ix_readings_installation_received": ("installation_id", "received_at"),
                "ix_readings_installation_channel_received": ("installation_id", "channel", "received_at"),
                "ix_readings_channel_received": ("channel", "received_at"),
                "ix_readings_device_measured": ("registered_device_id", "measured_at"),
                "ix_readings_gateway_received": ("gateway_id", "received_at"),
                "ix_readings_device_boot_sequence": ("registered_device_id", "sensor_boot_id", "sequence_number"),
            }
            for name, index_columns in indexes.items():
                if set(index_columns) <= all_columns:
                    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {name} ON readings ({', '.join(index_columns)})"))
            if {"registered_device_id", "sensor_boot_id", "sequence_number", "channel"} <= all_columns:
                connection.execute(text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_device_boot_sequence_channel "
                    "ON readings (registered_device_id, sensor_boot_id, sequence_number, channel) "
                    "WHERE sensor_boot_id IS NOT NULL AND sequence_number IS NOT NULL"
                ))
    inspector = inspect(engine)
    if "readings" in inspector.get_table_names() and "gateway_identity" in inspector.get_table_names():
        with engine.begin() as connection:
            gateway_id = connection.scalar(text("SELECT gateway_id FROM gateway_identity WHERE id = 1"))
            # The identity row is created after migration on a fresh upgrade. A later startup performs this backfill.
            if gateway_id:
                connection.execute(
                    text("UPDATE readings SET gateway_id = :gateway_id WHERE gateway_id IS NULL"), {"gateway_id": gateway_id}
                )
                ids = connection.execute(text("SELECT id FROM readings WHERE reading_uuid IS NULL ORDER BY id LIMIT 10000")).scalars().all()
                while ids:
                    for reading_id in ids:
                        stable = str(uuid5(UUID(gateway_id), f"legacy-reading:{reading_id}"))
                        connection.execute(text("UPDATE readings SET reading_uuid=:uuid WHERE id=:id"), {"uuid": stable, "id": reading_id})
                    ids = (
                        connection.execute(text("SELECT id FROM readings WHERE reading_uuid IS NULL ORDER BY id LIMIT 10000"))
                        .scalars()
                        .all()
                    )
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS ux_readings_reading_uuid ON readings (reading_uuid)"))
    if "registered_devices" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("registered_devices")}
        additions = {
            "sensor_package_version": "VARCHAR(32)",
            "protocol_version": "VARCHAR(32)",
            "configuration_schema_version": "INTEGER",
            "build_identifier": "VARCHAR(96)",
            "firmware_git_commit": "VARCHAR(64)",
            "compatibility_status": "VARCHAR(32)",
            "compatibility_message": "VARCHAR(500)",
            "enabled": "BOOLEAN NOT NULL DEFAULT 1",
            "archived": "BOOLEAN NOT NULL DEFAULT 0",
            "hardware_id": "VARCHAR(18)",
            "lifecycle_state": "VARCHAR(32) NOT NULL DEFAULT 'active'",
            "removed_at": "DATETIME",
            "removal_reason": "VARCHAR(240)",
            "factory_reset_status": "VARCHAR(32) NOT NULL DEFAULT 'not_requested'",
        }
        with engine.begin() as connection:
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE registered_devices ADD COLUMN {name} {sql_type}"))
            # Older/partially upgraded databases may already contain duplicate hardware IDs. Keep them readable,
            # index lookups, and prevent any *new* duplicate active membership without blocking startup.
            connection.execute(text("DROP INDEX IF EXISTS ix_registered_devices_hardware_id"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_registered_devices_hardware_id ON registered_devices (hardware_id)"))
            connection.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS trg_registered_devices_hardware_insert
                BEFORE INSERT ON registered_devices
                WHEN NEW.hardware_id IS NOT NULL AND NEW.hardware_id <> '' AND NEW.archived = 0
                  AND EXISTS (SELECT 1 FROM registered_devices
                              WHERE hardware_id = NEW.hardware_id AND archived = 0)
                BEGIN SELECT RAISE(ABORT, 'active hardware_id already registered'); END
            """)
            )
            connection.execute(
                text("""
                CREATE TRIGGER IF NOT EXISTS trg_registered_devices_hardware_update
                BEFORE UPDATE OF hardware_id, archived, lifecycle_state ON registered_devices
                WHEN NEW.hardware_id IS NOT NULL AND NEW.hardware_id <> '' AND NEW.archived = 0
                  AND EXISTS (SELECT 1 FROM registered_devices
                              WHERE hardware_id = NEW.hardware_id AND archived = 0 AND id <> NEW.id)
                BEGIN SELECT RAISE(ABORT, 'active hardware_id already registered'); END
            """)
            )
            connection.execute(
                text("CREATE INDEX IF NOT EXISTS ix_registered_devices_lifecycle_state ON registered_devices (lifecycle_state)")
            )
    inspector = inspect(engine)
    if "vibration_baselines" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("vibration_baselines")}
        with engine.begin() as connection:
            if "reason" not in columns:
                connection.execute(text("ALTER TABLE vibration_baselines ADD COLUMN reason VARCHAR(240)"))
            if "last_relearn_request_id" not in columns:
                connection.execute(text("ALTER TABLE vibration_baselines ADD COLUMN last_relearn_request_id VARCHAR(64)"))
    inspector = inspect(engine)
    if "vibration_windows" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("vibration_windows")}
        if "baseline_version" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE vibration_windows ADD COLUMN baseline_version INTEGER"))


def session_dependency(factory: sessionmaker[Session]):
    def dependency() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    return dependency


def get_session() -> Generator[Session, None, None]:
    """Overridden by the application factory with its configured session factory."""
    raise RuntimeError("database session dependency is not configured")
    yield  # pragma: no cover
