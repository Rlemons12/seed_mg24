import hashlib
import json
import statistics
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from gateway.app.config import Settings
from gateway.app.models import (
    AuditEvent,
    BatteryAlert,
    BatteryCycle,
    BatteryDetectorState,
    BatteryGeneration,
    BatteryReplacementEvent,
    Reading,
    RegisteredDevice,
    SensorInstallation,
    utc_now,
)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _seconds(start: datetime, end: datetime) -> float:
    return max(0.0, (_utc(end) - _utc(start)).total_seconds())


class BatteryHealthService:
    """Durable charge-cycle tracking based on observed runtime and voltage, never state-of-charge percentage."""

    def __init__(self, session_factory: sessionmaker[Session], settings: Settings) -> None:
        self.session_factory = session_factory
        self.settings = settings

    def process_readings(self, node_id: str, rows: list[Reading]) -> None:
        battery = next(
            (row for row in rows if row.channel == "battery_voltage" and row.normalized_value is not None), None
        )
        if battery is None:
            return
        if battery.delayed and battery.measured_at is None:
            # A flash-journal record replayed after a sensor reboot has no
            # trustworthy wall-clock anchor. Retain the Reading, but do not
            # let it drive charge detection as though it were current.
            return
        with self.session_factory() as session:
            device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == node_id))
            if device is None:
                return
            self._observe(
                session, device, float(battery.normalized_value), battery.measured_at or battery.received_at,
                battery.sensor_boot_id, battery.record_type,
            )
            session.commit()

    def _observe(
        self, session: Session, device: RegisteredDevice, voltage: float, observed_at: datetime,
        sensor_boot_id: str | None, record_type: str,
    ) -> None:
        generation = self._current_generation(session, device, observed_at)
        cycle = self._active_cycle(session, device.id)
        if cycle is None:
            cycle = self._new_cycle(session, device, generation, observed_at, voltage, "FIRST_OBSERVATION", "LOW")
        detector = session.get(BatteryDetectorState, device.id)
        if detector is None:
            detector = BatteryDetectorState(registered_device_id=device.id)
            session.add(detector)

        if cycle.last_observed_at is not None:
            gap = _seconds(cycle.last_observed_at, observed_at)
            if gap <= self.settings.battery_maximum_sample_gap_seconds:
                cycle.observed_operating_seconds += gap
            else:
                cycle.unobserved_seconds += gap
        if cycle.last_sensor_boot_id and sensor_boot_id and cycle.last_sensor_boot_id != sensor_boot_id:
            cycle.sensor_reboot_count += 1
        cycle.last_sensor_boot_id = sensor_boot_id or cycle.last_sensor_boot_id
        cycle.last_observed_at = observed_at
        cycle.telemetry_records_sent += 1
        cycle.event_count += int(record_type == "event")
        cycle.updated_at = utc_now()

        previous_voltage = detector.last_voltage
        previous_at = detector.last_sample_at
        gap_reset = previous_at is not None and _seconds(previous_at, observed_at) > self.settings.battery_maximum_sample_gap_seconds
        if gap_reset:
            self._reset_candidate(detector)

        if detector.state == "DISCHARGING" and previous_voltage is not None:
            if voltage > previous_voltage + self.settings.battery_voltage_noise_floor:
                detector.state = "POSSIBLE_CHARGING"
                detector.candidate_started_at = previous_at or observed_at
                detector.candidate_start_voltage = previous_voltage
                detector.candidate_sample_count = 2
                detector.peak_voltage = voltage
        elif detector.state == "POSSIBLE_CHARGING":
            detector.candidate_sample_count += 1
            detector.peak_voltage = max(detector.peak_voltage or voltage, voltage)
            rise = (detector.peak_voltage or voltage) - (detector.candidate_start_voltage or voltage)
            duration = _seconds(detector.candidate_started_at or observed_at, observed_at)
            if voltage < (detector.candidate_start_voltage or voltage) - self.settings.battery_voltage_noise_floor:
                self._reset_candidate(detector)
            elif (
                rise >= self.settings.battery_minimum_voltage_rise
                and duration >= self.settings.battery_charge_confirmation_seconds
                and detector.candidate_sample_count >= self.settings.battery_charge_minimum_samples
            ):
                detector.state = "CHARGING"
                detector.stable_started_at = None
        elif detector.state == "CHARGING":
            detector.peak_voltage = max(detector.peak_voltage or voltage, voltage)
            if previous_voltage is not None and abs(voltage - previous_voltage) <= self.settings.battery_voltage_noise_floor:
                detector.stable_started_at = detector.stable_started_at or observed_at
                if _seconds(detector.stable_started_at, observed_at) >= self.settings.battery_stable_voltage_seconds:
                    self._complete_charge(session, device, generation, cycle, detector, observed_at, voltage)
            else:
                detector.stable_started_at = None

        detector.last_voltage = voltage
        detector.last_sample_at = observed_at
        detector.updated_at = utc_now()
        self._refresh_alerts(session, device, observed_at)

    @staticmethod
    def _reset_candidate(detector: BatteryDetectorState) -> None:
        detector.state = "DISCHARGING"
        detector.candidate_started_at = None
        detector.candidate_start_voltage = None
        detector.candidate_sample_count = 0
        detector.peak_voltage = None
        detector.stable_started_at = None

    def _complete_charge(
        self, session: Session, device: RegisteredDevice, generation: BatteryGeneration, cycle: BatteryCycle,
        detector: BatteryDetectorState, observed_at: datetime, voltage: float,
    ) -> BatteryCycle:
        self._close_cycle(cycle, observed_at, voltage, "CHARGE_CONFIRMED")
        self._classify_completed(session, cycle)
        session.flush()
        new_cycle = self._new_cycle(session, device, generation, observed_at, voltage, "AUTO_CHARGE", "HIGH")
        self._reset_candidate(detector)
        return new_cycle

    def mark_charged(
        self, node_id: str, *, occurred_at: datetime | None = None, voltage: float | None = None,
        partial_charge: bool = False, notes: str | None = None,
    ) -> BatteryCycle:
        occurred_at = occurred_at or utc_now()
        with self.session_factory() as session:
            device = self._device(session, node_id)
            generation = self._current_generation(session, device, occurred_at)
            active = self._active_cycle(session, device.id)
            if active:
                self._close_cycle(active, occurred_at, voltage, "MANUAL_CHARGE")
                if partial_charge:
                    active.is_baseline_eligible = False
                    active.exclusion_reason = "PARTIAL_CHARGE"
                else:
                    self._classify_completed(session, active)
                session.flush()
            cycle = self._new_cycle(session, device, generation, occurred_at, voltage, "MANUAL_CHARGE", "HIGH")
            detector = session.get(BatteryDetectorState, device.id)
            if detector:
                self._reset_candidate(detector)
                detector.last_voltage = voltage or detector.last_voltage
                detector.last_sample_at = occurred_at
            session.add(AuditEvent(
                event_type="battery_charged", subject_id=node_id,
                detail_json=json.dumps({"partial_charge": partial_charge, "notes": notes}),
            ))
            session.commit()
            session.refresh(cycle)
            return cycle

    def replace(
        self, node_id: str, *, reason: str, notes: str | None = None, source: str = "operator",
        occurred_at: datetime | None = None, voltage: float | None = None,
    ) -> BatteryReplacementEvent:
        occurred_at = occurred_at or utc_now()
        with self.session_factory() as session:
            device = self._device(session, node_id)
            old = self._current_generation(session, device, occurred_at)
            prior_summary = self._summary(session, device, occurred_at)
            active = self._active_cycle(session, device.id)
            if active:
                self._close_cycle(active, occurred_at, voltage, "BATTERY_REPLACED")
                active.is_baseline_eligible = False
                active.exclusion_reason = "BATTERY_REPLACED"
                session.flush()
            old.ended_at = occurred_at
            old.updated_at = utc_now()
            generation_number = old.generation_number + 1
            new = BatteryGeneration(
                registered_device_id=device.id, generation_number=generation_number,
                started_at=occurred_at, start_reason="BATTERY_REPLACED", notes=notes,
            )
            session.add(new)
            session.flush()
            self._new_cycle(session, device, new, occurred_at, voltage, "BATTERY_REPLACED", "HIGH")
            event = BatteryReplacementEvent(
                registered_device_id=device.id, old_battery_generation_id=old.id,
                new_battery_generation_id=new.id, replaced_at=occurred_at, reason=reason, notes=notes,
                previous_runtime_health_ratio=prior_summary["health"]["runtime_health_ratio"],
                previous_cycle_count=prior_summary["history"]["completed_cycles"], source=source,
            )
            session.add(event)
            session.add(AuditEvent(
                event_type="battery_replaced", subject_id=node_id,
                detail_json=json.dumps({
                    "old_generation": old.generation_number, "new_generation": generation_number,
                    "reason": reason, "notes": notes, "source": source,
                }),
            ))
            detector = session.get(BatteryDetectorState, device.id)
            if detector:
                self._reset_candidate(detector)
                detector.last_voltage = voltage
                detector.last_sample_at = occurred_at
            session.commit()
            session.refresh(event)
            return event

    def summary(self, node_id: str, now: datetime | None = None) -> dict:
        with self.session_factory() as session:
            return self._summary(session, self._device(session, node_id), now or utc_now())

    def cycles(self, node_id: str, *, limit: int = 100) -> list[dict]:
        with self.session_factory() as session:
            device = self._device(session, node_id)
            rows = list(session.scalars(
                select(BatteryCycle).where(BatteryCycle.registered_device_id == device.id)
                .order_by(BatteryCycle.started_at.desc()).limit(limit)
            ))
            baselines = self._generation_baselines(session, {row.battery_generation_id for row in rows})
            return [self._cycle_dict(
                row, utc_now(),
                row.runtime_seconds / baselines[row.battery_generation_id]
                if row.runtime_seconds is not None and baselines.get(row.battery_generation_id) else None,
            ) for row in rows]

    def cycle(self, node_id: str, cycle_id: int) -> dict | None:
        with self.session_factory() as session:
            device = self._device(session, node_id)
            row = session.scalar(select(BatteryCycle).where(
                BatteryCycle.id == cycle_id, BatteryCycle.registered_device_id == device.id,
            ))
            if row is None:
                return None
            baselines = self._generation_baselines(session, {row.battery_generation_id})
            ratio = (
                row.runtime_seconds / baselines[row.battery_generation_id]
                if row.runtime_seconds is not None and baselines.get(row.battery_generation_id) else None
            )
            return self._cycle_dict(row, utc_now(), ratio)

    def voltage_history(self, node_id: str, *, hours: int = 168, limit: int = 1000) -> list[dict]:
        cutoff = utc_now() - timedelta(hours=hours)
        with self.session_factory() as session:
            device = self._device(session, node_id)
            rows = list(session.scalars(
                select(Reading).where(
                    Reading.registered_device_id == device.id, Reading.channel == "battery_voltage",
                    Reading.received_at >= cutoff, Reading.normalized_value.is_not(None),
                ).order_by(Reading.received_at.desc()).limit(limit)
            ))
            return [{"measured_at": row.received_at, "voltage": row.normalized_value} for row in reversed(rows)]

    def replacement_history(self, node_id: str, *, limit: int = 100) -> list[dict]:
        with self.session_factory() as session:
            device = self._device(session, node_id)
            rows = list(session.scalars(select(BatteryReplacementEvent).where(
                BatteryReplacementEvent.registered_device_id == device.id,
            ).order_by(BatteryReplacementEvent.replaced_at.desc()).limit(limit)))
            return [{
                "id": row.id, "old_battery_generation_id": row.old_battery_generation_id,
                "new_battery_generation_id": row.new_battery_generation_id, "replaced_at": row.replaced_at,
                "reason": row.reason, "notes": row.notes,
                "previous_runtime_health_ratio": row.previous_runtime_health_ratio,
                "previous_cycle_count": row.previous_cycle_count, "source": row.source,
            } for row in rows]

    def _summary(self, session: Session, device: RegisteredDevice, now: datetime) -> dict:
        generation = session.scalar(select(BatteryGeneration).where(
            BatteryGeneration.registered_device_id == device.id, BatteryGeneration.ended_at.is_(None),
        ))
        active = self._active_cycle(session, device.id)
        cycles = [] if generation is None else list(session.scalars(
            select(BatteryCycle).where(
                BatteryCycle.battery_generation_id == generation.id, BatteryCycle.is_complete.is_(True),
            ).order_by(BatteryCycle.cycle_number)
        ))
        eligible = [row for row in cycles if row.is_baseline_eligible and row.runtime_seconds is not None]
        minimum = self.settings.battery_baseline_minimum_cycles
        baseline_sample = eligible[:minimum]
        baseline = statistics.median(row.runtime_seconds for row in baseline_sample) if len(baseline_sample) >= minimum else None
        recent = eligible[-3:]
        recent_runtime = statistics.median(row.runtime_seconds for row in recent) if recent else None
        ratio = recent_runtime / baseline if baseline and recent_runtime is not None else None
        status = self._replacement_status(eligible, baseline)
        trend = self._trend(eligible, baseline)
        replacement_forecast = self._replacement_forecast(eligible, baseline)
        elapsed = _seconds(active.started_at, now) if active else None
        latest_voltage = session.scalar(select(Reading).where(
            Reading.registered_device_id == device.id, Reading.channel == "battery_voltage",
            Reading.normalized_value.is_not(None),
        ).order_by(Reading.received_at.desc()).limit(1))
        voltage_metrics = self._voltage_metrics(session, device.id, now, latest_voltage)
        prediction = self._prediction(eligible, active, now)
        prediction["voltage_based"] = self._voltage_recharge_prediction(voltage_metrics)
        if prediction["confidence"] in {"HIGH", "MEDIUM"} and voltage_metrics["recent_sample_count"] < 3:
            prediction["confidence"] = "LOW"
        voltage_state = self._voltage_state(latest_voltage.normalized_value if latest_voltage else None)
        explanation = self._explanation(baseline, ratio, eligible, status)
        return {
            "device_id": device.device_id,
            "battery_generation": generation.generation_number if generation else None,
            "voltage": {
                "current_v": latest_voltage.normalized_value if latest_voltage else None,
                "measured_at": latest_voltage.received_at if latest_voltage else None,
                "percentage": None,
                "calibration_status": "NOT_CALIBRATED",
                "state": voltage_state,
                "trend": voltage_metrics,
            },
            "current_cycle": self._cycle_dict(active, now) if active else None,
            "history": {
                "completed_cycles": len(cycles),
                "eligible_cycles": len(eligible),
                "latest_completed_runtime_seconds": cycles[-1].runtime_seconds if cycles else None,
                "baseline_runtime_seconds": baseline,
                "average_last_5_seconds": statistics.fmean(row.runtime_seconds for row in eligible[-5:]) if eligible else None,
                "average_last_10_seconds": statistics.fmean(row.runtime_seconds for row in eligible[-10:]) if eligible else None,
                "best_runtime_seconds": max((row.runtime_seconds for row in eligible), default=None),
            },
            "health": {
                "runtime_health_ratio": ratio, "runtime_health_percent": ratio * 100 if ratio is not None else None,
                "status": status, "trend": trend, "explanation": explanation,
            },
            "prediction": prediction,
            "replacement": {"status": status, "explanation": explanation, "forecast": replacement_forecast},
            "current_cycle_runtime_seconds": elapsed,
            "updated_at": now,
        }

    def _prediction(self, eligible: list[BatteryCycle], active: BatteryCycle | None, now: datetime) -> dict:
        minimum = self.settings.battery_baseline_minimum_cycles
        if active is None or len(eligible) < minimum:
            return self._empty_prediction("insufficient comparable completed cycles")
        runtimes = sorted(row.runtime_seconds for row in eligible[-10:] if row.runtime_seconds is not None)
        elapsed = _seconds(active.started_at, now)
        center = statistics.median(runtimes)
        lower_total = runtimes[max(0, int((len(runtimes) - 1) * 0.25))]
        upper_total = runtimes[min(len(runtimes) - 1, int((len(runtimes) - 1) * 0.75 + 0.5))]
        remaining = max(0.0, center - elapsed)
        variability = statistics.pstdev(runtimes) / center if len(runtimes) > 1 and center else 1.0
        observability = active.observability_ratio
        confidence = "HIGH" if len(runtimes) >= 10 and variability <= 0.1 and (observability or 0) >= 0.9 else "MEDIUM"
        if variability > 0.25 or (observability is not None and observability < 0.75):
            confidence = "LOW"
        return {
            "remaining_seconds": remaining,
            "estimated_recharge_at": now + timedelta(seconds=remaining),
            "lower_bound": now + timedelta(seconds=max(0.0, lower_total - elapsed)),
            "upper_bound": now + timedelta(seconds=max(0.0, upper_total - elapsed)),
            "confidence": confidence, "unavailable_reason": None,
        }

    @staticmethod
    def _voltage_metrics(
        session: Session, device_id: int, now: datetime, latest: Reading | None,
    ) -> dict:
        def window_before(at: datetime) -> list[Reading]:
            return list(session.scalars(select(Reading).where(
                Reading.registered_device_id == device_id, Reading.channel == "battery_voltage",
                Reading.normalized_value.is_not(None), Reading.received_at <= at,
            ).order_by(Reading.received_at.desc()).limit(20)))

        current_window = window_before(now)
        one_hour_window = window_before(now - timedelta(hours=1))
        one_day_window = window_before(now - timedelta(hours=24))
        recent = session.execute(select(
            func.min(Reading.normalized_value), func.max(Reading.normalized_value), func.count(Reading.id),
        ).where(
            Reading.registered_device_id == device_id, Reading.channel == "battery_voltage",
            Reading.normalized_value.is_not(None), Reading.received_at >= now - timedelta(hours=24),
        )).one()

        def median_value(rows: list[Reading]) -> float | None:
            values = [row.normalized_value for row in rows if row.normalized_value is not None]
            return statistics.median(values) if values else None

        current_smoothed = median_value(current_window)
        one_hour = median_value(one_hour_window)
        one_day = median_value(one_day_window)

        def slope(previous: float | None, previous_rows: list[Reading]) -> float | None:
            if current_smoothed is None or previous is None or not current_window or not previous_rows:
                return None
            hours = _seconds(previous_rows[0].received_at, current_window[0].received_at) / 3600
            return (current_smoothed - previous) / hours if hours >= 0.5 else None

        slope_1h = slope(one_hour, one_hour_window)
        slope_24h = slope(one_day, one_day_window)

        return {
            "current_smoothed_voltage": current_smoothed,
            "voltage_1h_ago": one_hour,
            "voltage_24h_ago": one_day,
            "voltage_change_per_hour": slope_1h,
            "voltage_change_per_day": slope_24h * 24 if slope_24h is not None else None,
            "voltage_change_per_hour_24h": slope_24h,
            "recent_min_voltage": recent[0], "recent_max_voltage": recent[1],
            "recent_sample_count": recent[2],
        }

    def _voltage_recharge_prediction(self, metrics: dict) -> dict:
        threshold = self.settings.battery_low_voltage_warning
        current = metrics["current_smoothed_voltage"]
        if threshold is None:
            return self._empty_voltage_prediction("low-voltage warning threshold is not configured")
        if current is None:
            return self._empty_voltage_prediction("battery voltage history is unavailable")
        if current <= threshold:
            return {
                "remaining_hours": 0.0, "lower_hours": 0.0, "upper_hours": 0.0,
                "threshold_voltage": threshold, "slope_volts_per_hour": None,
                "confidence": "HIGH", "unavailable_reason": None,
            }
        slopes = [value for value in (
            metrics["voltage_change_per_hour"], metrics["voltage_change_per_hour_24h"],
        ) if value is not None and value < -self.settings.battery_voltage_prediction_min_decline_per_hour]
        if not slopes:
            return self._empty_voltage_prediction("no sustained voltage decline is measurable")
        slope = statistics.median(slopes)
        remaining = max(0.0, (threshold - current) / slope)
        confidence = "MEDIUM"
        if len(slopes) == 2:
            disagreement = abs(slopes[0] - slopes[1]) / abs(slope)
            confidence = "HIGH" if disagreement <= 0.35 else "LOW"
        return {
            "remaining_hours": remaining,
            "lower_hours": max(0.0, (threshold - current) / (slope * 1.25)),
            "upper_hours": max(0.0, (threshold - current) / (slope * 0.75)),
            "threshold_voltage": threshold, "slope_volts_per_hour": slope,
            "confidence": confidence, "unavailable_reason": None,
        }

    @staticmethod
    def _empty_voltage_prediction(reason: str) -> dict:
        return {
            "remaining_hours": None, "lower_hours": None, "upper_hours": None,
            "threshold_voltage": None, "slope_volts_per_hour": None,
            "confidence": "UNKNOWN", "unavailable_reason": reason,
        }

    @staticmethod
    def _empty_prediction(reason: str) -> dict:
        return {
            "remaining_seconds": None, "estimated_recharge_at": None, "lower_bound": None,
            "upper_bound": None, "confidence": "UNKNOWN", "unavailable_reason": reason,
        }

    def _replacement_status(self, eligible: list[BatteryCycle], baseline: float | None) -> str:
        if baseline is None:
            return "LEARNING"
        count = self.settings.battery_required_degraded_cycles
        ratios = [row.runtime_seconds / baseline for row in eligible[-count:]]
        if len(ratios) < count:
            return "GOOD"
        if all(value < self.settings.battery_replace_runtime_ratio for value in ratios):
            return "REPLACE"
        if all(value < self.settings.battery_plan_replacement_runtime_ratio for value in ratios):
            return "PLAN_REPLACEMENT"
        if all(value < self.settings.battery_aging_runtime_ratio for value in ratios):
            return "AGING"
        return "GOOD"

    def _trend(self, eligible: list[BatteryCycle], baseline: float | None) -> str:
        rows = eligible[-self.settings.battery_trend_lookback_cycles:]
        if baseline is None or len(rows) < 3:
            return "UNKNOWN"
        values = [row.runtime_seconds / baseline for row in rows]
        mean_x = (len(values) - 1) / 2
        denominator = sum((index - mean_x) ** 2 for index in range(len(values)))
        slope = sum((index - mean_x) * (value - statistics.fmean(values)) for index, value in enumerate(values)) / denominator
        if slope > 0.03:
            return "IMPROVING"
        if slope >= -0.02:
            return "STABLE"
        if slope >= -0.06:
            return "DECLINING_SLOWLY"
        return "DECLINING_RAPIDLY"

    def _replacement_forecast(self, eligible: list[BatteryCycle], baseline: float | None) -> dict:
        rows = eligible[-self.settings.battery_trend_lookback_cycles:]
        if baseline is None or len(rows) < max(4, self.settings.battery_baseline_minimum_cycles):
            return self._empty_replacement_forecast("insufficient comparable completed cycles")
        ratios = [row.runtime_seconds / baseline for row in rows]
        mean_x = (len(ratios) - 1) / 2
        mean_y = statistics.fmean(ratios)
        denominator = sum((index - mean_x) ** 2 for index in range(len(ratios)))
        slope = sum((index - mean_x) * (value - mean_y) for index, value in enumerate(ratios)) / denominator
        if slope >= -0.005:
            return self._empty_replacement_forecast("runtime is stable, improving, or declining too slowly to extrapolate")
        fitted = [mean_y + slope * (index - mean_x) for index in range(len(ratios))]
        residual = sum((actual - expected) ** 2 for actual, expected in zip(ratios, fitted, strict=True))
        total = sum((actual - mean_y) ** 2 for actual in ratios)
        r_squared = max(0.0, 1 - residual / total) if total > 0 else 0.0
        recent_cycle_seconds = statistics.median(row.runtime_seconds for row in rows[-3:])
        current_ratio = ratios[-1]

        def estimate(threshold: float) -> dict:
            if current_ratio <= threshold:
                return {"days": 0.0, "lower_days": 0.0, "upper_days": 0.0}
            cycles = (threshold - current_ratio) / slope
            center = max(0.0, cycles * recent_cycle_seconds / 86400)
            faster_cycles = (threshold - current_ratio) / (slope * 1.25)
            slower_cycles = (threshold - current_ratio) / (slope * 0.75)
            return {
                "days": center,
                "lower_days": max(0.0, faster_cycles * recent_cycle_seconds / 86400),
                "upper_days": max(0.0, slower_cycles * recent_cycle_seconds / 86400),
            }

        confidence = "HIGH" if len(rows) >= 8 and r_squared >= 0.8 else "MEDIUM"
        if r_squared < 0.5 or len(rows) < 6:
            confidence = "LOW"
        return {
            "plan_replacement": estimate(self.settings.battery_plan_replacement_runtime_ratio),
            "replace": estimate(self.settings.battery_replace_runtime_ratio),
            "confidence": confidence,
            "runtime_ratio_change_per_cycle": slope,
            "r_squared": r_squared,
            "unavailable_reason": None,
        }

    @staticmethod
    def _empty_replacement_forecast(reason: str) -> dict:
        return {
            "plan_replacement": None, "replace": None, "confidence": "UNKNOWN",
            "runtime_ratio_change_per_cycle": None, "r_squared": None, "unavailable_reason": reason,
        }

    @staticmethod
    def _explanation(baseline: float | None, ratio: float | None, eligible: list[BatteryCycle], status: str) -> str:
        if baseline is None:
            return f"Learning from comparable completed cycles; {len(eligible)} eligible cycle(s) are available."
        recent = [round(row.runtime_seconds) for row in eligible[-3:]]
        return (
            f"Status {status}: recent comparable runtimes {recent} seconds; median initial baseline "
            f"{round(baseline)} seconds; runtime health {ratio * 100:.1f}%."
        )

    def _voltage_state(self, voltage: float | None) -> str:
        if voltage is None:
            return "VOLTAGE_UNKNOWN"
        if self.settings.battery_low_voltage_critical is not None and voltage <= self.settings.battery_low_voltage_critical:
            return "VOLTAGE_CRITICAL"
        if self.settings.battery_low_voltage_warning is not None and voltage <= self.settings.battery_low_voltage_warning:
            return "VOLTAGE_WARNING"
        return "VOLTAGE_NORMAL" if self.settings.battery_low_voltage_warning is not None else "VOLTAGE_UNKNOWN"

    def _refresh_alerts(self, session: Session, device: RegisteredDevice, now: datetime) -> None:
        summary = self._summary(session, device, now)
        desired: list[str] = []
        replacement = summary["replacement"]["status"]
        if replacement == "LEARNING":
            desired.append("BATTERY_DATA_INSUFFICIENT")
        elif replacement == "AGING":
            desired.append("BATTERY_RUNTIME_DEGRADING")
        elif replacement == "PLAN_REPLACEMENT":
            desired.append("BATTERY_REPLACEMENT_PLANNED")
        elif replacement == "REPLACE":
            desired.append("BATTERY_REPLACEMENT_REQUIRED")
        voltage_state = summary["voltage"]["state"]
        if voltage_state == "VOLTAGE_WARNING":
            desired.append("BATTERY_VOLTAGE_WARNING")
        elif voltage_state == "VOLTAGE_CRITICAL":
            desired.append("BATTERY_VOLTAGE_CRITICAL")
        remaining = summary["prediction"]["remaining_seconds"]
        if remaining is not None and remaining <= self.settings.battery_recharge_warning_seconds:
            desired.append("BATTERY_RECHARGE_SOON")
        cutoff = now - timedelta(seconds=self.settings.battery_alert_cooldown_seconds)
        for alert_type in desired:
            recent = session.scalar(select(BatteryAlert).where(
                BatteryAlert.registered_device_id == device.id, BatteryAlert.alert_type == alert_type,
                BatteryAlert.last_emitted_at >= cutoff,
            ).order_by(BatteryAlert.last_emitted_at.desc()).limit(1))
            if recent is None:
                session.add(BatteryAlert(
                    registered_device_id=device.id, alert_type=alert_type,
                    detail_json=json.dumps({
                        "replacement_status": replacement,
                        "runtime_health_ratio": summary["health"]["runtime_health_ratio"],
                        "voltage_state": voltage_state,
                    }),
                    first_emitted_at=now, last_emitted_at=now,
                ))

    def _classify_completed(self, session: Session, cycle: BatteryCycle) -> None:
        wall = cycle.runtime_seconds or 0
        observed = cycle.observed_operating_seconds
        cycle.observability_ratio = min(1.0, observed / wall) if wall > 0 else 0.0
        if 1 - cycle.observability_ratio > self.settings.battery_maximum_unobserved_ratio:
            cycle.is_baseline_eligible = False
            cycle.exclusion_reason = "LOW_OBSERVABILITY"
        fingerprint = self._configuration_fingerprint(session, cycle.registered_device_id)
        if cycle.configuration_version and fingerprint and cycle.configuration_version != fingerprint:
            cycle.is_baseline_eligible = False
            cycle.exclusion_reason = "CONFIG_CHANGED"
            cycle.configuration_change_count += 1

    def _generation_baselines(self, session: Session, generation_ids: set[int]) -> dict[int, float | None]:
        result: dict[int, float | None] = {}
        minimum = self.settings.battery_baseline_minimum_cycles
        for generation_id in generation_ids:
            eligible = list(session.scalars(select(BatteryCycle).where(
                BatteryCycle.battery_generation_id == generation_id, BatteryCycle.is_complete.is_(True),
                BatteryCycle.is_baseline_eligible.is_(True), BatteryCycle.runtime_seconds.is_not(None),
            ).order_by(BatteryCycle.cycle_number).limit(minimum)))
            result[generation_id] = (
                statistics.median(row.runtime_seconds for row in eligible) if len(eligible) >= minimum else None
            )
        return result

    def _new_cycle(
        self, session: Session, device: RegisteredDevice, generation: BatteryGeneration, started_at: datetime,
        voltage: float | None, reason: str, confidence: str,
    ) -> BatteryCycle:
        number = session.scalar(select(func.max(BatteryCycle.cycle_number)).where(
            BatteryCycle.battery_generation_id == generation.id,
        )) or 0
        cycle = BatteryCycle(
            registered_device_id=device.id, battery_generation_id=generation.id, cycle_number=number + 1,
            started_at=started_at, start_voltage=voltage, start_reason=reason,
            charge_detection_confidence=confidence, firmware_version=device.firmware_version,
            protocol_version=device.protocol_version, configuration_version=self._configuration_fingerprint(session, device.id),
            last_observed_at=started_at,
        )
        session.add(cycle)
        session.flush()
        return cycle

    @staticmethod
    def _close_cycle(cycle: BatteryCycle, ended_at: datetime, voltage: float | None, reason: str) -> None:
        cycle.ended_at = ended_at
        cycle.end_voltage = voltage
        cycle.runtime_seconds = _seconds(cycle.started_at, ended_at)
        cycle.end_reason = reason
        cycle.is_complete = True
        cycle.updated_at = utc_now()

    @staticmethod
    def _active_cycle(session: Session, device_id: int) -> BatteryCycle | None:
        return session.scalar(select(BatteryCycle).where(
            BatteryCycle.registered_device_id == device_id, BatteryCycle.is_complete.is_(False),
        ))

    @staticmethod
    def _current_generation(
        session: Session, device: RegisteredDevice, started_at: datetime,
    ) -> BatteryGeneration:
        generation = session.scalar(select(BatteryGeneration).where(
            BatteryGeneration.registered_device_id == device.id, BatteryGeneration.ended_at.is_(None),
        ))
        if generation is None:
            generation = BatteryGeneration(
                registered_device_id=device.id, generation_number=1, started_at=started_at,
                start_reason="INITIAL_OBSERVATION",
            )
            session.add(generation)
            session.flush()
        return generation

    @staticmethod
    def _device(session: Session, node_id: str) -> RegisteredDevice:
        device = session.scalar(select(RegisteredDevice).where(RegisteredDevice.device_id == node_id))
        if device is None or device.archived:
            raise LookupError("device not found")
        return device

    @staticmethod
    def _configuration_fingerprint(session: Session, device_id: int) -> str | None:
        rows = list(session.scalars(select(SensorInstallation).join(
            RegisteredDevice, SensorInstallation.node_id == RegisteredDevice.device_id,
        ).where(
            RegisteredDevice.id == device_id, SensorInstallation.enabled.is_(True), SensorInstallation.archived.is_(False),
        ).order_by(SensorInstallation.installation_id)))
        if not rows:
            return None
        value = "|".join(f"{row.installation_id}:{row.configuration_json}" for row in rows)
        return hashlib.sha256(value.encode()).hexdigest()[:16]

    @staticmethod
    def _cycle_dict(cycle: BatteryCycle, now: datetime, runtime_health_ratio: float | None = None) -> dict:
        runtime = cycle.runtime_seconds if cycle.is_complete else _seconds(cycle.started_at, now)
        wall_observed = cycle.observed_operating_seconds + cycle.unobserved_seconds
        observability = cycle.observability_ratio
        if not cycle.is_complete and wall_observed > 0:
            observability = cycle.observed_operating_seconds / wall_observed
        return {
            "id": cycle.id, "battery_generation_id": cycle.battery_generation_id,
            "cycle_number": cycle.cycle_number, "started_at": cycle.started_at, "ended_at": cycle.ended_at,
            "start_voltage": cycle.start_voltage, "end_voltage": cycle.end_voltage,
            "runtime_seconds": runtime, "observed_operating_seconds": cycle.observed_operating_seconds,
            "unobserved_seconds": cycle.unobserved_seconds, "observability_ratio": observability,
            "start_reason": cycle.start_reason, "end_reason": cycle.end_reason,
            "charge_detection_confidence": cycle.charge_detection_confidence, "is_complete": cycle.is_complete,
            "is_baseline_eligible": cycle.is_baseline_eligible, "exclusion_reason": cycle.exclusion_reason,
            "runtime_anomaly_score": cycle.runtime_anomaly_score, "runtime_health_ratio": runtime_health_ratio,
            "firmware_version": cycle.firmware_version, "protocol_version": cycle.protocol_version,
            "configuration_version": cycle.configuration_version,
            "workload": {
                "telemetry_records_sent": cycle.telemetry_records_sent, "event_count": cycle.event_count,
                "vibration_window_count": cycle.vibration_window_count, "sensor_reboot_count": cycle.sensor_reboot_count,
                "configuration_change_count": cycle.configuration_change_count,
            },
        }
