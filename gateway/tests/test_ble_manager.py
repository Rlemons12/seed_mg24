import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from gateway.app.ble.connection import DeviceConnection, reconnect_delay
from gateway.app.ble.manager import BleManager, validate_command


@pytest.mark.parametrize("command", [
    "PING", "LED ON", "LED OFF", "LED 0", "LED 255", "RATE 50", "RATE 5000",
    "MODE LIVE", "MODE LIVE_NEXT_WAKE", "MODE LOW_POWER",
])
def test_command_allowlist(command):
    assert validate_command(command) == command


@pytest.mark.parametrize("command", ["BLE START", "LED 256", "RATE 49", "RATE 5001", "MODE", "MODE EDGE_SUMMARY", "MODE TURBO", "rm -rf"])
def test_command_rejects_unsupported_input(command):
    with pytest.raises(ValueError):
        validate_command(command)


def test_reconnect_backoff_is_exponential_and_bounded():
    assert [reconnect_delay(1, 5, attempt) for attempt in range(5)] == [1, 2, 4, 5, 5]


@pytest.mark.asyncio
async def test_low_power_countdown_uses_only_current_low_power_evidence(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    manager.schedule("MG24-0001", "AA")
    recent = datetime.now(UTC) - timedelta(seconds=60)
    manager._confirm_runtime_mode("MG24-0001", "LOW_POWER", recent)
    runtime = manager.runtime("MG24-0001")
    assert 238 <= runtime["low_power_seconds_to_next_wake"] <= 240
    assert runtime["low_power_next_wake_at"] == recent + timedelta(seconds=300)
    manager._confirm_runtime_mode("MG24-0001", "LOW_POWER", datetime.now(UTC) - timedelta(seconds=600))
    overdue = manager.runtime("MG24-0001")
    assert overdue["low_power_seconds_to_next_wake"] == 0
    await manager.shutdown()


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
async def test_mode_write_stays_pending_until_ack_and_telemetry_confirmation(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def accept(_command):
        return None

    connection.send_command = accept
    assert manager.runtime("MG24-0001")["actual_mode"] == "UNKNOWN"
    await manager.command("MG24-0001", "MODE LIVE")
    runtime = manager.runtime("MG24-0001")
    assert runtime["actual_mode"] == "UNKNOWN" and runtime["transition_state"] == "LIVE_PENDING"
    manager._observe_runtime_evidence(
        "MG24-0001", b'{"t":"ca","v":1,"s":1,"ms":2,"rm":"live","code":"mode_live"}'
    )
    runtime = manager.runtime("MG24-0001")
    assert runtime["actual_mode"] == "UNKNOWN" and runtime["transition_acknowledged"] is True
    manager._observe_runtime_evidence("MG24-0001", b'{"t":"tele","v":1,"rm":"live"}')
    assert manager.runtime("MG24-0001")["transition_state"] == "LIVE_CONFIRMED"
    await manager.command("MG24-0001", "MODE LOW_POWER")
    assert manager.runtime("MG24-0001")["actual_mode"] == "LIVE"
    assert manager.runtime("MG24-0001")["transition_state"] == "LOW_POWER_PENDING"
    manager._observe_runtime_evidence("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    assert manager.runtime("MG24-0001")["actual_mode"] == "LOW_POWER"
    assert 1 <= manager.runtime("MG24-0001")["low_power_seconds_to_next_wake"] <= 300
    await manager.shutdown()


@pytest.mark.asyncio
async def test_live_timeout_waits_for_firmware_low_power_evidence(settings, monkeypatch):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"
    sent = []

    async def accept(command):
        sent.append(command)

    async def no_delay(_seconds):
        pass

    connection.send_command = accept
    monkeypatch.setattr("gateway.app.ble.manager.asyncio.sleep", no_delay)
    manager.reporting_modes["MG24-0001"] = "LIVE"
    await manager._expire_live_mode("MG24-0001")
    assert sent == []
    assert manager.runtime("MG24-0001")["actual_mode"] == "LIVE"
    assert manager.runtime("MG24-0001")["transition_state"] == "LOW_POWER_PENDING"
    assert manager.runtime("MG24-0001")["live_mode_ends_at"] is None
    await manager.shutdown()


@pytest.mark.asyncio
async def test_live_on_next_wake_is_deferred_until_telemetry(settings):
    received = []

    async def telemetry(device_id, data):
        received.append((device_id, data))

    async def status(*_args):
        pass

    manager = BleManager(settings, telemetry, status)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"
    sent = []

    async def accept(command):
        sent.append(command)

    connection.send_command = accept
    manager.reporting_modes["MG24-0001"] = "LOW_POWER"
    await manager.command("MG24-0001", "MODE LIVE_NEXT_WAKE")
    assert sent == []
    assert manager.runtime("MG24-0001")["live_on_next_wake"] is True
    replay = b'{"t":"tele","v":1,"rm":"low_power","d":true}'
    await connection.telemetry_callback("MG24-0001", replay)
    assert sent == []
    wake = b'{"t":"tele","v":1,"rm":"low_power"}'
    await connection.telemetry_callback("MG24-0001", wake)
    assert received == [("MG24-0001", replay), ("MG24-0001", wake)]
    assert sent == ["MODE LIVE"]
    assert manager.runtime("MG24-0001")["actual_mode"] == "LOW_POWER"
    assert manager.runtime("MG24-0001")["transition_state"] == "LIVE_PENDING"
    assert manager.runtime("MG24-0001")["live_on_next_wake"] is True
    await connection.telemetry_callback("MG24-0001", b'{"t":"tele","v":1,"rm":"live"}')
    assert manager.runtime("MG24-0001")["actual_mode"] == "LIVE"
    assert manager.runtime("MG24-0001")["live_on_next_wake"] is False
    await manager.shutdown()


@pytest.mark.asyncio
async def test_live_on_next_wake_is_written_before_slow_telemetry_processing(settings):
    events = []

    async def telemetry(*_args):
        events.append("persist")

    async def status(*_args):
        pass

    manager = BleManager(settings, telemetry, status)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def accept(command):
        events.append(command)

    connection.send_command = accept
    manager.reporting_modes["MG24-0001"] = "LOW_POWER"
    await manager.command("MG24-0001", "MODE LIVE_NEXT_WAKE")
    await connection.telemetry_callback("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    assert events == ["MODE LIVE", "persist"]
    assert manager.runtime("MG24-0001")["reporting_mode"] == "LOW_POWER"
    assert manager.runtime("MG24-0001")["transition_state"] == "LIVE_PENDING"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_failed_live_next_wake_send_retains_request(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def fail(_command):
        raise ConnectionError("closed")

    connection.send_command = fail
    await manager.command("MG24-0001", "MODE LIVE_NEXT_WAKE")
    await connection.telemetry_callback("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    runtime = manager.runtime("MG24-0001")
    assert runtime["live_on_next_wake"] is True
    assert runtime["transition_state"] == "LIVE_REQUESTED"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_disconnect_during_live_pending_sets_actual_unknown(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"
    connection.send_command = callback
    manager._confirm_runtime_mode("MG24-0001", "LOW_POWER", datetime.now(UTC))
    await manager.command("MG24-0001", "MODE LIVE")
    await connection.status_callback("backoff", "ConnectionError")
    runtime = manager.runtime("MG24-0001")
    assert runtime["actual_mode"] == "UNKNOWN"
    assert runtime["transition_state"] == "LIVE_PENDING"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_duplicate_mode_evidence_is_idempotent(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    manager.schedule("MG24-0001", "AA")
    evidence = b'{"t":"tele","v":1,"rm":"low_power"}'
    manager._observe_runtime_evidence("MG24-0001", evidence)
    first = manager.runtime("MG24-0001")
    manager._observe_runtime_evidence("MG24-0001", evidence)
    second = manager.runtime("MG24-0001")
    assert first["actual_mode"] == second["actual_mode"] == "LOW_POWER"
    assert second["transition_state"] == "LOW_POWER_CONFIRMED"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_explicit_live_rearm_moves_deadline_but_duplicate_telemetry_does_not(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"
    connection.send_command = callback
    evidence = b'{"t":"tele","v":1,"rm":"live"}'
    manager._observe_runtime_evidence("MG24-0001", evidence)
    original_deadline = manager.runtime("MG24-0001")["live_mode_ends_at"]

    await manager.command("MG24-0001", "MODE LIVE")
    manager._observe_runtime_evidence("MG24-0001", evidence)
    rearmed_deadline = manager.runtime("MG24-0001")["live_mode_ends_at"]
    assert rearmed_deadline > original_deadline

    manager._observe_runtime_evidence("MG24-0001", evidence)
    assert manager.runtime("MG24-0001")["live_mode_ends_at"] == rearmed_deadline
    await manager.shutdown()


@pytest.mark.asyncio
async def test_explicit_remove_clears_live_next_wake_intent_before_reschedule(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    manager.schedule("MG24-0001", "AA")
    await manager.command("MG24-0001", "MODE LIVE_NEXT_WAKE")
    await manager.remove("MG24-0001")
    runtime = manager.runtime("MG24-0001")
    assert runtime["actual_mode"] == "UNKNOWN"
    assert runtime["requested_mode"] is None
    assert runtime["transition_state"] == "UNKNOWN"
    assert runtime["live_on_next_wake"] is False

    sent = []
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def record(command):
        sent.append(command)

    connection.send_command = record
    await connection.telemetry_callback("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    assert sent == []
    assert manager.runtime("MG24-0001")["actual_mode"] == "LOW_POWER"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_temporary_remove_preserves_live_next_wake_intent(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    manager.schedule("MG24-0001", "AA")
    await manager.command("MG24-0001", "MODE LIVE_NEXT_WAKE")
    await manager.remove("MG24-0001", preserve_runtime_intent=True)
    assert manager.runtime("MG24-0001")["live_on_next_wake"] is True

    sent = []
    connection = manager.schedule("MG24-0001", "AA")
    connection.state = "connected"

    async def record(command):
        sent.append(command)

    connection.send_command = record
    await connection.telemetry_callback("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    assert sent == ["MODE LIVE"]
    assert manager.runtime("MG24-0001")["transition_state"] == "LIVE_PENDING"
    await manager.shutdown()


@pytest.mark.asyncio
async def test_delayed_low_power_replay_does_not_move_wake_deadline(settings):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    manager.schedule("MG24-0001", "AA")
    first_evidence = datetime.now(UTC) - timedelta(seconds=120)
    manager._confirm_runtime_mode("MG24-0001", "LOW_POWER", first_evidence)
    first_deadline = manager.runtime("MG24-0001")["low_power_next_wake_at"]

    manager._observe_runtime_evidence(
        "MG24-0001", b'{"t":"tele","v":1,"rm":"low_power","d":true}'
    )
    assert manager.runtime("MG24-0001")["low_power_next_wake_at"] == first_deadline

    manager._observe_runtime_evidence("MG24-0001", b'{"t":"tele","v":1,"rm":"low_power"}')
    assert manager.runtime("MG24-0001")["low_power_next_wake_at"] > first_deadline
    await manager.shutdown()


@pytest.mark.asyncio
async def test_identify_uses_distinctive_pattern_and_finishes_off(settings, monkeypatch):
    async def callback(*_args):
        pass

    manager = BleManager(settings, callback, callback)
    commands = []

    async def record(_device_id, command):
        commands.append(command)
        return command

    async def no_delay(_seconds):
        pass

    monkeypatch.setattr(manager, "command", record)
    monkeypatch.setattr("gateway.app.ble.manager.asyncio.sleep", no_delay)
    await manager.identify("MG24-0001")
    assert commands == [
        "LED OFF", "LED ON", "LED OFF", "LED ON", "LED OFF",
        "LED ON", "LED OFF", "LED ON", "LED OFF", "LED OFF",
    ]


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
async def test_closed_gatt_object_is_reported_as_a_connection_error():
    async def callback(*_args):
        pass

    connection = DeviceConnection(
        "MG24-0001", "AA", telemetry_callback=callback, status_callback=callback,
        connection_semaphore=asyncio.Semaphore(1),
    )
    future = asyncio.get_running_loop().create_future()
    connection._commands.put_nowait(("MODE LIVE", future))

    class ClosedClient:
        async def write_gatt_char(self, *_args, **_kwargs):
            raise OSError("The object has been closed")

    with pytest.raises(OSError, match="closed"):
        await connection._drain_commands(ClosedClient())
    with pytest.raises(ConnectionError, match="device connection closed"):
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
