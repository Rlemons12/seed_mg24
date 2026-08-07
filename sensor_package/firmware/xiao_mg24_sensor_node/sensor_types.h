#pragma once

#include <stdint.h>

enum class RawDataType : uint8_t { AnalogCount, Digital, FloatingPoint };
enum class ReportingMode : uint8_t { Periodic = 1, Change = 2, Event = 4, Heartbeat = 8, Burst = 16 };
enum class FilterType : uint8_t { None, MovingAverage, Exponential, Median, DigitalDebounce };
enum class MeasurementQuality : uint8_t { Good, Estimated, Uncalibrated, Invalid, SensorFault };
enum class MonitoringState : uint8_t { Normal, WarningLow, WarningHigh, AlarmLow, AlarmHigh, Invalid, SensorFault };
enum class RecordType : uint8_t { Measurement, AlarmTransition, SensorFaultTransition, Recovery, Configuration, Heartbeat, BurstFragment };
enum class RecordPriority : uint8_t { Routine = 0, Heartbeat = 1, Configuration = 2, Recovery = 3, SensorFault = 4, Alarm = 5 };

struct OptionalFloat {
  bool configured;
  float value;
};

struct ChannelConfig {
  const char* channel_id;
  const char* channel_type;
  bool enabled;
  RawDataType raw_data_type;
  uint32_t sample_interval_ms;
  uint32_t processing_interval_ms;
  uint32_t report_interval_ms;
  uint32_t heartbeat_interval_ms;
  uint8_t reporting_mode;
  FilterType filter_type;
  uint8_t filter_window;
  bool calibration_enabled;
  float calibration_offset;
  float calibration_gain;
  const char* engineering_unit;
  OptionalFloat change_deadband;
  OptionalFloat warning_low;
  OptionalFloat warning_high;
  OptionalFloat alarm_low;
  OptionalFloat alarm_high;
  OptionalFloat rate_of_change_limit;
  uint32_t activation_persistence_ms;
  uint32_t clearing_persistence_ms;
  float hysteresis;
  bool latching_enabled;
  bool event_reporting_enabled;
};

struct ProcessedValue {
  float raw_value;
  float processed_value;
  bool engineering_value_available;
  MeasurementQuality quality;
  float minimum;
  float maximum;
  float peak;
  float rate_of_change;
};

struct TelemetryRecord {
  RecordType type;
  RecordPriority priority;
  uint32_t sequence_number;
  uint32_t uptime_ms;
  char channel_id[24];
  float raw_value;
  float normalized_value;
  MeasurementQuality quality;
  MonitoringState state;
  bool has_normalized_value;
  bool delayed;
};

inline bool elapsed_since(uint32_t now, uint32_t previous, uint32_t interval) {
  return static_cast<uint32_t>(now - previous) >= interval;
}
