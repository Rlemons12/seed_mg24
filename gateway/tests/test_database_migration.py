import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

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


def test_data_management_schema_is_additive_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'data-management.db'}")
    initialize_database(engine)
    initialize_database(engine)
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("readings")}
    assert {"sensor_boot_id", "sample_count"} <= columns
    assert "telemetry_sync_states" in inspector.get_table_names()
    indexes = {item["name"] for item in inspector.get_indexes("readings")}
    assert "ix_readings_device_boot_sequence" in indexes


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


def test_migration_tolerates_null_and_duplicate_hardware_ids_but_blocks_new_active_duplicates(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'duplicates.db'}")
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE registered_devices (
              id INTEGER PRIMARY KEY, device_id VARCHAR(96), hardware_id VARCHAR(18),
              enabled BOOLEAN NOT NULL DEFAULT 1, archived BOOLEAN NOT NULL DEFAULT 0
            )
        """))
        connection.execute(text("""
            INSERT INTO registered_devices(id,device_id,hardware_id,archived) VALUES
              (1,'OLD-1','0x0123456789ABCDEF',1),
              (2,'OLD-2','0x0123456789ABCDEF',1),
              (3,'OLD-3',NULL,0),(4,'OLD-4',NULL,0)
        """))
    initialize_database(engine)
    initialize_database(engine)
    with engine.begin() as connection:
        connection.execute(text("UPDATE registered_devices SET archived=0 WHERE id=1"))
        with pytest.raises(IntegrityError, match="active hardware_id already registered"):
            connection.execute(text("UPDATE registered_devices SET archived=0 WHERE id=2"))
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM registered_devices")) == 4
        assert connection.scalar(text("SELECT COUNT(*) FROM registered_devices WHERE archived=0 AND hardware_id IS NOT NULL")) == 1


def test_vibration_relearn_schema_is_additive_to_existing_tables(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-vibration.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE vibration_baselines (id INTEGER PRIMARY KEY, baseline_version INTEGER)"))
        connection.execute(text("CREATE TABLE vibration_windows (id INTEGER PRIMARY KEY, window_sequence INTEGER)"))
        connection.execute(text("INSERT INTO vibration_baselines (id,baseline_version) VALUES (1,1)"))
        connection.execute(text("INSERT INTO vibration_windows (id,window_sequence) VALUES (1,42)"))
    initialize_database(engine)
    inspector = inspect(engine)
    baseline_columns = {item["name"] for item in inspector.get_columns("vibration_baselines")}
    window_columns = {item["name"] for item in inspector.get_columns("vibration_windows")}
    assert {"reason", "last_relearn_request_id"} <= baseline_columns
    assert "baseline_version" in window_columns
    assert "vibration_baseline_history" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT COUNT(*) FROM vibration_baselines")) == 1
        assert connection.scalar(text("SELECT COUNT(*) FROM vibration_windows")) == 1


def test_battery_cycle_schema_is_additive_and_enforces_one_active_cycle(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'battery.db'}")
    initialize_database(engine)
    initialize_database(engine)
    inspector = inspect(engine)
    assert {
        "battery_generations", "battery_cycles", "battery_detector_states",
        "battery_replacement_events", "battery_alerts",
    } <= set(inspector.get_table_names())
    cycle_indexes = {item["name"] for item in inspector.get_indexes("battery_cycles")}
    assert {"ix_battery_cycle_device_started", "ux_battery_cycle_active_device"} <= cycle_indexes
    with engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO registered_devices(id,device_id,display_name,device_type,enabled,archived,"
            "compatibility_status,created_at,updated_at,connection_status,lifecycle_state,factory_reset_status) "
            "VALUES(1,'BAT-1','Battery fixture','test',1,0,'compatible',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP,"
            "'disconnected','active','not_requested')"
        ))
        connection.execute(text(
            "INSERT INTO battery_generations(id,registered_device_id,generation_number,started_at,start_reason,"
            "created_at,updated_at) VALUES(1,1,1,CURRENT_TIMESTAMP,'INITIAL_OBSERVATION',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        connection.execute(text(
            "INSERT INTO battery_cycles(registered_device_id,battery_generation_id,cycle_number,started_at,"
            "observed_operating_seconds,unobserved_seconds,telemetry_records_sent,sensor_reboot_count,event_count,"
            "vibration_window_count,configuration_change_count,start_reason,charge_detection_confidence,is_complete,"
            "is_baseline_eligible,created_at,updated_at) VALUES(1,1,1,CURRENT_TIMESTAMP,0,0,0,0,0,0,0,'FIRST_SAMPLE',"
            "'LOW',0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
        ))
        with pytest.raises(IntegrityError):
            connection.execute(text(
                "INSERT INTO battery_cycles(registered_device_id,battery_generation_id,cycle_number,started_at,"
                "observed_operating_seconds,unobserved_seconds,telemetry_records_sent,sensor_reboot_count,event_count,"
                "vibration_window_count,configuration_change_count,start_reason,charge_detection_confidence,is_complete,"
                "is_baseline_eligible,created_at,updated_at) VALUES(1,1,2,CURRENT_TIMESTAMP,0,0,0,0,0,0,0,'MANUAL_CHARGE',"
                "'HIGH',0,1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ))
