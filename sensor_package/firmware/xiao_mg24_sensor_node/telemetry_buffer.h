#pragma once

#include "sensor_config.h"

class TelemetryBuffer {
 public:
  TelemetryBuffer();
  bool push(const TelemetryRecord& record);
  bool pop(TelemetryRecord* record);
  bool peek_oldest(TelemetryRecord* record) const;
  uint8_t acknowledge_through(uint32_t sequence);
  void clear() { count_ = 0; dropped_count_ = 0; }
  uint8_t size() const { return count_; }
  uint8_t capacity() const { return TELEMETRY_BUFFER_CAPACITY; }
  uint32_t dropped_count() const { return dropped_count_; }
  uint32_t oldest_sequence() const { return count_ ? records_[0].sequence_number : 0; }
  uint32_t newest_sequence() const { return count_ ? records_[count_ - 1].sequence_number : 0; }

 private:
  TelemetryRecord records_[TELEMETRY_BUFFER_CAPACITY];
  uint8_t count_;
  uint32_t dropped_count_;
};
