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
constexpr uint32_t kResetTransactionMarker = 0x0FF06u;
constexpr uint32_t kTelemetryJournalSlot0 = 0x0FF08u;
constexpr uint32_t kTelemetryJournalSlot1 = 0x0FF09u;
constexpr uint32_t kTelemetryJournalSlot2 = 0x0FF0Au;
constexpr uint32_t kTelemetryJournalSlot3 = 0x0FF0Bu;
constexpr uint32_t kTelemetryJournalSlot4 = 0x0FF0Cu;
constexpr uint32_t kTelemetryJournalSlot5 = 0x0FF0Du;
constexpr uint32_t kTelemetryJournalSlot6 = 0x0FF0Eu;
constexpr uint32_t kTelemetryJournalSlot7 = 0x0FF0Fu;
constexpr uint32_t kTelemetryJournalSlots[] = {
  kTelemetryJournalSlot0, kTelemetryJournalSlot1, kTelemetryJournalSlot2, kTelemetryJournalSlot3,
  kTelemetryJournalSlot4, kTelemetryJournalSlot5, kTelemetryJournalSlot6, kTelemetryJournalSlot7
};

struct NamedKey { const char* name; uint32_t key; };
constexpr NamedKey kRegistered[] = {
  {"identity_slot_a", kIdentitySlotA}, {"identity_slot_b", kIdentitySlotB},
  {"configuration_slot_a", kConfigurationSlotA}, {"configuration_slot_b", kConfigurationSlotB},
  {"configuration_staging", kConfigurationStaging}, {"store_metadata", kStoreMetadata},
  {"reset_transaction_marker", kResetTransactionMarker},
  {"telemetry_journal_slot_0", kTelemetryJournalSlot0}, {"telemetry_journal_slot_1", kTelemetryJournalSlot1},
  {"telemetry_journal_slot_2", kTelemetryJournalSlot2}, {"telemetry_journal_slot_3", kTelemetryJournalSlot3},
  {"telemetry_journal_slot_4", kTelemetryJournalSlot4}, {"telemetry_journal_slot_5", kTelemetryJournalSlot5},
  {"telemetry_journal_slot_6", kTelemetryJournalSlot6}, {"telemetry_journal_slot_7", kTelemetryJournalSlot7}
};
constexpr uint32_t kConfigurationReset[] = {
  kConfigurationSlotA, kConfigurationSlotB, kConfigurationStaging
};
constexpr uint32_t kApplicationFactoryReset[] = {
  kIdentitySlotA, kIdentitySlotB, kConfigurationSlotA, kConfigurationSlotB,
  kConfigurationStaging, kStoreMetadata,
  kTelemetryJournalSlot0, kTelemetryJournalSlot1, kTelemetryJournalSlot2, kTelemetryJournalSlot3,
  kTelemetryJournalSlot4, kTelemetryJournalSlot5, kTelemetryJournalSlot6, kTelemetryJournalSlot7
};
constexpr size_t kRegisteredCount = sizeof(kRegistered) / sizeof(kRegistered[0]);
constexpr size_t kConfigurationResetCount = sizeof(kConfigurationReset) / sizeof(kConfigurationReset[0]);
constexpr size_t kApplicationFactoryResetCount = sizeof(kApplicationFactoryReset) / sizeof(kApplicationFactoryReset[0]);
static_assert(kRangeStart >= kUserDomainStart && kRangeEnd <= kUserDomainEnd, "application keys outside user domain");
static_assert(kIdentitySlotA != kIdentitySlotB && kConfigurationSlotA != kConfigurationSlotB, "duplicate A/B keys");
static_assert(sizeof(kTelemetryJournalSlots) / sizeof(kTelemetryJournalSlots[0]) == 8, "journal slot count changed");
}
