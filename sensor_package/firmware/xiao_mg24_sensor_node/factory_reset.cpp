#include "factory_reset.h"
#include "application_nvm_keys.h"
#include "persistent_record.h"
#include <string.h>

namespace {
constexpr uint8_t kMarkerVersion = 1;
bool same(const char* left, const char* right) { return left && right && strcmp(left, right) == 0; }
}

StoreStatus FactoryResetController::write_marker(const char* operation_id, ResetStage stage) {
  const size_t length = operation_id ? strlen(operation_id) : 0;
  if (length != 32) return StoreStatus::InvalidArgument;
  uint8_t payload[35] = {kMarkerVersion, static_cast<uint8_t>(stage), static_cast<uint8_t>(length)};
  memcpy(payload + 3, operation_id, length);
  uint8_t record[kPersistentMaxRecord]; size_t record_size = 0;
  StoreStatus status = encode_persistent_record(PersistentRecordType::StoreMetadata, 1, 0, payload,
                                                 sizeof(payload), record, sizeof(record), &record_size);
  if (status != StoreStatus::Ok) return status;
  status = backend_.write(ApplicationNvmKeys::kResetTransactionMarker, record, record_size);
  if (status != StoreStatus::Ok) return status;
  char verified[33]; ResetStage verified_stage;
  status = read_marker(verified, sizeof(verified), &verified_stage);
  return status == StoreStatus::Ok && same(verified, operation_id) && verified_stage == stage
      ? StoreStatus::Ok : StoreStatus::ReadbackFailed;
}

StoreStatus FactoryResetController::read_marker(char* operation_id, size_t capacity, ResetStage* stage) {
  if (!operation_id || capacity < 33 || !stage) return StoreStatus::InvalidArgument;
  uint8_t record[kPersistentMaxRecord]; size_t size = 0;
  StoreStatus status = backend_.read(ApplicationNvmKeys::kResetTransactionMarker, record, sizeof(record), &size);
  if (status != StoreStatus::Ok) return status;
  RecordView view; status = decode_persistent_record(record, size, PersistentRecordType::StoreMetadata, &view);
  if (status != StoreStatus::Ok) return status;
  if (view.payload_size != 35 || view.payload[0] != kMarkerVersion || view.payload[2] != 32
      || view.payload[1] < static_cast<uint8_t>(ResetStage::MarkerWritten)
      || view.payload[1] > static_cast<uint8_t>(ResetStage::KeysCleared)) return StoreStatus::Corrupt;
  memcpy(operation_id, view.payload + 3, 32); operation_id[32] = '\0';
  *stage = static_cast<ResetStage>(view.payload[1]);
  return StoreStatus::Ok;
}

bool FactoryResetController::reset_keys_absent() const {
  uint8_t byte; size_t size = 0;
  for (size_t index = 0; index < ApplicationNvmKeys::kApplicationFactoryResetCount; ++index) {
    if (backend_.read(ApplicationNvmKeys::kApplicationFactoryReset[index], &byte, 1, &size) != StoreStatus::NotFound) return false;
  }
  return true;
}

StoreStatus FactoryResetController::clear_marker() {
  StoreStatus status = backend_.remove(ApplicationNvmKeys::kResetTransactionMarker);
  if (status != StoreStatus::Ok) return status;
  uint8_t byte; size_t size = 0;
  return backend_.read(ApplicationNvmKeys::kResetTransactionMarker, &byte, 1, &size) == StoreStatus::NotFound
      ? StoreStatus::Ok : StoreStatus::ReadbackFailed;
}

StoreStatus FactoryResetController::prepare(ResetScope scope, const char* hardware_id, const char* expected_hardware_id,
                                             const char* operation_id, const char* challenge, uint32_t now,
                                             uint32_t ttl, ResetChallenge* output) {
  if (!output || ttl < 1000 || ttl > 300000 || challenge_.pending || marker_active_ || !same(hardware_id, expected_hardware_id)
      || !operation_id || strlen(operation_id) != 32 || !challenge || strlen(challenge) != 32) return StoreStatus::InvalidArgument;
  challenge_.pending = true; challenge_.scope = scope; challenge_.expires_at_ms = now + ttl;
  strcpy(challenge_.hardware_id, hardware_id); strcpy(challenge_.operation_id, operation_id); strcpy(challenge_.challenge, challenge);
  *output = challenge_; return StoreStatus::Ok;
}

ResetResult FactoryResetController::confirm(ResetScope scope, const char* expected_hardware_id, const char* operation_id,
                                             const char* challenge, uint32_t now) {
  ResetResult result = {StoreStatus::InvalidArgument, 0, 0};
  if (!challenge_.pending) return result;
  ResetChallenge pending = challenge_; challenge_.pending = false;
  if (pending.scope != scope || !same(pending.hardware_id, expected_hardware_id) || !same(pending.operation_id, operation_id)
      || !same(pending.challenge, challenge) || static_cast<int32_t>(now - pending.expires_at_ms) >= 0) return result;
  busy_ = true;
  if (scope == ResetScope::ConfigurationOnly) {
    for (size_t index = 0; index < ApplicationNvmKeys::kConfigurationResetCount; ++index) {
      StoreStatus removed = backend_.remove(ApplicationNvmKeys::kConfigurationReset[index]); uint8_t byte; size_t size = 0;
      StoreStatus check = backend_.read(ApplicationNvmKeys::kConfigurationReset[index], &byte, 1, &size);
      if (removed == StoreStatus::Ok && check == StoreStatus::NotFound) result.deleted_count++; else result.failed_count++;
    }
    busy_ = false; result.status = result.failed_count ? StoreStatus::WriteFailed : StoreStatus::Ok; return result;
  }
  StoreStatus status = write_marker(operation_id, ResetStage::MarkerWritten);
  if (status != StoreStatus::Ok) { busy_ = false; result.status = status; return result; }
  marker_active_ = true;
  const uint32_t* keys = ApplicationNvmKeys::kApplicationFactoryReset;
  size_t count = ApplicationNvmKeys::kApplicationFactoryResetCount;
  for (size_t index = 0; index < count; ++index) {
    StoreStatus removed = backend_.remove(keys[index]); uint8_t byte; size_t size = 0;
    StoreStatus check = backend_.read(keys[index], &byte, 1, &size);
    if (removed == StoreStatus::Ok && check == StoreStatus::NotFound) result.deleted_count++; else result.failed_count++;
  }
  if (result.failed_count) { busy_ = false; result.status = StoreStatus::WriteFailed; return result; }
  status = write_marker(operation_id, ResetStage::KeysCleared);
  busy_ = false; result.status = status;
  if (status == StoreStatus::Ok) reboot_required_ = true;
  return result;
}

StoreStatus FactoryResetController::recover_on_boot(bool* recovered) {
  if (!recovered) return StoreStatus::InvalidArgument; *recovered = false;
  char operation_id[33]; ResetStage stage; StoreStatus status = read_marker(operation_id, sizeof(operation_id), &stage);
  if (status == StoreStatus::NotFound) { marker_active_ = false; return StoreStatus::Ok; }
  marker_active_ = true;
  if (status != StoreStatus::Ok) return status;
  if (!reset_keys_absent()) {
    for (size_t index = 0; index < ApplicationNvmKeys::kApplicationFactoryResetCount; ++index) {
      status = backend_.remove(ApplicationNvmKeys::kApplicationFactoryReset[index]);
      if (status != StoreStatus::Ok) return status;
    }
    if (!reset_keys_absent()) return StoreStatus::IntegrityFailed;
    status = write_marker(operation_id, ResetStage::KeysCleared);
    if (status != StoreStatus::Ok) return status;
  }
  status = clear_marker();
  if (status != StoreStatus::Ok) return status;
  marker_active_ = false; *recovered = true; return StoreStatus::Ok;
}

void FactoryResetController::cancel() { challenge_.pending = false; }
