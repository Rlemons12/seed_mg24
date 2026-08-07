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
