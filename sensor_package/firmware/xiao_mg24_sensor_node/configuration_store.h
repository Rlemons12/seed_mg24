#pragma once

#include <stdint.h>
#include "sensor_types.h"
#include "nvm_backend.h"

struct StoredChannelConfiguration {
  uint32_t magic;
  uint16_t schema_version;
  uint16_t reserved;
  uint32_t generation;
  uint32_t sample_interval_ms;
  uint32_t processing_interval_ms;
  uint32_t report_interval_ms;
  uint32_t heartbeat_interval_ms;
  uint8_t filter_type;
  uint8_t filter_window;
  uint8_t enabled;
  uint8_t reserved2;
  uint32_t checksum;
};
static_assert(sizeof(StoredChannelConfiguration) == 36, "configuration record layout changed");

uint32_t configuration_checksum(const StoredChannelConfiguration& value);
bool validate_stored_configuration(const StoredChannelConfiguration& value);

class VolatileConfigurationStore {
 public:
  explicit VolatileConfigurationStore(uint32_t minimum_write_interval_ms = 60000UL);
  bool write(const StoredChannelConfiguration& value, uint32_t now, bool force = false);
  bool load(StoredChannelConfiguration* output) const;
  void corrupt_slot_for_test(uint8_t slot);
  uint32_t write_count() const { return write_count_; }

 private:
  StoredChannelConfiguration slots_[2];
  bool populated_[2];
  uint8_t active_slot_;
  uint32_t last_write_ms_;
  uint32_t minimum_write_interval_ms_;
  uint32_t write_count_;
};

class PersistentConfigurationStore {
 public:
  explicit PersistentConfigurationStore(NvmBackend& backend):backend_(backend){}
  StoreStatus load(StoredChannelConfiguration* output) const;
  StoreStatus write(const StoredChannelConfiguration& value, StoredChannelConfiguration* verified);
 private: NvmBackend& backend_;
};
