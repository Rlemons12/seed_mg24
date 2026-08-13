import json
import math
from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.models import (
    RegisteredDevice,
    SensorInstallation,
    VibrationBaseline,
    VibrationBaselineHistory,
    VibrationCondition,
    VibrationWindow,
)
from gateway.app.schemas import VibrationSummary

FEATURES = (
    "accel_rms_x_g", "accel_rms_y_g", "accel_rms_z_g",
    "accel_peak_x_g", "accel_peak_y_g", "accel_peak_z_g",
    "crest_x", "crest_y", "crest_z", "kurtosis_x", "kurtosis_y", "kurtosis_z",
    "dominant_frequency_x_hz", "dominant_frequency_y_hz", "dominant_frequency_z_hz",
    "dominant_amplitude_x_g", "dominant_amplitude_y_g", "dominant_amplitude_z_g",
    "gyro_rms_x_dps", "gyro_rms_y_dps", "gyro_rms_z_dps",
)


def summary_values(summary: VibrationSummary) -> dict[str, float]:
    values: dict[str, float] = {}
    groups = (
        ("accel_rms_{}_g", summary.accel_rms_g),
        ("accel_peak_{}_g", summary.accel_peak_g),
        ("crest_{}", summary.crest_factor),
        ("kurtosis_{}", summary.kurtosis),
        ("dominant_frequency_{}_hz", summary.dominant_frequency_hz),
        ("dominant_amplitude_{}_g", summary.dominant_amplitude_g),
        ("gyro_rms_{}_dps", summary.gyro_rms_dps),
    )
    for template, group in groups:
        for axis, value in zip("xyz", group, strict=True):
            values[template.format(axis)] = float(value)
    return values


def update_statistics(statistics: dict, values: dict[str, float]) -> dict:
    for name in FEATURES:
        value = values[name]
        item = statistics.setdefault(name, {"count": 0, "mean": 0.0, "m2": 0.0, "min": value, "max": value})
        item["count"] += 1
        delta = value - item["mean"]
        item["mean"] += delta / item["count"]
        item["m2"] += delta * (value - item["mean"])
        item["min"] = min(item["min"], value)
        item["max"] = max(item["max"], value)
    return statistics


def public_statistics(statistics: dict) -> dict:
    result = {}
    for name, item in statistics.items():
        variance = item["m2"] / (item["count"] - 1) if item["count"] > 1 else 0.0
        result[name] = {
            "count": item["count"], "mean": item["mean"], "standard_deviation": math.sqrt(max(0.0, variance)),
            "minimum": item["min"], "maximum": item["max"],
        }
    return result


def evaluate_condition(values: dict[str, float], statistics: dict, bin_width_hz: float) -> tuple[str, float, list[dict]]:
    severity = 0.0
    factors: list[dict] = []
    for name in FEATURES:
        baseline = statistics[name]
        mean = baseline["mean"]
        std = math.sqrt(max(0.0, baseline["m2"] / max(1, baseline["count"] - 1)))
        current = values[name]
        if name.startswith("dominant_frequency"):
            normalized = abs(current - mean) / max(bin_width_hz, std, 1.0e-6)
        else:
            floor = 0.001 if "_g" in name else 0.05
            normalized = abs(current - mean) / max(std, abs(mean) * 0.1, floor)
        if normalized >= 2.0:
            factors.append({
                "feature": name, "baseline": mean, "current": current,
                "normalized_deviation": normalized,
                "change_percent": ((current - mean) / mean * 100.0) if abs(mean) > 1.0e-9 else None,
            })
        severity = max(severity, normalized)
    factors.sort(key=lambda item: item["normalized_deviation"], reverse=True)
    state = "SIGNIFICANT_CHANGE" if severity >= 6.0 else "ELEVATED" if severity >= 3.0 else "NORMAL"
    return state, max(0.0, min(100.0, 100.0 - severity * 10.0)), factors[:5]


class VibrationConditionService:
    def __init__(self, session_factory: sessionmaker[Session], gateway_id: str, *, minimum_windows: int = 100,
                 persistence_windows: int = 3, persistence_interval_seconds: float = 5.0) -> None:
        self.session_factory = session_factory
        self.gateway_id = gateway_id
        self.minimum_windows = minimum_windows
        self.persistence_windows = persistence_windows
        self.persistence_interval = timedelta(seconds=persistence_interval_seconds)

    def process(self, node_id: str, summary: VibrationSummary, *, session_id: str, observed_at) -> dict:
        with self.session_factory() as session:
            try:
                device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == node_id))
                if device is None or device.archived or not device.enabled:
                    raise ValueError("registered device is unavailable")
                installation = session.scalar(select(SensorInstallation).where(
                    SensorInstallation.node_id == node_id, SensorInstallation.interface_id == "IMU0",
                    SensorInstallation.enabled.is_(True), SensorInstallation.archived.is_(False)))
                installation_id = installation.installation_id if installation else None
                baseline = session.scalar(select(VibrationBaseline).where(
                    VibrationBaseline.registered_device_id == device.id,
                    VibrationBaseline.installation_id == installation_id,
                    VibrationBaseline.algorithm_version == summary.algorithm_version))
                if baseline is None:
                    baseline = VibrationBaseline(
                        registered_device_id=device.id, installation_id=installation_id,
                        algorithm_version=summary.algorithm_version, minimum_samples=self.minimum_windows,
                    )
                    session.add(baseline)
                    session.flush()
                condition = session.scalar(select(VibrationCondition).where(VibrationCondition.registered_device_id == device.id))
                if condition is None:
                    condition = VibrationCondition(registered_device_id=device.id, installation_id=installation_id, baseline_id=baseline.id)
                    session.add(condition)
                else:
                    condition.installation_id = installation_id
                    condition.baseline_id = baseline.id

                if baseline.last_session_id == session_id and baseline.last_window_sequence == summary.window_sequence:
                    session.rollback()
                    return {"duplicate": True, "state": condition.state}
                baseline.last_session_id = session_id
                baseline.last_window_sequence = summary.window_sequence
                values = summary_values(summary)
                eligible = summary.validity == "valid" and all(math.isfinite(value) for value in values.values())
                state = "INVALID"
                score = None
                factors: list[dict] = []
                if eligible and baseline.status == "building":
                    statistics = update_statistics(json.loads(baseline.statistics_json), values)
                    baseline.statistics_json = json.dumps(statistics, separators=(",", ":"), allow_nan=False)
                    baseline.sample_count += 1
                    if baseline.sample_count >= baseline.minimum_samples:
                        baseline.status = "frozen"
                        baseline.established_at = observed_at
                        state = "NORMAL"
                        score = 100.0
                    else:
                        state = "BASELINE_PENDING"
                elif eligible and baseline.status == "frozen":
                    statistics = json.loads(baseline.statistics_json)
                    state, score, factors = evaluate_condition(
                        values, statistics, summary.effective_sample_rate_hz / 256.0)
                elif baseline.sample_count == 0:
                    state = "INSUFFICIENT_DATA"

                if state in {"INVALID", "BASELINE_PENDING", "INSUFFICIENT_DATA"}:
                    condition.state = state
                    condition.pending_state = None
                    condition.pending_count = 0
                elif state == condition.state:
                    condition.pending_state = None
                    condition.pending_count = 0
                else:
                    if condition.pending_state == state:
                        condition.pending_count += 1
                    else:
                        condition.pending_state = state
                        condition.pending_count = 1
                    if condition.pending_count >= self.persistence_windows or condition.state == "BASELINE_PENDING" and state == "NORMAL":
                        condition.state = state
                        condition.pending_state = None
                        condition.pending_count = 0
                condition.baseline_similarity_score = score
                condition.factors_json = json.dumps(factors, separators=(",", ":"), allow_nan=False)
                condition.latest_window_sequence = summary.window_sequence
                condition.evaluated_at = observed_at

                latest_persisted = session.scalar(select(VibrationWindow).where(
                    VibrationWindow.registered_device_id == device.id).order_by(desc(VibrationWindow.observed_at)).limit(1))
                latest_observed_at = latest_persisted.observed_at if latest_persisted else None
                if latest_observed_at is not None and latest_observed_at.tzinfo is None:
                    latest_observed_at = latest_observed_at.replace(tzinfo=UTC)
                persist = latest_observed_at is None or observed_at - latest_observed_at >= self.persistence_interval
                if persist:
                    session.add(VibrationWindow(
                        gateway_id=self.gateway_id, registered_device_id=device.id, installation_id=installation_id,
                        session_id=session_id, window_sequence=summary.window_sequence, observed_at=observed_at,
                        device_uptime_ms=summary.device_uptime_ms, schema_version=summary.schema_version,
                        algorithm_version=summary.algorithm_version, baseline_version=baseline.baseline_version,
                        effective_sample_rate_hz=summary.effective_sample_rate_hz, validity=summary.validity, **values,
                    ))
                session.commit()
                return {"duplicate": False, "persisted": persist, "state": condition.state,
                        "score": score, "factors": factors, "baseline_count": baseline.sample_count,
                        "baseline_status": baseline.status}
            except (IntegrityError, SQLAlchemyError):
                session.rollback()
                raise

    def relearn_baseline(self, node_id: str, *, reason: str | None = None,
                         request_id: str | None = None) -> dict:
        with self.session_factory() as session:
            try:
                device = session.scalar(select(RegisteredDevice).where(
                    RegisteredDevice.device_id == node_id, RegisteredDevice.archived.is_(False)))
                if device is None:
                    raise ValueError("device not found")
                condition = session.scalar(select(VibrationCondition).where(
                    VibrationCondition.registered_device_id == device.id))
                baseline = session.get(VibrationBaseline, condition.baseline_id) if condition and condition.baseline_id else None
                if baseline is None:
                    baseline = session.scalar(select(VibrationBaseline).where(
                        VibrationBaseline.registered_device_id == device.id).order_by(desc(VibrationBaseline.updated_at)))
                if baseline is None:
                    raise ValueError("vibration baseline is unavailable until a vibration summary is received")
                if request_id and baseline.last_relearn_request_id == request_id:
                    return {
                        "status": baseline.status, "baseline_version": baseline.baseline_version,
                        "sample_count": baseline.sample_count, "minimum_samples": baseline.minimum_samples,
                        "reason": baseline.reason, "duplicate": True,
                    }
                now = datetime.now(UTC)
                session.add(VibrationBaselineHistory(
                    registered_device_id=baseline.registered_device_id, installation_id=baseline.installation_id,
                    baseline_version=baseline.baseline_version, algorithm_version=baseline.algorithm_version,
                    status="superseded", sample_count=baseline.sample_count, minimum_samples=baseline.minimum_samples,
                    statistics_json=baseline.statistics_json, created_at=baseline.created_at,
                    established_at=baseline.established_at, superseded_at=now, reason=reason,
                ))
                baseline.baseline_version += 1
                baseline.status = "building"
                baseline.sample_count = 0
                baseline.statistics_json = "{}"
                baseline.last_session_id = None
                baseline.last_window_sequence = None
                baseline.created_at = now
                baseline.established_at = None
                baseline.reason = reason
                baseline.last_relearn_request_id = request_id
                if condition is None:
                    condition = VibrationCondition(registered_device_id=device.id, installation_id=baseline.installation_id,
                                                   baseline_id=baseline.id)
                    session.add(condition)
                condition.baseline_id = baseline.id
                condition.installation_id = baseline.installation_id
                condition.state = "BASELINE_PENDING"
                condition.baseline_similarity_score = None
                condition.factors_json = "[]"
                condition.pending_state = None
                condition.pending_count = 0
                condition.latest_window_sequence = None
                condition.evaluated_at = now
                session.commit()
                return {
                    "status": baseline.status, "baseline_version": baseline.baseline_version,
                    "sample_count": 0, "minimum_samples": baseline.minimum_samples,
                    "created_at": baseline.created_at, "established_at": None,
                    "algorithm_version": baseline.algorithm_version, "installation_id": baseline.installation_id,
                    "reason": baseline.reason, "condition_state": condition.state, "duplicate": False,
                }
            except (IntegrityError, SQLAlchemyError):
                session.rollback()
                raise

    def reset_baseline(self, node_id: str) -> None:
        """Compatibility wrapper retaining history while starting a new analytical baseline."""
        self.relearn_baseline(node_id, reason="Baseline reset through compatibility endpoint")
