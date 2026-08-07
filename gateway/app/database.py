from collections.abc import Generator

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
            cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


def initialize_database(engine: Engine) -> None:
    from gateway.app import models  # noqa: F401

    Base.metadata.create_all(engine)
    migrate_existing_database(engine)


def migrate_existing_database(engine: Engine) -> None:
    """Idempotent compatibility migration for databases created by the first gateway phase."""
    if not engine.url.drivername.startswith("sqlite"):
        return
    inspector = inspect(engine)
    if "readings" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("readings")}
        if "installation_id" not in columns:
            with engine.begin() as connection:
                connection.execute(text("ALTER TABLE readings ADD COLUMN installation_id VARCHAR(64)"))
                connection.execute(text("CREATE INDEX IF NOT EXISTS ix_readings_installation_id ON readings (installation_id)"))
    if "registered_devices" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("registered_devices")}
        additions = {
            "sensor_package_version": "VARCHAR(32)", "protocol_version": "VARCHAR(32)",
            "configuration_schema_version": "INTEGER", "build_identifier": "VARCHAR(96)",
            "firmware_git_commit": "VARCHAR(64)", "compatibility_status": "VARCHAR(32)",
            "compatibility_message": "VARCHAR(500)",
        }
        with engine.begin() as connection:
            for name, sql_type in additions.items():
                if name not in columns:
                    connection.execute(text(f"ALTER TABLE registered_devices ADD COLUMN {name} {sql_type}"))


def session_dependency(factory: sessionmaker[Session]):
    def dependency() -> Generator[Session, None, None]:
        with factory() as session:
            yield session

    return dependency


def get_session() -> Generator[Session, None, None]:
    """Overridden by the application factory with its configured session factory."""
    raise RuntimeError("database session dependency is not configured")
    yield  # pragma: no cover
