#include "configuration_store.h"

#include <string.h>

static const uint32_t CONFIG_MAGIC = 0x4D473234UL;

uint32_t configuration_checksum(const StoredChannelConfiguration& value) {
  const uint8_t* bytes = reinterpret_cast<const uint8_t*>(&value);
  uint32_t hash = 2166136261UL;
  for (size_t index = 0; index < sizeof(value) - sizeof(value.checksum); ++index) {
    hash ^= bytes[index];
    hash *= 16777619UL;
  }
  return hash;
}

bool validate_stored_configuration(const StoredChannelConfiguration& value) {
  if (value.magic != CONFIG_MAGIC || value.schema_version != 1 || value.checksum != configuration_checksum(value)) return false;
  if (value.enabled > 1 || value.sample_interval_ms < 10 || value.sample_interval_ms > 5000) return false;
  if (value.processing_interval_ms < 10 || value.processing_interval_ms > 5000) return false;
  if (value.report_interval_ms < 50 || value.report_interval_ms > 5000) return false;
  if (value.heartbeat_interval_ms < 1000 || value.heartbeat_interval_ms > 3600000) return false;
  if (value.filter_type > static_cast<uint8_t>(FilterType::DigitalDebounce) || value.filter_window < 1 || value.filter_window > 9) return false;
  return true;
}

VolatileConfigurationStore::VolatileConfigurationStore(uint32_t minimum_write_interval_ms)
    : active_slot_(0), last_write_ms_(0), minimum_write_interval_ms_(minimum_write_interval_ms), write_count_(0) {
  memset(slots_, 0, sizeof(slots_)); populated_[0] = populated_[1] = false;
}

bool VolatileConfigurationStore::write(const StoredChannelConfiguration& input, uint32_t now, bool force) {
  if (!force && write_count_ > 0 && static_cast<uint32_t>(now - last_write_ms_) < minimum_write_interval_ms_) return false;
  StoredChannelConfiguration value = input;
  value.magic = CONFIG_MAGIC; value.schema_version = 1;
  value.generation = write_count_ + 1; value.checksum = configuration_checksum(value);
  if (!validate_stored_configuration(value)) return false;
  uint8_t target = populated_[active_slot_] ? 1 - active_slot_ : active_slot_;
  slots_[target] = value;
  populated_[target] = true;
  active_slot_ = target; last_write_ms_ = now; write_count_++;
  return true;
}

bool VolatileConfigurationStore::load(StoredChannelConfiguration* output) const {
  if (!output) return false;
  bool valid0 = populated_[0] && validate_stored_configuration(slots_[0]);
  bool valid1 = populated_[1] && validate_stored_configuration(slots_[1]);
  if (!valid0 && !valid1) return false;
  uint8_t selected = valid1 && (!valid0 || slots_[1].generation > slots_[0].generation) ? 1 : 0;
  *output = slots_[selected]; return true;
}

void VolatileConfigurationStore::corrupt_slot_for_test(uint8_t slot) {
  if (slot < 2 && populated_[slot]) slots_[slot].checksum ^= 0xFFFFFFFFUL;
}
