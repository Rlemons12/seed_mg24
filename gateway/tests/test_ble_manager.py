import asyncio

import pytest

from gateway.app.ble.connection import DeviceConnection, reconnect_delay
from gateway.app.ble.manager import BleManager, validate_command


@pytest.mark.parametrize("command", ["PING", "LED ON", "LED OFF", "LED 0", "LED 255", "RATE 50", "RATE 5000", "MODE LIVE", "MODE EDGE_SUMMARY"])
def test_command_allowlist(command):
    assert validate_command(command) == command


@pytest.mark.parametrize("command", ["BLE START", "LED 256", "RATE 49", "RATE 5001", "MODE", "MODE TURBO", "rm -rf"])
def test_command_rejects_unsupported_input(command):
    with pytest.raises(ValueError):
        validate_command(command)


def test_reconnect_backoff_is_exponential_and_bounded():
    assert [reconnect_delay(1, 5, attempt) for attempt in range(5)] == [1, 2, 4, 5, 5]


@pytest.mark.asyncio
async def test_multiple_devices_have_independent_state(settings):
    async def telemetry(*_):
        pass

    async def status(*_):
        pass

    manager = BleManager(settings, telemetry, status)
    first = manager.schedule("ARM2001-01", "AA")
    second = manager.schedule("ARM2001-02", "BB")
    assert first is not second and first._commands is not second._commands
    await manager.shutdown()


@pytest.mark.asyncio
async def test_reporting_mode_tracks_successful_mode_commands(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def accept(_command):
        return None

    connection.send_command = accept
    assert manager.runtime("MG24-0001")["reporting_mode"] == "EDGE_SUMMARY"
    await manager.command("MG24-0001", "MODE LIVE")
    assert manager.runtime("MG24-0001")["reporting_mode"] == "LIVE"
    await manager.command("MG24-0001", "MODE EDGE_SUMMARY")
    assert manager.runtime("MG24-0001")["reporting_mode"] == "EDGE_SUMMARY"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_stop_clears_pending_commands_safely():
    async def callback(*_args):
        pass

    connection = DeviceConnection(
        "MG24-0001", "AA", telemetry_callback=callback, status_callback=callback,
        connection_semaphore=asyncio.Semaphore(1),
    )
    future = asyncio.get_running_loop().create_future()
    connection._commands.put_nowait(("PING", future))
    await connection.stop()
    assert connection._commands.empty()
    with pytest.raises(ConnectionError, match="pending command cancelled"):
        await future


@pytest.mark.asyncio
async def test_persistence_ack_is_capability_gated_and_bounded():
    async def callback(*_args):
        pass

    connection = DeviceConnection(
        "MG24-0001", "AA", telemetry_callback=callback, status_callback=callback,
        connection_semaphore=asyncio.Semaphore(1),
    )
    connection.state = "connected"
    await connection.send_persistence_ack("0123456789abcdef", 7)
    assert connection._commands.empty()
    connection.capabilities = {"data_management": {
        "telemetry_version": 2, "boot_id": True, "persistence_ack": True, "backlog_ack": True,
    }}
    await connection.send_persistence_ack("0123456789abcdef", 7)
    await connection.send_persistence_ack("0123456789abcdef", 8)
    await connection.send_persistence_ack("0123456789abcdef", 6)
    assert connection._commands.empty()
    assert connection._pending_persistence_ack == ("0123456789abcdef", 8)
    assert connection._command_ready.is_set()

    class Client:
        writes = []

        async def write_gatt_char(self, _uuid, value, response):
            self.writes.append((value, response))

    client = Client()
    await connection._drain_commands(client)
    assert client.writes == [(b"TACK 2 0123456789abcdef 8\n", True)]
    assert connection._pending_persistence_ack is None


@pytest.mark.asyncio
async def test_live_ack_burst_never_consumes_operator_command_queue(settings):
    async def callback(*_args):
        pass

    connection = DeviceConnection(
        "MG24-0001", "AA", telemetry_callback=callback, status_callback=callback,
        connection_semaphore=asyncio.Semaphore(1),
    )
    connection.state = "connected"
    connection.capabilities = {"data_management": {
        "telemetry_version": 2, "boot_id": True, "persistence_ack": True, "backlog_ack": True,
    }}
    for sequence in range(1000):
        await connection.send_persistence_ack("0123456789abcdef", sequence)
    assert connection._commands.empty()
    assert connection._pending_persistence_ack == ("0123456789abcdef", 999)


class FailingClient:
    def __init__(self, *_args, **_kwargs):
        pass

    async def connect(self):
        raise ConnectionError("fixture failure")


@pytest.mark.asyncio
async def test_one_failing_device_does_not_stop_others(settings):
    states = []

    async def telemetry(*_):
        pass

    async def status(device, state, _error):
        states.append((device, state))

    manager = BleManager(settings, telemetry, status, client_factory=FailingClient)
    manager.schedule("ARM2001-01", "AA")
    manager.schedule("ARM2001-02", "BB")
    await asyncio.sleep(0.15)
    assert any(device == "ARM2001-01" for device, _ in states)
    assert any(device == "ARM2001-02" for device, _ in states)
    await manager.shutdown()
