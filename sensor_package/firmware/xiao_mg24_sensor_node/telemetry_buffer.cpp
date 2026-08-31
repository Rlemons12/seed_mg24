#include "telemetry_buffer.h"

TelemetryBuffer::TelemetryBuffer() : count_(0), dropped_count_(0) {}
bool TelemetryBuffer::push(const TelemetryRecord& record) {
  if (count_ < TELEMETRY_BUFFER_CAPACITY) { records_[count_++] = record; return true; }
  uint8_t lowest = 0;
  for (uint8_t i = 1; i < count_; ++i) if (static_cast<uint8_t>(records_[i].priority) < static_cast<uint8_t>(records_[lowest].priority)) lowest = i;
  if (static_cast<uint8_t>(record.priority) < static_cast<uint8_t>(records_[lowest].priority)) { dropped_count_++; return false; }
  for (uint8_t i = lowest; i + 1 < count_; ++i) records_[i] = records_[i + 1];
  records_[count_ - 1] = record; dropped_count_++; return true;
}
bool TelemetryBuffer::pop(TelemetryRecord* record) {
  if (!record || count_ == 0) return false;
  *record = records_[0]; record->delayed = true;
  for (uint8_t i = 0; i + 1 < count_; ++i) records_[i] = records_[i + 1];
  count_--; return true;
}
bool TelemetryBuffer::peek_oldest(TelemetryRecord* record) const {
  if (!record || count_ == 0) return false;
  *record = records_[0];
  return true;
}
uint8_t TelemetryBuffer::acknowledge_through(uint32_t sequence) {
  uint8_t removed = 0;
  while (count_ && records_[0].sequence_number <= sequence) {
    for (uint8_t i = 0; i + 1 < count_; ++i) records_[i] = records_[i + 1];
    count_--; removed++;
  }
  return removed;
}
