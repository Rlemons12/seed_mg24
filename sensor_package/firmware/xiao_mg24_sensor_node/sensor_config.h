#pragma once

#include "sensor_types.h"

// Production scripts inject these from tracked VERSION files and local device configuration.
#ifndef DEVICE_ID
#define DEVICE_ID "UNASSIGNED-MG24"
#endif
#ifndef SENSOR_PACKAGE_VERSION
#define SENSOR_PACKAGE_VERSION "0.1.0-dev"
#endif
#ifndef FIRMWARE_VERSION
#define FIRMWARE_VERSION SENSOR_PACKAGE_VERSION
#endif
#ifndef PROTOCOL_VERSION
#define PROTOCOL_VERSION "1.0.0-dev"
#endif
#ifndef CONFIGURATION_SCHEMA_VERSION
#define CONFIGURATION_SCHEMA_VERSION 1
#endif
#ifndef BUILD_IDENTIFIER
#define BUILD_IDENTIFIER "development"
#endif
#ifndef FIRMWARE_GIT_COMMIT
#define FIRMWARE_GIT_COMMIT "unknown"
#endif
#define TELEMETRY_SCHEMA_VERSION 1
#define TELEMETRY_BUFFER_CAPACITY 24
#define MAX_FILTER_WINDOW 9
#define DEFAULT_HEARTBEAT_INTERVAL_MS 30000UL

// Existing microphone behavior: raw ADC-like count with the established simple smoothing.
// Calibration and alarms are deliberately disabled because no external sensor is specified.
static const ChannelConfig MICROPHONE_CHANNEL_CONFIG = {
  "microphone_raw", "built_in_microphone", true, RawDataType::AnalogCount,
  100, 100, 100, DEFAULT_HEARTBEAT_INTERVAL_MS,
  static_cast<uint8_t>(ReportingMode::Periodic) | static_cast<uint8_t>(ReportingMode::Heartbeat),
  FilterType::Exponential, 2,
  false, 0.0f, 1.0f, "adc_count",
  {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f},
  0, 0, 0.0f, false, false
};

static const ChannelConfig UNCONFIGURED_EXTERNAL_ANALOG = {
  "sensor_1", "external_analog_unconfigured", false, RawDataType::AnalogCount,
  0, 0, 0, 0, 0, FilterType::None, 1,
  false, 0.0f, 1.0f, "adc_count",
  {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f}, {false, 0.0f},
  0, 0, 0.0f, false, false
};
