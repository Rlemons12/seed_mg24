import json
import math
import re
from datetime import UTC, datetime
from typing import Any

from gateway.app.schemas import ChannelValue, NormalizedTelemetry, VibrationSummary


class TelemetryParseError(ValueError):
    pass


def _finite_number(value: Any, field: str, minimum: float | None = None, maximum: float | None = None) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryParseError(f"{field} must be numeric")
    if not math.isfinite(float(value)):
        raise TelemetryParseError(f"{field} must be finite")
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise TelemetryParseError(f"{field} is outside the permitted range")
    return value


def _uint32(value: Any, field: str) -> int:
    return int(_finite_number(value, field, 0, 0xFFFFFFFF))


def _boot_id(payload: dict[str, Any], schema: int) -> str | None:
    value = payload.get("bid")
    if schema < 2:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{16}", value) is None:
        raise TelemetryParseError("bid must be a 16-character lowercase hexadecimal boot identifier")
    if "s" not in payload:
        raise TelemetryParseError("schema version 2 requires a sequence number")
    return value


def _runtime_mode(payload: dict[str, Any]) -> str | None:
    value = payload.get("rm")
    if value is None:
        return None
    if value not in {"live", "low_power"}:
        raise TelemetryParseError("rm must be live or low_power")
    return value.upper()


def _array(value: Any, field: str, exact: int | None = None, maximum: int = 16) -> list[float | int]:
    if not isinstance(value, list) or len(value) > maximum or exact is not None and len(value) != exact:
        raise TelemetryParseError(f"{field} has an invalid length")
    return [_finite_number(item, f"{field}[{index}]") for index, item in enumerate(value)]


def parse_telemetry(
    data: bytes | bytearray | str, *, max_payload_bytes: int = 2048, received_at: datetime | None = None
) -> NormalizedTelemetry:
    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    if len(raw) > max_payload_bytes:
        raise TelemetryParseError("payload exceeds configured maximum")
    try:
        text = raw.decode("utf-8").strip("\x00\r\n ")
        payload = json.loads(text)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TelemetryParseError("payload is not valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise TelemetryParseError("payload root must be an object")
    timestamp = received_at or datetime.now(UTC)
    kind = payload.get("t") or payload.get("type")
    if kind in {"tele", "telemetry"}:
        return _parse_legacy(payload, timestamp)
    if kind == "v":
        return _parse_vibration(payload, timestamp)
    return _parse_versioned(payload, timestamp)


def _bounded_axis(payload: dict[str, Any], name: str, maximum: float) -> tuple[float, float, float]:
    values = _array(payload.get(name), name, exact=3)
    return tuple(float(_finite_number(value, f"{name}[{index}]", 0, maximum)) for index, value in enumerate(values))


def _parse_vibration(payload: dict[str, Any], received_at: datetime) -> NormalizedTelemetry:
    if set(payload) != {"t", "v", "s", "m", "a", "f", "q", "r", "p", "c", "k", "d", "x", "g"}:
        raise TelemetryParseError("vibration summary has unexpected or missing fields")
    if payload.get("v") != 1 or payload.get("a") != 1:
        raise TelemetryParseError("unsupported vibration schema or algorithm version")
    quality = payload.get("q")
    if quality not in {0, 1}:
        raise TelemetryParseError("vibration validity is invalid")
    summary = VibrationSummary(
        algorithm_version=1,
        window_sequence=_uint32(payload["s"], "s"),
        device_uptime_ms=_uint32(payload["m"], "m"),
        effective_sample_rate_hz=float(_finite_number(payload["f"], "f", 1, 5000)) / 10.0,
        validity="valid" if quality == 1 else "invalid",
        accel_rms_g=tuple(value / 1000.0 for value in _bounded_axis(payload, "r", 16000)),
        accel_peak_g=tuple(value / 1000.0 for value in _bounded_axis(payload, "p", 16000)),
        crest_factor=tuple(value / 10.0 for value in _bounded_axis(payload, "c", 1000)),
        kurtosis=tuple(value / 10.0 for value in _bounded_axis(payload, "k", 10000)),
        dominant_frequency_hz=tuple(value / 10.0 for value in _bounded_axis(payload, "d", 2500)),
        dominant_amplitude_g=tuple(value / 1000.0 for value in _bounded_axis(payload, "x", 16000)),
        gyro_rms_dps=tuple(value / 10.0 for value in _bounded_axis(payload, "g", 20000)),
    )
    if summary.window_sequence == 0:
        raise TelemetryParseError("vibration window sequence must be positive")
    return NormalizedTelemetry(
        schema_version=1,
        record_type="vibration",
        device_uptime_ms=summary.device_uptime_ms,
        sequence_number=summary.window_sequence,
        received_at=received_at,
        vibration=summary,
        original_payload=payload,
    )


def _parse_legacy(payload: dict[str, Any], received_at: datetime) -> NormalizedTelemetry:
    channels: dict[str, ChannelValue] = {}
    mapping = {
        "m": ("microphone_raw", "adc_count", "raw", "good"),
        "mp": ("microphone_percent", "percent", "derived", "estimated"),
        "bv": ("battery_voltage", "V", "calibrated", "good"),
        "l": ("led_brightness", "pwm_count", "state", "good"),
    }
    long_mapping = {"mic": "m", "mic_pct": "mp", "battery_v": "bv", "led": "l"}
    work = dict(payload)
    for long_name, compact in long_mapping.items():
        if compact not in work and long_name in work:
            work[compact] = work[long_name]
    for key, (name, unit, value_kind, quality) in mapping.items():
        if key in work:
            value = _finite_number(work[key], key)
            channels[name] = ChannelValue(
                value=value,
                raw_value=value if value_kind == "raw" else None,
                unit=unit,
                quality=quality,
                value_kind=value_kind,
            )
    imu_indicator = work.get("io", work.get("imu", 1))
    if imu_indicator not in {0, 1} or isinstance(imu_indicator, bool):
        raise TelemetryParseError("io must be 0 or 1")
    imu_quality = "good" if imu_indicator == 1 else "sensor_fault"
    acceleration = work.get("a")
    if acceleration is None and isinstance(work.get("accel"), dict):
        acceleration = [work["accel"].get(axis) for axis in ("x", "y", "z")]
    if acceleration is not None:
        for axis, value in zip("xyz", _array(acceleration, "a", exact=3), strict=True):
            channels[f"acceleration_{axis}"] = ChannelValue(
                value=value, unit="g", quality=imu_quality, value_kind="calibrated"
            )
    gyro = work.get("g")
    if gyro is None and isinstance(work.get("gyro"), dict):
        gyro = [work["gyro"].get(axis) for axis in ("x", "y", "z")]
    if gyro is not None:
        for axis, value in zip("xyz", _array(gyro, "g", exact=3), strict=True):
            channels[f"angular_velocity_{axis}"] = ChannelValue(
                value=value, unit="dps", quality=imu_quality, value_kind="calibrated"
            )
    analog = work.get("n", work.get("analog"))
    if analog is not None:
        for index, value in enumerate(_array(analog, "n", maximum=16)):
            channels[f"analog_{index}"] = ChannelValue(
                value=value, raw_value=value, unit="adc_count", quality="uncalibrated", value_kind="raw"
            )
    uptime = _uint32(work["ms"], "ms") if "ms" in work else None
    sequence = _uint32(work["s"], "s") if "s" in work else None
    schema = int(_finite_number(work.get("v", 1), "v", 1, 255))
    device_id = work.get("id")
    if device_id is not None and (not isinstance(device_id, str) or len(device_id) > 96):
        raise TelemetryParseError("id is invalid")
    sample_count = _uint32(work["sc"], "sc") if "sc" in work else None
    if sample_count == 0:
        raise TelemetryParseError("sample count must be positive")
    return NormalizedTelemetry(
        schema_version=schema,
        device_id=device_id,
        record_type="measurement",
        device_uptime_ms=uptime,
        sequence_number=sequence,
        sensor_boot_id=_boot_id(work, schema),
        sample_count=sample_count,
        received_at=received_at,
        delayed=bool(work.get("d", False)),
        runtime_mode=_runtime_mode(work),
        metadata={
            "persistent_journal": True,
            **({"journal_age_ms": _uint32(work["jm"], "jm")} if "jm" in work else {}),
        } if work.get("pj") == 1 else {},
        channels=channels,
        original_payload=payload,
    )


def _parse_versioned(payload: dict[str, Any], received_at: datetime) -> NormalizedTelemetry:
    kind = payload.get("t")
    record_types = {
        "m": "measurement",
        "e": "event",
        "h": "heartbeat",
        "ca": "config_ack",
        "ce": "config_error",
        "b": "burst_fragment",
    }
    if kind not in record_types:
        raise TelemetryParseError("unsupported message type")
    version = int(_finite_number(payload.get("v"), "v", 1, 255))
    if version not in {1, 2}:
        raise TelemetryParseError("unsupported schema version")
    device_id = payload.get("id")
    if device_id is not None and (not isinstance(device_id, str) or not device_id or len(device_id) > 96):
        raise TelemetryParseError("id is invalid")
    uptime = _uint32(payload["ms"], "ms") if "ms" in payload else None
    sequence = _uint32(payload["s"], "s") if "s" in payload else None
    channel = payload.get("c")
    channels: dict[str, ChannelValue] = {}
    if channel is not None:
        if not isinstance(channel, str) or not channel or len(channel) > 96:
            raise TelemetryParseError("channel is invalid")
        raw_value = _finite_number(payload["rv"], "rv") if payload.get("rv") is not None else None
        normalized = _finite_number(payload["nv"], "nv") if payload.get("nv") is not None else None
        value = normalized if normalized is not None else raw_value
        unit = payload.get("u")
        quality = payload.get("q", "good")
        if quality not in {"good", "estimated", "uncalibrated", "invalid", "sensor_fault", "stale"}:
            raise TelemetryParseError("quality is invalid")
        kind_name = payload.get("k", "calibrated" if normalized is not None else "raw")
        if kind_name not in {"raw", "filtered", "calibrated", "derived", "state", "health"}:
            raise TelemetryParseError("value kind is invalid")
        channels[channel] = ChannelValue(value=value, raw_value=raw_value, unit=unit, quality=quality, value_kind=kind_name)
    event = payload.get("e")
    if event is not None and (not isinstance(event, str) or len(event) > 96):
        raise TelemetryParseError("event is invalid")
    metadata = {key: payload[key] for key in ("fw", "sh", "bu", "dr", "pe", "se", "mi", "fi", "fc", "crc", "code") if key in payload}
    if "code" in metadata and (not isinstance(metadata["code"], str) or not metadata["code"] or len(metadata["code"]) > 96):
        raise TelemetryParseError("code is invalid")
    if kind == "h" and "bv" in payload:
        battery = _finite_number(payload["bv"], "bv")
        channels["battery_voltage"] = ChannelValue(value=battery, unit="V", quality="good", value_kind="calibrated")
    if kind == "h":
        for short_name, channel_name in (
            ("bu", "buffer_utilization"),
            ("dr", "dropped_record_count"),
            ("pe", "processing_error_count"),
            ("se", "sensor_error_count"),
        ):
            if short_name in payload:
                counter = _uint32(payload[short_name], short_name)
                channels[channel_name] = ChannelValue(value=counter, unit="count", quality="good", value_kind="health")
    return NormalizedTelemetry(
        schema_version=version,
        device_id=device_id,
        record_type=record_types[kind],
        device_uptime_ms=uptime,
        sequence_number=sequence,
        sensor_boot_id=_boot_id(payload, version),
        received_at=received_at,
        delayed=bool(payload.get("d", False)),
        runtime_mode=_runtime_mode(payload),
        event=event,
        channels=channels,
        metadata=metadata,
        original_payload=payload,
    )
