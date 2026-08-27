#pragma once

#include <stddef.h>
#include <stdint.h>

#include "nvm_backend.h"

constexpr uint8_t kPersistentTelemetryBatchSize = 4;
constexpr uint8_t kPersistentTelemetrySlotCount = 8;

struct PersistentTelemetrySummary {
  char boot_id[17];
  uint32_t sequence_number;
  uint32_t uptime_ms;
  float battery_voltage;
  float acceleration[3];
  float angular_velocity[3];
  uint16_t sample_count;
  uint8_t flags;
};

class PersistentTelemetryJournal {
 public:
  explicit PersistentTelemetryJournal(NvmBackend& backend);
  StoreStatus initialize();
  StoreStatus append(const PersistentTelemetrySummary& summary);
  bool peek(PersistentTelemetrySummary* summary) const;
  StoreStatus acknowledge(const char* boot_id, uint32_t sequence_number, bool* matched);
  void discard_staging();
  uint8_t persisted_count() const;
  uint8_t staged_count() const { return staging_count_; }
  uint32_t dropped_count() const { return dropped_count_; }
  StoreStatus last_status() const { return last_status_; }

 private:
  struct Batch;
  StoreStatus commit_staging();
  StoreStatus read_batch(uint32_t key, Batch* batch) const;
  bool valid_summary(const PersistentTelemetrySummary& summary) const;
  void remove_oldest_slot();

  NvmBackend& backend_;
  uint32_t ordered_keys_[kPersistentTelemetrySlotCount];
  uint8_t ordered_count_;
  uint8_t head_entry_;
  uint32_t next_generation_;
  PersistentTelemetrySummary staging_[kPersistentTelemetryBatchSize];
  uint8_t staging_count_;
  uint32_t dropped_count_;
  StoreStatus last_status_;
};
