#pragma once

#include "sensor_config.h"

class TelemetryBuffer {
 public:
  TelemetryBuffer();
  bool push(const TelemetryRecord& record);
  bool pop(TelemetryRecord* record);
  uint8_t size() const { return count_; }
  uint8_t capacity() const { return TELEMETRY_BUFFER_CAPACITY; }
  uint32_t dropped_count() const { return dropped_count_; }

 private:
  TelemetryRecord records_[TELEMETRY_BUFFER_CAPACITY];
  uint8_t count_;
  uint32_t dropped_count_;
};
