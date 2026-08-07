from gateway.app.database import create_database_engine, create_session_factory, initialize_database
from gateway.app.repositories.firmware_repository import FirmwareHistoryRepository


def test_records_history_only_when_metadata_changes(settings):
    engine = create_database_engine(settings); initialize_database(engine); factory = create_session_factory(engine)
    base = {"sensor_package_version":"0.1.0","firmware_version":"0.1.0","protocol_version":"1.0.0",
            "configuration_schema_version":1,"build_identifier":"build-a","git_commit":"abc"}
    with factory() as session:
        repository = FirmwareHistoryRepository(session)
        first = repository.record("MG24-0001", base, "compatible")
        assert repository.record("MG24-0001", base, "compatible").id == first.id
        repository.record("MG24-0001", {**base, "build_identifier":"build-b"}, "compatible")
        assert len(repository.list("MG24-0001")) == 2
