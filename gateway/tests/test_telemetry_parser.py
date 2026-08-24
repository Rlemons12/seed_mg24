import json
import math

import pytest

from gateway.app.ble.telemetry_parser import TelemetryParseError, parse_telemetry

COMPACT = {
    "t": "tele",
    "ms": 2545,
    "m": 735,
    "mp": 0,
    "bv": 4.008,
    "l": 0,
    "bs": 1,
    "be": 1,
    "bc": 1,
    "imu": 1,
    "mk": 1,
    "a": [-0.0776, -0.0034, 1.0219],
    "g": [-0.280, -2.800, 0.000],
    "n": [620, 408, 304, 329, 269, 304],
}


def test_existing_compact_payload_normalizes_channels():
    result = parse_telemetry(json.dumps(COMPACT))
    assert result.channels["microphone_raw"].unit == "adc_count"
    assert result.channels["analog_0"].quality == "uncalibrated"
    assert result.channels["battery_voltage"].value == 4.008


def test_failed_imu_is_not_presented_as_good_zero_data():
    payload = dict(COMPACT, io=0, a=[0, 0, 0], g=[0, 0, 0])
    result = parse_telemetry(json.dumps(payload))
    assert result.channels["acceleration_x"].quality == "sensor_fault"
    assert result.channels["angular_velocity_z"].quality == "sensor_fault"


def test_new_measurement_and_missing_optional_fields():
    result = parse_telemetry('{"t":"m","v":1,"id":"ARM2001-01","c":"sensor_1","rv":1834,"nv":null,"u":"adc_count","q":"uncalibrated"}')
    assert result.record_type == "measurement"
    assert result.sequence_number is None
    assert result.channels["sensor_1"].value == 1834


@pytest.mark.parametrize("payload", ["not json", "[]", '{"t":"unknown","v":1}'])
def test_malformed_payload_rejected(payload):
    with pytest.raises(TelemetryParseError):
        parse_telemetry(payload)


def test_oversized_payload_rejected():
    with pytest.raises(TelemetryParseError):
        parse_telemetry("{}" + " " * 100, max_payload_bytes=10)


def test_non_finite_rejected():
    payload = dict(COMPACT)
    payload["bv"] = math.inf
    with pytest.raises(TelemetryParseError):
        parse_telemetry(json.dumps(payload))


def test_analog_length_is_bounded():
    payload = dict(COMPACT)
    payload["n"] = list(range(17))
    with pytest.raises(TelemetryParseError):
        parse_telemetry(json.dumps(payload))


@pytest.mark.parametrize(
    "kind,expected",
    [("e", "event"), ("h", "heartbeat"), ("ca", "config_ack"), ("ce", "config_error"), ("b", "burst_fragment")],
)
def test_versioned_record_types(kind, expected):
    result = parse_telemetry(json.dumps({"t": kind, "v": 1, "s": 1, "ms": 2, "e": "ok"}))
    assert result.record_type == expected


def test_heartbeat_channels_are_distinguished():
    result = parse_telemetry('{"t":"h","v":1,"s":2,"ms":10,"bv":4.1,"bu":3,"dr":1,"pe":0,"se":0}')
    assert result.channels["battery_voltage"].value_kind == "calibrated"
    assert result.channels["buffer_utilization"].value_kind == "health"


def test_parses_ack_capable_v2_identity_and_sample_count():
    result = parse_telemetry(
        '{"t":"tele","v":2,"id":"MG24-1","bid":"0123456789abcdef","s":9,"ms":1000,"sc":5,"m":800}'
    )
    assert result.sensor_boot_id == "0123456789abcdef"
    assert result.sequence_number == 9 and result.sample_count == 5


@pytest.mark.parametrize("payload", [
    '{"t":"m","v":2,"id":"MG24-1","s":1,"c":"x","rv":1}',
    '{"t":"m","v":2,"id":"MG24-1","bid":"ABCDEF0123456789","s":1,"c":"x","rv":1}',
])
def test_v2_requires_valid_boot_identity(payload):
    with pytest.raises(TelemetryParseError):
        parse_telemetry(payload)
