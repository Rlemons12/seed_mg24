from sqlalchemy import create_engine, inspect, text

from gateway.app.database import initialize_database


def test_existing_readings_table_migrates_without_data_loss(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE readings (id INTEGER PRIMARY KEY, channel VARCHAR(96))"))
        connection.execute(text("INSERT INTO readings (id,channel) VALUES (1,'analog_0')"))
    initialize_database(engine)
    assert "installation_id" in {column["name"] for column in inspect(engine).get_columns("readings")}
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM readings")) == 1


def test_registered_device_lifecycle_columns_and_event_table_are_additive(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-devices.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE registered_devices (id INTEGER PRIMARY KEY, device_id VARCHAR(96))"))
        connection.execute(text("INSERT INTO registered_devices (id,device_id) VALUES (1,'MG24-0001')"))
    initialize_database(engine)
    inspector = inspect(engine)
    columns = {item["name"] for item in inspector.get_columns("registered_devices")}
    assert {"hardware_id", "lifecycle_state", "removed_at", "removal_reason", "factory_reset_status"} <= columns
    assert "device_lifecycle_events" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM registered_devices")) == 1
