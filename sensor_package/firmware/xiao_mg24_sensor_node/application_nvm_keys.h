#pragma once

#include <stddef.h>
#include <stdint.h>

namespace ApplicationNvmKeys {
constexpr uint32_t kUserDomainStart = 0x00000u;
constexpr uint32_t kUserDomainEnd = 0x0FFFFu;
// High user-domain block avoids Arduino EEPROM emulation keys 0x0000-0x0028.
constexpr uint32_t kRangeStart = 0x0FF00u;
constexpr uint32_t kRangeEnd = 0x0FF0Fu;
constexpr uint32_t kIdentitySlotA = 0x0FF00u;
constexpr uint32_t kIdentitySlotB = 0x0FF01u;
constexpr uint32_t kConfigurationSlotA = 0x0FF02u;
constexpr uint32_t kConfigurationSlotB = 0x0FF03u;
constexpr uint32_t kConfigurationStaging = 0x0FF04u;
constexpr uint32_t kStoreMetadata = 0x0FF05u;

struct NamedKey { const char* name; uint32_t key; };
constexpr NamedKey kRegistered[] = {
  {"identity_slot_a", kIdentitySlotA}, {"identity_slot_b", kIdentitySlotB},
  {"configuration_slot_a", kConfigurationSlotA}, {"configuration_slot_b", kConfigurationSlotB},
  {"configuration_staging", kConfigurationStaging}, {"store_metadata", kStoreMetadata}
};
constexpr uint32_t kConfigurationReset[] = {
  kConfigurationSlotA, kConfigurationSlotB, kConfigurationStaging
};
constexpr uint32_t kApplicationFactoryReset[] = {
  kIdentitySlotA, kIdentitySlotB, kConfigurationSlotA, kConfigurationSlotB,
  kConfigurationStaging, kStoreMetadata
};
constexpr size_t kRegisteredCount = sizeof(kRegistered) / sizeof(kRegistered[0]);
constexpr size_t kConfigurationResetCount = sizeof(kConfigurationReset) / sizeof(kConfigurationReset[0]);
constexpr size_t kApplicationFactoryResetCount = sizeof(kApplicationFactoryReset) / sizeof(kApplicationFactoryReset[0]);
static_assert(kRangeStart >= kUserDomainStart && kRangeEnd <= kUserDomainEnd, "application keys outside user domain");
static_assert(kIdentitySlotA != kIdentitySlotB && kConfigurationSlotA != kConfigurationSlotB, "duplicate A/B keys");
}
